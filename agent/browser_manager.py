from typing import Any, Callable
import time

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

    def start_profile_ready(
        self,
        profile_id: str,
        timeout_seconds: int = 30,
        *,
        retries: int = 2,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Start one Profile and wait until its runtime reports usable state."""
        profile_id = str(profile_id)
        last_error: Exception | None = None
        for attempt in range(max(0, int(retries)) + 1):
            if progress:
                progress(f"PROFILE_STARTING attempt={attempt + 1}/{max(0, int(retries)) + 1}")
            try:
                response = self.start_profile(profile_id, timeout_seconds)
                if self._is_ready(response):
                    if progress:
                        progress("PROFILE_READY source=start_response")
                    return response
                deadline = time.monotonic() + max(1, int(timeout_seconds))
                while time.monotonic() < deadline:
                    time.sleep(0.5)
                    status = self.check_status(profile_id)
                    if self._is_ready(status):
                        merged = dict(response)
                        merged.update(status)
                        if progress:
                            progress("PROFILE_READY source=status_poll")
                        return merged
                raise BrowserManagerError(
                    f"Profile [{profile_id}] did not become ready within {timeout_seconds}s"
                )
            except Exception as exc:
                last_error = exc
                if progress:
                    progress(f"PROFILE_START_FAILED attempt={attempt + 1} error={exc}")
                if attempt < max(0, int(retries)):
                    time.sleep(min(3.0, 1.0 + attempt))
        raise BrowserManagerError(
            f"Profile [{profile_id}] failed to start after {max(0, int(retries)) + 1} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _is_ready(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        for key in ("cdpUrl", "cdp_url", "debuggerUrl", "debugger_url", "webSocketDebuggerUrl"):
            if str(payload.get(key) or "").strip():
                return True
        # Older Browser API versions expose only the debugging port. Treat a
        # valid port as ready; the controller normalizes it to a local CDP URL.
        for key in ("port", "cdpPort", "cdp_port", "debuggerPort", "debugger_port"):
            value = payload.get(key)
            if isinstance(value, int) and 1 <= value <= 65535:
                return True
        for key in ("debugReady", "debug_ready", "ready"):
            if payload.get(key) is True:
                return True
        status = str(payload.get("status") or payload.get("browserStatus") or "").upper()
        if status == "READY":
            return True
        # Laogu responses may wrap runtime data under ``data`` or ``result``.
        # Inspect nested objects without changing the response contract.
        for value in payload.values():
            if isinstance(value, dict) and BrowserManager._is_ready(value):
                return True
            if isinstance(value, list) and any(BrowserManager._is_ready(item) for item in value):
                return True
        return False

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
