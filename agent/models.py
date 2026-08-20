from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class BrowserStatus(str, Enum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class LoginStatus(str, Enum):
    LOGGED_IN = "LOGGED_IN"
    NOT_LOGGED_IN = "NOT_LOGGED_IN"
    UNKNOWN = "UNKNOWN"


class AccountStatus(str, Enum):
    VALID = "VALID"
    DUPLICATE_ACCOUNT = "DUPLICATE_ACCOUNT"
    UNKNOWN = "UNKNOWN"


@dataclass
class Task:
    task_id: str
    profile_id: str
    profile_name: str
    url: str
    timeout_seconds: int
    x_account_id: str = ""
    task_type: str = "browser.open_url"
    params: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    result: dict[str, Any] | None = None
    elapsed_time: float = 0.0
    retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Script bundles and cancellation callbacks are runtime-only values.
        # Persisting them would both duplicate script source and make JSON
        # serialization fail when cancel_check is a Python callable.
        data["metadata"] = {
            key: value
            for key, value in self.metadata.items()
            if key not in {"script_bundle", "cancel_check"}
        }
        data["status"] = self.status.value
        data["created_at"] = self.created_at.isoformat()
        data["started_at"] = self.started_at.isoformat() if self.started_at else None
        data["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        data["duration"] = self.elapsed_time
        return data


@dataclass
class DiscoveredAccount:
    profile_id: str
    instance_id: str
    browser_status: BrowserStatus
    login_status: LoginStatus
    x_username: str
    x_account_id: str
    last_checked: datetime
    profile_name: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["browser_status"] = self.browser_status.value
        data["login_status"] = self.login_status.value
        data["last_checked"] = self.last_checked.isoformat()
        return data
