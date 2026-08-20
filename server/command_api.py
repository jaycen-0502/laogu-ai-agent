from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .models import Agent, Command, CredentialCapability, Profile, Task, User, now
from .schemas import CommandAck, CommandCreate, CommandPull, CommandResult
from .security import audit, redact, redact_payload


COMMAND_STATUSES = frozenset({"PENDING", "DELIVERED", "ACKNOWLEDGED", "RUNNING", "SUCCESS", "FAILED", "CANCELLED"})
TERMINAL_COMMAND_STATUSES = frozenset({"SUCCESS", "FAILED", "CANCELLED"})
COMMAND_TYPES = frozenset({
    "START_PROFILE", "STOP_PROFILE", "START_TASK", "STOP_TASK",
    "UPDATE_PARAMS", "UPDATE_KEYWORDS", "REFRESH_PROFILE", "PROBE_CREDENTIAL_CAPABILITY",
})
COMMAND_LEASE_SECONDS = 60


def _dt(value):
    return value.isoformat() if value else None


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def sanitize_credential_probe_result(value) -> dict:
    source = value if isinstance(value, dict) else {}
    evidence = str(source.get("evidence") or "NOT_ADVERTISED")
    if evidence not in {"NOT_ADVERTISED", "ADVERTISED_CAPABILITY_METADATA"}:
        evidence = "NOT_ADVERTISED"
    read_supported = source.get("cookie_read_supported") is True
    write_supported = source.get("cookie_write_supported") is True
    return {
        "probe_version": "1",
        "browser_reachable": source.get("browser_reachable") is True,
        "cookie_read_supported": read_supported,
        "cookie_write_supported": write_supported,
        "credential_snapshot_allowed": bool(read_supported and source.get("credential_snapshot_allowed") is True and evidence == "ADVERTISED_CAPABILITY_METADATA"),
        "evidence": evidence,
    }


def store_credential_probe(db: Session, command: Command, value) -> dict:
    sanitized = sanitize_credential_probe_result(value)
    record = db.scalar(select(CredentialCapability).where(CredentialCapability.agent_id == command.agent_id, CredentialCapability.profile_id == command.profile_id))
    if record is None:
        record = CredentialCapability(workspace_id=command.workspace_id, agent_id=command.agent_id, profile_id=command.profile_id or "", command_id=command.id)
        db.add(record)
    record.command_id = command.id
    record.probe_version = sanitized["probe_version"]
    record.browser_reachable = sanitized["browser_reachable"]
    record.cookie_read_supported = sanitized["cookie_read_supported"]
    record.cookie_write_supported = sanitized["cookie_write_supported"]
    record.credential_snapshot_allowed = sanitized["credential_snapshot_allowed"]
    record.evidence = sanitized["evidence"]
    record.checked_at = now()
    return sanitized


def _command_dict(item: Command) -> dict:
    return {
        "command_id": item.id,
        "workspace_id": item.workspace_id,
        "agent_id": item.agent_id,
        "profile_id": item.profile_id,
        "task_id": item.task_id,
        "command_type": item.command_type,
        "payload": redact_payload(item.payload or {}),
        "status": item.status,
        "idempotency_key": item.idempotency_key,
        "attempts": item.attempts,
        "error": redact(item.error),
        "result": redact_payload(item.result),
        "created_at": _dt(item.created_at),
        "delivered_at": _dt(item.delivered_at),
        "acknowledged_at": _dt(item.acknowledged_at),
        "started_at": _dt(item.started_at),
        "completed_at": _dt(item.completed_at),
    }


def register_command_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
    current_agent: Callable,
) -> None:
    def visible_agent(db: Session, agent_id: str, user: User) -> Agent:
        agent = db.get(Agent, agent_id)
        if not agent or (user.role != "ADMIN" and agent.workspace_id != user.workspace_id):
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent

    def visible_command(db: Session, command_id: str, user: User) -> Command:
        item = db.get(Command, command_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id):
            raise HTTPException(status_code=404, detail="Command not found")
        return item

    def validate_target(db: Session, body: CommandCreate, user: User) -> tuple[Agent, str | None, str | None]:
        agent = visible_agent(db, body.agent_id, user)
        profile_id = body.profile_id
        task_id = body.task_id
        if body.command_type in {"START_PROFILE", "STOP_PROFILE", "UPDATE_PARAMS", "UPDATE_KEYWORDS", "REFRESH_PROFILE", "PROBE_CREDENTIAL_CAPABILITY"} and not profile_id:
            raise HTTPException(status_code=422, detail="profile_id is required for this command")
        if body.command_type in {"START_TASK", "STOP_TASK"} and not task_id:
            raise HTTPException(status_code=422, detail="task_id is required for this command")
        if task_id:
            task = db.get(Task, task_id)
            if not task or task.agent_id != agent.id or task.workspace_id != agent.workspace_id:
                raise HTTPException(status_code=404, detail="Task not found for agent")
            if profile_id and profile_id != task.profile_id:
                raise HTTPException(status_code=409, detail="Command profile does not match task profile")
            profile_id = task.profile_id
        if profile_id:
            profile = db.scalar(select(Profile).where(Profile.agent_id == agent.id, Profile.profile_id == profile_id))
            if not profile:
                raise HTTPException(status_code=404, detail="Profile not found for agent")
        return agent, profile_id, task_id

    @app.post("/api/commands")
    def create_command(
        body: CommandCreate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        if user.role not in {"ADMIN", "OWNER"}:
            raise HTTPException(status_code=403, detail="Only workspace owners can create commands")
        agent, profile_id, task_id = validate_target(db, body, user)
        if body.command_type in {"UPDATE_PARAMS", "UPDATE_KEYWORDS"}:
            mode = str((body.payload or {}).get("mode") or "NEXT_RUN").upper()
            if mode not in {"NEXT_RUN", "HOT_UPDATE"}:
                raise HTTPException(status_code=422, detail="Unsupported runtime config mode")
            if not isinstance((body.payload or {}).get("values"), (dict, list, str, int, float, bool, type(None))):
                raise HTTPException(status_code=422, detail="Invalid runtime config values")
        if body.command_type == "START_TASK":
            task_type = str((body.payload or {}).get("task_type") or "")
            if task_type not in {"browser.open_url", "x.check_login", "x.read_profile", "x.read_timeline", "x.search"}:
                raise HTTPException(status_code=422, detail="Unsafe or missing task_type")
        if body.idempotency_key:
            existing = db.scalar(select(Command).where(Command.agent_id == agent.id, Command.idempotency_key == body.idempotency_key))
            if existing:
                return {"ok": True, "idempotent": True, "command": _command_dict(existing)}
        item = Command(
            workspace_id=agent.workspace_id,
            agent_id=agent.id,
            profile_id=profile_id,
            task_id=task_id,
            command_type=body.command_type,
            payload=redact_payload(body.payload),
            idempotency_key=body.idempotency_key,
            status="PENDING",
        )
        db.add(item)
        db.commit()
        audit(db, request, action="COMMAND_CREATED", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, agent_id=item.agent_id, profile_id=item.profile_id, task_id=item.task_id, resource_type="command", resource_id=item.id)
        return {"ok": True, "idempotent": False, "command": _command_dict(item)}

    @app.get("/api/commands")
    def list_commands(
        status: str = "",
        agent_id: str = "",
        profile_id: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        query = select(Command).order_by(Command.created_at.desc())
        if user.role != "ADMIN":
            query = query.where(Command.workspace_id == user.workspace_id)
        if status.strip():
            query = query.where(Command.status == status.upper())
        if agent_id.strip():
            query = query.where(Command.agent_id == agent_id.strip())
        if profile_id.strip():
            query = query.where(Command.profile_id == profile_id.strip())
        items = list(db.scalars(query))
        size = min(100, max(1, page_size))
        offset = (max(1, page) - 1) * size
        return {"items": [_command_dict(item) for item in items[offset:offset + size]], "page": page, "page_size": size, "total": len(items), "pages": (len(items) + size - 1) // size}

    @app.get("/api/commands/metrics")
    def command_metrics(user: User = Depends(current_user), db: Session = Depends(get_db)):
        query = select(Command)
        if user.role != "ADMIN":
            query = query.where(Command.workspace_id == user.workspace_id)
        items = list(db.scalars(query))
        counts = {status: sum(1 for item in items if item.status == status) for status in COMMAND_STATUSES}
        current = datetime.now().astimezone()
        stale = 0
        for item in items:
            if item.status != "DELIVERED" or not item.delivered_at:
                continue
            delivered_at = item.delivered_at
            if delivered_at.tzinfo is None:
                stale += int(datetime.now() - delivered_at >= timedelta(seconds=COMMAND_LEASE_SECONDS))
            else:
                stale += int(current - delivered_at.astimezone(current.tzinfo) >= timedelta(seconds=COMMAND_LEASE_SECONDS))
        return {"total": len(items), "by_status": counts, "stale_delivered": stale, "lease_seconds": COMMAND_LEASE_SECONDS}

    @app.get("/api/credential-capabilities")
    def credential_capabilities(user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role not in {"ADMIN", "OWNER"}:
            raise HTTPException(status_code=403, detail="Credential capability metadata requires owner access")
        query = select(CredentialCapability).order_by(CredentialCapability.checked_at.desc())
        if user.role != "ADMIN":
            query = query.where(CredentialCapability.workspace_id == user.workspace_id)
        return [{
            "id": item.id,
            "agent_id": item.agent_id,
            "profile_id": item.profile_id,
            "probe_version": item.probe_version,
            "browser_reachable": item.browser_reachable,
            "cookie_read_supported": item.cookie_read_supported,
            "cookie_write_supported": item.cookie_write_supported,
            "credential_snapshot_allowed": item.credential_snapshot_allowed,
            "evidence": item.evidence,
            "checked_at": _dt(item.checked_at),
        } for item in db.scalars(query)]

    @app.get("/api/commands/{command_id}")
    def command_detail(command_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        return _command_dict(visible_command(db, command_id, user))

    @app.post("/api/commands/{command_id}/cancel")
    def cancel_command(command_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role not in {"ADMIN", "OWNER"}:
            raise HTTPException(status_code=403, detail="Only workspace owners can cancel commands")
        item = visible_command(db, command_id, user)
        if item.status not in TERMINAL_COMMAND_STATUSES:
            item.status = "CANCELLED"
            item.completed_at = now()
            item.error = "Cancelled by user"
            db.commit()
            audit(db, request, action="COMMAND_CANCELLED", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, agent_id=item.agent_id, profile_id=item.profile_id, task_id=item.task_id, resource_type="command", resource_id=item.id)
        return {"ok": True, "idempotent": item.status == "CANCELLED", "command": _command_dict(item)}

    @app.post("/api/agent/commands/pull")
    def pull_commands(body: CommandPull, request: Request, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
        if body.agent_id != agent.id:
            raise HTTPException(status_code=403, detail="Agent mismatch")
        lease_expired = now() - timedelta(seconds=COMMAND_LEASE_SECONDS)
        query = select(Command).where(
            Command.agent_id == agent.id,
            or_(Command.status == "PENDING", and_(Command.status == "DELIVERED", Command.delivered_at < lease_expired)),
        ).order_by(Command.created_at).limit(body.limit)
        items = list(db.scalars(query))
        delivered = now()
        for item in items:
            item.status = "DELIVERED"
            item.delivered_at = delivered
            item.attempts += 1
        db.commit()
        for item in items:
            audit(db, request, action="COMMAND_DELIVERED", result="SUCCESS", workspace_id=item.workspace_id, agent_id=agent.id, profile_id=item.profile_id, task_id=item.task_id, resource_type="command", resource_id=item.id)
        return {"items": [_command_dict(item) for item in items]}

    @app.post("/api/agent/commands/{command_id}/ack")
    def acknowledge_command(command_id: str, body: CommandAck, request: Request, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
        item = db.get(Command, command_id)
        if not item or item.agent_id != agent.id or body.agent_id != agent.id:
            raise HTTPException(status_code=404, detail="Command not found for agent")
        if item.status in TERMINAL_COMMAND_STATUSES:
            return {"ok": True, "idempotent": True, "command": _command_dict(item)}
        if item.status not in {"DELIVERED", "ACKNOWLEDGED", "RUNNING"}:
            raise HTTPException(status_code=409, detail="Command cannot be acknowledged in its current state")
        item.status = body.status
        item.acknowledged_at = item.acknowledged_at or now()
        if body.status == "RUNNING":
            item.started_at = item.started_at or now()
        db.commit()
        audit(db, request, action="COMMAND_ACKNOWLEDGED", result="SUCCESS", workspace_id=item.workspace_id, agent_id=agent.id, profile_id=item.profile_id, task_id=item.task_id, resource_type="command", resource_id=item.id)
        return {"ok": True, "idempotent": False, "command": _command_dict(item)}

    @app.post("/api/agent/commands/{command_id}/result")
    def command_result(command_id: str, body: CommandResult, request: Request, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
        item = db.get(Command, command_id)
        if not item or item.agent_id != agent.id or body.agent_id != agent.id:
            raise HTTPException(status_code=404, detail="Command not found for agent")
        if item.status in TERMINAL_COMMAND_STATUSES:
            return {"ok": True, "idempotent": True, "command": _command_dict(item)}
        if item.status not in {"DELIVERED", "ACKNOWLEDGED", "RUNNING"}:
            raise HTTPException(status_code=409, detail="Command is not running")
        item.status = body.status
        item.result = store_credential_probe(db, item, body.result) if item.command_type == "PROBE_CREDENTIAL_CAPABILITY" and body.status == "SUCCESS" else redact_payload(body.result)
        item.error = redact(body.error or "")
        item.completed_at = now()
        db.commit()
        audit(db, request, action="COMMAND_COMPLETED", result=body.status, workspace_id=item.workspace_id, agent_id=agent.id, profile_id=item.profile_id, task_id=item.task_id, resource_type="command", resource_id=item.id)
        return {"ok": True, "idempotent": False, "command": _command_dict(item)}
