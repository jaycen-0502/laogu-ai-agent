from __future__ import annotations

from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .ai_provider import (
    AIProviderTester,
    CredentialCipher,
    CredentialError,
    ProviderConnectionError,
    normalize_base_url,
    normalize_provider_type,
)
from .models import AIAnalysis, AIImage, AIProvider, AITaskProposal, AIWritingRecord, ChatSession, User, Workspace, now
from .schemas import AIProviderCreate, AIProviderUpdate
from .security import audit


PROVIDER_STATUSES = frozenset({"ENABLED", "DISABLED"})


def _dt(value):
    return value.isoformat() if value else None


def _provider_dict(item: AIProvider) -> dict:
    return {
        "provider_id": item.id,
        "workspace_id": item.workspace_id,
        "name": item.name,
        "provider_type": item.provider_type,
        "base_url": item.base_url,
        "api_key_masked": f"****{item.api_key_last4}" if item.api_key_last4 else "",
        "has_api_key": bool(item.api_key_encrypted),
        "default_model": item.default_model,
        "models": item.available_models or [],
        "status": item.status,
        "is_default": item.is_default,
        "last_test_status": item.last_test_status,
        "last_tested_at": _dt(item.last_tested_at),
        "last_error": item.last_error,
        "created_by": item.created_by,
        "created_at": _dt(item.created_at),
        "updated_at": _dt(item.updated_at),
    }


def register_ai_provider_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
    paged: Callable,
    deny: Callable,
    cipher: CredentialCipher,
    tester: AIProviderTester,
) -> None:
    app.state.ai_provider_tester = tester

    def visible_provider(provider_id: str, user: User, db: Session) -> AIProvider:
        item = db.get(AIProvider, provider_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id):
            raise HTTPException(status_code=404, detail="AI provider not found")
        return item

    def require_manager(request: Request, user: User, db: Session, workspace_id: str | None = None) -> None:
        target_workspace = workspace_id or user.workspace_id
        if user.role == "ADMIN" or (user.role == "OWNER" and user.workspace_id == target_workspace):
            return
        deny(request, db, action="AI_PROVIDER_MANAGE", user=user, message="AI provider modification is forbidden")

    def checked_status(value: str) -> str:
        normalized = str(value or "").strip().upper()
        if normalized not in PROVIDER_STATUSES:
            raise HTTPException(status_code=422, detail="Invalid AI provider status")
        return normalized

    def checked_type(value: str) -> str:
        try:
            return normalize_provider_type(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def checked_url(provider_type: str, value: str) -> str:
        try:
            normalized = normalize_base_url(provider_type, value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if app.state.settings.environment == "production" and not normalized.startswith("https://"):
            raise HTTPException(status_code=422, detail="HTTPS is required for AI providers in production")
        return normalized

    def ensure_unique_name(db: Session, workspace_id: str, name: str, *, exclude_id: str = "") -> None:
        query = select(AIProvider).where(
            AIProvider.workspace_id == workspace_id,
            func.lower(AIProvider.name) == name.lower(),
        )
        existing = db.scalar(query)
        if existing and existing.id != exclude_id:
            raise HTTPException(status_code=409, detail="AI provider name already exists")

    def set_default(db: Session, item: AIProvider) -> None:
        for current in db.scalars(
            select(AIProvider).where(
                AIProvider.workspace_id == item.workspace_id,
                AIProvider.id != item.id,
                AIProvider.is_default.is_(True),
            )
        ):
            current.is_default = False
            current.updated_at = now()
        db.flush()
        item.is_default = True

    @app.get("/api/ai/providers")
    def list_providers(
        q: str = "",
        status: str = "",
        workspace_id: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        paged_response: bool = Query(False, alias="paged"),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        query = select(AIProvider)
        if user.role != "ADMIN":
            query = query.where(AIProvider.workspace_id == user.workspace_id)
        elif workspace_id.strip():
            query = query.where(AIProvider.workspace_id == workspace_id.strip())
        if q.strip():
            value = f"%{q.strip()}%"
            query = query.where(or_(AIProvider.name.ilike(value), AIProvider.default_model.ilike(value)))
        if status.strip():
            query = query.where(AIProvider.status == checked_status(status))
        query = query.order_by(AIProvider.is_default.desc(), AIProvider.updated_at.desc())
        if paged_response:
            return paged(db, query, _provider_dict, page=page, page_size=page_size)
        return [_provider_dict(item) for item in db.scalars(query)]

    @app.get("/api/ai/providers/{provider_id}")
    def get_provider(provider_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        return _provider_dict(visible_provider(provider_id, user, db))

    @app.post("/api/ai/providers")
    def create_provider(
        body: AIProviderCreate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        workspace_id = body.workspace_id if user.role == "ADMIN" and body.workspace_id else user.workspace_id
        require_manager(request, user, db, workspace_id)
        if not workspace_id or not db.get(Workspace, workspace_id):
            raise HTTPException(status_code=404, detail="Workspace not found")
        name = body.name.strip()
        ensure_unique_name(db, workspace_id, name)
        provider_type = checked_type(body.provider_type)
        status = checked_status(body.status)
        if body.is_default and status != "ENABLED":
            raise HTTPException(status_code=422, detail="Default AI provider must be enabled")
        try:
            encrypted = cipher.encrypt(body.api_key)
        except CredentialError as exc:
            raise HTTPException(status_code=503, detail="AI credential service unavailable") from exc
        item = AIProvider(
            workspace_id=workspace_id,
            name=name,
            provider_type=provider_type,
            base_url=checked_url(provider_type, body.base_url),
            api_key_encrypted=encrypted,
            api_key_last4=body.api_key[-4:],
            default_model=body.default_model.strip(),
            available_models=[],
            status=status,
            is_default=False,
            created_by=user.id,
            created_at=now(),
            updated_at=now(),
        )
        db.add(item)
        db.flush()
        if body.is_default:
            set_default(db, item)
        db.commit()
        audit(db, request, action="AI_PROVIDER_CREATED", result="SUCCESS", user_id=user.id, workspace_id=workspace_id, resource_type="ai_provider", resource_id=item.id)
        return _provider_dict(item)

    @app.patch("/api/ai/providers/{provider_id}")
    def update_provider(
        provider_id: str,
        body: AIProviderUpdate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        item = visible_provider(provider_id, user, db)
        require_manager(request, user, db, item.workspace_id)
        updates = body.model_dump(exclude_unset=True)
        for key in ("name", "provider_type", "base_url", "api_key", "status", "is_default"):
            if key in updates and updates[key] is None:
                raise HTTPException(status_code=422, detail=f"{key} cannot be null")
        next_type = checked_type(str(updates.get("provider_type", item.provider_type)))
        next_status = checked_status(str(updates.get("status", item.status)))
        next_default = bool(updates.get("is_default", item.is_default))
        if next_status == "DISABLED":
            next_default = False
        if "name" in updates:
            name = str(updates["name"]).strip()
            ensure_unique_name(db, item.workspace_id, name, exclude_id=item.id)
            item.name = name
        item.provider_type = next_type
        if "base_url" in updates or "provider_type" in updates:
            item.base_url = checked_url(next_type, str(updates.get("base_url", item.base_url)))
        if "api_key" in updates:
            raw_key = str(updates["api_key"])
            try:
                item.api_key_encrypted = cipher.encrypt(raw_key)
            except CredentialError as exc:
                raise HTTPException(status_code=503, detail="AI credential service unavailable") from exc
            item.api_key_last4 = raw_key[-4:]
            item.last_test_status = "UNKNOWN"
            item.last_tested_at = None
            item.last_error = ""
            item.available_models = []
        if "default_model" in updates:
            item.default_model = str(updates["default_model"] or "").strip()
        item.status = next_status
        if next_status == "DISABLED":
            item.is_default = False
        elif next_default:
            set_default(db, item)
        elif "is_default" in updates:
            item.is_default = False
        item.updated_at = now()
        db.commit()
        audit(db, request, action="AI_PROVIDER_UPDATED", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, resource_type="ai_provider", resource_id=item.id)
        return _provider_dict(item)

    @app.post("/api/ai/providers/{provider_id}/test")
    def test_provider(
        provider_id: str,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        item = visible_provider(provider_id, user, db)
        require_manager(request, user, db, item.workspace_id)
        try:
            api_key = cipher.decrypt(item.api_key_encrypted)
            result = app.state.ai_provider_tester.test(
                item.base_url,
                api_key,
                default_model=item.default_model,
            )
        except CredentialError:
            result = {"status": "FAILED", "models": [], "error": "AI credential cannot be decrypted"}
        except ProviderConnectionError as exc:
            safe_error = str(exc).replace(api_key, "[REDACTED]")[:200]
            result = {"status": "FAILED", "models": [], "error": safe_error}
        item.last_test_status = result["status"]
        item.last_tested_at = now()
        item.last_error = str(result.get("error") or "")[:200]
        if result["status"] == "SUCCESS":
            item.available_models = list(result.get("models") or [])[:500]
        item.updated_at = now()
        db.commit()
        audit(db, request, action="AI_PROVIDER_TESTED", result=result["status"], user_id=user.id, workspace_id=item.workspace_id, resource_type="ai_provider", resource_id=item.id, message=item.last_error)
        return result | {"provider_id": item.id, "tested_at": _dt(item.last_tested_at)}

    @app.delete("/api/ai/providers/{provider_id}")
    def delete_provider(
        provider_id: str,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        item = visible_provider(provider_id, user, db)
        require_manager(request, user, db, item.workspace_id)
        if item.status == "ENABLED" or item.is_default:
            raise HTTPException(status_code=409, detail="Disable the AI provider before deletion")
        if db.scalar(select(ChatSession.id).where(ChatSession.provider_id == item.id).limit(1)):
            raise HTTPException(status_code=409, detail="AI provider is used by chat sessions")
        if db.scalar(select(AIImage.id).where(AIImage.provider_id == item.id).limit(1)):
            raise HTTPException(status_code=409, detail="AI provider is used by generated images")
        if db.scalar(select(AIAnalysis.id).where(AIAnalysis.provider_id == item.id).limit(1)):
            raise HTTPException(status_code=409, detail="AI provider is used by AI analyses")
        if db.scalar(select(AIWritingRecord.id).where(AIWritingRecord.provider_id == item.id).limit(1)):
            raise HTTPException(status_code=409, detail="AI provider is used by AI writing records")
        if db.scalar(select(AITaskProposal.id).where(AITaskProposal.provider_id == item.id).limit(1)):
            raise HTTPException(status_code=409, detail="AI provider is used by AI task proposals")
        workspace_id = item.workspace_id
        db.delete(item)
        db.commit()
        audit(db, request, action="AI_PROVIDER_DELETED", result="SUCCESS", user_id=user.id, workspace_id=workspace_id, resource_type="ai_provider", resource_id=provider_id)
        return {"deleted": True, "provider_id": provider_id}
