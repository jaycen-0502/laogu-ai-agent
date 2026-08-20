from __future__ import annotations

from agent.profile_worker import ProfileWorker, ProfileWorkerError, ProfileWorkerManager, WorkerState


class FakeBrowserManager:
    def __init__(self):
        self.calls = []
        self.statuses = {}

    def start_profile(self, profile_id):
        self.calls.append(("start", profile_id))
        self.statuses[profile_id] = "RUNNING"

    def stop_profile(self, profile_id):
        self.calls.append(("stop", profile_id))
        self.statuses[profile_id] = "STOPPED"

    def check_status(self, profile_id):
        self.calls.append(("status", profile_id))
        return {"status": self.statuses.get(profile_id, "STOPPED")}

    def probe_credential_capability(self, profile_id):
        self.calls.append(("probe", profile_id))
        return {"probe_version": "1", "browser_reachable": True, "cookie_read_supported": False, "cookie_write_supported": False, "credential_snapshot_allowed": False, "evidence": "NOT_ADVERTISED", "cookie_value": "must-not-leak"}


def test_profile_workers_are_isolated_and_lifecycle_calls_are_idempotent():
    browser = FakeBrowserManager()
    manager = ProfileWorkerManager(browser)
    first = manager.dispatch({"command_type": "START_PROFILE", "profile_id": "p1"})
    second = manager.dispatch({"command_type": "START_PROFILE", "profile_id": "p1"})
    other = manager.dispatch({"command_type": "START_PROFILE", "profile_id": "p2"})
    assert first["worker_state"] == "IDLE"
    assert second["worker_state"] == "IDLE"
    assert other["profile_id"] == "p2"
    assert browser.calls == [("start", "p1"), ("start", "p2")]
    assert manager.dispatch({"command_type": "STOP_PROFILE", "profile_id": "p1"})["worker_state"] == "STOPPED"
    assert manager.worker("p2").state is WorkerState.IDLE


def test_profile_worker_refresh_and_unsupported_commands_are_safe():
    browser = FakeBrowserManager()
    worker = ProfileWorker("p1", browser)
    browser.statuses["p1"] = "RUNNING"
    assert worker.refresh()["worker_state"] == "IDLE"
    try:
        ProfileWorkerManager(browser).dispatch({"command_type": "UPDATE_PARAMS", "profile_id": "p1"})
        assert False, "unsupported command was accepted"
    except ProfileWorkerError as exc:
        assert "not enabled" in str(exc)


def test_credential_probe_returns_metadata_only():
    result = ProfileWorkerManager(FakeBrowserManager()).dispatch({"command_type": "PROBE_CREDENTIAL_CAPABILITY", "profile_id": "p1"})
    assert result["browser_reachable"] is True
    assert result["credential_snapshot_allowed"] is False
    assert "cookie_value" not in result
