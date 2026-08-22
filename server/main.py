from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
import asyncio
import json
from pathlib import Path
import time
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from agent.task_manager import ALLOWED_TASK_TYPES

from .ai_provider import AIProviderTester, CredentialCipher
from .ai_policy import FEATURES, public_permissions
from .ai_provider_api import register_ai_provider_routes
from .ai_service import AIService, ChatRunRegistry
from .analysis_api import register_analysis_routes
from .analysis_service import AIAnalysisService
from .auth import create_jwt, decode_jwt, hash_password, new_agent_token, new_invitation_token, password_auth_version, token_hash, verify_login_password
from .chat_api import register_chat_routes
from .config import ROOT, ServerSettings, load_server_settings
from .config_check import check_server_config
from .database import Base, create_database
from .image_api import register_image_routes
from .image_service import AIImageService
from .engine_update_api import register_engine_update_routes
from .writing_api import register_writing_routes
from .writing_service import AIWritingService
from .task_proposal_api import register_task_proposal_routes
from .task_proposal_service import AITaskProposalService
from .control_api import register_control_routes
from .command_api import COMMAND_LEASE_SECONDS, COMMAND_STATUSES, register_command_routes, store_credential_probe
from .models import AIProvider, Account, Activity, Agent, AgentToken, AuditLog, AutomationMetric, Command, Invitation, License, LicenseCheck, LicenseDevice, LicenseRevocation, Profile, Script, ScriptVersion, Task, User, UserAIPolicy, Workspace, now
from .remote_license_api import register_remote_license_routes
from .schemas import AccountSync, AgentRegister, AutomationMetricSync, BootstrapRequest, Heartbeat, InvitationAccept, InvitationCreate, LoginRequest, PasswordChange, TaskCreate, TaskPull, TaskResult, UserAIPolicyUpdate, UserCreate, UserUpdate, WorkspaceCreate, WorkspaceUpdate
from .security import InMemoryRateLimiter, audit, audit_dict, client_ip, redact, redact_payload
from .security_diagnostics import configuration_diagnostics, database_diagnostic
from .script_api import register_script_routes
from common.release import VERSION, release_info
from common.safety_policy import evaluate_task


LOGGER = logging.getLogger("laogu.server")
TERMINAL_STATUSES = {"SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"}
TOKEN_ACTIVE = "ACTIVE"
TOKEN_REVOKED = "REVOKED"
TOKEN_EXPIRED = "EXPIRED"


def _bearer(value: str | None) -> str:
    if not value or not value.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return value[7:].strip()


def _dt(value):
    return value.isoformat() if value else None


def _agent_dict(item: Agent, settings: ServerSettings) -> dict:
    last = item.last_heartbeat
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    online = bool(last and datetime.now(timezone.utc) - last.astimezone(timezone.utc) <= timedelta(seconds=settings.agent_offline_seconds))
    visible_status = "DELETED" if item.status == "DELETED" else ("ONLINE" if online else "OFFLINE")
    return {"agent_id": item.id, "workspace_id": item.workspace_id, "agent_name": item.agent_name, "machine_name": item.machine_name, "client_version": item.client_version, "status": visible_status, "last_heartbeat": _dt(item.last_heartbeat), "profile_count": item.profile_count, "running_task_count": item.running_task_count, "binding_status": "BOUND" if item.bound_device_id else "UNBOUND", "bound_ip": item.bound_ip, "last_ip": item.last_ip, "ip_country": item.ip_country or "UNKNOWN"}


def _request_ip_country(request: Request) -> str:
    value = request.headers.get("cf-ipcountry", "").strip().upper()
    return value if value and len(value) <= 8 else "UNKNOWN"


def _agent_ip_allowed(country: str) -> bool:
    # Agent access is protected by the per-device binding below, not by a
    # country allow-list. Cloudflare's country header is still recorded for
    # audit and display, but users may operate the Agent from any country.
    return True


def _account_dict(item: Account) -> dict:
    return {key: getattr(item, key) for key in ("id", "workspace_id", "agent_id", "profile_id", "instance_id", "x_username", "x_account_id", "login_status", "browser_status", "account_status")} | {"last_checked": _dt(item.last_checked), "mapping_updated_at": _dt(item.mapping_updated_at)}


def _task_dict(item: Task) -> dict:
    return {"task_id": item.id, "workspace_id": item.workspace_id, "agent_id": item.agent_id, "profile_id": item.profile_id, "x_account_id": item.x_account_id, "script_id": item.script_id, "script_version_id": item.script_version_id, "task_type": item.task_type, "params": item.params or {}, "timeout": item.timeout, "status": item.status, "created_at": _dt(item.created_at), "pulled_at": _dt(item.pulled_at), "started_at": _dt(item.started_at), "finished_at": _dt(item.finished_at), "duration": item.duration, "result": item.result, "error": item.error}


def _user_dict(item: User, workspace_name: str | None = None) -> dict:
    return {
        "user_id": item.id,
        "username": item.username,
        "role": item.role,
        "workspace_id": item.workspace_id,
        "workspace_name": workspace_name,
        "status": item.status,
        "created_at": _dt(item.created_at),
    }


def _workspace_dict(item: Workspace) -> dict:
    return {"workspace_id": item.id, "name": item.name, "status": item.status, "created_at": _dt(item.created_at)}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _invitation_status(item: Invitation) -> str:
    if item.revoked_at:
        return "REVOKED"
    if item.accepted_at:
        return "ACCEPTED"
    if _aware(item.expires_at) <= datetime.now(timezone.utc):
        return "EXPIRED"
    return "ACTIVE"


def _invitation_dict(item: Invitation, workspace_name: str = "") -> dict:
    return {
        "invitation_id": item.id,
        "workspace_id": item.workspace_id,
        "workspace_name": workspace_name,
        "role": item.role,
        "status": _invitation_status(item),
        "created_by": item.created_by,
        "created_at": _dt(item.created_at),
        "expires_at": _dt(item.expires_at),
        "accepted_at": _dt(item.accepted_at),
        "accepted_user_id": item.accepted_user_id,
    }


def _profile_dict(item: Profile, account: Account | None = None) -> dict:
    return {
        "profile_id": item.profile_id,
        "profile_record_id": item.id,
        "agent_id": item.agent_id,
        "workspace_id": item.workspace_id,
        "instance_id": item.instance_id,
        "browser_status": item.status,
        "x_username": item.x_username,
        "x_account_id": item.x_account_id,
        "login_status": account.login_status if account else "UNKNOWN",
        "account_status": account.account_status if account else "UNKNOWN",
        "last_checked": _dt(account.last_checked if account else None),
    }


def _activity_dict(item: Activity) -> dict:
    return {"activity_id": item.id, "workspace_id": item.workspace_id, "agent_id": item.agent_id, "profile_id": item.profile_id, "x_account_id": item.x_account_id, "task_id": item.task_id, "script_id": item.script_id, "script_version_id": item.script_version_id, "activity_type": item.activity_type, "action": item.activity_type, "status": item.status, "duration": item.duration, "summary": item.summary, "result": item.result, "logs": item.logs or [], "timestamp": _dt(item.timestamp)}


def create_app(database_url: str | None = None, settings: ServerSettings | None = None) -> FastAPI:
    settings = settings or load_server_settings()
    check_server_config(settings)
    engine, SessionLocal = create_database(database_url)
    if settings.environment != "production":
        Base.metadata.create_all(engine)
    app = FastAPI(title="Laogu Coordination Server", version=VERSION, debug=False)
    app.state.engine = engine
    app.state.SessionLocal = SessionLocal
    app.state.settings = settings
    app.state.started_at = now()

    @app.websocket("/api/agent/commands/ws")
    async def agent_command_socket(websocket: WebSocket):
        """Authenticated push channel. HTTP command pull remains the fallback."""
        raw = ""
        header = websocket.headers.get("authorization", "")
        if header.startswith("Bearer "):
            raw = header[7:].strip()
        db = SessionLocal()
        try:
            digest = token_hash(raw)
            token = db.scalar(select(AgentToken).where(AgentToken.token_hash == digest, AgentToken.status == TOKEN_ACTIVE))
            agent = db.get(Agent, token.agent_id) if token else None
            if not agent or agent.status == "DELETED":
                await websocket.close(code=4401)
                return
            device_id = websocket.headers.get("x-laogu-device-id", "").strip()
            if agent.bound_device_id and (not device_id or device_id != agent.bound_device_id):
                await websocket.close(code=4403)
                return
            await websocket.accept()
            while True:
                lease_expired = now() - timedelta(seconds=COMMAND_LEASE_SECONDS)
                items = list(db.scalars(select(Command).where(Command.agent_id == agent.id, or_(Command.status == "PENDING", (Command.status == "DELIVERED") & (Command.delivered_at < lease_expired))).order_by(Command.created_at).limit(10)))
                for item in items:
                    item.status = "DELIVERED"; item.delivered_at = now(); item.attempts += 1
                db.commit()
                for item in items:
                    await websocket.send_json({"type": "command", "command": {"command_id": item.id, "agent_id": item.agent_id, "profile_id": item.profile_id, "task_id": item.task_id, "command_type": item.command_type, "payload": item.payload or {}, "status": item.status}})
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
                except asyncio.TimeoutError:
                    continue
                if not isinstance(message, dict):
                    continue
                item = db.get(Command, str(message.get("command_id") or ""))
                if not item or item.agent_id != agent.id:
                    continue
                if message.get("type") == "ack" and item.status not in {"SUCCESS", "FAILED", "CANCELLED"}:
                    item.status = "RUNNING"; item.acknowledged_at = item.acknowledged_at or now(); item.started_at = item.started_at or now()
                elif message.get("type") == "result" and item.status not in {"SUCCESS", "FAILED", "CANCELLED"}:
                    status = str(message.get("status") or "FAILED").upper()
                    item.status = status if status in {"SUCCESS", "FAILED", "CANCELLED"} else "FAILED"
                    raw_result = message.get("result") if isinstance(message.get("result"), dict) else None
                    item.result = store_credential_probe(db, item, raw_result) if item.command_type == "PROBE_CREDENTIAL_CAPABILITY" and item.status == "SUCCESS" else redact_payload(raw_result)
                    item.error = redact(str(message.get("error") or "")[:500]); item.completed_at = now()
                db.commit()
        except WebSocketDisconnect:
            return
        finally:
            db.close()
    credential_cipher = CredentialCipher(
        settings.ai_credential_key,
        settings.jwt_secret,
        production=settings.environment == "production",
    )
    provider_tester = AIProviderTester(
        settings.ai_provider_timeout_seconds,
        production=settings.environment == "production",
    )
    ai_service = AIService(
        settings.ai_chat_timeout_seconds,
        settings.ai_chat_max_output_tokens,
        production=settings.environment == "production",
    )
    analysis_service = AIAnalysisService(ai_service)
    writing_service = AIWritingService(analysis_service)
    task_proposal_service = AITaskProposalService(analysis_service)
    chat_runs = ChatRunRegistry()
    image_service = AIImageService(
        settings.ai_image_timeout_seconds,
        settings.ai_image_max_bytes,
        production=settings.environment == "production",
    )
    image_storage_root = Path(settings.ai_image_storage_path) if settings.ai_image_storage_path else ROOT / "data" / "ai-images"
    limiter = InMemoryRateLimiter(settings.rate_limit_window_seconds)

    def secure_response(status_code: int, detail: str) -> JSONResponse:
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        length = request.headers.get("content-length")
        try:
            declared_length = int(length) if length else None
        except ValueError:
            return secure_response(400, "Invalid Content-Length")
        if declared_length is not None and declared_length > settings.max_request_bytes:
            return secure_response(413, "Request payload too large")
        if request.method in {"POST", "PUT", "PATCH"} and "application/json" in request.headers.get("content-type", "").lower():
            body = await request.body()
            if len(body) > settings.max_request_bytes:
                return secure_response(413, "Request payload too large")
        limits = {"/api/auth/login": settings.rate_limit_auth, "/api/agents/register": settings.rate_limit_register, "/api/agents/heartbeat": settings.rate_limit_heartbeat}
        limit = limits.get(request.url.path)
        rate_bucket = request.url.path
        if request.url.path.startswith("/api/auth/invitations/") and request.method == "POST":
            limit = settings.rate_limit_register
            rate_bucket = "/api/auth/invitations/accept"
        if request.url.path.startswith("/api/tasks"):
            limit = settings.rate_limit_tasks
            rate_bucket = "/api/tasks"
        if request.url.path.startswith("/api/commands") or request.url.path.startswith("/api/agent/commands"):
            limit = settings.rate_limit_tasks
            rate_bucket = "/api/commands"
        if request.url.path.startswith("/api/ai/providers") and request.url.path.endswith("/test"):
            limit = settings.rate_limit_ai_test
            rate_bucket = "/api/ai/providers/test"
        if request.url.path.startswith("/api/ai/chat/") and request.url.path.endswith("/messages"):
            limit = settings.rate_limit_ai_chat
            rate_bucket = "/api/ai/chat/messages"
        if request.url.path == "/api/ai/images/generate":
            limit = settings.rate_limit_ai_image
            rate_bucket = "/api/ai/images/generate"
        if request.url.path in {"/api/ai/analysis/account", "/api/ai/analysis/keywords"}:
            limit = settings.rate_limit_ai_analysis
            rate_bucket = "/api/ai/analysis"
        if request.url.path in {"/api/ai/writing/analyze", "/api/ai/writing/replies"}:
            limit = settings.rate_limit_ai_writing
            rate_bucket = "/api/ai/writing"
        if request.url.path == "/api/ai/task-proposals":
            limit = settings.rate_limit_ai_task_proposal
            rate_bucket = "/api/ai/task-proposals"
        if request.url.path == "/api/license/issue" and request.method == "POST":
            limit = settings.rate_limit_license_issue
            rate_bucket = "/api/license/issue"
        if request.url.path == "/api/license/check" and request.method == "POST":
            limit = settings.rate_limit_license_check
            rate_bucket = "/api/license/check"
        if limit is not None:
            if not limiter.allow(f"{rate_bucket}:{client_ip(request)}", limit, time.monotonic()):
                return secure_response(429, "Too many requests")
        # Enforce the customer-facing surface at the API boundary as well as
        # in the web menu. A MEMBER cannot reach admin, provider or runtime
        # endpoints by typing a URL directly.
        if request.url.path.startswith("/api/") and not request.url.path.startswith(("/api/auth/", "/api/health")):
            raw = request.headers.get("authorization", "")
            if raw.startswith("Bearer "):
                session = SessionLocal()
                try:
                    try:
                        payload = decode_jwt(raw[7:].strip(), settings)
                        member = session.get(User, str(payload.get("sub") or ""))
                    except Exception:
                        member = None
                    if member and member.status == "ACTIVE" and member.role == "MEMBER":
                        path = request.url.path
                        allowed = ("/api/dashboard", "/api/control", "/api/profiles", "/api/ai/chat", "/api/ai/writing", "/api/ai/analysis", "/api/ai/task-proposals")
                        if not path.startswith(allowed):
                            return secure_response(403, "该账号无权访问此功能")
                        feature_by_prefix = {
                            "/api/ai/chat": "CHAT",
                            "/api/ai/writing": "WRITING",
                            "/api/ai/analysis": "ANALYSIS",
                            "/api/ai/task-proposals": "TASKS",
                        }
                        feature = next((value for prefix, value in feature_by_prefix.items() if path.startswith(prefix)), None)
                        if feature and not public_permissions(session, member).get(feature, False):
                            return secure_response(403, "该功能尚未分配")
                finally:
                    session.close()
        try:
            response = await call_next(request)
        except Exception as exc:
            LOGGER.error("Unhandled server error type=%s message=%s", type(exc).__name__, redact(exc))
            return secure_response(500, "Internal server error")
        # Remove implementation details from JSON returned to MEMBER users.
        # The server still records complete usage internally for administrators.
        raw = request.headers.get("authorization", "")
        if raw.startswith("Bearer ") and response.headers.get("content-type", "").startswith("application/json"):
            session = SessionLocal()
            try:
                try:
                    payload = decode_jwt(raw[7:].strip(), settings)
                    member = session.get(User, str(payload.get("sub") or ""))
                except Exception:
                    member = None
                if member and member.status == "ACTIVE" and member.role == "MEMBER":
                    chunks = [chunk async for chunk in response.body_iterator]
                    try:
                        data = json.loads(b"".join(chunks).decode("utf-8"))
                        hidden = {"provider_id", "provider_name", "provider_type", "base_url", "api_key_masked", "has_api_key", "default_model", "models", "model", "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms", "ai_total_tokens"}
                        def scrub(value):
                            if isinstance(value, dict):
                                return {key: scrub(item) for key, item in value.items() if key not in hidden}
                            if isinstance(value, list):
                                return [scrub(item) for item in value]
                            return value
                        response = JSONResponse(status_code=response.status_code, content=scrub(data), headers={"Cache-Control": "no-store"})
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
            finally:
                session.close()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail if exc.status_code < 500 else "Internal server error"
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def paged(db: Session, query, serializer, *, page: int, page_size: int) -> dict:
        page = max(1, page)
        page_size = min(100, max(1, page_size))
        total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
        items = [serializer(item) for item in db.scalars(query.offset((page - 1) * page_size).limit(page_size))]
        return {"items": items, "page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size}

    def period_since(period: str) -> datetime | None:
        normalized = (period or "all").lower()
        if normalized == "today":
            return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        if normalized == "yesterday":
            return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        if normalized == "7d":
            return datetime.now().astimezone() - timedelta(days=7)
        if normalized == "30d":
            return datetime.now().astimezone() - timedelta(days=30)
        return None

    def deny(request: Request, db: Session, *, action: str, user: User | None = None, agent: Agent | None = None, message: str = "Forbidden"):
        audit(db, request, action=action, result="DENIED", user_id=user.id if user else None, workspace_id=user.workspace_id if user else (agent.workspace_id if agent else None), agent_id=agent.id if agent else None, message=message)
        raise HTTPException(status_code=403, detail="Forbidden")

    def current_user(request: Request, authorization: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)) -> User:
        try:
            payload = decode_jwt(_bearer(authorization), settings)
            user = db.get(User, str(payload.get("sub") or ""))
        except Exception:
            audit(db, request, action="AUTH_USER", result="DENIED", message="Invalid user authentication")
            raise HTTPException(status_code=401, detail="Unauthorized")
        if not user or user.status != "ACTIVE" or payload.get("auth_version") != password_auth_version(user.password_hash):
            audit(db, request, action="AUTH_USER", result="DENIED", message="Inactive or missing user")
            raise HTTPException(status_code=401, detail="Unauthorized")
        return user

    def current_agent(request: Request, authorization: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)) -> Agent:
        try:
            digest = token_hash(_bearer(authorization))
        except HTTPException:
            audit(db, request, action="AUTH_AGENT", result="DENIED", message="Missing agent authentication")
            raise HTTPException(status_code=401, detail="Unauthorized")
        token = db.scalar(select(AgentToken).where(AgentToken.token_hash == digest))
        if not token:
            audit(db, request, action="AUTH_AGENT", result="DENIED", message="Invalid agent token")
            raise HTTPException(status_code=401, detail="Unauthorized")
        expires = token.expires_at.replace(tzinfo=timezone.utc) if token.expires_at.tzinfo is None else token.expires_at
        if token.status != TOKEN_ACTIVE or token.revoked_at or expires <= datetime.now(timezone.utc):
            if expires <= datetime.now(timezone.utc) and token.status == TOKEN_ACTIVE:
                token.status = TOKEN_EXPIRED; db.commit()
            audit(db, request, action="AUTH_AGENT", result="DENIED", agent_id=token.agent_id, message="Expired or revoked agent token")
            raise HTTPException(status_code=401, detail="Unauthorized")
        agent = db.get(Agent, token.agent_id)
        if not agent or agent.status == "DELETED":
            audit(db, request, action="AUTH_AGENT", result="DENIED", agent_id=token.agent_id, message="Missing agent")
            raise HTTPException(status_code=401, detail="Unauthorized")
        device_id = request.headers.get("x-laogu-device-id", "").strip()
        if agent.bound_device_id and (not device_id or device_id != agent.bound_device_id):
            audit(db, request, action="AUTH_AGENT", result="DENIED", agent_id=agent.id, message="Agent token is bound to another device")
            raise HTTPException(status_code=403, detail="此 Agent Token 已绑定其他电脑，不能共用")
        token.last_used_at = now(); db.commit()
        return agent

    def require_agent_manager(request: Request, db: Session, user: User, agent: Agent) -> None:
        if user.role == "ADMIN" or (user.role == "OWNER" and user.workspace_id == agent.workspace_id):
            return
        deny(request, db, action="AGENT_MANAGE", user=user, agent=agent)

    def create_token(db: Session, agent_id: str) -> tuple[str, AgentToken]:
        raw = new_agent_token()
        issued = now()
        token = AgentToken(agent_id=agent_id, token_hash=token_hash(raw), created_at=issued, expires_at=issued + timedelta(days=settings.agent_token_ttl_days), status=TOKEN_ACTIVE)
        db.add(token)
        return raw, token

    @app.get("/api/health")
    def health():
        return {"ok": True, "release": release_info(component="server")}

    @app.get("/api/health/ready")
    def ready(db: Session = Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            raise HTTPException(status_code=503, detail="Service unavailable")
        return {"ok": True, "release": release_info(component="server")}

    @app.get("/api/admin/security/diagnostics")
    @app.get("/api/security/diagnostics", include_in_schema=False)
    def security_diagnostics(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role != "ADMIN":
            deny(request, db, action="SECURITY_DIAGNOSTICS_READ", user=user)
        report = configuration_diagnostics(settings)
        report["database"] = database_diagnostic(db)
        report["database"]["migration_head"] = "unknown"
        try:
            from alembic.script import ScriptDirectory
            from alembic.config import Config

            alembic_config = Config(str(ROOT / "alembic.ini"))
            report["database"]["migration_head"] = ScriptDirectory.from_config(alembic_config).get_current_head()
        except Exception:
            pass
        agents = list(db.scalars(select(Agent)))
        report["agents"] = {
            "total": len(agents),
            "online": sum(1 for item in agents if _agent_dict(item, settings)["status"] == "ONLINE"),
            "offline": sum(1 for item in agents if _agent_dict(item, settings)["status"] != "ONLINE"),
        }
        report["websocket"] = {"enabled": True, "path": "/api/agent/commands/ws", "http_pull_fallback": True}
        report["service"] = {"version": app.version, "debug": False}
        return report

    @app.get("/api/admin/ops/metrics")
    def ops_metrics(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role != "ADMIN":
            deny(request, db, action="OPS_METRICS_READ", user=user)
        agents = list(db.scalars(select(Agent)))
        commands = list(db.scalars(select(Command)))
        current = datetime.now().astimezone()
        stale_delivered = 0
        for item in commands:
            if item.status != "DELIVERED" or not item.delivered_at:
                continue
            delivered_at = item.delivered_at
            if delivered_at.tzinfo is None:
                delivered_at = delivered_at.replace(tzinfo=current.tzinfo)
            if current - delivered_at.astimezone(current.tzinfo) >= timedelta(seconds=COMMAND_LEASE_SECONDS):
                stale_delivered += 1
        command_counts = Counter(item.status for item in commands)
        database = database_diagnostic(db)
        started_at = app.state.started_at
        return {
            "service": {"version": app.version, "started_at": _dt(started_at), "uptime_seconds": max(0, int((current - started_at).total_seconds()))},
            "database": database,
            "agents": {
                "total": len(agents),
                "online": sum(1 for item in agents if _agent_dict(item, settings)["status"] == "ONLINE"),
                "offline": sum(1 for item in agents if _agent_dict(item, settings)["status"] != "ONLINE"),
            },
            "commands": {
                "total": len(commands),
                "by_status": {status: command_counts[status] for status in sorted(COMMAND_STATUSES)},
                "stale_delivered": stale_delivered,
                "lease_seconds": COMMAND_LEASE_SECONDS,
            },
            "channels": {"websocket": True, "http_pull_fallback": True},
        }

    @app.post("/api/auth/bootstrap")
    def bootstrap(request: Request, body: BootstrapRequest, db: Session = Depends(get_db)):
        if db.scalar(select(func.count(User.id))) > 0:
            audit(db, request, action="BOOTSTRAP", result="DENIED", message="Already initialized")
            raise HTTPException(status_code=409, detail="Server already initialized")
        workspace = Workspace(name=body.workspace_name); db.add(workspace); db.flush()
        user = User(username=body.username, password_hash=hash_password(body.password), role="ADMIN", workspace_id=workspace.id); db.add(user); db.commit()
        audit(db, request, action="BOOTSTRAP", result="SUCCESS", user_id=user.id, workspace_id=workspace.id, resource_type="workspace", resource_id=workspace.id)
        return {"workspace_id": workspace.id, "user_id": user.id, "access_token": create_jwt(user, settings), "token_type": "bearer"}

    @app.post("/api/auth/login")
    def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
        user = db.scalar(select(User).where(User.username == body.username))
        password_valid = verify_login_password(body.password, user.password_hash if user else None)
        if not user or not password_valid or user.status != "ACTIVE":
            audit(db, request, action="LOGIN", result="DENIED", user_id=user.id if user else None, workspace_id=user.workspace_id if user else None, message="Invalid credentials")
            raise HTTPException(status_code=401, detail="Unauthorized")
        audit(db, request, action="LOGIN", result="SUCCESS", user_id=user.id, workspace_id=user.workspace_id)
        return {"access_token": create_jwt(user, settings), "token_type": "bearer", "role": user.role, "workspace_id": user.workspace_id}

    @app.get("/api/auth/me")
    def auth_me(user: User = Depends(current_user), db: Session = Depends(get_db)):
        workspace = db.get(Workspace, user.workspace_id) if user.workspace_id else None
        return _user_dict(user, workspace.name if workspace else None) | {"permissions": public_permissions(db, user)}

    def active_invitation(db: Session, raw_token: str) -> Invitation:
        item = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(raw_token)).with_for_update())
        if not item:
            raise HTTPException(status_code=404, detail="Invitation not found")
        if _invitation_status(item) != "ACTIVE":
            raise HTTPException(status_code=410, detail="Invitation is no longer available")
        workspace = db.get(Workspace, item.workspace_id)
        if not workspace or workspace.status != "ACTIVE":
            raise HTTPException(status_code=410, detail="Invitation is no longer available")
        return item

    @app.get("/api/auth/invitations/{raw_token}")
    def invitation_detail(raw_token: str, db: Session = Depends(get_db)):
        item = active_invitation(db, raw_token)
        workspace = db.get(Workspace, item.workspace_id)
        return _invitation_dict(item, workspace.name if workspace else "")

    @app.post("/api/auth/invitations/{raw_token}/accept")
    def accept_invitation(raw_token: str, body: InvitationAccept, request: Request, db: Session = Depends(get_db)):
        item = active_invitation(db, raw_token)
        username = body.username.strip().lower()
        if len(username) < 3 or db.scalar(select(User.id).where(func.lower(User.username) == username)):
            raise HTTPException(status_code=409, detail="Username is not available")
        user = User(
            username=username,
            password_hash=hash_password(body.password),
            role=item.role,
            workspace_id=item.workspace_id,
            status="ACTIVE",
        )
        db.add(user)
        db.flush()
        item.accepted_at = now()
        item.accepted_user_id = user.id
        db.commit()
        audit(
            db,
            request,
            action="INVITATION_ACCEPT",
            result="SUCCESS",
            user_id=user.id,
            workspace_id=user.workspace_id,
            resource_type="invitation",
            resource_id=item.id,
        )
        return {
            "access_token": create_jwt(user, settings),
            "token_type": "bearer",
            "user": _user_dict(user),
        }

    @app.post("/api/auth/password")
    def change_password(body: PasswordChange, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not verify_login_password(body.current_password, user.password_hash):
            audit(db, request, action="PASSWORD_CHANGE", result="DENIED", user_id=user.id, workspace_id=user.workspace_id)
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        if verify_login_password(body.new_password, user.password_hash):
            raise HTTPException(status_code=422, detail="New password must be different")
        user.password_hash = hash_password(body.new_password)
        db.commit()
        audit(db, request, action="PASSWORD_CHANGE", result="SUCCESS", user_id=user.id, workspace_id=user.workspace_id, resource_type="user", resource_id=user.id)
        return {"ok": True, "access_token": create_jwt(user, settings)}

    @app.get("/api/invitations")
    def list_invitations(user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role not in {"ADMIN", "OWNER"}:
            raise HTTPException(status_code=403, detail="Forbidden")
        query = select(Invitation).order_by(Invitation.created_at.desc())
        if user.role != "ADMIN":
            query = query.where(Invitation.workspace_id == user.workspace_id)
        workspaces = {item.id: item.name for item in db.scalars(select(Workspace))}
        return [_invitation_dict(item, workspaces.get(item.workspace_id, "")) for item in db.scalars(query)]

    @app.post("/api/invitations")
    def create_invitation(body: InvitationCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role not in {"ADMIN", "OWNER"}:
            deny(request, db, action="INVITATION_CREATE", user=user)
        role = body.role.upper()
        if user.role == "OWNER" and role != "MEMBER":
            deny(request, db, action="INVITATION_CREATE", user=user)
        workspace_id = body.workspace_id if user.role == "ADMIN" else user.workspace_id
        workspace = db.get(Workspace, workspace_id) if workspace_id else None
        if not workspace or workspace.status != "ACTIVE":
            raise HTTPException(status_code=404, detail="Workspace not found")
        raw_token = new_invitation_token()
        item = Invitation(
            workspace_id=workspace.id,
            role=role,
            token_hash=token_hash(raw_token),
            created_by=user.id,
            expires_at=now() + timedelta(hours=body.expires_hours),
        )
        db.add(item)
        db.commit()
        audit(db, request, action="INVITATION_CREATE", result="SUCCESS", user_id=user.id, workspace_id=workspace.id, resource_type="invitation", resource_id=item.id)
        return _invitation_dict(item, workspace.name) | {
            "token": raw_token,
            "invite_path": f"/invite/{raw_token}",
        }

    @app.delete("/api/invitations/{invitation_id}")
    def revoke_invitation(invitation_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role not in {"ADMIN", "OWNER"}:
            deny(request, db, action="INVITATION_REVOKE", user=user)
        item = db.get(Invitation, invitation_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id):
            raise HTTPException(status_code=404, detail="Invitation not found")
        if _invitation_status(item) != "ACTIVE":
            raise HTTPException(status_code=409, detail="Invitation is not active")
        item.revoked_at = now()
        db.commit()
        audit(db, request, action="INVITATION_REVOKE", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, resource_type="invitation", resource_id=item.id)
        return {"ok": True}

    @app.post("/api/workspaces")
    def create_workspace(request: Request, body: WorkspaceCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role != "ADMIN": deny(request, db, action="WORKSPACE_CREATE", user=user)
        item = Workspace(name=body.name); db.add(item); db.commit()
        audit(db, request, action="WORKSPACE_CREATE", result="SUCCESS", user_id=user.id, workspace_id=item.id, resource_type="workspace", resource_id=item.id)
        return {"id": item.id, "name": item.name}

    @app.get("/api/workspaces")
    def list_workspaces(q: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), paged_response: bool = Query(False, alias="paged"), user: User = Depends(current_user), db: Session = Depends(get_db)):
        query = select(Workspace) if user.role == "ADMIN" else select(Workspace).where(Workspace.id == user.workspace_id)
        if q.strip(): query = query.where(Workspace.name.ilike(f"%{q.strip()}%"))
        query = query.order_by(Workspace.created_at.desc())
        if paged_response: return paged(db, query, _workspace_dict, page=page, page_size=page_size)
        return [{"id": item.id, "name": item.name, "status": item.status} for item in db.scalars(query)]

    @app.get("/api/workspaces/{workspace_id}")
    def workspace_detail(workspace_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = db.get(Workspace, workspace_id)
        if not item or (user.role != "ADMIN" and user.workspace_id != item.id):
            raise HTTPException(status_code=404, detail="Workspace not found")
        return _workspace_dict(item) | {
            "user_count": int(db.scalar(select(func.count(User.id)).where(User.workspace_id == item.id)) or 0),
            "agent_count": int(db.scalar(select(func.count(Agent.id)).where(Agent.workspace_id == item.id)) or 0),
            "profile_count": int(db.scalar(select(func.count(Profile.id)).where(Profile.workspace_id == item.id)) or 0),
            "account_count": int(db.scalar(select(func.count(Account.id)).where(Account.workspace_id == item.id)) or 0),
            "task_count": int(db.scalar(select(func.count(Task.id)).where(Task.workspace_id == item.id)) or 0),
        }

    @app.patch("/api/workspaces/{workspace_id}")
    def update_workspace(workspace_id: str, body: WorkspaceUpdate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = db.get(Workspace, workspace_id)
        if not item: raise HTTPException(status_code=404, detail="Workspace not found")
        if user.role != "ADMIN": deny(request, db, action="WORKSPACE_UPDATE", user=user)
        if body.name is not None: item.name = body.name.strip()
        if body.status is not None:
            if body.status.upper() not in {"ACTIVE", "DISABLED"}: raise HTTPException(status_code=422, detail="Invalid workspace status")
            item.status = body.status.upper()
        db.commit(); audit(db, request, action="WORKSPACE_UPDATE", result="SUCCESS", user_id=user.id, workspace_id=item.id, resource_type="workspace", resource_id=item.id)
        return _workspace_dict(item)

    @app.get("/api/users")
    def list_users(q: str = "", include_deleted: bool = False, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), paged_response: bool = Query(False, alias="paged"), user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role == "MEMBER": raise HTTPException(status_code=403, detail="Forbidden")
        query = select(User)
        if user.role != "ADMIN": query = query.where(User.workspace_id == user.workspace_id)
        if not include_deleted: query = query.where(User.status != "DELETED")
        if q.strip(): query = query.where(User.username.ilike(f"%{q.strip()}%"))
        query = query.order_by(User.created_at.desc())
        workspace_names = {item.id: item.name for item in db.scalars(select(Workspace))}
        serialize = lambda item: _user_dict(item, workspace_names.get(item.workspace_id))
        if paged_response: return paged(db, query, serialize, page=page, page_size=page_size)
        return [serialize(item) for item in db.scalars(query)]

    @app.get("/api/users/{user_id}/ai-policy")
    def get_user_ai_policy(user_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        target = db.get(User, user_id)
        if not target or user.role == "MEMBER" or (user.role != "ADMIN" and target.workspace_id != user.workspace_id):
            deny(request, db, action="USER_AI_POLICY_READ", user=user)
        rows = {item.feature: item for item in db.scalars(select(UserAIPolicy).where(UserAIPolicy.user_id == target.id))}
        defaults = {"CHAT", "WRITING", "ANALYSIS", "TASKS"}
        return {
            "user_id": target.id,
            "features": {feature: (rows[feature].enabled if feature in rows else feature in defaults) for feature in FEATURES},
            "models": {feature: {"provider_id": rows[feature].provider_id, "model": rows[feature].model} for feature in FEATURES if feature in rows and rows[feature].provider_id},
        }

    @app.put("/api/users/{user_id}/ai-policy")
    def update_user_ai_policy(user_id: str, body: UserAIPolicyUpdate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        target = db.get(User, user_id)
        if not target or user.role == "MEMBER" or (user.role != "ADMIN" and target.workspace_id != user.workspace_id):
            deny(request, db, action="USER_AI_POLICY_UPDATE", user=user)
        if body.provider_id:
            provider = db.get(AIProvider, body.provider_id)
            if not provider or provider.workspace_id != target.workspace_id or provider.status != "ENABLED":
                raise HTTPException(status_code=422, detail="Provider 不属于该工作区或未启用")
            allowed = set(provider.available_models or [])
            if provider.default_model:
                allowed.add(provider.default_model)
            if body.model and allowed and body.model not in allowed:
                raise HTTPException(status_code=422, detail="模型不属于所选服务商")
        item = db.scalar(select(UserAIPolicy).where(UserAIPolicy.user_id == target.id, UserAIPolicy.feature == body.feature))
        if not item:
            item = UserAIPolicy(user_id=target.id, workspace_id=target.workspace_id, feature=body.feature, updated_by=user.id)
            db.add(item)
        item.enabled = body.enabled
        item.provider_id = body.provider_id if body.enabled else None
        item.model = ((body.model or "").strip() or None) if body.enabled else None
        item.updated_by = user.id
        item.updated_at = now()
        db.commit()
        audit(db, request, action="USER_AI_POLICY_UPDATE", result="SUCCESS", user_id=user.id, workspace_id=target.workspace_id, resource_type="user_ai_policy", resource_id=item.id)
        return {"ok": True, "feature": item.feature, "enabled": item.enabled, "provider_id": item.provider_id, "model": item.model}

    @app.post("/api/users")
    def create_user(request: Request, body: UserCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
        role = body.role.upper()
        if role not in {"ADMIN", "OWNER", "MEMBER"} or user.role not in {"ADMIN", "OWNER"}: deny(request, db, action="USER_CREATE", user=user)
        workspace_id = body.workspace_id if user.role == "ADMIN" else user.workspace_id
        if role == "ADMIN" and user.role != "ADMIN": deny(request, db, action="USER_CREATE", user=user)
        if not workspace_id or not db.get(Workspace, workspace_id): raise HTTPException(status_code=404, detail="Workspace not found")
        item = User(username=body.username, password_hash=hash_password(body.password), role=role, workspace_id=workspace_id); db.add(item); db.commit()
        audit(db, request, action="USER_CREATE", result="SUCCESS", user_id=user.id, workspace_id=workspace_id, resource_type="user", resource_id=item.id)
        return {"id": item.id, "username": item.username, "role": item.role, "workspace_id": item.workspace_id}

    @app.patch("/api/users/{user_id}")
    def update_user(user_id: str, body: UserUpdate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = db.get(User, user_id)
        if not item: raise HTTPException(status_code=404, detail="User not found")
        if user.role == "MEMBER" or (user.role != "ADMIN" and item.workspace_id != user.workspace_id): deny(request, db, action="USER_UPDATE", user=user)
        previous_status = item.status
        role = body.role.upper() if body.role else item.role
        workspace_id = body.workspace_id if body.workspace_id is not None else item.workspace_id
        if user.role != "ADMIN" and (role == "ADMIN" or workspace_id != user.workspace_id): deny(request, db, action="USER_UPDATE", user=user)
        if body.username is not None: item.username = body.username.strip()
        if body.password is not None:
            item.password_hash = hash_password(body.password)
        item.role = role; item.workspace_id = workspace_id
        if body.status is not None:
            next_status = body.status.upper()
            if next_status not in {"ACTIVE", "DISABLED", "DELETED"}: raise HTTPException(status_code=422, detail="Invalid user status")
            if item.id == user.id and next_status != "ACTIVE": raise HTTPException(status_code=422, detail="Cannot disable or delete current user")
            if next_status == "DELETED" and user.role != "ADMIN": deny(request, db, action="USER_DELETE", user=user)
            if item.status == "DELETED" and next_status != "DELETED" and user.role != "ADMIN": deny(request, db, action="USER_RESTORE", user=user)
            item.status = next_status
        db.commit()
        action = "USER_DELETE" if body.status and body.status.upper() == "DELETED" else "USER_RESTORE" if previous_status == "DELETED" and body.status and body.status.upper() == "ACTIVE" else "USER_UPDATE"
        audit(db, request, action=action, result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, resource_type="user", resource_id=item.id)
        return _user_dict(item)

    @app.post("/api/agents/register")
    def register_agent(request: Request, body: AgentRegister, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role not in {"ADMIN", "OWNER"}: deny(request, db, action="AGENT_REGISTER", user=user)
        workspace_id = body.workspace_id if user.role == "ADMIN" and body.workspace_id else user.workspace_id
        if not workspace_id or not db.get(Workspace, workspace_id):
            raise HTTPException(status_code=404, detail="Workspace not found")
        country = _request_ip_country(request)
        device_id = body.device_id.strip()
        item = Agent(workspace_id=workspace_id, agent_name=body.agent_name, machine_name=body.machine_name, client_version=body.client_version, token_hash="", bound_device_id=device_id or None, bound_ip=client_ip(request) if device_id else None, last_ip=client_ip(request), ip_country=country, bound_at=now() if device_id else None, registered_by_user_id=user.id)
        db.add(item); db.flush(); raw_token, _ = create_token(db, item.id); db.commit()
        audit(db, request, action="AGENT_REGISTER", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, agent_id=item.id, resource_type="agent", resource_id=item.id)
        return {"agent_id": item.id, "agent_token": raw_token, "workspace_id": item.workspace_id}

    @app.post("/api/agents/{agent_id}/token/rotate")
    def rotate_token(agent_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        agent = db.get(Agent, agent_id)
        if not agent: raise HTTPException(status_code=404, detail="Agent not found")
        require_agent_manager(request, db, user, agent)
        issued = now()
        for old in db.scalars(select(AgentToken).where(AgentToken.agent_id == agent.id, AgentToken.status == TOKEN_ACTIVE)):
            old.status = TOKEN_REVOKED; old.revoked_at = issued
        raw_token, token = create_token(db, agent.id); db.commit()
        audit(db, request, action="AGENT_TOKEN_ROTATE", result="SUCCESS", user_id=user.id, workspace_id=agent.workspace_id, agent_id=agent.id, resource_type="agent_token", resource_id=token.token_id)
        return {"agent_id": agent.id, "token_id": token.token_id, "agent_token": raw_token, "expires_at": _dt(token.expires_at)}

    @app.post("/api/agents/{agent_id}/token/revoke")
    def revoke_token(agent_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        agent = db.get(Agent, agent_id)
        if not agent: raise HTTPException(status_code=404, detail="Agent not found")
        require_agent_manager(request, db, user, agent)
        when = now(); changed = 0
        for token in db.scalars(select(AgentToken).where(AgentToken.agent_id == agent.id, AgentToken.status == TOKEN_ACTIVE)):
            token.status = TOKEN_REVOKED; token.revoked_at = when; changed += 1
        db.commit()
        audit(db, request, action="AGENT_TOKEN_REVOKE", result="SUCCESS", user_id=user.id, workspace_id=agent.workspace_id, agent_id=agent.id, resource_type="agent", resource_id=agent.id)
        return {"agent_id": agent.id, "revoked": changed}

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        agent = db.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        require_agent_manager(request, db, user, agent)
        when = now(); revoked = 0
        for token in db.scalars(select(AgentToken).where(AgentToken.agent_id == agent.id, AgentToken.status == TOKEN_ACTIVE)):
            token.status = TOKEN_REVOKED; token.revoked_at = when; revoked += 1
        agent.status = "DELETED"
        db.commit()
        audit(db, request, action="AGENT_DELETE", result="SUCCESS", user_id=user.id, workspace_id=agent.workspace_id, agent_id=agent.id, resource_type="agent", resource_id=agent.id, message=f"revoked_tokens={revoked}")
        return {"agent_id": agent.id, "status": "DELETED", "revoked": revoked}

    @app.post("/api/agents/{agent_id}/recover")
    def recover_agent(agent_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Reactivate a deleted runtime with a fresh, one-time Token.

        Recovery never restores an old credential or device binding. The next
        successful heartbeat binds the new Token to the recovering Windows
        device, preventing a deleted runtime from being silently reused.
        """
        agent = db.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        require_agent_manager(request, db, user, agent)
        if agent.status != "DELETED":
            raise HTTPException(status_code=409, detail="Agent is not deleted")
        issued = now()
        for old in db.scalars(select(AgentToken).where(AgentToken.agent_id == agent.id, AgentToken.status == TOKEN_ACTIVE)):
            old.status = TOKEN_REVOKED
            old.revoked_at = issued
        agent.status = "OFFLINE"
        agent.bound_device_id = None
        agent.bound_ip = None
        agent.bound_at = None
        agent.last_heartbeat = None
        agent.last_ip = None
        raw_token, token = create_token(db, agent.id)
        db.commit()
        audit(db, request, action="AGENT_RECOVER", result="SUCCESS", user_id=user.id, workspace_id=agent.workspace_id, agent_id=agent.id, resource_type="agent", resource_id=agent.id, message="new token issued; device binding reset")
        return {"agent_id": agent.id, "token_id": token.token_id, "agent_token": raw_token, "workspace_id": agent.workspace_id, "status": agent.status}

    @app.post("/api/agents/heartbeat")
    def heartbeat(request: Request, body: Heartbeat, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
        if body.agent_id != agent.id: deny(request, db, action="AGENT_HEARTBEAT", agent=agent)
        country = _request_ip_country(request)
        device_id = (body.device_id or request.headers.get("x-laogu-device-id", "")).strip()
        if not device_id:
            # 旧版 Agent 可能还没有设备指纹；允许它暂时心跳，但不会获得
            # 绑定状态。升级到新版后首次心跳才会锁定 Token。
            agent.last_ip = client_ip(request)
            agent.ip_country = country
        if agent.bound_device_id and device_id != agent.bound_device_id:
            deny(request, db, action="AGENT_HEARTBEAT", agent=agent, message="Agent Token 已绑定其他电脑")
        if device_id and not agent.bound_device_id:
            agent.bound_device_id = device_id
            agent.bound_ip = client_ip(request)
            agent.bound_at = now()
        if device_id:
            agent.last_ip = client_ip(request)
            agent.ip_country = country
        agent.status = "ONLINE"; agent.last_heartbeat = body.timestamp; agent.profile_count = body.profile_count; agent.running_task_count = body.running_task_count
        if body.client_version:
            agent.client_version = body.client_version
        db.commit()
        return _agent_dict(agent, settings)

    @app.get("/api/agents")
    def agents(q: str = "", status: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), paged_response: bool = Query(False, alias="paged"), user: User = Depends(current_user), db: Session = Depends(get_db)):
        query = select(Agent) if user.role == "ADMIN" else select(Agent).where(Agent.workspace_id == user.workspace_id)
        if q.strip(): query = query.where(Agent.agent_name.ilike(f"%{q.strip()}%"))
        query = query.order_by(Agent.created_at.desc())
        items = list(db.scalars(query))
        if status.strip(): items = [item for item in items if _agent_dict(item, settings)["status"] == status.upper()]
        if paged_response:
            total = len(items); offset = (max(1, page) - 1) * min(100, max(1, page_size)); size = min(100, max(1, page_size))
            return {"items": [_agent_dict(item, settings) for item in items[offset:offset + size]], "page": page, "page_size": size, "total": total, "pages": (total + size - 1) // size}
        return [_agent_dict(item, settings) for item in items]

    @app.get("/api/agents/{agent_id}")
    def agent_detail(agent_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = db.get(Agent, agent_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id):
            if item:
                audit(db, request, action="AGENT_READ", result="DENIED", user_id=user.id, workspace_id=user.workspace_id, resource_type="agent", resource_id=agent_id, message="Agent not found")
            raise HTTPException(status_code=404, detail="Agent not found")
        return _agent_dict(item, settings) | {
            "profiles": [_profile_dict(profile, db.scalar(select(Account).where(Account.agent_id == item.id, Account.profile_id == profile.profile_id))) for profile in db.scalars(select(Profile).where(Profile.agent_id == item.id))],
            "accounts": [_account_dict(account) for account in db.scalars(select(Account).where(Account.agent_id == item.id))],
            "recent_tasks": [_task_dict(task) for task in db.scalars(select(Task).where(Task.agent_id == item.id).order_by(Task.created_at.desc()).limit(20))],
            "recent_activities": [_activity_dict(activity) for activity in db.scalars(select(Activity).where(Activity.agent_id == item.id).order_by(Activity.timestamp.desc()).limit(20))],
        }

    @app.post("/api/accounts/sync")
    def sync_accounts(request: Request, body: AccountSync, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
        if body.agent_id != agent.id: deny(request, db, action="ACCOUNT_SYNC", agent=agent)
        for incoming in body.items:
            profile = db.scalar(select(Profile).where(Profile.agent_id == agent.id, Profile.profile_id == incoming.profile_id))
            if profile is None: profile = Profile(workspace_id=agent.workspace_id, agent_id=agent.id, profile_id=incoming.profile_id); db.add(profile)
            profile.instance_id = incoming.instance_id; profile.x_username = incoming.x_username; profile.x_account_id = incoming.x_account_id; profile.status = incoming.browser_status
            account = db.scalar(select(Account).where(Account.agent_id == agent.id, Account.profile_id == incoming.profile_id))
            if account is None: account = Account(workspace_id=agent.workspace_id, agent_id=agent.id, profile_id=incoming.profile_id); db.add(account)
            for key, value in incoming.model_dump().items(): setattr(account, key, value)
        agent.profile_count = len(body.items); db.commit()
        return {"ok": True, "synced": len(body.items)}

    @app.post("/api/agent/automation-metrics")
    def sync_automation_metric(
        request: Request,
        body: AutomationMetricSync,
        agent: Agent = Depends(current_agent),
        db: Session = Depends(get_db),
    ):
        if body.agent_id != agent.id:
            deny(request, db, action="AUTOMATION_METRIC_SYNC", agent=agent)
        existing = db.scalar(select(AutomationMetric).where(AutomationMetric.run_id == body.run_id))
        if existing is not None:
            if existing.agent_id != agent.id:
                raise HTTPException(status_code=409, detail="Automation run ID already belongs to another Agent")
            return {"ok": True, "idempotent": True, "run_id": existing.run_id}
        profile = db.scalar(
            select(Profile).where(Profile.agent_id == agent.id, Profile.profile_id == body.profile_id)
        )
        account = db.scalar(
            select(Account).where(Account.agent_id == agent.id, Account.profile_id == body.profile_id)
        )
        if profile is None or account is None:
            raise HTTPException(status_code=409, detail="Profile/account must be synchronized before metrics")
        if body.x_account_id and account.x_account_id and body.x_account_id != account.x_account_id:
            raise HTTPException(status_code=409, detail="X account does not match the synchronized Profile")
        metric = AutomationMetric(
            run_id=body.run_id,
            workspace_id=agent.workspace_id,
            agent_id=agent.id,
            profile_id=body.profile_id,
            x_account_id=account.x_account_id or body.x_account_id,
            account_tag=body.account_tag,
            metric_date=body.metric_date,
            started_at=body.started_at,
            finished_at=body.finished_at,
            status=body.status,
            processed_count=body.processed_count,
            likes=body.likes,
            follows=body.follows,
            comments=body.comments,
            scanned_posts=body.scanned_posts,
            own_followers=body.own_followers,
            own_following=body.own_following,
        )
        db.add(metric)
        db.commit()
        return {"ok": True, "idempotent": False, "run_id": metric.run_id}

    @app.get("/api/profiles")
    def profiles(q: str = "", status: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), paged_response: bool = Query(False, alias="paged"), user: User = Depends(current_user), db: Session = Depends(get_db)):
        query = select(Profile) if user.role == "ADMIN" else select(Profile).where(Profile.workspace_id == user.workspace_id)
        if q.strip(): query = query.where((Profile.profile_id.ilike(f"%{q.strip()}%")) | (Profile.x_username.ilike(f"%{q.strip()}%")))
        query = query.order_by(Profile.profile_id)
        records = list(db.scalars(query))
        accounts_by_key = {(account.agent_id, account.profile_id): account for account in db.scalars(select(Account)) if user.role == "ADMIN" or account.workspace_id == user.workspace_id}
        serialized = [_profile_dict(item, accounts_by_key.get((item.agent_id, item.profile_id))) for item in records]
        if status.strip(): serialized = [item for item in serialized if status.upper() in {item["browser_status"], item["login_status"], item["account_status"]}]
        if paged_response:
            size = min(100, max(1, page_size)); offset = (max(1, page) - 1) * size; total = len(serialized)
            return {"items": serialized[offset:offset + size], "page": page, "page_size": size, "total": total, "pages": (total + size - 1) // size}
        return serialized

    @app.get("/api/accounts")
    def accounts(q: str = "", status: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), paged_response: bool = Query(False, alias="paged"), user: User = Depends(current_user), db: Session = Depends(get_db)):
        query = select(Account) if user.role == "ADMIN" else select(Account).where(Account.workspace_id == user.workspace_id)
        if q.strip(): query = query.where((Account.x_username.ilike(f"%{q.strip()}%")) | (Account.x_account_id.ilike(f"%{q.strip()}%")) | (Account.profile_id.ilike(f"%{q.strip()}%")))
        query = query.order_by(Account.last_checked.desc())
        if status.strip(): query = query.where(Account.login_status == status.upper())
        if paged_response: return paged(db, query, _account_dict, page=page, page_size=page_size)
        return [_account_dict(item) for item in db.scalars(query)]

    @app.get("/api/accounts/{account_id}")
    def account_detail(account_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = db.get(Account, account_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id):
            if item: deny(request, db, action="ACCOUNT_READ", user=user)
            raise HTTPException(status_code=404, detail="Account not found")
        return _account_dict(item)

    @app.post("/api/tasks")
    def create_task(request: Request, body: TaskCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if body.task_type == "script.execute": raise HTTPException(status_code=422, detail="Use the Script execute endpoint")
        if body.task_type not in ALLOWED_TASK_TYPES: raise HTTPException(status_code=422, detail="Unsupported task type")
        safety = evaluate_task(body.task_type, body.params)
        if not safety.allowed: raise HTTPException(status_code=422, detail=safety.message)
        allowed = {"query"} if body.task_type == "x.search" else ({"url"} if body.task_type == "browser.open_url" else set())
        if set(body.params) - allowed or (body.task_type == "x.search" and not str(body.params.get("query") or "").strip()): raise HTTPException(status_code=422, detail="Unsupported task parameters")
        query = select(Profile).where(Profile.profile_id == body.profile_id)
        if user.role != "ADMIN": query = query.where(Profile.workspace_id == user.workspace_id)
        profiles = list(db.scalars(query))
        if len(profiles) != 1: raise HTTPException(status_code=404 if not profiles else 409, detail="Profile not found" if not profiles else "Profile is ambiguous")
        profile = profiles[0]; agent = db.get(Agent, profile.agent_id)
        if not agent or agent.workspace_id != profile.workspace_id or (user.role != "ADMIN" and user.workspace_id != agent.workspace_id): deny(request, db, action="TASK_CREATE", user=user, agent=agent, message="Workspace routing mismatch")
        task = Task(workspace_id=profile.workspace_id, agent_id=profile.agent_id, profile_id=profile.profile_id, x_account_id=profile.x_account_id, task_type=body.task_type, params=body.params, timeout=body.timeout); db.add(task); db.commit()
        audit(db, request, action="TASK_CREATE", result="SUCCESS", user_id=user.id, workspace_id=task.workspace_id, agent_id=task.agent_id, resource_type="task", resource_id=task.id)
        return _task_dict(task)

    @app.get("/api/tasks")
    def tasks(q: str = "", status: str = "", profile_id: str = "", agent_id: str = "", period: str = "all", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), paged_response: bool = Query(False, alias="paged"), user: User = Depends(current_user), db: Session = Depends(get_db)):
        query = select(Task).order_by(Task.created_at.desc()) if user.role == "ADMIN" else select(Task).where(Task.workspace_id == user.workspace_id).order_by(Task.created_at.desc())
        if q.strip(): query = query.where((Task.id.ilike(f"%{q.strip()}%")) | (Task.task_type.ilike(f"%{q.strip()}%")))
        if status.strip(): query = query.where(Task.status == status.upper())
        if profile_id.strip(): query = query.where(Task.profile_id == profile_id.strip())
        if agent_id.strip(): query = query.where(Task.agent_id == agent_id.strip())
        since = period_since(period)
        if since: query = query.where(Task.created_at >= since)
        if paged_response: return paged(db, query, _task_dict, page=page, page_size=page_size)
        return [_task_dict(item) for item in db.scalars(query)]

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = db.get(Task, task_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id):
            if item: deny(request, db, action="TASK_READ", user=user)
            raise HTTPException(status_code=404, detail="Task not found")
        activity = db.scalar(select(Activity).where(Activity.task_id == item.id))
        return _task_dict(item) | {"activity": _activity_dict(activity) if activity else None}

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = db.get(Task, task_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id): raise HTTPException(status_code=404, detail="Task not found")
        if item.status not in TERMINAL_STATUSES: item.status = "CANCELLED"; item.finished_at = now(); db.commit()
        audit(db, request, action="TASK_CANCEL", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, agent_id=item.agent_id, resource_type="task", resource_id=item.id)
        if item.task_type == "script.execute":
            audit(db, request, action="SCRIPT_STOPPED", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, agent_id=item.agent_id, profile_id=item.profile_id, task_id=item.id, script_id=item.script_id, script_version_id=item.script_version_id, resource_type="task", resource_id=item.id)
        return _task_dict(item)

    @app.post("/api/tasks/pull")
    def pull_tasks(request: Request, body: TaskPull, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
        if body.agent_id != agent.id: deny(request, db, action="TASK_PULL", agent=agent)
        lease_expired = now() - timedelta(seconds=60)
        items = list(db.scalars(select(Task).where(Task.agent_id == agent.id, or_(Task.status == "PENDING", (Task.status == "DISPATCHED") & (Task.pulled_at < lease_expired))).order_by(Task.created_at).limit(body.limit)))
        pulled = now()
        for item in items: item.status = "DISPATCHED"; item.pulled_at = pulled
        db.commit()
        return {"items": [_task_dict(item) for item in items]}

    @app.post("/api/tasks/result")
    def task_result(request: Request, body: TaskResult, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
        item = db.get(Task, body.task_id)
        if not item or item.agent_id != agent.id or body.agent_id != agent.id or body.profile_id != item.profile_id: raise HTTPException(status_code=404, detail="Task not found for agent")
        if item.status in TERMINAL_STATUSES: return {"ok": True, "idempotent": True, "task": _task_dict(item)}
        if body.status not in TERMINAL_STATUSES: raise HTTPException(status_code=422, detail="Invalid terminal status")
        item.status = body.status; item.started_at = body.started_at; item.finished_at = body.finished_at; item.duration = body.duration; item.result = redact_payload(body.result); item.error = redact(body.error or "")
        result_logs = item.result.get("logs", []) if isinstance(item.result, dict) and isinstance(item.result.get("logs", []), list) else []
        db.add(Activity(workspace_id=item.workspace_id, agent_id=item.agent_id, profile_id=item.profile_id, x_account_id=item.x_account_id, task_id=item.id, script_id=item.script_id, script_version_id=item.script_version_id, activity_type=item.task_type, status=item.status, duration=item.duration, summary=item.task_type, result=item.result, logs=result_logs[:500], timestamp=item.finished_at or now())); db.commit()
        audit(db, request, action="TASK_RESULT", result="SUCCESS", workspace_id=item.workspace_id, agent_id=agent.id, resource_type="task", resource_id=item.id)
        if item.task_type == "script.execute":
            audit(db, request, action="SCRIPT_FAILED" if item.status != "SUCCESS" else "SCRIPT_EXECUTED", result=item.status, workspace_id=item.workspace_id, agent_id=agent.id, profile_id=item.profile_id, task_id=item.id, script_id=item.script_id, script_version_id=item.script_version_id, resource_type="task", resource_id=item.id)
        return {"ok": True, "idempotent": False, "task": _task_dict(item)}

    @app.get("/api/activities")
    def activities(q: str = "", action: str = "", period: str = "all", agent_id: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), paged_response: bool = Query(False, alias="paged"), user: User = Depends(current_user), db: Session = Depends(get_db)):
        query = select(Activity).order_by(Activity.timestamp.desc()) if user.role == "ADMIN" else select(Activity).where(Activity.workspace_id == user.workspace_id).order_by(Activity.timestamp.desc())
        if q.strip(): query = query.where((Activity.profile_id.ilike(f"%{q.strip()}%")) | (Activity.task_id.ilike(f"%{q.strip()}%")) | (Activity.activity_type.ilike(f"%{q.strip()}%")))
        if action.strip(): query = query.where(Activity.activity_type == action.strip())
        if agent_id.strip(): query = query.where(Activity.agent_id == agent_id.strip())
        since = period_since(period)
        if since: query = query.where(Activity.timestamp >= since)
        if paged_response: return paged(db, query, _activity_dict, page=page, page_size=page_size)
        return [_activity_dict(item) for item in db.scalars(query)]

    @app.get("/api/statistics")
    def statistics(period: str = "all", user: User = Depends(current_user), db: Session = Depends(get_db)):
        query = select(Task) if user.role == "ADMIN" else select(Task).where(Task.workspace_id == user.workspace_id)
        since = period_since(period)
        if since: query = query.where(Task.created_at >= since)
        items = list(db.scalars(query)); statuses = Counter(item.status for item in items); types = Counter(item.task_type for item in items)
        accounts_query = select(Account) if user.role == "ADMIN" else select(Account).where(Account.workspace_id == user.workspace_id)
        accounts = list(db.scalars(accounts_query)); agents_query = select(Agent) if user.role == "ADMIN" else select(Agent).where(Agent.workspace_id == user.workspace_id)
        agents = list(db.scalars(agents_query)); logged = sum(1 for account in accounts if account.login_status == "LOGGED_IN")
        return {"period": period, "total_tasks": len(items), "success_tasks": statuses["SUCCESS"], "failed_tasks": statuses["FAILED"], "timeout_tasks": statuses["TIMEOUT"], "running_tasks": statuses["RUNNING"] + statuses["DISPATCHED"], "by_task_type": dict(types), "accounts": {"total": len(accounts), "logged_in": logged, "not_logged_in": sum(1 for account in accounts if account.login_status == "NOT_LOGGED_IN"), "unknown": sum(1 for account in accounts if account.login_status == "UNKNOWN")}, "agents": {"total": len(agents), "online": sum(1 for agent in agents if _agent_dict(agent, settings)["status"] == "ONLINE"), "offline": sum(1 for agent in agents if _agent_dict(agent, settings)["status"] != "ONLINE")}}

    @app.get("/api/dashboard")
    def dashboard(period: str = "today", user: User = Depends(current_user), db: Session = Depends(get_db)):
        workspace_query = select(Workspace) if user.role == "ADMIN" else select(Workspace).where(Workspace.id == user.workspace_id)
        workspaces = list(db.scalars(workspace_query))
        agent_query = select(Agent) if user.role == "ADMIN" else select(Agent).where(Agent.workspace_id == user.workspace_id)
        agents = list(db.scalars(agent_query))
        profile_query = select(Profile) if user.role == "ADMIN" else select(Profile).where(Profile.workspace_id == user.workspace_id)
        account_query = select(Account) if user.role == "ADMIN" else select(Account).where(Account.workspace_id == user.workspace_id)
        task_query = select(Task) if user.role == "ADMIN" else select(Task).where(Task.workspace_id == user.workspace_id)
        since = period_since(period)
        if since: task_query = task_query.where(Task.created_at >= since)
        tasks_list = list(db.scalars(task_query)); statuses = Counter(item.status for item in tasks_list); accounts = list(db.scalars(account_query))
        online = sum(1 for agent in agents if _agent_dict(agent, settings)["status"] == "ONLINE")
        return {"period": period, "workspace_count": len(workspaces), "agent_count": len(agents), "online_agents": online, "offline_agents": len(agents) - online, "agent_online_rate": round(online / len(agents), 4) if agents else 0, "profile_count": int(db.scalar(select(func.count(Profile.id)).where(Profile.workspace_id.in_([item.id for item in workspaces]))) or 0) if workspaces else 0, "logged_in_accounts": sum(1 for account in accounts if account.login_status == "LOGGED_IN"), "running_tasks": statuses["RUNNING"] + statuses["DISPATCHED"], "success_tasks": statuses["SUCCESS"], "failed_tasks": statuses["FAILED"], "task_success_rate": round(statuses["SUCCESS"] / len(tasks_list), 4) if tasks_list else 0, "recent_activities": [_activity_dict(item) for item in db.scalars(select(Activity).where(Activity.workspace_id.in_([item.id for item in workspaces])).order_by(Activity.timestamp.desc()).limit(10))] if workspaces else [], "recent_tasks": [_task_dict(item) for item in db.scalars(select(Task).where(Task.workspace_id.in_([item.id for item in workspaces])).order_by(Task.created_at.desc()).limit(10))] if workspaces else []}

    @app.get("/api/audit")
    def audit_logs(request: Request, q: str = "", action: str = "", result: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), paged_response: bool = Query(False, alias="paged"), user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role == "MEMBER": deny(request, db, action="AUDIT_READ", user=user)
        query = select(AuditLog).order_by(AuditLog.timestamp.desc()) if user.role == "ADMIN" else select(AuditLog).where(AuditLog.workspace_id == user.workspace_id).order_by(AuditLog.timestamp.desc())
        if q.strip(): query = query.where((AuditLog.action.ilike(f"%{q.strip()}%")) | (AuditLog.resource_id.ilike(f"%{q.strip()}%")))
        if action.strip(): query = query.where(AuditLog.action == action.strip())
        if result.strip(): query = query.where(AuditLog.result == result.upper())
        if paged_response: return paged(db, query, audit_dict, page=page, page_size=page_size)
        return [audit_dict(item) for item in db.scalars(query)]

    register_script_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        current_agent=current_agent,
        paged=paged,
        deny=deny,
        task_serializer=_task_dict,
    )
    register_engine_update_routes(
        app,
        current_agent=current_agent,
    )
    register_ai_provider_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        paged=paged,
        deny=deny,
        cipher=credential_cipher,
        tester=provider_tester,
    )
    register_chat_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        paged=paged,
        cipher=credential_cipher,
        ai_service=ai_service,
        registry=chat_runs,
    )
    register_image_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        paged=paged,
        cipher=credential_cipher,
        image_service=image_service,
        storage_root=image_storage_root,
    )
    register_analysis_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        paged=paged,
        cipher=credential_cipher,
        analysis_service=analysis_service,
    )
    register_writing_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        paged=paged,
        cipher=credential_cipher,
        writing_service=writing_service,
    )
    register_task_proposal_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        paged=paged,
        cipher=credential_cipher,
        proposal_service=task_proposal_service,
        task_serializer=_task_dict,
    )
    register_control_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        settings=settings,
        agent_serializer=_agent_dict,
        profile_serializer=_profile_dict,
        account_serializer=_account_dict,
        task_serializer=_task_dict,
        activity_serializer=_activity_dict,
    )
    register_command_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        current_agent=current_agent,
    )
    register_remote_license_routes(
        app,
        get_db=get_db,
        current_user=current_user,
        deny=deny,
    )

    return app


app = create_app()
