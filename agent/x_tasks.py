from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading
import time
import random
import os
from typing import Any

from .models import Task


class ProfileSnapshotStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def update(self, profile_id: str, result: dict[str, Any]) -> None:
        incoming = {
            "x_username": result.get("xUsername") or result.get("x_username"),
            "x_account_id": result.get("xAccountId") or result.get("x_account_id"),
            "display_name": result.get("display_name"),
            "bio": result.get("bio"),
            "followers_count": result.get("followers_count"),
            "following_count": result.get("following_count"),
            "profile_url": result.get("profile_url"),
            "profile_data_status": result.get("profile_data_status") or "UNKNOWN",
            "profile_data_warnings": result.get("profile_data_warnings") or [],
            "checked_at": datetime.now().astimezone().isoformat(),
        }
        with self._lock:
            payload = self._load()
            previous = payload.get(str(profile_id), {})
            snapshot = dict(previous) if isinstance(previous, dict) else {}
            # A temporarily incomplete X render must not erase the last known
            # good account asset values. Fresh non-null fields still win.
            for key, value in incoming.items():
                if value is not None or key not in snapshot:
                    snapshot[key] = value
            payload[str(profile_id)] = snapshot
            
            self.path.parent.mkdir(parents=True, exist_ok=True)
            
            # 20 窗口高并发专属：带 PID/TID 的临时文件名与避让重试逻辑
            temporary = self.path.with_suffix(f"{self.path.suffix}.tmp_{os.getpid()}_{threading.get_ident()}_{random.randint(1000, 9999)}")
            for attempt in range(8):
                try:
                    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    temporary.replace(self.path)
                    break
                except (OSError, PermissionError):
                    time.sleep(random.uniform(0.02, 0.08))
                finally:
                    if temporary.exists():
                        try:
                            temporary.unlink()
                        except Exception:
                            pass

    def get(self, profile_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._load().get(str(profile_id))
            return dict(item) if isinstance(item, dict) else None

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._load().items()}

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        # 针对并发读取时的锁文件重试避让
        for attempt in range(5):
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                return payload if isinstance(payload, dict) else {}
            except (OSError, PermissionError):
                time.sleep(random.uniform(0.01, 0.04))
            except json.JSONDecodeError:
                return {}
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
        # Accept casing used by browser runtimes and older Hook builds while
        # leaving unrelated task result fields unchanged.
        for target, candidates in {
            "followers_count": ("followers_count", "followersCount", "followers"),
            "following_count": ("following_count", "followingCount", "following"),
        }.items():
            value = normalized.get(target)
            if value is None:
                for key in candidates[1:]:
                    if normalized.get(key) is not None:
                        value = normalized[key]
                        break
            if value is not None:
                normalized[target] = ReadOnlyTaskExecutor._coerce_count(value)
        return normalized

    @staticmethod
    def _coerce_count(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return max(0, value)
        text = str(value).strip().replace(",", "").replace("，", "")
        try:
            return max(0, int(float(text)))
        except (TypeError, ValueError):
            return None
