from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any

from .models import AccountStatus, BrowserStatus, DiscoveredAccount, LoginStatus


@dataclass
class AccountRecord:
    profile_id: str
    instance_id: str
    x_username: str
    x_account_id: str
    login_status: LoginStatus
    browser_status: BrowserStatus
    account_status: AccountStatus
    last_checked: datetime
    mapping_updated_at: datetime
    profile_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["login_status"] = self.login_status.value
        data["browser_status"] = self.browser_status.value
        data["account_status"] = self.account_status.value
        data["last_checked"] = self.last_checked.isoformat()
        data["mapping_updated_at"] = self.mapping_updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountRecord":
        return cls(
            profile_id=str(data.get("profile_id") or ""),
            instance_id=str(data.get("instance_id") or ""),
            profile_name=str(data.get("profile_name") or ""),
            x_username=str(data.get("x_username") or ""),
            x_account_id=str(data.get("x_account_id") or ""),
            login_status=LoginStatus(str(data.get("login_status") or "UNKNOWN")),
            browser_status=BrowserStatus(str(data.get("browser_status") or "UNKNOWN")),
            account_status=AccountStatus(str(data.get("account_status") or "UNKNOWN")),
            last_checked=_parse_datetime(data.get("last_checked")),
            mapping_updated_at=_parse_datetime(data.get("mapping_updated_at")),
        )


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.astimezone()
        except ValueError:
            pass
    return datetime.now().astimezone()


class AccountRegistry:
    def __init__(self, registry_file: Path, history_file: Path):
        self.registry_file = registry_file
        self.history_file = history_file
        self._lock = threading.RLock()
        self._records = self._load()
        self._apply_duplicate_statuses()

    def register(self, discovered: DiscoveredAccount) -> AccountRecord:
        with self._lock:
            if discovered.profile_id in self._records:
                raise ValueError(f"Profile is already registered: {discovered.profile_id}")
            return self._upsert(discovered)

    def update(self, discovered: DiscoveredAccount) -> AccountRecord:
        with self._lock:
            return self._upsert(discovered)

    def get(self, profile_id: str) -> AccountRecord | None:
        with self._lock:
            return self._records.get(str(profile_id))

    def list(self) -> list[AccountRecord]:
        with self._lock:
            return sorted(
                self._records.values(),
                key=lambda item: (item.profile_name, item.profile_id),
            )

    def remove(self, profile_id: str) -> bool:
        with self._lock:
            if self._records.pop(str(profile_id), None) is None:
                return False
            self._apply_duplicate_statuses()
            self._save()
            return True

    def find_by_profile(self, profile_id: str) -> AccountRecord | None:
        return self.get(profile_id)

    def find_by_x_account(self, x_account_id: str) -> list[AccountRecord]:
        account_id = str(x_account_id).strip()
        if not account_id:
            return []
        with self._lock:
            return [item for item in self.list() if item.x_account_id == account_id]

    def update_many(self, discoveries: list[DiscoveredAccount]) -> list[AccountRecord]:
        with self._lock:
            updated = [self._upsert(item, save=False) for item in discoveries]
            self._apply_duplicate_statuses()
            self._save()
            return [self._records[item.profile_id] for item in updated]

    def _upsert(self, discovered: DiscoveredAccount, *, save: bool = True) -> AccountRecord:
        now = datetime.now().astimezone()
        existing = self._records.get(discovered.profile_id)
        old_username = existing.x_username if existing else ""
        old_account_id = existing.x_account_id if existing else ""
        old_status = existing.login_status if existing else LoginStatus.UNKNOWN

        verified_mapping = (
            discovered.login_status is LoginStatus.LOGGED_IN
            and bool(discovered.x_username)
            and bool(discovered.x_account_id)
        )
        new_username = discovered.x_username if verified_mapping else old_username
        new_account_id = discovered.x_account_id if verified_mapping else old_account_id
        mapping_changed = (new_username, new_account_id) != (old_username, old_account_id)
        status_changed = discovered.login_status is not old_status

        record = AccountRecord(
            profile_id=discovered.profile_id,
            instance_id=discovered.instance_id,
            profile_name=discovered.profile_name,
            x_username=new_username,
            x_account_id=new_account_id,
            login_status=discovered.login_status,
            browser_status=discovered.browser_status,
            account_status=AccountStatus.UNKNOWN,
            last_checked=discovered.last_checked,
            mapping_updated_at=(
                now if existing is None or mapping_changed else existing.mapping_updated_at
            ),
        )
        self._records[record.profile_id] = record

        if mapping_changed or status_changed:
            self._append_history(
                timestamp=now,
                profile_id=record.profile_id,
                old_x_username=old_username,
                old_x_account_id=old_account_id,
                new_x_username=new_username,
                new_x_account_id=new_account_id,
                old_status=old_status,
                new_status=record.login_status,
            )

        if save:
            self._apply_duplicate_statuses()
            self._save()
        return record

    def _apply_duplicate_statuses(self) -> None:
        current_ids: dict[str, list[AccountRecord]] = {}
        for record in self._records.values():
            if record.login_status is LoginStatus.LOGGED_IN and record.x_account_id:
                current_ids.setdefault(record.x_account_id, []).append(record)

        for record in self._records.values():
            if record.login_status is not LoginStatus.LOGGED_IN or not record.x_account_id:
                record.account_status = AccountStatus.UNKNOWN
            elif len(current_ids.get(record.x_account_id, [])) > 1:
                record.account_status = AccountStatus.DUPLICATE_ACCOUNT
            else:
                record.account_status = AccountStatus.VALID

    def _append_history(
        self,
        *,
        timestamp: datetime,
        profile_id: str,
        old_x_username: str,
        old_x_account_id: str,
        new_x_username: str,
        new_x_account_id: str,
        old_status: LoginStatus,
        new_status: LoginStatus,
    ) -> None:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": timestamp.isoformat(),
            "profile_id": profile_id,
            "old_x_username": old_x_username,
            "old_x_account_id": old_x_account_id,
            "new_x_username": new_x_username,
            "new_x_account_id": new_x_account_id,
            "old_status": old_status.value,
            "new_status": new_status.value,
        }
        with self.history_file.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _load(self) -> dict[str, AccountRecord]:
        if not self.registry_file.exists():
            return {}
        try:
            payload = json.loads(self.registry_file.read_text(encoding="utf-8"))
            items = payload.get("items", []) if isinstance(payload, dict) else []
            records = [
                AccountRecord.from_dict(item) for item in items if isinstance(item, dict)
            ]
            return {item.profile_id: item for item in records if item.profile_id}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot load Account Registry: {exc}") from exc

    def _save(self) -> None:
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updatedAt": datetime.now().astimezone().isoformat(),
            "count": len(self._records),
            "items": [item.to_dict() for item in self.list()],
        }
        temporary = self.registry_file.with_suffix(self.registry_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.registry_file)
