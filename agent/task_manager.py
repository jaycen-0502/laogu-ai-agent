from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime
import threading
import time
from typing import Any, Iterable
from uuid import uuid4

from .browser_manager import BrowserManager
from .logger import log_task_event
from .models import Task, TaskStatus


ALLOWED_TASK_TYPES = frozenset({
    "browser.open_url",
    "x.check_login",
    "x.read_profile",
    "x.read_timeline",
    "x.search",
    "script.execute",
})


class TaskManager:
    def __init__(
        self,
        browser_manager: BrowserManager,
        logger,
        max_workers: int = 2,
        *,
        task_executor: Any | None = None,
        task_store: Any | None = None,
        activity_store: Any | None = None,
    ):
        self.browser_manager = browser_manager
        self.logger = logger
        self.max_workers = max(1, max_workers)
        self.task_executor = task_executor
        self.task_store = task_store
        self.activity_store = activity_store
        self._tasks: dict[str, Task] = {}
        self._futures: dict[str, Future[Task]] = {}
        self._lock = threading.RLock()

    def create_task(
        self,
        *,
        profile_id: str,
        profile_name: str,
        url: str,
        timeout_seconds: int,
        metadata: dict | None = None,
        x_account_id: str = "",
        task_type: str = "browser.open_url",
        params: dict | None = None,
        retry_count: int = 0,
        task_id: str | None = None,
    ) -> Task:
        if task_type not in ALLOWED_TASK_TYPES:
            raise ValueError(f"Unsupported task type: {task_type}")
        normalized_params = dict(params or {})
        if task_type == "x.search" and not str(normalized_params.get("query") or "").strip():
            raise ValueError("x.search requires a non-empty query")
        if task_type == "script.execute":
            required = {"script_id", "script_version_id", "params"}
            if not required.issubset(normalized_params) or not isinstance(normalized_params.get("params"), dict):
                raise ValueError("script.execute requires registered Script metadata")
            unexpected = set(normalized_params) - {"script_id", "script_version_id", "params", "timeout"}
            if unexpected:
                raise ValueError(f"Unsupported parameters for script.execute: {sorted(unexpected)}")
        allowed_params = {"query"} if task_type == "x.search" else set()
        unexpected = set(normalized_params) - allowed_params
        if task_type.startswith("x.") and unexpected:
            raise ValueError(f"Unsupported parameters for {task_type}: {sorted(unexpected)}")
        task = Task(
            task_id=task_id or uuid4().hex[:12],
            profile_id=profile_id,
            profile_name=profile_name,
            url=url,
            timeout_seconds=timeout_seconds,
            x_account_id=str(x_account_id),
            task_type=task_type,
            params=normalized_params,
            retry_count=max(0, int(retry_count)),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._tasks[task.task_id] = task
        return task

    def _run_task(self, task: Task) -> Task:
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now().astimezone()
        started = time.monotonic()
        log_task_event(
            self.logger,
            task_id=task.task_id,
            profile_id=task.profile_id,
            profile_name=task.profile_name,
            status=task.status.value,
            operation=task.task_type,
            url=task.url,
            timeout=f"{task.timeout_seconds}s",
        )
        try:
            if self.task_executor is not None:
                task.result = self.task_executor.execute(task)
            else:
                task.result = self.browser_manager.run_automation(
                    profile_id=task.profile_id,
                    url=task.url,
                    timeout_seconds=task.timeout_seconds,
                )
            task.status = TaskStatus.SUCCESS
        except Exception as exc:  # Task failures must not escape the worker.
            task.error = str(exc)
            lowered = task.error.lower()
            if "cancelled" in lowered or "canceled" in lowered:
                task.status = TaskStatus.CANCELLED
            elif "timed out" in lowered or "timeout" in type(exc).__name__.lower():
                task.status = TaskStatus.TIMEOUT
            else:
                task.status = TaskStatus.FAILED
        finally:
            task.elapsed_time = round(time.monotonic() - started, 3)
            task.finished_at = datetime.now().astimezone()

        result = task.result.get("result", task.result) if task.result else {}
        log_task_event(
            self.logger,
            task_id=task.task_id,
            profile_id=task.profile_id,
            profile_name=task.profile_name,
            status=task.status.value,
            operation=task.task_type,
            elapsed=f"{task.elapsed_time:.3f}s",
            url=result.get("url", task.url),
            title=result.get("title", ""),
            error=task.error,
        )
        if self.task_store is not None:
            self.task_store.append(task)
        if self.activity_store is not None:
            self.activity_store.record_task(task)
        return task

    def run_concurrent(self, tasks: Iterable[Task]) -> list[Task]:
        task_list = list(tasks)
        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="laogu-task",
        ) as executor:
            future_to_task: dict[Future[Task], Task] = {}
            for task in task_list:
                future = executor.submit(self._run_task, task)
                future_to_task[future] = task
                with self._lock:
                    self._futures[task.task_id] = future

            completed: list[Task] = []
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    completed.append(future.result())
                except Exception as exc:
                    task.status = TaskStatus.FAILED
                    task.error = f"Unexpected task worker failure: {exc}"
                    task.finished_at = datetime.now().astimezone()
                    completed.append(task)
            return sorted(completed, key=lambda item: item.started_at or datetime.min.astimezone())

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            future = self._futures.get(task_id)
            if task is None or future is None or not future.cancel():
                return False
            task.status = TaskStatus.CANCELLED
            task.finished_at = datetime.now().astimezone()
            return True

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[Task]:
        with self._lock:
            return list(self._tasks.values())
