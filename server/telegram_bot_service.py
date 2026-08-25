from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import httpx
from sqlalchemy import select
from starlette.requests import Request

from .ai_provider import CredentialCipher, CredentialError
from .ai_service import AIRequestError, AIRequestTimeout, AIService
from .models import TelegramAllowedUser, TelegramBotBinding, User, now
from .security import audit
from .translation_api import perform_translation


LOGGER = logging.getLogger("laogu.telegram")
SUPPORTED_TARGETS = {"zh-CN", "zh-TW", "en", "ja", "ko", "fr", "de", "es", "ru", "pt-BR"}
POLL_TIMEOUT_SECONDS = 35
MAX_TEXT_LENGTH = 20_000
USER_RATE_LIMIT = 20


def _audit_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/telegram",
            "headers": [],
            "client": ("telegram", 0),
            "scheme": "https",
        }
    )


class TelegramBotManager:
    """Poll enabled Telegram bots in one daemon thread."""

    def __init__(
        self,
        session_factory,
        cipher: CredentialCipher,
        ai_service: AIService,
    ):
        self.session_factory = session_factory
        self.cipher = cipher
        self.ai_service = ai_service
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._user_events: dict[str, list[float]] = {}
        self._rate_lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="laogu-telegram-poll",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run(self) -> None:
        try:
            asyncio.run(self._poll_forever())
        except Exception:
            LOGGER.exception("Telegram polling worker stopped unexpectedly")

    async def _poll_forever(self) -> None:
        offsets: dict[str, int] = {}
        while not self._stop.is_set():
            db = self.session_factory()
            try:
                bindings = list(
                    db.scalars(
                        select(TelegramBotBinding).where(
                            TelegramBotBinding.enabled.is_(True)
                        )
                    )
                )
            finally:
                db.close()
            if not bindings:
                await asyncio.sleep(5)
                continue
            for binding in bindings:
                if self._stop.is_set():
                    break
                try:
                    token = self.cipher.decrypt(binding.bot_token_encrypted)
                    updates = await self._get_updates(
                        token,
                        offsets.get(binding.id, 0),
                    )
                    for update in updates:
                        offsets[binding.id] = int(update.get("update_id", 0)) + 1
                        await self._handle_update(binding.id, token, update)
                    self._mark_poll(binding.id, "")
                except (CredentialError, httpx.HTTPError, asyncio.TimeoutError) as exc:
                    self._mark_poll(binding.id, "Telegram connection unavailable")
                    LOGGER.warning(
                        "Telegram polling failed binding=%s type=%s",
                        binding.id,
                        type(exc).__name__,
                    )
                except Exception:
                    self._mark_poll(binding.id, "Telegram update processing failed")
                    LOGGER.exception(
                        "Telegram update processing failed binding=%s",
                        binding.id,
                    )
            await asyncio.sleep(0.2)

    async def _get_updates(
        self,
        token: str,
        offset: int,
    ) -> list[dict[str, Any]]:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        timeout = httpx.Timeout(POLL_TIMEOUT_SECONDS + 5, connect=10)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                url,
                params={
                    "timeout": POLL_TIMEOUT_SECONDS,
                    "offset": offset,
                    "allowed_updates": "[\"message\"]",
                },
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError("Telegram rejected getUpdates")
        result = payload.get("result")
        return result if isinstance(result, list) else []

    async def _send(self, token: str, chat_id: str, message: str) -> None:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=False,
        ) as client:
            response = await client.post(
                url,
                json={"chat_id": chat_id, "text": message[:4096]},
            )
            response.raise_for_status()

    async def _handle_update(
        self,
        binding_id: str,
        token: str,
        update: dict[str, Any],
    ) -> None:
        message = update.get("message")
        sender = message.get("from") if isinstance(message, dict) else None
        if not isinstance(message, dict) or not isinstance(sender, dict):
            return
        telegram_id = str(sender.get("id") or "").strip()
        chat = message.get("chat")
        chat_id = str(
            (chat.get("id") if isinstance(chat, dict) else "") or telegram_id
        )
        source = str(message.get("text") or "").strip()
        if not telegram_id or not source:
            return

        db = self.session_factory()
        try:
            binding = db.get(TelegramBotBinding, binding_id)
            allowed = (
                db.scalar(
                    select(TelegramAllowedUser).where(
                        TelegramAllowedUser.binding_id == binding_id,
                        TelegramAllowedUser.telegram_user_id == telegram_id,
                        TelegramAllowedUser.enabled.is_(True),
                    )
                )
                if binding
                else None
            )
            if not binding or not binding.enabled:
                return
            if source.startswith(("/start", "/help")):
                response = (
                    "发送要翻译的文字即可。可用 /to en 文本 指定目标语言。"
                    if allowed
                    else "此机器人尚未授权你的 Telegram 账号。"
                )
                await self._send(token, chat_id, response)
                return
            if not allowed:
                await self._send(
                    token,
                    chat_id,
                    "此机器人尚未授权你的 Telegram 账号。",
                )
                return
            if not self._allow_request(telegram_id):
                await self._send(token, chat_id, "请求过于频繁，请稍后再试。")
                return
            try:
                source_language, target_language, source_text = self._parse(
                    source,
                    binding.default_target_language,
                )
            except AIRequestError:
                await self._send(
                    token,
                    chat_id,
                    "目标语言格式无效，请使用 /to en 文本。",
                )
                return

            owner = db.get(User, binding.created_by)
            if not owner or owner.status != "ACTIVE":
                await self._send(
                    token,
                    chat_id,
                    "翻译服务暂不可用，请联系管理员。",
                )
                return

            started = time.monotonic()
            try:
                translated, _model, usage = perform_translation(
                    db,
                    user=owner,
                    workspace_id=binding.workspace_id,
                    text=source_text,
                    source_language=source_language,
                    target_language=target_language,
                    cipher=self.cipher,
                    ai_service=self.ai_service,
                )
            except (AIRequestError, AIRequestTimeout, CredentialError):
                audit(
                    db,
                    _audit_request(),
                    action="TELEGRAM_TRANSLATE",
                    result="FAILED",
                    user_id=owner.id,
                    workspace_id=binding.workspace_id,
                    resource_type="telegram_translation",
                    resource_id=telegram_id,
                    message=(
                        f"target={target_language} chars={len(source_text)}"
                    ),
                )
                await self._send(
                    token,
                    chat_id,
                    "翻译失败，请稍后重试或联系管理员。",
                )
                return

            elapsed_ms = round((time.monotonic() - started) * 1000)
            audit(
                db,
                _audit_request(),
                action="TELEGRAM_TRANSLATE",
                result="SUCCESS",
                user_id=owner.id,
                workspace_id=binding.workspace_id,
                resource_type="telegram_translation",
                resource_id=telegram_id,
                message=(
                    f"target={target_language} chars={len(source_text)} "
                    f"latency_ms={elapsed_ms} total_tokens={usage.total_tokens}"
                ),
            )
            await self._send(token, chat_id, translated)
        finally:
            db.close()

    @staticmethod
    def _parse(text: str, default_target: str) -> tuple[str, str, str]:
        if text.startswith("/to "):
            parts = text.split(maxsplit=2)
            if (
                len(parts) < 3
                or parts[1] not in SUPPORTED_TARGETS
                or not parts[2].strip()
            ):
                raise AIRequestError("Invalid target language")
            return "auto", parts[1], parts[2][:MAX_TEXT_LENGTH]
        return (
            "auto",
            default_target if default_target in SUPPORTED_TARGETS else "zh-CN",
            text[:MAX_TEXT_LENGTH],
        )

    def _allow_request(self, telegram_user_id: str) -> bool:
        current = time.monotonic()
        with self._rate_lock:
            events = [
                event
                for event in self._user_events.get(telegram_user_id, [])
                if current - event < 60
            ]
            if len(events) >= USER_RATE_LIMIT:
                self._user_events[telegram_user_id] = events
                return False
            events.append(current)
            self._user_events[telegram_user_id] = events
            return True

    def _mark_poll(self, binding_id: str, error: str) -> None:
        db = self.session_factory()
        try:
            binding = db.get(TelegramBotBinding, binding_id)
            if binding:
                binding.last_poll_at = now()
                binding.last_error = error[:200]
                db.commit()
        finally:
            db.close()
