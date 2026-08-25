from __future__ import annotations

from fastapi.testclient import TestClient

from server.config import ServerSettings
from server.main import create_app
from server.models import AIImage


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_env(storage_path=""):
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="user-admin-cleanup-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        ai_image_storage_path=str(storage_path),
    )
    client = TestClient(create_app(settings.database_url, settings))
    boot = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Studio", "username": "admin", "password": "password123"},
    ).json()
    created = client.post(
        "/api/users",
        headers=auth(boot["access_token"]),
        json={
            "username": "member",
            "password": "password123",
            "role": "MEMBER",
            "workspace_id": boot["workspace_id"],
        },
    ).json()
    member = client.post(
        "/api/auth/login",
        json={"username": "member", "password": "password123"},
    ).json()["access_token"]
    return client, boot, created, member


def test_admin_user_list_contains_usage_and_online_fields():
    client, boot, _, _ = make_env()
    payload = client.get("/api/users?paged=true", headers=auth(boot["access_token"])).json()
    assert payload["items"]
    assert {"ai_total_tokens", "storage_bytes", "last_seen_at", "online"}.issubset(payload["items"][0])


def test_cache_and_permanent_delete_are_admin_only_and_self_delete_is_blocked():
    client, boot, created, member = make_env()
    user_id = created["id"]

    assert client.post(f"/api/users/{user_id}/clear-cache", headers=auth(member)).status_code == 403
    assert client.delete(f"/api/users/{user_id}", headers=auth(member)).status_code == 403
    assert client.delete(f"/api/users/{boot['user_id']}", headers=auth(boot["access_token"])).status_code == 422

    cleared = client.post(f"/api/users/{user_id}/clear-cache", headers=auth(boot["access_token"]))
    assert cleared.status_code == 200 and cleared.json()["released_bytes"] == 0
    assert client.delete(f"/api/users/{user_id}", headers=auth(boot["access_token"])).status_code == 200
    assert client.post("/api/auth/login", json={"username": "member", "password": "password123"}).status_code == 401


def test_clear_cache_removes_image_record_and_file(tmp_path):
    client, boot, created, _ = make_env(tmp_path)
    provider = client.post(
        "/api/ai/providers",
        headers=auth(boot["access_token"]),
        json={
            "name": "Provider",
            "provider_type": "OPENAI",
            "base_url": "",
            "api_key": "test-key",
            "default_model": "gpt-image-2",
            "status": "DISABLED",
        },
    ).json()
    image_dir = tmp_path / boot["workspace_id"] / created["id"]
    image_dir.mkdir(parents=True)
    image_file = image_dir / "cached.png"
    image_file.write_bytes(b"cached-image")
    with client.app.state.SessionLocal() as db:
        db.add(AIImage(
            workspace_id=boot["workspace_id"],
            user_id=created["id"],
            provider_id=provider["provider_id"],
            prompt="test",
            resolution="1K",
            size="1024x1024",
            quality="medium",
            status="SUCCESS",
            mime_type="image/png",
            file_name=image_file.name,
            byte_size=image_file.stat().st_size,
        ))
        db.commit()

    response = client.post(
        f"/api/users/{created['id']}/clear-cache",
        headers=auth(boot["access_token"]),
    )

    assert response.status_code == 200
    assert response.json()["released_bytes"] == len(b"cached-image")
    assert not image_file.exists()
    with client.app.state.SessionLocal() as db:
        assert db.query(AIImage).filter(AIImage.user_id == created["id"]).count() == 0
