from datetime import datetime, timezone

from fastapi.testclient import TestClient

from server.config import ServerSettings
from server.main import create_app


def auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_deleted_agent_recovery_issues_new_token_and_resets_binding():
    settings = ServerSettings(database_url="sqlite://", jwt_secret="recovery-test-secret-more-than-32-bytes", jwt_expire_minutes=60, agent_offline_seconds=90)
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post("/api/auth/bootstrap", json={"workspace_name": "Recovery", "username": "admin", "password": "password123"}).json()
    owner = client.post("/api/users", headers=auth(bootstrap["access_token"]), json={"username": "owner", "password": "password123", "role": "OWNER", "workspace_id": bootstrap["workspace_id"]})
    owner_token = client.post("/api/auth/login", json={"username": "owner", "password": "password123"}).json()["access_token"]
    registered = client.post("/api/agents/register", headers=auth(owner_token), json={"agent_name": "PC", "machine_name": "PC", "client_version": "0.21.7", "device_id": "win-old"}).json()
    assert client.delete(f"/api/agents/{registered['agent_id']}", headers=auth(owner_token)).status_code == 200
    recovered = client.post(f"/api/agents/{registered['agent_id']}/recover", headers=auth(owner_token))
    assert recovered.status_code == 200
    body = recovered.json()
    assert body["agent_id"] == registered["agent_id"]
    assert body["agent_token"] != registered["agent_token"]
    assert body["status"] == "OFFLINE"

    heartbeat = {
        "agent_id": registered["agent_id"],
        "device_id": "win-new",
        "client_version": "0.21.7",
        "status": "ONLINE",
        "profile_count": 0,
        "running_task_count": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    old_auth = client.post("/api/agents/heartbeat", headers=auth(registered["agent_token"]), json=heartbeat)
    assert old_auth.status_code == 401

    rebound = client.post("/api/agents/heartbeat", headers=auth(body["agent_token"]), json=heartbeat)
    assert rebound.status_code == 200
    assert rebound.json()["binding_status"] == "BOUND"
