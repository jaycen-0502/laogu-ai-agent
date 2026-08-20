from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from .models import Task


@dataclass(frozen=True)
class Activity:
    activity_id: str
    timestamp: datetime
    profile_id: str
    x_account_id: str
    task_id: str
    activity_type: str
    status: str
    duration: float
    summary: str
    result: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Activity":
        parsed = datetime.fromisoformat(str(payload["timestamp"]))
        return cls(
            activity_id=str(payload.get("activity_id") or ""),
            timestamp=parsed if parsed.tzinfo else parsed.astimezone(),
            profile_id=str(payload.get("profile_id") or ""),
            x_account_id=str(payload.get("x_account_id") or ""),
            task_id=str(payload.get("task_id") or ""),
            activity_type=str(payload.get("activity_type") or ""),
            status=str(payload.get("status") or ""),
            duration=float(payload.get("duration") or 0),
            summary=str(payload.get("summary") or ""),
            result=payload.get("result") if isinstance(payload.get("result"), dict) else None,
        )


SUMMARY_BY_TYPE = {
    "browser.open_url": "打开网页",
    "x.check_login": "检查 X 登录状态",
    "x.read_profile": "读取账号公开资料",
    "x.read_timeline": "读取 X 时间线",
    "x.search": "读取 X 搜索页面",
}


class ActivityStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def record_task(self, task: Task) -> Activity:
        activity = Activity(
            activity_id=uuid4().hex[:12],
            timestamp=task.finished_at or datetime.now().astimezone(),
            profile_id=task.profile_id,
            x_account_id=task.x_account_id,
            task_id=task.task_id,
            activity_type=task.task_type,
            status=task.status.value,
            duration=task.elapsed_time,
            summary=SUMMARY_BY_TYPE.get(task.task_type, task.task_type),
            result=task.result,
        )
        self.append(activity)
        return activity

    def append(self, activity: Activity) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(activity.to_dict(), ensure_ascii=False) + "\n")

    def list(self) -> list[Activity]:
        if not self.path.exists():
            return []
        records = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    payload = json.loads(line)
                    if isinstance(payload, dict):
                        records.append(Activity.from_dict(payload))
                except (ValueError, TypeError, json.JSONDecodeError, KeyError):
                    continue
        return records
