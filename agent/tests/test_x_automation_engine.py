import asyncio
import pytest

from agent.x_automation_engine import AutomationConfig, RateLimitPause, XAutomationEngine


def test_automation_config_clamps_values_and_supports_keywords_alias():
    config = AutomationConfig.from_mapping({"keywords": " AI ", "daily_task_limit": "0", "max_engagement_threshold": "999999999"})
    assert config.keyword == "AI"
    assert config.daily_task_limit == 1
    assert config.max_engagement_threshold == 100_000_000


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
