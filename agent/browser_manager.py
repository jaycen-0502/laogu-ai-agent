from typing import Any

from .laogu_api import LaoguApi, LaoguApiError


class BrowserManagerError(RuntimeError):
    pass


class BrowserManager:
    def __init__(self, api: LaoguApi):
        self.api = api

    def get_profiles(self) -> list[dict[str, Any]]:
        return self.api.list_profiles()

    def get_profile(self, *, profile_id: str = "", profile_name: str = "") -> dict[str, Any]:
        for profile in self.get_profiles():
            if profile_id and profile.get("profileId") == profile_id:
                return profile
            if profile_name and str(profile.get("profileName")) == profile_name:
                return profile
        target = profile_id or profile_name
        raise BrowserManagerError(f"Profile not found: {target}")

    def start_profile(self, profile_id: str, timeout_seconds: int = 30) -> dict[str, Any]:
        try:
            return self.api.start_profile(profile_id, timeout_seconds)
        except LaoguApiError as exc:
            raise BrowserManagerError(f"Failed to start profile {profile_id}: {exc}") from exc

    def check_status(self, profile_id: str) -> dict[str, Any]:
        try:
            return self.api.profile_status(profile_id)
        except LaoguApiError as exc:
            raise BrowserManagerError(f"Failed to read profile status {profile_id}: {exc}") from exc

    def stop_profile(self, profile_id: str) -> dict[str, Any]:
        try:
            return self.api.stop_profile(profile_id)
        except LaoguApiError as exc:
            raise BrowserManagerError(f"Failed to stop profile {profile_id}: {exc}") from exc

    def run_automation(
        self,
        *,
        profile_id: str,
        url: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        try:
            # The Hook owns browser startup and Playwright CDP attachment.
            return self.api.run_hook(profile_id, url, timeout_seconds)
        except LaoguApiError as exc:
            raise BrowserManagerError(
                f"Automation failed for profile {profile_id}: {exc}"
            ) from exc

    def run_account_discovery(
        self,
        *,
        profile_id: str,
        url: str,
        timeout_seconds: int,
        hook_path: str,
    ) -> dict[str, Any]:
        try:
            return self.api.run_hook_params(
                profile_id,
                {
                    "url": url,
                    "timeoutMs": timeout_seconds * 1000,
                    "readOnly": True,
                    "operation": "ACCOUNT_DISCOVERY",
                },
                timeout_seconds,
                hook_path=hook_path,
            )
        except LaoguApiError as exc:
            raise BrowserManagerError(
                f"Account discovery failed for profile {profile_id}: {exc}"
            ) from exc

    def probe_credential_capability(self, profile_id: str) -> dict[str, Any]:
        try:
            return self.api.probe_credential_capability(profile_id)
        except LaoguApiError as exc:
            raise BrowserManagerError(f"Credential capability probe failed for {profile_id}: {exc}") from exc
