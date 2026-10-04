import sqlite3

import pytest

from agent.automation_safety import AutomationSafetyStore


@pytest.mark.parametrize("action, column", [("unfollow", "unfollows"), ("tweet", "tweets")])
def test_maintenance_daily_limit_and_persistence(tmp_path, action, column):
    path = tmp_path / "safety.db"
    store = AutomationSafetyStore(path)
    config = {f"daily_{column}_limit": 2}
    guard, merged, decision = store.begin_run("worker-1", "run-1", config)
    assert decision.allowed
    assert merged[f"daily_{column}_used"] == 0
    for target in ("record:1", "record:2"):
        assert guard.allow_action(action, target).allowed
        guard.record_action(action, target)
        guard.record_action(action, target)
    assert store.daily_totals("worker-1")[column] == 2
    assert not guard.allow_action(action, "record:3").allowed
    restored = AutomationSafetyStore(path)
    guard, merged, decision = restored.begin_run("worker-1", "run-2", config)
    assert merged[f"daily_{column}_used"] == 2
    assert not guard.allow_action(action, "record:3").allowed
    assert restored.check_action("worker-2", action, "record:3", config).allowed


@pytest.mark.parametrize("existing", [None, "unfollows", "tweets"])
def test_legacy_daily_schema_migrates_without_data_loss(tmp_path, existing):
    path = tmp_path / "legacy.db"
    day = AutomationSafetyStore._today()
    with sqlite3.connect(path) as database:
        database.execute("""
            CREATE TABLE automation_safety_daily (
                profile_id TEXT NOT NULL, metric_date TEXT NOT NULL,
                processed_count INTEGER NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0, follows INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0, bookmarks INTEGER NOT NULL DEFAULT 0,
                retweets INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
                PRIMARY KEY(profile_id, metric_date)
            )
        """)
        if existing:
            database.execute(f"ALTER TABLE automation_safety_daily ADD COLUMN {existing} INTEGER NOT NULL DEFAULT 0")
        database.execute(
            "INSERT INTO automation_safety_daily(profile_id,metric_date,likes,updated_at) VALUES(?,?,?,?)",
            ("worker-1", day, 7, day),
        )
        if existing:
            database.execute(f"UPDATE automation_safety_daily SET {existing}=3")
    for attempt in range(2):
        store = AutomationSafetyStore(path)
        totals = store.daily_totals("worker-1")
        assert totals["likes"] == 7
        assert totals["unfollows"] == (3 if existing == "unfollows" else 0)
        assert totals["tweets"] == (3 if existing == "tweets" else 0)
    with sqlite3.connect(path) as database:
        columns = {row[1]: row for row in database.execute("PRAGMA table_info(automation_safety_daily)")}
    for column in ("unfollows", "tweets"):
        assert columns[column][2:5] == ("INTEGER", 1, "0")
    store.record_action("worker-1", "unfollow", "record:1")
    store.record_action("worker-1", "tweet", "record:2")
    assert store.daily_totals("worker-1")["unfollows"] == totals["unfollows"] + 1
    assert store.daily_totals("worker-1")["tweets"] == totals["tweets"] + 1


@pytest.mark.parametrize("action, column", [("unfollow", "unfollows"), ("tweet", "tweets")])
@pytest.mark.parametrize("limit", [None, 0])
def test_missing_or_zero_limit_remains_unlimited(tmp_path, action, column, limit):
    store = AutomationSafetyStore(tmp_path / "safety.db")
    config = {} if limit is None else {f"daily_{column}_limit": limit}
    store.record_action("worker-1", action, "")
    store.record_action("worker-1", action, "")
    assert store.daily_totals("worker-1")[column] == 2
    assert store.check_action("worker-1", action, "", config).allowed


@pytest.mark.parametrize("use_aliases", [False, True])
def test_result_metrics_include_maintenance_without_double_counting(tmp_path, use_aliases):
    store = AutomationSafetyStore(tmp_path / "safety.db")
    metrics = {"unfollow_count": 2, "tweet_count": 3} if use_aliases else {"unfollows": 2, "tweets": 3}
    store.record_result("worker-1", "run-1", {"status": "COMPLETED", **metrics})
    store.record_result("worker-1", "run-2", {"status": "COMPLETED", **metrics}, action_records=1)
    assert store.daily_totals("worker-1")["unfollows"] == 2
    assert store.daily_totals("worker-1")["tweets"] == 3
