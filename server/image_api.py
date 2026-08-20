from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .ai_provider import CredentialCipher, CredentialError
from .chat_api import sanitize_chat_content
from .image_service import AIImageRequestError, AIImageRequestTimeout, AIImageResponseTooLarge, AIImageService
from .models import AIImage, AIProvider, User, now
from .schemas import AIImageGenerate
from .security import audit


IMAGE_MODEL = "gpt-image-2"
IMAGE_SIZES = {"1K": "1024x1024", "2K": "2048x2048"}
IMAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _dt(value):
    return value.isoformat() if value else None


def _image_dict(item: AIImage, provider_name: str = "") -> dict:
    return {
        "image_id": item.id,
        "workspace_id": item.workspace_id,
        "user_id": item.user_id,
        "provider_id": item.provider_id,
        "provider_name": provider_name,
        "model": item.model,
        "prompt": item.prompt,
        "resolution": item.resolution,
        "size": item.size,
        "quality": item.quality,
        "status": item.status,
        "mime_type": item.mime_type,
        "byte_size": item.byte_size,
        "prompt_tokens": item.prompt_tokens,
        "image_tokens": item.image_tokens,
        "total_tokens": item.total_tokens,
        "latency_ms": item.latency_ms,
        "error_code": item.error_code,
        "error": item.error,
        "content_url": f"/api/ai/images/{item.id}/content" if item.status == "SUCCESS" else "",
        "created_at": _dt(item.created_at),
        "completed_at": _dt(item.completed_at),
    }


def register_image_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
    paged: Callable,
    cipher: CredentialCipher,
    image_service: AIImageService,
    storage_root: Path,
) -> None:
    app.state.ai_image_service = image_service
    app.state.ai_image_storage_root = storage_root.resolve()

    def visible_image(image_id: str, user: User, db: Session) -> AIImage:
        item = db.get(AIImage, image_id)
        if not item or item.workspace_id != user.workspace_id or item.user_id != user.id:
            raise HTTPException(status_code=404, detail="AI image not found")
        return item

    def image_path(item: AIImage) -> Path:
        root = app.state.ai_image_storage_root
        candidate = (root / item.workspace_id / item.user_id / item.file_name).resolve()
        if root not in candidate.parents:
            raise HTTPException(status_code=500, detail="Internal server error")
        return candidate

    def checked_provider(db: Session, user: User, provider_id: str | None) -> AIProvider:
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
        return provider

    @app.get("/api/ai/images")
    def list_images(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=50),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        query = select(AIImage).where(
            AIImage.workspace_id == user.workspace_id,
            AIImage.user_id == user.id,
        ).order_by(AIImage.created_at.desc(), AIImage.id.desc())

        def serialize(item: AIImage) -> dict:
            provider = db.get(AIProvider, item.provider_id)
            return _image_dict(item, provider.name if provider else "")

        return paged(db, query, serialize, page=page, page_size=page_size)

    @app.post("/api/ai/images/generate")
    def generate_image(
        body: AIImageGenerate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        if not user.workspace_id:
            raise HTTPException(status_code=422, detail="User workspace is required")
        provider = checked_provider(db, user, body.provider_id)
        prompt = sanitize_chat_content(body.prompt)
        if not prompt:
            raise HTTPException(status_code=422, detail="Image prompt is required")
        try:
            api_key = cipher.decrypt(provider.api_key_encrypted)
        except CredentialError as exc:
            raise HTTPException(status_code=503, detail="AI credential service unavailable") from exc

        item = AIImage(
            workspace_id=user.workspace_id,
            user_id=user.id,
            provider_id=provider.id,
            model=IMAGE_MODEL,
            prompt=prompt,
            resolution=body.resolution,
            size=IMAGE_SIZES[body.resolution],
            quality=body.quality,
            status="PENDING",
            created_at=now(),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        started = time.monotonic()
        error_code = ""
        safe_error = ""
        try:
            result = app.state.ai_image_service.generate(
                base_url=provider.base_url,
                api_key=api_key,
                prompt=prompt,
                size=item.size,
                quality=item.quality,
                model=IMAGE_MODEL,
            )
            extension = IMAGE_EXTENSIONS[result.mime_type]
            item.file_name = f"{item.id}.{extension}"
            target = image_path(item)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_name = ""
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{item.id}-", delete=False) as handle:
                    temporary_name = handle.name
                    handle.write(result.content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary_name, 0o600)
                os.replace(temporary_name, target)
            finally:
                if temporary_name and os.path.exists(temporary_name):
                    os.unlink(temporary_name)
            item.status = "SUCCESS"
            item.mime_type = result.mime_type
            item.byte_size = len(result.content)
            item.prompt_tokens = result.prompt_tokens
            item.image_tokens = result.image_tokens
            item.total_tokens = result.total_tokens
        except AIImageRequestTimeout:
            error_code, safe_error = "TIMEOUT", "AI生图请求超时"
        except AIImageResponseTooLarge:
            error_code, safe_error = "IMAGE_TOO_LARGE", "生成图片超过服务器大小限制"
        except AIImageRequestError:
            error_code, safe_error = "PROVIDER_ERROR", "AI生图服务请求失败"
        except Exception:
            error_code, safe_error = "INTERNAL_ERROR", "AI生图服务暂时不可用"

        item.latency_ms = max(0, round((time.monotonic() - started) * 1000))
        item.completed_at = now()
        if error_code:
            item.status = "FAILED"
            item.error_code = error_code
            item.error = safe_error
        db.commit()
        action = "AI_IMAGE_GENERATED" if item.status == "SUCCESS" else "AI_IMAGE_FAILED"
        audit(
            db,
            request,
            action=action,
            result=item.status,
            user_id=user.id,
            workspace_id=user.workspace_id,
            resource_type="ai_image",
            resource_id=item.id,
            message=safe_error,
        )
        if item.status != "SUCCESS":
            raise HTTPException(status_code=504 if error_code == "TIMEOUT" else 502, detail=safe_error)
        return _image_dict(item, provider.name)

    @app.get("/api/ai/images/{image_id}/content")
    def get_image_content(
        image_id: str,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        item = visible_image(image_id, user, db)
        if item.status != "SUCCESS" or not item.file_name:
            raise HTTPException(status_code=404, detail="AI image content not found")
        path = image_path(item)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="AI image content not found")
        return FileResponse(
            path,
            media_type=item.mime_type,
            filename=item.file_name,
            headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
            content_disposition_type="inline",
        )

    @app.delete("/api/ai/images/{image_id}")
    def delete_image(
        image_id: str,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        item = visible_image(image_id, user, db)
        path = image_path(item) if item.file_name else None
        db.execute(delete(AIImage).where(AIImage.id == item.id))
        db.commit()
        if path and path.is_file():
            path.unlink()
        audit(db, request, action="AI_IMAGE_DELETED", result="SUCCESS", user_id=user.id, workspace_id=user.workspace_id, resource_type="ai_image", resource_id=image_id)
        return {"deleted": True, "image_id": image_id}
