from __future__ import annotations

from enum import Enum
import threading
from typing import Any


class WorkerState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"


class ProfileWorkerError(RuntimeError):
    pass


class ProfileWorker:
    """Serialized lifecycle controller for one browser Profile."""

    def __init__(self, profile_id: str, browser_manager):
        self.profile_id = str(profile_id)
        self.browser_manager = browser_manager
        self.state = WorkerState.STOPPED
        self.last_error = ""
        self._lock = threading.RLock()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state in {WorkerState.IDLE, WorkerState.RUNNING}:
                return self.snapshot()
            self.state = WorkerState.STARTING
            self.last_error = ""
            try:
                self.browser_manager.start_profile(self.profile_id)
            except Exception as exc:
                self.state = WorkerState.ERROR
                self.last_error = str(exc)[:500]
                raise ProfileWorkerError(self.last_error) from exc
            self.state = WorkerState.IDLE
            return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self.state == WorkerState.STOPPED:
                return self.snapshot()
            self.state = WorkerState.STOPPING
            self.last_error = ""
            try:
                self.browser_manager.stop_profile(self.profile_id)
            except Exception as exc:
                self.state = WorkerState.ERROR
                self.last_error = str(exc)[:500]
                raise ProfileWorkerError(self.last_error) from exc
            self.state = WorkerState.STOPPED
            return self.snapshot()

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            try:
                response = self.browser_manager.check_status(self.profile_id)
            except Exception as exc:
                self.state = WorkerState.OFFLINE
                self.last_error = str(exc)[:500]
                raise ProfileWorkerError(self.last_error) from exc
            response = response if isinstance(response, dict) else {}
            status = str(response.get("status") or response.get("browserStatus") or "").upper()
            if status in {"RUNNING", "OPEN", "STARTED", "IDLE"}:
                self.state = WorkerState.IDLE
            elif status in {"STOPPED", "CLOSED", "OFFLINE"}:
                self.state = WorkerState.STOPPED
            elif status:
                self.state = WorkerState.ERROR
            self.last_error = ""
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {"profile_id": self.profile_id, "worker_state": self.state.value, "last_error": self.last_error}


class ProfileWorkerManager:
    SUPPORTED_COMMANDS = frozenset({"START_PROFILE", "STOP_PROFILE", "REFRESH_PROFILE", "START_TASK", "STOP_TASK", "UPDATE_PARAMS", "UPDATE_KEYWORDS", "PROBE_CREDENTIAL_CAPABILITY"})

    def __init__(self, browser_manager, task_service=None, runtime_config=None):
        self.browser_manager = browser_manager
        self.task_service = task_service
        self.runtime_config = runtime_config
        self._workers: dict[str, ProfileWorker] = {}
        self._lock = threading.RLock()

    def worker(self, profile_id: str) -> ProfileWorker:
        key = str(profile_id)
        with self._lock:
            if key not in self._workers:
                self._workers[key] = ProfileWorker(key, self.browser_manager)
            return self._workers[key]

    def dispatch(self, command: dict[str, Any]) -> dict[str, Any]:
        command_type = str(command.get("command_type") or "")
        if command_type not in self.SUPPORTED_COMMANDS:
            raise ProfileWorkerError(f"Command type is not enabled in this worker: {command_type}")
        profile_id = str(command.get("profile_id") or "")
        if not profile_id:
            raise ProfileWorkerError("Profile command requires profile_id")
        worker = self.worker(profile_id)
        if command_type == "START_PROFILE":
            return worker.start()
        if command_type == "STOP_PROFILE":
            return worker.stop()
        if command_type == "REFRESH_PROFILE":
            return worker.refresh()
        if command_type == "PROBE_CREDENTIAL_CAPABILITY":
            result = self.browser_manager.probe_credential_capability(profile_id)
            allowed = {"probe_version", "browser_reachable", "cookie_read_supported", "cookie_write_supported", "credential_snapshot_allowed", "evidence"}
            return {key: result[key] for key in allowed if key in result}
        if command_type in {"UPDATE_PARAMS", "UPDATE_KEYWORDS"}:
            if self.runtime_config is None:
                raise ProfileWorkerError("Command type is not enabled without RuntimeConfig")
            mode = str((command.get("payload") or {}).get("mode") or "NEXT_RUN").upper()
            values = (command.get("payload") or {}).get("values") or {}
            if command_type == "UPDATE_KEYWORDS":
                values = {"keywords": values}
            return {"runtime_config": self.runtime_config.update(profile_id, values, mode=mode)}
        if self.task_service is None:
            raise ProfileWorkerError("Command type is not enabled without TaskService")
        if command_type == "STOP_TASK":
            task_id = str(command.get("task_id") or (command.get("payload") or {}).get("task_id") or "")
            if not task_id or not self.task_service.task_manager.cancel_task(task_id):
                raise ProfileWorkerError("Task is not running or cannot be cancelled")
            return {"task_id": task_id, "cancelled": True}
        payload = command.get("payload") or {}
        task_type = str(payload.get("task_type") or "")
        if task_type not in {"browser.open_url", "x.check_login", "x.read_profile", "x.read_timeline", "x.search"}:
            raise ProfileWorkerError("START_TASK requires a safe task_type")
        task = self.task_service.create_task(profile_id, task_type, payload.get("params") or {}, timeout_seconds=int(payload.get("timeout") or 30))
        result = self.task_service.run_prepared_server_tasks([task])[0]
        return {"task_id": result.task_id, "status": result.status.value, "error": result.error}
