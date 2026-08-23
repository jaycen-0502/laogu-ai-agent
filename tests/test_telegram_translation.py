from sqlalchemy import select

from server.models import TelegramAllowedUser, TelegramBotBinding
from server.telegram_bot_service import TelegramBotManager
from tests.test_ai_providers import auth, make_env


BOT_TOKEN = "123456789:AAExampleTelegramTokenSecret1234"


def test_admin_binds_encrypted_bot_and_manages_allowlist():
    env = make_env()
    response = env["client"].put(
        "/api/admin/telegram/translation",
        headers=auth(env["admin"]),
        json={"workspace_id": env["workspace_id"], "bot_token": BOT_TOKEN, "admin_telegram_user_id": "10001", "default_target_language": "en", "enabled": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["bot_token_last4"] == BOT_TOKEN[-4:]
    assert BOT_TOKEN not in response.text
    with env["client"].app.state.SessionLocal() as db:
        binding = db.scalar(select(TelegramBotBinding).where(TelegramBotBinding.workspace_id == env["workspace_id"]))
        assert binding and binding.bot_token_encrypted != BOT_TOKEN
        assert env["client"].app.state.telegram_manager.cipher.decrypt(binding.bot_token_encrypted) == BOT_TOKEN
        assert db.scalar(select(TelegramAllowedUser).where(TelegramAllowedUser.binding_id == binding.id, TelegramAllowedUser.telegram_user_id == "10001"))
    added = env["client"].post(
        f"/api/admin/telegram/translation/users?workspace_id={env['workspace_id']}",
        headers=auth(env["admin"]),
        json={"telegram_user_id": "10002", "username": "translator", "display_name": "Translator"},
    )
    assert added.status_code == 200, added.text
    users = env["client"].get(f"/api/admin/telegram/translation/users?workspace_id={env['workspace_id']}", headers=auth(env["admin"]))
    assert {item["telegram_user_id"] for item in users.json()} == {"10001", "10002"}
    assert env["client"].delete(f"/api/admin/telegram/translation/users/10002?workspace_id={env['workspace_id']}", headers=auth(env["admin"])).status_code == 200


def test_non_admin_cannot_manage_telegram_binding():
    env = make_env()
    response = env["client"].put(
        "/api/admin/telegram/translation",
        headers=auth(env["owner"]),
        json={"workspace_id": env["workspace_id"], "bot_token": BOT_TOKEN, "admin_telegram_user_id": "10001", "default_target_language": "zh-CN", "enabled": False},
    )
    assert response.status_code == 403
    assert BOT_TOKEN not in response.text


def test_telegram_command_parser_and_rate_limit():
    manager = TelegramBotManager(None, None, None)
    assert manager._parse("hello", "zh-CN") == ("auto", "zh-CN", "hello")
    assert manager._parse("/to ja hello", "zh-CN") == ("auto", "ja", "hello")
    assert all(manager._allow_request("10001") for _ in range(20))
    assert manager._allow_request("10001") is False
