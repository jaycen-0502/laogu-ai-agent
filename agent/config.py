from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys


FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)).resolve() if FROZEN else PROJECT_ROOT
_ENV_NAME = re.compile(r"^LAOGU_[A-Z0-9_]+$")


def _load_local_environment() -> None:
    """Load an optional portable configuration without overriding real env vars."""
    path = PROJECT_ROOT / "config" / "laogu.env"
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    base_url: str
    hook_path: str
    api_header: str
    api_key: str
    default_timeout_seconds: int
    max_concurrency: int
    log_file: Path
    result_file: Path
    account_discovery_hook_path: str
    account_discovery_url: str
    account_discovery_result_file: Path
    account_registry_file: Path
    account_mapping_history_file: Path
    task_log_file: Path
    activity_log_file: Path
    profile_snapshot_file: Path
    automation_runtime_dir: Path
    automation_node_path: Path
    account_discovery_script: Path
    x_readonly_task_script: Path
    server_url: str
    server_enrollment_token: str
    server_agent_id: str
    server_agent_token: str
    agent_credentials_file: Path
    agent_state_file: Path
    agent_heartbeat_seconds: int
    engine_update_url: str

    @property
    def hook_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.hook_path.lstrip('/')}"


def load_settings() -> Settings:
    _load_local_environment()
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        base_url=os.getenv("LAOGU_BASE_URL", "http://127.0.0.1:19876").strip(),
        hook_path=os.getenv("LAOGU_HOOK_PATH", "/api/automation/hooks/11").strip(),
        api_header=os.getenv("LAOGU_API_HEADER", "X-Ant-Api-Key").strip(),
        api_key=os.getenv("LAOGU_API_KEY", "").strip(),
        default_timeout_seconds=_env_int("LAOGU_TASK_TIMEOUT_SECONDS", 30),
        max_concurrency=max(1, _env_int("LAOGU_MAX_CONCURRENCY", 2)),
        log_file=logs_dir / "agent.log",
        result_file=logs_dir / "test_results.json",
        account_discovery_hook_path=os.getenv(
            "LAOGU_ACCOUNT_DISCOVERY_HOOK_PATH",
            os.getenv("LAOGU_HOOK_PATH", "/api/automation/hooks/11"),
        ).strip(),
        account_discovery_url=os.getenv(
            "LAOGU_ACCOUNT_DISCOVERY_URL",
            "https://x.com/home",
        ).strip(),
        account_discovery_result_file=logs_dir / "account_discovery.json",
        account_registry_file=logs_dir / "account_registry.json",
        account_mapping_history_file=logs_dir / "account_mapping_history.jsonl",
        task_log_file=logs_dir / "tasks.jsonl",
        activity_log_file=logs_dir / "activity.jsonl",
        profile_snapshot_file=logs_dir / "profile_snapshot.json",
        automation_runtime_dir=Path(
            os.getenv(
                "LAOGU_AUTOMATION_RUNTIME_DIR",
                "C:/Users/Administrator/Desktop/laogu/data/runtime/automation/"
                "node-22.15.1-playwright-core-1.59.0",
            ).strip()
        ),
        automation_node_path=Path(
            os.getenv(
                "LAOGU_AUTOMATION_NODE_PATH",
                "C:/Program Files/nodejs/node.exe",
            ).strip()
        ),
        account_discovery_script=RESOURCE_ROOT / "scripts" / "discover_x_account.js",
        x_readonly_task_script=RESOURCE_ROOT / "scripts" / "x_readonly_tasks.js",
        server_url=os.getenv("LAOGU_SERVER_URL", "").strip(),
        server_enrollment_token=os.getenv("LAOGU_SERVER_ENROLLMENT_TOKEN", "").strip(),
        server_agent_id=os.getenv("LAOGU_AGENT_ID", "").strip(),
        server_agent_token=os.getenv("LAOGU_AGENT_TOKEN", "").strip(),
        agent_credentials_file=PROJECT_ROOT / "agent_data" / "credentials.json",
        agent_state_file=PROJECT_ROOT / "agent_data" / "agent_state.db",
        agent_heartbeat_seconds=max(5, _env_int("LAOGU_AGENT_HEARTBEAT_SECONDS", 30)),
        engine_update_url=os.getenv("LAOGU_ENGINE_UPDATE_URL", "").strip(),
    )
