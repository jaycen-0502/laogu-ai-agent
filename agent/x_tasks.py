from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any

from .models import Task


class ProfileSnapshotStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def update(self, profile_id: str, result: dict[str, Any]) -> None:
        snapshot = {
            "x_username": result.get("xUsername") or result.get("x_username"),
            "x_account_id": result.get("xAccountId") or result.get("x_account_id"),
            "display_name": result.get("display_name"),
            "bio": result.get("bio"),
            "followers_count": result.get("followers_count"),
            "following_count": result.get("following_count"),
            "profile_url": result.get("profile_url"),
            "checked_at": datetime.now().astimezone().isoformat(),
        }
        with self._lock:
            payload = self._load()
            payload[str(profile_id)] = snapshot
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)

    def get(self, profile_id: str) -> dict[str, Any] | None:
        return self._load().get(str(profile_id))

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


class ReadOnlyTaskExecutor:
    def __init__(self, browser_manager, hook_runner, script_path: Path, snapshot_store: ProfileSnapshotStore, script_runner=None):
        self.browser_manager = browser_manager
        self.hook_runner = hook_runner
        self.script_path = script_path
        self.snapshot_store = snapshot_store
        self.script_runner = script_runner

    def execute(self, task: Task) -> dict[str, Any]:
        if task.task_type == "script.execute":
            if self.script_runner is None:
                raise RuntimeError("Script Runner is not configured")
            return self.script_runner.execute(task)
        if task.task_type == "browser.open_url":
            return self.browser_manager.run_automation(
                profile_id=task.profile_id,
                url=task.url,
                timeout_seconds=task.timeout_seconds,
            )
        response = self.hook_runner.run_read_only_task(
            profile_id=task.profile_id,
            task_type=task.task_type,
            params=task.params,
            timeout_seconds=task.timeout_seconds,
            script_path=self.script_path,
        )
        if response.get("ok") is not True:
            raise RuntimeError(str(response.get("reason") or response.get("error") or "Read-only X task failed"))
        result = response
        for _ in range(4):
            if result.get("ok") is False or result.get("status") in {"error", "failed"}:
                raise RuntimeError(str(result.get("reason") or result.get("error") or "Read-only X task failed"))
            nested = result.get("result") if isinstance(result, dict) else None
            if not isinstance(nested, dict):
                break
            result = nested
        result = self._normalize_result(result)
        if task.task_type == "x.read_profile":
            self.snapshot_store.update(task.profile_id, result)
        return result

    @staticmethod
    def _normalize_result(result: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(result)
        aliases = {
            "loginStatus": "login_status",
            "xUsername": "x_username",
            "xAccountId": "x_account_id",
            "identityVerified": "identity_verified",
        }
        for source, target in aliases.items():
            if source in normalized:
                normalized[target] = normalized.pop(source)
        return normalized
