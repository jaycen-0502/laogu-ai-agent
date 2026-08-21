from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from server.config import ServerSettings
from server.main import create_app


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_authenticated_engine_manifest_and_source():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="engine-update-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
    )
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Engine", "username": "admin", "password": "password123"},
    ).json()
    registered = client.post(
        "/api/agents/register",
        headers=auth(bootstrap["access_token"]),
        json={"agent_name": "Agent", "machine_name": "PC", "client_version": "0.21.6"},
    ).json()
    agent_headers = auth(registered["agent_token"])

    denied = client.get("/api/agent/engine/manifest")
    assert denied.status_code == 401
    manifest_response = client.get("/api/agent/engine/manifest", headers=agent_headers)
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["engine"] == "x_automation_engine"
    assert manifest["read_only"] is True
    assert len(manifest["sha256"]) == 64

    source_response = client.get("/api/agent/engine/source", headers=agent_headers)
    assert source_response.status_code == 200
    source = source_response.content
    assert hashlib.sha256(source).hexdigest() == manifest["sha256"]
    assert source_response.headers["x-laogu-engine-sha256"] == manifest["sha256"]
