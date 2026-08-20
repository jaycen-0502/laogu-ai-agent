import json
import socket
from typing import Any
import urllib.error
import urllib.request

from .config import Settings


AUTOMATION_HOOK_CONTRACT = {
    "url": "http://127.0.0.1:19876/api/automation/hooks/11",
    "method": "POST",
    "headers": {"Content-Type": "application/json; charset=utf-8"},
    "requestBody": {
        "instance": {
            "type": "existing",
            "selector": {"profileId": "<profile-id>"},
        },
        "params": {"url": "https://example.com", "timeoutMs": 30000},
        "timeoutMs": 30000,
    },
    "response": {
        "ok": True,
        "status": "success",
        "data": {"title": "Example Domain", "url": "https://example.com/"},
        "result": {"title": "Example Domain", "url": "https://example.com/"},
    },
}


class LaoguApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class LaoguTimeoutError(LaoguApiError):
    pass


class LaoguApi:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self.settings.api_key:
            headers[self.settings.api_header] = self.settings.api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        url = f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers=self._headers(),
            method=method,
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                error_payload = json.loads(raw)
            except json.JSONDecodeError:
                error_payload = raw
            raise LaoguApiError(
                f"Laogu API returned HTTP {exc.code}: {error_payload}",
                status_code=exc.code,
                payload=error_payload,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise LaoguTimeoutError(
                f"Laogu API request timed out after {timeout_seconds:.1f}s"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LaoguTimeoutError(
                    f"Laogu API request timed out after {timeout_seconds:.1f}s"
                ) from exc
            raise LaoguApiError(f"Cannot connect to Laogu Browser: {exc.reason}") from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LaoguApiError(f"Laogu API returned invalid JSON: {raw[:300]}") from exc
        if not isinstance(decoded, dict):
            raise LaoguApiError("Laogu API response must be a JSON object", payload=decoded)
        return decoded

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health", timeout_seconds=5)

    def list_profiles(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/profiles", timeout_seconds=10)
        items = response.get("items", [])
        if not isinstance(items, list):
            raise LaoguApiError("Profile list response has no items array", payload=response)
        return items

    def start_profile(self, profile_id: str, timeout_seconds: int = 30) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/runtime/session",
            {
                "selector": {"profileId": profile_id},
                "timeoutMs": timeout_seconds * 1000,
            },
            timeout_seconds=timeout_seconds + 5,
        )

    def profile_status(self, profile_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/runtime/status",
            {"selector": {"profileId": profile_id}},
            timeout_seconds=10,
        )

    def stop_profile(self, profile_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/runtime/stop",
            {"selector": {"profileId": profile_id}},
            timeout_seconds=20,
        )

    def run_hook(
        self,
        profile_id: str,
        url: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return self.run_hook_params(
            profile_id,
            {"url": url, "timeoutMs": timeout_seconds * 1000},
            timeout_seconds,
        )

    def run_hook_params(
        self,
        profile_id: str,
        params: dict[str, Any],
        timeout_seconds: int,
        *,
        hook_path: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "instance": {
                "type": "existing",
                "selector": {"profileId": profile_id},
            },
            "params": dict(params),
            "timeoutMs": timeout_seconds * 1000,
        }
        response = self._request(
            "POST",
            hook_path or self.settings.hook_path,
            payload,
            timeout_seconds=timeout_seconds + 3,
        )
        if response.get("ok") is not True or response.get("status") != "success":
            message = response.get("error") or response.get("message") or "Automation Hook failed"
            raise LaoguApiError(str(message), payload=response)
        return response

    def recent_runs(self, limit: int = 20) -> dict[str, Any]:
        safe_limit = min(200, max(1, int(limit)))
        return self._request(
            "GET",
            f"/api/automation/scripts/runs?limit={safe_limit}",
            timeout_seconds=10,
        )

    def probe_credential_capability(self, profile_id: str) -> dict[str, Any]:
        """Inspect advertised capability metadata only; never request credential values."""
        responses: list[dict[str, Any]] = []
        try:
            responses.append(self.health())
        except LaoguApiError:
            pass
        responses.append(self.profile_status(profile_id))

        advertised: dict[str, bool] = {}
        allowed_keys = {
            "cookiereadsupported": "cookie_read_supported",
            "cookiewritesupported": "cookie_write_supported",
            "credentialsnapshotsupported": "credential_snapshot_supported",
        }

        def inspect(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = "".join(character for character in str(key).lower() if character.isalnum())
                    mapped = allowed_keys.get(normalized)
                    if mapped and isinstance(child, bool):
                        advertised[mapped] = child
                    elif normalized in {"capabilities", "features", "credentialcapabilities"}:
                        inspect(child)

        for response in responses:
            inspect(response)
        cookie_read = advertised.get("cookie_read_supported", False)
        cookie_write = advertised.get("cookie_write_supported", False)
        snapshot_advertised = advertised.get("credential_snapshot_supported", False)
        return {
            "probe_version": "1",
            "browser_reachable": True,
            "cookie_read_supported": cookie_read,
            "cookie_write_supported": cookie_write,
            "credential_snapshot_allowed": bool(cookie_read and snapshot_advertised),
            "evidence": "ADVERTISED_CAPABILITY_METADATA" if advertised else "NOT_ADVERTISED",
        }
