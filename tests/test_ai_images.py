from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.config import ServerSettings
from server.image_service import AIImageRequestError, AIImageResult, AIImageService
from server.main import create_app
from server.models import AIImage


API_KEY = "sk-stage9b-image-secret-1234"
FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"stage9b-image-content"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_env(tmp_path):
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="ai-image-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        ai_credential_key=FERNET_KEY,
        ai_image_storage_path=str(tmp_path / "images"),
        rate_limit_ai_image=100,
    )
    client = TestClient(create_app(settings.database_url, settings))
    boot = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Studio A", "username": "admin", "password": "password123"},
    ).json()
    admin = boot["access_token"]
    for username in ("owner-a", "member-a", "member-a2"):
        role = "OWNER" if username == "owner-a" else "MEMBER"
        response = client.post(
            "/api/users",
            headers=auth(admin),
            json={"username": username, "password": "password123", "role": role, "workspace_id": boot["workspace_id"]},
        )
        assert response.status_code == 200

    def login(username: str) -> str:
        return client.post("/api/auth/login", json={"username": username, "password": "password123"}).json()["access_token"]

    env = {
        "client": client,
        "owner": login("owner-a"),
        "member": login("member-a"),
        "member2": login("member-a2"),
        "storage": tmp_path / "images",
    }
    provider = client.post(
        "/api/ai/providers",
        headers=auth(env["owner"]),
        json={
            "name": "Image Relay",
            "provider_type": "OPENAI_COMPATIBLE",
            "base_url": "https://relay.example.com",
            "api_key": API_KEY,
            "default_model": "gpt-5.6-sol",
            "status": "ENABLED",
            "is_default": True,
        },
    )
    assert provider.status_code == 200, provider.text
    env["provider"] = provider.json()
    return env


class FakeImageService:
    def __init__(self, result=None):
        self.result = result or AIImageResult(PNG_BYTES, "image/png", 12, 200, 212)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_generate_list_content_delete_and_audit(tmp_path):
    env = make_env(tmp_path)
    fake = FakeImageService()
    env["client"].app.state.ai_image_service = fake
    response = env["client"].post(
        "/api/ai/images/generate",
        headers=auth(env["member"]),
        json={
            "provider_id": env["provider"]["provider_id"],
            "prompt": "A friendly otter at a lake",
            "resolution": "2K",
            "quality": "high",
        },
    )
    assert response.status_code == 200, response.text
    image = response.json()
    assert image["model"] == "gpt-image-2"
    assert image["status"] == "SUCCESS"
    assert image["size"] == "2048x2048"
    assert image["total_tokens"] == 212
    assert fake.calls[0]["model"] == "gpt-image-2"
    assert fake.calls[0]["api_key"] == API_KEY
    assert fake.calls[0]["size"] == "2048x2048"

    listed = env["client"].get("/api/ai/images", headers=auth(env["member"])).json()
    assert listed["total"] == 1 and listed["items"][0]["image_id"] == image["image_id"]
    content = env["client"].get(image["content_url"], headers=auth(env["member"]));
    assert content.status_code == 200 and content.content == PNG_BYTES
    assert content.headers["content-type"].startswith("image/png")
    assert env["client"].get(image["content_url"], headers=auth(env["member2"])).status_code == 404

    stored = list(env["storage"].rglob(f"{image['image_id']}.png"))
    assert len(stored) == 1 and stored[0].read_bytes() == PNG_BYTES
    actions = {item["action"] for item in env["client"].get("/api/audit", headers=auth(env["owner"])).json()}
    assert "AI_IMAGE_GENERATED" in actions

    deleted = env["client"].delete(f"/api/ai/images/{image['image_id']}", headers=auth(env["member"]))
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert not stored[0].exists()
    assert env["client"].get(image["content_url"], headers=auth(env["member"])).status_code == 404


def test_fixed_model_default_provider_validation_and_failed_record(tmp_path):
    env = make_env(tmp_path)
    env["client"].app.state.ai_image_service = FakeImageService(AIImageRequestError(f"bad {API_KEY}"))
    response = env["client"].post(
        "/api/ai/images/generate",
        headers=auth(env["member"]),
        json={"prompt": "test image", "resolution": "1K", "quality": "medium"},
    )
    assert response.status_code == 502
    assert API_KEY not in response.text
    listed = env["client"].get("/api/ai/images", headers=auth(env["member"])).json()
    assert listed["total"] == 1
    assert listed["items"][0]["status"] == "FAILED"
    assert listed["items"][0]["error_code"] == "PROVIDER_ERROR"
    with env["client"].app.state.SessionLocal() as db:
        row = db.scalar(select(AIImage))
        assert row.model == "gpt-image-2" and API_KEY not in row.error

    assert env["client"].post(
        "/api/ai/images/generate",
        headers=auth(env["member"]),
        json={"prompt": "test", "resolution": "4K", "quality": "medium"},
    ).status_code == 422
    assert env["client"].post(
        "/api/ai/images/generate",
        headers=auth(env["member"]),
        json={"prompt": "test", "resolution": "1K", "quality": "ultra"},
    ).status_code == 422


def test_prompt_secrets_are_redacted_before_provider_and_database(tmp_path):
    env = make_env(tmp_path)
    fake = FakeImageService()
    env["client"].app.state.ai_image_service = fake
    secret = "sk-user-image-secret-9999"
    response = env["client"].post(
        "/api/ai/images/generate",
        headers=auth(env["member"]),
        json={"prompt": f"Draw a card, api_key={secret}"},
    )
    assert response.status_code == 200
    assert secret not in response.text
    assert secret not in fake.calls[0]["prompt"]
    assert "[REDACTED]" in fake.calls[0]["prompt"]


def test_image_service_uses_image_endpoint_and_validates_base64(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}],
                "usage": {"input_tokens": 7, "output_tokens": 99, "total_tokens": 106},
            }

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr("server.image_service.validate_provider_destination", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("server.image_service.httpx.post", fake_post)
    result = AIImageService(180, 1024 * 1024, production=True).generate(
        base_url="https://relay.example.com",
        api_key="relay-key",
        prompt="A lake",
        size="1024x1024",
        quality="medium",
    )
    assert result.content == PNG_BYTES and result.total_tokens == 106
    assert calls[0][0] == "https://relay.example.com/images/generations"
    assert calls[0][1]["json"] == {
        "model": "gpt-image-2",
        "prompt": "A lake",
        "size": "1024x1024",
        "quality": "medium",
        "n": 1,
    }
    assert calls[0][1]["headers"]["Authorization"] == "Bearer relay-key"
