from __future__ import annotations

import json

from fastapi.testclient import TestClient

from server.config import ServerSettings
from server.main import create_app
from server.security import redact_payload
from server.security_diagnostics import configuration_diagnostics


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_redact_payload_preserves_capability_metadata_and_hides_values():
    value = redact_payload({
        "cookie_value": "do-not-store",
        "api_key": "do-not-store",
        "cookie_read_supported": True,
        "credential_snapshot_allowed": False,
        "nested": [{"password": "do-not-store"}],
    })
    serialized = json.dumps(value)
    assert "do-not-store" not in serialized
    assert value["cookie_read_supported"] is True
    assert value["credential_snapshot_allowed"] is False


def test_production_diagnostics_never_contains_secret_values():
    settings = ServerSettings(
        database_url="postgresql+psycopg://user:secret-password@db.example/laogu",
        jwt_secret="jwt-secret-value-that-must-not-be-returned-1234567890",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        environment="production",
        https_enabled=True,
        ai_credential_key="not-a-valid-key",
    )
    report = configuration_diagnostics(settings)
    serialized = json.dumps(report)
    assert "secret-password" not in serialized
    assert "jwt-secret-value" not in serialized
    assert report["ok"] is False
    assert report["checks"]["jwt_secret"]["configured"] is True


def test_security_diagnostics_is_admin_only_and_redacted():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="stage10e-test-secret-with-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
    )
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Security Workspace", "username": "admin", "password": "password123"},
    ).json()
    response = client.get("/api/admin/security/diagnostics", headers=auth(bootstrap["access_token"]))
    assert response.status_code == 200
    assert response.json()["service"]["version"] == "0.20.0"
    assert response.json()["websocket"]["http_pull_fallback"] is True
    assert "stage10e-test-secret" not in response.text

    member = client.post(
        "/api/users",
        headers=auth(bootstrap["access_token"]),
        json={"username": "member", "password": "password123", "role": "MEMBER", "workspace_id": bootstrap["workspace_id"]},
    )
    assert member.status_code == 200
    member_token = client.post("/api/auth/login", json={"username": "member", "password": "password123"}).json()["access_token"]
    assert client.get("/api/security/diagnostics", headers=auth(member_token)).status_code == 403
