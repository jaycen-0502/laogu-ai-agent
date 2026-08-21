from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from fastapi import Request
import jwt
import pytest
from sqlalchemy import select

from agent.logger import build_logger, log_task_event
from agent.server_client import CredentialStore
from server.auth import token_hash
from server.config import ServerSettings
from server.config_check import ProductionConfigError, check_server_config
from server.main import TOKEN_EXPIRED, TOKEN_REVOKED, create_app
from server.models import AgentToken


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def settings(**overrides) -> ServerSettings:
    base = ServerSettings(
        database_url="sqlite://",
        jwt_secret="test-secret-key-with-more-than-thirty-two-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
    )
    return replace(base, **overrides)


def make_client(**overrides) -> TestClient:
    configured = settings(**overrides)
    return TestClient(create_app(configured.database_url, configured))


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def bootstrap(client: TestClient, workspace: str = "Studio A", username: str = "admin") -> dict:
    response = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": workspace, "username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()


def register_agent(client: TestClient, user_token: str, machine: str = "PC-A") -> dict:
    response = client.post(
        "/api/agents/register",
        headers=auth(user_token),
        json={"agent_name": f"Agent {machine}", "machine_name": machine, "client_version": "0.8.0"},
    )
    assert response.status_code == 200
    return response.json()


def heartbeat(client: TestClient, registered: dict, token: str | None = None):
    return client.post(
        "/api/agents/heartbeat",
        headers=auth(token or registered["agent_token"]),
        json={
            "agent_id": registered["agent_id"],
            "status": "ONLINE",
            "profile_count": 0,
            "running_task_count": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def test_alembic_upgrade_downgrade_upgrade_and_legacy_token_migration(tmp_path, monkeypatch):
    database = tmp_path / "migration.db"
    url = f"sqlite:///{database.as_posix()}"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    monkeypatch.delenv("LAOGU_SERVER_DATABASE_URL", raising=False)

    command.upgrade(config, "0001_stage7")
    legacy_hash = token_hash("legacy-agent-token")
    timestamp = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO workspaces(id,name,status,created_at) VALUES(?,?,?,?)", ("w1", "Legacy", "ACTIVE", timestamp))
        db.execute(
            "INSERT INTO agents(id,workspace_id,agent_name,machine_name,client_version,token_hash,status,last_heartbeat,profile_count,running_task_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("a1", "w1", "Legacy Agent", "Legacy PC", "0.7.0", legacy_hash, "OFFLINE", None, 0, 0, timestamp),
        )

    command.upgrade(config, "head")
    with sqlite3.connect(database) as db:
        migrated = db.execute("SELECT agent_id, token_hash, status FROM agent_tokens").fetchone()
        assert migrated == ("a1", legacy_hash, "ACTIVE")
        assert db.execute("SELECT token_hash FROM agents WHERE id='a1'").fetchone()[0] == ""
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0015_agent_device_bindings"
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scripts'").fetchone() == ("scripts",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_providers'").fetchone() == ("ai_providers",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_sessions'").fetchone() == ("chat_sessions",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_messages'").fetchone() == ("chat_messages",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_usage'").fetchone() == ("ai_usage",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_images'").fetchone() == ("ai_images",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_analyses'").fetchone() == ("ai_analyses",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_writing_records'").fetchone() == ("ai_writing_records",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_task_proposals'").fetchone() == ("ai_task_proposals",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='commands'").fetchone() == ("commands",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='credential_capabilities'").fetchone() == ("credential_capabilities",)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='invitations'").fetchone() == ("invitations",)

    command.downgrade(config, "0001_stage7")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001_stage7"
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_tokens'").fetchone() is None
        assert db.execute("SELECT token_hash FROM agents WHERE id='a1'").fetchone()[0] == legacy_hash

    command.upgrade(config, "head")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT token_hash FROM agent_tokens WHERE agent_id='a1'").fetchone()[0] == legacy_hash


def test_agent_token_rotation_revoke_and_agent_scope():
    client = make_client()
    boot = bootstrap(client)
    client.post(
        "/api/users",
        headers=auth(boot["access_token"]),
        json={"username": "owner", "password": "password123", "role": "OWNER", "workspace_id": boot["workspace_id"]},
    )
    owner_token = client.post("/api/auth/login", json={"username": "owner", "password": "password123"}).json()["access_token"]
    registered = register_agent(client, owner_token)
    old_token = registered["agent_token"]
    assert heartbeat(client, registered).status_code == 200

    rotated = client.post(
        f"/api/agents/{registered['agent_id']}/token/rotate",
        headers=auth(owner_token),
    )
    assert rotated.status_code == 200
    new_token = rotated.json()["agent_token"]
    assert new_token != old_token
    assert heartbeat(client, registered, old_token).status_code == 401
    assert heartbeat(client, registered, new_token).status_code == 200

    assert client.get("/api/workspaces", headers=auth(new_token)).status_code == 401
    assert client.post(
        f"/api/agents/{registered['agent_id']}/token/rotate",
        headers=auth(new_token),
    ).status_code == 401

    revoked = client.post(
        f"/api/agents/{registered['agent_id']}/token/revoke",
        headers=auth(owner_token),
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] == 1
    assert heartbeat(client, registered, new_token).status_code == 401

    with client.app.state.SessionLocal() as db:
        statuses = [item.status for item in db.scalars(select(AgentToken).where(AgentToken.agent_id == registered["agent_id"]))]
        assert statuses == [TOKEN_REVOKED, TOKEN_REVOKED]


def test_expired_agent_token_returns_401_and_updates_status():
    client = make_client()
    boot = bootstrap(client)
    registered = register_agent(client, boot["access_token"])
    with client.app.state.SessionLocal() as db:
        item = db.scalar(select(AgentToken).where(AgentToken.agent_id == registered["agent_id"]))
        item.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    assert heartbeat(client, registered).status_code == 401
    with client.app.state.SessionLocal() as db:
        item = db.scalar(select(AgentToken).where(AgentToken.agent_id == registered["agent_id"]))
        assert item.status == TOKEN_EXPIRED


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI and ACL test")
def test_windows_dpapi_storage_acl_and_plaintext_rejection(tmp_path):
    import win32api
    import win32security

    credential_file = tmp_path / "protected" / "credentials.json"
    store = CredentialStore(credential_file)
    store.save({"agent_id": "agent-1", "agent_token": "dpapi-test-secret", "server_url": "https://example.invalid"})
    raw = json.loads(credential_file.read_text(encoding="utf-8"))
    assert "agent_token" not in raw
    assert "dpapi-test-secret" not in credential_file.read_text(encoding="utf-8")
    assert store.load()["agent_token"] == "dpapi-test-secret"

    descriptor = win32security.GetNamedSecurityInfo(
        str(credential_file.parent),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    actual_sids = {win32security.ConvertSidToStringSid(dacl.GetAce(index)[2]) for index in range(dacl.GetAceCount())}
    process_token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    expected_sids = {
        win32security.ConvertSidToStringSid(sid)
        for sid in (
        win32security.GetTokenInformation(process_token, win32security.TokenUser)[0],
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None),
        win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None),
        )
    }
    assert actual_sids == expected_sids

    credential_file.write_text(json.dumps({"agent_id": "agent-1", "agent_token": "plaintext"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Plaintext Agent Token"):
        store.load()


def test_jwt_login_security_and_expired_token():
    client = make_client()
    boot = bootstrap(client)
    wrong = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    missing = client.post("/api/auth/login", json={"username": "missing", "password": "wrong-password"})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json() == {"detail": "Unauthorized"}
    invalid = client.post(
        "/api/users",
        headers=auth(boot["access_token"]),
        json={"username": "short-password", "password": "secret", "role": "MEMBER", "workspace_id": boot["workspace_id"]},
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "Invalid request"}
    assert "secret" not in invalid.text

    expired = jwt.encode(
        {
            "sub": boot["user_id"],
            "role": "ADMIN",
            "workspace_id": boot["workspace_id"],
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        settings().jwt_secret,
        algorithm="HS256",
    )
    response = client.get("/api/workspaces", headers=auth(expired))
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"jwt_secret": ""},
        {"jwt_secret": "change-me"},
        {"jwt_secret": "short"},
        {"debug": True},
        {"https_enabled": False},
        {"database_url": "sqlite:///production.db"},
        {"database_url": "postgresql+psycopg://laogu@127.0.0.1/laogu"},
    ],
)
def test_invalid_production_configuration_is_rejected(overrides):
    valid = settings(
        environment="production",
        database_url="postgresql+psycopg://laogu:password@127.0.0.1/laogu",
        jwt_secret="production-secret-with-more-than-thirty-two-bytes",
        https_enabled=True,
        ai_credential_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    )
    with pytest.raises(ProductionConfigError):
        check_server_config(replace(valid, **overrides))


def test_valid_production_configuration_and_runtime_bounds():
    valid = settings(
        environment="production",
        database_url="postgresql+psycopg://laogu:password@127.0.0.1/laogu",
        jwt_secret="production-secret-with-more-than-thirty-two-bytes",
        https_enabled=True,
        ai_credential_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    )
    assert check_server_config(valid) == []
    with pytest.raises(ProductionConfigError):
        check_server_config(replace(valid, jwt_expire_minutes=0))
    with pytest.raises(ProductionConfigError):
        check_server_config(replace(valid, agent_token_ttl_days=0))
    with pytest.raises(ProductionConfigError):
        check_server_config(replace(valid, rate_limit_license_issue=0))
    with pytest.raises(ProductionConfigError):
        check_server_config(replace(valid, rate_limit_license_check=0))
    with pytest.raises(ProductionConfigError):
        check_server_config(replace(valid, license_check_retention_days=0))


def test_workspace_agent_task_isolation_and_task_result_routing():
    client = make_client()
    boot = bootstrap(client)
    admin = boot["access_token"]
    agent_a = register_agent(client, admin, "PC-A")
    workspace_b = client.post("/api/workspaces", headers=auth(admin), json={"name": "Studio B"}).json()
    client.post(
        "/api/users",
        headers=auth(admin),
        json={"username": "owner-b", "password": "password123", "role": "OWNER", "workspace_id": workspace_b["id"]},
    )
    owner_b = client.post("/api/auth/login", json={"username": "owner-b", "password": "password123"}).json()["access_token"]
    agent_b = register_agent(client, owner_b, "PC-B")

    client.post("/api/accounts/sync", headers=auth(agent_a["agent_token"]), json={"agent_id": agent_a["agent_id"], "items": [{"profile_id": "profile-a"}]})
    client.post("/api/accounts/sync", headers=auth(agent_b["agent_token"]), json={"agent_id": agent_b["agent_id"], "items": [{"profile_id": "profile-b"}]})
    task = client.post("/api/tasks", headers=auth(admin), json={"profile_id": "profile-a", "task_type": "x.check_login", "params": {}}).json()

    assert client.get(f"/api/agents/{agent_a['agent_id']}", headers=auth(owner_b)).status_code == 404
    assert client.post(f"/api/agents/{agent_a['agent_id']}/token/rotate", headers=auth(owner_b)).status_code == 403
    assert client.post("/api/tasks", headers=auth(owner_b), json={"profile_id": "profile-a", "task_type": "x.check_login", "params": {}}).status_code == 404
    wrong_result = client.post(
        "/api/tasks/result",
        headers=auth(agent_b["agent_token"]),
        json={"task_id": task["task_id"], "agent_id": agent_b["agent_id"], "profile_id": "profile-a", "status": "FAILED"},
    )
    assert wrong_result.status_code == 404


def test_audit_permissions_and_secret_redaction(tmp_path):
    client = make_client()
    boot = bootstrap(client)
    admin = boot["access_token"]
    client.post(
        "/api/auth/login",
        headers={"User-Agent": "Bearer audit-secret-value"},
        json={"username": "admin", "password": "not-the-password"},
    )
    client.post(
        "/api/users",
        headers=auth(admin),
        json={"username": "member", "password": "password123", "role": "MEMBER", "workspace_id": boot["workspace_id"]},
    )
    member = client.post("/api/auth/login", json={"username": "member", "password": "password123"}).json()["access_token"]
    assert client.get("/api/audit", headers=auth(member)).status_code == 403

    response = client.get("/api/audit", headers=auth(admin))
    assert response.status_code == 200
    serialized = json.dumps(response.json())
    assert "audit-secret-value" not in serialized
    assert "not-the-password" not in serialized
    assert any(item["action"] == "LOGIN" and item["result"] == "DENIED" for item in response.json())

    log_file = tmp_path / "agent.log"
    logger = build_logger(log_file)
    log_task_event(logger, task_id="t1", profile_id="p1", profile_name="P1", status="FAILED", operation="test", error="Bearer log-secret-value")
    for handler in logger.handlers:
        handler.flush()
    contents = log_file.read_text(encoding="utf-8")
    assert "log-secret-value" not in contents
    assert "[REDACTED]" in contents


def test_rate_limit_uses_shared_task_bucket_and_request_size_limit():
    client = make_client(rate_limit_auth=2, rate_limit_tasks=1, max_request_bytes=1024)
    boot = bootstrap(client)
    login = {"username": "admin", "password": "password123"}
    assert client.post("/api/auth/login", json=login).status_code == 200
    assert client.post("/api/auth/login", json=login).status_code == 200
    assert client.post("/api/auth/login", json=login).status_code == 429

    assert client.get("/api/tasks", headers=auth(boot["access_token"])).status_code == 200
    second_task_path = client.get("/api/tasks/nonexistent", headers=auth(boot["access_token"]))
    assert second_task_path.status_code == 429
    assert second_task_path.headers["x-content-type-options"] == "nosniff"

    oversized = client.post("/api/auth/login", content=b"x" * 2048, headers={"content-type": "application/json"})
    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "Request payload too large"}
    assert oversized.headers["x-frame-options"] == "DENY"


def test_health_and_readiness_do_not_expose_sensitive_details():
    client = make_client()
    health = client.get("/api/health")
    ready = client.get("/api/health/ready")
    assert health.status_code == ready.status_code == 200
    assert health.json() == ready.json()
    assert health.json().get("ok") is True
    assert health.json().get("release", {}).get("version")
    assert "database" not in json.dumps(ready.json()).lower()


def test_unhandled_error_has_uniform_safe_response():
    client = make_client()

    @client.app.get("/api/test/unhandled")
    def unhandled(request: Request):
        raise RuntimeError("database=C:/secret/path token=secret-value")

    response = client.get("/api/test/unhandled")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret-value" not in response.text
    assert "C:/secret/path" not in response.text
