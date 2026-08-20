from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from server.ai_provider import AIProviderTester, ProviderConnectionError, normalize_base_url
from server.config import ServerSettings
from server.config_check import ProductionConfigError, check_server_config
from server.main import create_app
from server.models import AIProvider


API_KEY = "sk-stage9a-plain-secret-1234"
FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_env():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="ai-provider-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        ai_credential_key=FERNET_KEY,
    )
    client = TestClient(create_app(settings.database_url, settings))
    boot = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Studio A", "username": "admin", "password": "password123"},
    ).json()
    admin = boot["access_token"]
    client.post(
        "/api/users",
        headers=auth(admin),
        json={"username": "owner-a", "password": "password123", "role": "OWNER", "workspace_id": boot["workspace_id"]},
    )
    client.post(
        "/api/users",
        headers=auth(admin),
        json={"username": "member-a", "password": "password123", "role": "MEMBER", "workspace_id": boot["workspace_id"]},
    )
    owner = client.post("/api/auth/login", json={"username": "owner-a", "password": "password123"}).json()["access_token"]
    member = client.post("/api/auth/login", json={"username": "member-a", "password": "password123"}).json()["access_token"]
    workspace_b = client.post("/api/workspaces", headers=auth(admin), json={"name": "Studio B"}).json()["id"]
    client.post(
        "/api/users",
        headers=auth(admin),
        json={"username": "owner-b", "password": "password123", "role": "OWNER", "workspace_id": workspace_b},
    )
    owner_b = client.post("/api/auth/login", json={"username": "owner-b", "password": "password123"}).json()["access_token"]
    return {
        "client": client,
        "admin": admin,
        "owner": owner,
        "member": member,
        "owner_b": owner_b,
        "workspace_id": boot["workspace_id"],
        "workspace_b": workspace_b,
    }


def create_provider(env, *, name="OpenAI Main", token="owner", is_default=False):
    response = env["client"].post(
        "/api/ai/providers",
        headers=auth(env[token]),
        json={
            "name": name,
            "provider_type": "OPENAI",
            "base_url": "",
            "api_key": API_KEY,
            "default_model": "gpt-test",
            "status": "ENABLED" if is_default else "DISABLED",
            "is_default": is_default,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_api_key_is_encrypted_at_rest_and_never_returned():
    env = make_env()
    item = create_provider(env)
    serialized = str(item)
    assert API_KEY not in serialized
    assert item["api_key_masked"] == "****1234"
    assert item["has_api_key"] is True
    with env["client"].app.state.SessionLocal() as db:
        stored = db.get(AIProvider, item["provider_id"])
        assert stored.api_key_encrypted != API_KEY
        assert API_KEY not in stored.api_key_encrypted


def test_workspace_isolation_and_member_read_only_permissions():
    env = make_env()
    item = create_provider(env)
    assert env["client"].get("/api/ai/providers", headers=auth(env["member"])).json()[0]["provider_id"] == item["provider_id"]
    assert env["client"].patch(
        f"/api/ai/providers/{item['provider_id']}",
        headers=auth(env["member"]),
        json={"name": "blocked"},
    ).status_code == 403
    assert env["client"].get(
        f"/api/ai/providers/{item['provider_id']}",
        headers=auth(env["owner_b"]),
    ).status_code == 404


def test_update_without_key_preserves_ciphertext_and_default_is_unique():
    env = make_env()
    first = create_provider(env, name="First", is_default=True)
    second = create_provider(env, name="Second")
    with env["client"].app.state.SessionLocal() as db:
        before = db.get(AIProvider, second["provider_id"]).api_key_encrypted
    updated = env["client"].patch(
        f"/api/ai/providers/{second['provider_id']}",
        headers=auth(env["owner"]),
        json={"status": "ENABLED", "is_default": True, "default_model": "gpt-new"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["is_default"] is True
    with env["client"].app.state.SessionLocal() as db:
        assert db.get(AIProvider, first["provider_id"]).is_default is False
        assert db.get(AIProvider, second["provider_id"]).api_key_encrypted == before
    disabled = env["client"].patch(
        f"/api/ai/providers/{second['provider_id']}",
        headers=auth(env["owner"]),
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"
    assert disabled.json()["is_default"] is False


def test_connection_test_uses_decrypted_key_saves_models_and_audits():
    env = make_env()
    item = create_provider(env)
    calls = []

    class FakeTester:
        def test(self, base_url, api_key, *, default_model=""):
            calls.append((base_url, api_key, default_model))
            return {"status": "SUCCESS", "models": ["gpt-a", "gpt-b"], "latency_ms": 12}

    env["client"].app.state.ai_provider_tester = FakeTester()
    response = env["client"].post(
        f"/api/ai/providers/{item['provider_id']}/test",
        headers=auth(env["owner"]),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["models"] == ["gpt-a", "gpt-b"]
    assert calls == [("https://api.openai.com/v1", API_KEY, "gpt-test")]
    detail = env["client"].get(f"/api/ai/providers/{item['provider_id']}", headers=auth(env["member"])).json()
    assert detail["last_test_status"] == "SUCCESS" and detail["models"] == ["gpt-a", "gpt-b"]
    actions = {row["action"] for row in env["client"].get("/api/audit", headers=auth(env["owner"])).json()}
    assert {"AI_PROVIDER_CREATED", "AI_PROVIDER_TESTED"}.issubset(actions)


def test_connection_failure_is_safe_and_does_not_leak_key():
    env = make_env()
    item = create_provider(env)

    class FailedTester:
        def test(self, _base_url, api_key, *, default_model=""):
            raise ProviderConnectionError(f"failed with {api_key}")

    env["client"].app.state.ai_provider_tester = FailedTester()
    response = env["client"].post(
        f"/api/ai/providers/{item['provider_id']}/test",
        headers=auth(env["owner"]),
    )
    assert response.status_code == 200 and response.json()["status"] == "FAILED"
    assert API_KEY not in response.text
    assert "[REDACTED]" in response.json()["error"]


def test_disabled_provider_can_be_deleted_but_enabled_provider_cannot():
    env = make_env()
    disabled = create_provider(env, name="Delete Me")
    enabled = create_provider(env, name="Keep Me", is_default=True)
    assert env["client"].delete(
        f"/api/ai/providers/{enabled['provider_id']}", headers=auth(env["owner"])
    ).status_code == 409
    deleted = env["client"].delete(
        f"/api/ai/providers/{disabled['provider_id']}", headers=auth(env["owner"])
    )
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True


def test_provider_url_and_production_encryption_key_validation():
    with pytest.raises(ValueError, match="Private"):
        normalize_base_url("OPENAI_COMPATIBLE", "http://127.0.0.1:8080/v1")
    valid = ServerSettings(
        database_url="postgresql+psycopg://laogu:password@127.0.0.1/laogu",
        jwt_secret="production-secret-with-more-than-thirty-two-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        environment="production",
        https_enabled=True,
        ai_credential_key=FERNET_KEY,
    )
    assert check_server_config(valid) == []
    with pytest.raises(ProductionConfigError):
        check_server_config(replace(valid, ai_credential_key=""))


def test_provider_tester_uses_models_endpoint_and_bearer_header(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"object": "list", "data": [{"id": "gpt-z"}, {"id": "gpt-a"}]}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr("server.ai_provider.socket.getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))])
    monkeypatch.setattr("server.ai_provider.httpx.get", fake_get)
    result = AIProviderTester(5, production=True).test("https://api.example.com/v1", API_KEY)
    assert result["status"] == "SUCCESS" and result["models"] == ["gpt-a", "gpt-z"]
    assert calls[0][0] == "https://api.example.com/v1/models"
    assert calls[0][1]["headers"]["Authorization"] == f"Bearer {API_KEY}"


def test_provider_tester_falls_back_to_authenticated_responses_probe(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            if self._payload is None:
                raise ValueError
            return self._payload

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/models"):
            return FakeResponse(200)
        return FakeResponse(426, {"detail": "Use a POST streaming request"})

    monkeypatch.setattr("server.ai_provider.socket.getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))])
    monkeypatch.setattr("server.ai_provider.httpx.get", fake_get)
    result = AIProviderTester(5, production=True).test(
        "https://relay.example.com",
        API_KEY,
        default_model="gpt-5.6-sol",
    )
    assert result["status"] == "SUCCESS"
    assert result["models"] == ["gpt-5.6-sol"]
    assert [item[0] for item in calls] == [
        "https://relay.example.com/models",
        "https://relay.example.com/responses",
    ]
    assert calls[1][1]["headers"]["Authorization"] == f"Bearer {API_KEY}"
