from pathlib import Path
import tempfile

from agent.automation_statistics import AutomationStatisticsStore


def test_automation_statistics_are_idempotent_scoped_and_persistent():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "agent_state.db"
        store = AutomationStatisticsStore(path)
        common = {
            "started_at": "2026-08-22T10:00:00+08:00",
            "result": {"status": "SUCCESS", "processed_count": 8, "likes": 3, "follows": 2, "comments": 1, "views": 12},
        }
        store.record_result(run_id="run-1", profile_id="profile-a", x_account_id="x-a", **common)
        store.record_result(run_id="run-1", profile_id="profile-a", x_account_id="x-a", **common)
        store.record_result(run_id="run-2", profile_id="profile-b", x_account_id="x-b", **common)
        summary = store.summary()
        assert summary["automation_runs"] == 2
        assert summary["likes"] == 6
        assert summary["scanned_posts"] == 24
        assert summary["by_account"]["profile-a"]["automation_runs"] == 1
        store.mark_uploaded("run-1")
        assert [item["run_id"] for item in AutomationStatisticsStore(path).pending()] == ["run-2"]
