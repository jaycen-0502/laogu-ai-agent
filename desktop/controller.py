from __future__ import annotations

from collections.abc import MutableSet, MutableMapping
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
from typing import Any, Callable, Iterable
import time

import urllib.request
from uuid import uuid4

from agent.account_discovery import AccountDiscovery
from agent.account_registry import AccountRecord, AccountRegistry
from agent.automation_safety import AutomationRunGuard, AutomationSafetyStore
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
from agent.value_parsing import parse_bool
from agent.x_tasks import ProfileSnapshotStore
from agent.group_manager import ProfileGroupStore, GroupRecord
from agent.rotation_scheduler import GroupRotationManager

from agent.x_automation_engine import XAutomationEngine
from agent.telegram_notifier import TelegramNotifier, TelegramConfig
from agent.blacklist_filter import (
    DEFAULT_BLACKLIST_WORDS,
    DEFAULT_CHECK_OPTIONS,
    clean_target_creators,
    parse_blacklist_input,
)


_module_logger = logging.getLogger("laogu-ai-agent.controller")
_AUTO_AGENT_SERVICE = object()

_AUTOMATION_EXECUTOR = ThreadPoolExecutor(max_workers=20, thread_name_prefix="XAutomationWorker")


def _get_automation_executor() -> ThreadPoolExecutor:
    global _AUTOMATION_EXECUTOR
    if _AUTOMATION_EXECUTOR._shutdown or getattr(_AUTOMATION_EXECUTOR, "_broken", False):
        _AUTOMATION_EXECUTOR = ThreadPoolExecutor(max_workers=20, thread_name_prefix="XAutomationWorker")
    return _AUTOMATION_EXECUTOR

@dataclass
class RunState:
    profile_id: str
    run_id: str
    guard: AutomationRunGuard | None = None
    future: Future | None = None
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task | None = None
    finalized_event: threading.Event = field(default_factory=threading.Event)
    phase: str = "STARTING"

_LIFECYCLE_LOCK = threading.RLock()
_RUN_STATES: dict[str, RunState] = {}
_STOP_EVENT = threading.Event()

# ================= 兼容旧测试反射与引用的全功能双向代理 =================

class _RunningProfilesProxy(MutableSet):
    def __contains__(self, item: Any) -> bool:
        with _LIFECYCLE_LOCK:
            return str(item) in _RUN_STATES

    def __iter__(self):
        with _LIFECYCLE_LOCK:
            return iter(list(_RUN_STATES.keys()))

    def __len__(self) -> int:
        with _LIFECYCLE_LOCK:
            return len(_RUN_STATES)

    def add(self, value: Any) -> None:
        val_str = str(value)
        with _LIFECYCLE_LOCK:
            if val_str not in _RUN_STATES:
                _RUN_STATES[val_str] = RunState(profile_id=val_str, run_id=uuid4().hex, phase="STARTING")

    def discard(self, value: Any) -> None:
        val_str = str(value)
        with _LIFECYCLE_LOCK:
            st = _RUN_STATES.pop(val_str, None)
            if st:
                st.finalized_event.set()


class _AutomationControlsProxy(MutableMapping):
    def __getitem__(self, key: Any) -> Any:
        with _LIFECYCLE_LOCK:
            st = _RUN_STATES.get(str(key))
            if st and st.guard is not None:
                return st.guard
            raise KeyError(key)

    def __setitem__(self, key: Any, value: Any) -> None:
        k_str = str(key)
        with _LIFECYCLE_LOCK:
            st = _RUN_STATES.get(k_str)
            if not st:
                st = RunState(profile_id=k_str, run_id=uuid4().hex, phase="STARTING")
                _RUN_STATES[k_str] = st
            st.guard = value

    def __delitem__(self, key: Any) -> None:
        with _LIFECYCLE_LOCK:
            st = _RUN_STATES.get(str(key))
            if not st or st.guard is None:
                raise KeyError(key)
            st.guard = None

    def __iter__(self):
        with _LIFECYCLE_LOCK:
            return iter([k for k, v in _RUN_STATES.items() if v.guard is not None])

    def __len__(self) -> int:
        with _LIFECYCLE_LOCK:
            return sum(1 for v in _RUN_STATES.values() if v.guard is not None)


class _AutomationRunIdsProxy(MutableMapping):
    def __getitem__(self, key: Any) -> Any:
        with _LIFECYCLE_LOCK:
            st = _RUN_STATES.get(str(key))
            if st and st.run_id:
                return st.run_id
            raise KeyError(key)

    def __setitem__(self, key: Any, value: Any) -> None:
        k_str = str(key)
        with _LIFECYCLE_LOCK:
            st = _RUN_STATES.get(k_str)
            if not st:
                st = RunState(profile_id=k_str, run_id=str(value), phase="STARTING")
                _RUN_STATES[k_str] = st
            else:
                st.run_id = str(value)

    def __delitem__(self, key: Any) -> None:
        with _LIFECYCLE_LOCK:
            st = _RUN_STATES.get(str(key))
            if not st or not st.run_id:
                raise KeyError(key)
            st.run_id = ""

    def __iter__(self):
        with _LIFECYCLE_LOCK:
            return iter([k for k, v in _RUN_STATES.items() if v.run_id])

    def __len__(self) -> int:
        with _LIFECYCLE_LOCK:
            return sum(1 for v in _RUN_STATES.values() if v.run_id)


_RUNNING_LOCK = _LIFECYCLE_LOCK
_RUNNING_PROFILES = _RunningProfilesProxy()
_AUTOMATION_CONTROLS = _AutomationControlsProxy()
_AUTOMATION_RUN_IDS = _AutomationRunIdsProxy()
_AUTOMATION_CONTROL_LOCK = _LIFECYCLE_LOCK
_BROWSER_START_LOCK = threading.Lock()

_SCHEDULE_MODES = {"smart", "immediate", "scheduled"}
_SCHEDULE_TYPES = {"once", "daily"}


def _schedule_timezone(name: str):
    normalized = str(name or "Asia/Shanghai").strip()
    if normalized not in {"Asia/Shanghai", "UTC+08:00"}:
        raise ValueError(f"Unsupported schedule timezone: {name}")
    return timezone(timedelta(hours=8), name="Asia/Shanghai")


def _clear_automation_state(profile_id: str, run_id: str = "") -> None:
    profile_id = str(profile_id)
    with _LIFECYCLE_LOCK:
        state = _RUN_STATES.get(profile_id)
        if state:
            if run_id and state.run_id and state.run_id != run_id:
                return
            state.finalized_event.set()
            _RUN_STATES.pop(profile_id, None)


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


def _resolve_automation_engine_class(cache_dir: str | Path, engine_id: str, logger: Any = None) -> type:
    normalized_id = str(engine_id or "default").strip() or "default"
    log_info = logger.info if logger and hasattr(logger, "info") else _module_logger.info
    log_warn = logger.warning if logger and hasattr(logger, "warning") else _module_logger.warning
    if cache_dir:
        try:
            cached = get_cached_automation_engine_class(cache_dir, normalized_id)
        except Exception as exc:
            log_warn("读取引擎缓存 [%s] 失败: %s，回退内置最新引擎", normalized_id, exc)
            cached = None
        if cached is not None:
            mod_name = getattr(cached, "__module__", "")
            if mod_name.startswith("laogu_dynamic_x_engine_"):
                mod = sys.modules.get(mod_name)
                auto_cfg = getattr(cached, "AutomationConfig", None) or (getattr(mod, "AutomationConfig", None) if mod else None)
                if auto_cfg and not hasattr(auto_cfg, "smart_schedule_enabled"):
                    log_info("引擎缓存 [%s] 缺少最新特性或已过时，自动采用内置最新 XAutomationEngine", normalized_id)
                    return XAutomationEngine
            return cached
    if normalized_id == "default":
        return XAutomationEngine
    raise RuntimeError(f"未找到已校验的自动化引擎缓存：{normalized_id}")



def _resolve_ws_cdp_url(cdp_url: str, stop_event: threading.Event | None = None) -> str:
    if str(cdp_url).startswith("ws://") or str(cdp_url).startswith("wss://"):
        return cdp_url
    
    base_url = cdp_url.rstrip("/")
    for _ in range(5):
        if (stop_event and stop_event.is_set()) or _STOP_EVENT.is_set():
            return cdp_url
        try:
            req_url = f"{base_url}/json/version"
            req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=1.0) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    ws_url = data.get("webSocketDebuggerUrl")
                    if ws_url:
                        return ws_url
        except Exception:
            pass
        for _s in range(8):
            if (stop_event and stop_event.is_set()) or _STOP_EVENT.is_set():
                return cdp_url
            time.sleep(0.1)
    return cdp_url


def _run_engine_in_thread(
    cdp_url: str,
    logger: Any,
    config: dict,
    cache_dir: str = "",
    engine_id: str = "default",
    progress_callback=None,
    safety_guard: AutomationRunGuard | None = None,
    risk_artifact_dir: str = "",
    *,
    profile_id: str = "",
    run_id: str = "",
) -> dict:
    if _STOP_EVENT.is_set():
        return {"status": "CANCELLED", "error": "Controller is shutting down"}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task: asyncio.Task | None = None
    try:
        real_cdp_url = _resolve_ws_cdp_url(cdp_url, _STOP_EVENT)
        if _STOP_EVENT.is_set():
            return {"status": "CANCELLED", "error": "Controller was stopped during CDP resolution"}

        try:
            engine_class = _resolve_automation_engine_class(cache_dir, engine_id, logger=logger)
        except TypeError:
            engine_class = _resolve_automation_engine_class(cache_dir, engine_id)
        if logger:
            logger.info(
                "Automation engine selected: id=%s class=%s module=%s source=%s",
                str(engine_id or "default").strip() or "default",
                getattr(engine_class, "__name__", str(engine_class)),
                getattr(engine_class, "__module__", ""),
                "cache" if getattr(engine_class, "__module__", "").startswith("laogu_dynamic_x_engine_") else "bundled",
            )
        engine = engine_class(cdp_url=real_cdp_url, logger=logger)
        if progress_callback is not None:
            setattr(engine, "progress_callback", progress_callback)
        if safety_guard is not None:
            setattr(engine, "safety_guard", safety_guard)
        if risk_artifact_dir:
            setattr(engine, "risk_artifact_dir", risk_artifact_dir)

        task = loop.create_task(engine.run(config))
        if profile_id:
            with _LIFECYCLE_LOCK:
                state = _RUN_STATES.get(profile_id)
                if state and state.run_id == run_id:
                    state.loop = loop
                    state.task = task
                    state.phase = "RUNNING"
                if _STOP_EVENT.is_set():
                    loop.call_soon_threadsafe(task.cancel)

        return loop.run_until_complete(task)
    except asyncio.CancelledError:
        if logger:
            logger.info("[任务取消] Profile %s 异步任务已被安全终止", profile_id)
        return {"status": "CANCELLED", "error": "Task was cancelled"}
    except Exception as exc:
        if logger:
            logger.error("Profile runner in thread failed: %s", exc)
        return {"status": "ERROR", "error": str(exc)}
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for p in pending:
                p.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            if hasattr(loop, "shutdown_default_executor"):
                try:
                    loop.run_until_complete(asyncio.wait_for(loop.shutdown_default_executor(), timeout=1.5))
                except Exception:
                    pass
        except Exception:
            pass
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
    proxy_id: str = ""
    proxy_name: str = ""
    proxy_protocol: str = ""
    proxy_host: str = ""
    proxy_port: str = ""
    proxy_status: str = "UNKNOWN"
    exit_ip: str = ""
    proxy_checked_at: str = ""
    schedule_mode: str = "smart"
    schedule_status: str = ""
    schedule_next_run: str = ""
    schedule_type: str = "once"
    automation_running: bool = False


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
    task_config: dict[str, Any] | None = None,
    automation_running: bool = False,
) -> AccountRow:
    snapshot = snapshot or {}
    runtime = runtime or {}
    runtime_known = bool(runtime)
    running = bool(runtime.get("running") or runtime.get("active")) if runtime_known else False
    debug_ready = bool(
        runtime.get("debugReady", runtime.get("debug_ready", running))
    ) if runtime_known else False

    cfg = task_config or {}
    active_cfg = cfg.get("active") if isinstance(cfg, dict) and isinstance(cfg.get("active"), dict) else (cfg if isinstance(cfg, dict) else {})
    sched_mode = str(active_cfg.get("schedule_mode") or "smart")
    sched_status = str(active_cfg.get("schedule_status") or "")
    sched_next = str(active_cfg.get("schedule_next_run") or "")
    sched_type = str(active_cfg.get("schedule_type") or "once")

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
        proxy_id=record.proxy_id,
        proxy_name=record.proxy_name,
        proxy_protocol=record.proxy_protocol,
        proxy_host=record.proxy_host,
        proxy_port=record.proxy_port,
        proxy_status=record.proxy_status,
        exit_ip=record.exit_ip,
        proxy_checked_at=_format_datetime(record.proxy_checked_at) if record.proxy_checked_at else "",
        schedule_mode=sched_mode,
        schedule_status=sched_status,
        schedule_next_run=sched_next,
        schedule_type=sched_type,
        automation_running=automation_running,
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
        automation_safety: AutomationSafetyStore | None = None,
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
        self.group_store = ProfileGroupStore(
            settings.agent_state_file.with_name("profile_groups.json")
        )
        self.rotation_manager = GroupRotationManager(self)
        self.automation_statistics = automation_statistics or AutomationStatisticsStore(
            settings.agent_state_file
        )
        self.automation_safety = automation_safety or AutomationSafetyStore(settings.agent_state_file)
        self._profiles_lock = threading.RLock()
        self._profiles_by_id: dict[str, dict[str, Any]] = {}
        self._schedule_lock = threading.RLock()
        self._scheduled_timers: dict[str, threading.Timer] = {}
        self._scheduled_tokens: dict[str, str] = {}
        self._on_schedule_event: Callable[[str, str, dict[str, Any]], None] | None = None
        self.telegram_notifier = TelegramNotifier()
        self._account_snapshots: dict[str, dict[str, Any]] = {}
        _STOP_EVENT.clear()
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

    @property
    def _stopping(self) -> bool:
        return _STOP_EVENT.is_set()

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
        # Keep proxy labels and Profile metadata fresh without opening a
        # browser page or re-running account discovery.  Verified X identity
        # values remain owned by AccountRegistry and are never overwritten by
        # an incomplete Profile response.
        update_profile_metadata = getattr(self.registry, "update_profile_metadata", None)
        if callable(update_profile_metadata):
            update_profile_metadata(profiles)
        return profiles

    def register_schedule_listener(self, listener: Callable[[str, str, dict[str, Any]], None]) -> None:
        self._on_schedule_event = listener

    def list_accounts(self) -> list[AccountRow]:
        snapshots = self.profile_snapshot_store.all()
        with self._profiles_lock:
            profiles = {key: dict(value) for key, value in self._profiles_by_id.items()}
        task_configs = self.runtime_config.all() if hasattr(self.runtime_config, "all") else {}
        with _LIFECYCLE_LOCK:
            running_profile_ids = set(_RUN_STATES.keys())
        return [
            account_to_row(
                record,
                snapshots.get(record.profile_id),
                profiles.get(record.profile_id),
                task_configs.get(record.profile_id),
                record.profile_id in running_profile_ids,
            )
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
        self.cancel_automation_task(profile_id, reason="浏览器已由用户停止")
        self._cancel_scheduled_task(profile_id, state="CANCELLED_BY_USER")
        try:
            return self.browser_manager.stop_profile(profile_id)
        except Exception as exc:
            err_msg = str(exc).lower()
            if "not found" in err_msg or "404" in err_msg:
                if hasattr(self, "logger") and self.logger:
                    self.logger.info("stop_profile: ignored 404/not found for profile %s: %s", profile_id, exc)
                return {
                    "ok": True,
                    "stopped": True,
                    "profileId": profile_id,
                    "note": "already_stopped_or_not_found",
                }
            raise

    def release_profile_resources(self, profile_id: str) -> None:
        """Idempotently ensure automation task is cancelled and browser is fully stopped."""
        profile_id = str(profile_id)
        try:
            self.cancel_automation_task(profile_id, reason="轮换批次资源释放")
        except Exception:
            pass
        try:
            if hasattr(self, "browser_manager") and hasattr(self.browser_manager, "stop_profile"):
                self.browser_manager.stop_profile(profile_id)
        except Exception:
            pass

    def delete_account(self, profile_id: str) -> bool:
        pid = str(profile_id).strip()
        if not pid:
            return False
        try:
            self.stop_profile(pid)
        except Exception:
            pass
        try:
            delete_fn = getattr(self.browser_manager, "delete_profile", None)
            if callable(delete_fn):
                delete_fn(pid)
            elif hasattr(self, "api") and hasattr(self.api, "delete_profile"):
                self.api.delete_profile(pid)
        except Exception:
            pass
        with self._profiles_lock:
            self._profiles_by_id.pop(pid, None)
        try:
            if hasattr(self.profile_snapshot_store, "remove"):
                self.profile_snapshot_store.remove(pid)
        except Exception:
            pass
        try:
            if hasattr(self.runtime_config, "remove"):
                self.runtime_config.remove(pid)
        except Exception:
            pass
        try:
            self.group_store.assign_profile(pid, None)
        except Exception:
            pass
        return self.registry.remove(pid)

    def delete_accounts(self, profile_ids: Iterable[str]) -> int:
        count = 0
        for pid in list(profile_ids):
            if self.delete_account(pid):
                count += 1
        return count

    # ------------------ Group Management & Serial Rotation ------------------

    def list_profile_groups(self, records: list[AccountRow] | None = None) -> list[dict[str, Any]]:
        """按同节点 IP 自动聚类与用户自定义分组双模输出分组结构。"""
        if records is None:
            records = self.list_accounts()
        return self.group_store.cluster_accounts(records)

    def list_custom_groups(self) -> list[GroupRecord]:
        return self.group_store.list_custom_groups()

    def create_custom_group(self, name: str) -> GroupRecord:
        return self.group_store.create_group(name)

    def delete_custom_group(self, group_id: str) -> bool:
        self.rotation_manager.stop_rotation(group_id)
        return self.group_store.delete_group(group_id)

    def rename_custom_group(self, group_id: str, new_name: str) -> bool:
        return self.group_store.rename_group(group_id, new_name)

    def assign_profile_to_group(self, profile_id: str, group_id: str | None) -> None:
        self.group_store.assign_profile(profile_id, group_id)

    def batch_assign_profiles_to_group(self, profile_ids: list[str], group_id: str | None) -> None:
        self.group_store.batch_assign_profiles(profile_ids, group_id)

    def is_group_collapsed(self, group_id: str) -> bool:
        return self.group_store.is_group_collapsed(group_id)

    def set_group_collapsed(self, group_id: str, collapsed: bool) -> None:
        self.group_store.set_group_collapsed(group_id, collapsed)

    def set_all_groups_collapsed(self, group_ids: list[str], collapsed: bool) -> None:
        self.group_store.set_all_collapsed(group_ids, collapsed)

    def start_group_rotation(
        self,
        group_id: str,
        group_name: str = "",
        profile_ids: list[str] | None = None,
        on_status_change: Any = None,
    ) -> dict[str, Any]:
        """为指定分组（同一 IP / 节点 / 自定义组）启动防风控单批次串行轮换调度。"""
        if profile_ids is None:
            clusters = self.list_profile_groups()
            for g in clusters:
                if g["group_id"] == group_id:
                    profile_ids = [getattr(r, "profile_id", "") for r in g["records"] if getattr(r, "profile_id", "")]
                    if not group_name:
                        group_name = g["name"]
                    break
        if not profile_ids:
            raise ValueError(f"分组【{group_name or group_id}】名下没有任何可用账号")
        return self.rotation_manager.start_rotation(
            group_id=group_id,
            group_name=group_name or group_id,
            profile_ids=profile_ids,
            on_status_change=on_status_change,
        )

    def stop_group_rotation(self, group_id: str) -> None:
        self.rotation_manager.stop_rotation(group_id)

    def is_group_rotating(self, group_id: str) -> bool:
        return self.rotation_manager.is_group_running(group_id)

    def get_group_rotation_status(self, group_id: str) -> dict[str, Any]:
        return self.rotation_manager.get_status(group_id)

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
        if "retweet_ratio" in normalized:
            value = normalized["retweet_ratio"]
            if isinstance(value, bool):
                raise ValueError("retweet_ratio must be a number between 0.0 and 1.0")
            try:
                ratio = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("retweet_ratio must be a number between 0.0 and 1.0") from exc
            if not 0.0 <= ratio <= 1.0:
                raise ValueError("retweet_ratio must be a number between 0.0 and 1.0")
            normalized["retweet_ratio"] = ratio

        if "outreach_mode" in normalized:
            mode_str = str(normalized["outreach_mode"]).strip().lower()
            normalized["outreach_mode"] = mode_str if mode_str in {"keyword", "keyword_chain", "network_hop", "target_followers"} else "keyword"
        if "target_creators" in normalized:
            normalized["target_creators"] = clean_target_creators(normalized["target_creators"])
        if "max_followers_per_target" in normalized:
            try:
                normalized["max_followers_per_target"] = max(1, min(10000, int(normalized["max_followers_per_target"])))
            except (TypeError, ValueError):
                normalized["max_followers_per_target"] = 50
        if "action_min_delay" in normalized:
            try:
                normalized["action_min_delay"] = max(1.0, min(60.0, float(normalized["action_min_delay"])))
            except (TypeError, ValueError):
                normalized["action_min_delay"] = 4.0
        if "action_max_delay" in normalized:
            try:
                normalized["action_max_delay"] = max(1.0, min(120.0, float(normalized["action_max_delay"])))
            except (TypeError, ValueError):
                normalized["action_max_delay"] = 8.0
        if float(normalized.get("action_min_delay", 4.0)) > float(normalized.get("action_max_delay", 8.0)):
            normalized["action_min_delay"], normalized["action_max_delay"] = normalized["action_max_delay"], normalized["action_min_delay"]
        if "blacklist_words" in normalized:
            normalized["blacklist_words"] = parse_blacklist_input(normalized["blacklist_words"])
        if "check_options" in normalized and isinstance(normalized["check_options"], dict):
            normalized["check_options"] = {
                "check_name": bool(normalized["check_options"].get("check_name", True)),
                "check_bio": bool(normalized["check_options"].get("check_bio", True)),
                "check_tweets": bool(normalized["check_options"].get("check_tweets", True)),
            }
        if "execution_preset" in normalized:
            preset_str = str(normalized["execution_preset"]).strip().lower()
            normalized["execution_preset"] = preset_str if preset_str in {"safe", "turbo"} else "safe"
        if "batch_interval_minutes" in normalized:
            try:
                b_interval = int(normalized["batch_interval_minutes"])
            except (TypeError, ValueError) as exc:
                raise ValueError("batch_interval_minutes must be a positive whole number") from exc
            normalized["batch_interval_minutes"] = max(1, min(1440, b_interval))
        if "batch_jitter_enabled" in normalized:
            normalized["batch_jitter_enabled"] = parse_bool(normalized["batch_jitter_enabled"])
        if "smart_schedule_enabled" in normalized:
            normalized["smart_schedule_enabled"] = parse_bool(normalized["smart_schedule_enabled"])
        else:
            normalized["smart_schedule_enabled"] = True
        if "periodic_search_refresh_enabled" in normalized:
            normalized["periodic_search_refresh_enabled"] = parse_bool(normalized["periodic_search_refresh_enabled"])
        if "search_refresh_interval_minutes" in normalized:
            try:
                normalized["search_refresh_interval_minutes"] = max(1, min(60, int(normalized["search_refresh_interval_minutes"])))
            except (TypeError, ValueError):
                normalized["search_refresh_interval_minutes"] = 5
        if "authenticity_learning_enabled" in normalized:
            normalized["authenticity_learning_enabled"] = parse_bool(normalized["authenticity_learning_enabled"])
        if "chain_hop_enabled" in normalized:
            normalized["chain_hop_enabled"] = parse_bool(normalized["chain_hop_enabled"])
        if "chain_hop_ratio" in normalized:
            try:
                c_ratio = float(normalized["chain_hop_ratio"])
            except (TypeError, ValueError) as exc:
                raise ValueError("chain_hop_ratio must be a number between 0.0 and 1.0") from exc
            normalized["chain_hop_ratio"] = max(0.0, min(1.0, c_ratio))
        if "max_chain_depth" in normalized:
            try:
                c_depth = int(normalized["max_chain_depth"])
            except (TypeError, ValueError) as exc:
                raise ValueError("max_chain_depth must be an integer between 0 and 10") from exc
            normalized["max_chain_depth"] = max(0, min(10, c_depth))
        if "hesitation_skip_ratio" in normalized:
            try:
                h_ratio = float(normalized["hesitation_skip_ratio"])
            except (TypeError, ValueError) as exc:
                raise ValueError("hesitation_skip_ratio must be a number between 0.0 and 1.0") from exc
            normalized["hesitation_skip_ratio"] = max(0.0, min(1.0, h_ratio))

        for action in ("like", "follow", "reply", "bookmark", "retweet"):
            enabled_key = f"allow_{action}"
            if enabled_key in normalized:
                normalized[enabled_key] = parse_bool(normalized[enabled_key])
            limit_key = f"daily_{action}s_limit"
            if action == "reply":
                limit_key = "daily_replies_limit"
            if limit_key in normalized:
                try:
                    limit = int(normalized[limit_key])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{limit_key} must be a whole number") from exc
                if not 0 <= limit <= 100_000:
                    raise ValueError(f"{limit_key} must be between 0 and 100000")
                normalized[limit_key] = limit
        if "dry_run" in normalized:
            normalized["dry_run"] = parse_bool(normalized["dry_run"])
        if "cloud_dedup_enabled" in normalized:
            normalized["cloud_dedup_enabled"] = parse_bool(normalized["cloud_dedup_enabled"])
        if "cloud_dedup_studio_token" in normalized:
            normalized["cloud_dedup_studio_token"] = str(normalized["cloud_dedup_studio_token"] or "").strip()
        if "studio_token" in normalized:
            normalized["studio_token"] = str(normalized["studio_token"] or "").strip()
        if "cdp_timezone_override_enabled" in normalized:
            normalized["cdp_timezone_override_enabled"] = parse_bool(normalized["cdp_timezone_override_enabled"])
        if "cdp_override_timezone" in normalized:
            normalized["cdp_override_timezone"] = str(normalized["cdp_override_timezone"] or "auto").strip()
        if "cdp_geolocation_override_enabled" in normalized:
            normalized["cdp_geolocation_override_enabled"] = parse_bool(normalized["cdp_geolocation_override_enabled"])
        if "graphql_risk_pause_enabled" in normalized:
            normalized["graphql_risk_pause_enabled"] = parse_bool(normalized["graphql_risk_pause_enabled"])
        if "natural_roaming_enabled" in normalized:
            normalized["natural_roaming_enabled"] = parse_bool(normalized["natural_roaming_enabled"])
        if "pre_click_guard_enabled" in normalized:
            normalized["pre_click_guard_enabled"] = parse_bool(normalized["pre_click_guard_enabled"])
        if "search_pagination_refresh_enabled" in normalized:
            normalized["search_pagination_refresh_enabled"] = parse_bool(normalized["search_pagination_refresh_enabled"])
        if "graphql_scout_filter_enabled" in normalized:
            normalized["graphql_scout_filter_enabled"] = parse_bool(normalized["graphql_scout_filter_enabled"])
        if "smart_newbie_recognition_enabled" in normalized:
            normalized["smart_newbie_recognition_enabled"] = parse_bool(normalized["smart_newbie_recognition_enabled"])
        if "block_video_streams" in normalized:
            normalized["block_video_streams"] = parse_bool(normalized["block_video_streams"])
        if "max_harvest_per_seed" in normalized:
            try:
                normalized["max_harvest_per_seed"] = max(1, min(100, int(normalized["max_harvest_per_seed"])))
            except (TypeError, ValueError):
                normalized["max_harvest_per_seed"] = 10
        if "in_situ_rest_enabled" in normalized:
            normalized["in_situ_rest_enabled"] = parse_bool(normalized["in_situ_rest_enabled"])
        return normalized

    def get_profile_task_config(self, profile_id: str) -> dict[str, Any]:
        snapshot = self.runtime_config.snapshot(str(profile_id))
        if isinstance(snapshot, dict):
            active = dict(snapshot.get("active") or {}) if isinstance(snapshot.get("active"), dict) else {}
            next_run = dict(snapshot.get("next_run") or {}) if isinstance(snapshot.get("next_run"), dict) else {}
            merged = dict(snapshot)
            merged.update(next_run)
            merged.update(active)
            merged["active"] = active
            merged["next_run"] = next_run
            return merged
        return {}

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

        with _LIFECYCLE_LOCK:
            if _STOP_EVENT.is_set():
                raise RuntimeError("控制器正在关闭，不能启动自动化任务")
            if profile_id in _RUN_STATES:
                self.logger.warning(f"Profile [{profile_id}] 已经在运行自动化中，拦截重复启动请求。")
                return {
                    "status": "SKIPPED",
                    "profile_id": profile_id,
                    "message": "当前档案已有自动化正在运行，跳过重复启动",
                }
            run_id = uuid4().hex
            run_state = RunState(profile_id=profile_id, run_id=run_id, phase="STARTING")
            _RUN_STATES[profile_id] = run_state

        try:
            saved = saved or self.set_profile_task_config(profile_id, config)
            safety_guard, guarded_config, decision = self.automation_safety.begin_run(
                profile_id,
                run_id,
                config,
            )
            if safety_guard is None or not decision.allowed:
                self.logger.warning("[自动化保护] profile=%s state=NEEDS_ATTENTION reason=%s", profile_id, decision.reason)
                _clear_automation_state(profile_id, run_id)
                return {
                    "status": "NEEDS_ATTENTION",
                    "profile_id": profile_id,
                    "reason": decision.reason,
                    "safety": self.automation_safety.snapshot(profile_id),
                    "message": "账号已暂停自动化，请在控制中心人工处理后恢复。",
                }

            with _LIFECYCLE_LOCK:
                if _STOP_EVENT.is_set():
                    raise RuntimeError("控制器正在关闭，任务已终止")
                run_state.guard = safety_guard

            engine_id = str(config.get("engine_id") or "default").strip() or "default"
            engine_name = str(config.get("engine_name") or engine_id).strip()
            engine_client = getattr(self.agent_service, "server_client", None) if self.agent_service is not None else None
            if engine_client is not None and self.server_agent_status().get("server") == "ONLINE":
                permitted_engine_ids = {
                    str(item.get("engine_id") or "")
                    for item in self.list_automation_engines()
                }
                if engine_id not in permitted_engine_ids:
                    raise RuntimeError(f"当前运行端未获授权使用自动化引擎“{engine_name}”。请联系管理员分配脚本权限。")
            cached_engine = (
                get_cached_automation_engine_class(self.settings.engine_cache_dir, engine_id)
                if self.settings.engine_cache_dir
                else None
            )
            if engine_id == "default":
                cached_cfg = getattr(cached_engine, "AutomationConfig", None)
                if cached_engine is None or cached_cfg is None or not hasattr(cached_cfg, "smart_schedule_enabled"):
                    cached_engine = XAutomationEngine
            if cached_engine is None and engine_id != "default":
                if engine_client is None or (self.server_agent_status().get("server") != "ONLINE"):
                    raise RuntimeError(f"自定义自动化引擎“{engine_name}”尚未下载，当前服务器不可用。")
                sync_engine_from_server(engine_client, self.settings.engine_cache_dir, engine_id)
            started = self._start_profile_serialized(profile_id)
            
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
            raw_username = str(getattr(account, "x_username", "") or "").strip().lstrip("@")
            if raw_username and "account_username" not in guarded_config:
                guarded_config["account_username"] = raw_username

            acc_snap = self.profile_snapshot_store.get(profile_id) or {}
            snap_fans = _optional_count(_snapshot_value(acc_snap, "followers_count", "followersCount", "followers"))
            snap_ing = _optional_count(_snapshot_value(acc_snap, "following_count", "followingCount", "following"))
            if snap_fans is not None and "initial_followers" not in guarded_config:
                guarded_config["initial_followers"] = snap_fans
            if snap_ing is not None and "initial_following" not in guarded_config:
                guarded_config["initial_following"] = snap_ing

            # 继承全局工作室协同码 (Studio Token) 设置（若单个账号未单独覆盖）
            global_st = self.get_studio_token_config()
            if global_st.get("enabled") and global_st.get("studio_token"):
                if "cloud_dedup_enabled" not in guarded_config:
                    guarded_config["cloud_dedup_enabled"] = True
                if not guarded_config.get("cloud_dedup_studio_token") and not guarded_config.get("studio_token"):
                    guarded_config["cloud_dedup_studio_token"] = global_st["studio_token"]
                    guarded_config["studio_token"] = global_st["studio_token"]
                if not guarded_config.get("cloud_dedup_server_url"):
                    guarded_config["cloud_dedup_server_url"] = global_st.get("server_url") or "https://api.jaycwl.org"

            # 继承全局黑名单词库与全字段过滤开关设置
            global_bl = self.get_blacklist_config()
            bl_enabled = guarded_config.get("blacklist_filter_enabled")
            if bl_enabled is None:
                bl_enabled = bool(global_bl.get("enabled", True))
            guarded_config["blacklist_filter_enabled"] = bool(bl_enabled)

            if guarded_config["blacklist_filter_enabled"]:
                if not guarded_config.get("blacklist_words"):
                    guarded_config["blacklist_words"] = global_bl.get("blacklist_words") or list(DEFAULT_BLACKLIST_WORDS)
                if "check_options" not in guarded_config or not guarded_config["check_options"]:
                    guarded_config["check_options"] = global_bl.get("check_options") or dict(DEFAULT_CHECK_OPTIONS)
            else:
                guarded_config["blacklist_words"] = []

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
            with _LIFECYCLE_LOCK:
                if _STOP_EVENT.is_set():
                    raise RuntimeError("控制器正在关闭，放弃提交 Worker 线程")
                future = _get_automation_executor().submit(
                    _run_engine_in_thread,
                    cdp_url,
                    self.logger,
                    guarded_config,
                    str(self.settings.engine_cache_dir),
                    engine_id,
                    report_progress,
                    safety_guard,
                    str(self.settings.log_file.parent / "automation_artifacts"),
                    profile_id=profile_id,
                    run_id=run_id,
                )
                run_state.future = future

            future.add_done_callback(
                lambda completed: self._automation_result_finished(
                    completed,
                    run_id=run_id,
                    profile_id=profile_id,
                    x_account_id=x_account_id,
                    account_tag=account_tag,
                    started_at=started_at,
                    safety_guard=safety_guard,
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
            _clear_automation_state(profile_id, run_id)
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
        if callable(self._on_schedule_event):
            try:
                self._on_schedule_event(profile_id, "WAITING_SCHEDULE", {"scheduled_for": scheduled_for.isoformat(timespec="minutes")})
            except Exception:
                pass
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
            if _STOP_EVENT.is_set():
                return {}
            previous = self._scheduled_timers.pop(profile_id, None)
            if previous is not None:
                try:
                    previous.cancel()
                except Exception:
                    pass
            self._scheduled_tokens[profile_id] = token
            self._scheduled_timers[profile_id] = timer
            saved = self.runtime_config.update(
                profile_id,
                {
                    **config,
                    "schedule_status": state,
                    "schedule_next_run": scheduled_for.isoformat(timespec="minutes"),
                },
                mode="HOT_UPDATE",
            )
            timer.start()
        return saved

    def _claim_scheduled_execution(
        self,
        profile_id: str,
        expected_token: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """原子认领到期的定时任务执行权。
        保证无论由精确 Timer 还是看门狗 Watchdog 触发，同一个档案在同一时刻绝对只有单一线程能够获得执行权。
        """
        profile_id = str(profile_id)
        with self._schedule_lock:
            if _STOP_EVENT.is_set():
                return False, {}

            if expected_token is not None:
                current_token = self._scheduled_tokens.get(profile_id)
                if current_token != expected_token:
                    return False, {}

            snapshot = self.runtime_config.snapshot(profile_id) if hasattr(self.runtime_config, "snapshot") else {}
            active = snapshot.get("active") if isinstance(snapshot, dict) else {}
            if not isinstance(active, dict) or active.get("schedule_mode") != "scheduled":
                return False, {}

            current_status = str(active.get("schedule_status") or "")
            if current_status == "TRIGGERING":
                return False, {}

            timer = self._scheduled_timers.pop(profile_id, None)
            self._scheduled_tokens.pop(profile_id, None)
            if timer is not None:
                try:
                    timer.cancel()
                except Exception:
                    pass

            self.runtime_config.update(
                profile_id,
                {"schedule_status": "TRIGGERING"},
                mode="HOT_UPDATE",
            )
            return True, dict(active)

    def _scheduled_task_due(self, profile_id: str, token: str, config: dict[str, Any]) -> None:
        claimed, active_cfg = self._claim_scheduled_execution(profile_id, expected_token=token)
        if not claimed:
            return
        self._execute_scheduled_task(profile_id, active_cfg)

    def _postpone_scheduled_task(
        self,
        profile_id: str,
        config: dict[str, Any],
        *,
        reason: str = "PROFILE_BUSY",
        delay_seconds: int = 60,
    ) -> None:
        tz = _schedule_timezone(config.get("schedule_timezone", "Asia/Shanghai"))
        retry_at = datetime.now(tz) + timedelta(seconds=delay_seconds)
        self.logger.info(
            "[自动化调度] profile=%s state=DELAYED_PROFILE_BUSY reason=%s retry_at=%s",
            profile_id,
            reason,
            retry_at.isoformat(timespec="minutes"),
        )
        self._arm_scheduled_timer(
            profile_id,
            config,
            retry_at,
            state="DELAYED_PROFILE_BUSY",
        )
        if callable(self._on_schedule_event):
            try:
                self._on_schedule_event(
                    profile_id,
                    "DELAYED_PROFILE_BUSY",
                    {"retry_at": retry_at.isoformat(timespec="minutes"), "reason": reason},
                )
            except Exception:
                pass

    def _execute_scheduled_task(self, profile_id: str, config: dict[str, Any]) -> None:
        profile_id = str(profile_id)
        self.logger.info("[自动化调度] ⏰ profile=%s 到点触发定时任务", profile_id)
        if callable(self._on_schedule_event):
            try:
                self._on_schedule_event(profile_id, "TRIGGERED", {})
            except Exception:
                pass

        with _RUNNING_LOCK:
            profile_busy = profile_id in _RUNNING_PROFILES
        with _LIFECYCLE_LOCK:
            profile_busy = profile_busy or (profile_id in _RUN_STATES)
        if profile_busy:
            self._postpone_scheduled_task(profile_id, config, reason="PROFILE_BUSY", delay_seconds=60)
            return

        try:
            self._require_capability(
                "automation.run",
                "定时任务到点，但当前授权不允许启动自动化任务。",
            )
        except Exception as auth_err:
            self.logger.error("[自动化调度] profile=%s 授权检查未通过: %s", profile_id, auth_err)
            self.runtime_config.update(
                profile_id,
                {"schedule_status": "FAILED", "schedule_next_run": ""},
                mode="HOT_UPDATE",
            )
            if callable(self._on_schedule_event):
                try:
                    self._on_schedule_event(profile_id, "FAILED", {"error": str(auth_err)})
                except Exception:
                    pass
            return

        max_start_retries = 3
        last_exc: Exception | None = None
        started_result: dict[str, Any] | None = None

        for attempt in range(1, max_start_retries + 1):
            if _STOP_EVENT.is_set():
                return
            try:
                if hasattr(self, "api") and hasattr(self.api, "health"):
                    try:
                        h = self.api.health()
                        if not (isinstance(h, dict) and (h.get("status") == "ok" or h.get("code") == 0 or h.get("success"))):
                            time.sleep(1.5)
                    except Exception:
                        time.sleep(1.5)

                result = self._start_automation_now(
                    profile_id,
                    config,
                    saved=self.runtime_config.snapshot(profile_id),
                )
                if result.get("status") == "SKIPPED":
                    self._postpone_scheduled_task(profile_id, config, reason="PROFILE_BUSY", delay_seconds=60)
                    return
                started_result = result
                break
            except Exception as exc:
                last_exc = exc
                self.logger.warning(
                    "[自动化调度] profile=%s 启动尝试 (%d/%d) 遇到异常: %s",
                    profile_id,
                    attempt,
                    max_start_retries,
                    exc,
                )
                if attempt < max_start_retries:
                    time.sleep(2.0)

        if started_result is not None:
            self.logger.info(
                "[自动化调度] profile=%s state=STARTED run_id=%s",
                profile_id,
                started_result.get("run_id", ""),
            )
            if callable(self._on_schedule_event):
                try:
                    self._on_schedule_event(profile_id, "STARTED", started_result)
                except Exception:
                    pass

            if config.get("schedule_type") == "daily":
                tz = _schedule_timezone(config.get("schedule_timezone", "Asia/Shanghai"))
                next_run = _next_schedule_at(config, now=datetime.now(tz) + timedelta(minutes=1))
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
        else:
            current_fail_count = int(config.get("_schedule_fail_count") or 0) + 1
            config["_schedule_fail_count"] = current_fail_count
            if current_fail_count <= 3 and not _STOP_EVENT.is_set():
                self.logger.info(
                    "[自动化调度] profile=%s 启动暂时遇阻，延后 60 秒自愈重试 (第 %d/3 轮)...",
                    profile_id,
                    current_fail_count,
                )
                self._postpone_scheduled_task(
                    profile_id,
                    config,
                    reason="START_RETRY",
                    delay_seconds=60,
                )
            else:
                self.logger.error(
                    "[自动化调度] profile=%s state=FAILED error=%s",
                    profile_id,
                    last_exc,
                )
                self.runtime_config.update(
                    profile_id,
                    {"schedule_status": "FAILED", "schedule_next_run": ""},
                    mode="HOT_UPDATE",
                )
                if callable(self._on_schedule_event):
                    try:
                        self._on_schedule_event(profile_id, "FAILED", {"error": str(last_exc)})
                    except Exception:
                        pass
                if config.get("schedule_type") == "daily" and not _STOP_EVENT.is_set():
                    tz = _schedule_timezone(config.get("schedule_timezone", "Asia/Shanghai"))
                    next_run = _next_schedule_at(config, now=datetime.now(tz) + timedelta(minutes=1))
                    self._arm_scheduled_timer(
                        profile_id,
                        config,
                        next_run,
                        state="WAITING_SCHEDULE",
                    )

    def cancel_scheduled_task(self, profile_id: str) -> bool:
        """用户显式取消指定档案的定时任务"""
        cancelled = self._cancel_scheduled_task(profile_id, state="CANCELLED_BY_USER")
        if cancelled and callable(self._on_schedule_event):
            try:
                self._on_schedule_event(profile_id, "CANCELLED_BY_USER", {})
            except Exception:
                pass
        return cancelled

    def _cancel_scheduled_task(self, profile_id: str, *, state: str) -> bool:
        profile_id = str(profile_id)
        with self._schedule_lock:
            timer = self._scheduled_timers.pop(profile_id, None)
            self._scheduled_tokens.pop(profile_id, None)
            if timer is not None:
                try:
                    timer.cancel()
                except Exception:
                    pass
            snapshot = self.runtime_config.snapshot(profile_id) if hasattr(self.runtime_config, "snapshot") else {}
            active = snapshot.get("active") if isinstance(snapshot, dict) else {}
            curr_status = str(active.get("schedule_status") or "") if isinstance(active, dict) else ""
            was_scheduled = (timer is not None) or (curr_status in {"WAITING_SCHEDULE", "DELAYED_PROFILE_BUSY", "TRIGGERING"})
            if not was_scheduled:
                return False
            self.runtime_config.update(
                profile_id,
                {"schedule_status": state, "schedule_next_run": ""},
                mode="HOT_UPDATE",
            )
        self.logger.info("[自动化调度] profile=%s state=%s", profile_id, state)
        return True

    def check_scheduled_tasks_tick(self) -> list[str]:
        """看门狗心跳巡检：周期性检查 runtime_config 中所有定时档案。
        若当前时间已达到或超过执行时刻，自动执行漏跑或被挂起的任务。
        返回本次心跳唤醒触发的 profile_id 列表。
        """
        if _STOP_EVENT.is_set():
            return []
        triggered: list[str] = []
        if not hasattr(self.runtime_config, "all"):
            return triggered

        configs = self.runtime_config.all()
        for profile_id, snapshot in configs.items():
            active = snapshot.get("active") if isinstance(snapshot, dict) else None
            if not isinstance(active, dict) or active.get("schedule_mode") != "scheduled":
                continue
            schedule_status = str(active.get("schedule_status") or "")
            if schedule_status not in {"WAITING_SCHEDULE", "DELAYED_PROFILE_BUSY"}:
                continue

            with _LIFECYCLE_LOCK:
                if profile_id in _RUN_STATES:
                    continue

            # 优先从已持久化的准确下次执行时间读取
            scheduled_for = None
            raw_next_run = str(active.get("schedule_next_run") or "").strip()
            tz = _schedule_timezone(str(active.get("schedule_timezone") or "Asia/Shanghai"))
            if raw_next_run:
                try:
                    scheduled_for = datetime.fromisoformat(raw_next_run)
                    if scheduled_for.tzinfo is None:
                        scheduled_for = scheduled_for.replace(tzinfo=tz)
                    else:
                        scheduled_for = scheduled_for.astimezone(tz)
                except Exception:
                    scheduled_for = None

            if scheduled_for is None:
                try:
                    scheduled_for = _next_schedule_at(active)
                except Exception as exc:
                    self.logger.warning("[看门狗调度] 解析 profile=%s 定时配置失败: %s", profile_id, exc)
                    continue

            current_time = datetime.now(scheduled_for.tzinfo)
            if current_time >= scheduled_for:
                claimed, active_cfg = self._claim_scheduled_execution(profile_id)
                if not claimed:
                    continue

                self.logger.info(
                    "[看门狗调度] 捕获到期任务 profile=%s scheduled_for=%s, 立即唤醒执行",
                    profile_id,
                    scheduled_for.isoformat(timespec="minutes"),
                )
                triggered.append(profile_id)
                _get_automation_executor().submit(self._execute_scheduled_task, profile_id, active_cfg)

        return triggered

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
            if hasattr(client, "list_engines_with_policy"):
                response = client.list_engines_with_policy()
                items = response.get("items", []) if isinstance(response, dict) else []
                authorization_enforced = bool(isinstance(response, dict) and response.get("authorization_enforced"))
            else:
                items = client.list_engines()
                authorization_enforced = False
        except Exception:
            return local or [default]
        normalized = [item for item in items if isinstance(item, dict) and item.get("enabled", True)]
        by_id = {str(item.get("engine_id") or ""): item for item in normalized}
        if authorization_enforced:
            return [item | {"authorization_enforced": True} for item in by_id.values() if item.get("engine_id")]
        for item in local:
            eid = str(item.get("engine_id") or "")
            if eid != "default":
                by_id.setdefault(eid, item)
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
        safety_guard: AutomationRunGuard | None = None,
    ) -> None:
        try:
            self._automation_result_finished_impl(
                future,
                run_id=run_id,
                profile_id=profile_id,
                x_account_id=x_account_id,
                account_tag=account_tag,
                started_at=started_at,
                safety_guard=safety_guard,
            )
        finally:
            _clear_automation_state(profile_id, run_id)

    def _automation_result_finished_impl(
        self,
        future,
        *,
        run_id: str,
        profile_id: str,
        x_account_id: str,
        account_tag: str,
        started_at: str,
        safety_guard: AutomationRunGuard | None = None,
    ) -> None:
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
        if result.get("snapshot_path"):
            self.logger.warning(
                "[自动化风险截图] profile=%s path=%s",
                profile_id,
                result["snapshot_path"],
            )
        try:
            self.automation_safety.record_result(
                profile_id,
                run_id,
                result,
                action_records=safety_guard.action_records if safety_guard is not None else 0,
            )
            runtime_state = "NEEDS_ATTENTION" if str(result.get("status") or "").upper() in {
                "CHALLENGE_REQUIRED", "NOT_LOGGED_IN", "PROXY_DISCONNECTED"
            } else str(result.get("status") or "FINISHED").upper()
            self.runtime_config.update(profile_id, {"safety_status": runtime_state}, mode="HOT_UPDATE")
        except Exception as exc:
            self.logger.info("Automation safety persistence deferred: %s", exc)

        # 🛡️ 硬件级防裸连泄露：若发生代理掉线 (PROXY_DISCONNECTED)，立刻强制终止浏览器进程，杜绝真实 IP 暴露
        if str(result.get("status") or "").upper() == "PROXY_DISCONNECTED":
            try:
                self.logger.warning("[防风控断电保护] profile=%s 代理连接中断，强制关闭浏览器进程以防真实 IP 泄露！", profile_id)
                self.stop_profile(profile_id)
            except Exception as kill_err:
                self.logger.error("强制关闭代理中断浏览器失败: %s", kill_err)
        persisted = {}
        try:
            persisted = self.automation_statistics.record_result(
                run_id=run_id,
                profile_id=profile_id,
                x_account_id=x_account_id,
                account_tag=account_tag,
                started_at=started_at,
                result=result,
            ) or {}
            if self.agent_service is not None:
                self.agent_service.flush_automation_metrics()
        except Exception as exc:
            self.logger.info("Automation statistics sync deferred: %s", exc)

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

        # 优先从 result 读取，若 result 缺失（如取消中止）则从持久化汇总兜底
        follows_cnt = int(result.get("follows") if result.get("follows") is not None else persisted.get("follows", 0))
        likes_cnt = int(result.get("likes") if result.get("likes") is not None else persisted.get("likes", 0))
        scanned_cnt = int(result.get("scanned_posts") if result.get("scanned_posts") is not None else persisted.get("scanned_posts", 0))
        initial_fans = int(result.get("initial_followers") or persisted.get("own_followers") or 0)
        final_fans = int(result.get("final_followers") or persisted.get("own_followers") or 0)
        initial_ing = int(result.get("initial_following") or persisted.get("own_following") or 0)
        final_ing = int(result.get("final_following") or persisted.get("own_following") or 0)

        # 档案快照多级兜底保障：若因中止或探查延迟导致资产为0，从本地档案快照补齐
        acc_handle = str(result.get("username") or "").strip().lstrip("@")
        try:
            acc_snap = self.profile_snapshot_store.get(profile_id) or {}
            snap_fans = _optional_count(_snapshot_value(acc_snap, "followers_count", "followersCount", "followers"))
            snap_ing = _optional_count(_snapshot_value(acc_snap, "following_count", "followingCount", "following"))
            if not acc_handle:
                acc_handle = str(_snapshot_value(acc_snap, "x_username", "username", "handle") or "").strip().lstrip("@")

            if final_fans <= 0 and snap_fans is not None and snap_fans > 0:
                final_fans = snap_fans
                if initial_fans <= 0:
                    initial_fans = snap_fans

            if final_ing <= 0 and snap_ing is not None and snap_ing > 0:
                final_ing = snap_ing
                if initial_ing <= 0:
                    initial_ing = max(0, snap_ing - follows_cnt)
            elif initial_ing > 0 and final_ing == initial_ing and follows_cnt > 0:
                final_ing = initial_ing + follows_cnt
        except Exception as snap_err:
            self.logger.debug("从本地快照兜底资产数据异常: %s", snap_err)

        # 记录账号当前最新运行战报快照
        self._account_snapshots[profile_id] = {
            "name": account_tag or profile_id,
            "handle": acc_handle,
            "follows": follows_cnt,
            "likes": likes_cnt,
            "scanned": scanned_cnt,
            "initial_followers": initial_fans,
            "final_followers": final_fans,
            "initial_following": initial_ing,
            "final_following": final_ing,
            "status": str(result.get("status") or "FINISHED"),
        }

        # 若达到单日上限或成功完成，或者人工中止但已有成果产出，自动触发 Telegram 单账号战报通知
        res_status = str(result.get("status") or "").upper()
        is_normal_finish = res_status in {"SUCCESS", "PARTIAL_SUCCESS", "COMPLETED"}
        is_cancelled_with_yield = (res_status == "CANCELLED" and (follows_cnt > 0 or likes_cnt > 0))

        if is_normal_finish or is_cancelled_with_yield:
            report_result = dict(result)
            report_result["username"] = acc_handle
            report_result["follows"] = follows_cnt
            report_result["likes"] = likes_cnt
            report_result["scanned_posts"] = scanned_cnt
            report_result["initial_followers"] = initial_fans
            report_result["final_followers"] = final_fans
            report_result["initial_following"] = initial_ing
            report_result["final_following"] = final_ing
            report_result["status"] = res_status
            self._dispatch_telegram_account_report(profile_id, account_tag, report_result, started_at)

        # 针对异常、网络断开或风控终端状态自动关闭回收底层浏览器进程，彻底防止僵尸 Chromium 进程常驻与内存泄漏
        with _LIFECYCLE_LOCK:
            state = _RUN_STATES.get(profile_id)
            is_current_run = (state is None or not state.run_id or state.run_id == run_id)
        if is_current_run and res_status in {"PROXY_DISCONNECTED", "CHALLENGE_REQUIRED", "ERROR", "FAILED"}:
            try:
                self.browser_manager.stop_profile(profile_id)
                self.logger.info("[浏览器回收] profile=%s 因异常状态(%s)已自动回收并关闭底层浏览器进程", profile_id, res_status)
            except Exception as exc:
                self.logger.info("Browser cleanup deferred for profile=%s: %s", profile_id, exc)

    def get_account_snapshot(self, profile_id: str) -> dict[str, Any]:
        """获取指定账号的最新运行战报快照字典 (只读拷贝)."""
        return dict(self._account_snapshots.get(str(profile_id), {}))

    @staticmethod
    def _extract_cdp_url(payload: Any) -> str:
        if isinstance(payload, dict):
            # 1. 优先提取专属直连调试地址 (directDebugUrl)，彻底规避 19876 端口代理多开会话冲突
            for key in ("directDebugUrl", "direct_debug_url"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip() and "19876" not in value:
                    return value.strip()

            # 2. 优先提取该 Profile 独立的 Chromium debugPort (排除 19876 共享端口)
            for key in ("debugPort", "debug_port"):
                value = payload.get(key)
                if isinstance(value, int) and 1 <= value <= 65535 and value != 19876:
                    return f"http://127.0.0.1:{value}"

            # 3. 检查常规 cdpUrl，但若指向 19876 且当前存在独立 debugPort，则强行使用直连端口
            for key in ("cdpUrl", "cdp_url", "debuggerUrl", "debugger_url", "webSocketDebuggerUrl"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    if "19876" in value:
                        alt_port = payload.get("debugPort") or payload.get("debug_port")
                        if isinstance(alt_port, int) and 1 <= alt_port <= 65535 and alt_port != 19876:
                            return f"http://127.0.0.1:{alt_port}"
                        prof = payload.get("profile")
                        if isinstance(prof, dict):
                            prof_port = prof.get("debugPort") or prof.get("debug_port")
                            if isinstance(prof_port, int) and 1 <= prof_port <= 65535 and prof_port != 19876:
                                return f"http://127.0.0.1:{prof_port}"
                    else:
                        return value.strip()

            # 4. 其它备选独立端口字段 (排除 19876 共享代理端口)
            for key in ("port", "cdpPort", "cdp_port", "debuggerPort", "debugger_port"):
                value = payload.get(key)
                if isinstance(value, int) and 1 <= value <= 65535 and value != 19876:
                    return f"http://127.0.0.1:{value}"

            # 5. 递归遍历嵌套字典 (优先寻找非 19876 的独立端口)
            for value in payload.values():
                found = DesktopController._extract_cdp_url(value)
                if found and "19876" not in found:
                    return found

            # 6. 保底兜底：若实在没有任何独立端口（单开极端兼容老接口情况），最后才采纳 19876 端口
            for key in ("cdpUrl", "cdp_url", "debuggerUrl", "debugger_url", "webSocketDebuggerUrl"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in ("port", "cdpPort", "cdp_port"):
                value = payload.get(key)
                if isinstance(value, int) and 1 <= value <= 65535:
                    return f"http://127.0.0.1:{value}"

        elif isinstance(payload, list):
            for value in payload:
                found = DesktopController._extract_cdp_url(value)
                if found and "19876" not in found:
                    return found
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
        raw_by_account = dict(summary.get("by_account") or {})
        for key, values in automation.get("by_account", {}).items():
            merged = dict(raw_by_account.get(key) or {})
            merged.update(values)
            raw_by_account[key] = merged

        registered_records = self.registry.list() if hasattr(self, "registry") and hasattr(self.registry, "list") else []

        # 1. 建立基于唯一规范档案 profile_id 的账号统计（严格去重，避免别名被重复统计翻倍）
        canonical_stats: dict[str, dict[str, Any]] = {}
        alias_to_pid: dict[str, str] = {}
        pid_to_record: dict[str, Any] = {}
        for record in registered_records:
            pid = str(record.profile_id)
            pid_to_record[pid] = record
            alias_to_pid[pid] = pid
            for alias in (
                str(getattr(record, "profile_name", "") or "").strip(),
                str(getattr(record, "x_account_id", "") or "").strip(),
                str(getattr(record, "x_username", "") or "").strip(),
            ):
                if alias:
                    alias_to_pid[alias] = pid

        # 将 raw_by_account 中的别名记录安全归并到规范 profile_id
        for key, vals in raw_by_account.items():
            if not isinstance(vals, dict):
                continue
            canonical_pid = alias_to_pid.get(str(key), str(key))
            cur = canonical_stats.setdefault(canonical_pid, {"follows": 0, "likes": 0, "comments": 0, "processed_count": 0, "scanned_posts": 0})
            for field in ("follows", "likes", "comments", "processed_count", "scanned_posts"):
                cur[field] = max(int(cur.get(field, 0) or 0), int(vals.get(field, 0) or 0))

        # 实时合并来自 automation_safety 的今日动作计数 (防止运行中尚未入库导致统计为0)
        if hasattr(self, "automation_safety"):
            try:
                for pid in set(canonical_stats.keys()).union(pid_to_record.keys()):
                    daily = self.automation_safety.daily_totals(str(pid))
                    if any(daily.values()):
                        acc_stat = canonical_stats.setdefault(str(pid), {"follows": 0, "likes": 0, "comments": 0, "processed_count": 0, "scanned_posts": 0})
                        for k, v in daily.items():
                            acc_stat[k] = max(int(acc_stat.get(k, 0) or 0), int(v or 0))
            except Exception:
                pass

        # 确保所有已注册账号均在统计字典中
        for pid in pid_to_record:
            canonical_stats.setdefault(pid, {"follows": 0, "likes": 0, "comments": 0, "processed_count": 0, "scanned_posts": 0})

        # 2. 仅对唯一真实账号计算进度与累加全局大盘（绝不把别名二次累加）
        total_target = 0
        total_follows = 0
        total_likes = 0
        with _LIFECYCLE_LOCK:
            running_profile_ids = set(_RUN_STATES.keys())

        target_pids = set(pid_to_record.keys()) if pid_to_record else set(canonical_stats.keys())

        for pid_str in sorted(target_pids):
            acc_stat = canonical_stats.get(pid_str)
            if not isinstance(acc_stat, dict):
                continue
            cur_follows = int(acc_stat.get("follows", 0) or 0)
            cur_likes = int(acc_stat.get("likes", 0) or 0)
            cur_processed = int(acc_stat.get("processed_count", 0) or 0)

            is_running = pid_str in running_profile_ids
            has_records = (cur_follows > 0 or cur_likes > 0 or cur_processed > 0)

            # 未跑任务且当日尚无任何记录：不展示配额目标与进度，不计入大盘总目标
            if not is_running and not has_records:
                acc_stat["progress"] = ""
                acc_stat["progress_pct"] = 0
                acc_stat["target_val"] = 0
                acc_stat["target_follows"] = 0
                continue

            total_follows += cur_follows
            total_likes += cur_likes

            cfg = self.get_profile_task_config(pid_str) if hasattr(self, "get_profile_task_config") else {}
            active_cfg = cfg.get("active") if isinstance(cfg.get("active"), dict) else {}
            follows_limit = int(cfg.get("daily_follows_limit") or active_cfg.get("daily_follows_limit") or 0)
            daily_task_limit = int(cfg.get("daily_task_limit") or active_cfg.get("daily_task_limit") or 0)
            task_limit = int(cfg.get("task_limit") or cfg.get("batch_limit") or active_cfg.get("task_limit") or 0)

            # 目标判定优先级：根据输入的专项关注上限 -> 输入的每日任务总上限 -> 批次/单次上限 -> 默认兜底 100
            if follows_limit > 0:
                target_val = follows_limit
                current_val = cur_follows
            elif daily_task_limit > 0:
                target_val = daily_task_limit
                current_val = cur_follows
            elif task_limit > 0:
                target_val = task_limit
                current_val = cur_follows if cur_follows > 0 else (cur_processed or cur_likes)
            else:
                target_val = 100
                current_val = cur_follows

            pct = int(min(100, max(0, (current_val / target_val) * 100))) if target_val > 0 else 0
            if current_val > 0 and pct < 2:
                pct = 2
            progress_str = f"{current_val}/{target_val}"
            total_target += target_val

            acc_stat["progress"] = progress_str
            acc_stat["progress_pct"] = pct
            acc_stat["target_val"] = target_val
            acc_stat["target_follows"] = follows_limit or daily_task_limit or target_val

        # 3. 构造按账号查询映射（为兼容界面按别名快速检索，将规范数据同步挂载到别名键下）
        output_by_account: dict[str, dict[str, Any]] = dict(canonical_stats)
        for alias, pid in alias_to_pid.items():
            if alias not in output_by_account and pid in canonical_stats:
                output_by_account[alias] = canonical_stats[pid]

        summary["by_account"] = output_by_account
        summary["total_target"] = total_target
        summary["follows"] = total_follows
        summary["likes"] = total_likes
        for key in (
            "automation_runs",
            "processed_count",
            "comments",
            "scanned_posts",
        ):
            summary[key] = automation.get(key, 0)
        return summary

    def automation_safety_status(self, profile_id: str) -> dict[str, Any]:
        profile_id = str(profile_id)
        snapshot = self.automation_safety.snapshot(profile_id)
        with _LIFECYCLE_LOCK:
            state = _RUN_STATES.get(profile_id)
            control = state.guard if state else None
        snapshot["running"] = control is not None
        snapshot["paused"] = bool(control and control.is_paused())
        snapshot["cancel_requested"] = bool(control and control.is_cancelled())
        return snapshot

    def pause_automation_task(self, profile_id: str, reason: str = "管理员暂停") -> dict[str, Any]:
        profile_id = str(profile_id)
        with _LIFECYCLE_LOCK:
            state = _RUN_STATES.get(profile_id)
            control = state.guard if state else None
        if control is not None:
            control.pause()
        self.automation_safety.pause(profile_id, reason)
        self.runtime_config.update(profile_id, {"safety_status": "PAUSED"}, mode="HOT_UPDATE")
        return {"status": "PAUSED", "profile_id": profile_id, "running": control is not None}

    def resume_automation_task(self, profile_id: str) -> dict[str, Any]:
        profile_id = str(profile_id)
        with _LIFECYCLE_LOCK:
            state = _RUN_STATES.get(profile_id)
            control = state.guard if state else None
        if control is not None:
            control.resume()
        self.automation_safety.resume(profile_id)
        self.runtime_config.update(profile_id, {"safety_status": "READY"}, mode="HOT_UPDATE")
        return {"status": "READY", "profile_id": profile_id, "running": control is not None}

    def cancel_automation_task(self, profile_id: str, reason: str = "管理员取消") -> dict[str, Any]:
        profile_id = str(profile_id)
        with _LIFECYCLE_LOCK:
            state = _RUN_STATES.get(profile_id)
            if state:
                if state.future and not state.future.done():
                    state.future.cancel()
                if state.loop and state.task and not state.task.done():
                    state.loop.call_soon_threadsafe(state.task.cancel)
                if state.guard:
                    state.guard.cancel()
        if state and state.guard:
            self.automation_safety.cancel(profile_id, reason)
            self.runtime_config.update(profile_id, {"safety_status": "CANCEL_REQUESTED"}, mode="HOT_UPDATE")
            return {"status": "CANCEL_REQUESTED", "profile_id": profile_id, "running": True}
        return {"status": "SKIPPED", "profile_id": profile_id, "running": False}

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

    # ================= 工作室协同码 (Studio Token) 跨设备去重支持 =================

    def get_studio_token_config(self) -> dict[str, Any]:
        """获取当前工作室协同码配置."""
        try:
            path = self.settings.log_file.parent / "studio_token_config.json"
            if not path.exists():
                path = Path("studio_token_config.json")
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"enabled": False, "studio_token": "", "server_url": "https://api.jaycwl.org"}

    def save_studio_token_config(self, cfg_data: dict[str, Any]) -> dict[str, Any]:
        """保存工作室协同码配置."""
        token = str(cfg_data.get("studio_token") or cfg_data.get("cloud_dedup_studio_token") or "").strip()
        server_url = str(cfg_data.get("server_url") or cfg_data.get("cloud_dedup_server_url") or "https://api.jaycwl.org").strip().rstrip("/")
        enabled = bool(cfg_data.get("enabled", bool(token)))
        payload = {
            "enabled": enabled,
            "studio_token": token,
            "server_url": server_url,
        }
        try:
            path = self.settings.log_file.parent / "studio_token_config.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        try:
            Path("studio_token_config.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return payload

    # ================= 动态全字段黑名单词库与一票否决配置支持 =================

    def get_blacklist_config(self) -> dict[str, Any]:
        """获取当前全局黑名单词库与全字段过滤开关配置."""
        candidates = [
            self.settings.log_file.parent / "config.json",
            Path("config.json"),
            self.settings.log_file.parent / "filter_blacklist_config.json",
            Path("filter_blacklist_config.json"),
        ]
        for path in candidates:
            try:
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and ("blacklist_words" in data or "check_options" in data or "enabled" in data):
                        raw_words = data.get("blacklist_words")
                        words = parse_blacklist_input(raw_words) if raw_words is not None else list(DEFAULT_BLACKLIST_WORDS)
                        check_opts = data.get("check_options") or {}
                        enabled = bool(data.get("enabled", True))
                        return {
                            "enabled": enabled,
                            "blacklist_words": words,
                            "check_options": {
                                "check_name": bool(check_opts.get("check_name", True)),
                                "check_bio": bool(check_opts.get("check_bio", True)),
                                "check_tweets": bool(check_opts.get("check_tweets", True)),
                            },
                        }
            except Exception:
                pass
        return {
            "enabled": True,
            "blacklist_words": list(DEFAULT_BLACKLIST_WORDS),
            "check_options": dict(DEFAULT_CHECK_OPTIONS),
        }

    def save_blacklist_config(self, cfg_data: dict[str, Any]) -> dict[str, Any]:
        """保存全局黑名单词库与全字段过滤开关配置到 config.json."""
        enabled = bool(cfg_data.get("enabled", True))
        words = parse_blacklist_input(cfg_data.get("blacklist_words") or [])
        check_opts = {
            "check_name": bool(cfg_data.get("check_options", {}).get("check_name", True)),
            "check_bio": bool(cfg_data.get("check_options", {}).get("check_bio", True)),
            "check_tweets": bool(cfg_data.get("check_options", {}).get("check_tweets", True)),
        }
        payload = {
            "enabled": enabled,
            "blacklist_words": words,
            "check_options": check_opts,
        }
        candidates = [
            self.settings.log_file.parent / "config.json",
            Path("config.json"),
            self.settings.log_file.parent / "filter_blacklist_config.json",
            Path("filter_blacklist_config.json"),
        ]
        for target in candidates:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                existing = {}
                if target.exists():
                    try:
                        existing = json.loads(target.read_text(encoding="utf-8"))
                        if not isinstance(existing, dict):
                            existing = {}
                    except Exception:
                        existing = {}
                existing.update(payload)
                target.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return payload

    # ================= Telegram 自动化战报通知支持 =================

    def get_telegram_config(self) -> dict[str, Any]:
        """获取当前 Telegram 通知配置."""
        return self.telegram_notifier.load_config().to_dict()

    def save_telegram_config(self, cfg_data: dict[str, Any]) -> bool:
        """保存 Telegram 通知配置."""
        cfg = TelegramConfig.from_mapping(cfg_data)
        return self.telegram_notifier.save_config(cfg)

    def send_telegram_test_message(
        self,
        chat_id: str = "",
        bot_token: str = "",
        proxy_url: str = "",
        use_custom: bool = False,
    ) -> tuple[bool, str]:
        """发送 Telegram 连通性测试消息."""
        target_id = (chat_id or self.telegram_notifier.config.chat_id).strip()
        if not target_id:
            return False, "请输入您的 Telegram Chat ID"
        effective_token = bot_token.strip() if (use_custom and bot_token.strip()) else None
        text = self.telegram_notifier.build_test_message(target_id)
        return self.telegram_notifier.send_raw_message(
            chat_id=target_id,
            text=text,
            bot_token=effective_token,
            proxy_url=proxy_url.strip() if proxy_url else None,
        )

    def send_telegram_summary_report(self) -> tuple[bool, str]:
        """手动汇总当前所有账号数据并推送大盘战报到 Telegram (统一由持久化数据库驱动，与界面卡片100%一致)."""
        if not self.telegram_notifier.config.enabled:
            return False, "Telegram 通知尚未启用，请在设置中开启并绑定 Chat ID"
        if not self.telegram_notifier.config.chat_id:
            return False, "尚未绑定 Telegram 接收者 Chat ID"

        accounts_data: list[dict[str, Any]] = []
        try:
            today_stats = self.task_statistics("today")
            by_account = today_stats.get("by_account", {}) if isinstance(today_stats, dict) else {}
            accounts = self.list_accounts()
            for acc in accounts:
                if not acc.profile_name:
                    continue

                # 聚合今日真实统计，多键匹配兜底（与主界面 AccountCard 完全一致）
                stat: dict[str, Any] = {}
                for key in (acc.profile_id, acc.x_account_id, acc.x_username, acc.profile_name):
                    if key and key in by_account and isinstance(by_account[key], dict):
                        stat.update(by_account[key])

                snapshot = self._account_snapshots.get(acc.profile_id, {})
                f_count = acc.followers_count
                if f_count is None:
                    f_count = stat.get("own_followers", snapshot.get("final_followers", 0))

                accounts_data.append({
                    "name": acc.profile_name,
                    "handle": acc.x_username or "",
                    "follows": int(stat.get("follows") if stat.get("follows") is not None else snapshot.get("follows", 0)),
                    "likes": int(stat.get("likes") if stat.get("likes") is not None else snapshot.get("likes", 0)),
                    "scanned": int(stat.get("scanned_posts") if stat.get("scanned_posts") is not None else snapshot.get("scanned", 0)),
                    "initial_followers": int(snapshot.get("initial_followers") or f_count or 0),
                    "final_followers": int(f_count or 0),
                })
        except Exception as exc:
            self.logger.error("从今日统计构建大盘战报异常: %s", exc)

        if not accounts_data:
            accounts_data = list(self._account_snapshots.values())

        text = self.telegram_notifier.build_summary_report(accounts_data)
        return self.telegram_notifier.send_raw_message(
            chat_id=self.telegram_notifier.config.chat_id,
            text=text,
        )

    def _dispatch_telegram_account_report(
        self,
        profile_id: str,
        account_tag: str,
        result: dict[str, Any],
        started_at: str,
    ) -> None:
        """异步分发单账号完工喜报到 Telegram."""
        if not self.telegram_notifier.config.enabled or not self.telegram_notifier.config.notify_on_account_finish:
            return
        if not self.telegram_notifier.config.chat_id:
            return

        def _send():
            try:
                try:
                    s_dt = datetime.fromisoformat(started_at)
                    dur_min = (datetime.now(s_dt.tzinfo) - s_dt).total_seconds() / 60.0
                except Exception:
                    dur_min = 30.0

                handle = str(result.get("username") or "")
                likes = int(result.get("likes", 0))
                follows = int(result.get("follows", 0))
                scanned = int(result.get("scanned_posts", 0))
                init_fans = int(result.get("initial_followers", 0))
                fin_fans = int(result.get("final_followers", 0))
                init_ing = int(result.get("initial_following", 0))
                fin_ing = int(result.get("final_following", 0))
                status = str(result.get("status") or "COMPLETED")

                text = self.telegram_notifier.build_account_completion_report(
                    account_name=account_tag or profile_id,
                    handle=handle,
                    duration_min=dur_min,
                    likes=likes,
                    follows=follows,
                    scanned=scanned,
                    initial_followers=init_fans,
                    final_followers=fin_fans,
                    initial_following=init_ing,
                    final_following=fin_ing,
                    status=status,
                )
                self.telegram_notifier.send_raw_message(
                    chat_id=self.telegram_notifier.config.chat_id,
                    text=text,
                )
            except Exception as e:
                self.logger.warning("Telegram 账号完工自动推送异常: %s", e)

        threading.Thread(target=_send, daemon=True).start()

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
                # If capability check failed due to transient offline status, attempt an immediate heartbeat refresh
                heartbeat_fn = getattr(self.agent_service, "heartbeat_once", None)
                if callable(heartbeat_fn) and getattr(self.agent_service, "agent_status", "") != "REAUTH_REQUIRED":
                    if heartbeat_fn() and checker(capability):
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
            from agent.agent_service import build_agent_service
            self.agent_service = build_agent_service(
                self.task_service,
                self.registry,
                automation_statistics=self.automation_statistics,
            )
            if self.agent_service is not None:
                self.agent_service.start()
            else:
                raise RuntimeError("服务器运行端尚未配置，请检查是否能连接服务器 https://api.jaycwl.org")
        client = getattr(self.agent_service, "server_client", None)
        if client is None or not hasattr(client, "replace_agent_token"):
            raise RuntimeError("当前运行端不支持凭据更新")

        current_id = str(getattr(client, "agent_id", "") or "")
        if current_id and agent_id != current_id:
            raise ValueError("Agent ID 不允许修改，只能替换服务器重新生成的 Token。")
        client.replace_agent_token(agent_id, agent_token)
        try:
            if not self.agent_service.heartbeat_once():
                err = self.agent_service.last_error or "运行端认证失败"
                raise RuntimeError(err)
        except Exception as exc:
            err_msg = str(exc)
            if "已绑定其他电脑" in err_msg:
                raise RuntimeError("认证失败：该 Agent Token 已绑定在其他电脑上！\n如需在此电脑使用，请先在 Web 后台该运行端右侧点击【重新生成令牌】解绑，再在此处激活。") from exc
            if "404" in err_msg or "not found" in err_msg.lower():
                raise RuntimeError("认证失败：服务器未找到该 Agent ID，请检查是否在 Web 后台输入正确。") from exc
            raise RuntimeError(f"运行端认证失败: {err_msg}") from exc
        return self.agent_service.status()

    def profile_runtime_status(self, profile_id: str) -> dict[str, Any]:
        return self.browser_manager.check_status(str(profile_id))

    def stop_agent_service(self) -> None:
        _STOP_EVENT.set()
        try:
            self.rotation_manager.stop_all(join_timeout=0.8)
        except Exception:
            pass

        with self._schedule_lock:
            timers = list(self._scheduled_timers.values())
            self._scheduled_timers.clear()
            self._scheduled_tokens.clear()
        for timer in timers:
            timer.cancel()

        with _LIFECYCLE_LOCK:
            active_states = list(_RUN_STATES.values())

        # 1. 立即取消排队和正在执行的 Future
        for st in active_states:
            if st.future and not st.future.done():
                st.future.cancel()
            if st.guard:
                st.guard.cancel()
            if st.loop and st.task and not st.task.done():
                try:
                    st.loop.call_soon_threadsafe(st.task.cancel)
                except Exception:
                    pass

        # 2. 等待各个运行态的 finalized_event (最多 1 秒，绝不长久卡死用户界面)
        deadline = time.monotonic() + 1.0
        for st in active_states:
            remaining = max(0.05, deadline - time.monotonic())
            st.finalized_event.wait(timeout=remaining)
            if time.monotonic() >= deadline:
                break

        # 3. 彻底断开线程池调度
        _AUTOMATION_EXECUTOR.shutdown(wait=False, cancel_futures=True)

        if self.agent_service is not None:
            try:
                self.agent_service.stop(timeout=0.8)
            except Exception:
                pass
