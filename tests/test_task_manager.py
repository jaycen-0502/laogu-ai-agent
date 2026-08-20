import logging
from pathlib import Path
import tempfile
import threading
import time
import unittest

from agent.models import TaskStatus
from agent.task_manager import TaskManager
from agent.task_store import TaskStore


class FakeBrowserManager:
    def __init__(self):
        self.started = threading.Barrier(2)

    def run_automation(self, *, profile_id: str, url: str, timeout_seconds: int):
        self.started.wait(timeout=2)
        time.sleep(0.05)
        if "fail" in url:
            raise RuntimeError("deliberate failure")
        return {
            "ok": True,
            "status": "success",
            "result": {"url": url, "title": "Example Domain"},
        }


class TaskManagerTests(unittest.TestCase):
    def test_runtime_callback_is_not_persisted_or_reported_as_failure(self):
        logger = logging.getLogger("task-manager-runtime-metadata-test")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())

        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir) / "tasks.jsonl")
            manager = TaskManager(
                FakeBrowserManager(),
                logger,
                max_workers=2,
                task_store=store,
            )
            task = manager.create_task(
                profile_id="p11",
                profile_name="11",
                url="https://example.org",
                timeout_seconds=1,
                metadata={
                    "scenario": "runtime-metadata",
                    "script_bundle": {"source": "module.exports.run = () => ({})"},
                    "cancel_check": lambda: False,
                },
            )
            companion = manager.create_task(
                profile_id="p22",
                profile_name="22",
                url="https://example.com",
                timeout_seconds=1,
            )

            completed = {
                item.task_id: item
                for item in manager.run_concurrent([task, companion])
            }[task.task_id]
            persisted = store.get(task.task_id)

            self.assertEqual(completed.status, TaskStatus.SUCCESS)
            self.assertEqual(persisted["status"], "SUCCESS")
            self.assertEqual(persisted["metadata"], {"scenario": "runtime-metadata"})

    def test_failure_does_not_stop_other_profile(self):
        logger = logging.getLogger("task-manager-test")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        manager = TaskManager(FakeBrowserManager(), logger, max_workers=2)
        failed = manager.create_task(
            profile_id="p11",
            profile_name="11",
            url="https://fail.invalid",
            timeout_seconds=1,
        )
        succeeded = manager.create_task(
            profile_id="p22",
            profile_name="22",
            url="https://example.org",
            timeout_seconds=1,
        )

        results = manager.run_concurrent([failed, succeeded])
        status = {task.profile_name: task.status for task in results}
        self.assertEqual(status["11"], TaskStatus.FAILED)
        self.assertEqual(status["22"], TaskStatus.SUCCESS)


if __name__ == "__main__":
    unittest.main()
