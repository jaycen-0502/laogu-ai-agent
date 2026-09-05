from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlsplit

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
    proxy_id: str = ""
    proxy_name: str = ""
    proxy_protocol: str = ""
    proxy_host: str = ""
    proxy_port: str = ""
    proxy_status: str = "UNKNOWN"
    exit_ip: str = ""
    proxy_checked_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["login_status"] = self.login_status.value
        data["browser_status"] = self.browser_status.value
        data["account_status"] = self.account_status.value
        data["last_checked"] = self.last_checked.isoformat()
        data["mapping_updated_at"] = self.mapping_updated_at.isoformat()
        data["proxy_checked_at"] = self.proxy_checked_at.isoformat() if self.proxy_checked_at else None
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
            proxy_id=str(data.get("proxy_id") or ""),
            proxy_name=str(data.get("proxy_name") or ""),
            proxy_protocol=str(data.get("proxy_protocol") or ""),
            proxy_host=str(data.get("proxy_host") or ""),
            proxy_port=str(data.get("proxy_port") or ""),
            proxy_status=str(data.get("proxy_status") or "UNKNOWN"),
            exit_ip=str(data.get("exit_ip") or ""),
            proxy_checked_at=_parse_optional_datetime(data.get("proxy_checked_at")),
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


def _parse_optional_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.astimezone()
    except ValueError:
        return None


def _profile_proxy_metadata(profile: dict[str, Any]) -> dict[str, str]:
    raw = str(profile.get("proxyConfig") or profile.get("proxy_config") or "").strip()
    proxy_id = str(profile.get("proxyId") or profile.get("proxy_id") or "").strip()
    proxy_name = str(profile.get("proxyBindName") or profile.get("proxyName") or profile.get("proxy_name") or "").strip()
    if not raw or raw.lower() in {"direct://", "direct"}:
        return {
            "proxy_id": proxy_id,
            "proxy_name": proxy_name or ("直连" if raw else ""),
            "proxy_protocol": "direct" if raw else "",
            "proxy_host": "",
            "proxy_port": "",
            "proxy_status": "DIRECT" if raw else "UNKNOWN",
        }
    try:
        parsed = urlsplit(raw)
        protocol = (parsed.scheme or "custom").lower()
        host = parsed.hostname or ""
        port = str(parsed.port or "")
    except ValueError:
        protocol, host, port = "custom", "", ""
    return {
        "proxy_id": proxy_id,
        "proxy_name": proxy_name,
        "proxy_protocol": protocol,
        "proxy_host": host,
        "proxy_port": port,
        "proxy_status": "CONFIGURED",
    }


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

    def update_profile_metadata(self, profiles: list[dict[str, Any]]) -> bool:
        """Merge non-identity Profile metadata without probing the browser.

        This is used by the cache-first path: refreshing the Profile list can
        update proxy labels immediately while preserving the last verified X
        identity.  It never creates a new account row and never overwrites an
        existing exit-IP value with an empty value.
        """
        changed = False
        with self._lock:
            for profile in profiles:
                profile_id = str(profile.get("profileId") or profile.get("profile_id") or "").strip()
                record = self._records.get(profile_id)
                if not record:
                    continue
                metadata = {
                    "instance_id": str(profile.get("instanceId") or record.instance_id),
                    "profile_name": str(profile.get("profileName") or record.profile_name),
                }
                proxy_metadata = _profile_proxy_metadata(profile)
                for key, value in proxy_metadata.items():
                    if value and (key != "proxy_status" or not record.proxy_status or record.proxy_status == "UNKNOWN"):
                        metadata[key] = value
                for key, value in metadata.items():
                    if value != getattr(record, key):
                        setattr(record, key, value)
                        changed = True
            if changed:
                self._save()
        return changed

    def merge_profile_snapshots(self, snapshots: dict[str, dict[str, Any]]) -> bool:
        """Merge verified identity values produced by an already-running task.

        The daily engine/read-only profile task may already have a page open and
        write a snapshot.  Reusing that snapshot avoids a second browser probe.
        Empty or partial snapshots are ignored so a transient render cannot
        erase the last known mapping.
        """
        changed = False
        with self._lock:
            for profile_id, snapshot in (snapshots or {}).items():
                record = self._records.get(str(profile_id))
                if not record or not isinstance(snapshot, dict):
                    continue
                username = str(snapshot.get("x_username") or snapshot.get("xUsername") or "").strip()
                account_id = str(snapshot.get("x_account_id") or snapshot.get("xAccountId") or "").strip()
                if not username or not account_id.isdigit():
                    continue
                checked_at = _parse_optional_datetime(snapshot.get("checked_at")) or datetime.now().astimezone()
                mapping_changed = (record.x_username, record.x_account_id) != (username, account_id)
                if mapping_changed:
                    self._append_history(
                        timestamp=datetime.now().astimezone(),
                        profile_id=record.profile_id,
                        old_x_username=record.x_username,
                        old_x_account_id=record.x_account_id,
                        new_x_username=username,
                        new_x_account_id=account_id,
                        old_status=record.login_status,
                        new_status=LoginStatus.LOGGED_IN,
                    )
                    record.x_username = username
                    record.x_account_id = account_id
                    record.mapping_updated_at = datetime.now().astimezone()
                    changed = True
                if record.login_status is not LoginStatus.LOGGED_IN:
                    record.login_status = LoginStatus.LOGGED_IN
                    changed = True
                if checked_at > record.last_checked:
                    record.last_checked = checked_at
                    changed = True
            if changed:
                self._apply_duplicate_statuses()
                self._save()
        return changed

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
        new_login_status = discovered.login_status
        if (
            existing is not None
            and old_status is LoginStatus.LOGGED_IN
            and discovered.login_status is LoginStatus.UNKNOWN
        ):
            new_login_status = old_status
        mapping_changed = (new_username, new_account_id) != (old_username, old_account_id)
        status_changed = new_login_status is not old_status

        record = AccountRecord(
            profile_id=discovered.profile_id,
            instance_id=discovered.instance_id,
            profile_name=discovered.profile_name,
            x_username=new_username,
            x_account_id=new_account_id,
            login_status=new_login_status,
            browser_status=discovered.browser_status,
            account_status=AccountStatus.UNKNOWN,
            last_checked=discovered.last_checked,
            mapping_updated_at=(
                now if existing is None or mapping_changed else existing.mapping_updated_at
            ),
            proxy_id=discovered.proxy_id or (existing.proxy_id if existing else ""),
            proxy_name=discovered.proxy_name or (existing.proxy_name if existing else ""),
            proxy_protocol=discovered.proxy_protocol or (existing.proxy_protocol if existing else ""),
            proxy_host=discovered.proxy_host or (existing.proxy_host if existing else ""),
            proxy_port=discovered.proxy_port or (existing.proxy_port if existing else ""),
            proxy_status=discovered.proxy_status or (existing.proxy_status if existing else "UNKNOWN"),
            exit_ip=discovered.exit_ip or (existing.exit_ip if existing else ""),
            proxy_checked_at=discovered.proxy_checked_at or (existing.proxy_checked_at if existing else None),
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
