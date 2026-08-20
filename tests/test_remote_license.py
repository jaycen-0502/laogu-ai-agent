from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from server.config import ServerSettings
from server.main import create_app


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _code(private_key: Ed25519PrivateKey, *, license_id: str = "lic_test_1", device_id: str = "device-1", expires_days: int = 30) -> str:
    expires = datetime.now(timezone.utc) + timedelta(days=expires_days)
    issued = datetime.now(timezone.utc) if expires_days >= 0 else expires - timedelta(days=1)
    payload = {
        "version": 1,
        "licenseId": license_id,
        "customer": "Test Customer",
        "deviceId": device_id,
        "installPublicKey": "install-key-1",
        "requestNonce": "nonce-1",
        "issuedAt": issued.isoformat().replace("+00:00", "Z"),
        "expiresAt": expires.isoformat().replace("+00:00", "Z"),
        "features": ["browser", "playwright"],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "LGACT1." + _b64(raw) + "." + _b64(private_key.sign(raw))


def _client():
    private_key = Ed25519PrivateKey.generate()
    public_key = _b64(private_key.public_key().public_bytes_raw())
    settings = ServerSettings(database_url="sqlite://", jwt_secret="remote-license-test-secret-more-than-32", jwt_expire_minutes=60, agent_offline_seconds=90, license_issuer_public_key=public_key)
    return TestClient(create_app(settings.database_url, settings)), private_key


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_remote_license_register_check_device_binding_and_revoke():
    client, private_key = _client()
    boot = client.post("/api/auth/bootstrap", json={"workspace_name": "Studio", "username": "admin", "password": "password123"}).json()
    code = _code(private_key)
    registered = client.post("/api/license/register", headers=_auth(boot["access_token"]), json={"activation_code": code, "offline_grace_days": 7})
    assert registered.status_code == 200
    assert "activation_code" not in registered.text

    checked = client.post("/api/license/check", json={"activation_code": code, "device_id": "device-1", "install_public_key": "install-key-1", "app_version": "1.0"})
    assert checked.status_code == 200 and checked.json()["ok"] is True
    mismatch = client.post("/api/license/check", json={"activation_code": code, "device_id": "device-2", "install_public_key": "install-key-1"})
    assert mismatch.status_code == 403

    devices = client.get("/api/license/lic_test_1/devices", headers=_auth(boot["access_token"]))
    assert devices.status_code == 200
    assert devices.json()[0]["device_id"] == "device-1"
    assert "install_public_key" not in devices.text

    checks = client.get("/api/license/lic_test_1/checks?limit=10", headers=_auth(boot["access_token"]))
    assert checks.status_code == 200
    assert checks.json()[0]["result"] == "VALID"
    assert "activation_code" not in checks.text

    revoked = client.post("/api/license/revoke", headers=_auth(boot["access_token"]), json={"license_id": "lic_test_1", "reason": "test"})
    assert revoked.status_code == 200
    after = client.post("/api/license/check", json={"activation_code": code, "device_id": "device-1", "install_public_key": "install-key-1"})
    assert after.status_code == 200 and after.json()["state"] == "REVOKED" and after.json()["ok"] is False


def test_remote_license_expired_and_non_admin_forbidden():
    client, private_key = _client()
    boot = client.post("/api/auth/bootstrap", json={"workspace_name": "Studio", "username": "admin", "password": "password123"}).json()
    owner = client.post("/api/users", headers=_auth(boot["access_token"]), json={"username": "owner", "password": "password123", "role": "OWNER", "workspace_id": boot["workspace_id"]}).json()
    owner_token = client.post("/api/auth/login", json={"username": "owner", "password": "password123"}).json()["access_token"]
    expired = _code(private_key, license_id="lic_expired", expires_days=-1)
    assert client.post("/api/license/register", headers=_auth(owner_token), json={"activation_code": expired}).status_code == 403
    assert client.post("/api/license/register", headers=_auth(boot["access_token"]), json={"activation_code": expired}).status_code == 200
    result = client.post("/api/license/check", json={"activation_code": expired, "device_id": "device-1", "install_public_key": "install-key-1"})
    assert result.status_code == 200 and result.json()["state"] == "EXPIRED"
    assert client.get("/api/license/lic_expired/devices", headers=_auth(owner_token)).status_code == 403
    assert client.get("/api/license/lic_expired/checks", headers=_auth(owner_token)).status_code == 403


def test_remote_license_admin_views_mask_sensitive_device_metadata():
    client, private_key = _client()
    boot = client.post("/api/auth/bootstrap", json={"workspace_name": "Studio", "username": "admin", "password": "password123"}).json()
    code = _code(private_key, license_id="lic_masked", device_id="0123456789abcdef0123456789abcdef")
    assert client.post("/api/license/register", headers=_auth(boot["access_token"]), json={"activation_code": code}).status_code == 200
    assert client.post(
        "/api/license/check",
        headers={"X-Forwarded-For": "203.0.113.42"},
        json={"activation_code": code, "device_id": "0123456789abcdef0123456789abcdef", "install_public_key": "install-key-1", "app_version": "1.0"},
    ).status_code == 200

    devices = client.get("/api/license/lic_masked/devices", headers=_auth(boot["access_token"])).json()
    assert devices[0]["device_id"] == "01234567...abcdef"
    assert devices[0]["last_ip"] != "203.0.113.42"
    checks = client.get("/api/license/lic_masked/checks", headers=_auth(boot["access_token"])).json()
    assert checks[0]["device_id"] == "01234567...abcdef"
    assert checks[0]["ip"] != "203.0.113.42"
