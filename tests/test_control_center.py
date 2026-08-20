from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.config import ServerSettings
from server.main import create_app
from server.models import Account, Activity, Agent, AuditLog, Profile, Script, ScriptVersion, Task


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_env():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="control-center-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        ai_credential_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    )
    client = TestClient(create_app(settings.database_url, settings))
    boot = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Primary", "username": "admin", "password": "password123"},
    ).json()
    admin_token = boot["access_token"]
    second_workspace = client.post("/api/workspaces", headers=auth(admin_token), json={"name": "Secondary"}).json()
    member = client.post(
        "/api/users",
        headers=auth(admin_token),
        json={"username": "member", "password": "password123", "role": "MEMBER", "workspace_id": boot["workspace_id"]},
    )
    assert member.status_code == 200, member.text
    member_token = client.post("/api/auth/login", json={"username": "member", "password": "password123"}).json()["access_token"]
    now = datetime.now(timezone.utc)
    with client.app.state.SessionLocal() as db:
        agent_one = Agent(workspace_id=boot["workspace_id"], agent_name="Agent Primary", machine_name="primary", client_version="1.0", status="ONLINE", last_heartbeat=now)
        agent_two = Agent(workspace_id=second_workspace["id"], agent_name="Agent Secondary", machine_name="secondary", client_version="1.0", status="OFFLINE")
        db.add_all([agent_one, agent_two]); db.flush()
        profile_one = Profile(workspace_id=boot["workspace_id"], agent_id=agent_one.id, profile_id="profile-one", x_username="@primary", status="RUNNING")
        profile_two = Profile(workspace_id=second_workspace["id"], agent_id=agent_two.id, profile_id="profile-two", x_username="@secondary", status="STOPPED")
        db.add_all([profile_one, profile_two]); db.flush()
        account = Account(workspace_id=boot["workspace_id"], agent_id=agent_one.id, profile_id=profile_one.profile_id, x_username="@primary", login_status="LOGGED_IN", browser_status="RUNNING", account_status="VALID", last_checked=now)
        script = Script(workspace_id=boot["workspace_id"], name="Control script", status="ENABLED", current_version=1, created_by=boot["user_id"])
        db.add_all([account, script]); db.flush()
        version = ScriptVersion(script_id=script.id, version=1, source="module.exports.run = async () => ({ok:true})", params_schema={}, sha256="a" * 64, created_by=boot["user_id"])
        task = Task(workspace_id=boot["workspace_id"], agent_id=agent_one.id, profile_id=profile_one.profile_id, task_type="script.execute", script_id=script.id, script_version_id=version.id if version.id else None, params={}, timeout=30, status="RUNNING", created_at=now)
        db.add(version); db.flush(); task.script_version_id = version.id; db.add(task); db.flush()
        db.add(Activity(workspace_id=boot["workspace_id"], agent_id=agent_one.id, profile_id=profile_one.profile_id, x_account_id="", task_id=task.id, script_id=script.id, script_version_id=version.id, activity_type="script.execute", status="RUNNING", summary="script.execute", timestamp=now))
        db.add(AuditLog(workspace_id=boot["workspace_id"], action="CONTROL_TEST", resource_type="task", resource_id=task.id, result="SUCCESS", timestamp=now))
        db.commit()
        profile_one_id, profile_two_id = profile_one.id, profile_two.id
    return client, admin_token, member_token, profile_one_id, profile_two_id


def test_control_overview_aggregates_data_and_profile_detail_is_scoped():
    client, admin_token, member_token, primary_profile_id, secondary_profile_id = make_env()
    overview = client.get("/api/control/overview", headers=auth(member_token))
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["scope"] == "workspace"
    assert body["summary"]["profile_count"] == 1
    assert body["summary"]["online_agents"] == 1
    assert body["summary"]["running_tasks"] == 1
    assert body["summary"]["logged_in_accounts"] == 1
    assert body["profiles"][0]["profile_record_id"] == primary_profile_id
    assert body["profiles"][0]["current_task"]["status"] == "RUNNING"
    detail = client.get(f"/api/control/profiles/{primary_profile_id}", headers=auth(member_token))
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["tasks"]) == 1
    assert client.get(f"/api/control/profiles/{secondary_profile_id}", headers=auth(member_token)).status_code == 404
    timeline = client.get("/api/control/timeline?limit=10", headers=auth(member_token))
    assert timeline.status_code == 200
    assert {item["event_type"] for item in timeline.json()["items"]} == {"activity", "audit"}

    admin_overview = client.get("/api/control/overview", headers=auth(admin_token))
    assert admin_overview.status_code == 200
    assert admin_overview.json()["scope"] == "global"
    assert admin_overview.json()["summary"]["profile_count"] == 2
