import asyncio
import pytest

from agent.x_automation_engine import AutomationConfig, RateLimitPause, XAutomationEngine


def test_automation_config_clamps_values_and_supports_keywords_alias():
    config = AutomationConfig.from_mapping({
        "keywords": " AI ",
        "daily_task_limit": "0",
        "max_engagement_threshold": "999999999",
        "ai_reply_ratio": "0.25",
    })
    assert config.keyword == "AI"
    assert config.daily_task_limit == 1
    assert config.max_engagement_threshold == 100_000_000
    assert config.ai_reply_ratio == 0.25


def test_automation_config_disables_ai_replies_and_clamps_invalid_ratios():
    assert AutomationConfig.from_mapping({"ai_reply_ratio": 0.0}).ai_reply_ratio == 0.0
    assert AutomationConfig.from_mapping({"ai_reply_ratio": 2}).ai_reply_ratio == 1.0
    assert AutomationConfig.from_mapping({"ai_reply_ratio": -1}).ai_reply_ratio == 0.0
    assert AutomationConfig.from_mapping({"ai_reply_ratio": True}).ai_reply_ratio == 0.15


def test_schedule_mode_preserves_smart_default_and_controls_time_window_bypass():
    assert AutomationConfig.from_mapping({}).bypass_time_window is False
    assert AutomationConfig.from_mapping({"schedule_mode": "immediate"}).bypass_time_window is True
    assert AutomationConfig.from_mapping({"schedule_mode": "scheduled"}).bypass_time_window is True
    assert AutomationConfig.from_mapping({"schedule_mode": "invalid"}).schedule_mode == "smart"


def test_read_only_snapshot_filters_keyword_and_thresholds():
    config = AutomationConfig.from_mapping({"keyword": "python", "max_follower_threshold": 150})
    result = XAutomationEngine._filter_read_only_snapshot(
        "Python profile\nFollowers 120\nPosts 20",
        url="https://x.com/home",
        title="Home",
        config=config,
    )
    assert result["read_only"] is True
    assert result["matched"] is True
    assert result["eligible"] is True


def test_rate_limit_text_is_detected():
    assert XAutomationEngine._find_rate_limit("请求过于频繁，请稍后再试") == "请求过于频繁"


def test_rate_limit_pause_carries_seconds():
    with pytest.raises(RateLimitPause) as error:
        raise RateLimitPause(23 * 60)
    assert error.value.seconds == 23 * 60


def test_daily_limit_skips_before_cdp_navigation():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    result = asyncio.run(engine.run({"daily_task_limit": 2, "daily_tasks_used": 2}))
    assert result["status"] == "SKIPPED"
    assert result["reason"] == "DAILY_TASK_LIMIT_REACHED"
