from __future__ import annotations

from fastapi.testclient import TestClient

from server.config import ServerSettings
from server.main import create_app


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_deleted_agent_can_recover_with_fresh_token_and_reset_binding():
    settings = ServerSettings(database_url="sqlite://", jwt_secret="recovery-test-secret-more-than-32-bytes", jwt_expire_minutes=60, agent_offline_seconds=90)
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post("/api/auth/bootstrap", json={"workspace_name": "Recovery", "username": "admin", "password": "password123"}).json()
    owner = client.post("/api/users", headers=auth(bootstrap["access_token"]), json={"username": "owner", "password": "password123", "role": "OWNER", "workspace_id": bootstrap["workspace_id"]}).json()
    owner_token = client.post("/api/auth/login", json={"username": "owner", "password": "password123"}).json()["access_token"]
    registered = client.post("/api/agents/register", headers=auth(owner_token), json={"agent_name": "PC", "machine_name": "PC", "client_version": "0.21.7", "device_id": "win-old"}).json()
    assert client.delete(f"/api/agents/{registered['agent_id']}", headers=auth(owner_token)).status_code == 200
    recovered = client.post(f"/api/agents/{registered['agent_id']}/recover", headers=auth(owner_token))
    assert recovered.status_code == 200
    body = recovered.json()
    assert body["agent_id"] == registered["agent_id"]
    assert body["agent_token"].startswith("lag_") and body["agent_token"] != registered["agent_token"]
    old = client.post("/api/agents/heartbeat", headers={**auth(registered["agent_token"]), "X-Laogu-Device-ID": "win-old"}, json={"agent_id": registered["agent_id"], "device_id": "win-old", "timestamp": "2026-08-22T00:00:00Z"})
    assert old.status_code == 401
    new = client.post("/api/agents/heartbeat", headers={**auth(body["agent_token"]), "X-Laogu-Device-ID": "win-new"}, json={"agent_id": body["agent_id"], "device_id": "win-new", "timestamp": "2026-08-22T00:00:00Z"})
    assert new.status_code == 200
