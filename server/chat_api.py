from __future__ import annotations

import json
import re
import time
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .ai_provider import CredentialCipher, CredentialError
from .ai_service import (
    AIRequestCancelled,
    AIRequestError,
    AIRequestTimeout,
    AIService,
    AIUsageResult,
    ChatRunHandle,
    ChatRunRegistry,
)
from .models import AIProvider, AIUsage, ChatMessage, ChatSession, User, now
from .schemas import ChatMessageCreate, ChatSessionCreate
from .security import audit


MESSAGE_ROLES = frozenset({"system", "user", "assistant"})
MESSAGE_STATUSES = frozenset({"PENDING", "STREAMING", "SUCCESS", "FAILED", "CANCELLED"})
_INLINE_SECRET = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+"
    r"|(sk-[A-Za-z0-9_-]{8,})"
    r"|(lag_[A-Za-z0-9_-]{12,})"
    r"|((?:api[_ -]?key|authorization|jwt|agent[_ -]?token|x[_ -]?token|cookie|password)"
    r"\s*(?:is|[:=])\s*)\S+"
)


def sanitize_chat_content(value: str) -> str:
    text = str(value or "").strip()

    def replace(match: re.Match) -> str:
        prefix = match.group(1) or match.group(4) or ""
        return f"{prefix}[REDACTED]"

    return _INLINE_SECRET.sub(replace, text)


def estimate_context_tokens(content: str) -> int:
    return max(1, (len(content) + 3) // 4) + 4


def trim_context(
    messages: list[dict[str, str]],
    *,
    max_messages: int,
    max_tokens: int,
) -> list[dict[str, str]]:
    valid = [item for item in messages if item.get("role") in MESSAGE_ROLES and item.get("content")]
    systems = [dict(item) for item in valid if item["role"] == "system"][: max(1, max_messages // 4)]
    recent = [dict(item) for item in valid if item["role"] != "system"]
    system_budget = max(1, max_tokens // 4)
    kept_systems: list[dict[str, str]] = []
    used_system = 0
    for item in systems:
        remaining = system_budget - used_system
        if remaining <= 4:
            break
        content = item["content"]
        if estimate_context_tokens(content) > remaining:
            content = content[: max(1, (remaining - 4) * 4)]
        kept_systems.append({"role": "system", "content": content})
        used_system += estimate_context_tokens(content)

    remaining_slots = max(1, max_messages - len(kept_systems))
    remaining_tokens = max(8, max_tokens - used_system)
    kept_recent: list[dict[str, str]] = []
    used_recent = 0
    for item in reversed(recent):
        if len(kept_recent) >= remaining_slots:
            break
        cost = estimate_context_tokens(item["content"])
        if used_recent + cost <= remaining_tokens:
            kept_recent.append(item)
            used_recent += cost
            continue
        if not kept_recent:
            allowed_chars = max(1, (remaining_tokens - 4) * 4)
            kept_recent.append({"role": item["role"], "content": item["content"][-allowed_chars:]})
        break
    return kept_systems + list(reversed(kept_recent))


def _dt(value):
    return value.isoformat() if value else None


def _usage_dict(item: AIUsage | None) -> dict:
    return {
        "prompt_tokens": item.prompt_tokens if item else 0,
        "completion_tokens": item.completion_tokens if item else 0,
        "total_tokens": item.total_tokens if item else 0,
        "latency_ms": item.latency_ms if item else 0,
    }


def _message_dict(item: ChatMessage, usage: AIUsage | None = None) -> dict:
    return {
        "message_id": item.id,
        "session_id": item.session_id,
        "role": item.role,
        "content": item.content,
        "status": item.status,
        "error": item.error,
        "created_at": _dt(item.created_at),
        "usage": _usage_dict(usage),
    }


def _session_dict(item: ChatSession, provider_name: str = "", *, running: bool = False) -> dict:
    return {
        "session_id": item.id,
        "workspace_id": item.workspace_id,
        "user_id": item.user_id,
        "title": item.title,
        "provider_id": item.provider_id,
        "provider_name": provider_name,
        "model": item.model,
        "is_running": running,
        "created_at": _dt(item.created_at),
        "updated_at": _dt(item.updated_at),
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def register_chat_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
    paged: Callable,
    cipher: CredentialCipher,
    ai_service: AIService,
    registry: ChatRunRegistry,
) -> None:
    app.state.ai_service = ai_service
    app.state.chat_runs = registry

    def visible_session(session_id: str, user: User, db: Session) -> ChatSession:
        item = db.get(ChatSession, session_id)
        if not item or item.workspace_id != user.workspace_id or item.user_id != user.id:
            raise HTTPException(status_code=404, detail="Chat session not found")
        return item

    def checked_provider(
        db: Session,
        user: User,
        provider_id: str | None,
        model: str | None,
    ) -> tuple[AIProvider, str]:
        query = select(AIProvider).where(
            AIProvider.workspace_id == user.workspace_id,
            AIProvider.status == "ENABLED",
        )
        if provider_id:
            query = query.where(AIProvider.id == provider_id)
        else:
            query = query.where(AIProvider.is_default.is_(True))
        provider = db.scalar(query)
        if not provider:
            raise HTTPException(status_code=422, detail="Enabled AI provider not found for this workspace")
        selected_model = str(model or provider.default_model or "").strip()
        if not selected_model:
            raise HTTPException(status_code=422, detail="AI model is required")
        allowed_models = set(provider.available_models or [])
        if provider.default_model:
            allowed_models.add(provider.default_model)
        if selected_model not in allowed_models:
            raise HTTPException(status_code=422, detail="AI model is not allowed for this provider")
        return provider, selected_model

    def detail_payload(item: ChatSession, db: Session) -> dict:
        provider = db.get(AIProvider, item.provider_id)
        messages = list(db.scalars(
            select(ChatMessage).where(ChatMessage.session_id == item.id).order_by(ChatMessage.created_at, ChatMessage.id)
        ))
        usage_items = list(db.scalars(select(AIUsage).where(AIUsage.session_id == item.id)))
        usage_by_message = {usage.message_id: usage for usage in usage_items}
        totals = {
            "prompt_tokens": sum(usage.prompt_tokens for usage in usage_items),
            "completion_tokens": sum(usage.completion_tokens for usage in usage_items),
            "total_tokens": sum(usage.total_tokens for usage in usage_items),
            "latency_ms": sum(usage.latency_ms for usage in usage_items),
        }
        return _session_dict(
            item,
            provider.name if provider else "",
            running=registry.is_running(item.id),
        ) | {
            "messages": [_message_dict(message, usage_by_message.get(message.id)) for message in messages],
            "usage": totals,
        }

    @app.get("/api/ai/chat/sessions")
    def list_sessions(
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        query = select(ChatSession).where(
            ChatSession.workspace_id == user.workspace_id,
            ChatSession.user_id == user.id,
        ).order_by(ChatSession.updated_at.desc())

        def serialize(item: ChatSession) -> dict:
            provider = db.get(AIProvider, item.provider_id)
            return _session_dict(
                item,
                provider.name if provider else "",
                running=registry.is_running(item.id),
            )

        return paged(db, query, serialize, page=page, page_size=page_size)

    @app.post("/api/ai/chat/sessions")
    def create_session(
        body: ChatSessionCreate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        if not user.workspace_id:
            raise HTTPException(status_code=422, detail="User workspace is required")
        provider, model = checked_provider(db, user, body.provider_id, body.model)
        item = ChatSession(
            workspace_id=user.workspace_id,
            user_id=user.id,
            title=body.title.strip(),
            provider_id=provider.id,
            model=model,
            created_at=now(),
            updated_at=now(),
        )
        db.add(item)
        db.flush()
        if body.system_prompt and body.system_prompt.strip():
            db.add(ChatMessage(
                session_id=item.id,
                role="system",
                content=sanitize_chat_content(body.system_prompt),
                status="SUCCESS",
                created_at=now(),
            ))
        db.commit()
        audit(db, request, action="AI_CHAT_SESSION_CREATED", result="SUCCESS", user_id=user.id, workspace_id=user.workspace_id, resource_type="chat_session", resource_id=item.id)
        return detail_payload(item, db)

    @app.get("/api/ai/chat/sessions/{session_id}")
    def get_session(
        session_id: str,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        return detail_payload(visible_session(session_id, user, db), db)

    @app.delete("/api/ai/chat/sessions/{session_id}")
    def delete_session(
        session_id: str,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        item = visible_session(session_id, user, db)
        if registry.is_running(item.id):
            raise HTTPException(status_code=409, detail="Stop generation before deleting the session")
        db.execute(delete(AIUsage).where(AIUsage.session_id == item.id))
        db.execute(delete(ChatMessage).where(ChatMessage.session_id == item.id))
        db.delete(item)
        db.commit()
        audit(db, request, action="AI_CHAT_SESSION_DELETED", result="SUCCESS", user_id=user.id, workspace_id=user.workspace_id, resource_type="chat_session", resource_id=session_id)
        return {"deleted": True, "session_id": session_id}

    @app.post("/api/ai/chat/sessions/{session_id}/messages")
    def send_message(
        session_id: str,
        body: ChatMessageCreate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        session = visible_session(session_id, user, db)
        provider, model = checked_provider(db, user, session.provider_id, session.model)
        handle = registry.begin(session.id)
        if not handle:
            raise HTTPException(status_code=409, detail="This chat session is already generating")
        try:
            api_key = cipher.decrypt(provider.api_key_encrypted)
        except CredentialError as exc:
            registry.finish(session.id, handle)
            raise HTTPException(status_code=503, detail="AI credential service unavailable") from exc

        safe_content = sanitize_chat_content(body.content)
        previous_user_count = int(db.scalar(select(func.count(ChatMessage.id)).where(
            ChatMessage.session_id == session.id,
            ChatMessage.role == "user",
        )) or 0)
        user_message = ChatMessage(
            session_id=session.id,
            role="user",
            content=safe_content,
            status="SUCCESS",
            created_at=now(),
        )
        assistant_message = ChatMessage(
            session_id=session.id,
            role="assistant",
            content="",
            status="PENDING",
            created_at=now(),
        )
        db.add_all([user_message, assistant_message])
        session.updated_at = now()
        if previous_user_count == 0 and session.title == "新聊天":
            title = re.sub(r"\s+", " ", safe_content).strip()
            session.title = (title[:50] or "新聊天")
        db.commit()

        context_rows = list(db.scalars(
            select(ChatMessage).where(
                ChatMessage.session_id == session.id,
                ((ChatMessage.role.in_(["system", "user"])) & (ChatMessage.status == "SUCCESS"))
                | ((ChatMessage.role == "assistant") & (ChatMessage.status == "SUCCESS")),
            ).order_by(ChatMessage.created_at, ChatMessage.id)
        ))
        context = trim_context(
            [{"role": item.role, "content": item.content} for item in context_rows],
            max_messages=app.state.settings.ai_chat_max_context_messages,
            max_tokens=app.state.settings.ai_chat_max_context_tokens,
        )
        session_snapshot = {
            "session_id": session.id,
            "workspace_id": session.workspace_id,
            "user_id": session.user_id,
            "provider_id": provider.id,
            "model": model,
            "user_message_id": user_message.id,
            "assistant_message_id": assistant_message.id,
        }

        def stream_response():
            started = time.monotonic()
            content_parts: list[str] = []
            usage = AIUsageResult()
            final_status = "FAILED"
            error_code = "AI_ERROR"
            safe_error = "AI request failed"
            client_disconnected = False
            stream_db = app.state.SessionLocal()
            try:
                current_assistant = stream_db.get(ChatMessage, assistant_message.id)
                if current_assistant:
                    current_assistant.status = "STREAMING"
                    stream_db.commit()
                yield _sse("message.started", {
                    "session_id": session.id,
                    "user_message": _message_dict(user_message),
                    "assistant_message": _message_dict(assistant_message) | {"status": "STREAMING"},
                })
                for event in app.state.ai_service.stream(
                    base_url=provider.base_url,
                    api_key=api_key,
                    model=model,
                    messages=context,
                    handle=handle,
                ):
                    if handle.cancelled:
                        raise AIRequestCancelled("AI request cancelled")
                    if event.get("type") == "delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            content_parts.append(delta)
                            yield _sse("message.delta", {
                                "message_id": assistant_message.id,
                                "content": sanitize_chat_content("".join(content_parts)),
                            })
                    elif event.get("type") == "completed":
                        usage = event.get("usage") if isinstance(event.get("usage"), AIUsageResult) else AIUsageResult()
                final_status = "SUCCESS"
                error_code = ""
                safe_error = ""
            except GeneratorExit:
                handle.cancel()
                final_status = "CANCELLED"
                error_code = "CANCELLED"
                safe_error = "生成已停止"
                client_disconnected = True
            except AIRequestCancelled:
                handle.cancel()
                final_status = "CANCELLED"
                error_code = "CANCELLED"
                safe_error = "生成已停止"
            except AIRequestTimeout:
                final_status = "FAILED"
                error_code = "TIMEOUT"
                safe_error = "AI请求超时"
            except AIRequestError:
                final_status = "FAILED"
                error_code = "PROVIDER_ERROR"
                safe_error = "AI服务请求失败"
            except Exception:
                final_status = "FAILED"
                error_code = "INTERNAL_ERROR"
                safe_error = "AI服务暂时不可用"
            finally:
                latency_ms = max(0, round((time.monotonic() - started) * 1000))
                stored_content = sanitize_chat_content("".join(content_parts))
                try:
                    current_assistant = stream_db.get(ChatMessage, assistant_message.id)
                    if current_assistant:
                        current_assistant.content = stored_content
                        current_assistant.status = final_status
                        current_assistant.error = safe_error
                    current_session = stream_db.get(ChatSession, session.id)
                    if current_session:
                        current_session.updated_at = now()
                    stream_db.add(AIUsage(
                        workspace_id=session_snapshot["workspace_id"],
                        user_id=session_snapshot["user_id"],
                        provider_id=session_snapshot["provider_id"],
                        session_id=session_snapshot["session_id"],
                        message_id=session_snapshot["assistant_message_id"],
                        model=session_snapshot["model"],
                        status=final_status,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        total_tokens=usage.total_tokens,
                        latency_ms=latency_ms,
                        error_code=error_code,
                        created_at=now(),
                    ))
                    stream_db.commit()
                    action = {
                        "SUCCESS": "AI_CHAT_COMPLETED",
                        "CANCELLED": "AI_CHAT_CANCELLED",
                    }.get(final_status, "AI_CHAT_FAILED")
                    audit(stream_db, request, action=action, result=final_status, user_id=user.id, workspace_id=user.workspace_id, resource_type="chat_message", resource_id=assistant_message.id, message=safe_error)
                finally:
                    registry.finish(session.id, handle)
                    stream_db.close()
                if client_disconnected:
                    return
                if final_status == "SUCCESS":
                    yield _sse("message.completed", {
                        "message_id": assistant_message.id,
                        "status": final_status,
                        "content": stored_content,
                        "usage": {
                            "prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                            "total_tokens": usage.total_tokens,
                            "latency_ms": latency_ms,
                        },
                    })
                else:
                    yield _sse("message.cancelled" if final_status == "CANCELLED" else "message.error", {
                        "message_id": assistant_message.id,
                        "status": final_status,
                        "content": stored_content,
                        "error": safe_error,
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "latency_ms": latency_ms},
                    })

        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/ai/chat/sessions/{session_id}/stop")
    def stop_generation(
        session_id: str,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        item = visible_session(session_id, user, db)
        stopped = registry.stop(item.id)
        if stopped:
            audit(db, request, action="AI_CHAT_STOP_REQUESTED", result="SUCCESS", user_id=user.id, workspace_id=user.workspace_id, resource_type="chat_session", resource_id=item.id)
        return {"session_id": item.id, "stopped": stopped}
