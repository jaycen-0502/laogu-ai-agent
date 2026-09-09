# -*- coding: utf-8 -*-
import asyncio
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
    wheels_y = [dy for _, dy in page.mouse.wheels]
    assert sum(wheels_y) == 600
    assert len(wheels_y) >= 2
    assert wheels_y[0] > wheels_y[-1]


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

    cfg = AutomationConfig.from_mapping({"max_follower_threshold": 500, "max_chain_depth": 1})

    e, l, f, v = asyncio.run(engine._chain_hop_following_exploration(
        page, "seed_user", cfg, current_chain_depth=1, current_total_exec=0, remaining_batch_budget=5
    ))

    # cand_fail click failed, so cand_success was attempted and successfully interacted with!
    assert "interacted_cand_success" in clicked_handles
    assert f == 1





