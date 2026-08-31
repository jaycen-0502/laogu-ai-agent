from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import asyncio
import json
from pathlib import Path
import threading
from typing import Any, Iterable
import time

import urllib.request
from uuid import uuid4

from agent.account_discovery import AccountDiscovery
from agent.account_registry import AccountRecord, AccountRegistry
from agent.automation_statistics import AutomationStatisticsStore
from agent.browser_manager import BrowserManager, BrowserManagerError
from agent.config import load_settings
from agent.laogu_api import LaoguApi
from agent.laogu_hook_runner import LaoguProjectHookRunner
from agent.logger import build_logger
from agent.script_updater import (
    get_cached_automation_engine_class,
    get_file_sha256,
    list_cached_engine_metadata,
    read_engine_state,
    sync_engine_from_server,
)
from agent.task_service import TaskService
from agent.agent_service import build_agent_service
from agent.runtime_config import RuntimeConfig
from agent.x_tasks import ProfileSnapshotStore

from agent.x_automation_engine import XAutomationEngine


_AUTO_AGENT_SERVICE = object()

# 支持最多 20 个 Profile 并行窗口独立运行
_AUTOMATION_EXECUTOR = ThreadPoolExecutor(max_workers=20, thread_name_prefix="XAutomationWorker")

# 运行状态互锁集合，防止同一 Profile 被并发重复启动
_RUNNING_PROFILES: set[str] = set()
_RUNNING_LOCK = threading.Lock()

# Serialize browser creation only. Once CDP is ready, different Profiles run
# in parallel and the slot is released.
_BROWSER_START_LOCK = threading.Lock()

_SCHEDULE_MODES = {"smart", "immediate", "scheduled"}
_SCHEDULE_TYPES = {"once", "daily"}


def _schedule_timezone(name: str):
    normalized = str(name or "Asia/Shanghai").strip()
    if normalized not in {"Asia/Shanghai", "UTC+08:00"}:
        raise ValueError(f"Unsupported schedule timezone: {name}")
    return timezone(timedelta(hours=8), name="Asia/Shanghai")


def _next_schedule_at(config: dict[str, Any], *, now: datetime | None = None) -> datetime:
    timezone = _schedule_timezone(str(config.get("schedule_timezone") or "Asia/Shanghai"))
    current = now.astimezone(timezone) if now is not None else datetime.now(timezone)
    schedule_type = str(config.get("schedule_type") or "once").lower()
    if schedule_type == "daily":
        raw_time = str(config.get("scheduled_time") or "").strip()
        try:
            hour, minute = (int(part) for part in raw_time.split(":", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("Daily schedule time must use HH:mm") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Daily schedule time must use HH:mm")
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= current:
            candidate += timedelta(days=1)
        return candidate

    raw_at = str(config.get("scheduled_at") or "").strip()
    if not raw_at:
        raise ValueError("One-time schedule requires scheduled_at")
    try:
        candidate = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("One-time schedule time must be an ISO date-time") from exc
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone)
    return candidate.astimezone(timezone)


def _resolve_automation_engine_class(cache_dir: str | Path, engine_id: str) -> type:
    """Keep the bundled default stable; cached files are for named engines only."""
    normalized_id = str(engine_id or "default").strip() or "default"
    if normalized_id == "default":
        return XAutomationEngine
    cached = get_cached_automation_engine_class(cache_dir, normalized_id) if cache_dir else None
    if cached is None:
        raise RuntimeError(f"未找到已校验的自动化引擎缓存：{normalized_id}")
    return cached


def _resolve_ws_cdp_url(cdp_url: str) -> str:
    """将 http://127.0.0.1:19876 自动解析转换为 Playwright 要求的 ws:// 连接地址，带重试保护"""
    if str(cdp_url).startswith("ws://") or str(cdp_url).startswith("wss://"):
        return cdp_url
    
    base_url = cdp_url.rstrip("/")
    for _ in range(5):  # 最多等待 5 秒直到 CDP 端口完全激活
        try:
            req_url = f"{base_url}/json/version"
            req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    ws_url = data.get("webSocketDebuggerUrl")
                    if ws_url:
                        return ws_url
        except Exception:
            pass
        time.sleep(1)
    return cdp_url


def _run_engine_in_thread(
    cdp_url: str,
    logger: Any,
    config: dict,
    cache_dir: str = "",
    engine_id: str = "default",
    progress_callback=None,
) -> dict:
    """在独立的子线程中直接调用本地 agent.x_automation_engine 脚本"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        real_cdp_url = _resolve_ws_cdp_url(cdp_url)
        engine_class = _resolve_automation_engine_class(cache_dir, engine_id)
        if logger:
            logger.info(
                "Automation engine selected: id=%s class=%s module=%s source=%s",
                str(engine_id or "default").strip() or "default",
                getattr(engine_class, "__name__", str(engine_class)),
                getattr(engine_class, "__module__", ""),
                "bundled" if str(engine_id or "default").strip() in ("", "default") else "cache",
            )
        engine = engine_class(cdp_url=real_cdp_url, logger=logger)
        if progress_callback is not None:
            try:
                setattr(engine, "progress_callback", progress_callback)
            except Exception:
                pass
        return loop.run_until_complete(engine.run(config))
    except Exception as exc:
        if logger:
            logger.error("Profile runner in thread failed: %s", exc)
        return {"status": "ERROR", "error": str(exc)}
    finally:
        loop.close()


@dataclass(frozen=True)
class AccountRow:
    profile_id: str
    profile_name: str
    instance_id: str
    browser_status: str
    login_status: str
    x_username: str
    x_account_id: str
    account_status: str
    last_checked: str
    display_name: str = ""
    bio: str = ""
    followers_count: int | None = None
    following_count: int | None = None
    profile_checked_at: str = ""
    profile_data_status: str = "UNKNOWN"
    runtime_running: bool = False
    runtime_debug_ready: bool = False


def _optional_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    text = str(value or "").strip().replace(",", "").replace("，", "")
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return None


def _snapshot_value(snapshot: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = snapshot.get(key)
        if value is not None and value != "":
            return value
    return None


def account_to_row(
    record: AccountRecord,
    snapshot: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
) -> AccountRow:
    snapshot = snapshot or {}
    runtime = runtime or {}
    runtime_known = bool(runtime)
    running = bool(runtime.get("running") or runtime.get("active")) if runtime_known else False
    debug_ready = bool(
        runtime.get("debugReady", runtime.get("debug_ready", running))
    ) if runtime_known else False
    return AccountRow(
        profile_id=record.profile_id,
        profile_name=str(runtime.get("profileName") or runtime.get("profile_name") or record.profile_name),
        instance_id=record.instance_id,
        browser_status=("RUNNING" if running else "STOPPED") if runtime_known else record.browser_status.value,
        login_status=record.login_status.value,
        x_username=str(snapshot.get("x_username") or record.x_username),
        x_account_id=str(snapshot.get("x_account_id") or record.x_account_id),
        account_status=record.account_status.value,
        last_checked=_format_datetime(record.last_checked),
        display_name=str(snapshot.get("display_name") or ""),
        bio=str(snapshot.get("bio") or ""),
        followers_count=_optional_count(
            _snapshot_value(snapshot, "followers_count", "followersCount", "followers")
        ),
        following_count=_optional_count(
            _snapshot_value(snapshot, "following_count", "followingCount", "following")
        ),
        profile_checked_at=str(snapshot.get("checked_at") or ""),
        profile_data_status=str(snapshot.get("profile_data_status") or "UNKNOWN"),
        runtime_running=running,
        runtime_debug_ready=debug_ready,
    )


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


class DesktopController:
    """Thin desktop facade over the existing agent services."""

    def __init__(
        self,
        *,
        api: LaoguApi | None = None,
        browser_manager: BrowserManager | None = None,
        discovery: AccountDiscovery | None = None,
        registry: AccountRegistry | None = None,
        task_service: TaskService | None = None,
        agent_service=_AUTO_AGENT_SERVICE,
        runtime_config: RuntimeConfig | None = None,
        profile_snapshot_store: ProfileSnapshotStore | None = None,
        automation_statistics: AutomationStatisticsStore | None = None,
    ):
        settings = load_settings()
        self.settings = settings
        logger = build_logger(settings.log_file)
        self.logger = logger
        self.api = api or LaoguApi(settings)
        self.browser_manager = browser_manager or BrowserManager(self.api)
        self.registry = registry or AccountRegistry(
            settings.account_registry_file,
            settings.account_mapping_history_file,
        )
        if discovery is not None:
            self.discovery = discovery
        else:
            hook_runner = LaoguProjectHookRunner(
                node_path=settings.automation_node_path,
                runtime_dir=settings.automation_runtime_dir,
                script_path=settings.account_discovery_script,
                launch_base_url=settings.base_url,
                api_header=settings.api_header,
                api_key=settings.api_key,
                working_dir=settings.account_registry_file.parent.parent,
            )
            self.discovery = AccountDiscovery(
                self.browser_manager,
                logger,
                hook_path=settings.account_discovery_hook_path,
                discovery_url=settings.account_discovery_url,
                timeout_seconds=settings.default_timeout_seconds,
                max_workers=settings.max_concurrency,
                result_file=settings.account_discovery_result_file,
                hook_runner=hook_runner,
            )
        self.task_service = task_service or TaskService()
        self.runtime_config = runtime_config or RuntimeConfig(
            settings.agent_state_file.with_name("runtime_config.json")
        )
        self.profile_snapshot_store = profile_snapshot_store or ProfileSnapshotStore(
            settings.profile_snapshot_file
        )
        self.automation_statistics = automation_statistics or AutomationStatisticsStore(
            settings.agent_state_file
        )
        self._profiles_lock = threading.RLock()
        self._profiles_by_id: dict[str, dict[str, Any]] = {}
        self._schedule_lock = threading.RLock()
        self._scheduled_timers: dict[str, threading.Timer] = {}
        self._scheduled_tokens: dict[str, str] = {}
        self._stopping = False
        self.agent_service = (
            build_agent_service(
                self.task_service,
                self.registry,
                automation_statistics=self.automation_statistics,
            )
            if agent_service is _AUTO_AGENT_SERVICE
            else agent_service
        )
        if self.agent_service is not None:
            self.agent_service.start()
        self._restore_scheduled_tasks()

    def health(self) -> dict[str, Any]:
        return self.api.health()

    def refresh_profiles(self) -> list[dict[str, Any]]:
        profiles = self.browser_manager.get_profiles()
        with self._profiles_lock:
            self._profiles_by_id = {
                str(item.get("profileId") or item.get("profile_id") or ""): dict(item)
                for item in profiles
                if str(item.get("profileId") or item.get("profile_id") or "")
            }
        return profiles

    def list_accounts(self) -> list[AccountRow]:
        snapshots = self.profile_snapshot_store.all()
        with self._profiles_lock:
            profiles = {key: dict(value) for key, value in self._profiles_by_id.items()}
        return [
            account_to_row(record, snapshots.get(record.profile_id), profiles.get(record.profile_id))
            for record in self.registry.list()
        ]

    def scan_accounts(self, profile_ids: Iterable[str] | None = None) -> list[AccountRow]:
        normalized = [str(item) for item in profile_ids or [] if str(item)]
        discoveries = self.discovery.scan(normalized or None)
        self.registry.update_many(discoveries)
        return self.list_accounts()

    def start_profile(self, profile_id: str) -> dict[str, Any]:
        self._require_capability(
            "local.browser.control",
            "当前授权不允许启动浏览器档案。请连接服务器完成认证，或在离线授权有效期内重试。",
        )
        return self._start_profile_serialized(str(profile_id))

    def stop_profile(self, profile_id: str) -> dict[str, Any]:
        profile_id = str(profile_id)
        self._cancel_scheduled_task(profile_id, state="CANCELLED_BY_USER")
        return self.browser_manager.stop_profile(profile_id)

    def set_profile_task_config(self, profile_id: str, config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise ValueError("Profile task config must be an object")
        normalized = self._normalize_profile_task_config(config)
        return self.runtime_config.update(str(profile_id), normalized, mode="HOT_UPDATE")

    @staticmethod
    def _normalize_profile_task_config(config: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(config)
        schedule_mode = str(normalized.get("schedule_mode") or "smart").strip().lower()
        if schedule_mode not in _SCHEDULE_MODES:
            raise ValueError("schedule_mode must be smart, immediate or scheduled")
        schedule_type = str(normalized.get("schedule_type") or "once").strip().lower()
        if schedule_type not in _SCHEDULE_TYPES:
            raise ValueError("schedule_type must be once or daily")
        normalized["schedule_mode"] = schedule_mode
        normalized["schedule_type"] = schedule_type
        normalized["schedule_timezone"] = str(normalized.get("schedule_timezone") or "Asia/Shanghai")
        _schedule_timezone(normalized["schedule_timezone"])
        if schedule_mode == "scheduled":
            scheduled_for = _next_schedule_at(normalized)
            if schedule_type == "once" and scheduled_for <= datetime.now(scheduled_for.tzinfo):
                raise ValueError("单次执行时间必须晚于当前时间")
            if schedule_type == "daily":
                normalized["scheduled_time"] = scheduled_for.strftime("%H:%M")
            else:
                normalized["scheduled_at"] = scheduled_for.isoformat(timespec="minutes")
        if "ai_reply_ratio" in normalized:
            value = normalized["ai_reply_ratio"]
            if isinstance(value, bool):
                raise ValueError("ai_reply_ratio must be a number between 0.0 and 1.0")
            try:
                ratio = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("ai_reply_ratio must be a number between 0.0 and 1.0") from exc
            if not 0.0 <= ratio <= 1.0:
                raise ValueError("ai_reply_ratio must be a number between 0.0 and 1.0")
            normalized["ai_reply_ratio"] = ratio
        return normalized

    def get_profile_task_config(self, profile_id: str) -> dict[str, Any]:
        return self.runtime_config.snapshot(str(profile_id))

    def start_automation_task(self, profile_id: str, config: dict[str, Any]) -> dict[str, Any]:
        self._require_capability(
            "automation.run",
            "当前授权不允许启动自动化任务。请连接服务器完成认证，或在离线授权有效期内重试。",
        )
        profile_id = str(profile_id)
        config = self._normalize_profile_task_config(config)
        saved = self.set_profile_task_config(profile_id, config)
        self.logger.info(
            "[自动化调度] profile=%s state=REQUESTED mode=%s type=%s",
            profile_id,
            config["schedule_mode"],
            config["schedule_type"],
        )
        if config["schedule_mode"] == "scheduled":
            return self._schedule_automation_task(profile_id, config)

        self._cancel_scheduled_task(profile_id, state="REPLACED_BY_MANUAL_RUN")
        return self._start_automation_now(profile_id, config, saved=saved)

    def _start_automation_now(
        self,
        profile_id: str,
        config: dict[str, Any],
        *,
        saved: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile_id = str(profile_id)

        with _RUNNING_LOCK:
            if profile_id in _RUNNING_PROFILES:
                self.logger.warning(f"Profile [{profile_id}] 已经在运行自动化中，拦截重复启动请求。")
                return {
                    "status": "SKIPPED",
                    "profile_id": profile_id,
                    "message": "当前档案已有自动化正在运行，跳过重复启动",
                }
            _RUNNING_PROFILES.add(profile_id)

        try:
            saved = saved or self.set_profile_task_config(profile_id, config)
            engine_id = str(config.get("engine_id") or "default").strip() or "default"
            engine_name = str(config.get("engine_name") or engine_id).strip()
            engine_client = getattr(self.agent_service, "server_client", None) if self.agent_service is not None else None
            cached_engine = get_cached_automation_engine_class(self.settings.engine_cache_dir, engine_id) if engine_id != "default" else XAutomationEngine
            if cached_engine is None and engine_id != "default":
                if engine_client is None or (self.server_agent_status().get("server") != "ONLINE"):
                    raise RuntimeError(f"自定义自动化引擎“{engine_name}”尚未下载，当前服务器不可用。")
                sync_engine_from_server(engine_client, self.settings.engine_cache_dir, engine_id)
            started = self._start_profile_serialized(profile_id)
            
            # 强化 CDP 端点获取逻辑，加入最多 5 次轮询重试
            cdp_url = ""
            for attempt in range(5):
                cdp_url = self._extract_cdp_url(started)
                if not cdp_url:
                    try:
                        status = self.browser_manager.check_status(profile_id)
                        cdp_url = self._extract_cdp_url(status)
                    except AttributeError:
                        break
                    except Exception:
                        pass
                if cdp_url:
                    break
                time.sleep(1.0)

            if not cdp_url:
                raise BrowserManagerError(
                    f"Profile [{profile_id}] 已启动，但老谷浏览器未在超时时间内返回有效的 CDP 端点"
                )

            engine_info = {
                "source": "LOCAL_PARALLEL_ENGINE",
                "engine_id": engine_id,
                "name": engine_name,
                "version": "cached" if engine_id != "default" else "bundled",
                "sha256": "cached" if engine_id != "default" else "bundled",
            }

            run_id = uuid4().hex
            started_at = datetime.now().astimezone().isoformat()
            account = next(
                (item for item in self.registry.list() if item.profile_id == profile_id),
                None,
            )
            x_account_id = str(getattr(account, "x_account_id", "") or "")
            account_tag = str(
                config.get("account_tag")
                or getattr(account, "x_username", "")
                or getattr(account, "profile_name", "")
                or profile_id
            )

            def report_progress(progress: dict[str, Any]) -> None:
                if not isinstance(progress, dict):
                    return
                self.automation_statistics.record_progress(
                    run_id=run_id,
                    profile_id=profile_id,
                    x_account_id=x_account_id,
                    account_tag=account_tag,
                    started_at=started_at,
                    progress=progress,
                )

            report_progress({})
            future = _AUTOMATION_EXECUTOR.submit(
                _run_engine_in_thread,
                cdp_url,
                self.logger,
                config,
                str(self.settings.engine_cache_dir),
                engine_id,
                report_progress,
            )
            future.add_done_callback(
                lambda completed: self._automation_result_finished(
                    completed,
                    run_id=run_id,
                    profile_id=profile_id,
                    x_account_id=x_account_id,
                    account_tag=account_tag,
                    started_at=started_at,
                )
            )

            return {
                "status": "SUCCESS",
                "profile_id": profile_id,
                "run_id": run_id,
                "cdp_url": cdp_url,
                "engine": engine_info,
                "runtime_config": saved,
                "message": "自动化任务已在后台独立线程启动，多窗口隔离运行",
            }
        except Exception:
            with _RUNNING_LOCK:
                _RUNNING_PROFILES.discard(profile_id)
            raise

    def _schedule_automation_task(
        self,
        profile_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        scheduled_for = _next_schedule_at(config)
        saved = self._arm_scheduled_timer(
            profile_id,
            config,
            scheduled_for,
            state="WAITING_SCHEDULE",
        )
        self.logger.info(
            "[自动化调度] profile=%s state=WAITING_SCHEDULE type=%s run_at=%s timezone=%s",
            profile_id,
            config["schedule_type"],
            scheduled_for.isoformat(timespec="minutes"),
            config["schedule_timezone"],
        )
        return {
            "status": "SCHEDULED",
            "profile_id": profile_id,
            "schedule_type": config["schedule_type"],
            "scheduled_for": scheduled_for.isoformat(timespec="minutes"),
            "runtime_config": saved,
            "message": "定时任务已保存；到点后才会启动浏览器，不会提前占用 Profile",
        }

    def _arm_scheduled_timer(
        self,
        profile_id: str,
        config: dict[str, Any],
        scheduled_for: datetime,
        *,
        state: str,
    ) -> dict[str, Any]:
        profile_id = str(profile_id)
        token = uuid4().hex
        delay = max(0.05, (scheduled_for - datetime.now(scheduled_for.tzinfo)).total_seconds())
        timer = threading.Timer(
            delay,
            self._scheduled_task_due,
            args=(profile_id, token, dict(config)),
        )
        timer.daemon = True
        with self._schedule_lock:
            previous = self._scheduled_timers.pop(profile_id, None)
            if previous is not None:
                previous.cancel()
            self._scheduled_tokens[profile_id] = token
            self._scheduled_timers[profile_id] = timer
            saved = self.runtime_config.update(
                profile_id,
                {
                    "schedule_status": state,
                    "schedule_next_run": scheduled_for.isoformat(timespec="minutes"),
                },
                mode="HOT_UPDATE",
            )
            if not self._stopping:
                timer.start()
        return saved

    def _scheduled_task_due(self, profile_id: str, token: str, config: dict[str, Any]) -> None:
        with self._schedule_lock:
            if self._stopping or self._scheduled_tokens.get(profile_id) != token:
                return
            self._scheduled_tokens.pop(profile_id, None)
            self._scheduled_timers.pop(profile_id, None)

        self.logger.info("[自动化调度] profile=%s state=TRIGGERED", profile_id)
        with _RUNNING_LOCK:
            profile_busy = profile_id in _RUNNING_PROFILES
        if profile_busy:
            retry_at = datetime.now(_schedule_timezone(config.get("schedule_timezone", "Asia/Shanghai"))) + timedelta(seconds=60)
            self.logger.info(
                "[自动化调度] profile=%s state=DELAYED_PROFILE_BUSY retry_at=%s",
                profile_id,
                retry_at.isoformat(timespec="minutes"),
            )
            self._arm_scheduled_timer(
                profile_id,
                config,
                retry_at,
                state="DELAYED_PROFILE_BUSY",
            )
            return

        try:
            self._require_capability(
                "automation.run",
                "定时任务到点，但当前授权不允许启动自动化任务。",
            )
            result = self._start_automation_now(
                profile_id,
                config,
                saved=self.runtime_config.snapshot(profile_id),
            )
            if result.get("status") == "SKIPPED":
                retry_at = datetime.now(_schedule_timezone(config.get("schedule_timezone", "Asia/Shanghai"))) + timedelta(seconds=60)
                self._arm_scheduled_timer(
                    profile_id,
                    config,
                    retry_at,
                    state="DELAYED_PROFILE_BUSY",
                )
                return
            self.logger.info(
                "[自动化调度] profile=%s state=STARTED run_id=%s",
                profile_id,
                result.get("run_id", ""),
            )
            if config.get("schedule_type") == "daily":
                next_run = _next_schedule_at(config)
                self._arm_scheduled_timer(
                    profile_id,
                    config,
                    next_run,
                    state="WAITING_SCHEDULE",
                )
            else:
                self.runtime_config.update(
                    profile_id,
                    {"schedule_status": "TRIGGERED", "schedule_next_run": ""},
                    mode="HOT_UPDATE",
                )
        except Exception as exc:
            self.logger.error(
                "[自动化调度] profile=%s state=FAILED error=%s",
                profile_id,
                exc,
            )
            self.runtime_config.update(
                profile_id,
                {"schedule_status": "FAILED", "schedule_next_run": ""},
                mode="HOT_UPDATE",
            )
            if config.get("schedule_type") == "daily" and not self._stopping:
                next_run = _next_schedule_at(config)
                self._arm_scheduled_timer(
                    profile_id,
                    config,
                    next_run,
                    state="WAITING_SCHEDULE",
                )

    def _cancel_scheduled_task(self, profile_id: str, *, state: str) -> bool:
        profile_id = str(profile_id)
        with self._schedule_lock:
            timer = self._scheduled_timers.pop(profile_id, None)
            self._scheduled_tokens.pop(profile_id, None)
            if timer is None:
                return False
            timer.cancel()
        self.runtime_config.update(
            profile_id,
            {"schedule_status": state, "schedule_next_run": ""},
            mode="HOT_UPDATE",
        )
        self.logger.info("[自动化调度] profile=%s state=%s", profile_id, state)
        return True

    def _restore_scheduled_tasks(self) -> None:
        if not hasattr(self.runtime_config, "all"):
            return
        for profile_id, snapshot in self.runtime_config.all().items():
            active = snapshot.get("active") if isinstance(snapshot, dict) else None
            if not isinstance(active, dict) or active.get("schedule_mode") != "scheduled":
                continue
            schedule_type = str(active.get("schedule_type") or "once").lower()
            schedule_status = str(active.get("schedule_status") or "")
            if schedule_status in {"CANCELLED_BY_USER", "REPLACED_BY_MANUAL_RUN"}:
                continue
            if schedule_type == "once" and schedule_status in {"TRIGGERED", "FAILED", "MISSED"}:
                continue
            try:
                scheduled_for = _next_schedule_at(active)
            except (TypeError, ValueError) as exc:
                self.logger.info(
                    "[自动化调度] profile=%s state=RESTORE_SKIPPED error=%s",
                    profile_id,
                    exc,
                )
                continue
            now = datetime.now(scheduled_for.tzinfo)
            if schedule_type == "once" and scheduled_for <= now:
                self.runtime_config.update(
                    profile_id,
                    {"schedule_status": "MISSED", "schedule_next_run": ""},
                    mode="HOT_UPDATE",
                )
                self.logger.info("[自动化调度] profile=%s state=MISSED", profile_id)
                continue
            self._arm_scheduled_timer(
                profile_id,
                active,
                scheduled_for,
                state="WAITING_SCHEDULE",
            )
            self.logger.info(
                "[自动化调度] profile=%s state=RESTORED run_at=%s",
                profile_id,
                scheduled_for.isoformat(timespec="minutes"),
            )

    def _start_profile_serialized(self, profile_id: str) -> dict[str, Any]:
        """Serialize Profile creation and wait for a usable startup response."""
        profile_id = str(profile_id)
        self.logger.info("[启动队列] profile=%s state=WAITING_START_SLOT", profile_id)
        with _BROWSER_START_LOCK:
            self.logger.info("[启动队列] profile=%s state=STARTING", profile_id)
            try:
                if hasattr(self.browser_manager, "start_profile_ready"):
                    started = self.browser_manager.start_profile_ready(
                        profile_id,
                        max(1, int(self.settings.default_timeout_seconds)),
                        retries=2,
                        progress=lambda state: self.logger.info(
                            "[启动队列] profile=%s state=%s", profile_id, state
                        ),
                    )
                else:
                    started = self.browser_manager.start_profile(profile_id)
                if not isinstance(started, dict):
                    raise BrowserManagerError("Browser start returned an invalid response")
                if not hasattr(self.browser_manager, "check_status") and not hasattr(self.browser_manager, "start_profile_ready"):
                    self.logger.info("[启动队列] profile=%s state=START_RESPONSE", profile_id)
                    return started
                cdp_url = self._extract_cdp_url(started)
                deadline = time.monotonic() + max(1, int(self.settings.default_timeout_seconds))
                attempt = 0
                while not cdp_url and time.monotonic() < deadline:
                    attempt += 1
                    self.logger.info(
                        "[启动队列] profile=%s state=WAITING_CDP attempt=%s",
                        profile_id, attempt,
                    )
                    try:
                        status = self.browser_manager.check_status(profile_id)
                        cdp_url = self._extract_cdp_url(status)
                        if cdp_url:
                            started = dict(started)
                            started.update(status)
                    except Exception as exc:
                        self.logger.info(
                            "[启动队列] profile=%s state=CDP_CHECK_FAILED error=%s",
                            profile_id, exc,
                        )
                    if not cdp_url:
                        time.sleep(0.5)
                if not cdp_url:
                    raise BrowserManagerError(
                        f"Profile [{profile_id}] did not expose a usable CDP endpoint"
                    )
                self.logger.info("[启动队列] profile=%s state=READY cdp=%s", profile_id, cdp_url)
                return started
            except Exception as exc:
                self.logger.error("[启动队列] profile=%s state=FAILED error=%s", profile_id, exc)
                raise
            finally:
                self.logger.info("[启动队列] profile=%s state=RELEASE_START_SLOT", profile_id)

    def list_automation_engines(self) -> list[dict[str, Any]]:
        """Return server-published engine choices, keeping the bundled default available."""
        default = {
            "engine_id": "default",
            "name": "默认自动化引擎",
            "description": "控制中心内置的 x_automation_engine.py",
            "version": "bundled",
            "enabled": True,
            "read_only": True,
        }
        local = list_cached_engine_metadata(self.settings.engine_cache_dir)
        if self.agent_service is None:
            return local or [default]
        client = getattr(self.agent_service, "server_client", None)
        if client is None or not hasattr(client, "list_engines"):
            return local or [default]
        try:
            items = client.list_engines()
        except Exception:
            return local or [default]
        normalized = [item for item in items if isinstance(item, dict) and item.get("enabled", True)]
        by_id = {str(item.get("engine_id") or ""): item for item in normalized}
        for item in local:
            by_id.setdefault(str(item.get("engine_id") or ""), item)
        normalized = [item for item in by_id.values() if item.get("engine_id")]
        if not any(str(item.get("engine_id") or "") == "default" for item in normalized):
            normalized.insert(0, default)
        return normalized or [default]

    def _automation_result_finished(
        self,
        future,
        *,
        run_id: str,
        profile_id: str,
        x_account_id: str,
        account_tag: str,
        started_at: str,
    ) -> None:
        with _RUNNING_LOCK:
            _RUNNING_PROFILES.discard(profile_id)

        try:
            result = future.result()
        except Exception as exc:
            result = {"status": "ERROR", "error": str(exc)}
        if not isinstance(result, dict):
            result = {"status": "ERROR", "error": "Automation engine returned an invalid result"}
        self.logger.info(
            "[自动化状态] profile=%s state=FINISHED result=%s error=%s",
            profile_id,
            str(result.get("status") or "UNKNOWN"),
            str(result.get("error") or "")[:300],
        )
        try:
            self.automation_statistics.record_result(
                run_id=run_id,
                profile_id=profile_id,
                x_account_id=x_account_id,
                account_tag=account_tag,
                started_at=started_at,
                result=result,
            )
            if self.agent_service is not None:
                self.agent_service.flush_automation_metrics()
        except Exception as exc:
            self.logger.info("Automation statistics sync deferred: %s", exc)

        # Refresh only the local account snapshot; this supplementary read must
        # never turn a completed automation run into a failure.
        if str(result.get("status") or "").upper() in {"SUCCESS", "COMPLETED"}:
            try:
                profile_task = self.task_service.run(
                    profile_id,
                    "x.read_profile",
                    {"readOnly": True, "source": "automation_finished"},
                )
                profile_status = getattr(profile_task, "status", None)
                if str(getattr(profile_status, "value", profile_status) or "").upper() != "SUCCESS":
                    self.logger.info("Profile asset refresh returned %s for profile=%s", profile_status, profile_id)
            except Exception as exc:
                self.logger.info("Profile asset refresh deferred for profile=%s: %s", profile_id, exc)

    @staticmethod
    def _extract_cdp_url(payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("cdpUrl", "cdp_url", "debuggerUrl", "debugger_url", "webSocketDebuggerUrl"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in ("port", "cdpPort", "cdp_port", "debuggerPort", "debugger_port"):
                value = payload.get(key)
                if isinstance(value, int) and 1 <= value <= 65535:
                    return f"http://127.0.0.1:{value}"
            for value in payload.values():
                found = DesktopController._extract_cdp_url(value)
                if found:
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = DesktopController._extract_cdp_url(value)
                if found:
                    return found
        return ""

    def task_statistics(self, period: str = "today") -> dict[str, Any]:
        summary = dict(self.task_service.statistics.summary(period) or {})
        if period != "today":
            return summary
        automation = self.automation_statistics.summary()
        existing = dict(summary.get("by_account") or {})
        for key, values in automation.get("by_account", {}).items():
            merged = dict(existing.get(key) or {})
            merged.update(values)
            existing[key] = merged
        summary["by_account"] = existing
        for key in (
            "automation_runs",
            "processed_count",
            "likes",
            "follows",
            "comments",
            "scanned_posts",
        ):
            summary[key] = automation.get(key, 0)
        return summary

    def automation_engine_update_status(self) -> dict[str, Any]:
        self._require_remote_agent()
        client = getattr(self.agent_service, "server_client", None)
        if client is None or not hasattr(client, "fetch_engine_manifest"):
            raise RuntimeError("当前运行端不支持自动化脚本更新")

        manifest = client.fetch_engine_manifest()
        if not isinstance(manifest, dict):
            raise RuntimeError("Web 后台返回的脚本版本信息无效")
        cache_dir = Path(self.settings.engine_cache_dir)
        state = read_engine_state(cache_dir)
        active_sha = str(state.get("active_sha256") or "").lower()
        active_path = cache_dir / str(state.get("active_path") or "")
        try:
            active_path.resolve().relative_to(cache_dir.resolve())
            installed_ok = bool(
                active_sha
                and active_path.is_file()
                and get_file_sha256(active_path) == active_sha
            )
        except (OSError, ValueError):
            installed_ok = False
        remote_sha = str(manifest.get("sha256") or "").lower()
        return {
            "status": "OK",
            "remote_version": str(manifest.get("version") or ""),
            "remote_sha256": remote_sha,
            "installed_version": str(state.get("active_version") or ""),
            "installed_sha256": active_sha if installed_ok else "",
            "installed": installed_ok,
            "update_available": not installed_ok or remote_sha != active_sha,
            "read_only": manifest.get("read_only") is True,
        }

    def download_automation_engine_update(self, progress=None) -> dict[str, Any]:
        if progress:
            progress(10, "正在连接 Web 后台…")
        self._require_remote_agent()
        client = getattr(self.agent_service, "server_client", None)
        if client is None or not hasattr(client, "fetch_engine_manifest"):
            raise RuntimeError("当前运行端不支持自动化脚本更新")
        if progress:
            progress(35, "正在下载脚本…")
        changed = sync_engine_from_server(client, self.settings.engine_cache_dir)
        if progress:
            progress(85, "正在校验并替换脚本…")
        status = self.automation_engine_update_status()
        status["downloaded"] = bool(changed)
        status["message"] = "脚本已下载并激活" if changed else "当前已是最新脚本"
        if progress:
            progress(100, "下载完成，替换成功")
        return status

    def recent_activities(self, limit: int = 20) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.task_service.statistics.recent_activities(limit)]

    def task_detail(self, task_id: str) -> dict[str, Any] | None:
        return self.task_service.statistics.task_store.get(task_id)

    def run_read_only_task(
        self, profile_id: str, task_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._require_capability(
            "local.readonly.run",
            "当前授权不允许执行本地只读任务。请连接服务器完成认证，或在离线授权有效期内重试。",
        )
        return self.task_service.run(profile_id, task_type, params).to_dict()

    def operation_permissions(self) -> dict[str, Any]:
        always_allowed = {"local.view", "local.browser.stop"}
        online_capabilities = {
            *always_allowed,
            "local.browser.control",
            "local.readonly.run",
            "automation.run",
            "server.task.receive",
            "engine.update",
        }
        if self.agent_service is None:
            return {
                "mode": "RESTRICTED",
                "expires_at": "",
                "capabilities": sorted(always_allowed),
            }

        try:
            status = self.agent_service.status() or {}
        except Exception:
            status = {}
        online = status.get("server") == "ONLINE" and status.get("agent") == "ONLINE"
        mode = str(status.get("authorization_mode") or ("ONLINE" if online else "RESTRICTED"))
        capabilities = {str(item) for item in status.get("capabilities") or [] if str(item)}

        if online:
            capabilities.update(online_capabilities)
            mode = "ONLINE"
        else:
            capabilities.update(always_allowed)

        return {
            "mode": mode,
            "expires_at": str(status.get("authorization_expires_at") or ""),
            "capabilities": sorted(capabilities),
        }

    def _require_capability(self, capability: str, message: str) -> None:
        capability = str(capability)
        if self.agent_service is None:
            raise RuntimeError(
                "未连接 Web 服务器，当前授权不允许执行本地操作。请先完成运行端认证。"
            )
        checker = getattr(self.agent_service, "has_capability", None)
        if callable(checker):
            try:
                if checker(capability):
                    return
            except Exception:
                pass
        if capability in set(self.operation_permissions().get("capabilities") or []):
            return
        raise RuntimeError(message)

    def _require_remote_agent(self) -> None:
        self._require_capability(
            "engine.update",
            "此操作必须连接服务器并完成运行端认证。",
        )

    def server_agent_status(self) -> dict[str, Any]:
        if self.agent_service is None:
            return {
                "server": "OFFLINE",
                "agent": "UNCONFIGURED",
                "lifecycle": "STOPPED",
                "execution_mode": "EMBEDDED_DESKTOP",
                "cdp_url": self.settings.base_url,
                "last_heartbeat": "",
                "last_error": "LAOGU_SERVER_URL not configured",
                "authorization_mode": "RESTRICTED",
                "authorization_expires_at": "",
                "capabilities": ["local.browser.stop", "local.view"],
            }
        status = self.agent_service.status() or {}
        status.setdefault("cdp_url", self.settings.base_url)
        permissions = self.operation_permissions()
        status.setdefault("authorization_mode", permissions["mode"])
        status.setdefault("authorization_expires_at", permissions["expires_at"])
        status.setdefault("capabilities", permissions["capabilities"])
        return status

    def current_agent_id(self) -> str:
        if self.agent_service is None:
            return ""
        client = getattr(self.agent_service, "server_client", None)
        return str(getattr(client, "agent_id", "") or "")

    def replace_agent_credentials(self, agent_id: str, agent_token: str) -> dict[str, str]:
        agent_id = str(agent_id).strip()
        agent_token = str(agent_token).strip()
        if not agent_id:
            raise ValueError("Agent ID 不能为空")
        if not agent_token.startswith("lag_") or len(agent_token) < 20:
            raise ValueError("Agent Token 格式无效")
        if self.agent_service is None:
            raise RuntimeError("服务器运行端尚未配置")
        client = getattr(self.agent_service, "server_client", None)
        if client is None or not hasattr(client, "replace_agent_token"):
            raise RuntimeError("当前运行端不支持凭据更新")

        current_id = str(getattr(client, "agent_id", "") or "")
        if not current_id or agent_id != current_id:
            raise ValueError("Agent ID 不允许修改，只能替换服务器重新生成的 Token。")
        client.replace_agent_token(agent_id, agent_token)
        if not self.agent_service.heartbeat_once():
            raise RuntimeError(self.agent_service.last_error or "运行端认证失败")
        return self.agent_service.status()

    def profile_runtime_status(self, profile_id: str) -> dict[str, Any]:
        return self.browser_manager.check_status(str(profile_id))

    def stop_agent_service(self) -> None:
        with self._schedule_lock:
            self._stopping = True
            timers = list(self._scheduled_timers.values())
            self._scheduled_timers.clear()
            self._scheduled_tokens.clear()
        for timer in timers:
            timer.cancel()
        if self.agent_service is not None:
            self.agent_service.stop()
