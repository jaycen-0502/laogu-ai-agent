from __future__ import annotations

import time
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_provider import CredentialCipher, CredentialError
from .ai_policy import resolve_provider
from .ai_service import AIRequestError, AIRequestTimeout
from .analysis_service import AIAnalysisRunResult, sanitize_analysis_text
from .models import Account, AIProvider, AIWritingRecord, User, now
from .schemas import AIReplyGenerateCreate, AIWritingAnalyzeCreate
from .security import audit


RECORD_TYPES = frozenset({"ANALYSIS", "REPLY"})


def _dt(value):
    return value.isoformat() if value else None


def _writing_dict(item: AIWritingRecord, provider_name: str = "", *, detail: bool = False) -> dict:
    payload = {
        "record_id": item.id,
        "workspace_id": item.workspace_id,
        "user_id": item.user_id,
        "provider_id": item.provider_id,
        "provider_name": provider_name,
        "account_id": item.account_id,
        "record_type": item.record_type,
        "title": item.title,
        "model": item.model,
        "status": item.status,
        "parameters": item.parameters or {},
        "summary": item.summary,
        "prompt_tokens": item.prompt_tokens,
        "completion_tokens": item.completion_tokens,
        "total_tokens": item.total_tokens,
        "latency_ms": item.latency_ms,
        "error_code": item.error_code,
        "error": item.error,
        "created_at": _dt(item.created_at),
        "completed_at": _dt(item.completed_at),
    }
    if detail:
        payload.update({
            "source_text": item.source_text,
            "context_text": item.context_text,
            "result": item.result or {},
        })
    return payload


def register_writing_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
    paged: Callable,
    cipher: CredentialCipher,
    writing_service,
) -> None:
    app.state.ai_writing_service = writing_service

    def visible_account(account_id: str, user: User, db: Session) -> Account:
        item = db.get(Account, account_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id):
            raise HTTPException(status_code=404, detail="Account not found")
        return item

    def visible_record(record_id: str, user: User, db: Session) -> AIWritingRecord:
        item = db.get(AIWritingRecord, record_id)
        if not item or item.user_id != user.id:
            raise HTTPException(status_code=404, detail="AI writing record not found")
        return item

    def target_workspace(user: User, db: Session, account: Account | None, provider_id: str | None) -> str:
        if account:
            return account.workspace_id
        if provider_id and user.role == "ADMIN":
            provider = db.get(AIProvider, provider_id)
            if provider:
                return provider.workspace_id
        if not user.workspace_id:
            raise HTTPException(status_code=422, detail="Workspace is required")
        return user.workspace_id

    def checked_provider(
        db: Session,
        user: User,
        workspace_id: str,
        provider_id: str | None,
        model: str | None,
    ) -> tuple[AIProvider, str]:
        return resolve_provider(db, user, "WRITING", provider_id, model, workspace_id=workspace_id)

    def execute(
        item: AIWritingRecord,
        provider: AIProvider,
        operation,
        request: Request,
        user: User,
        db: Session,
        profile_id: str | None,
    ) -> dict:
        started = time.monotonic()
        action = "AI_WRITING_FAILED"
        http_error: HTTPException | None = None
        try:
            api_key = cipher.decrypt(provider.api_key_encrypted)
            output: AIAnalysisRunResult = operation(api_key)
            item.status = "SUCCESS"
            item.result = output.result
            item.summary = output.summary
            item.prompt_tokens = output.usage.prompt_tokens
            item.completion_tokens = output.usage.completion_tokens
            item.total_tokens = output.usage.total_tokens
            action = "AI_WRITING_ANALYZED" if item.record_type == "ANALYSIS" else "AI_REPLIES_GENERATED"
        except CredentialError:
            item.status = "FAILED"
            item.error_code = "CREDENTIAL_ERROR"
            item.error = "AI凭据不可用"
            http_error = HTTPException(status_code=503, detail=item.error)
        except AIRequestTimeout:
            item.status = "FAILED"
            item.error_code = "TIMEOUT"
            item.error = "AI话术请求超时"
            http_error = HTTPException(status_code=504, detail=item.error)
        except AIRequestError:
            item.status = "FAILED"
            item.error_code = "PROVIDER_ERROR"
            item.error = "AI话术服务请求失败"
            http_error = HTTPException(status_code=502, detail=item.error)
        except Exception:
            item.status = "FAILED"
            item.error_code = "INTERNAL_ERROR"
            item.error = "AI话术服务暂时不可用"
            http_error = HTTPException(status_code=502, detail=item.error)
        item.latency_ms = max(0, round((time.monotonic() - started) * 1000))
        item.completed_at = now()
        db.commit()
        audit(
            db,
            request,
            action=action,
            result=item.status,
            user_id=user.id,
            workspace_id=item.workspace_id,
            profile_id=profile_id,
            resource_type="ai_writing",
            resource_id=item.id,
            message=item.error,
        )
        if http_error:
            raise http_error
        return _writing_dict(item, provider.name, detail=True)

    @app.get("/api/ai/writing")
    def list_records(
        record_type: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        query = select(AIWritingRecord).where(AIWritingRecord.user_id == user.id)
        if record_type.strip():
            normalized = record_type.strip().upper()
            if normalized not in RECORD_TYPES:
                raise HTTPException(status_code=422, detail="Invalid writing record type")
            query = query.where(AIWritingRecord.record_type == normalized)
        query = query.order_by(AIWritingRecord.created_at.desc())

        def serialize(item: AIWritingRecord) -> dict:
            provider = db.get(AIProvider, item.provider_id)
            return _writing_dict(item, provider.name if provider else "")

        return paged(db, query, serialize, page=page, page_size=page_size)

    @app.get("/api/ai/writing/{record_id}")
    def get_record(record_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_record(record_id, user, db)
        provider = db.get(AIProvider, item.provider_id)
        return _writing_dict(item, provider.name if provider else "", detail=True)

    @app.post("/api/ai/writing/analyze")
    def analyze_writing(
        body: AIWritingAnalyzeCreate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        account = visible_account(body.account_id, user, db) if body.account_id else None
        workspace_id = target_workspace(user, db, account, body.provider_id)
        provider, model = checked_provider(db, user, workspace_id, body.provider_id, body.model)
        source_text = sanitize_analysis_text(body.source_text)
        context_text = sanitize_analysis_text(body.context_text)
        item = AIWritingRecord(
            workspace_id=workspace_id,
            user_id=user.id,
            provider_id=provider.id,
            account_id=account.id if account else None,
            record_type="ANALYSIS",
            title=(body.title.strip() or f"话术分析：{source_text[:30]}")[:160],
            model=model,
            status="PENDING",
            source_text=source_text,
            context_text=context_text,
            parameters={},
            result={},
            summary="",
            created_at=now(),
        )
        db.add(item)
        db.commit()
        return execute(
            item,
            provider,
            lambda api_key: app.state.ai_writing_service.analyze(
                base_url=provider.base_url,
                api_key=api_key,
                model=model,
                source_text=source_text,
                context_text=context_text,
            ),
            request,
            user,
            db,
            account.profile_id if account else None,
        )

    @app.post("/api/ai/writing/replies")
    def generate_replies(
        body: AIReplyGenerateCreate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        account = visible_account(body.account_id, user, db) if body.account_id else None
        workspace_id = target_workspace(user, db, account, body.provider_id)
        provider, model = checked_provider(db, user, workspace_id, body.provider_id, body.model)
        source_text = sanitize_analysis_text(body.source_text)
        context_text = sanitize_analysis_text(body.context_text)
        parameters = {
            "objective": sanitize_analysis_text(body.objective),
            "brand_voice": sanitize_analysis_text(body.brand_voice),
            "tone": body.tone,
            "language": body.language,
            "variant_count": body.variant_count,
            "max_characters": body.max_characters,
            "requires_human_review": True,
            "auto_publish": False,
        }
        item = AIWritingRecord(
            workspace_id=workspace_id,
            user_id=user.id,
            provider_id=provider.id,
            account_id=account.id if account else None,
            record_type="REPLY",
            title=(body.title.strip() or f"回复草稿：{source_text[:30]}")[:160],
            model=model,
            status="PENDING",
            source_text=source_text,
            context_text=context_text,
            parameters=parameters,
            result={},
            summary="",
            created_at=now(),
        )
        db.add(item)
        db.commit()
        return execute(
            item,
            provider,
            lambda api_key: app.state.ai_writing_service.generate(
                base_url=provider.base_url,
                api_key=api_key,
                model=model,
                source_text=source_text,
                context_text=context_text,
                parameters=parameters,
            ),
            request,
            user,
            db,
            account.profile_id if account else None,
        )

    @app.delete("/api/ai/writing/{record_id}")
    def delete_record(
        record_id: str,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        item = visible_record(record_id, user, db)
        workspace_id = item.workspace_id
        db.delete(item)
        db.commit()
        audit(db, request, action="AI_WRITING_DELETED", result="SUCCESS", user_id=user.id, workspace_id=workspace_id, resource_type="ai_writing", resource_id=record_id)
        return {"deleted": True, "record_id": record_id}
