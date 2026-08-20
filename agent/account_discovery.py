from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from .browser_manager import BrowserManager
from .models import BrowserStatus, DiscoveredAccount, LoginStatus


class AccountDiscovery:
    def __init__(
        self,
        browser_manager: BrowserManager,
        logger: logging.Logger,
        *,
        hook_path: str,
        discovery_url: str = "https://x.com/home",
        timeout_seconds: int = 30,
        max_workers: int = 2,
        result_file: Path | None = None,
        hook_runner: Any | None = None,
    ):
        self.browser_manager = browser_manager
        self.logger = logger
        self.hook_path = hook_path
        self.discovery_url = discovery_url
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_workers = max(1, max_workers)
        self.result_file = result_file
        self.hook_runner = hook_runner

    def scan(self, profile_ids: Iterable[str] | None = None) -> list[DiscoveredAccount]:
        requested = {str(item) for item in profile_ids or [] if str(item)}
        profiles = [
            profile
            for profile in self.browser_manager.get_profiles()
            if self._is_enabled(profile)
            and (not requested or self._profile_id(profile) in requested)
        ]

        records: list[DiscoveredAccount] = []
        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="account-discovery",
        ) as executor:
            future_to_profile = {
                executor.submit(self._scan_profile, profile): profile for profile in profiles
            }
            for future in as_completed(future_to_profile):
                profile = future_to_profile[future]
                try:
                    records.append(future.result())
                except Exception as exc:
                    records.append(self._unknown_record(profile, str(exc)))

        self._refresh_browser_status(records)
        records.sort(key=lambda item: (item.profile_name, item.profile_id))
        if self.result_file is not None:
            self._write_result(records)
        return records

    @staticmethod
    def _profile_id(profile: dict[str, Any]) -> str:
        return str(profile.get("profileId") or "")

    @staticmethod
    def _is_enabled(profile: dict[str, Any]) -> bool:
        return (
            bool(profile.get("profileId"))
            and profile.get("enabled", True) is not False
            and not str(profile.get("deletedAt") or "").strip()
        )

    @staticmethod
    def _browser_status(profile: dict[str, Any]) -> BrowserStatus:
        running = profile.get("running")
        if running is True:
            return BrowserStatus.RUNNING
        if running is False:
            return BrowserStatus.STOPPED
        return BrowserStatus.UNKNOWN

    def _scan_profile(self, profile: dict[str, Any]) -> DiscoveredAccount:
        profile_id = self._profile_id(profile)
        profile_name = str(profile.get("profileName") or "")
        instance_id = str(profile.get("instanceId") or profile_id)
        checked_at = datetime.now().astimezone()
        try:
            runner = self.hook_runner or self.browser_manager
            response = runner.run_account_discovery(
                profile_id=profile_id,
                url=self.discovery_url,
                timeout_seconds=self.timeout_seconds,
                hook_path=self.hook_path,
            )
            login_status, username, account_id, reason = self._parse_identity(response)
            record = DiscoveredAccount(
                profile_id=profile_id,
                instance_id=instance_id,
                profile_name=profile_name,
                browser_status=self._browser_status(profile),
                login_status=login_status,
                x_username=username,
                x_account_id=account_id,
                last_checked=checked_at,
                error=reason,
            )
        except Exception as exc:
            record = self._unknown_record(profile, str(exc), checked_at=checked_at)

        self.logger.info(
            "profile=%s profile_id=%s status=%s operation=account_discovery "
            "login_status=%s x_username=%s error=%s",
            profile_name,
            profile_id,
            record.browser_status.value,
            record.login_status.value,
            record.x_username,
            record.error,
        )
        return record

    def _unknown_record(
        self,
        profile: dict[str, Any],
        error: str,
        *,
        checked_at: datetime | None = None,
    ) -> DiscoveredAccount:
        profile_id = self._profile_id(profile)
        return DiscoveredAccount(
            profile_id=profile_id,
            instance_id=str(profile.get("instanceId") or profile_id),
            profile_name=str(profile.get("profileName") or ""),
            browser_status=BrowserStatus.ERROR,
            login_status=LoginStatus.UNKNOWN,
            x_username="",
            x_account_id="",
            last_checked=checked_at or datetime.now().astimezone(),
            error=error,
        )

    @classmethod
    def _parse_identity(
        cls, response: dict[str, Any]
    ) -> tuple[LoginStatus, str, str, str]:
        payloads = cls._response_payloads(response)
        explicit_status = cls._first_value(payloads, "loginStatus", "login_status")
        identity_verified = cls._first_value(
            payloads, "identityVerified", "identity_verified"
        ) is True
        reason = str(cls._first_value(payloads, "reason", "error", "message") or "").strip()
        username = cls._clean_username(
            cls._first_value(payloads, "xUsername", "x_username", "username")
        )
        raw_account_id = str(
            cls._first_value(payloads, "xAccountId", "x_account_id", "accountId") or ""
        ).strip()
        account_id = raw_account_id if raw_account_id.isdigit() else ""

        normalized_status = str(explicit_status or "").strip().upper()
        if normalized_status == LoginStatus.NOT_LOGGED_IN.value and identity_verified:
            return LoginStatus.NOT_LOGGED_IN, "", "", reason
        if normalized_status == LoginStatus.LOGGED_IN.value:
            if identity_verified and username and account_id:
                return LoginStatus.LOGGED_IN, username, account_id, reason
            return (
                LoginStatus.UNKNOWN,
                "",
                "",
                reason or "Hook did not provide a verified username and numeric account ID",
            )

        return LoginStatus.UNKNOWN, "", "", reason or "Hook did not verify login status"

    @staticmethod
    def _response_payloads(response: dict[str, Any]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = [response]
        for key in ("result", "data"):
            value = response.get(key)
            if isinstance(value, dict):
                payloads.append(value)
                nested = value.get("result")
                if isinstance(nested, dict):
                    payloads.append(nested)
        return payloads

    @staticmethod
    def _first_value(payloads: list[dict[str, Any]], *keys: str) -> Any:
        for payload in reversed(payloads):
            for key in keys:
                value = payload.get(key)
                if value not in (None, ""):
                    return value
        return None

    @classmethod
    def _clean_username(cls, value: Any) -> str:
        username = str(value or "").strip().lstrip("@")
        if not username or len(username) > 15:
            return ""
        if not all(char.isascii() and (char.isalnum() or char == "_") for char in username):
            return ""
        return "@" + username

    def _refresh_browser_status(self, records: list[DiscoveredAccount]) -> None:
        try:
            current = {
                self._profile_id(profile): profile
                for profile in self.browser_manager.get_profiles()
            }
        except Exception as exc:
            self.logger.info("operation=account_discovery_status_refresh error=%s", exc)
            return
        for record in records:
            if record.browser_status is BrowserStatus.ERROR:
                continue
            profile = current.get(record.profile_id)
            if profile is not None:
                record.browser_status = self._browser_status(profile)

    def _write_result(self, records: list[DiscoveredAccount]) -> None:
        assert self.result_file is not None
        self.result_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "scannedAt": datetime.now().astimezone().isoformat(),
            "count": len(records),
            "items": [record.to_dict() for record in records],
        }
        temporary = self.result_file.with_suffix(self.result_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.result_file)
