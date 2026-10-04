# -*- coding: utf-8 -*-
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from agent.x_automation_engine import (
    AutomationConfig,
    CaptchaChallengeDetected,
    RateLimitPause,
    AutomationStopped,
    XAutomationEngine,
    AccountFilterGuard,
    is_element_fully_loaded,
    generate_bezier_trajectory,
    human_discrete_scroll,
    safe_human_click,
)


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


def test_smart_schedule_enabled_controls_time_window_bypass():
    # When smart_schedule_enabled is False, bypass_time_window is always True regardless of schedule mode
    assert AutomationConfig.from_mapping({"smart_schedule_enabled": False}).bypass_time_window is True
    assert AutomationConfig.from_mapping({"smart_schedule_enabled": "false"}).bypass_time_window is True
    assert AutomationConfig.from_mapping({"smart_schedule_enabled": "0"}).bypass_time_window is True
    assert AutomationConfig.from_mapping({"smart_schedule_enabled": False, "schedule_mode": "smart"}).bypass_time_window is True

    # When smart_schedule_enabled is True, bypass_time_window is False unless single_batch_mode is True
    assert AutomationConfig.from_mapping({"smart_schedule_enabled": True}).bypass_time_window is False
    assert AutomationConfig.from_mapping({"smart_schedule_enabled": True, "single_batch_mode": True}).bypass_time_window is True

    # When smart_schedule_enabled is True even with schedule_mode="immediate", it enforces smart schedule
    assert AutomationConfig.from_mapping({"smart_schedule_enabled": True, "schedule_mode": "immediate"}).bypass_time_window is False


def test_evaluate_time_window_afternoon_full_coverage():
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8))

    # 1. 上午 09:30
    dt_morning = datetime(2026, 9, 19, 9, 30, tzinfo=tz)
    allowed, desc, limit = XAutomationEngine.evaluate_time_window(0, 100, now=dt_morning)
    assert allowed is True
    assert "上午窗口" in desc
    assert limit == 35

    # 2. 中午午休 12:30
    dt_noon = datetime(2026, 9, 19, 12, 30, tzinfo=tz)
    allowed, desc, limit = XAutomationEngine.evaluate_time_window(10, 100, now=dt_noon)
    assert allowed is False
    assert "非工作时段" in desc

    # 3. 下午前半段 14:30
    dt_afternoon_early = datetime(2026, 9, 19, 14, 30, tzinfo=tz)
    allowed, desc, limit = XAutomationEngine.evaluate_time_window(20, 100, now=dt_afternoon_early)
    assert allowed is True
    assert "14:00 - 16:00" in desc
    assert limit == 60

    # 4. 下午后半段 16:52 (用户截图中的时段，必须放行拓客！)
    dt_afternoon_late = datetime(2026, 9, 19, 16, 52, tzinfo=tz)
    allowed, desc, limit = XAutomationEngine.evaluate_time_window(20, 100, now=dt_afternoon_late)
    assert allowed is True
    assert "16:00 - 18:00" in desc
    assert limit == 60

    # 5. 傍晚 17:45
    dt_afternoon_end = datetime(2026, 9, 19, 17, 45, tzinfo=tz)
    allowed, desc, limit = XAutomationEngine.evaluate_time_window(30, 100, now=dt_afternoon_end)
    assert allowed is True
    assert "16:00 - 18:00" in desc
    assert limit == 60

    # 6. 晚间 19:30
    dt_evening = datetime(2026, 9, 19, 19, 30, tzinfo=tz)
    allowed, desc, limit = XAutomationEngine.evaluate_time_window(50, 100, now=dt_evening)
    assert allowed is True
    assert "晚间窗口" in desc
    assert limit == 100

    # 7. 深夜 23:30
    dt_night = datetime(2026, 9, 19, 23, 30, tzinfo=tz)
    allowed, desc, limit = XAutomationEngine.evaluate_time_window(50, 100, now=dt_night)
    assert allowed is False
    assert "非工作时段" in desc




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


def test_challenge_frame_is_not_swallowed_by_broad_exception_handler():
    class Page:
        url = "https://x.com/home"

        async def query_selector(self, _selector):
            return object()

    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    with pytest.raises(Exception) as error:
        asyncio.run(engine._assert_no_challenge(Page()))
    assert error.value.__class__.__name__ == "CaptchaChallengeDetected"


def test_page_ready_reports_false_when_no_ready_selector_is_found():
    class Page:
        async def wait_for_selector(self, _selector, timeout):
            raise TimeoutError(f"timed out after {timeout}")

    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    assert asyncio.run(engine._wait_for_page_ready(Page(), timeout_sec=0.01)) is False


def test_safe_goto_with_referer():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockPage:
        def __init__(self):
            self.url = "https://x.com/home"
            self.goto_calls = []

        async def goto(self, url, **kwargs):
            self.goto_calls.append((url, kwargs))

    page = MockPage()
    asyncio.run(engine._safe_goto(page, "https://x.com/search?q=test"))
    assert len(page.goto_calls) == 1
    url, kwargs = page.goto_calls[0]
    assert url == "https://x.com/search?q=test"
    assert kwargs.get("referer") == "https://x.com/home"


def test_account_filter_guard_bot_detection():
    # 正例测试（应当被拦截为 bot）
    positive_cases = [
        ("神薙茉莉bot（非公式）", "bot_kannagi"),
        ("柳智敏", "liuzhimin_bot"),
        ("柳智敏", "liuzhiminbot"),
        ("ChatGPT Bot", "chatgpt_bot"),
        ("每日美图bot", "daily_pic_bot"),
        ("自动化新闻", "auto_news"),
        ("Weather Robot", "weather_robot"),
        ("Funny Bot 2026", "funnybot"),
    ]
    for name, handle in positive_cases:
        is_bot, reason = AccountFilterGuard.is_bot_name_or_handle(name, handle)
        assert is_bot is True, f"Failed to detect bot for {name} / @{handle}"
        assert "bot" in reason or "机器人" in reason

    # 负例测试（真实普通用户，不应误伤）
    negative_cases = [
        ("Abbott Laboratories", "abbott_lab"),
        ("Talbot Smith", "talbot_s"),
        ("Both Of Us", "bothofus"),
        ("Sakura Miku", "miku_sakura"),
        ("张三", "zhangsan123"),
    ]
    for name, handle in negative_cases:
        is_bot, _ = AccountFilterGuard.is_bot_name_or_handle(name, handle)
        assert is_bot is False, f"False positive bot detection for {name} / @{handle}"


def test_account_filter_guard_parody_detection():
    assert AccountFilterGuard.is_parody_or_automated("🎭 戏仿账号")[0] is True
    assert AccountFilterGuard.is_parody_or_automated("Parody account of Elon")[0] is True
    assert AccountFilterGuard.is_parody_or_automated("Fan account for Karina")[0] is True
    assert AccountFilterGuard.is_parody_or_automated("神薙茉莉（非公式）")[0] is True
    assert AccountFilterGuard.is_parody_or_automated("公式アカウント")[0] is False
    assert AccountFilterGuard.is_parody_or_automated("普通个人日常记录")[0] is False


def test_account_filter_guard_check_candidate_account():
    cfg_default = AutomationConfig.from_mapping({})
    assert cfg_default.filter_verified_accounts is True
    assert cfg_default.filter_parody_accounts is True
    assert cfg_default.filter_bot_accounts is True

    # 1. 认证账号（蓝勾/金勾）
    is_f, r = AccountFilterGuard.check_candidate_account(
        name="Tech Reviewer", handle="tech_rev", has_verified_badge=True, config=cfg_default
    )
    assert is_f is True
    assert "已认证账号" in r

    # 允许已认证账号（关闭过滤）
    cfg_allow_verified = AutomationConfig.from_mapping({"filter_verified_accounts": False})
    is_f, _ = AccountFilterGuard.check_candidate_account(
        name="Tech Reviewer", handle="tech_rev", has_verified_badge=True, config=cfg_allow_verified
    )
    assert is_f is False

    # 2. 戏仿账号
    is_f, r = AccountFilterGuard.check_candidate_account(
        name="柳智敏", handle="liuzhimin", card_or_dom_text="🎭 戏仿账号", config=cfg_default
    )
    assert is_f is True
    assert "戏仿" in r

    # 3. Bot 账号
    is_f, r = AccountFilterGuard.check_candidate_account(
        name="神薙茉莉bot", handle="bot_kannagi", config=cfg_default
    )
    assert is_f is True
    assert "bot" in r

    # 4. 正常普通博主
    is_f, _ = AccountFilterGuard.check_candidate_account(
        name="田中太郎", handle="tanaka_taro", card_or_dom_text="日常、写真、カフェ巡り", config=cfg_default
    )
    assert is_f is False


def test_is_element_fully_loaded_hit_testing():
    class MockHitElement:
        def __init__(self, visible=True, width=100, height=50, hit_valid=True):
            self._visible = visible
            self._width = width
            self._height = height
            self._hit_valid = hit_valid

        async def scroll_into_view_if_needed(self, timeout=2000):
            pass

        async def is_visible(self):
            return self._visible

        async def bounding_box(self):
            return {"x": 100, "y": 100, "width": self._width, "height": self._height}

        async def evaluate(self, js, arg):
            return self._hit_valid

    # 1. 正常完全可见元素
    ok_el = MockHitElement(visible=True, width=100, height=50, hit_valid=True)
    assert asyncio.run(is_element_fully_loaded(ok_el)) is True

    # 2. 被蒙版遮挡或透明度过低 (Hit-test 校验失败)
    occluded_el = MockHitElement(visible=True, width=100, height=50, hit_valid=False)
    assert asyncio.run(is_element_fully_loaded(occluded_el)) is False

    # 3. 尺寸为 0 的隐形诱饵
    zero_size_el = MockHitElement(visible=True, width=0, height=50, hit_valid=True)
    assert asyncio.run(is_element_fully_loaded(zero_size_el)) is False


def test_generate_bezier_trajectory_tremor_and_bounds():
    start = (100.0, 100.0)
    end = (500.0, 400.0)
    steps = 20
    points = generate_bezier_trajectory(start, end, steps, max_bounds=(1280.0, 800.0))
    assert len(points) == steps
    # 终点严格收敛至目标位置或目标边界内
    last_pt = points[-1]
    assert abs(last_pt[0] - end[0]) < 1.0
    assert abs(last_pt[1] - end[1]) < 1.0
    # 所有轨迹点均在边界安全区域内
    for px, py in points:
        assert 0.0 <= px <= 1280.0
        assert 0.0 <= py <= 800.0


def test_human_discrete_scroll_bidirectional():
    class MockMouse:
        def __init__(self):
            self.wheels = []

        async def wheel(self, dx, dy):
            self.wheels.append((dx, dy))

    class MockPage:
        def __init__(self):
            self.mouse = MockMouse()

    page = MockPage()
    # Scroll downwards (positive)
    asyncio.run(human_discrete_scroll(page, distance=300))
    assert len(page.mouse.wheels) > 0
    total_y = sum(dy for _, dy in page.mouse.wheels)
    assert total_y == 300

    # Scroll upwards (negative)
    page.mouse.wheels.clear()
    asyncio.run(human_discrete_scroll(page, distance=-250))
    assert len(page.mouse.wheels) > 0
    total_neg_y = sum(dy for _, dy in page.mouse.wheels)
    assert total_neg_y == -250


def test_human_discrete_scroll_ease_out_decay():
    class MockMouse:
        def __init__(self):
            self.wheels = []

        async def wheel(self, dx, dy):
            self.wheels.append((dx, dy))

    class MockPage:
        def __init__(self):
            self.mouse = MockMouse()

    page = MockPage()
    asyncio.run(human_discrete_scroll(page, distance=600))
    forward_wheels_y = [dy for _, dy in page.mouse.wheels if dy > 0]
    assert sum(forward_wheels_y) == 600
    assert len(forward_wheels_y) >= 2
    assert forward_wheels_y[0] > forward_wheels_y[-1]


def test_dismiss_hover_card_neutral_viewport_area():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockMouse:
        def __init__(self):
            self.positions = []

        async def move(self, x, y):
            self.positions.append((x, y))

    class MockPage:
        viewport_size = {"width": 1000, "height": 600}

        def __init__(self):
            self.mouse = MockMouse()

    page = MockPage()
    asyncio.run(engine._dismiss_hover_card(page))
    assert len(page.mouse.positions) == 1
    x, y = page.mouse.positions[0]
    # Neutral zone: x in [120, 280], y in [210, 390], never (10, 10)
    assert 120 <= x <= 280
    assert 210 <= y <= 390
    assert (x, y) != (10, 10)


def test_detect_hover_card_with_human_dwell_finds_loaded():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockCard:
        async def scroll_into_view_if_needed(self, timeout=2000):
            pass

        async def is_visible(self):
            return True

        async def bounding_box(self):
            return {"x": 200, "y": 200, "width": 300, "height": 200}

        async def evaluate(self, js, arg):
            return True

    class MockPage:
        async def query_selector(self, sel):
            return MockCard()

    card = asyncio.run(engine._detect_hover_card_with_human_dwell(MockPage()))
    assert card is not None


def test_verify_follow_success_detects_rate_limit_toast():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockToast:
        async def is_visible(self):
            return True

        async def inner_text(self):
            return "You are unable to follow more people at this time. Learn more"

    class MockPage:
        async def query_selector(self, sel):
            if 'div[data-testid="toast"]' in sel:
                return MockToast()
            return None

    ok, reason = asyncio.run(engine._verify_follow_success(MockPage()))
    assert ok is False
    assert "RATE_LIMITED" in reason


def test_verify_follow_success_detects_reverted_follow_button():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockFollowBtn:
        async def is_visible(self):
            return True

    class MockPage:
        async def query_selector(self, sel):
            if 'button[data-testid$="-follow"]' in sel:
                return MockFollowBtn()
            return None

    ok, reason = asyncio.run(engine._verify_follow_success(MockPage()))
    assert ok is False
    assert reason == "SILENT_REJECTION"


def test_verify_follow_success_detects_unfollow_button():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockUnfollowBtn:
        async def is_visible(self):
            return True

    class MockPage:
        async def query_selector(self, sel):
            if 'button[data-testid$="-unfollow"]' in sel:
                return MockUnfollowBtn()
            return None

    ok, reason = asyncio.run(engine._verify_follow_success(MockPage()))
    assert ok is True
    assert reason == "FOLLOWED"


def test_check_run_control_pauses_and_resumes():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockGuard:
        def __init__(self):
            self.paused_states = [True, False]

        def is_cancelled(self):
            return False

        def is_paused(self):
            if self.paused_states:
                return self.paused_states.pop(0)
            return False

    engine.safety_guard = MockGuard()
    asyncio.run(engine._check_run_control())


def test_check_run_control_raises_automation_stopped():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockCancelledGuard:
        def is_cancelled(self):
            return True

        def is_paused(self):
            return False

    engine.safety_guard = MockCancelledGuard()
    with pytest.raises(AutomationStopped):
        asyncio.run(engine._check_run_control())


def test_capture_risk_snapshot_creates_file(tmp_path):
    from pathlib import Path
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    engine.risk_artifact_dir = str(tmp_path)

    class MockPage:
        async def screenshot(self, path, full_page=False):
            with open(path, "wb") as f:
                f.write(b"mock_png_data")

    path = asyncio.run(engine._capture_risk_snapshot(MockPage(), "test_challenge"))
    assert path != ""
    assert Path(path).exists()


def test_dismiss_modal_dialogs_presses_escape():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockKeyboard:
        def __init__(self):
            self.pressed = []

        async def press(self, key):
            self.pressed.append(key)

    class MockPage:
        def __init__(self):
            self.keyboard = MockKeyboard()

        async def query_selector(self, sel):
            return None

    page = MockPage()
    asyncio.run(engine._dismiss_modal_dialogs(page))
    assert "Escape" in page.keyboard.pressed


def test_response_callback_and_risk_reason_triggers_rate_limit_pause():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockResponse:
        status = 429
        url = "https://x.com/i/api/graphql/xxx"

    engine._response_callback(MockResponse())
    assert engine._risk_reason == "HTTP_429_RATE_LIMITED"

    class MockPage:
        url = "https://x.com/home"

        async def query_selector(self, sel):
            return None

    with pytest.raises(RateLimitPause):
        asyncio.run(engine._assert_no_challenge(MockPage()))


def test_suspended_account_url_detection():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockPage:
        url = "https://x.com/account/suspended"

        async def query_selector(self, sel):
            return None

    with pytest.raises(Exception) as exc_info:
        asyncio.run(engine._assert_no_challenge(MockPage()))
    assert "Suspended" in str(exc_info.value) or "冻结" in str(exc_info.value)


def test_bookmark_reverse_action_avoidance():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockButton:
        async def is_visible(self):
            return True

        async def bounding_box(self):
            return {"x": 10, "y": 10, "width": 20, "height": 20}

        async def get_attribute(self, attr):
            if attr == "aria-label":
                return "Remove Tweet from Bookmarks"
            return None

    class MockArticle:
        async def query_selector(self, sel):
            if sel == 'button[data-testid="bookmark"]':
                return MockButton()
            return None

    class MockPage:
        pass

    res = asyncio.run(engine._add_bookmark(MockPage(), MockArticle(), "post_1"))
    assert res is False


def test_retweet_reverse_action_avoidance():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockButton:
        async def is_visible(self):
            return True

        async def bounding_box(self):
            return {"x": 10, "y": 10, "width": 20, "height": 20}

        async def get_attribute(self, attr):
            if attr == "aria-label":
                return "Undo Repost"
            return None

    class MockArticle:
        async def query_selector(self, sel):
            if sel == 'button[data-testid="retweet"]':
                return MockButton()
            return None

    class MockPage:
        pass

    res = asyncio.run(engine._do_retweet(MockPage(), MockArticle(), "post_1"))
    assert res is False


def test_control_exceptions_propagated_from_subroutines():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class RaisingPage:
        keyboard = None

        async def query_selector(self, sel):
            raise AutomationStopped("task cancelled by operator")

        async def goto(self, *args, **kwargs):
            raise AutomationStopped("task cancelled by operator")

    # 1. _interact_on_profile_page must re-raise AutomationStopped
    with pytest.raises(AutomationStopped):
        asyncio.run(engine._interact_on_profile_page(RaisingPage(), "bad_user", "#tag"))

    # 2. _browse_home_feed must re-raise AutomationStopped
    with pytest.raises(AutomationStopped):
        asyncio.run(engine._browse_home_feed(RaisingPage()))


def test_persistent_history_pool_integration(tmp_path):
    from agent.history_pool import PersistentHistoryPool
    db_file = tmp_path / "engine_pool.json"
    pool = PersistentHistoryPool(storage_file=db_file)
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222", history_pool=pool)
    engine.tag = "acc_alpha"

    # Initially not visited
    assert engine.history_pool.is_visited("acc_alpha", "satle40") is False

    # Mark visited
    engine.history_pool.mark_visited("acc_alpha", "satle40")
    assert engine.history_pool.is_visited("acc_alpha", "satle40") is True


def test_sleep_with_control_aborts_on_cancelled_guard():
    class CancelledGuard:
        def is_cancelled(self):
            return True

        def is_paused(self):
            return False

    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222", safety_guard=CancelledGuard())
    with pytest.raises(AutomationStopped):
        asyncio.run(engine._sleep_with_control(600.0))


def test_safe_human_click_mouse_up_guaranteed_on_exception():
    from agent.x_automation_engine import safe_human_click, ProfilePersonality

    mouse_events = []

    class MockMouse:
        async def move(self, *args, **kwargs):
            pass

        async def down(self):
            mouse_events.append("down")

        async def up(self):
            mouse_events.append("up")

    class CrashingPage:
        mouse = MockMouse()

    class MockElement:
        async def is_visible(self):
            return True

        async def bounding_box(self):
            return {"x": 10, "y": 10, "width": 20, "height": 20}

        async def hover(self):
            pass

    personality = ProfilePersonality("http://127.0.0.1:9222")
    personality.press_duration = (0.01, 0.02)

    class ErrorOnSleepPage:
        def __init__(self):
            self.mouse = MockMouse()

    page = ErrorOnSleepPage()

    # Test normal click guarantees down and up
    res = asyncio.run(safe_human_click(page, MockElement(), personality))
    assert res is True
    assert "down" in mouse_events
    assert "up" in mouse_events
    assert mouse_events.count("down") == mouse_events.count("up")

    # Test when exception occurs after mouse.down, finally still calls mouse.up
    class FailingMouse:
        def __init__(self):
            self.actions = []

        async def move(self, *args, **kwargs):
            pass

        async def down(self):
            self.actions.append("down")
            raise RuntimeError("unexpected failure while pressing button")

        async def up(self):
            self.actions.append("up")

    class PageWithFailingMouseDown:
        def __init__(self):
            self.mouse = FailingMouse()

    failing_page = PageWithFailingMouseDown()
    res_fail = asyncio.run(safe_human_click(failing_page, MockElement(), personality))
    assert res_fail is False
    assert "down" in failing_page.mouse.actions


def test_browse_home_feed_filters_nsfw_and_respects_quota():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockArticle:
        def __init__(self, text, allow_like=True):
            self.text = text
            self.allow_like = allow_like

        async def is_visible(self):
            return True

        async def bounding_box(self):
            return {"x": 10, "y": 10, "width": 20, "height": 20}

        async def inner_text(self):
            return self.text

        async def query_selector(self, sel):
            return None

    class MockFeedPage:
        async def goto(self, *args, **kwargs):
            pass

        async def query_selector_all(self, sel):
            # Returns an NSFW article and an ad article
            return [
                MockArticle("パパ活 募集中 裏垢女子 稼げる副業"),
                MockArticle("成人 熟女 巨乳 无码 视频"),
            ]

    page = MockFeedPage()
    likes = asyncio.run(engine._browse_home_feed(page))
    # Both articles are spam/NSFW, so likes must be 0
    assert likes == 0


def test_arkose_labs_and_account_locked_detection():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    # 1. URL with account/locked
    class LockedPage:
        url = "https://x.com/account/locked?lang=en"

        async def query_selector(self, sel):
            return None

    with pytest.raises(CaptchaChallengeDetected):
        asyncio.run(engine._assert_no_challenge(LockedPage()))

    # 2. Page containing Arkose Labs iframe
    class ArkosePage:
        url = "https://x.com/home"

        async def query_selector(self, sel):
            if "arkoselabs" in sel:
                class MockFrame:
                    pass
                return MockFrame()
            return None

    with pytest.raises(CaptchaChallengeDetected):
        asyncio.run(engine._assert_no_challenge(ArkosePage()))


def test_chain_hop_config_defaults_and_parsing():
    cfg = AutomationConfig.from_mapping({
        "chain_hop_enabled": "true",
        "chain_hop_ratio": 0.85,
        "max_chain_depth": 3,
    })
    assert cfg.chain_hop_enabled is True
    assert cfg.chain_hop_ratio == 0.85
    assert cfg.max_chain_depth == 3

    cfg_off = AutomationConfig.from_mapping({"chain_hop_enabled": False})
    assert cfg_off.chain_hop_enabled is False


def test_chain_hop_candidate_retry_on_click_failure():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    engine.my_username = "test_self"

    clicked_handles = []

    class MockMouse:
        def __init__(self):
            self.current_handle = None

        async def move(self, x, y):
            pass

        async def down(self):
            if self.current_handle == "cand_fail":
                raise RuntimeError("Element click obscured or intercepted")

        async def up(self):
            pass

        async def wheel(self, dx, dy):
            pass

    page_mouse = MockMouse()

    class MockLink:
        def __init__(self, handle, can_click=True):
            self.handle = handle
            self.can_click = can_click

        async def get_attribute(self, attr):
            if attr == "href":
                return f"/{self.handle}"
            return ""

        async def is_visible(self):
            return True

        async def scroll_into_view_if_needed(self, **kwargs):
            pass

        async def hover(self):
            pass

        async def bounding_box(self):
            page_mouse.current_handle = self.handle
            return {"x": 100, "y": 100, "width": 50, "height": 20}

        async def click(self, **kwargs):
            if not self.can_click:
                raise RuntimeError("Element click obscured or intercepted")
            clicked_handles.append(self.handle)

    class MockHoverCard:
        def __init__(self, handle):
            self.handle = handle

        async def inner_text(self):
            return f"@{self.handle} 120 followers"

        async def query_selector(self, selector):
            return None

    class MockCell:
        def __init__(self, handle, can_click=True):
            self.handle = handle
            self.link = MockLink(handle, can_click)

        async def query_selector(self, selector):
            if 'button[data-testid$="-unfollow"]' in selector:
                return None
            if 'a[href^="/"][role="link"]' in selector:
                return self.link
            return None

    class MockPage:
        def __init__(self):
            self.url = "https://x.com/seed_user/following"
            self.mouse = page_mouse

        async def query_selector_all(self, selector):
            if "UserCell" in selector:
                return [MockCell("cand_fail", can_click=False), MockCell("cand_success", can_click=True)]
            return []

        async def query_selector(self, selector):
            return None

        async def goto(self, url, **kwargs):
            self.url = url

    page = MockPage()

    async def mock_detect_hover(p):
        return MockHoverCard(page_mouse.current_handle or "cand")

    async def mock_followers(p, handle, card):
        return 100, -1

    async def mock_dismiss(p):
        pass

    async def mock_interact(p, handle):
        clicked_handles.append(f"interacted_{handle}")
        return True, 1

    async def mock_safe_go_back(p):
        return True

    engine._detect_hover_card_with_human_dwell = mock_detect_hover
    engine._get_followers_robust = mock_followers
    engine._dismiss_hover_card = mock_dismiss
    engine._interact_on_profile_page_simple = mock_interact
    engine._safe_go_back = mock_safe_go_back

    cfg = AutomationConfig.from_mapping({
        "chain_hop_enabled": True,
        "max_follower_threshold": 500,
        "max_chain_depth": 1,
        "graphql_scout_filter_enabled": False,
    })

    e, l, f, v = asyncio.run(engine._chain_hop_following_exploration(
        page, "seed_user", cfg, current_chain_depth=1, current_total_exec=0, remaining_batch_budget=5
    ))

    # cand_fail click failed, so cand_success was attempted and successfully interacted with!
    assert "interacted_cand_success" in clicked_handles
    assert f == 1


def test_detect_hover_card_with_human_dwell_layers_dialog():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockLayersCard:
        async def is_visible(self):
            return True

        async def bounding_box(self):
            return {"x": 150, "y": 150, "width": 280, "height": 180}

    class MockPage:
        async def query_selector(self, sel):
            if "layers" in sel or "HoverCard" in sel:
                return MockLayersCard()
            return None

    card = asyncio.run(engine._detect_hover_card_with_human_dwell(MockPage()))
    assert card is not None


def test_get_followers_robust_parses_multilingual():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    class MockLink:
        def __init__(self, text):
            self._text = text

        async def inner_text(self):
            return self._text

    class MockContainer:
        def __init__(self, text):
            self._text = text

        async def query_selector_all(self, sel):
            return [MockLink(self._text)]

        async def inner_text(self):
            return self._text

    # Japanese: "15 フォロワー"
    res_ja, _ = asyncio.run(engine._get_followers_robust(None, "test_ja", MockContainer("15 フォロワー")))
    assert res_ja == 15

    # English: "100 Followers"
    res_en, _ = asyncio.run(engine._get_followers_robust(None, "test_en", MockContainer("100 Followers")))
    assert res_en == 100

    # Chinese: "3.5K 关注者"
    res_zh, _ = asyncio.run(engine._get_followers_robust(None, "test_zh", MockContainer("3.5K 关注者")))
    assert res_zh == 3500


def test_automation_config_parses_preset_and_batch_jitter():
    cfg_turbo = AutomationConfig.from_mapping({
        "execution_preset": "turbo",
        "batch_jitter_enabled": False,
        "batch_interval_minutes": 1,
    })
    assert cfg_turbo.execution_preset == "turbo"
    assert cfg_turbo.batch_jitter_enabled is False
    assert cfg_turbo.batch_interval_minutes == 1

    cfg_safe = AutomationConfig.from_mapping({
        "execution_preset": "safe",
        "batch_jitter_enabled": True,
        "batch_interval_minutes": 15,
    })
    assert cfg_safe.execution_preset == "safe"
    assert cfg_safe.batch_jitter_enabled is True
    assert cfg_safe.batch_interval_minutes == 15


def test_compute_batch_cooldown_turbo_mode_respects_1_min_setting():
    cfg = AutomationConfig(
        execution_preset="turbo",
        batch_interval_minutes=1,
        batch_jitter_enabled=True,
    )
    for _ in range(50):
        cd_min, micro = cfg.compute_batch_cooldown()
        assert cd_min == 1, f"Expected 1 min for turbo with 1-min setting, got {cd_min}"
        assert 5 <= micro <= 25

    cfg_2m = AutomationConfig(
        execution_preset="turbo",
        batch_interval_minutes=2,
        batch_jitter_enabled=True,
    )
    for _ in range(50):
        cd_min, micro = cfg_2m.compute_batch_cooldown()
        assert cd_min in {1, 2, 3}, f"Expected 1~3 min for turbo with 2-min setting, got {cd_min}"
        assert 5 <= micro <= 25


def test_compute_batch_cooldown_jitter_disabled_returns_exact_value():
    cfg = AutomationConfig(
        execution_preset="turbo",
        batch_interval_minutes=1,
        batch_jitter_enabled=False,
    )
    cd_min, micro = cfg.compute_batch_cooldown()
    assert cd_min == 1
    assert micro == 0

    cfg2 = AutomationConfig(
        execution_preset="safe",
        batch_interval_minutes=7,
        batch_jitter_enabled=False,
    )
    cd_min2, micro2 = cfg2.compute_batch_cooldown()
    assert cd_min2 == 7
    assert micro2 == 0


def test_compute_batch_cooldown_safe_mode():
    cfg = AutomationConfig(
        execution_preset="safe",
        batch_interval_minutes=15,
        batch_jitter_enabled=True,
    )
    for _ in range(50):
        cd_min, micro = cfg.compute_batch_cooldown()
        assert 13 <= cd_min <= 18
        assert 10 <= micro <= 35


def test_keyword_chain_config_and_depth_zero():
    cfg = AutomationConfig.from_mapping({
        "outreach_mode": "keyword_chain",
        "max_chain_depth": 0,
        "chain_hop_enabled": True,
        "chain_hop_ratio": 1.0,
    })
    assert cfg.outreach_mode == "keyword_chain"
    assert cfg.max_chain_depth == 0
    assert cfg.is_chain_infinite is True
    assert cfg.chain_hop_enabled is True
    assert cfg.chain_hop_ratio == 1.0


def test_chain_backtrack_logic():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    engine._chain_stack = ["user_a", "user_b", "user_c"]
    engine._chain_current_following_handle = "user_c"

    # 1. 回溯第一层 (弹回 user_b)
    engine._chain_backtrack()
    assert engine._chain_stack == ["user_a", "user_b"]
    assert engine._chain_current_following_handle == "user_b"

    # 2. 回溯第二层 (弹回 user_a)
    engine._chain_backtrack()
    assert engine._chain_stack == ["user_a"]
    assert engine._chain_current_following_handle == "user_a"

    # 3. 回溯第三层 (栈底清空，重置为空等待新种子)
    engine._chain_backtrack()
    assert engine._chain_stack == []
    assert engine._chain_current_following_handle == ""


def test_soft_landing_config_default_and_toggle():
    # Default is enabled
    cfg = AutomationConfig.from_mapping({})
    assert cfg.enable_soft_landing is True

    # Disable via enable_soft_landing
    cfg_off = AutomationConfig.from_mapping({"enable_soft_landing": False})
    assert cfg_off.enable_soft_landing is False

    # Disable via soft_landing_enabled
    cfg_off2 = AutomationConfig.from_mapping({"soft_landing_enabled": False})
    assert cfg_off2.enable_soft_landing is False


def test_is_promoted_tweet():
    async def _run():
        # 1. Article with placementTracking
        mock_art_promoted = MagicMock()
        mock_art_promoted.query_selector = AsyncMock(return_value=MagicMock())
        is_promo = await XAutomationEngine._is_promoted_tweet(mock_art_promoted)
        assert is_promo is True

        # 2. Article without promo elements
        mock_art_clean = MagicMock()
        mock_art_clean.query_selector = AsyncMock(return_value=None)
        mock_art_clean.inner_text = AsyncMock(return_value="今日はいい天気ですね！")
        is_promo2 = await XAutomationEngine._is_promoted_tweet(mock_art_clean)
        assert is_promo2 is False

    asyncio.run(_run())


def test_handle_response_interception_403_and_graphql_error():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

        # 1. HTTP 403 on target candidate must NOT set _risk_reason (avoids false-positive halt)
        mock_resp_403 = MagicMock()
        mock_resp_403.status = 403
        mock_resp_403.url = "https://x.com/i/api/graphql/xxx"
        await engine._handle_response_interception(mock_resp_403)
        assert engine._risk_reason == ""

        # 2. HTTP 429 on core API endpoint sets HTTP_429_RATE_LIMITED
        mock_resp_429 = MagicMock()
        mock_resp_429.status = 429
        mock_resp_429.url = "https://x.com/i/api/graphql/SearchTimeline"
        await engine._handle_response_interception(mock_resp_429)
        assert engine._risk_reason == "HTTP_429_RATE_LIMITED"
        engine._risk_reason = ""

        # 3. GraphQL response with error code 326
        mock_resp_graphql = MagicMock()
        mock_resp_graphql.status = 200
        mock_resp_graphql.url = "https://x.com/i/api/graphql/UserByScreenName"
        mock_resp_graphql.headers = {"content-type": "application/json"}
        mock_resp_graphql.json = AsyncMock(return_value={
            "errors": [{"code": 326, "message": "To protect our users, this account is temporarily locked."}]
        })
        await engine._handle_response_interception(mock_resp_graphql)
        assert engine._risk_reason == "GRAPHQL_ERROR_326"

    asyncio.run(_run())


def test_handle_response_interception_friends_count():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.url = "https://x.com/i/api/graphql/UserDetail"
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json = AsyncMock(return_value={
            "data": {
                "user": {
                    "result": {
                        "legacy": {
                            "screen_name": "test_user",
                            "followers_count": 88,
                            "friends_count": 120,
                            "statuses_count": 45,
                            "verified": False,
                            "protected": True,
                            "name": "Test Account",
                            "description": "Just a normal user",
                        }
                    }
                }
            }
        })
        await engine._handle_response_interception(mock_resp)
        assert "test_user" in engine.user_cache
        assert engine.user_cache["test_user"]["followers_count"] == 88
        assert engine.user_cache["test_user"]["friends_count"] == 120
        assert engine.user_cache["test_user"]["protected"] is True

    asyncio.run(_run())


def test_response_callback_precision():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    # 1. Non-core telemetry / image 429 must not set risk_reason
    resp_telemetry = MagicMock()
    resp_telemetry.status = 429
    resp_telemetry.url = "https://x.com/i/api/1.1/jot/client_event.json"
    engine._response_callback(resp_telemetry)
    assert engine._risk_reason == ""

    # 2. Account access challenge
    resp_access = MagicMock()
    resp_access.status = 200
    resp_access.url = "https://x.com/account/access"
    engine._response_callback(resp_access)
    assert engine._risk_reason == "ACCOUNT_ACCESS_CHALLENGE"
    engine._risk_reason = ""

    # 3. Core mutation 429 sets HTTP_429_RATE_LIMITED
    resp_follow_429 = MagicMock()
    resp_follow_429.status = 429
    resp_follow_429.url = "https://x.com/i/api/graphql/CreateFriendship"
    engine._response_callback(resp_follow_429)
    assert engine._risk_reason == "HTTP_429_RATE_LIMITED"


def test_interact_on_profile_page_simple_lock_icon_skip():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        mock_page = MagicMock()
        mock_page.wait_for_selector = AsyncMock(return_value=None)
        mock_page.evaluate = AsyncMock(return_value=None)

        # Mock lock icon present
        mock_lock = MagicMock()
        mock_page.query_selector = AsyncMock(return_value=mock_lock)

        followed, likes = await engine._interact_on_profile_page_simple(mock_page, "private_user")
        assert followed is False
        assert likes == 0

    asyncio.run(_run())


def test_navigation_context_destruction_recovered():
    from unittest.mock import patch
    from playwright.async_api import Error as PlaywrightError

    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        mock_page = MagicMock()
        mock_page.on = MagicMock()
        mock_page.remove_listener = MagicMock()
        mock_page.set_viewport_size = AsyncMock()

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]
        mock_browser.disconnect = AsyncMock()

        mock_playwright = MagicMock()
        mock_playwright.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

        class MockPlaywrightContext:
            async def __aenter__(self):
                return mock_playwright

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch("playwright.async_api.async_playwright", return_value=MockPlaywrightContext()):
            engine._select_or_open_x_page = AsyncMock(return_value=mock_page)
            engine._wait_for_login_state_surface = AsyncMock()
            engine._check_login_status = AsyncMock(return_value=True)
            engine._assert_no_challenge = AsyncMock()
            engine._wait_for_page_ready = AsyncMock(return_value=True)

            # Simulate Playwright throwing Execution context was destroyed
            engine._run_single_batch = AsyncMock(side_effect=PlaywrightError("Execution context was destroyed, most likely because of a navigation"))

            result = await engine.run({
                "daily_task_limit": 10,
                "daily_tasks_used": 0,
                "single_batch_mode": True,
            })
            assert result["status"] == "SUCCESS"
            assert result.get("recovered_from_navigation") is True

    asyncio.run(_run())



def test_interact_on_profile_page_simple_like_disabled_short_circuit():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        engine._active_config = MagicMock(allow_like=False, max_follower_threshold=1000)
        mock_page = MagicMock()
        mock_page.wait_for_selector = AsyncMock(return_value=None)
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.query_selector_all = AsyncMock(return_value=[])

        followed, likes = await engine._interact_on_profile_page_simple(mock_page, "test_user")
        assert likes == 0
        # When allow_like is False, query_selector_all("article") should NOT be called
        for call in mock_page.query_selector_all.call_args_list:
            assert "article" not in call[0]

    asyncio.run(_run())


def test_safe_human_click_blocks_external_links_and_cards():
    async def _run():
        mock_page = MagicMock()
        mock_personality = MagicMock()

        # 1. Simulate an external link element (e.g. card with target="_blank")
        ext_element = MagicMock()
        ext_element.is_visible = AsyncMock(return_value=True)
        ext_element.evaluate = AsyncMock(return_value=True)  # triggers external jump detection

        clicked = await safe_human_click(mock_page, ext_element, mock_personality)
        assert clicked is False
        assert mock_page.mouse.down.called is False

        # 2. Simulate a normal Twitter element (like button, follow button, username)
        normal_element = MagicMock()
        normal_element.is_visible = AsyncMock(return_value=True)

        async def mock_eval(js_code, *args):
            if "card." in js_code or "userurl" in js_code:
                return False  # not external
            return True  # hit_ok

        normal_element.evaluate = AsyncMock(side_effect=mock_eval)
        normal_element.scroll_into_view_if_needed = AsyncMock()
        normal_element.bounding_box = AsyncMock(return_value={"x": 100, "y": 100, "width": 50, "height": 30})
        normal_element.hover = AsyncMock()

        mock_page.viewport_size = {"width": 1280, "height": 800}
        mock_page.mouse.move = AsyncMock()
        mock_page.mouse.down = AsyncMock()
        mock_page.mouse.up = AsyncMock()
        mock_personality.mouse_steps = 5
        mock_personality.press_duration = (0.05, 0.1)

        clicked_normal = await safe_human_click(mock_page, normal_element, mock_personality)
        assert clicked_normal is True
        assert mock_page.mouse.down.called is True

    asyncio.run(_run())


def test_ensure_on_track_and_clean_tabs_preserves_x_tabs_and_closes_external():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        engine._ensure_page_focus_emulation = AsyncMock()

        # Main automation page
        main_page = MagicMock()
        main_page.url = "https://x.com/search?q=%23test"
        main_page.is_closed = MagicMock(return_value=False)
        main_page.bring_to_front = AsyncMock()

        # User's manually opened X tab (should NOT be closed)
        user_x_tab = MagicMock()
        user_x_tab.url = "https://x.com/notifications"
        user_x_tab.is_closed = MagicMock(return_value=False)
        user_x_tab.close = AsyncMock()

        # Rogue external website tab (SHOULD be closed)
        ext_tab = MagicMock()
        ext_tab.url = "https://some-external-official-store.com/product"
        ext_tab.is_closed = MagicMock(return_value=False)
        ext_tab.close = AsyncMock()

        mock_context = MagicMock()
        mock_context.pages = [main_page, user_x_tab, ext_tab]
        main_page.context = mock_context

        on_track = await engine._ensure_on_track_and_clean_tabs(main_page, current_keyword="#test")
        assert on_track is True

        # External tab must be closed
        ext_tab.close.assert_called_once()
        # User's X tab must NOT be closed
        user_x_tab.close.assert_not_called()
        # Silent focus emulation must be called, and bring_to_front must NOT be called (prevent stealing window focus)
        engine._ensure_page_focus_emulation.assert_called_once_with(main_page)
        main_page.bring_to_front.assert_not_called()

    asyncio.run(_run())


def test_ensure_on_track_and_clean_tabs_recovers_offtrack_url():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        engine._safe_go_back = AsyncMock(return_value=False)
        engine.navigate_to_keyword_search = AsyncMock()
        engine._ensure_page_focus_emulation = AsyncMock()

        main_page = MagicMock()
        main_page.url = "https://random-scam-site.com/home"
        main_page.is_closed = MagicMock(return_value=False)
        main_page.bring_to_front = AsyncMock()
        main_page.context = MagicMock(pages=[main_page])

        on_track = await engine._ensure_on_track_and_clean_tabs(main_page, current_keyword="#AI")
        assert on_track is False
        # Failed to go back, so it should re-navigate to search page
        engine.navigate_to_keyword_search.assert_called_once_with(main_page, "#AI")
        main_page.bring_to_front.assert_not_called()

    asyncio.run(_run())


def test_ensure_page_focus_emulation_activates_cdp():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        page = MagicMock()
        mock_cdp = MagicMock()
        mock_cdp.send = AsyncMock()
        mock_context = MagicMock()
        mock_context.new_cdp_session = AsyncMock(return_value=mock_cdp)
        page.context = mock_context

        await engine._ensure_page_focus_emulation(page)
        mock_context.new_cdp_session.assert_called_once_with(page)
        mock_cdp.send.assert_called_once_with("Emulation.setFocusEmulationEnabled", {"enabled": True})

        # Second call should be a no-op due to caching
        await engine._ensure_page_focus_emulation(page)
        assert mock_cdp.send.call_count == 1

    asyncio.run(_run())


def test_check_and_handle_retry_clicks_button_and_recovers_articles():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        page = MagicMock()
        page.is_closed = MagicMock(return_value=False)
        # simulate DOM evaluation finding error and retry button
        page.evaluate = AsyncMock(return_value={"hasErrorText": True, "foundBtn": True})
        mock_btn = MagicMock()
        mock_btn.is_visible = AsyncMock(return_value=True)
        mock_btn.click = AsyncMock()
        page.query_selector = AsyncMock(return_value=mock_btn)

        # First query_selector_all returns empty, after click returns articles
        mock_article = MagicMock()
        page.query_selector_all = AsyncMock(side_effect=[
            [mock_article]  # recovered after click
        ])

        with patch("agent.x_automation_engine.safe_human_click", AsyncMock(return_value=True)), \
             patch("asyncio.sleep", AsyncMock()):
            recovered = await engine._check_and_handle_retry(page, current_keyword="test")

        assert recovered is True
        page.query_selector.assert_called_with('[data-laogu-retry-btn="true"]')

    asyncio.run(_run())


def test_check_and_handle_retry_falls_back_to_reload_and_recovers():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        page = MagicMock()
        page.is_closed = MagicMock(return_value=False)
        page.evaluate = AsyncMock(return_value={"hasErrorText": True, "foundBtn": False})
        page.query_selector = AsyncMock(return_value=None)
        page.reload = AsyncMock()

        mock_article = MagicMock()
        page.query_selector_all = AsyncMock(side_effect=[
            [],             # after click attempt (none)
            [mock_article]  # after reload
        ])

        with patch("agent.x_automation_engine.safe_human_click", AsyncMock(return_value=False)), \
             patch("asyncio.sleep", AsyncMock()), \
             patch.object(engine, "_sleep_with_control", AsyncMock()):
            recovered = await engine._check_and_handle_retry(page, current_keyword="test")

        assert recovered is True
        page.reload.assert_called_once()

    asyncio.run(_run())


def test_check_and_handle_retry_falls_back_to_renavigate():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        page = MagicMock()
        page.is_closed = MagicMock(return_value=False)
        page.evaluate = AsyncMock(return_value={"hasErrorText": True, "foundBtn": False})
        page.query_selector = AsyncMock(return_value=None)
        page.reload = AsyncMock()
        engine.navigate_to_keyword_search = AsyncMock()

        mock_article = MagicMock()
        page.query_selector_all = AsyncMock(side_effect=[
            [],             # after click attempt
            [],             # after reload attempt
            [mock_article]  # after re-navigation
        ])

        with patch("asyncio.sleep", AsyncMock()), \
             patch.object(engine, "_sleep_with_control", AsyncMock()):
            recovered = await engine._check_and_handle_retry(page, current_keyword="AI")

        assert recovered is True
        page.reload.assert_called_once()
        engine.navigate_to_keyword_search.assert_called_once_with(page, "AI")

    asyncio.run(_run())


def test_run_single_batch_empty_rounds_triggers_renavigate():
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        config = AutomationConfig.from_mapping({"keyword": "Python", "daily_task_limit": 10})
        page = MagicMock()
        page.is_closed = MagicMock(return_value=False)
        engine._assert_no_challenge = AsyncMock()
        engine._ensure_on_track_and_clean_tabs = AsyncMock(return_value=True)
        engine._execute_follow_burst_protection = AsyncMock(return_value=0)
        engine._check_and_handle_retry = AsyncMock(return_value=False)
        engine.navigate_to_keyword_search = AsyncMock()

        # Simulate empty article queries
        page.query_selector_all = AsyncMock(return_value=[])

        call_count = 0
        async def mock_human_discrete_scroll(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 6:
                raise AutomationStopped("test_completed")

        with patch("agent.x_automation_engine.human_discrete_scroll", mock_human_discrete_scroll), \
             patch("asyncio.sleep", AsyncMock()), \
             pytest.raises(AutomationStopped):
            await engine._run_single_batch(page, config, current_total_exec=0)

        # At round 6, navigate_to_keyword_search must have been triggered
        engine.navigate_to_keyword_search.assert_called_with(page, "Python")

    asyncio.run(_run())


def test_engine_preserves_native_viewport_without_device_emulation():
    """验证自动化引擎连接页面时严格保留原生物理视口，杜绝调用 set_viewport_size 触发 DevTools 模拟黑边与指纹异常."""
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        mock_page = MagicMock()
        mock_page.url = "https://x.com/home"
        mock_page.evaluate = AsyncMock(return_value=960)  # 即使物理窗口偏小
        mock_page.set_viewport_size = AsyncMock()

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        page = await engine._select_or_open_x_page(mock_context)
        assert page is mock_page
        # 确保没有调用 set_viewport_size
        mock_page.set_viewport_size.assert_not_called()

    asyncio.run(_run())


def test_chain_hop_uses_safe_goto_when_candidate_element_detached():
    """验证当列表 DOM 重新渲染导致活动链接游离 (active_link 为 None) 时，能够自动平滑采用 safe_goto 直达目标博主主页."""
    async def _run():
        engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
        engine.my_username = "tester"
        engine._assert_no_challenge = AsyncMock()
        engine._detect_hover_card_with_human_dwell = AsyncMock(return_value=None)
        engine._get_followers_robust = AsyncMock(return_value=(150, 0))
        engine._dismiss_hover_card = AsyncMock()
        engine._interact_on_profile_page_simple = AsyncMock(return_value=(True, 1))
        engine._safe_go_back = AsyncMock(return_value=True)
        engine._safe_goto = AsyncMock(return_value=True)

        mock_cell = MagicMock()
        mock_user_link = MagicMock()
        mock_user_link.get_attribute = AsyncMock(return_value="/detached_friend")
        mock_cell.query_selector = AsyncMock(side_effect=lambda sel: mock_user_link if 'a[href^="/"][role="link"]' in sel else None)
        mock_cell.inner_text = AsyncMock(return_value="Detached Friend")

        mock_page = MagicMock()
        mock_page.url = "https://x.com/seed/following"
        # 探测阶段返回 1 个候选 cell，后续查找 active_link 时返回空列表（模拟 React 卸载）
        query_all_calls = 0
        def mock_query_all(sel):
            nonlocal query_all_calls
            query_all_calls += 1
            if "UserCell" in sel and query_all_calls <= 1:
                return [mock_cell]
            return []

        mock_page.query_selector_all = AsyncMock(side_effect=mock_query_all)
        mock_page.query_selector = AsyncMock(return_value=None)

        config = AutomationConfig.from_mapping({
            "chain_hop_enabled": True,
            "chain_hop_source": "following",
            "max_follower_threshold": 500,
            "max_chain_depth": 1,
            "daily_task_limit": 10,
            "graphql_scout_filter_enabled": False,
        })

        with patch("agent.x_automation_engine.human_move_to_fast", AsyncMock(return_value=True)):
            exec_count, likes, follows, views = await engine._chain_hop_following_exploration(
                mock_page, "seed", config, current_chain_depth=1, current_total_exec=0, remaining_batch_budget=5
            )

        assert follows == 1
        assert exec_count == 1
        # 验证 safe_goto 被作为兜底平滑直达调用
        engine._safe_goto.assert_any_call(
            mock_page, "https://x.com/detached_friend", referer="https://x.com/seed/following", timeout=15000
        )

    asyncio.run(_run())


def test_is_crash_error_detection():
    """验证 _is_crash_error 精准识别各类 Sad Tab 与 OOM 异常（中 / 日 / 英全语言覆盖）"""
    # 英文
    assert XAutomationEngine._is_crash_error(Exception("Page crashed!")) is True
    assert XAutomationEngine._is_crash_error(Exception("Error: Target page, context or browser has been closed")) is True
    assert XAutomationEngine._is_crash_error("Target crashed") is True
    assert XAutomationEngine._is_crash_error("Aw, Snap! Something went wrong") is True
    assert XAutomationEngine._is_crash_error("Execution context was destroyed") is True
    # 日文
    assert XAutomationEngine._is_crash_error("エラー コード: Out of Memory") is True
    assert XAutomationEngine._is_crash_error("エラーコード: Out of Memory") is True
    assert XAutomationEngine._is_crash_error("このウェブページの表示中に問題が発生しました。") is True
    assert XAutomationEngine._is_crash_error("レンダラーがクラッシュしました") is True
    # 中文
    assert XAutomationEngine._is_crash_error("错误代码：内存不足") is True
    assert XAutomationEngine._is_crash_error("糟糕，页面崩溃了！") is True
    assert XAutomationEngine._is_crash_error("页面已崩溃，请重新加载") is True
    # 正常/非崩溃
    assert XAutomationEngine._is_crash_error(None) is False
    assert XAutomationEngine._is_crash_error(Exception("Timeout 30000ms exceeded")) is False


def test_maybe_purge_renderer_memory_cdp_and_eval():
    """验证主动内存泄放调用 CDP HeapProfiler.collectGarbage 与 window.gc()"""
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    mock_cdp = AsyncMock()
    mock_cdp.send = AsyncMock()
    mock_cdp.detach = AsyncMock()

    mock_context = MagicMock()
    mock_context.new_cdp_session = AsyncMock(return_value=mock_cdp)

    mock_page = MagicMock()
    mock_page.context = mock_context
    mock_page.evaluate = AsyncMock()

    # 1. 未达阈值时节流跳过
    engine._scroll_actions_since_last_gc = 3
    engine._last_renderer_gc_time = time.time()
    res = asyncio.run(engine._maybe_purge_renderer_memory(mock_page, force=False))
    assert res is False
    assert mock_cdp.send.call_count == 0

    # 2. force=True 时强制触发全量垃圾回收
    res_forced = asyncio.run(engine._maybe_purge_renderer_memory(mock_page, force=True))
    assert res_forced is True
    mock_cdp.send.assert_called_once_with("HeapProfiler.collectGarbage")
    mock_cdp.detach.assert_called_once()
    mock_page.evaluate.assert_called_once()
    assert engine._scroll_actions_since_last_gc == 0


def test_recover_crashed_page_creates_fresh_page_and_resumes():
    """验证标签页崩溃无损自愈机制：销毁旧页、创建全新标签、重新挂载监听与导航"""
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    engine._is_page_crashed = True

    old_page = MagicMock()
    old_page.is_closed = MagicMock(return_value=False)
    old_page.close = AsyncMock()

    new_page = MagicMock()
    new_page.goto = AsyncMock()
    new_page.on = MagicMock()

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=new_page)

    with patch.object(engine, "_ensure_page_focus_emulation", AsyncMock()) as mock_focus:
        recovered_page = asyncio.run(
            engine._recover_crashed_page(mock_context, old_page=old_page, fallback_url="https://x.com/creator/followers")
        )

    assert recovered_page is new_page
    assert old_page.close.called
    assert engine._is_page_crashed is False
    new_page.goto.assert_called_once()
    assert "https://x.com/creator/followers" in str(new_page.goto.call_args)
    assert mock_focus.called


def test_select_or_open_x_page_skips_and_cleans_crashed_pages():
    """验证 _select_or_open_x_page 自动跳过并关闭 chrome-error 崩溃残余页"""
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    crashed_page = MagicMock()
    crashed_page.url = "chrome-error://chromewebdata/"
    crashed_page.is_closed = MagicMock(return_value=False)
    crashed_page.close = AsyncMock()

    valid_x_page = MagicMock()
    valid_x_page.url = "https://x.com/home"
    valid_x_page.is_closed = MagicMock(return_value=False)
    valid_x_page.on = MagicMock()
    valid_x_page.evaluate = AsyncMock(return_value=1280)

    mock_context = MagicMock()
    mock_context.pages = [crashed_page, valid_x_page]

    chosen = asyncio.run(engine._select_or_open_x_page(mock_context))

    # 崩溃页应被自动关闭，选择有效页面
    assert crashed_page.close.called
    assert chosen is valid_x_page


def test_select_or_open_x_page_waits_for_initial_pages():
    """验证 _select_or_open_x_page 在 Chrome 刚启动 pages 为空时自动轮询等待，并在页面出现后平滑复用"""
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    valid_x_page = MagicMock()
    valid_x_page.url = "https://x.com/home"
    valid_x_page.is_closed = MagicMock(return_value=False)
    valid_x_page.on = MagicMock()
    valid_x_page.evaluate = AsyncMock(return_value=1280)

    # 模拟第一次检查为空，第二次检查时页面注册完毕
    poll_calls = 0
    mock_context = MagicMock()

    def get_pages():
        nonlocal poll_calls
        poll_calls += 1
        if poll_calls >= 2:
            return [valid_x_page]
        return []

    type(mock_context).pages = property(lambda self: get_pages())
    mock_context.new_page = AsyncMock()

    chosen = asyncio.run(engine._select_or_open_x_page(mock_context))

    assert chosen is valid_x_page
    assert poll_calls >= 2
    # 确认直接复用注册出来的页面，未触发多余的 new_page
    mock_context.new_page.assert_not_called()


def test_select_or_open_x_page_retries_new_page_on_target_createtarget_error():
    """验证 _select_or_open_x_page 在遇到 Target.createTarget 报错时自动退避重试并成功获取页面"""
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    new_page = MagicMock()
    new_page.url = "about:blank"
    new_page.is_closed = MagicMock(return_value=False)
    new_page.goto = AsyncMock()
    new_page.on = MagicMock()
    new_page.evaluate = AsyncMock(return_value=1280)

    attempt_count = 0
    async def mock_new_page():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            raise Exception("Protocol error (Target.createTarget): Failed to open a new tab")
        return new_page

    mock_context = MagicMock()
    mock_context.pages = []
    mock_context.new_page = AsyncMock(side_effect=mock_new_page)

    with patch("asyncio.sleep", AsyncMock(return_value=None)):
        chosen = asyncio.run(engine._select_or_open_x_page(mock_context))

    assert chosen is new_page
    assert attempt_count == 2
    new_page.goto.assert_called_once()
    assert "https://x.com/home" in str(new_page.goto.call_args)


def test_recover_crashed_page_retries_new_page_on_target_error():
    """验证 _recover_crashed_page 遇到 Target.createTarget 冲突时重试新建页面"""
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    engine._is_page_crashed = True

    old_page = MagicMock()
    old_page.is_closed = MagicMock(return_value=False)
    old_page.close = AsyncMock()

    new_page = MagicMock()
    new_page.goto = AsyncMock()
    new_page.on = MagicMock()

    attempts = 0
    async def mock_new_page():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise Exception("Protocol error (Target.createTarget): Failed to open a new tab")
        return new_page

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(side_effect=mock_new_page)

    with patch("asyncio.sleep", AsyncMock(return_value=None)), \
         patch.object(engine, "_ensure_page_focus_emulation", AsyncMock()):
        recovered = asyncio.run(
            engine._recover_crashed_page(mock_context, old_page=old_page, fallback_url="https://x.com/home")
        )

    assert recovered is new_page
    assert attempts == 2
    assert engine._is_page_crashed is False


def test_detect_network_proxy_error_detects_chrome_error():
    """验证 _detect_network_proxy_error 能准确探测 chrome-error 及网络离线关键词"""
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    mock_page = MagicMock()
    mock_page.url = "chrome-error://chromewebdata/"
    mock_page.is_closed = MagicMock(return_value=False)
    mock_page.evaluate = AsyncMock(return_value="ERR_PROXY_CONNECTION_FAILED")

    res = asyncio.run(engine._detect_network_proxy_error(mock_page))
    assert res == "ERR_PROXY_CONNECTION_FAILED"


def test_select_or_open_x_page_preserves_single_chrome_error_tab():
    """验证当代理断网导致首个标签页处于 chrome-error 时，严格保留该标签页，绝不调用 close 导致浏览器退出"""
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    error_page = MagicMock()
    error_page.url = "chrome-error://chromewebdata/"
    error_page.is_closed = MagicMock(return_value=False)
    error_page.close = AsyncMock()
    # 模拟由于断网导致的 goto 异常
    error_page.goto = AsyncMock(side_effect=Exception("net::ERR_PROXY_CONNECTION_FAILED at https://x.com/home"))
    error_page.on = MagicMock()
    error_page.evaluate = AsyncMock(return_value=1280)

    mock_context = MagicMock()
    mock_context.pages = [error_page]
    mock_context.new_page = AsyncMock()

    with patch("asyncio.sleep", AsyncMock(return_value=None)):
        chosen = asyncio.run(engine._select_or_open_x_page(mock_context))

    # 严禁关闭这唯一的标签页
    error_page.close.assert_not_called()
    assert chosen is error_page


def test_wait_for_network_recovery_resumes_when_network_restored():
    """验证 _wait_for_network_recovery 在检测到断网时轮询待机，一旦网络恢复（goto成功）自动无损唤醒"""
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    engine._active_config = AutomationConfig.from_mapping({"account_tag": "测试11"})

    mock_page = MagicMock()
    mock_page.is_closed = MagicMock(return_value=False)
    mock_page.goto = AsyncMock(return_value=True)

    detect_calls = 0
    async def mock_detect(p):
        nonlocal detect_calls
        detect_calls += 1
        if detect_calls == 1:
            return "ERR_PROXY_CONNECTION_FAILED"
        return ""  # 第二次探测网络已恢复

    engine._detect_network_proxy_error = AsyncMock(side_effect=mock_detect)
    engine._dispatch_network_alert_telegram = MagicMock()

    with patch("asyncio.sleep", AsyncMock(return_value=None)):
        res = asyncio.run(engine._wait_for_network_recovery(mock_page, initial_err="ERR_PROXY_CONNECTION_FAILED"))

    assert res is True
    assert detect_calls >= 2
    engine._dispatch_network_alert_telegram.assert_called_once()




def test_automation_config_periodic_search_refresh():
    # 默认值：开启，15 分钟
    cfg_default = AutomationConfig.from_mapping({})
    assert cfg_default.periodic_search_refresh_enabled is True
    assert cfg_default.search_refresh_interval_minutes == 15

    # 显式开启并设置间隔
    cfg_on = AutomationConfig.from_mapping({
        "periodic_search_refresh_enabled": True,
        "search_refresh_interval_minutes": 10,
    })
    assert cfg_on.periodic_search_refresh_enabled is True
    assert cfg_on.search_refresh_interval_minutes == 10

    # 边界钳制：小于 1 钳制为 1，大于 60 钳制为 60
    cfg_low = AutomationConfig.from_mapping({
        "periodic_search_refresh_enabled": "true",
        "search_refresh_interval_minutes": 0,
    })
    assert cfg_low.periodic_search_refresh_enabled is True
    assert cfg_low.search_refresh_interval_minutes == 1

    cfg_high = AutomationConfig.from_mapping({
        "periodic_search_refresh_enabled": False,
        "search_refresh_interval_minutes": 100,
    })
    assert cfg_high.periodic_search_refresh_enabled is False
    assert cfg_high.search_refresh_interval_minutes == 60


def test_refresh_latest_search_feed_clicks_floating_pill():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    mock_pill = MagicMock()
    mock_pill.is_visible = AsyncMock(return_value=True)
    mock_pill.click = AsyncMock()

    mock_page = MagicMock()
    mock_page.query_selector = AsyncMock(return_value=mock_pill)

    success = asyncio.run(engine.refresh_latest_search_feed(mock_page, "tokyo"))
    assert success is True
    mock_pill.click.assert_called_once()
    assert engine._last_search_refresh_time > 0


def test_refresh_latest_search_feed_fallback_to_live_url():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    mock_page = MagicMock()
    # 模拟未出现浮动胶囊
    mock_page.query_selector = AsyncMock(return_value=None)
    mock_page.evaluate = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()

    with patch.object(engine, "_check_and_handle_retry", AsyncMock()):
        success = asyncio.run(engine.refresh_latest_search_feed(mock_page, "tokyo travel"))

    assert success is True
    mock_page.evaluate.assert_called_once_with("window.scrollTo(0, 0)")
    mock_page.goto.assert_called_once()
    call_url = mock_page.goto.call_args[0][0]
    assert "https://x.com/search?q=" in call_url
    assert "&f=live" in call_url
    assert engine._last_search_refresh_time > 0


def test_cdp_timezone_override_config_mapping():
    # 1. 默认状态：开启 (True)，auto
    cfg_default = AutomationConfig.from_mapping({})
    assert cfg_default.cdp_timezone_override_enabled is True
    assert cfg_default.cdp_override_timezone == "auto"
    assert cfg_default.cdp_override_locale == ""

    # 2. 开启并指定时区
    cfg_custom = AutomationConfig.from_mapping({
        "cdp_timezone_override_enabled": True,
        "cdp_override_timezone": "Asia/Tokyo",
        "cdp_override_locale": "ja-JP"
    })
    assert cfg_custom.cdp_timezone_override_enabled is True
    assert cfg_custom.cdp_override_timezone == "Asia/Tokyo"
    assert cfg_custom.cdp_override_locale == "ja-JP"

    # 3. 各种布尔格式兼容
    assert AutomationConfig.from_mapping({"cdp_timezone_override_enabled": "true"}).cdp_timezone_override_enabled is True
    assert AutomationConfig.from_mapping({"cdp_timezone_override_enabled": "1"}).cdp_timezone_override_enabled is True
    assert AutomationConfig.from_mapping({"cdp_timezone_override_enabled": "false"}).cdp_timezone_override_enabled is False


def test_cdp_timezone_override_engine_execution():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_cdp = MagicMock()
    mock_cdp.send = AsyncMock()
    mock_context.new_cdp_session = AsyncMock(return_value=mock_cdp)
    mock_page.context = mock_context

    # Case 1: 关闭状态下，绝不调用 CDP session
    cfg_off = AutomationConfig.from_mapping({"cdp_timezone_override_enabled": False})
    asyncio.run(engine._ensure_cdp_locale_and_timezone_override(mock_page, cfg_off))
    mock_context.new_cdp_session.assert_not_called()

    # Case 2: 开启状态且显式指定日本东京
    cfg_on = AutomationConfig.from_mapping({
        "cdp_timezone_override_enabled": True,
        "cdp_override_timezone": "Asia/Tokyo"
    })
    asyncio.run(engine._ensure_cdp_locale_and_timezone_override(mock_page, cfg_on))
    mock_context.new_cdp_session.assert_called_once_with(mock_page)
    # 验证 Emulation.setTimezoneOverride 与 Emulation.setLocaleOverride 以及 Emulation.setGeolocationOverride 调用
    calls = mock_cdp.send.call_args_list
    method_args = {call[0][0]: call[0][1] for call in calls}
    assert "Emulation.setTimezoneOverride" in method_args
    assert method_args["Emulation.setTimezoneOverride"]["timezoneId"] == "Asia/Tokyo"
    assert "Emulation.setLocaleOverride" in method_args
    assert method_args["Emulation.setLocaleOverride"]["locale"] == "ja-JP"
    assert "Emulation.setGeolocationOverride" in method_args
    assert method_args["Emulation.setGeolocationOverride"]["latitude"] == 35.6762
    assert method_args["Emulation.setGeolocationOverride"]["longitude"] == 139.6503
    assert getattr(mock_page, "_cdp_timezone_attached") is True

    # Case 3: 二次调用幂等性（已有 _cdp_timezone_attached 时跳过）
    mock_context.new_cdp_session.reset_mock()
    asyncio.run(engine._ensure_cdp_locale_and_timezone_override(mock_page, cfg_on))
    mock_context.new_cdp_session.assert_not_called()

    # Case 4: 自动探测模式 (auto) 且探测返回美国时区
    mock_page2 = MagicMock()
    mock_context2 = MagicMock()
    mock_cdp2 = MagicMock()
    mock_cdp2.send = AsyncMock()
    mock_context2.new_cdp_session = AsyncMock(return_value=mock_cdp2)
    mock_page2.context = mock_context2
    mock_page2.evaluate = AsyncMock(return_value={"timezone": "America/New_York", "country_code": "US"})

    cfg_auto = AutomationConfig.from_mapping({
        "cdp_timezone_override_enabled": True,
        "cdp_override_timezone": "auto"
    })
    asyncio.run(engine._ensure_cdp_locale_and_timezone_override(mock_page2, cfg_auto))
    mock_context2.new_cdp_session.assert_called_once_with(mock_page2)
    calls2 = mock_cdp2.send.call_args_list
    method_args2 = {call[0][0]: call[0][1] for call in calls2}
    assert method_args2["Emulation.setTimezoneOverride"]["timezoneId"] == "America/New_York"
    assert method_args2["Emulation.setLocaleOverride"]["locale"] == "en-US"
    assert "Emulation.setGeolocationOverride" in method_args2
    assert method_args2["Emulation.setGeolocationOverride"]["latitude"] == 40.7128

    # Case 5: 验证引擎全局缓存：后续新页面直接复用探测结果，不再二次发起网络请求
    mock_page3 = MagicMock()
    mock_context3 = MagicMock()
    mock_cdp3 = MagicMock()
    mock_cdp3.send = AsyncMock()
    mock_context3.new_cdp_session = AsyncMock(return_value=mock_cdp3)
    mock_page3.context = mock_context3
    mock_page3.evaluate = AsyncMock()
    asyncio.run(engine._ensure_cdp_locale_and_timezone_override(mock_page3, cfg_auto))
    mock_page3.evaluate.assert_not_called()
    calls3 = mock_cdp3.send.call_args_list
    method_args3 = {call[0][0]: call[0][1] for call in calls3}
    assert method_args3["Emulation.setTimezoneOverride"]["timezoneId"] == "America/New_York"
    assert method_args3["Emulation.setLocaleOverride"]["locale"] == "en-US"

    # Case 6: 当用户自定义关闭 GPS 经纬度覆盖时，不调用 Emulation.setGeolocationOverride
    mock_page4 = MagicMock()
    mock_context4 = MagicMock()
    mock_cdp4 = MagicMock()
    mock_cdp4.send = AsyncMock()
    mock_context4.new_cdp_session = AsyncMock(return_value=mock_cdp4)
    mock_page4.context = mock_context4
    cfg_no_geo = AutomationConfig.from_mapping({
        "cdp_timezone_override_enabled": True,
        "cdp_override_timezone": "Europe/London",
        "cdp_geolocation_override_enabled": False
    })
    asyncio.run(engine._ensure_cdp_locale_and_timezone_override(mock_page4, cfg_no_geo))
    calls4 = mock_cdp4.send.call_args_list
    method_args4 = {call[0][0]: call[0][1] for call in calls4}
    assert "Emulation.setTimezoneOverride" in method_args4
    assert method_args4["Emulation.setTimezoneOverride"]["timezoneId"] == "Europe/London"
    assert "Emulation.setGeolocationOverride" not in method_args4


def test_anti_risk_japanese_patterns_and_graphql_hardening():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    # 1. 验证日文与英文原生限流正则匹配
    assert engine._find_rate_limit("レート制限により操作が制限されています") is not None
    assert engine._find_rate_limit("試行回数が多すぎます。しばらく待ってからやり直してください") is not None
    assert engine._find_rate_limit("リクエスト回数が多すぎます") is not None
    assert engine._find_rate_limit("temporarily restricted account") is not None
    assert engine._find_rate_limit("This action is an action blocked event") is not None

    # 2. 验证 GraphQL 致命风控错误码捕获 (226, 399, 185)
    class FakeResponse:
        def __init__(self, code, msg):
            self.url = "https://x.com/i/api/graphql/CreateTweet"
            self.status = 200
            self.headers = {"content-type": "application/json"}
            self._data = {"errors": [{"code": code, "message": msg}]}

        async def json(self):
            return self._data

    # 验证 226 (自动化判别)
    engine._risk_reason = ""
    asyncio.run(engine._handle_response_interception(FakeResponse(226, "This request looks like it might be automated")))
    assert engine._risk_reason == "GRAPHQL_ERROR_226"

    # 验证 399 (人机挑战)
    engine._risk_reason = ""
    asyncio.run(engine._handle_response_interception(FakeResponse(399, "Challenge required")))
    assert engine._risk_reason == "GRAPHQL_ERROR_399"

    # 验证 185 (单日发帖上限)
    engine._risk_reason = ""
    asyncio.run(engine._handle_response_interception(FakeResponse(185, "User is over daily status update limit")))
    assert engine._risk_reason == "LIMIT_EXCEEDED_185"

    # 3. 验证用户在面板上关闭风控熔断时 (graphql_risk_pause_enabled=False)：
    #    不暂停任务（_risk_reason 保持为空），但依旧触发 Telegram 告警分发
    engine._risk_reason = ""
    engine._active_config = AutomationConfig.from_mapping({"graphql_risk_pause_enabled": False, "account_tag": "测试窗口-03"})
    engine.my_username = "test_trader"

    dispatched_alerts = []
    engine._dispatch_risk_alert_telegram = lambda window_name, handle, err_code, err_msg, auto_pause: dispatched_alerts.append(
        (window_name, handle, err_code, auto_pause)
    )

    asyncio.run(engine._handle_response_interception(FakeResponse(226, "This request looks like it might be automated")))
    # 验证未暂停
    assert engine._risk_reason == ""
    # 验证第一时间触发了包含窗口名称和账号ID的 Telegram 报警，且标注 auto_pause 为 False
    assert len(dispatched_alerts) == 1
    assert dispatched_alerts[0] == ("测试窗口-03", "test_trader", 226, False)


def test_automation_config_block_video_streams():
    # 默认开启
    cfg_default = AutomationConfig.from_mapping({})
    assert cfg_default.block_video_streams is True

    # 自定义关闭
    cfg_disabled = AutomationConfig.from_mapping({"block_video_streams": False})
    assert cfg_disabled.block_video_streams is False

    cfg_disabled_str = AutomationConfig.from_mapping({"block_video_streams": "false"})
    assert cfg_disabled_str.block_video_streams is False

    cfg_disabled_zero = AutomationConfig.from_mapping({"block_video_streams": 0})
    assert cfg_disabled_zero.block_video_streams is False

    # 自定义开启
    cfg_enabled = AutomationConfig.from_mapping({"block_video_streams": True})
    assert cfg_enabled.block_video_streams is True

    cfg_enabled_str = AutomationConfig.from_mapping({"block_video_streams": "true"})
    assert cfg_enabled_str.block_video_streams is True


def test_ensure_video_stream_blocking_execution():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    # 1. 验证当 block_video_streams 为 True 时，调用 CDP 与 route
    mock_cdp = AsyncMock()
    mock_context = MagicMock()
    mock_context.new_cdp_session = AsyncMock(return_value=mock_cdp)

    mock_page = MagicMock()
    mock_page.context = mock_context
    mock_page.route = AsyncMock()

    cfg_on = AutomationConfig.from_mapping({"block_video_streams": True})
    asyncio.run(engine._ensure_video_stream_blocking(mock_page, cfg_on))

    # 验证 CDP 调用
    mock_cdp.send.assert_any_call("Network.enable")
    mock_cdp.send.assert_any_call(
        "Network.setBlockedURLs",
        {"urls": ["*://video.twimg.com/*", "*.mp4*", "*.m3u8*", "*.ts*"]},
    )
    # 验证 route 调用
    assert mock_page.route.call_count == 1
    assert getattr(mock_page, "_video_blocking_applied", False) is True

    # 2. 再次调用时幂等保护（不重复绑定）
    mock_cdp.reset_mock()
    mock_page.route.reset_mock()
    asyncio.run(engine._ensure_video_stream_blocking(mock_page, cfg_on))
    assert mock_cdp.send.call_count == 0
    assert mock_page.route.call_count == 0

    # 3. 验证当 block_video_streams 为 False 时，不执行任何拦截
    mock_page_off = MagicMock()
    mock_cdp_off = AsyncMock()
    mock_ctx_off = MagicMock()
    mock_ctx_off.new_cdp_session = AsyncMock(return_value=mock_cdp_off)
    mock_page_off.context = mock_ctx_off
    mock_page_off.route = AsyncMock()

    cfg_off = AutomationConfig.from_mapping({"block_video_streams": False})
    asyncio.run(engine._ensure_video_stream_blocking(mock_page_off, cfg_off))
    assert mock_cdp_off.send.call_count == 0
    assert mock_page_off.route.call_count == 0
    assert getattr(mock_page_off, "_video_blocking_applied", None) is not True







