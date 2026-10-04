# -*- coding: utf-8 -*-
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from agent.telegram_notifier import TelegramConfig, TelegramNotifier, DEFAULT_OFFICIAL_BOT_TOKEN


def test_telegram_config_defaults():
    cfg = TelegramConfig()
    assert not cfg.enabled
    assert not cfg.use_custom_bot
    assert cfg.get_effective_token() == DEFAULT_OFFICIAL_BOT_TOKEN
    assert cfg.notify_on_account_finish
    assert cfg.notify_on_all_finish


def test_telegram_config_custom_bot():
    cfg = TelegramConfig(enabled=True, use_custom_bot=True, bot_token="1234:custom", chat_id="999888")
    assert cfg.get_effective_token() == "1234:custom"
    assert cfg.chat_id == "999888"


def test_telegram_notifier_save_load(tmp_path: Path):
    notifier = TelegramNotifier(config_dir=tmp_path)
    cfg = TelegramConfig(enabled=True, chat_id="112233", proxy_url="http://127.0.0.1:7890")
    assert notifier.save_config(cfg)

    loaded = notifier.load_config()
    assert loaded.enabled
    assert loaded.chat_id == "112233"
    assert loaded.proxy_url == "http://127.0.0.1:7890"


def test_build_account_completion_report():
    msg = TelegramNotifier.build_account_completion_report(
        account_name="账号: 11",
        handle="moto_jp",
        duration_min=45.2,
        likes=25,
        follows=15,
        scanned=60,
        initial_followers=320,
        final_followers=328,
        initial_following=100,
        final_following=115,
    )
    assert "账号: 11" in msg
    assert "@moto_jp" in msg
    assert "净增 +8" in msg
    assert "净增 +15" in msg
    assert "25" in msg
    assert "达成通知" in msg

    # 测试人工中止/阶段成果通知
    msg_cancelled = TelegramNotifier.build_account_completion_report(
        account_name="账号: 11",
        handle="moto_jp",
        duration_min=10.0,
        likes=6,
        follows=5,
        scanned=41,
        status="CANCELLED",
    )
    assert "阶段成果通知" in msg_cancelled
    assert "人工中止" in msg_cancelled
    assert "• ➕ 今日新增关注：*5* 人（人工中止）" in msg_cancelled


def test_build_summary_report():
    accounts = [
        {"name": "账号: 11", "handle": "moto_jp", "follows": 15, "likes": 20, "scanned": 50, "initial_followers": 100, "final_followers": 105},
        {"name": "账号: 22", "handle": "sub_jp", "follows": 10, "likes": 12, "scanned": 30, "initial_followers": 50, "final_followers": 52},
    ]
    summary = TelegramNotifier.build_summary_report(accounts)
    assert "全盘运行汇总战报" in summary
    assert "今日累计关注：*25* 人" in summary
    assert "今日累计点赞：*32* 次" in summary
    assert "全局粉丝净增：*+7* 粉丝" in summary
    assert "[账号: 11]" in summary
    assert "[账号: 22]" in summary


def test_completion_report_zero_followers_omitted():
    """验证当粉丝为0或未变动时，彻底去除【📈 账号粉丝资产前后对比】及【持平】等垃圾信息."""
    msg = TelegramNotifier.build_account_completion_report(
        account_name="窗口-01",
        handle="crypto_trader_vip",
        duration_min=108.5,
        likes=30,
        follows=15,
        scanned=95,
        initial_followers=0,
        final_followers=0,
        initial_following=0,
        final_following=0,
        status="COMPLETED",
    )
    # 验证请求去除的字段绝对不出现
    assert "账号粉丝资产" not in msg
    assert "粉丝量" not in msg
    assert "关注量" not in msg
    assert "持平" not in msg
    assert "0 ➔ 0" not in msg

    # 验证排版优化要点
    assert "1 小时 48 分钟 (108.5 分钟)" in msg
    assert "@crypto_trader_vip" in msg
    assert "今日新增关注：*15* 人（达成配置目标）" in msg
    assert "今日推文点赞：*30* 次" in msg
    assert "扫描博主/推文：*95* 位" in msg
    assert "✅ 当日目标达成 · 已安全切回首页消痕" in msg
    assert "ℹ️ _由老谷控制中心 2026 自动推送_" in msg


def test_summary_report_zero_followers_clean():
    """验证大盘汇总时无粉丝变动下不显示冗余的0粉丝信息."""
    accounts = [
        {"name": "窗口-01", "handle": "moto_jp", "follows": 10, "likes": 15, "scanned": 40},
        {"name": "窗口-02", "handle": "tokyo_ceo", "follows": 5, "likes": 8, "scanned": 20},
    ]
    summary = TelegramNotifier.build_summary_report(accounts)
    assert "全盘运行汇总战报" in summary
    assert "今日累计关注：*15* 人" in summary
    assert "今日累计点赞：*23* 次" in summary
    assert "今日扫描总量：*60* 位" in summary
    assert "全局粉丝净增" not in summary
    assert "0 ➔ 0" not in summary
    assert "0➔0" not in summary



def test_send_raw_message_mock(tmp_path: Path):
    notifier = TelegramNotifier(config_dir=tmp_path)
    notifier.config = TelegramConfig(enabled=True, chat_id="12345", bot_token="fake_token", use_custom_bot=True)

    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps({"ok": True}).encode("utf-8")
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.OpenerDirector.open", return_value=fake_response):
        ok, reason = notifier.send_raw_message("12345", "Test Message")
        assert ok
        assert "成功" in reason


def test_format_telegram_api_error():
    chat_err = json.dumps({"ok": False, "description": "Bad Request: chat not found"})
    msg = TelegramNotifier._format_telegram_api_error(chat_err)
    assert "未找到该 Chat ID" in msg
    assert "@xchengxutz_bot" in msg
    assert "Start" in msg

    auth_err = json.dumps({"ok": False, "description": "Unauthorized"})
    msg2 = TelegramNotifier._format_telegram_api_error(auth_err)
    assert "Bot Token 无效" in msg2


def test_send_raw_message_socks5_dispatch(tmp_path: Path):
    notifier = TelegramNotifier(config_dir=tmp_path)
    notifier.config = TelegramConfig(enabled=True, chat_id="12345", bot_token="fake_token")

    with patch.object(TelegramNotifier, "_send_via_socks5", return_value=(True, "发送成功")) as mock_socks:
        ok, reason = notifier.send_raw_message("12345", "Test Message", proxy_url="socks5://127.0.0.1:10808")
        assert ok
        assert "成功" in reason
        mock_socks.assert_called_once()

