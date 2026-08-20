from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class RuntimeConfig:
    """Thread-safe, versioned per-profile runtime configuration."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            self._items = payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            self._items = {}

    def get(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(str(profile_id), {})
            return json.loads(json.dumps(item, ensure_ascii=False))

    def update(self, profile_id: str, values: dict[str, Any], *, mode: str = "HOT_UPDATE") -> dict[str, Any]:
        if mode not in {"NEXT_RUN", "HOT_UPDATE"}:
            raise ValueError("Unsupported runtime config mode")
        if not isinstance(values, dict):
            raise ValueError("Runtime config must be an object")
        with self._lock:
            current = self._items.setdefault(str(profile_id), {"version": 0, "next_run": {}, "active": {}})
            key = "active" if mode == "HOT_UPDATE" else "next_run"
            current.setdefault(key, {}).update(values)
            current["version"] = int(current.get("version") or 0) + 1
            self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")
            return self.get(profile_id)

    def snapshot(self, profile_id: str) -> dict[str, Any]:
        return self.get(profile_id)
