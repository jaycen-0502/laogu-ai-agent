from pathlib import Path
import tempfile

from agent.automation_statistics import AutomationStatisticsStore


def test_automation_statistics_are_idempotent_scoped_and_persistent():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "agent_state.db"
        store = AutomationStatisticsStore(path)
        common = {
            "started_at": "2026-08-22T10:00:00+08:00",
            "result": {
                "status": "SUCCESS",
                "processed_count": 8,
                "likes": 3,
                "follows": 2,
                "comments": 1,
                "views": 12,
            },
        }
        store.record_result(run_id="run-1", profile_id="profile-a", x_account_id="x-a", **common)
        store.record_result(run_id="run-1", profile_id="profile-a", x_account_id="x-a", **common)
        store.record_result(run_id="run-2", profile_id="profile-b", x_account_id="x-b", **common)

        summary = store.summary()
        assert summary["automation_runs"] == 2
        assert summary["likes"] == 6
        assert summary["scanned_posts"] == 24
        assert summary["by_account"]["profile-a"]["automation_runs"] == 1
        assert summary["by_account"]["x-b"]["likes"] == 3

        assert {item["run_id"] for item in store.pending()} == {"run-1", "run-2"}
        store.mark_uploaded("run-1")
        assert [item["run_id"] for item in AutomationStatisticsStore(path).pending()] == ["run-2"]


def test_progress_updates_one_run_and_final_result_keeps_highest_counters():
    with tempfile.TemporaryDirectory() as directory:
        store = AutomationStatisticsStore(Path(directory) / "agent_state.db")
        common = {
            "run_id": "run-live",
            "profile_id": "profile-live",
            "x_account_id": "x-live",
            "started_at": "2026-08-27T10:00:00+08:00",
        }
        store.record_progress(
            **common,
            progress={"likes": 2, "follows": 1, "comments": 1, "scanned_posts": 8},
        )
        store.record_progress(
            **common,
            progress={"likes": 1, "follows": 3, "comment_count": 2, "views": 15},
        )
        store.record_result(
            **common,
            result={
                "status": "SUCCESS",
                "likes": 0,
                "like_count": 4,
                "follows_today": 3,
                "comments": 2,
                "scanned_posts": 15,
            },
        )

        summary = store.summary()
        assert summary["automation_runs"] == 1
        assert summary["likes"] == 4
        assert summary["follows"] == 3
        assert summary["comments"] == 2
        assert summary["scanned_posts"] == 15
        assert store.pending()[0]["status"] == "SUCCESS"
