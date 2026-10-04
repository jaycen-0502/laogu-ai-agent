from __future__ import annotations

from pathlib import Path

from agent.automation_safety import AutomationSafetyStore


def test_action_budget_and_target_dedup_persist_across_store_instances(tmp_path: Path):
    path = tmp_path / "agent_state.db"
    store = AutomationSafetyStore(path)
    guard, config, decision = store.begin_run(
        "profile-1",
        "run-1",
        {"daily_likes_limit": 1, "allow_like": True},
    )

    assert decision.allowed is True
    assert guard is not None
    assert config["daily_likes_used"] == 0
    assert guard.allow_action("like", "url:/user/status/1").allowed is True

    guard.record_action("like", "url:/user/status/1")
    assert guard.allow_action("like", "url:/user/status/1").allowed is False
    assert "上限" in guard.allow_action("like", "url:/user/status/2").reason

    restored = AutomationSafetyStore(path)
    assert restored.daily_totals("profile-1")["likes"] == 1


def test_risk_state_blocks_new_runs_until_administrator_resumes(tmp_path: Path):
    store = AutomationSafetyStore(tmp_path / "agent_state.db")
    store.record_result(
        "profile-1",
        "run-risk",
        {"status": "CHALLENGE_REQUIRED", "error": "verification page"},
    )

    guard, _, decision = store.begin_run("profile-1", "run-next", {})
    assert guard is None
    assert decision.allowed is False
    assert "CHALLENGE_REQUIRED" in decision.reason

    store.resume("profile-1")
    guard, _, decision = store.begin_run("profile-1", "run-next", {})
    assert guard is not None
    assert decision.allowed is True


def test_unknown_login_result_does_not_pause_future_runs(tmp_path: Path):
    store = AutomationSafetyStore(tmp_path / "agent_state.db")
    store.record_result(
        "profile-1",
        "run-unknown-login",
        {"status": "LOGIN_STATUS_UNKNOWN", "error": "X homepage did not finish loading"},
    )

    assert store.risk_status("profile-1")["status"] == "READY"
    guard, _, decision = store.begin_run("profile-1", "run-retry", {})
    assert guard is not None
    assert decision.allowed is True


def test_manual_pause_blocks_new_runs_until_resumed(tmp_path: Path):
    store = AutomationSafetyStore(tmp_path / "agent_state.db")
    store.pause("profile-1")

    guard, _, decision = store.begin_run("profile-1", "run-paused", {})
    assert guard is None
    assert decision.allowed is False

    store.resume("profile-1")
    guard, _, decision = store.begin_run("profile-1", "run-paused", {})
    assert guard is not None
    assert decision.allowed is True


def test_action_permission_and_dry_run_are_enforced_before_script_clicks(tmp_path: Path):
    store = AutomationSafetyStore(tmp_path / "agent_state.db")
    guard, _, decision = store.begin_run(
        "profile-1",
        "run-safe",
        {"allow_follow": False, "dry_run": True},
    )

    assert decision.allowed is True
    assert guard is not None
    assert guard.allow_action("follow", "handle:example").allowed is False
    assert guard.allow_action("like", "url:/example/status/1").allowed is False


def test_unfollow_and_tweet_actions_and_result_metrics(tmp_path: Path):
    store = AutomationSafetyStore(tmp_path / "agent_state.db")
    guard, _, decision = store.begin_run("profile-1", "run-1", {})
    assert decision.allowed is True

    # L2: unfollow and tweet are valid actions
    assert guard.allow_action("unfollow", "handle:old_friend").allowed is True
    guard.record_action("unfollow", "handle:old_friend")
    assert guard.allow_action("unfollow", "handle:old_friend").allowed is False  # dedup

    assert guard.allow_action("tweet", "tweet:daily").allowed is True
    guard.record_action("tweet", "tweet:daily")

    # L3: record_result stores bookmarks and retweets in daily rollups
    store.record_result("profile-1", "run-2", {
        "status": "SUCCESS",
        "processed_count": 5,
        "bookmarks": 3,
        "retweets": 2,
    })
    totals = store.daily_totals("profile-1")
    assert totals["bookmarks"] == 3
    assert totals["retweets"] == 2


def test_cleanup_old_actions_and_guard_update_config(tmp_path: Path):
    store = AutomationSafetyStore(tmp_path / "agent_state.db")
    guard, _, _ = store.begin_run("profile-1", "run-1", {"allow_like": False})

    # Initially like is disallowed
    assert guard.allow_action("like", "url:test").allowed is False

    # L8: hot-update config dynamically
    guard.update_config({"allow_like": True})
    assert guard.allow_action("like", "url:test").allowed is True

    # L4: TTL cleanup executes without error
    deleted = store.cleanup_old_actions(days=30)
    assert deleted >= 0


def test_rate_limited_does_not_block_and_auto_recovers(tmp_path: Path):
    store = AutomationSafetyStore(tmp_path / "agent_state.db")
    
    # 1. record_result with RATE_LIMITED should NOT insert into automation_safety_risks
    store.record_result("profile-rate", "run-1", {"status": "RATE_LIMITED", "error": "Rate limit detected; pause for 900 seconds"})
    risk = store.risk_status("profile-rate")
    assert risk["status"] == "READY"
    
    # 2. Even if a stale RATE_LIMITED risk exists in DB, begin_run auto-clears it and allows run
    store._set_manual_state("profile-rate", "RATE_LIMITED", "Stale rate limit")
    assert store.risk_status("profile-rate")["status"] == "RATE_LIMITED"
    
    guard, _, decision = store.begin_run("profile-rate", "run-2", {})
    assert decision.allowed is True
    assert guard is not None
    # Stale record must be cleared
    assert store.risk_status("profile-rate")["status"] == "READY"

