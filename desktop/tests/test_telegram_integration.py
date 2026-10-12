# -*- coding: utf-8 -*-
import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
from desktop.main_window import TelegramConfigDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_telegram_config_dialog_init(qapp):
    mock_controller = MagicMock()
    mock_controller.get_telegram_config.return_value = {
        "enabled": True,
        "use_custom_bot": False,
        "bot_token": "",
        "chat_id": "987654321",
        "proxy_url": "http://127.0.0.1:7890",
        "notify_on_account_finish": True,
    }

    dialog = TelegramConfigDialog(mock_controller)
    assert dialog.enabled_checkbox.isChecked()
    assert dialog.chat_id_input.text() == "987654321"
    assert dialog.proxy_input.text() == "http://127.0.0.1:7890"
    assert dialog.bot_mode_combo.currentData() == "official"
    assert dialog.bot_token_input.isHidden()


def test_telegram_config_dialog_custom_bot(qapp):
    mock_controller = MagicMock()
    mock_controller.get_telegram_config.return_value = {
        "enabled": True,
        "use_custom_bot": True,
        "bot_token": "12345:token",
        "chat_id": "111222",
        "proxy_url": "",
        "notify_on_account_finish": True,
    }

    dialog = TelegramConfigDialog(mock_controller)
    assert dialog.bot_mode_combo.currentData() == "custom"
    assert not dialog.bot_token_input.isHidden()
    assert dialog.bot_token_input.text() == "12345:token"


def test_telegram_config_dialog_save(qapp):
    mock_controller = MagicMock()
    mock_controller.get_telegram_config.return_value = {}

    dialog = TelegramConfigDialog(mock_controller)
    dialog.enabled_checkbox.setChecked(True)
    dialog.chat_id_input.setText("555666")
    dialog.proxy_input.setText("http://127.0.0.1:1080")
    dialog._save()

    mock_controller.save_telegram_config.assert_called_once()
    saved_cfg = mock_controller.save_telegram_config.call_args[0][0]
    assert saved_cfg["enabled"] is True
    assert saved_cfg["chat_id"] == "555666"
    assert saved_cfg["proxy_url"] == "http://127.0.0.1:1080"
    assert saved_cfg["notify_on_risk_alert"] is True


def test_telegram_risk_alert_report_formatting():
    from agent.telegram_notifier import TelegramNotifier, TelegramConfig

    # 1. 自动熔断开启时的告警文案构建
    msg_paused = TelegramNotifier.build_risk_alert_report(
        window_name="窗口-01-美国商业",
        handle="tokyo_tech_ceo",
        error_code=226,
        error_message="This request looks like it might be automated",
        auto_paused=True,
    )
    assert "推特风控拦截紧急告警" in msg_paused
    assert "窗口-01-美国商业" in msg_paused
    assert "@tokyo_tech_ceo" in msg_paused
    assert "Code 226" in msg_paused
    assert "已触发底层自动熔断" in msg_paused

    # 2. 自动熔断关闭时的告警文案构建
    msg_unpaused = TelegramNotifier.build_risk_alert_report(
        window_name="窗口-02",
        handle="",
        error_code=399,
        error_message="Challenge required",
        auto_paused=False,
    )
    assert "窗口-02" in msg_unpaused
    assert "未抓取或未登录" in msg_unpaused
    assert "Code 399" in msg_unpaused
    assert "未暂停（用户已关闭熔断）" in msg_unpaused


def test_telegram_send_risk_alert():
    from agent.telegram_notifier import TelegramNotifier, TelegramConfig

    notifier = TelegramNotifier()
    notifier.config = TelegramConfig(
        enabled=True,
        chat_id="999888",
        notify_on_risk_alert=True,
    )

    with patch.object(notifier, "send_raw_message", return_value=(True, "OK")) as mock_send:
        ok, res = notifier.send_risk_alert(
            window_name="测试窗口A",
            handle="test_user",
            error_code=326,
            error_message="Account locked",
            auto_paused=True,
        )
        assert ok is True
        mock_send.assert_called_once()
        args = mock_send.call_args[1] if mock_send.call_args[1] else {}
        if not args:
            chat_id = mock_send.call_args[0][0]
            text = mock_send.call_args[0][1]
        else:
            chat_id = args.get("chat_id")
            text = args.get("text")
        assert chat_id == "999888"
        assert "测试窗口A" in text
        assert "@test_user" in text
        assert "Code 326" in text


def test_telegram_chat_id_validation():
    from agent.telegram_notifier import is_valid_user_chat_id

    # 占位符必须被拒绝
    assert not is_valid_user_chat_id("112233")
    assert not is_valid_user_chat_id("123456789")
    assert not is_valid_user_chat_id("987654321")
    assert not is_valid_user_chat_id("555666")
    assert not is_valid_user_chat_id("0")
    assert not is_valid_user_chat_id("")
    assert not is_valid_user_chat_id(None)
    assert not is_valid_user_chat_id("abc12345")

    # 真实用户 ID 通过验证
    assert is_valid_user_chat_id("1311430580")
    assert is_valid_user_chat_id(1311430580)
    assert is_valid_user_chat_id("876543210")


def test_telegram_auto_detect_chat_id():
    from agent.telegram_notifier import TelegramNotifier, TelegramConfig

    notifier = TelegramNotifier()
    # 模拟云端验证成功
    with patch.object(
        notifier,
        "get_chat_info",
        return_value=(True, {"first_name": "老谷", "username": "Lgwlip"}, "验证成功"),
    ):
        res = notifier.auto_detect_chat_id()
        assert res["success"] is True
        assert res["chat_id"] == "1311430580"
        assert "老谷" in res["display_title"]
        assert "@Lgwlip" in res["display_title"]


def test_telegram_config_dialog_auto_detect_button(qapp):
    mock_controller = MagicMock()
    mock_controller.get_telegram_config.return_value = {
        "enabled": False,
        "chat_id": "",
        "proxy_url": "",
    }
    mock_controller.auto_detect_telegram_chat_id.return_value = {
        "success": True,
        "chat_id": "1311430580",
        "name": "老谷",
        "username": "Lgwlip",
        "display_title": "老谷 (@Lgwlip)",
        "proxy_used": "http://127.0.0.1:10809",
    }

    dialog = TelegramConfigDialog(mock_controller)
    assert hasattr(dialog, "auto_detect_btn")
    assert hasattr(dialog, "chat_id_status_label")
    assert not dialog.enabled_checkbox.isChecked()

    # 点击自动获取按钮
    with patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
        dialog.auto_detect_btn.click()
        assert dialog.chat_id_input.text() == "1311430580"
        assert dialog.proxy_input.text() == "http://127.0.0.1:10809"
        assert "1311430580" in dialog.chat_id_status_label.text()
        assert dialog.enabled_checkbox.isChecked() is True
        mock_info.assert_called_once()
        mock_controller.save_telegram_config.assert_called_once()
        saved_cfg = mock_controller.save_telegram_config.call_args[0][0]
        assert saved_cfg["enabled"] is True


