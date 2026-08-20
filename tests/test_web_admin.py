from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import select

from server.config import ServerSettings
from server.auth import create_jwt
from server.main import create_app
from server.models import User


ROOT = Path(__file__).resolve().parent.parent


def configured(**overrides) -> ServerSettings:
    values = dict(database_url="sqlite://", jwt_secret="web-admin-test-secret-more-than-thirty-two-bytes", jwt_expire_minutes=60, agent_offline_seconds=90)
    values.update(overrides)
    return ServerSettings(**values)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def web_env():
    settings = configured()
    client = TestClient(create_app(settings.database_url, settings))
    boot = client.post("/api/auth/bootstrap", json={"workspace_name": "Studio A", "username": "admin", "password": "password123"}).json()
    admin = boot["access_token"]
    client.post("/api/users", headers=auth(admin), json={"username": "owner-a", "password": "password123", "role": "OWNER", "workspace_id": boot["workspace_id"]})
    client.post("/api/users", headers=auth(admin), json={"username": "member-a", "password": "password123", "role": "MEMBER", "workspace_id": boot["workspace_id"]})
    owner_a = client.post("/api/auth/login", json={"username": "owner-a", "password": "password123"}).json()["access_token"]
    member_a = client.post("/api/auth/login", json={"username": "member-a", "password": "password123"}).json()["access_token"]
    workspace_b = client.post("/api/workspaces", headers=auth(admin), json={"name": "Studio B"}).json()
    client.post("/api/users", headers=auth(admin), json={"username": "owner-b", "password": "password123", "role": "OWNER", "workspace_id": workspace_b["id"]})
    owner_b = client.post("/api/auth/login", json={"username": "owner-b", "password": "password123"}).json()["access_token"]
    agent_a = client.post("/api/agents/register", headers=auth(owner_a), json={"agent_name": "Agent A", "machine_name": "WEB-PC-A", "client_version": "0.8.0"}).json()
    agent_b = client.post("/api/agents/register", headers=auth(owner_b), json={"agent_name": "Agent B", "machine_name": "WEB-PC-B", "client_version": "0.8.0"}).json()
    account_a = {"profile_id": "profile-11", "instance_id": "instance-11", "x_username": "@webtest", "x_account_id": "111", "login_status": "LOGGED_IN", "browser_status": "RUNNING", "account_status": "VALID", "last_checked": datetime.now(timezone.utc).isoformat()}
    account_b = {"profile_id": "profile-12", "instance_id": "instance-12", "x_username": "@other", "x_account_id": "222", "login_status": "LOGGED_IN", "browser_status": "RUNNING", "account_status": "VALID", "last_checked": datetime.now(timezone.utc).isoformat()}
    client.post("/api/accounts/sync", headers=auth(agent_a["agent_token"]), json={"agent_id": agent_a["agent_id"], "items": [account_a]})
    client.post("/api/accounts/sync", headers=auth(agent_b["agent_token"]), json={"agent_id": agent_b["agent_id"], "items": [account_b]})
    task = client.post("/api/tasks", headers=auth(owner_a), json={"profile_id": "profile-11", "task_type": "x.check_login", "params": {}}).json()
    client.post("/api/tasks/result", headers=auth(agent_a["agent_token"]), json={"task_id": task["task_id"], "agent_id": agent_a["agent_id"], "profile_id": "profile-11", "status": "SUCCESS", "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(), "duration": 0.2, "result": {"login_status": "LOGGED_IN"}})
    return {"client": client, "settings": settings, "boot": boot, "admin": admin, "owner_a": owner_a, "member_a": member_a, "owner_b": owner_b, "workspace_b": workspace_b, "agent_a": agent_a, "agent_b": agent_b, "task": task}


def test_web_login_success(web_env):
    response = web_env["client"].post("/api/auth/login", json={"username": "admin", "password": "password123"})
    assert response.status_code == 200 and response.json()["access_token"]


def test_web_login_failure_is_uniform(web_env):
    response = web_env["client"].post("/api/auth/login", json={"username": "admin", "password": "incorrect"})
    assert response.status_code == 401 and response.json() == {"detail": "Unauthorized"}


def test_web_expired_jwt_returns_401(web_env):
    expired = jwt.encode({"sub": web_env["boot"]["user_id"], "role": "ADMIN", "workspace_id": web_env["boot"]["workspace_id"], "iat": datetime.now(timezone.utc) - timedelta(hours=2), "exp": datetime.now(timezone.utc) - timedelta(hours=1)}, web_env["settings"].jwt_secret, algorithm="HS256")
    assert web_env["client"].get("/api/auth/me", headers=auth(expired)).status_code == 401


def test_admin_dashboard_sees_global_data(web_env):
    data = web_env["client"].get("/api/dashboard?period=all", headers=auth(web_env["admin"])).json()
    assert data["workspace_count"] == 2 and data["agent_count"] == 2


def test_owner_dashboard_is_workspace_scoped(web_env):
    data = web_env["client"].get("/api/dashboard?period=all", headers=auth(web_env["owner_a"])).json()
    assert data["workspace_count"] == 1 and data["agent_count"] == 1


def test_member_dashboard_is_workspace_scoped(web_env):
    data = web_env["client"].get("/api/dashboard?period=all", headers=auth(web_env["member_a"])).json()
    assert data["workspace_count"] == 1 and data["logged_in_accounts"] == 1


def test_workspace_list_isolation(web_env):
    data = web_env["client"].get("/api/workspaces?paged=true", headers=auth(web_env["owner_a"])).json()
    assert data["total"] == 1 and data["items"][0]["name"] == "Studio A"


def test_workspace_detail_counts(web_env):
    workspace_id = web_env["boot"]["workspace_id"]
    data = web_env["client"].get(f"/api/workspaces/{workspace_id}", headers=auth(web_env["owner_a"])).json()
    assert data["agent_count"] == 1 and data["profile_count"] == 1 and data["account_count"] == 1


def test_agent_list_and_detail(web_env):
    data = web_env["client"].get("/api/agents?paged=true", headers=auth(web_env["owner_a"])).json()
    assert data["total"] == 1 and data["items"][0]["agent_name"] == "Agent A"
    detail = web_env["client"].get(f"/api/agents/{web_env['agent_a']['agent_id']}", headers=auth(web_env["owner_a"])).json()
    assert len(detail["profiles"]) == 1 and len(detail["accounts"]) == 1


def test_account_list(web_env):
    data = web_env["client"].get("/api/accounts?paged=true", headers=auth(web_env["owner_a"])).json()
    assert data["total"] == 1 and data["items"][0]["x_username"] == "@webtest"


def test_profile_list(web_env):
    data = web_env["client"].get("/api/profiles?paged=true", headers=auth(web_env["owner_a"])).json()
    assert data["total"] == 1 and data["items"][0]["profile_id"] == "profile-11"


def test_task_list(web_env):
    data = web_env["client"].get("/api/tasks?paged=true", headers=auth(web_env["owner_a"])).json()
    assert data["total"] >= 1 and data["items"][0]["task_type"] == "x.check_login"


def test_task_detail_contains_activity(web_env):
    data = web_env["client"].get(f"/api/tasks/{web_env['task']['task_id']}", headers=auth(web_env["owner_a"])).json()
    assert data["activity"]["status"] == "SUCCESS"


def test_create_read_only_task(web_env):
    response = web_env["client"].post("/api/tasks", headers=auth(web_env["owner_a"]), json={"profile_id": "profile-11", "task_type": "x.search", "params": {"query": "Python"}})
    assert response.status_code == 200 and response.json()["task_type"] == "x.search"


def test_prohibited_task_type_is_rejected(web_env):
    response = web_env["client"].post("/api/tasks", headers=auth(web_env["owner_a"]), json={"profile_id": "profile-11", "task_type": "x.follow", "params": {}})
    assert response.status_code == 422


def test_activity_list_and_audit_permission(web_env):
    activity = web_env["client"].get("/api/activities?paged=true", headers=auth(web_env["member_a"])).json()
    assert activity["total"] >= 1
    assert web_env["client"].get("/api/audit", headers=auth(web_env["member_a"])).status_code == 403


def test_statistics_are_workspace_scoped(web_env):
    data = web_env["client"].get("/api/statistics?period=all", headers=auth(web_env["owner_a"])).json()
    assert data["agents"]["total"] == 1 and data["accounts"]["total"] == 1


def test_user_list_permission(web_env):
    assert web_env["client"].get("/api/users?paged=true", headers=auth(web_env["member_a"])).status_code == 403
    owner = web_env["client"].get("/api/users?paged=true", headers=auth(web_env["owner_a"])).json()
    assert {item["username"] for item in owner["items"]} == {"admin", "owner-a", "member-a"}


def test_admin_can_soft_delete_and_restore_user(web_env):
    created = web_env["client"].post(
        "/api/users",
        headers=auth(web_env["admin"]),
        json={"username": "retired-user", "password": "password123", "role": "MEMBER", "workspace_id": web_env["boot"]["workspace_id"]},
    )
    assert created.status_code == 200
    user_id = created.json()["id"]

    deleted = web_env["client"].patch(f"/api/users/{user_id}", headers=auth(web_env["admin"]), json={"status": "DELETED"})
    assert deleted.status_code == 200 and deleted.json()["status"] == "DELETED"
    assert web_env["client"].post("/api/auth/login", json={"username": "retired-user", "password": "password123"}).status_code == 401

    visible = web_env["client"].get("/api/users?paged=true", headers=auth(web_env["admin"])).json()
    assert "retired-user" not in {item["username"] for item in visible["items"]}
    deleted_list = web_env["client"].get("/api/users?paged=true&include_deleted=true", headers=auth(web_env["admin"])).json()
    retired = next(item for item in deleted_list["items"] if item["username"] == "retired-user")
    assert retired["workspace_name"] == "Studio A" and retired["status"] == "DELETED"

    restored = web_env["client"].patch(f"/api/users/{user_id}", headers=auth(web_env["admin"]), json={"status": "ACTIVE"})
    assert restored.status_code == 200 and restored.json()["status"] == "ACTIVE"
    assert web_env["client"].post("/api/auth/login", json={"username": "retired-user", "password": "password123"}).status_code == 200


def test_server_side_pagination(web_env):
    data = web_env["client"].get("/api/workspaces?paged=true&page=1&page_size=1", headers=auth(web_env["admin"])).json()
    assert len(data["items"]) == 1 and data["total"] == 2 and data["pages"] == 2


def test_server_side_search(web_env):
    data = web_env["client"].get("/api/accounts?paged=true&q=webtest", headers=auth(web_env["admin"])).json()
    assert data["total"] == 1 and data["items"][0]["x_account_id"] == "111"


def test_web_401_handling_source_endpoint(web_env):
    assert web_env["client"].get("/api/dashboard").status_code == 401


def test_web_403_handling_source_endpoint(web_env):
    response = web_env["client"].post("/api/users", headers=auth(web_env["member_a"]), json={"username": "blocked", "password": "password123", "role": "MEMBER"})
    assert response.status_code == 403


def test_web_404_handling_source_endpoint(web_env):
    assert web_env["client"].get("/api/tasks/not-found", headers=auth(web_env["owner_a"])).status_code == 404


def test_web_429_handling_source_endpoint():
    settings = configured(rate_limit_auth=1)
    client = TestClient(create_app(settings.database_url, settings))
    client.post("/api/auth/bootstrap", json={"workspace_name": "Rate", "username": "admin", "password": "password123"})
    assert client.post("/api/auth/login", json={"username": "admin", "password": "password123"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "admin", "password": "password123"}).status_code == 429


def test_web_422_api_error_is_safe(web_env):
    response = web_env["client"].post("/api/tasks", headers=auth(web_env["owner_a"]), json={"profile_id": "profile-11", "task_type": "x.check_login", "params": {"script": "forbidden"}})
    assert response.status_code == 422 and "traceback" not in response.text.lower()


def test_real_stage7_account_data_is_visible_through_web_api(tmp_path):
    source = ROOT / "server" / "e2e-stage7-idempotent.db"
    copied = tmp_path / "real-data.db"
    shutil.copy2(source, copied)
    settings = configured(database_url=f"sqlite:///{copied.as_posix()}")
    client = TestClient(create_app(settings.database_url, settings))
    with client.app.state.SessionLocal() as db:
        user = db.scalar(select(User))
        token = create_jwt(user, settings)
    accounts = client.get("/api/accounts?paged=true", headers=auth(token)).json()["items"]
    profiles = client.get("/api/profiles?paged=true", headers=auth(token)).json()["items"]
    assert any(item["x_username"] == "@ZarrarSiddiqui3" and item["x_account_id"] == "1219971479187517440" for item in accounts)
    assert any(item["profile_id"] for item in profiles)
