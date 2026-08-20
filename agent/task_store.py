from __future__ import annotations

import json
from pathlib import Path
import threading

from .models import Task


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def append(self, task: Task) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(task.to_dict(), ensure_ascii=False) + "\n")

    def list(self) -> list[dict]:
        if not self.path.exists():
            return []
        records = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    payload = json.loads(line)
                    if isinstance(payload, dict):
                        records.append(payload)
                except json.JSONDecodeError:
                    continue
        return records

    def get(self, task_id: str) -> dict | None:
        return next(
            (item for item in reversed(self.list()) if str(item.get("task_id")) == str(task_id)),
            None,
        )
