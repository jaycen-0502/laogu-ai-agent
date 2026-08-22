from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.config import ServerSettings
from server.main import create_app
from server.models import Agent, User


def make_client(temp_dir):
    settings = ServerSettings(database_url="sqlite://", jwt_secret="test-secret-key-with-more-than-thirty-two-bytes", jwt_expire_minutes=60, agent_offline_seconds=90)
    return TestClient(create_app(settings.database_url, settings))


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def setup_workspace(client):
    bootstrap = client.post("/api/auth/bootstrap", json={"workspace_name": "Studio A", "username": "admin", "password": "password123"}).json()
    admin_token = bootstrap["access_token"]
    owner = client.post("/api/users", headers=auth(admin_token), json={"username": "owner-a", "password": "password123", "role": "OWNER", "workspace_id": bootstrap["workspace_id"]})
    assert owner.status_code == 200
    owner_token = client.post("/api/auth/login", json={"username": "owner-a", "password": "password123"}).json()["access_token"]
    registered = client.post("/api/agents/register", headers=auth(owner_token), json={"agent_name": "Agent A", "machine_name": "PC-A", "client_version": "0.7.0"}).json()
    return bootstrap, admin_token, owner_token, registered


def test_auth_agent_heartbeat_account_task_and_idempotent_result():
    with tempfile.TemporaryDirectory() as temp_dir:
        client = make_client(temp_dir)
        bootstrap, admin_token, owner_token, registered = setup_workspace(client)
        with client.app.state.SessionLocal() as db:
            stored_user = db.scalar(select(User).where(User.username == "owner-a"))
            assert stored_user.password_hash != "password123"
            assert stored_user.password_hash.startswith("$2")
        agent_headers = auth(registered["agent_token"])
        heartbeat = client.post("/api/agents/heartbeat", headers=agent_headers, json={"agent_id": registered["agent_id"], "client_version": "0.8.0", "status": "ONLINE", "profile_count": 1, "running_task_count": 0, "timestamp": datetime.now().astimezone().isoformat()})
        assert heartbeat.status_code == 200
        assert heartbeat.json()["client_version"] == "0.8.0"
        account = {"profile_id": "profile-11", "instance_id": "profile-11", "x_username": "@ZarrarSiddiqui3", "x_account_id": "1219971479187517440", "login_status": "LOGGED_IN", "browser_status": "RUNNING", "account_status": "VALID", "last_checked": datetime.now().astimezone().isoformat(), "mapping_updated_at": datetime.now().astimezone().isoformat()}
        synced = client.post("/api/accounts/sync", headers=agent_headers, json={"agent_id": registered["agent_id"], "items": [account]})
        assert synced.json()["synced"] == 1
        created = client.post("/api/tasks", headers=auth(owner_token), json={"profile_id": "profile-11", "task_type": "x.check_login", "params": {}, "timeout": 30})
        assert created.status_code == 200
        task_id = created.json()["task_id"]
        pulled = client.post("/api/tasks/pull", headers=agent_headers, json={"agent_id": registered["agent_id"], "limit": 10}).json()["items"]
        assert [item["task_id"] for item in pulled] == [task_id]
        payload = {"task_id": task_id, "agent_id": registered["agent_id"], "profile_id": "profile-11", "status": "SUCCESS", "started_at": datetime.now().astimezone().isoformat(), "finished_at": datetime.now().astimezone().isoformat(), "duration": 1.2, "result": {"login_status": "LOGGED_IN"}, "error": None}
        first = client.post("/api/tasks/result", headers=agent_headers, json=payload).json()
        second = client.post("/api/tasks/result", headers=agent_headers, json=payload).json()
        assert first["idempotent"] is False
        assert second["idempotent"] is True
        assert len(client.get("/api/activities", headers=auth(owner_token)).json()) == 1
        assert client.get("/api/statistics", headers=auth(owner_token)).json()["success_tasks"] == 1
        assert client.post("/api/agents/heartbeat", headers=auth("wrong-token"), json={"agent_id": registered["agent_id"], "timestamp": datetime.now().astimezone().isoformat()}).status_code == 401


def test_agent_automation_metrics_are_idempotent_and_bound_to_synced_account():
    with tempfile.TemporaryDirectory() as temp_dir:
        client = make_client(temp_dir)
        _, _, _, registered = setup_workspace(client)
        headers = auth(registered["agent_token"])
        synced = client.post(
            "/api/accounts/sync", headers=headers,
            json={"agent_id": registered["agent_id"], "items": [{"profile_id": "profile-11", "x_username": "@one", "x_account_id": "x-11"}]},
        )
        assert synced.status_code == 200
        timestamp = datetime.now().astimezone().isoformat()
        payload = {
            "agent_id": registered["agent_id"], "run_id": "run-11",
            "profile_id": "profile-11", "x_account_id": "x-11",
            "metric_date": datetime.now().astimezone().date().isoformat(),
            "started_at": timestamp, "finished_at": timestamp, "status": "SUCCESS",
            "processed_count": 7, "likes": 2, "follows": 1,
            "comments": 0, "scanned_posts": 9,
        }
        first = client.post("/api/agent/automation-metrics", headers=headers, json=payload)
        second = client.post("/api/agent/automation-metrics", headers=headers, json=payload)
        assert first.status_code == 200 and first.json()["idempotent"] is False
        assert second.status_code == 200 and second.json()["idempotent"] is True
        mismatch = dict(payload, run_id="run-mismatch", x_account_id="somebody-else")
        assert client.post("/api/agent/automation-metrics", headers=headers, json=mismatch).status_code == 409
        missing = dict(payload, run_id="run-missing", profile_id="missing")
        assert client.post("/api/agent/automation-metrics", headers=headers, json=missing).status_code == 409


def test_workspace_permission_isolation_and_task_type_restriction():
    with tempfile.TemporaryDirectory() as temp_dir:
        client = make_client(temp_dir)
        first, admin_token, owner_a, registered_a = setup_workspace(client)
        second_workspace = client.post("/api/workspaces", headers=auth(admin_token), json={"name": "Studio B"}).json()
        client.post("/api/users", headers=auth(admin_token), json={"username": "owner-b", "password": "password123", "role": "OWNER", "workspace_id": second_workspace["id"]})
        owner_b = client.post("/api/auth/login", json={"username": "owner-b", "password": "password123"}).json()["access_token"]
        client.post("/api/users", headers=auth(owner_b), json={"username": "member-b", "password": "password123", "role": "MEMBER"})
        member_b = client.post("/api/auth/login", json={"username": "member-b", "password": "password123"}).json()["access_token"]
        assert client.post("/api/users", headers=auth(member_b), json={"username": "forbidden", "password": "password123", "role": "MEMBER"}).status_code == 403
        registered_b = client.post("/api/agents/register", headers=auth(owner_b), json={"agent_name": "Agent B", "machine_name": "PC-B", "client_version": "0.7.0"}).json()
        client.post("/api/accounts/sync", headers=auth(registered_a["agent_token"]), json={"agent_id": registered_a["agent_id"], "items": [{"profile_id": "profile-a", "x_username": "@a", "x_account_id": "1"}]})
        client.post("/api/accounts/sync", headers=auth(registered_b["agent_token"]), json={"agent_id": registered_b["agent_id"], "items": [{"profile_id": "profile-b", "x_username": "@b", "x_account_id": "2"}]})
        accounts_a = client.get("/api/accounts", headers=auth(owner_a)).json()
        accounts_b = client.get("/api/accounts", headers=auth(owner_b)).json()
        assert {item["profile_id"] for item in accounts_a} == {"profile-a"}
        assert {item["profile_id"] for item in accounts_b} == {"profile-b"}
        assert client.post("/api/tasks", headers=auth(owner_a), json={"profile_id": "profile-b", "task_type": "x.check_login", "params": {}}).status_code == 404
        assert client.post("/api/tasks", headers=auth(owner_a), json={"profile_id": "profile-a", "task_type": "x.like", "params": {}}).status_code == 422
        assert client.get(f"/api/agents/{registered_b['agent_id']}", headers=auth(owner_a)).status_code == 404
