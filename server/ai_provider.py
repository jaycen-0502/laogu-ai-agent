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

    def test(self, base_url: str, api_key: str, *, default_model: str = "") -> dict:
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
        if response.status_code >= 400:
            raise ProviderConnectionError(f"AI provider returned HTTP {response.status_code}")
        try:
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
            try:
                probe = httpx.get(
                    f"{base_url.rstrip('/')}/responses",
                    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                )
            except httpx.TimeoutException as exc:
                raise ProviderConnectionError("AI provider connection timed out") from exc
            except httpx.HTTPError as exc:
                raise ProviderConnectionError("AI provider connection failed") from exc
            if probe.status_code == 401:
                raise ProviderConnectionError("AI provider authentication failed (HTTP 401)")
            if probe.status_code not in {200, 400, 405, 422, 426}:
                raise ProviderConnectionError(f"AI provider returned HTTP {probe.status_code}")
            models = [configured_model]
        return {
            "status": "SUCCESS",
            "models": models,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
