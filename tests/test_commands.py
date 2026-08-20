from __future__ import annotations

from fastapi.testclient import TestClient

from server.config import ServerSettings
from server.main import create_app
from server.models import Profile
from datetime import datetime, timedelta
from server.models import Command, CredentialCapability, now


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_env():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="command-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        ai_credential_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    )
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Command Workspace", "username": "admin", "password": "password123"},
    ).json()
    admin = bootstrap["access_token"]
    owner = client.post(
        "/api/users",
        headers=auth(admin),
        json={"username": "owner", "password": "password123", "role": "OWNER", "workspace_id": bootstrap["workspace_id"]},
    )
    assert owner.status_code == 200, owner.text
    owner_token = client.post("/api/auth/login", json={"username": "owner", "password": "password123"}).json()["access_token"]
    registered = client.post(
        "/api/agents/register",
        headers=auth(owner_token),
        json={"agent_name": "Command Agent", "machine_name": "command-pc", "client_version": "0.12.0"},
    ).json()
    with client.app.state.SessionLocal() as db:
        profile = Profile(
            workspace_id=bootstrap["workspace_id"],
            agent_id=registered["agent_id"],
            profile_id="profile-command",
            status="RUNNING",
        )
        db.add(profile)
        db.commit()
    return client, owner_token, registered


def test_command_http_fallback_state_machine_and_idempotency():
    client, owner, agent = make_env()
    body = {
        "agent_id": agent["agent_id"],
        "profile_id": "profile-command",
        "command_type": "REFRESH_PROFILE",
        "payload": {},
        "idempotency_key": "refresh-1",
    }
    created = client.post("/api/commands", headers=auth(owner), json=body)
    assert created.status_code == 200, created.text
    command = created.json()["command"]
    duplicate = client.post("/api/commands", headers=auth(owner), json=body)
    assert duplicate.status_code == 200
    assert duplicate.json()["idempotent"] is True
    assert duplicate.json()["command"]["command_id"] == command["command_id"]

    agent_headers = auth(agent["agent_token"])
    pulled = client.post(
        "/api/agent/commands/pull",
        headers=agent_headers,
        json={"agent_id": agent["agent_id"], "limit": 10},
    )
    assert pulled.status_code == 200, pulled.text
    assert pulled.json()["items"][0]["status"] == "DELIVERED"
    acknowledged = client.post(
        f"/api/agent/commands/{command['command_id']}/ack",
        headers=agent_headers,
        json={"agent_id": agent["agent_id"], "status": "RUNNING"},
    )
    assert acknowledged.status_code == 200
    completed = client.post(
        f"/api/agent/commands/{command['command_id']}/result",
        headers=agent_headers,
        json={"agent_id": agent["agent_id"], "status": "SUCCESS", "result": {"refreshed": True}},
    )
    assert completed.status_code == 200
    assert completed.json()["command"]["status"] == "SUCCESS"
    repeated = client.post(
        f"/api/agent/commands/{command['command_id']}/result",
        headers=agent_headers,
        json={"agent_id": agent["agent_id"], "status": "FAILED", "error": "late"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True
    assert client.get("/api/commands", headers=auth(owner)).json()["total"] == 1


def test_command_workspace_and_role_boundaries():
    client, owner, agent = make_env()
    workspace_id = client.get("/api/auth/me", headers=auth(owner)).json()["workspace_id"]
    member = client.post(
        "/api/users",
        headers=auth(owner),
        json={"username": "member", "password": "password123", "role": "MEMBER", "workspace_id": workspace_id},
    )
    assert member.status_code == 200, member.text
    member_token = client.post("/api/auth/login", json={"username": "member", "password": "password123"}).json()["access_token"]
    command = client.post(
        "/api/commands",
        headers=auth(owner),
        json={"agent_id": agent["agent_id"], "profile_id": "profile-command", "command_type": "STOP_PROFILE"},
    ).json()["command"]
    assert client.post(
        "/api/commands",
        headers=auth(member_token),
        json={"agent_id": agent["agent_id"], "profile_id": "profile-command", "command_type": "START_PROFILE"},
    ).status_code == 403
    assert client.post(f"/api/commands/{command['command_id']}/cancel", headers=auth(member_token)).status_code == 403
    assert client.get("/api/commands", headers=auth(member_token)).status_code == 200


def test_command_websocket_push_and_result():
    client, owner, agent = make_env()
    command = client.post(
        "/api/commands",
        headers=auth(owner),
        json={"agent_id": agent["agent_id"], "profile_id": "profile-command", "command_type": "REFRESH_PROFILE"},
    ).json()["command"]
    with client.websocket_connect("/api/agent/commands/ws", headers=auth(agent["agent_token"])) as socket:
        pushed = socket.receive_json()
        assert pushed["type"] == "command"
        assert pushed["command"]["command_id"] == command["command_id"]
        socket.send_json({"type": "ack", "command_id": command["command_id"], "status": "RUNNING"})
        socket.send_json({"type": "result", "command_id": command["command_id"], "status": "SUCCESS", "result": {"ok": True}})
    detail = client.get(f"/api/commands/{command['command_id']}", headers=auth(owner)).json()
    assert detail["status"] == "SUCCESS"


def test_command_metrics_and_expired_websocket_lease_redelivery():
    client, owner, agent = make_env()
    created = client.post(
        "/api/commands",
        headers=auth(owner),
        json={"agent_id": agent["agent_id"], "profile_id": "profile-command", "command_type": "REFRESH_PROFILE"},
    ).json()["command"]
    with client.app.state.SessionLocal() as db:
        item = db.get(Command, created["command_id"])
        item.status = "DELIVERED"
        item.delivered_at = datetime.now() - timedelta(seconds=120)
        db.commit()
    metrics = client.get("/api/commands/metrics", headers=auth(owner))
    assert metrics.status_code == 200
    assert metrics.json()["stale_delivered"] == 1
    with client.websocket_connect("/api/agent/commands/ws", headers=auth(agent["agent_token"])) as socket:
        pushed = socket.receive_json()
        assert pushed["command"]["command_id"] == created["command_id"]


def test_credential_probe_result_is_strictly_sanitized_and_stored():
    client, owner, agent = make_env()
    created = client.post(
        "/api/commands",
        headers=auth(owner),
        json={"agent_id": agent["agent_id"], "profile_id": "profile-command", "command_type": "PROBE_CREDENTIAL_CAPABILITY"},
    ).json()["command"]
    headers = auth(agent["agent_token"])
    client.post("/api/agent/commands/pull", headers=headers, json={"agent_id": agent["agent_id"], "limit": 10})
    client.post(f"/api/agent/commands/{created['command_id']}/ack", headers=headers, json={"agent_id": agent["agent_id"], "status": "RUNNING"})
    completed = client.post(
        f"/api/agent/commands/{created['command_id']}/result",
        headers=headers,
        json={"agent_id": agent["agent_id"], "status": "SUCCESS", "result": {"browser_reachable": True, "cookie_read_supported": False, "cookie_write_supported": False, "credential_snapshot_allowed": True, "evidence": "NOT_ADVERTISED", "cookie_value": "secret"}},
    )
    assert completed.status_code == 200
    result = completed.json()["command"]["result"]
    assert result["credential_snapshot_allowed"] is False
    assert "cookie_value" not in result
    capabilities = client.get("/api/credential-capabilities", headers=auth(owner)).json()
    assert capabilities[0]["evidence"] == "NOT_ADVERTISED"
    assert "secret" not in str(capabilities)
