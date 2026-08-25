from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .remote_license_api import _load_issuer_private_key


OFFLINE_ACCESS_PREFIX = "LGOFF1."
OFFLINE_CAPABILITIES = (
    "local.browser.control",
    "local.readonly.run",
    "automation.run",
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def issue_agent_offline_access(settings, agent, *, device_id: str) -> dict:
    """Issue a short-lived, device-bound capability snapshot after heartbeat auth."""
    if not device_id:
        return {}
    try:
        key: Ed25519PrivateKey = _load_issuer_private_key(settings)
    except Exception:
        return {}
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(hours=max(1, int(settings.agent_offline_grace_hours)))
    payload = {
        "version": 1,
        "agent_id": str(agent.id),
        "workspace_id": str(agent.workspace_id),
        "device_id": str(device_id),
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "capabilities": list(OFFLINE_CAPABILITIES),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return {
        "token": OFFLINE_ACCESS_PREFIX + _b64url(raw) + "." + _b64url(key.sign(raw)),
        "public_key": str(settings.license_issuer_public_key),
        **payload,
    }
