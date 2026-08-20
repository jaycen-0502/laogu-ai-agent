from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Iterable


TIME_RANGES = {"today", "yesterday", "7d", "30d", "all"}


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.astimezone()
    except (TypeError, ValueError):
        return None


def _in_range(timestamp: datetime, period: str, now: datetime) -> bool:
    if period == "all":
        return True
    local = timestamp.astimezone()
    current = now.astimezone()
    if period == "today":
        return local.date() == current.date()
    if period == "yesterday":
        return local.date() == (current.date() - timedelta(days=1))
    days = 7 if period == "7d" else 30
    return current - timedelta(days=days) <= local <= current


class Statistics:
    def __init__(self, task_store, activity_store=None):
        self.task_store = task_store
        self.activity_store = activity_store

    def tasks(self, period: str = "all", *, now: datetime | None = None) -> list[dict]:
        if period not in TIME_RANGES:
            raise ValueError(f"Unsupported time range: {period}")
        current = now or datetime.now().astimezone()
        result = []
        for item in self.task_store.list():
            timestamp = _parse_timestamp(item.get("finished_at") or item.get("created_at"))
            if timestamp and _in_range(timestamp, period, current):
                result.append(item)
        return result

    def summary(self, period: str = "all", *, now: datetime | None = None) -> dict[str, Any]:
        items = self.tasks(period, now=now)
        statuses = Counter(str(item.get("status") or "") for item in items)
        by_type: Counter[str] = Counter(str(item.get("task_type") or "") for item in items)
        by_account: dict[str, dict[str, int]] = {}
        for item in items:
            account = str(item.get("x_account_id") or item.get("profile_id") or "UNKNOWN")
            row = by_account.setdefault(account, {"total_tasks": 0, "success_tasks": 0, "failed_tasks": 0, "timeout_tasks": 0})
            row["total_tasks"] += 1
            if item.get("status") == "SUCCESS":
                row["success_tasks"] += 1
            elif item.get("status") == "TIMEOUT":
                row["timeout_tasks"] += 1
            elif item.get("status") == "FAILED":
                row["failed_tasks"] += 1
        by_task_type = {
            key: {"total_tasks": count, "success_tasks": sum(1 for item in items if item.get("task_type") == key and item.get("status") == "SUCCESS"), "failed_tasks": sum(1 for item in items if item.get("task_type") == key and item.get("status") == "FAILED"), "timeout_tasks": sum(1 for item in items if item.get("task_type") == key and item.get("status") == "TIMEOUT")}
            for key, count in by_type.items()
        }
        return {
            "period": period,
            "total_tasks": len(items),
            "success_tasks": statuses["SUCCESS"],
            "failed_tasks": statuses["FAILED"],
            "timeout_tasks": statuses["TIMEOUT"],
            "by_account": by_account,
            "by_task_type": by_task_type,
        }

    def recent_activities(self, limit: int = 20) -> list:
        if self.activity_store is None:
            return []
        return sorted(self.activity_store.list(), key=lambda item: item.timestamp, reverse=True)[: max(0, limit)]
