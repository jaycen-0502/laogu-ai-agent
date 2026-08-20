from __future__ import annotations

from typing import Any

from .account_registry import AccountRegistry
from .activity import ActivityStore
from .browser_manager import BrowserManager
from .config import load_settings
from .laogu_api import LaoguApi
from .laogu_hook_runner import LaoguProjectHookRunner
from .logger import build_logger
from .script_runner import ScriptRunner
from .statistics import Statistics
from .task_manager import TaskManager
from .task_store import TaskStore
from .x_tasks import ProfileSnapshotStore, ReadOnlyTaskExecutor


class TaskService:
    def __init__(self):
        settings = load_settings()
        logger = build_logger(settings.log_file)
        api = LaoguApi(settings)
        browser_manager = BrowserManager(api)
        registry = AccountRegistry(settings.account_registry_file, settings.account_mapping_history_file)
        task_store = TaskStore(settings.task_log_file)
        activity_store = ActivityStore(settings.activity_log_file)
        hook_runner = LaoguProjectHookRunner(
            node_path=settings.automation_node_path,
            runtime_dir=settings.automation_runtime_dir,
            script_path=settings.account_discovery_script,
            launch_base_url=settings.base_url,
            api_header=settings.api_header,
            api_key=settings.api_key,
            working_dir=settings.account_registry_file.parent.parent,
        )
        executor = ReadOnlyTaskExecutor(
            browser_manager,
            hook_runner,
            settings.x_readonly_task_script,
            ProfileSnapshotStore(settings.profile_snapshot_file),
            ScriptRunner(hook_runner, settings.account_registry_file.parent.parent),
        )
        self.registry = registry
        self.task_manager = TaskManager(
            browser_manager,
            logger,
            max_workers=settings.max_concurrency,
            task_executor=executor,
            task_store=task_store,
            activity_store=activity_store,
        )
        self.statistics = Statistics(task_store, activity_store)
        self.timeout_seconds = settings.default_timeout_seconds

    def create_task(
        self,
        profile_id: str,
        task_type: str,
        params: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
        timeout_seconds: int | None = None,
    ):
        record = self.registry.get(profile_id)
        profile_name = record.profile_name if record else profile_id
        account_id = record.x_account_id if record else ""
        url = ""
        if task_type == "browser.open_url":
            url = str((params or {}).get("url") or "")
        return self.task_manager.create_task(
            profile_id=profile_id,
            profile_name=profile_name,
            x_account_id=account_id,
            task_type=task_type,
            params=params,
            url=url,
            timeout_seconds=timeout_seconds or self.timeout_seconds,
            task_id=task_id,
        )

    def run(self, profile_id: str, task_type: str, params: dict[str, Any] | None = None):
        return self.task_manager.run_concurrent([self.create_task(profile_id, task_type, params)])[0]

    def run_server_task(self, payload: dict[str, Any], *, script_bundle: dict[str, Any] | None = None, cancel_check=None):
        task = self.prepare_server_task(
            payload,
            script_bundle=script_bundle,
            cancel_check=cancel_check,
        )
        return self.run_prepared_server_tasks([task])[0]

    def prepare_server_task(self, payload: dict[str, Any], *, script_bundle: dict[str, Any] | None = None, cancel_check=None):
        task = self.create_task(
            str(payload["profile_id"]),
            str(payload["task_type"]),
            payload.get("params"),
            task_id=str(payload["task_id"]),
            timeout_seconds=int(payload.get("timeout") or self.timeout_seconds),
        )
        if script_bundle is not None:
            task.metadata["script_bundle"] = script_bundle
        if cancel_check is not None:
            task.metadata["cancel_check"] = cancel_check
        return task

    def run_prepared_server_tasks(self, tasks):
        return self.task_manager.run_concurrent(tasks)

    def run_many(self, requests: list[dict[str, Any]]):
        tasks = [self.create_task(str(item["profile_id"]), str(item["task_type"]), item.get("params")) for item in requests]
        return self.task_manager.run_concurrent(tasks)
