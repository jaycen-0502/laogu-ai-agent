from __future__ import annotations

import base64
import hashlib
import ipaddress
import socket
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx


SUPPORTED_PROVIDER_TYPES = frozenset({"OPENAI", "OPENAI_COMPATIBLE"})
OPENAI_BASE_URL = "https://api.openai.com/v1"

# Suggestions only: providers may expose additional model IDs. The API never
# treats this list as an allowlist, so OpenAI-compatible relays remain usable.
COMMON_MODEL_SUGGESTIONS = (
    "gpt-5.4-2026-03-05",
    "gpt-5.4-mini",
    "gpt-5.5",
    "gpt-5.6",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-image-1",
    "gpt-image-1.5",
    "gpt-image-2",
    "apt-imaae-2",
)


class CredentialError(RuntimeError):
    pass


class ProviderConnectionError(RuntimeError):
    pass


class CredentialCipher:
    def __init__(self, configured_key: str, jwt_secret: str, *, production: bool):
        key = configured_key.strip()
        if not key and not production:
            key = base64.urlsafe_b64encode(
                hashlib.sha256(jwt_secret.encode("utf-8")).digest()
            ).decode("ascii")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except Exception as exc:
            raise CredentialError("AI credential encryption key is invalid") from exc

    def encrypt(self, value: str) -> str:
        if not value:
            raise CredentialError("AI provider API key is required")
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise CredentialError("AI provider credential cannot be decrypted") from exc


def normalize_provider_type(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in SUPPORTED_PROVIDER_TYPES:
        raise ValueError("Unsupported AI provider type")
    return normalized


def normalize_base_url(provider_type: str, value: str) -> str:
    normalized_type = normalize_provider_type(provider_type)
    url = str(value or "").strip().rstrip("/")
    if normalized_type == "OPENAI" and not url:
        url = OPENAI_BASE_URL
    if not url:
        raise ValueError("AI provider base URL is required")
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("AI provider base URL is invalid")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("AI provider base URL must not contain credentials, query, or fragment")
    if parsed.hostname.lower() == "localhost":
        raise ValueError("Local AI provider addresses are not allowed")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private AI provider addresses are not allowed")
    return url


def validate_provider_destination(base_url: str, *, production: bool) -> None:
    parsed = urlsplit(base_url)
    if production and parsed.scheme != "https":
        raise ProviderConnectionError("HTTPS is required for AI providers in production")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise ProviderConnectionError("AI provider hostname cannot be resolved") from exc
    if not addresses:
        raise ProviderConnectionError("AI provider hostname cannot be resolved")
    for value in addresses:
        try:
            if not ipaddress.ip_address(value).is_global:
                raise ProviderConnectionError("Private AI provider addresses are not allowed")
        except ValueError as exc:
            raise ProviderConnectionError("AI provider address is invalid") from exc


class AIProviderTester:
    def __init__(self, timeout_seconds: int, *, production: bool):
        self.timeout_seconds = timeout_seconds
        self.production = production

    def _validate_destination(self, base_url: str) -> None:
        validate_provider_destination(base_url, production=self.production)

    def _probe_actual_model(self, base_url: str, api_key: str, model: str) -> str:
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json", "Content-Type": "application/json"}
        attempts = (
            ("responses", {"model": model, "input": "Reply with OK.", "max_output_tokens": 8}),
            ("chat/completions", {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 8}),
        )
        last_status = 0
        for endpoint, payload in attempts:
            try:
                response = httpx.post(f"{base_url.rstrip('/')}/{endpoint}", headers=headers, json=payload, timeout=self.timeout_seconds, follow_redirects=False)
            except httpx.TimeoutException as exc:
                raise ProviderConnectionError("AI provider real request timed out") from exc
            except httpx.HTTPError as exc:
                raise ProviderConnectionError("AI provider real request failed") from exc
            last_status = response.status_code
            if last_status == 401:
                raise ProviderConnectionError("AI provider authentication failed (HTTP 401)")
            if last_status in {400, 404, 405, 422} and endpoint == "responses":
                continue
            if last_status >= 400:
                raise ProviderConnectionError(f"AI provider real request returned HTTP {last_status}")
            try:
                payload_json = response.json()
            except ValueError as exc:
                raise ProviderConnectionError("AI provider real request returned invalid JSON") from exc
            actual = str(payload_json.get("model") or "").strip() if isinstance(payload_json, dict) else ""
            return actual or model
        raise ProviderConnectionError(f"AI provider real request returned HTTP {last_status}")

    def test(self, base_url: str, api_key: str, *, default_model: str = "", configured_models: list[str] | None = None, probe_actual: bool = False) -> dict:
        self._validate_destination(base_url)
        started = time.monotonic()
        try:
            response = httpx.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise ProviderConnectionError("AI provider connection timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderConnectionError("AI provider connection failed") from exc
        if response.status_code == 401:
            raise ProviderConnectionError("AI provider authentication failed (HTTP 401)")
        # A number of OpenAI-compatible relays intentionally omit /models;
        # authenticate by probing /responses below instead of rejecting them.
        if response.status_code >= 400 and response.status_code not in {404, 405} and not (probe_actual and default_model):
            raise ProviderConnectionError(f"AI provider returned HTTP {response.status_code}")
        try:
            if response.status_code >= 400:
                raise ValueError
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise ValueError
            models = sorted(
                {
                    str(item.get("id"))
                    for item in data
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                }
            )[:500]
        except (ValueError, TypeError):
            configured_model = str(default_model or "").strip()
            if not configured_model:
                raise ProviderConnectionError("AI provider returned an invalid model list")
            if not probe_actual:
                probe_status = 404
                for endpoint in ("responses", "chat/completions"):
                    try:
                        probe = httpx.get(
                            f"{base_url.rstrip('/')}/{endpoint}",
                            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                            timeout=self.timeout_seconds,
                            follow_redirects=False,
                        )
                    except httpx.TimeoutException as exc:
                        raise ProviderConnectionError("AI provider connection timed out") from exc
                    except httpx.HTTPError as exc:
                        raise ProviderConnectionError("AI provider connection failed") from exc
                    probe_status = probe.status_code
                    if probe_status == 401:
                        raise ProviderConnectionError("AI provider authentication failed (HTTP 401)")
                    if probe_status in {200, 400, 405, 422, 426}:
                        break
                if probe_status not in {200, 400, 405, 422, 426}:
                    raise ProviderConnectionError(f"AI provider returned HTTP {probe_status}")
            models = [configured_model]
        configured = [str(item).strip() for item in (configured_models or []) if str(item).strip()]
        requested_model = str(default_model or "").strip() or (models[0] if models else "")
        actual_model = self._probe_actual_model(base_url, api_key, requested_model) if probe_actual and requested_model else ""
        models = list(dict.fromkeys([*models, *configured, *([actual_model] if actual_model else [])]))[:500]
        return {
            "status": "SUCCESS",
            "models": models,
            "requested_model": requested_model,
            "actual_model": actual_model,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
