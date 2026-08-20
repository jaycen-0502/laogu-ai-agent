from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from agent.account_discovery import AccountDiscovery
from agent.account_registry import AccountRecord, AccountRegistry
from agent.browser_manager import BrowserManager
from agent.config import load_settings
from agent.laogu_api import LaoguApi
from agent.laogu_hook_runner import LaoguProjectHookRunner
from agent.logger import build_logger
from agent.task_service import TaskService
from agent.agent_service import build_agent_service


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
    ):
        settings = load_settings()
        logger = build_logger(settings.log_file)
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
            return {"server": "OFFLINE", "agent": "UNCONFIGURED", "last_heartbeat": "", "last_error": "LAOGU_SERVER_URL not configured"}
        return self.agent_service.status()

    def stop_agent_service(self) -> None:
        if self.agent_service is not None:
            self.agent_service.stop()
