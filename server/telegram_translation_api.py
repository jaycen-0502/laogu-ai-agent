from __future__ import annotations

from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_provider import CredentialCipher
from .models import TelegramAllowedUser, TelegramBotBinding, User, Workspace, now
from .schemas import TelegramAllowedUserCreate, TelegramBindingTest, TelegramBindingUpdate
from .security import audit


def _require_admin(request: Request, db: Session, user: User) -> None:
    if user.role != "ADMIN":
        audit(db, request, action="TELEGRAM_ADMIN", result="DENIED", user_id=user.id, workspace_id=user.workspace_id, message="Administrator role required")
        raise HTTPException(status_code=403, detail="仅系统管理员可以配置 Telegram 机器人")


def _binding_dict(item: TelegramBotBinding | None) -> dict:
    if not item:
        return {"configured": False, "enabled": False, "workspace_id": "", "bot_token_last4": "", "admin_telegram_user_id": "", "default_target_language": "zh-CN", "last_error": "", "last_poll_at": None}
    return {"configured": True, "binding_id": item.id, "workspace_id": item.workspace_id, "enabled": bool(item.enabled), "bot_token_last4": item.bot_token_last4, "admin_telegram_user_id": item.admin_telegram_user_id, "default_target_language": item.default_target_language, "last_error": item.last_error, "last_poll_at": item.last_poll_at.isoformat() if item.last_poll_at else None}


def _user_dict(item: TelegramAllowedUser) -> dict:
    return {"id": item.id, "telegram_user_id": item.telegram_user_id, "username": item.username, "display_name": item.display_name, "enabled": bool(item.enabled), "created_at": item.created_at.isoformat()}


def register_telegram_translation_routes(app: FastAPI, *, get_db: Callable, current_user: Callable, cipher: CredentialCipher) -> None:
    @app.get("/api/admin/telegram/translation")
    def get_binding(request: Request, workspace_id: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
        _require_admin(request, db, user)
        query = select(TelegramBotBinding).order_by(TelegramBotBinding.updated_at.desc())
        if workspace_id:
            query = query.where(TelegramBotBinding.workspace_id == workspace_id)
        item = db.scalar(query)
        return _binding_dict(item)

    @app.put("/api/admin/telegram/translation")
    def update_binding(body: TelegramBindingUpdate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        _require_admin(request, db, user)
        if not body.admin_telegram_user_id.isdigit() or int(body.admin_telegram_user_id) <= 0:
            raise HTTPException(status_code=422, detail="Telegram 管理员用户 ID 必须为正整数")
        if not db.get(Workspace, body.workspace_id):
            raise HTTPException(status_code=404, detail="Workspace not found")
        item = db.scalar(select(TelegramBotBinding).where(TelegramBotBinding.workspace_id == body.workspace_id))
        if item and body.bot_token is None:
            item.admin_telegram_user_id = body.admin_telegram_user_id
            item.default_target_language = body.default_target_language
            item.enabled = body.enabled
        else:
            if not body.bot_token:
                raise HTTPException(status_code=422, detail="首次绑定必须提供 Bot Token")
            token = body.bot_token.strip()
            item = item or TelegramBotBinding(workspace_id=body.workspace_id, created_by=user.id)
            item.bot_token_encrypted = cipher.encrypt(token)
            item.bot_token_last4 = token[-4:]
            item.admin_telegram_user_id = body.admin_telegram_user_id
            item.default_target_language = body.default_target_language
            item.enabled = body.enabled
            item.updated_at = now()
            db.add(item)
        db.commit()
        allowed_admin = db.scalar(
            select(TelegramAllowedUser).where(
                TelegramAllowedUser.binding_id == item.id,
                TelegramAllowedUser.telegram_user_id == body.admin_telegram_user_id,
            )
        )
        if allowed_admin:
            allowed_admin.enabled = True
            allowed_admin.display_name = allowed_admin.display_name or "管理员"
            allowed_admin.updated_at = now()
        else:
            db.add(
                TelegramAllowedUser(
                    binding_id=item.id,
                    telegram_user_id=body.admin_telegram_user_id,
                    display_name="管理员",
                )
            )
        item.updated_at = now()
        db.commit()
        audit(db, request, action="TELEGRAM_BINDING_UPDATE", result="SUCCESS", user_id=user.id, workspace_id=body.workspace_id, resource_type="telegram_binding", resource_id=item.id, message=f"enabled={item.enabled} admin_telegram_user_id={item.admin_telegram_user_id}")
        return _binding_dict(item)

    @app.post("/api/admin/telegram/translation/test")
    def test_binding(body: TelegramBindingTest, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        _require_admin(request, db, user)
        import httpx
        try:
            response = httpx.get(f"https://api.telegram.org/bot{body.bot_token.strip()}/getMe", timeout=10, follow_redirects=False)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Telegram Bot API 连接失败") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise HTTPException(status_code=422, detail="Telegram Bot Token 无效")
        bot = payload.get("result") or {}
        return {"ok": True, "bot": {"id": str(bot.get("id") or ""), "username": bot.get("username") or "", "name": bot.get("first_name") or ""}}

    @app.get("/api/admin/telegram/translation/users")
    def list_allowed_users(request: Request, workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        _require_admin(request, db, user)
        binding = db.scalar(select(TelegramBotBinding).where(TelegramBotBinding.workspace_id == workspace_id))
        return [] if not binding else [_user_dict(item) for item in db.scalars(select(TelegramAllowedUser).where(TelegramAllowedUser.binding_id == binding.id).order_by(TelegramAllowedUser.created_at))]

    @app.post("/api/admin/telegram/translation/users")
    def add_allowed_user(body: TelegramAllowedUserCreate, request: Request, workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        _require_admin(request, db, user)
        if not body.telegram_user_id.isdigit() or int(body.telegram_user_id) <= 0:
            raise HTTPException(status_code=422, detail="Telegram 用户 ID 必须为正整数")
        binding = db.scalar(select(TelegramBotBinding).where(TelegramBotBinding.workspace_id == workspace_id))
        if not binding:
            raise HTTPException(status_code=404, detail="请先绑定 Telegram 机器人")
        item = db.scalar(select(TelegramAllowedUser).where(TelegramAllowedUser.binding_id == binding.id, TelegramAllowedUser.telegram_user_id == body.telegram_user_id))
        if item:
            item.username, item.display_name, item.enabled, item.updated_at = body.username, body.display_name, True, now()
        else:
            item = TelegramAllowedUser(binding_id=binding.id, telegram_user_id=body.telegram_user_id, username=body.username, display_name=body.display_name)
            db.add(item)
        db.commit()
        audit(db, request, action="TELEGRAM_ALLOW_USER", result="SUCCESS", user_id=user.id, workspace_id=workspace_id, resource_type="telegram_allowed_user", resource_id=item.id, message=f"telegram_user_id={body.telegram_user_id}")
        return _user_dict(item)

    @app.delete("/api/admin/telegram/translation/users/{telegram_user_id}")
    def remove_allowed_user(telegram_user_id: str, request: Request, workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        _require_admin(request, db, user)
        binding = db.scalar(select(TelegramBotBinding).where(TelegramBotBinding.workspace_id == workspace_id))
        item = db.scalar(select(TelegramAllowedUser).where(TelegramAllowedUser.binding_id == (binding.id if binding else ""), TelegramAllowedUser.telegram_user_id == telegram_user_id))
        if not item:
            raise HTTPException(status_code=404, detail="Telegram 用户未在白名单中")
        db.delete(item)
        db.commit()
        audit(db, request, action="TELEGRAM_REMOVE_USER", result="SUCCESS", user_id=user.id, workspace_id=workspace_id, resource_type="telegram_allowed_user", resource_id=telegram_user_id, message="allowlist user removed")
        return {"ok": True}
