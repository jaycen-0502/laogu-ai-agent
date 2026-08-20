from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path
import tempfile

from agent.activity import ActivityStore
from agent.models import TaskStatus
from agent.statistics import Statistics
from agent.task_manager import TaskManager
from agent.task_store import TaskStore
from agent.x_tasks import ProfileSnapshotStore, ReadOnlyTaskExecutor


class FakeBrowserManager:
    def run_automation(self, **kwargs):
        return {"url": kwargs["url"], "title": "Example"}


class FakeHookRunner:
    def __init__(self):
        self.calls = []

    def run_read_only_task(self, **kwargs):
        self.calls.append(kwargs)
        task_type = kwargs["task_type"]
        base = {
            "loginStatus": "LOGGED_IN",
            "xUsername": "@example",
            "xAccountId": "123",
            "identityVerified": True,
        }
        if task_type == "x.read_profile":
            base.update({"display_name": "Example", "bio": None, "followers_count": 10, "following_count": 2, "profile_url": "https://x.com/example"})
        if task_type == "x.read_timeline":
            base.update({"url": "https://x.com/home", "title": "Home / X", "success": True, "duration": 0.1, "posts": []})
        if task_type == "x.search":
            base.update({"query": kwargs["params"]["query"], "url": "https://x.com/search", "title": "Search / X", "success": True, "duration": 0.1, "posts": []})
        return {"ok": True, "status": "success", "result": {"result": base}}


class RaisingExecutor:
    def __init__(self, failure):
        self.failure = failure

    def execute(self, task):
        if task.profile_id == "bad":
            raise self.failure
        return {"ok": True}


def logger():
    value = logging.getLogger("stage6-test")
    value.handlers.clear()
    value.addHandler(logging.NullHandler())
    return value


def make_executor(temp_dir):
    hook = FakeHookRunner()
    executor = ReadOnlyTaskExecutor(
        FakeBrowserManager(),
        hook,
        Path(temp_dir) / "hook.js",
        ProfileSnapshotStore(Path(temp_dir) / "profile_snapshot.json"),
    )
    return hook, executor


def make_task(manager, task_type, params=None, profile_id="p1"):
    return manager.create_task(
        profile_id=profile_id,
        profile_name=profile_id,
        x_account_id="123",
        task_type=task_type,
        params=params,
        url="",
        timeout_seconds=1,
    )


def test_four_read_only_task_types_return_expected_structures():
    with tempfile.TemporaryDirectory() as temp_dir:
        hook, executor = make_executor(temp_dir)
        manager = TaskManager(FakeBrowserManager(), logger(), task_executor=executor)
        tasks = [
            make_task(manager, "x.check_login"),
            make_task(manager, "x.read_profile"),
            make_task(manager, "x.read_timeline"),
            make_task(manager, "x.search", {"query": "python"}),
        ]
        results = manager.run_concurrent(tasks)
        by_type = {item.task_type: item for item in results}
        assert all(item.status is TaskStatus.SUCCESS for item in results)
        assert by_type["x.check_login"].result["login_status"] == "LOGGED_IN"
        assert by_type["x.read_profile"].result["profile_url"] == "https://x.com/example"
        assert by_type["x.read_timeline"].result["posts"] == []
        assert by_type["x.search"].result["query"] == "python"
        assert len(hook.calls) == 4
        assert Path(temp_dir, "profile_snapshot.json").exists()


def test_task_failure_and_timeout_are_distinct_and_profile_isolated():
    failed_manager = TaskManager(FakeBrowserManager(), logger(), max_workers=2, task_executor=RaisingExecutor(RuntimeError("failure")))
    failed = make_task(failed_manager, "x.check_login", profile_id="bad")
    succeeded = make_task(failed_manager, "x.check_login", profile_id="good")
    results = {item.profile_id: item for item in failed_manager.run_concurrent([failed, succeeded])}
    assert results["bad"].status is TaskStatus.FAILED
    assert results["good"].status is TaskStatus.SUCCESS

    timeout_manager = TaskManager(FakeBrowserManager(), logger(), task_executor=RaisingExecutor(TimeoutError("hook timeout")))
    timeout = timeout_manager.run_concurrent([make_task(timeout_manager, "x.check_login", profile_id="bad")])[0]
    assert timeout.status is TaskStatus.TIMEOUT


def test_activity_and_statistics_ranges_without_x_access():
    with tempfile.TemporaryDirectory() as temp_dir:
        task_store = TaskStore(Path(temp_dir) / "tasks.jsonl")
        activity_store = ActivityStore(Path(temp_dir) / "activity.jsonl")
        manager = TaskManager(FakeBrowserManager(), logger(), task_executor=RaisingExecutor(RuntimeError("failure")), task_store=task_store, activity_store=activity_store)
        task = make_task(manager, "x.check_login", profile_id="bad")
        manager.run_concurrent([task])
        assert len(activity_store.list()) == 1
        assert activity_store.list()[0].task_id == task.task_id

        now = datetime.now().astimezone()
        old = task.to_dict()
        old["task_id"] = "old"
        old["created_at"] = (now - timedelta(days=6)).isoformat()
        old["finished_at"] = (now - timedelta(days=6)).isoformat()
        old["status"] = "SUCCESS"
        with task_store.path.open("a", encoding="utf-8") as stream:
            import json
            stream.write(json.dumps(old) + "\n")

        statistics = Statistics(task_store, activity_store)
        assert statistics.summary("today", now=now)["total_tasks"] == 1
        assert statistics.summary("7d", now=now)["total_tasks"] == 2
        assert statistics.summary("all", now=now)["failed_tasks"] == 1


def test_statistics_never_invokes_task_executor():
    class CountingStore:
        def __init__(self):
            self.calls = 0

        def list(self):
            self.calls += 1
            return []

    store = CountingStore()
    statistics = Statistics(store)
    assert statistics.summary("all")["total_tasks"] == 0
    assert store.calls == 1


def test_task_registry_rejects_unregistered_type_and_parameter_bypass():
    manager = TaskManager(FakeBrowserManager(), logger())
    try:
        make_task(manager, "x.like")
        assert False, "unregistered task type was accepted"
    except ValueError:
        pass
    try:
        make_task(manager, "x.check_login", {"script": "click like"})
        assert False, "unexpected parameter was accepted"
    except ValueError:
        pass
