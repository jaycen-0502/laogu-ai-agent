from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading

import pytest

import desktop.controller as controller_module
from agent.runtime_config import RuntimeConfig
from agent.x_automation_engine import AutomationConfig
from desktop.controller import DesktopController, _next_schedule_at


class FakeApi:
    pass


class FakeBrowser:
    def __init__(self):
        self.started: list[str] = []

    def start_profile(self, profile_id):
        self.started.append(str(profile_id))
        return {"cdpUrl": "http://127.0.0.1:9222"}

    def stop_profile(self, profile_id):
        return {"ok": True}


class FakeDiscovery:
    def scan(self, profile_ids=None):
        return []


class FakeRegistry:
    def list(self):
        return []


class FakeAgentService:
    def start(self):
        pass

    def stop(self):
        pass

    def status(self):
        return {"server": "ONLINE", "agent": "ONLINE"}

    def has_capability(self, capability):
        return capability in {"automation.run", "local.browser.control"}


def make_controller(tmp_path: Path) -> tuple[DesktopController, FakeBrowser]:
    browser = FakeBrowser()
    controller = DesktopController(
        api=FakeApi(),
        browser_manager=browser,
        discovery=FakeDiscovery(),
        registry=FakeRegistry(),
        agent_service=FakeAgentService(),
        runtime_config=RuntimeConfig(tmp_path / "runtime.json"),
    )
    return controller, browser


def future_at(minutes: int = 10) -> str:
    china = timezone(timedelta(hours=8))
    return (datetime.now(china) + timedelta(minutes=minutes)).isoformat(timespec="minutes")


def test_old_config_defaults_to_smart_mode():
    config = AutomationConfig.from_mapping({"keyword": "example"})
    assert config.schedule_mode == "smart"
    assert AutomationConfig.from_mapping({"schedule_mode": "immediate"}).schedule_mode == "immediate"
    assert AutomationConfig.from_mapping({"schedule_mode": "scheduled"}).schedule_mode == "scheduled"
    assert DesktopController._normalize_profile_task_config({})["schedule_mode"] == "smart"


def test_schedule_calculation_supports_once_and_daily():
    china = timezone(timedelta(hours=8))
    now = datetime(2026, 8, 28, 16, 30, tzinfo=china)
    once = _next_schedule_at({
        "schedule_type": "once",
        "scheduled_at": "2026-08-28T17:00:00+08:00",
        "schedule_timezone": "Asia/Shanghai",
    }, now=now)
    assert once.hour == 17 and once.day == 28

    daily = _next_schedule_at({
        "schedule_type": "daily",
        "scheduled_time": "16:00",
        "schedule_timezone": "Asia/Shanghai",
    }, now=now)
    assert daily.hour == 16 and daily.day == 29


def test_scheduled_task_does_not_start_browser_before_due(tmp_path):
    controller, browser = make_controller(tmp_path)
    result = controller.start_automation_task("profile-a", {
        "schedule_mode": "scheduled",
        "schedule_type": "once",
        "scheduled_at": future_at(),
        "schedule_timezone": "Asia/Shanghai",
    })
    try:
        assert result["status"] == "SCHEDULED"
        assert browser.started == []
        assert controller.runtime_config.snapshot("profile-a")["active"]["schedule_status"] == "WAITING_SCHEDULE"
    finally:
        controller.stop_agent_service()


def test_due_schedule_starts_without_waiting_in_timer(monkeypatch, tmp_path):
    controller, _browser = make_controller(tmp_path)
    captured: list[dict] = []
    monkeypatch.setattr(
        controller,
        "_start_automation_now",
        lambda profile_id, config, saved=None: captured.append(dict(config)) or {"status": "SUCCESS", "run_id": "run-1"},
    )
    controller.start_automation_task("profile-a", {
        "schedule_mode": "scheduled",
        "schedule_type": "once",
        "scheduled_at": future_at(),
        "schedule_timezone": "Asia/Shanghai",
    })
    token = controller._scheduled_tokens["profile-a"]
    controller._scheduled_task_due("profile-a", token, controller.runtime_config.snapshot("profile-a")["active"])
    try:
        assert captured[0]["schedule_mode"] == "scheduled"
        assert controller.runtime_config.snapshot("profile-a")["active"]["schedule_status"] == "TRIGGERED"
    finally:
        controller.stop_agent_service()


def test_busy_profile_delays_schedule_and_releases_other_profiles(monkeypatch, tmp_path):
    controller, _browser = make_controller(tmp_path)
    controller.start_automation_task("profile-a", {
        "schedule_mode": "scheduled",
        "schedule_type": "once",
        "scheduled_at": future_at(),
        "schedule_timezone": "Asia/Shanghai",
    })
    token = controller._scheduled_tokens["profile-a"]
    with controller_module._RUNNING_LOCK:
        controller_module._RUNNING_PROFILES.add("profile-a")
    try:
        controller._scheduled_task_due("profile-a", token, controller.runtime_config.snapshot("profile-a")["active"])
        assert "profile-a" in controller._scheduled_timers
        assert controller.runtime_config.snapshot("profile-a")["active"]["schedule_status"] == "DELAYED_PROFILE_BUSY"
    finally:
        with controller_module._RUNNING_LOCK:
            controller_module._RUNNING_PROFILES.discard("profile-a")
        controller.stop_agent_service()


def test_once_schedule_rejects_past_time(tmp_path):
    controller, _browser = make_controller(tmp_path)
    china = timezone(timedelta(hours=8))
    past = (datetime.now(china) - timedelta(minutes=1)).isoformat(timespec="minutes")
    try:
        with pytest.raises(ValueError, match="晚于当前时间"):
            controller.start_automation_task("profile-a", {
                "schedule_mode": "scheduled",
                "schedule_type": "once",
                "scheduled_at": past,
                "schedule_timezone": "Asia/Shanghai",
            })
    finally:
        controller.stop_agent_service()


def test_future_once_schedule_is_restored_after_controller_restart(tmp_path):
    runtime = RuntimeConfig(tmp_path / "runtime.json")
    runtime.update("profile-restored", {
        "schedule_mode": "scheduled",
        "schedule_type": "once",
        "scheduled_at": future_at(),
        "schedule_timezone": "Asia/Shanghai",
        "schedule_status": "WAITING_SCHEDULE",
    })
    controller, _browser = make_controller(tmp_path)
    try:
        assert "profile-restored" in controller._scheduled_timers
        assert controller.runtime_config.snapshot("profile-restored")["active"]["schedule_status"] == "WAITING_SCHEDULE"
    finally:
        controller.stop_agent_service()


def test_daily_schedule_rearms_after_trigger(monkeypatch, tmp_path):
    controller, _browser = make_controller(tmp_path)
    monkeypatch.setattr(
        controller,
        "_start_automation_now",
        lambda profile_id, config, saved=None: {"status": "SUCCESS", "run_id": "run-daily"},
    )
    china = timezone(timedelta(hours=8))
    daily_time = (datetime.now(china) + timedelta(minutes=5)).strftime("%H:%M")
    controller.start_automation_task("profile-daily", {
        "schedule_mode": "scheduled",
        "schedule_type": "daily",
        "scheduled_time": daily_time,
        "schedule_timezone": "Asia/Shanghai",
    })
    old_token = controller._scheduled_tokens["profile-daily"]
    controller._scheduled_task_due(
        "profile-daily",
        old_token,
        controller.runtime_config.snapshot("profile-daily")["active"],
    )
    try:
        assert controller._scheduled_tokens["profile-daily"] != old_token
        active = controller.runtime_config.snapshot("profile-daily")["active"]
        assert active["schedule_status"] == "WAITING_SCHEDULE"
        assert active["schedule_next_run"]
    finally:
        controller.stop_agent_service()


def test_real_timer_triggers_scheduled_task(monkeypatch, tmp_path):
    controller, _browser = make_controller(tmp_path)
    triggered = threading.Event()
    monkeypatch.setattr(
        controller,
        "_start_automation_now",
        lambda profile_id, config, saved=None: triggered.set() or {"status": "SUCCESS", "run_id": "run-timer"},
    )
    china = timezone(timedelta(hours=8))
    config = {
        "schedule_mode": "scheduled",
        "schedule_type": "once",
        "scheduled_at": future_at(),
        "schedule_timezone": "Asia/Shanghai",
    }
    controller._arm_scheduled_timer(
        "profile-timer",
        config,
        datetime.now(china) + timedelta(milliseconds=100),
        state="WAITING_SCHEDULE",
    )
    try:
        assert triggered.wait(timeout=2)
    finally:
        controller.stop_agent_service()


def test_cancelled_daily_schedule_is_not_restored(tmp_path):
    controller, _browser = make_controller(tmp_path)
    china = timezone(timedelta(hours=8))
    controller.start_automation_task("profile-cancelled", {
        "schedule_mode": "scheduled",
        "schedule_type": "daily",
        "scheduled_time": (datetime.now(china) + timedelta(minutes=5)).strftime("%H:%M"),
        "schedule_timezone": "Asia/Shanghai",
    })
    controller.stop_profile("profile-cancelled")
    controller.stop_agent_service()

    restored, _browser = make_controller(tmp_path)
    try:
        assert "profile-cancelled" not in restored._scheduled_timers
        active = restored.runtime_config.snapshot("profile-cancelled")["active"]
        assert active["schedule_status"] == "CANCELLED_BY_USER"
    finally:
        restored.stop_agent_service()
