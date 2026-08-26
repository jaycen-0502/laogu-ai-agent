from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.config import ServerSettings
from server.main import TOKEN_REVOKED, create_app
from server.models import Agent, AgentToken


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_disabling_user_revokes_only_their_linked_agent_tokens():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="user-agent-revocation-test-secret-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
    )
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Revocation", "username": "admin", "password": "password123"},
    ).json()
    admin_token = bootstrap["access_token"]
    owner = client.post(
        "/api/users",
        headers=auth(admin_token),
        json={
            "username": "owner",
            "password": "password123",
            "role": "OWNER",
            "workspace_id": bootstrap["workspace_id"],
        },
    ).json()
    owner_token = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "password123"},
    ).json()["access_token"]
    owner_agent = client.post(
        "/api/agents/register",
        headers=auth(owner_token),
        json={"agent_name": "Owner PC", "machine_name": "OWNER-PC", "client_version": "0.21.13"},
    ).json()
    admin_agent = client.post(
        "/api/agents/register",
        headers=auth(admin_token),
        json={"agent_name": "Admin PC", "machine_name": "ADMIN-PC", "client_version": "0.21.13"},
    ).json()

    disabled = client.patch(
        f"/api/users/{owner['id']}",
        headers=auth(admin_token),
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["linked_agent_count"] == 1
    assert disabled.json()["revoked_agent_tokens"] == 1
    assert client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "password123"},
    ).status_code == 401

    heartbeat = {"agent_id": owner_agent["agent_id"], "client_version": "0.21.13", "status": "ONLINE", "timestamp": datetime.now(timezone.utc).isoformat()}
    assert client.post("/api/agents/heartbeat", headers=auth(owner_agent["agent_token"]), json=heartbeat).status_code == 401
    admin_heartbeat = {"agent_id": admin_agent["agent_id"], "client_version": "0.21.13", "status": "ONLINE", "timestamp": datetime.now(timezone.utc).isoformat()}
    assert client.post("/api/agents/heartbeat", headers=auth(admin_agent["agent_token"]), json=admin_heartbeat).status_code == 200

    with client.app.state.SessionLocal() as db:
        linked_agent = db.get(Agent, owner_agent["agent_id"])
        linked_token = db.scalar(select(AgentToken).where(AgentToken.agent_id == owner_agent["agent_id"]))
        admin_token_record = db.scalar(select(AgentToken).where(AgentToken.agent_id == admin_agent["agent_id"]))
        assert linked_agent.status == "OFFLINE"
        assert linked_agent.last_heartbeat is None
        assert linked_token.status == TOKEN_REVOKED
        assert admin_token_record.status == "ACTIVE"

    repeated = client.patch(
        f"/api/users/{owner['id']}",
        headers=auth(admin_token),
        json={"status": "DISABLED"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["linked_agent_count"] == 1
    assert repeated.json()["revoked_agent_tokens"] == 0

    enabled = client.patch(
        f"/api/users/{owner['id']}",
        headers=auth(admin_token),
        json={"status": "ACTIVE"},
    )
    assert enabled.status_code == 200
    assert client.post("/api/agents/heartbeat", headers=auth(owner_agent["agent_token"]), json=heartbeat).status_code == 401
