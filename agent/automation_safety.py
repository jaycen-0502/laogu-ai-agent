from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any


_ACTIONS = ("like", "follow", "reply", "bookmark", "retweet", "unfollow", "tweet")
_COUNTER_COLUMNS = {
    "like": "likes",
    "follow": "follows",
    "reply": "comments",
    "bookmark": "bookmarks",
    "retweet": "retweets",
    "unfollow": "unfollows",
    "tweet": "tweets",
}
_RISK_STATES = {"CHALLENGE_REQUIRED", "NOT_LOGGED_IN"}


def _count(value: Any, default: int = 0, maximum: int = 100_000) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(maximum, parsed))


def _enabled(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


@dataclass(frozen=True)
class ActionDecision:
    allowed: bool
    reason: str = ""


class AutomationRunGuard:
    """Thread-safe run-scoped safety adapter exposed to bundled engines."""

    def __init__(
        self,
        store: "AutomationSafetyStore",
        profile_id: str,
        run_id: str,
        config: dict[str, Any],
        *,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ):
        self.store = store
        self.profile_id = str(profile_id)
        self.run_id = str(run_id)
        self.config = dict(config)
        self.pause_event = pause_event or threading.Event()
        self.cancel_event = cancel_event or threading.Event()
        self._action_records = 0
        self._lock = threading.RLock()

    @property
    def action_records(self) -> int:
        with self._lock:
            return self._action_records

    def cancel(self) -> None:
        self.cancel_event.set()

    def pause(self) -> None:
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def is_paused(self) -> bool:
        return self.pause_event.is_set()

    def update_config(self, new_config: dict[str, Any]) -> None:
        """支持运行期间动态热更新配额与开关配置."""
        with self._lock:
            self.config.update(dict(new_config))

    def allow_action(self, action: str, target_key: str = "") -> ActionDecision:
        action = str(action or "").strip().lower()
        if action not in _ACTIONS:
            return ActionDecision(False, "未知动作类型")
        if self.cancel_event.is_set():
            return ActionDecision(False, "任务已取消")
        if self.pause_event.is_set():
            return ActionDecision(False, "任务已暂停")
        with self._lock:
            active_cfg = dict(self.config)
        default_allowed = action not in ("reply", "bookmark", "retweet")
        if not _enabled(active_cfg.get(f"allow_{action}"), default_allowed):
            return ActionDecision(False, f"控制中心已禁止{_action_label(action)}")
        if _enabled(active_cfg.get("dry_run"), False):
            return ActionDecision(False, "预演模式不会执行互动动作")

        target_key = str(target_key or "").strip().lower()[:500]
        return self.store.check_action(self.profile_id, action, target_key, active_cfg)

    def record_action(self, action: str, target_key: str = "") -> None:
        action = str(action or "").strip().lower()
        if action not in _ACTIONS:
            return
        self.store.record_action(self.profile_id, action, str(target_key or "").strip().lower()[:500])
        with self._lock:
            self._action_records += 1


class AutomationSafetyStore:
    """Persistent per-account safeguards shared by the embedded desktop Agent."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=30000")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_safety_daily (
                    profile_id TEXT NOT NULL,
                    metric_date TEXT NOT NULL,
                    processed_count INTEGER NOT NULL DEFAULT 0,
                    likes INTEGER NOT NULL DEFAULT 0,
                    follows INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    bookmarks INTEGER NOT NULL DEFAULT 0,
                    retweets INTEGER NOT NULL DEFAULT 0,
                    unfollows INTEGER NOT NULL DEFAULT 0,
                    tweets INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(profile_id, metric_date)
                )
                """
            )
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(automation_safety_daily)")}
            for column in ("unfollows", "tweets"):
                if column not in columns:
                    db.execute(f"ALTER TABLE automation_safety_daily ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_safety_actions (
                    profile_id TEXT NOT NULL,
                    metric_date TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(profile_id, metric_date, action, target_key)
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_safety_risks (
                    profile_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )
        try:
            self.cleanup_old_actions(30)
        except Exception:
            pass

    @contextmanager
    def _connect(self):
        for attempt in range(5):
            try:
                db = sqlite3.connect(self.path, timeout=30)
                try:
                    yield db
                    db.commit()
                    return
                finally:
                    db.close()
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < 4:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise

    @staticmethod
    def _today() -> str:
        # 统一使用业务目标时区（东八区 UTC+8），避免海外/云服务器因 UTC 零点产生统计日期错位 (L5)
        tz = timezone(timedelta(hours=8))
        return datetime.now(tz).date().isoformat()

    def cleanup_old_actions(self, days: int = 30) -> int:
        """自动清理超过 N 天的历史动作记录，防止 SQLite 数据库无限膨胀 (L4)."""
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "DELETE FROM automation_safety_actions WHERE metric_date < date('now', ?)",
                (f"-{max(1, int(days))} days",),
            )
            return cursor.rowcount

    def daily_totals(self, profile_id: str, metric_date: str | None = None) -> dict[str, int]:
        day = metric_date or self._today()
        with self._lock, self._connect() as db:
            row = db.execute(
                """
                SELECT processed_count,likes,follows,comments,bookmarks,retweets,unfollows,tweets
                FROM automation_safety_daily WHERE profile_id=? AND metric_date=?
                """,
                (str(profile_id), day),
            ).fetchone()
        keys = ("processed_count", "likes", "follows", "comments", "bookmarks", "retweets", "unfollows", "tweets")
        return dict(zip(keys, (_count(value) for value in row), strict=True)) if row else {key: 0 for key in keys}

    def risk_status(self, profile_id: str) -> dict[str, str]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT status,reason,run_id,updated_at FROM automation_safety_risks WHERE profile_id=?",
                (str(profile_id),),
            ).fetchone()
        if not row:
            return {"status": "READY", "reason": "", "run_id": "", "updated_at": ""}
        return {"status": str(row[0]), "reason": str(row[1]), "run_id": str(row[2]), "updated_at": str(row[3])}

    def snapshot(self, profile_id: str) -> dict[str, Any]:
        return {"daily": self.daily_totals(profile_id), "risk": self.risk_status(profile_id)}

    def begin_run(
        self,
        profile_id: str,
        run_id: str,
        config: dict[str, Any],
        *,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[AutomationRunGuard | None, dict[str, Any], ActionDecision]:
        profile_id = str(profile_id)
        risk = self.risk_status(profile_id)
        if risk["status"] == "RATE_LIMITED":
            self.resume(profile_id)
            risk = {"status": "READY", "reason": "", "run_id": "", "updated_at": ""}
        if risk["status"] in _RISK_STATES | {"PAUSED"}:
            return None, dict(config), ActionDecision(False, f"账号需人工处理：{risk['status']} {risk['reason']}")
        daily = self.daily_totals(profile_id)
        merged = dict(config)
        merged.update(
            {
                "daily_tasks_used": daily["processed_count"],
                "daily_likes_used": daily["likes"],
                "daily_follows_used": daily["follows"],
                "daily_replies_used": daily["comments"],
                "daily_bookmarks_used": daily["bookmarks"],
                "daily_retweets_used": daily["retweets"],
                "daily_unfollows_used": daily["unfollows"],
                "daily_tweets_used": daily["tweets"],
            }
        )
        return (
            AutomationRunGuard(
                self,
                profile_id,
                run_id,
                merged,
                pause_event=pause_event,
                cancel_event=cancel_event,
            ),
            merged,
            ActionDecision(True),
        )

    def check_action(self, profile_id: str, action: str, target_key: str, config: dict[str, Any]) -> ActionDecision:
        counter = _COUNTER_COLUMNS.get(action)
        if counter:
            raw_limit = config.get(f"daily_{counter}_limit")
            if raw_limit is None and action == "reply":
                raw_limit = config.get("daily_replies_limit")
            if raw_limit is None:
                raw_limit = config.get(f"daily_{action}s_limit")
            limit = _count(raw_limit, 0)
            totals = self.daily_totals(profile_id)
            if limit > 0 and totals.get(counter, 0) >= limit:
                return ActionDecision(False, f"今日{_action_label(action)}已达到账号上限（{totals[counter]}/{limit}）")
        if target_key:
            day = self._today()
            with self._lock, self._connect() as db:
                duplicate = db.execute(
                    """
                    SELECT 1 FROM automation_safety_actions
                    WHERE profile_id=? AND metric_date=? AND action=? AND target_key=?
                    """,
                    (str(profile_id), day, action, target_key),
                ).fetchone()
            if duplicate:
                return ActionDecision(False, f"已处理过同一目标，跳过重复{_action_label(action)}")
        return ActionDecision(True)

    def record_action(self, profile_id: str, action: str, target_key: str) -> None:
        counter = _COUNTER_COLUMNS.get(action)
        day = self._today()
        now = datetime.now().astimezone().isoformat()
        with self._lock, self._connect() as db:
            if target_key:
                added = db.execute(
                    """
                    INSERT OR IGNORE INTO automation_safety_actions(profile_id,metric_date,action,target_key,created_at)
                    VALUES(?,?,?,?,?)
                    """,
                    (str(profile_id), day, action, target_key, now),
                ).rowcount
                if added == 0:
                    return
            db.execute(
                """
                INSERT INTO automation_safety_daily(profile_id,metric_date,updated_at)
                VALUES(?,?,?)
                ON CONFLICT(profile_id,metric_date) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (str(profile_id), day, now),
            )
            if counter:
                db.execute(
                    f"UPDATE automation_safety_daily SET {counter}={counter}+1, updated_at=? WHERE profile_id=? AND metric_date=?",
                    (now, str(profile_id), day),
                )

    def record_result(self, profile_id: str, run_id: str, result: dict[str, Any], *, action_records: int = 0) -> None:
        status = str(result.get("status") or "ERROR").upper()
        now = datetime.now().astimezone().isoformat()
        day = self._today()
        processed = _count(result.get("processed_count", result.get("processed", 0)))
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO automation_safety_daily(profile_id,metric_date,processed_count,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(profile_id,metric_date) DO UPDATE SET
                    processed_count=automation_safety_daily.processed_count + excluded.processed_count,
                    updated_at=excluded.updated_at
                """,
                (str(profile_id), day, processed, now),
            )
            if action_records == 0:
                values = {
                    "likes": _count(result.get("likes", result.get("like_count", 0))),
                    "follows": _count(result.get("follows", result.get("follow_count", 0))),
                    "comments": _count(result.get("comments", result.get("comment_count", 0))),
                    "bookmarks": _count(result.get("bookmarks", result.get("bookmark_count", 0))),
                    "retweets": _count(result.get("retweets", result.get("retweet_count", 0))),
                    "unfollows": _count(result.get("unfollows", result.get("unfollow_count", 0))),
                    "tweets": _count(result.get("tweets", result.get("tweet_count", 0))),
                }
                for column, value in values.items():
                    if value:
                        db.execute(
                            f"UPDATE automation_safety_daily SET {column}={column}+?, updated_at=? WHERE profile_id=? AND metric_date=?",
                            (value, now, str(profile_id), day),
                        )
            if status in _RISK_STATES:
                db.execute(
                    """
                    INSERT INTO automation_safety_risks(profile_id,status,reason,run_id,updated_at)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(profile_id) DO UPDATE SET
                        status=excluded.status, reason=excluded.reason, run_id=excluded.run_id, updated_at=excluded.updated_at
                    """,
                    (str(profile_id), status, str(result.get("error") or "")[:500], str(run_id), now),
                )
            # 自动修剪 30 天以前的旧动作历史明细，防止 SQLite 数据库无限膨胀
            try:
                cutoff_day = (datetime.now().date() - timedelta(days=30)).isoformat()
                db.execute("DELETE FROM automation_safety_actions WHERE metric_date < ?", (cutoff_day,))
            except Exception:
                pass

    def prune_expired_actions(self, retention_days: int = 30) -> int:
        """显式修剪超过 retention_days 天的旧动作明细记录."""
        cutoff_day = (datetime.now().date() - timedelta(days=retention_days)).isoformat()
        with self._lock, self._connect() as db:
            cursor = db.execute("DELETE FROM automation_safety_actions WHERE metric_date < ?", (cutoff_day,))
            return cursor.rowcount

    def pause(self, profile_id: str, reason: str = "管理员暂停") -> None:
        self._set_manual_state(profile_id, "PAUSED", reason)

    def cancel(self, profile_id: str, reason: str = "管理员取消") -> None:
        self._set_manual_state(profile_id, "CANCELLED", reason)

    def resume(self, profile_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM automation_safety_risks WHERE profile_id=?", (str(profile_id),))

    def _set_manual_state(self, profile_id: str, status: str, reason: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO automation_safety_risks(profile_id,status,reason,run_id,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    status=excluded.status, reason=excluded.reason, updated_at=excluded.updated_at
                """,
                (str(profile_id), status, str(reason)[:500], "", datetime.now().astimezone().isoformat()),
            )


def _action_label(action: str) -> str:
    return {
        "like": "点赞",
        "follow": "关注",
        "reply": "回复",
        "bookmark": "收藏",
        "retweet": "转推",
        "unfollow": "取关",
        "tweet": "发推",
    }.get(action, action)
