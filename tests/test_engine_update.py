from __future__ import annotations

import hashlib
import importlib

from fastapi.testclient import TestClient

from server.config import ServerSettings
from server import engine_update_api
from server.main import create_app


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_authenticated_engine_manifest_and_source(monkeypatch, tmp_path):
    monkeypatch.setattr(engine_update_api, "_PUBLISH_DIR", tmp_path / "engine_publish")
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
        json={
            "agent_name": "Agent",
            "machine_name": "PC",
            "client_version": "0.21.7",
            "device_id": "engine-update-device",
        },
    ).json()
    agent_headers = {
        **auth(registered["agent_token"]),
        "X-Laogu-Device-ID": "engine-update-device",
    }

    denied = client.get("/api/agent/engine/manifest")
    assert denied.status_code == 401
    wrong_device = client.get(
        "/api/agent/engine/manifest",
        headers={**auth(registered["agent_token"]), "X-Laogu-Device-ID": "another-device"},
    )
    assert wrong_device.status_code == 403
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


def test_publish_directory_can_be_configured(monkeypatch, tmp_path):
    target = tmp_path / "persistent-engines"
    monkeypatch.setenv("LAOGU_ENGINE_PUBLISH_DIR", str(target))
    module = importlib.import_module("server.engine_update_api")
    module = importlib.reload(module)
    assert module._PUBLISH_DIR == target
    monkeypatch.delenv("LAOGU_ENGINE_PUBLISH_DIR", raising=False)
    importlib.reload(module)


def test_admin_can_publish_and_agent_can_select_named_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(engine_update_api, "_PUBLISH_DIR", tmp_path / "engine_publish")
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
        json={"agent_name": "Agent", "machine_name": "PC", "client_version": "0.21.8", "device_id": "named-engine-device"},
    ).json()
    source = (
        b"import os\n"
        b"import urllib.request\n\n"
        b"class XAutomationEngine:\n"
        b"    async def run(self, config=None):\n"
        b"        return {'status': 'SUCCESS', 'endpoint': os.environ.get('TEST_ENDPOINT', '')}\n"
    )
    published = client.post(
        "/api/admin/engine/publish?engine_id=new-account&name=%E6%96%B0%E5%8F%B7&description=test&version=1.0.0",
        headers=auth(bootstrap["access_token"]),
        content=source,
    )
    assert published.status_code == 200
    assert published.json()["engine_id"] == "new-account"
    assert published.json()["trusted_by_admin"] is True
    assert published.json()["security_warnings"] == ["os", "urllib"]
    agent_headers = {**auth(registered["agent_token"]), "X-Laogu-Device-ID": "named-engine-device"}
    listing = client.get("/api/agent/engines", headers=agent_headers)
    assert listing.status_code == 200
    selected = next(item for item in listing.json()["items"] if item["engine_id"] == "new-account")
    assert selected["name"] == "新号"
    manifest = client.get("/api/agent/engines/new-account/manifest", headers=agent_headers)
    assert manifest.status_code == 200
    assert manifest.json()["trusted_by_admin"] is True
    downloaded = client.get("/api/agent/engines/new-account/source", headers=agent_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == source


def test_admin_can_assign_engines_by_runtime_id(monkeypatch, tmp_path):
    """Explicit grants restrict one runtime without breaking older ALL runtimes."""
    monkeypatch.setattr(engine_update_api, "_PUBLISH_DIR", tmp_path / "engine_publish")
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="engine-assignment-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
    )
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Engine", "username": "admin", "password": "password123"},
    ).json()
    admin = bootstrap["access_token"]
    client.post(
        "/api/users",
        headers=auth(admin),
        json={"username": "owner", "password": "password123", "role": "OWNER", "workspace_id": bootstrap["workspace_id"]},
    )
    owner = client.post("/api/auth/login", json={"username": "owner", "password": "password123"}).json()["access_token"]
    restricted = client.post(
        "/api/agents/register",
        headers=auth(admin),
        json={"agent_name": "运营一组", "machine_name": "PC-A", "client_version": "0.21.21", "device_id": "engine-grant-a"},
    ).json()
    legacy = client.post(
        "/api/agents/register",
        headers=auth(admin),
        json={"agent_name": "运营二组", "machine_name": "PC-B", "client_version": "0.21.21", "device_id": "engine-grant-b"},
    ).json()
    source = b"class XAutomationEngine:\n    async def run(self, config=None):\n        return {'status': 'SUCCESS'}\n"
    assert client.post(
        "/api/admin/engine/publish?engine_id=new-account&name=%E6%96%B0%E5%8F%B7&version=1.0.0",
        headers=auth(admin),
        content=source,
    ).status_code == 200

    forbidden = client.put(
        f"/api/admin/agents/{restricted['agent_id']}/engines",
        headers=auth(owner),
        json={"mode": "ASSIGNED", "engine_ids": ["new-account"]},
    )
    assert forbidden.status_code == 403
    saved = client.put(
        f"/api/admin/agents/{restricted['agent_id']}/engines",
        headers=auth(admin),
        json={"mode": "ASSIGNED", "engine_ids": ["new-account"]},
    )
    assert saved.status_code == 200
    assert saved.json()["agent_id"] == restricted["agent_id"]
    assert saved.json()["engine_access_mode"] == "ASSIGNED"
    assert {item["engine_id"] for item in saved.json()["items"] if item["assigned"]} == {"new-account"}

    restricted_headers = {**auth(restricted["agent_token"]), "X-Laogu-Device-ID": "engine-grant-a"}
    restricted_list = client.get("/api/agent/engines", headers=restricted_headers)
    assert restricted_list.status_code == 200
    assert restricted_list.json()["authorization_enforced"] is True
    assert [item["engine_id"] for item in restricted_list.json()["items"]] == ["new-account"]
    assert client.get("/api/agent/engine/manifest", headers=restricted_headers).status_code == 403
    assert client.get("/api/agent/engines/new-account/manifest", headers=restricted_headers).status_code == 200

    legacy_headers = {**auth(legacy["agent_token"]), "X-Laogu-Device-ID": "engine-grant-b"}
    legacy_list = client.get("/api/agent/engines", headers=legacy_headers)
    assert legacy_list.status_code == 200
    assert legacy_list.json()["authorization_enforced"] is False
    assert {item["engine_id"] for item in legacy_list.json()["items"]} >= {"default", "new-account"}

    audits = client.get("/api/audit?paged=true&page_size=100", headers=auth(admin)).json()["items"]
    assert any(item["action"] == "AGENT_ENGINE_ASSIGNMENTS_UPDATED" and item["agent_id"] == restricted["agent_id"] for item in audits)
