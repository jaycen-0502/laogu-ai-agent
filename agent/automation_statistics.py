from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


class AutomationStatisticsStore:
    """Persist engine result counters without changing the engine itself."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_runs (
                    run_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    x_account_id TEXT NOT NULL DEFAULT '',
                    account_tag TEXT NOT NULL DEFAULT '',
                    metric_date TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    processed_count INTEGER NOT NULL DEFAULT 0,
                    likes INTEGER NOT NULL DEFAULT 0,
                    follows INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    scanned_posts INTEGER NOT NULL DEFAULT 0,
                    own_followers INTEGER,
                    own_following INTEGER,
                    payload TEXT NOT NULL,
                    uploaded INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_automation_runs_date_profile "
                "ON automation_runs(metric_date, profile_id)"
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def record_result(
        self,
        *,
        run_id: str,
        profile_id: str,
        x_account_id: str = "",
        account_tag: str = "",
        started_at: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        finished = datetime.now().astimezone()
        payload = {
            "run_id": str(run_id),
            "profile_id": str(profile_id),
            "x_account_id": str(x_account_id or ""),
            "account_tag": str(account_tag or "")[:120],
            "metric_date": finished.date().isoformat(),
            "started_at": str(started_at),
            "finished_at": finished.isoformat(),
            "status": str(result.get("status") or "ERROR")[:20],
            "processed_count": _count(result.get("processed_count")),
            "likes": _count(result.get("likes")),
            "follows": _count(result.get("follows")),
            "comments": _count(result.get("comments")),
            "scanned_posts": _count(result.get("views")),
            "own_followers": _optional_count(result.get("own_followers")),
            "own_following": _optional_count(result.get("own_following")),
        }
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO automation_runs(
                    run_id,profile_id,x_account_id,account_tag,metric_date,
                    started_at,finished_at,status,processed_count,likes,follows,
                    comments,scanned_posts,own_followers,own_following,payload,uploaded
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (
                    payload["run_id"], payload["profile_id"], payload["x_account_id"],
                    payload["account_tag"], payload["metric_date"], payload["started_at"],
                    payload["finished_at"], payload["status"], payload["processed_count"],
                    payload["likes"], payload["follows"], payload["comments"],
                    payload["scanned_posts"], payload["own_followers"],
                    payload["own_following"], json.dumps(payload, ensure_ascii=False),
                ),
            )
        return payload

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM automation_runs WHERE uploaded=0 ORDER BY finished_at LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def mark_uploaded(self, run_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE automation_runs SET uploaded=1 WHERE run_id=?", (str(run_id),))

    def summary(self, metric_date: str | None = None) -> dict[str, Any]:
        day = metric_date or datetime.now().astimezone().date().isoformat()
        with self._lock, self._connect() as db:
            rows = db.execute(
                """
                SELECT profile_id,x_account_id,account_tag,
                       processed_count,likes,follows,comments,scanned_posts,
                       own_followers,own_following
                FROM automation_runs WHERE metric_date=?
                ORDER BY finished_at
                """,
                (day,),
            ).fetchall()
        totals = {"automation_runs": 0, "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0}
        by_profile: dict[str, dict[str, Any]] = {}
        aliases: dict[str, set[str]] = {}
        for profile_id, x_account_id, account_tag, processed, likes, follows, comments, scanned, own_followers, own_following in rows:
            profile_key = str(profile_id)
            item = by_profile.setdefault(
                profile_key,
                {**{key: 0 for key in totals}, "own_followers": None, "own_following": None},
            )
            values = {
                "automation_runs": 1,
                "processed_count": int(processed or 0),
                "likes": int(likes or 0),
                "follows": int(follows or 0),
                "comments": int(comments or 0),
                "scanned_posts": int(scanned or 0),
            }
            for key, value in values.items():
                item[key] += value
                totals[key] += value
            if own_followers is not None:
                item["own_followers"] = int(own_followers)
            if own_following is not None:
                item["own_following"] = int(own_following)
            aliases.setdefault(profile_key, set()).update(
                str(key) for key in (profile_id, x_account_id, account_tag) if key
            )
        by_account: dict[str, dict[str, Any]] = {}
        for profile_key, item in by_profile.items():
            for alias in aliases[profile_key]:
                by_account[alias] = dict(item)
        return {"metric_date": day, "by_account": by_account, **totals}
