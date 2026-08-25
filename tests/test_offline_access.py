from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.offline_access import OfflineAccessStore


class PlainProtector:
    def protect(self, value: str) -> str:
        return value

    def unprotect(self, value: str) -> str:
        return value


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _lease(private_key: Ed25519PrivateKey, *, expires_at: datetime, agent_id="a1", device_id="d1", workspace_id="w1") -> dict:
    payload = {
        "version": 1,
        "agent_id": agent_id,
        "device_id": device_id,
        "workspace_id": workspace_id,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat(),
        "capabilities": ["local.browser.control", "local.readonly.run", "automation.run"],
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return {
        "token": "LGOFF1." + _b64(raw) + "." + _b64(private_key.sign(raw)),
        "public_key": _b64(private_key.public_key().public_bytes_raw()),
    }


def test_signed_lease_is_bound_and_revocable(tmp_path):
    key = Ed25519PrivateKey.generate()
    store = OfflineAccessStore(tmp_path / "offline.json", protector=PlainProtector())
    response = _lease(key, expires_at=datetime.now(timezone.utc) + timedelta(hours=2))
    assert store.accept(response, agent_id="a1", device_id="d1", workspace_id="w1")
    assert store.status(agent_id="a1", device_id="d1", workspace_id="w1")["valid"]
    assert not store.status(agent_id="a1", device_id="other", workspace_id="w1")["valid"]
    assert not store.status(agent_id="a1", device_id="d1", workspace_id="other")["valid"]
    store.revoke()
    assert store.status(agent_id="a1", device_id="d1", workspace_id="w1")["capabilities"] == ["local.browser.stop", "local.view"]


def test_tampered_or_expired_lease_is_rejected(tmp_path):
    key = Ed25519PrivateKey.generate()
    store = OfflineAccessStore(tmp_path / "offline.json", protector=PlainProtector())
    response = _lease(key, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert store.accept(response, agent_id="a1", device_id="d1", workspace_id="w1")
    assert not store.status(agent_id="a1", device_id="d1", workspace_id="w1")["valid"]
    tampered = dict(response, token=response["token"][:-1] + ("A" if response["token"][-1] != "A" else "B"))
    assert not store.accept(tampered, agent_id="a1", device_id="d1", workspace_id="w1")
