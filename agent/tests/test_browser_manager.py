from __future__ import annotations

import agent.browser_manager as module
from agent.browser_manager import BrowserManager, BrowserManagerError


class FakeApi:
    def __init__(self, starts, statuses):
        self.starts = list(starts)
        self.statuses = list(statuses)

    def start_profile(self, profile_id, timeout_seconds):
        value = self.starts.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def profile_status(self, profile_id):
        value = self.statuses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_start_profile_ready_polls_until_cdp_is_available(monkeypatch):
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    manager = BrowserManager(FakeApi(
        [{"ok": True}],
        [{"status": "STARTING"}, {"status": "RUNNING", "cdpUrl": "http://127.0.0.1:9222"}],
    ))
    states: list[str] = []
    result = manager.start_profile_ready("profile-a", timeout_seconds=1, retries=0, progress=states.append)
    assert result["cdpUrl"].endswith(":9222")
    assert "PROFILE_READY source=status_poll" in states


def test_start_profile_ready_retries_failed_profile_without_blocking_next(monkeypatch):
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    manager = BrowserManager(FakeApi(
        [BrowserManagerError("first failure"), {"cdpUrl": "http://127.0.0.1:9333"}],
        [],
    ))
    states: list[str] = []
    result = manager.start_profile_ready("profile-b", timeout_seconds=1, retries=1, progress=states.append)
    assert result["cdpUrl"].endswith(":9333")
    assert any(item.startswith("PROFILE_START_FAILED") for item in states)


def test_start_profile_ready_accepts_nested_debug_port(monkeypatch):
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    manager = BrowserManager(FakeApi(
        [{"ok": True}],
        [{"status": "RUNNING", "data": {"cdpPort": 9444}}],
    ))
    result = manager.start_profile_ready("profile-port", timeout_seconds=1, retries=0)
    assert result["data"]["cdpPort"] == 9444
