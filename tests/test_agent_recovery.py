from datetime import datetime, timezone

from fastapi.testclient import TestClient

from server.config import ServerSettings
from server.main import create_app


def auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_revoked_agent_recovery_issues_new_token_and_resets_binding():
    settings = ServerSettings(database_url="sqlite://", jwt_secret="recovery-test-secret-more-than-32-bytes", jwt_expire_minutes=60, agent_offline_seconds=90)
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post("/api/auth/bootstrap", json={"workspace_name": "Recovery", "username": "admin", "password": "password123"}).json()
    owner = client.post("/api/users", headers=auth(bootstrap["access_token"]), json={"username": "owner", "password": "password123", "role": "OWNER", "workspace_id": bootstrap["workspace_id"]})
    owner_token = client.post("/api/auth/login", json={"username": "owner", "password": "password123"}).json()["access_token"]
    registered = client.post("/api/agents/register", headers=auth(owner_token), json={"agent_name": "PC", "device_id": "win-old"}).json()
    
    # 取消运行端授权
    revoked = client.post(f"/api/agents/{registered['agent_id']}/revoke-auth", headers=auth(owner_token))
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "UNAUTHORIZED"

    # 恢复授权
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


def test_permanent_purge_agent_leaves_no_residue():
    settings = ServerSettings(database_url="sqlite://", jwt_secret="recovery-test-secret-more-than-32-bytes", jwt_expire_minutes=60, agent_offline_seconds=90)
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post("/api/auth/bootstrap", json={"workspace_name": "PurgeTest", "username": "admin", "password": "password123"}).json()
    owner = client.post("/api/users", headers=auth(bootstrap["access_token"]), json={"username": "owner2", "password": "password123", "role": "OWNER", "workspace_id": bootstrap["workspace_id"]})
    owner_token = client.post("/api/auth/login", json={"username": "owner2", "password": "password123"}).json()["access_token"]
    
    # 注册不传机器名称和版本
    registered = client.post("/api/agents/register", headers=auth(owner_token), json={"agent_name": "PurgePC"}).json()
    agent_id = registered["agent_id"]

    # 查列表能查到
    agent_list = client.get("/api/agents", headers=auth(owner_token)).json()
    assert any(a["agent_id"] == agent_id for a in agent_list)

    # 彻底整列删除
    del_res = client.delete(f"/api/agents/{agent_id}", headers=auth(owner_token))
    assert del_res.status_code == 200
    assert del_res.json()["purged"] is True

    # 查详情 404
    assert client.get(f"/api/agents/{agent_id}", headers=auth(owner_token)).status_code == 404

    # 查列表彻底不残留任何条目
    agent_list_after = client.get("/api/agents", headers=auth(owner_token)).json()
    assert not any(a["agent_id"] == agent_id for a in agent_list_after)
