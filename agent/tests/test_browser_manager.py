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


def test_stop_profile_returns_success_on_404():
    from agent.laogu_api import LaoguApiError

    class ApiWith404:
        def stop_profile(self, profile_id):
            raise LaoguApiError("Laogu API returned HTTP 404: {'error': 'profile not found', 'ok': False}", status_code=404)

    manager = BrowserManager(ApiWith404())
    res = manager.stop_profile("missing-profile")
    assert res["ok"] is True
    assert res["stopped"] is True
    assert res["profileId"] == "missing-profile"
    assert res["note"] == "already_stopped_or_not_found"


def test_stop_profile_raises_on_non_404_error():
    import pytest
    from agent.laogu_api import LaoguApiError

    class ApiWith500:
        def stop_profile(self, profile_id):
            raise LaoguApiError("Internal Server Error", status_code=500)

    manager = BrowserManager(ApiWith500())
    with pytest.raises(BrowserManagerError, match="Internal Server Error"):
        manager.stop_profile("error-profile")


def test_delete_profile_returns_success_on_404():
    from agent.laogu_api import LaoguApiError

    class ApiWith404:
        def delete_profile(self, profile_id):
            raise LaoguApiError("profile not found", status_code=404)

    manager = BrowserManager(ApiWith404())
    res = manager.delete_profile("missing-profile")
    assert res["ok"] is True
    assert res["deleted"] is True
    assert res["profileId"] == "missing-profile"

