from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import asyncio
from pathlib import Path
from typing import Any, Iterable

from agent.account_discovery import AccountDiscovery
from agent.account_registry import AccountRecord, AccountRegistry
from agent.browser_manager import BrowserManager, BrowserManagerError
from agent.config import load_settings
from agent.laogu_api import LaoguApi
from agent.laogu_hook_runner import LaoguProjectHookRunner
from agent.logger import build_logger
from agent.task_service import TaskService
from agent.agent_service import build_agent_service
from agent.runtime_config import RuntimeConfig
from agent.script_updater import get_automation_engine_class, sync_engine_from_server


_AUTO_AGENT_SERVICE = object()


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


def account_to_row(record: AccountRecord) -> AccountRow:
    return AccountRow(
        profile_id=record.profile_id,
        profile_name=record.profile_name,
        instance_id=record.instance_id,
        browser_status=record.browser_status.value,
        login_status=record.login_status.value,
        x_username=record.x_username,
        x_account_id=record.x_account_id,
        account_status=record.account_status.value,
        last_checked=_format_datetime(record.last_checked),
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
        self.agent_service = (
            build_agent_service(self.task_service, self.registry)
            if agent_service is _AUTO_AGENT_SERVICE
            else agent_service
        )
        if self.agent_service is not None:
            self.agent_service.start()

    def health(self) -> dict[str, Any]:
        return self.api.health()

    def refresh_profiles(self) -> list[dict[str, Any]]:
        return self.browser_manager.get_profiles()

    def list_accounts(self) -> list[AccountRow]:
        return [account_to_row(record) for record in self.registry.list()]

    def scan_accounts(self, profile_ids: Iterable[str] | None = None) -> list[AccountRow]:
        normalized = [str(item) for item in profile_ids or [] if str(item)]
        discoveries = self.discovery.scan(normalized or None)
        self.registry.update_many(discoveries)
        return self.list_accounts()

    def start_profile(self, profile_id: str) -> dict[str, Any]:
        return self.browser_manager.start_profile(str(profile_id))

    def stop_profile(self, profile_id: str) -> dict[str, Any]:
        return self.browser_manager.stop_profile(str(profile_id))

    def set_profile_task_config(self, profile_id: str, config: dict[str, Any]) -> dict[str, Any]:
        """Persist a validated per-profile configuration for the next run."""
        if not isinstance(config, dict):
            raise ValueError("Profile task config must be an object")
        return self.runtime_config.update(str(profile_id), dict(config), mode="HOT_UPDATE")

    def get_profile_task_config(self, profile_id: str) -> dict[str, Any]:
        return self.runtime_config.snapshot(str(profile_id))

    def start_automation_task(self, profile_id: str, config: dict[str, Any]) -> dict[str, Any]:
        """Start a Profile and run the safe read-only CDP validation workflow."""
        profile_id = str(profile_id)
        saved = self.set_profile_task_config(profile_id, config)
        started = self.browser_manager.start_profile(profile_id)
        cdp_url = self._extract_cdp_url(started)
        if not cdp_url:
            try:
                cdp_url = self._extract_cdp_url(self.browser_manager.check_status(profile_id))
            except Exception as exc:
                raise BrowserManagerError(
                    f"Profile started but did not expose a CDP endpoint: {exc}"
                ) from exc
        if not cdp_url:
            raise BrowserManagerError(
                "Profile started but Laogu Browser did not expose a CDP endpoint"
            )
        engine_cache_dir = getattr(self.settings, "engine_cache_dir", None)
        server_client = getattr(self.agent_service, "server_client", None)
        if (
            getattr(self.settings, "engine_auto_update", False)
            and server_client is not None
            and getattr(server_client, "agent_id", "")
            and engine_cache_dir
        ):
            try:
                sync_engine_from_server(server_client, engine_cache_dir)
            except Exception as exc:
                # A network or validation failure must never take down the
                # desktop controller; the last known good/bundled engine wins.
                self.logger.warning("远程自动化引擎更新不可用，继续使用本地版本：%s", exc)
        engine_class = get_automation_engine_class(
            cache_dir=str(engine_cache_dir) if engine_cache_dir else "",
        )
        engine = engine_class(cdp_url=cdp_url, logger=self.logger)
        result = asyncio.run(engine.run(config))
        return {
            "status": "SUCCESS",
            "profile_id": profile_id,
            "cdp_url": cdp_url,
            "runtime_config": saved,
            "result": result,
        }

    @staticmethod
    def _extract_cdp_url(payload: Any) -> str:
        """Read only documented CDP endpoint fields from nested API responses."""
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
        return self.task_service.statistics.summary(period)

    def recent_activities(self, limit: int = 20) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.task_service.statistics.recent_activities(limit)]

    def task_detail(self, task_id: str) -> dict[str, Any] | None:
        return self.task_service.statistics.task_store.get(task_id)

    def run_read_only_task(
        self, profile_id: str, task_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.task_service.run(profile_id, task_type, params).to_dict()

    def server_agent_status(self) -> dict[str, str]:
        if self.agent_service is None:
            return {"server": "OFFLINE", "agent": "UNCONFIGURED", "lifecycle": "STOPPED", "execution_mode": "EMBEDDED_DESKTOP", "cdp_url": self.settings.base_url, "last_heartbeat": "", "last_error": "LAOGU_SERVER_URL not configured"}
        status = self.agent_service.status()
        status.setdefault("cdp_url", self.settings.base_url)
        return status

    def current_agent_id(self) -> str:
        """Return the non-secret Agent identifier currently stored locally."""
        if self.agent_service is None:
            return ""
        client = getattr(self.agent_service, "server_client", None)
        return str(getattr(client, "agent_id", "") or "")

    def replace_agent_credentials(self, agent_id: str, agent_token: str) -> dict[str, str]:
        """DPAPI-protect replacement credentials and verify them immediately."""
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

        client.replace_agent_token(agent_id, agent_token)
        if not self.agent_service.heartbeat_once():
            raise RuntimeError(self.agent_service.last_error or "运行端认证失败")
        return self.agent_service.status()

    def profile_runtime_status(self, profile_id: str) -> dict[str, Any]:
        """Return documented runtime/CDP fields for one Profile."""
        return self.browser_manager.check_status(str(profile_id))

    def stop_agent_service(self) -> None:
        if self.agent_service is not None:
            self.agent_service.stop()
