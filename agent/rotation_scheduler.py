# -*- coding: utf-8 -*-
"""Thread-safe group serial rotation scheduler for Laogu Control Center.

Enforces same-IP / same-node serialized rotation:
- In rotation mode, profiles in the same group execute in serial turns.
- Each profile executes 1 single batch, then enters a handover cooldown.
- Accounts rotate in round-robin fashion until all daily limits are achieved.
- Zero concurrent requests from the same IP, keeping traffic completely human.

Controller requirements:
- get_profile_task_config(profile_id)
- start_automation_task(profile_id, config)
- is_profile_running(profile_id)
- cancel_automation_task(profile_id, reason=...)
- release_profile_resources(profile_id) [optional, recommended]
"""

from __future__ import annotations

import logging
import random
import threading
import time
from copy import deepcopy
from typing import Any, Callable

logger = logging.getLogger("laogu.agent.rotation_scheduler")

StatusCallback = Callable[[str, str, dict[str, Any]], None]
CleanupCallback = Callable[[str], None]


class GroupRotationRunner:
    """Manage serial execution for one group of profiles."""

    TERMINAL_STATUSES = {"STOPPED", "COMPLETED", "FAILED"}

    def __init__(
        self,
        group_id: str,
        group_name: str,
        profile_ids: list[str],
        controller: Any,
        on_status_change: StatusCallback | None = None,
        cleanup_profile: CleanupCallback | None = None,
        task_timeout_sec: float | None = 6 * 60 * 60,
        startup_grace_sec: float = 3.0,
    ) -> None:
        self.group_id = str(group_id)
        self.group_name = str(group_name)
        # Deduplicate profile IDs while preserving original order
        self.profile_ids = list(dict.fromkeys(str(pid) for pid in profile_ids))
        self.controller = controller
        self.on_status_change = on_status_change
        self.cleanup_profile = cleanup_profile
        self.task_timeout_sec = task_timeout_sec
        self.startup_grace_sec = max(0.0, startup_grace_sec)

        self.status = "IDLE"  # IDLE, STARTING, RUNNING, STOPPING, STOPPED, COMPLETED, FAILED
        self.current_profile_id = ""
        self.round_index = 0
        self.last_message = ""

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._stop_reason = ""
        self._start_cancelled = False

        self.progress_by_profile: dict[str, dict[str, Any]] = {}
        self.skipped_profiles: set[str] = set()
        self.cooldown_profiles: dict[str, float] = {}

    def start(self) -> None:
        """Start exactly one worker thread; refuse overlapping running/stopping workers."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self.status in {"STARTING", "RUNNING"}:
                    return
                raise RuntimeError(f"rotation group {self.group_id!r} is still stopping")

            if self.status == "STOPPING" and self._thread is None:
                return
            if self.status == "STOPPING":
                raise RuntimeError(f"rotation group {self.group_id!r} is still stopping")
            if self.status == "STOPPED" and self._start_cancelled:
                return

            if not self.profile_ids:
                self.status = "COMPLETED"
                self.last_message = f"分组【{self.group_name}】没有可执行账号"
                notify = True
            else:
                self._stop_event.clear()
                self._stop_reason = ""
                self._start_cancelled = False
                self.skipped_profiles.clear()
                self.cooldown_profiles.clear()
                self.round_index = 0
                self.current_profile_id = ""
                self.status = "RUNNING"
                self.last_message = f"启动分组【{self.group_name}】串行轮换调度..."
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name=f"Rotation-{self.group_id}",
                    daemon=True,
                )
                thread = self._thread
                notify = True

        if notify:
            self._notify()
        if self.profile_ids:
            with self._lock:
                should_start = self.status == "RUNNING" and not self._stop_event.is_set()
            if should_start:
                thread.start()

    def stop(self, join_timeout: float = 30.0) -> None:
        """Request shutdown, cancel active task outside lock, and wait for worker thread."""
        with self._lock:
            if self.status in self.TERMINAL_STATUSES:
                return

            self._stop_reason = "分组轮换停止"
            self._stop_event.set()
            if self.status == "STARTING" and self._thread is None:
                self._start_cancelled = True
            self.status = "STOPPING"
            self.last_message = f"分组【{self.group_name}】正在停止..."
            profile_id = self.current_profile_id
            thread = self._thread

        self._notify()
        if profile_id:
            self._cancel_profile(profile_id, self._stop_reason)

        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(max(0.0, join_timeout))

        with self._lock:
            if thread is None or not thread.is_alive():
                self.status = "STOPPED"
                self.current_profile_id = ""
                self.last_message = f"分组【{self.group_name}】轮换已手动停止"
        self._notify()

    def is_running(self) -> bool:
        with self._lock:
            return self.status == "RUNNING" and self._thread is not None and self._thread.is_alive()

    def is_active(self) -> bool:
        """Return whether this runner is running or still being started/stopped."""
        with self._lock:
            return self.status in {"STARTING", "RUNNING", "STOPPING"} or (
                self._thread is not None and self._thread.is_alive()
            )

    def mark_starting(self) -> None:
        """Reserve a newly-created runner before manager lock hand-off."""
        with self._lock:
            if self.status != "IDLE":
                raise RuntimeError(f"rotation group {self.group_id!r} is not idle")
            self.status = "STARTING"
            self._start_cancelled = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "group_id": self.group_id,
                "group_name": self.group_name,
                "status": self.status,
                "current_profile_id": self.current_profile_id,
                "round_index": self.round_index,
                "profile_ids": list(self.profile_ids),
                "progress": deepcopy(self.progress_by_profile),
                "skipped_profiles": sorted(self.skipped_profiles),
                "cooldown_profiles": {pid: max(0, int(ts - time.monotonic())) for pid, ts in self.cooldown_profiles.items()},
                "last_message": self.last_message,
            }

    def _notify(self) -> None:
        callback = self.on_status_change
        if callback is None:
            return

        # Snapshot is taken and callback is executed OUTSIDE self._lock to prevent deadlocks
        snapshot = self.snapshot()
        try:
            callback(self.group_id, snapshot["status"], snapshot)
        except Exception:
            logger.exception("Failed to notify rotation status change for group %s", self.group_id)

    def _set_state(
        self,
        *,
        status: str | None = None,
        current_profile_id: str | None = None,
        message: str | None = None,
    ) -> None:
        with self._lock:
            if status is not None:
                self.status = status
            if current_profile_id is not None:
                self.current_profile_id = current_profile_id
            if message is not None:
                self.last_message = message
        self._notify()

    def _get_profile_progress(self, profile_id: str) -> dict[str, Any]:
        """Read and validate today's quota progress for one profile."""
        name = str(profile_id)
        current_val = 0
        target_val = 50
        is_done = False

        try:
            cfg = self.controller.get_profile_task_config(profile_id) or {}
            limit = max(0, int(cfg.get("daily_task_limit", 50)))
            follows_limit = max(0, int(cfg.get("daily_follows_limit", 0)))

            registry = getattr(self.controller, "registry", None)
            rec = registry.get(profile_id) if registry is not None else None
            profile_name = getattr(rec, "profile_name", None)
            if profile_name:
                name = str(profile_name)

            safety = getattr(self.controller, "automation_safety", None)
            if safety is not None:
                today_snap = safety.snapshot(profile_id) or {}
                processed = max(0, int(today_snap.get("processed_count", 0)))
                follows = max(0, int(today_snap.get("follows", 0)))
            else:
                processed = 0
                follows = 0

            if follows_limit > 0:
                current_val = follows
                target_val = follows_limit
            else:
                current_val = processed
                target_val = limit
            is_done = current_val >= target_val

            percent = int(min(100, (current_val / max(1, target_val)) * 100))
            progress = {
                "profile_id": profile_id,
                "name": name,
                "current": current_val,
                "target": target_val,
                "percent": percent,
                "is_done": is_done,
            }
        except Exception:
            logger.exception("Check profile progress failed for %s", profile_id)
            progress = {
                "profile_id": profile_id,
                "name": name,
                "current": 0,
                "target": 50,
                "percent": 0,
                "is_done": False,
            }

        with self._lock:
            self.progress_by_profile[profile_id] = deepcopy(progress)
        return progress

    def _is_profile_quota_fulfilled(self, profile_id: str) -> bool:
        return bool(self._get_profile_progress(profile_id).get("is_done"))

    def _cancel_profile(self, profile_id: str, reason: str) -> None:
        try:
            self.controller.cancel_automation_task(profile_id, reason=reason)
        except Exception:
            logger.exception("Failed to cancel profile task %s", profile_id)

    def _validate_controller(self) -> None:
        required = (
            "get_profile_task_config",
            "start_automation_task",
            "is_profile_running",
            "cancel_automation_task",
        )
        missing = [
            name
            for name in required
            if not callable(getattr(self.controller, name, None))
        ]
        if missing:
            raise RuntimeError(
                "controller is missing required rotation APIs: " + ", ".join(missing)
            )

    def _cleanup_profile(self, profile_id: str) -> None:
        cleanup = self.cleanup_profile
        if cleanup is None:
            cleanup = getattr(self.controller, "release_profile_resources", None)
        if cleanup is None:
            return
        try:
            cleanup(profile_id)
        except Exception:
            logger.exception("Failed to release resources for %s", profile_id)

    def _wait_for_profile_completion(self, profile_id: str) -> None:
        """Wait until a started task is observably stopped.

        The startup grace period closes the race where start_automation_task()
        returns before the controller registers the task as running. Monotonic
        deadline prevents hanging forever on broken tasks.
        """
        started_at = time.monotonic()
        observed_running = False
        deadline = (
            None
            if self.task_timeout_sec is None
            else started_at + max(0.0, self.task_timeout_sec)
        )

        while not self._stop_event.is_set():
            try:
                running = bool(self.controller.is_profile_running(profile_id))
            except Exception as exc:
                raise RuntimeError(f"failed to query profile state: {profile_id}") from exc

            if running:
                observed_running = True
            elif observed_running or (time.monotonic() - started_at >= self.startup_grace_sec):
                return

            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"profile task timed out: {profile_id}")
            self._stop_event.wait(0.2)

    def _run_one_profile(self, profile_id: str) -> bool:
        """Run one micro-batch and guarantee release of profile resources."""
        start_invoked = False
        task_accepted = False
        wait_completed = False
        try:
            if self._stop_event.is_set():
                return False

            task_cfg = dict(self.controller.get_profile_task_config(profile_id) or {})
            task_cfg["single_batch_mode"] = True
            task_cfg["schedule_mode"] = "immediate"

            if self._stop_event.is_set():
                return False

            start_invoked = True
            start_res = self.controller.start_automation_task(profile_id, task_cfg) or {}
            if not isinstance(start_res, dict):
                raise TypeError("start_automation_task must return a dict")

            start_status = str(start_res.get("status", "")).upper()
            if start_status in {"NEEDS_ATTENTION", "ERROR", "FAILED"}:
                reason = str(start_res.get("reason", "启动异常"))
                is_rate_limit = (
                    "RATE_LIMIT" in start_status
                    or "RATE_LIMIT" in reason.upper()
                    or "429" in reason
                    or "冷却" in reason
                    or start_res.get("cooldown_seconds") is not None
                )
                if is_rate_limit:
                    cooldown_sec = float(start_res.get("cooldown_seconds") or 1800.0)
                    logger.info("[组内轮换] 账号【%s】因临时风控冷却中 (%s)，进入动态冷却队列，%d 秒后自动恢复", profile_id, reason, int(cooldown_sec))
                    with self._lock:
                        self.cooldown_profiles[profile_id] = time.monotonic() + cooldown_sec
                else:
                    logger.warning("[组内轮换] 账号【%s】启动致命异常 (%s)，移出今日轮询池", profile_id, reason)
                    with self._lock:
                        self.skipped_profiles.add(profile_id)
                return False

            task_accepted = True
            self._wait_for_profile_completion(profile_id)
            wait_completed = True

            # 运行完成后检查账号最新快照，若运行中途触发了限流，自动将其加入动态冷却队列
            snapshot = {}
            get_snap = getattr(self.controller, "get_account_snapshot", None)
            if callable(get_snap):
                snapshot = get_snap(profile_id) or {}
            elif hasattr(self.controller, "_account_snapshots"):
                snapshot = getattr(self.controller, "_account_snapshots", {}).get(profile_id, {})

            snap_status = str(snapshot.get("status", "")).upper()
            if snap_status == "RATE_LIMITED":
                cooldown_sec = float(snapshot.get("retry_after_seconds") or 1800.0)
                logger.info("[组内轮换] 账号【%s】单批次执行遇到限流冷却，将在 %d 秒后自动重试", profile_id, int(cooldown_sec))
                with self._lock:
                    self.cooldown_profiles[profile_id] = time.monotonic() + cooldown_sec

            return True
        finally:
            if start_invoked and (
                not task_accepted
                or not wait_completed
                or self._stop_event.is_set()
            ):
                reason = self._stop_reason or "轮换批次异常退出"
                self._cancel_profile(profile_id, reason)
            if start_invoked:
                self._cleanup_profile(profile_id)

    def _run_loop(self) -> None:
        logger.info(
            "[组内轮换] 分组【%s】进入排队执行循环，共涉及 %d 个账号: %s",
            self.group_name,
            len(self.profile_ids),
            self.profile_ids,
        )

        try:
            self._validate_controller()
            while not self._stop_event.is_set():
                now = time.monotonic()
                with self._lock:
                    # 清理已到期的冷却记录
                    expired_cooling = [pid for pid, expiry in self.cooldown_profiles.items() if now >= expiry]
                    for pid in expired_cooling:
                        del self.cooldown_profiles[pid]

                    skipped = set(self.skipped_profiles)
                    cooling = dict(self.cooldown_profiles)

                # 寻找未达标且未永久跳过的账号
                unfulfilled = [
                    pid for pid in self.profile_ids
                    if pid not in skipped and not self._is_profile_quota_fulfilled(pid)
                ]

                if not unfulfilled:
                    done_count = sum(
                        self._is_profile_quota_fulfilled(pid)
                        for pid in self.profile_ids
                    )
                    with self._lock:
                        skipped_count = len(self.skipped_profiles)
                    if skipped_count:
                        message = (
                            f"🎉 分组【{self.group_name}】轮换结束："
                            f"{done_count}个账号达标，{skipped_count}个账号因异常跳过"
                        )
                    else:
                        message = (
                            f"🎉 分组【{self.group_name}】名下所有账号 "
                            f"({len(self.profile_ids)}个)今日配额均已达成！"
                        )
                    self._set_state(
                        status="COMPLETED",
                        current_profile_id="",
                        message=message,
                    )
                    return

                # 当前轮次可立即执行的候选账号 (排除正在冷却中的账号)
                active_candidates = [pid for pid in unfulfilled if pid not in cooling]

                if not active_candidates:
                    # 所有剩余未达标账号均处于冷却期中，按最短剩余时间进行可中断休眠，避免忙轮询
                    min_remaining_cooldown = min(cooling[pid] - now for pid in unfulfilled)
                    sleep_duration = min(min_remaining_cooldown, 60.0)
                    self._set_state(
                        current_profile_id="",
                        message=(
                            f"Group [{self.group_name}]: all pending profiles are cooling down; "
                            f"{min_remaining_cooldown:.1f} seconds remaining, retrying in {sleep_duration:.1f} seconds."
                        ),
                    )
                    self._stop_event.wait(sleep_duration)
                    continue

                with self._lock:
                    self.round_index += 1
                    round_index = self.round_index

                executed_in_round = False
                for idx, profile_id in enumerate(active_candidates):
                    if self._stop_event.is_set():
                        break

                    progress = self._get_profile_progress(profile_id)
                    if progress.get("is_done"):
                        continue

                    account_name = progress.get("name", profile_id)
                    current = progress.get("current", 0)
                    target = progress.get("target", 50)
                    percent = progress.get("percent", 0)
                    self._set_state(
                        current_profile_id=profile_id,
                        message=(
                            f"第 {round_index} 轮 | 正在调度账号【{account_name}】"
                            f"(当前进度: {current}/{target}, {percent}%) 执行单批次..."
                        ),
                    )

                    try:
                        completed = self._run_one_profile(profile_id)
                    except Exception:
                        logger.exception("[组内轮换] 账号【%s】执行失败，移出今日轮询池", account_name)
                        with self._lock:
                            self.skipped_profiles.add(profile_id)
                        continue

                    if not completed:
                        continue
                    executed_in_round = True

                    progress_after = self._get_profile_progress(profile_id)
                    new_value = progress_after.get("current", 0)
                    new_target = progress_after.get("target", target)
                    delta = new_value - current
                    with self._lock:
                        skipped_now = set(self.skipped_profiles)
                    remaining = [
                        item for item in active_candidates[idx + 1:]
                        if item not in skipped_now and not self._is_profile_quota_fulfilled(item)
                    ]
                    next_tip = "，即将进入下一轮轮询"
                    if remaining:
                        next_name = self._get_profile_progress(remaining[0]).get("name", remaining[0])
                        next_tip = f"，下一账号: 【{next_name}】"

                    dwell_sec = random.uniform(25.0, 45.0)
                    if progress_after.get("is_done"):
                        message = (
                            f"账号【{account_name}】今日配额 ({new_value}/{new_target}) 已达成！"
                            f"换号静默 {dwell_sec:.1f} 秒{next_tip}"
                        )
                    else:
                        message = (
                            f"账号【{account_name}】本批次完成 (+{delta}, 总计: {new_value}/{new_target})，"
                            f"换号微静默 {dwell_sec:.1f} 秒{next_tip}"
                        )
                    self._set_state(message=message)
                    self._stop_event.wait(dwell_sec)

                if not executed_in_round and not self._stop_event.is_set():
                    now = time.monotonic()
                    with self._lock:
                        skipped = set(self.skipped_profiles)
                        cooling = dict(self.cooldown_profiles)
                    unfulfilled = [
                        pid for pid in self.profile_ids
                        if pid not in skipped and not self._is_profile_quota_fulfilled(pid)
                    ]
                    if unfulfilled and all(cooling.get(pid, now) > now for pid in unfulfilled):
                        continue
                    self._stop_event.wait(10.0)
        except Exception as exc:
            logger.exception("Rotation worker crashed for group %s: %s", self.group_id, exc)
            if self._stop_event.is_set():
                self._set_state(
                    status="STOPPED",
                    current_profile_id="",
                    message=f"分组【{self.group_name}】轮换已停止",
                )
            else:
                self._set_state(
                    status="FAILED",
                    current_profile_id="",
                    message=f"分组【{self.group_name}】调度线程异常退出: {exc}",
                )
        finally:
            with self._lock:
                should_stop = self.status not in {"COMPLETED", "FAILED"}
                if should_stop:
                    self.status = "STOPPED"
                self.current_profile_id = ""
            self._notify()


class GroupRotationManager:
    """Global manager for all group rotation runners."""

    def __init__(
        self,
        controller: Any,
        cleanup_profile: CleanupCallback | None = None,
    ) -> None:
        self.controller = controller
        self.cleanup_profile = cleanup_profile
        self._runners: dict[str, GroupRotationRunner] = {}
        self._lock = threading.RLock()

    def start_rotation(
        self,
        group_id: str,
        group_name: str,
        profile_ids: list[str],
        on_status_change: StatusCallback | None = None,
    ) -> dict[str, Any]:
        gid = str(group_id)
        with self._lock:
            runner = self._runners.get(gid)
            if runner is not None and runner.is_active():
                return runner.snapshot()
            runner = GroupRotationRunner(
                group_id=gid,
                group_name=group_name,
                profile_ids=profile_ids,
                controller=self.controller,
                on_status_change=on_status_change,
                cleanup_profile=self.cleanup_profile,
            )
            runner.mark_starting()
            self._runners[gid] = runner

        runner.start()
        return runner.snapshot()

    def stop_rotation(self, group_id: str) -> None:
        with self._lock:
            runner = self._runners.get(str(group_id))
        if runner is not None:
            runner.stop()

    def stop_all(self, join_timeout: float = 1.0) -> None:
        with self._lock:
            runners = list(self._runners.values())
        for runner in runners:
            runner.stop(join_timeout=join_timeout)

    def is_group_running(self, group_id: str) -> bool:
        with self._lock:
            runner = self._runners.get(str(group_id))
        return runner.is_running() if runner else False

    def get_status(self, group_id: str) -> dict[str, Any]:
        with self._lock:
            runner = self._runners.get(str(group_id))
        return (
            runner.snapshot()
            if runner
            else {"group_id": str(group_id), "status": "IDLE"}
        )
