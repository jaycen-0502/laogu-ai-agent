from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.config import ServerSettings
from server.main import create_app
from server.models import Invitation, User


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def build_client() -> tuple[TestClient, dict]:
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="onboarding-test-secret-more-than-thirty-two-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
    )
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Workspace A", "username": "admin", "password": "password123"},
    ).json()
    admin_token = bootstrap["access_token"]
    owner = client.post(
        "/api/users",
        headers=auth(admin_token),
        json={"username": "owner-a", "password": "password123", "role": "OWNER", "workspace_id": bootstrap["workspace_id"]},
    )
    assert owner.status_code == 200
    owner_token = client.post("/api/auth/login", json={"username": "owner-a", "password": "password123"}).json()["access_token"]
    member = client.post(
        "/api/users",
        headers=auth(admin_token),
        json={"username": "member-a", "password": "password123", "role": "MEMBER", "workspace_id": bootstrap["workspace_id"]},
    )
    assert member.status_code == 200
    member_token = client.post("/api/auth/login", json={"username": "member-a", "password": "password123"}).json()["access_token"]
    workspace_b = client.post("/api/workspaces", headers=auth(admin_token), json={"name": "Workspace B"}).json()
    return client, {
        "admin": admin_token,
        "owner": owner_token,
        "member": member_token,
        "workspace_a": bootstrap["workspace_id"],
        "workspace_b": workspace_b["id"],
    }


def test_invitation_acceptance_is_one_time_and_workspace_scoped():
    client, context = build_client()
    created = client.post(
        "/api/invitations",
        headers=auth(context["owner"]),
        json={"role": "MEMBER", "expires_hours": 24},
    )
    assert created.status_code == 200
    invitation = created.json()
    assert invitation["workspace_id"] == context["workspace_a"]
    assert invitation["token"].startswith("lgi_")

    detail = client.get(f"/api/auth/invitations/{invitation['token']}")
    assert detail.status_code == 200
    assert detail.json()["workspace_name"] == "Workspace A"
    assert "token" not in detail.json()

    accepted = client.post(
        f"/api/auth/invitations/{invitation['token']}/accept",
        json={"username": "New-Member", "password": "new-password-123"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["user"]["username"] == "new-member"
    assert accepted.json()["user"]["workspace_id"] == context["workspace_a"]
    assert client.get("/api/dashboard", headers=auth(accepted.json()["access_token"])).status_code == 200
    assert client.post(
        f"/api/auth/invitations/{invitation['token']}/accept",
        json={"username": "another-user", "password": "new-password-123"},
    ).status_code == 410


def test_invitation_permissions_and_revocation():
    client, context = build_client()
    assert client.post(
        "/api/invitations",
        headers=auth(context["member"]),
        json={"role": "MEMBER"},
    ).status_code == 403
    assert client.post(
        "/api/invitations",
        headers=auth(context["owner"]),
        json={"role": "OWNER"},
    ).status_code == 403

    created = client.post(
        "/api/invitations",
        headers=auth(context["admin"]),
        json={"role": "OWNER", "workspace_id": context["workspace_b"], "expires_hours": 24},
    ).json()
    owner_list = client.get("/api/invitations", headers=auth(context["owner"])).json()
    assert all(item["workspace_id"] == context["workspace_a"] for item in owner_list)
    assert client.delete(f"/api/invitations/{created['invitation_id']}", headers=auth(context["owner"])).status_code == 404
    assert client.delete(f"/api/invitations/{created['invitation_id']}", headers=auth(context["admin"])).status_code == 200
    assert client.get(f"/api/auth/invitations/{created['token']}").status_code == 410


def test_expired_invitation_and_password_change():
    client, context = build_client()
    created = client.post(
        "/api/invitations",
        headers=auth(context["owner"]),
        json={"role": "MEMBER", "expires_hours": 24},
    ).json()
    with client.app.state.SessionLocal() as db:
        item = db.scalar(select(Invitation).where(Invitation.id == created["invitation_id"]))
        item.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert client.get(f"/api/auth/invitations/{created['token']}").status_code == 410

    wrong = client.post(
        "/api/auth/password",
        headers=auth(context["member"]),
        json={"current_password": "wrong-password", "new_password": "updated-password-123"},
    )
    assert wrong.status_code == 400
    changed = client.post(
        "/api/auth/password",
        headers=auth(context["member"]),
        json={"current_password": "password123", "new_password": "updated-password-123"},
    )
    assert changed.status_code == 200
    assert client.get("/api/auth/me", headers=auth(context["member"])).status_code == 401
    assert client.get("/api/auth/me", headers=auth(changed.json()["access_token"])).status_code == 200
    assert client.post("/api/auth/login", json={"username": "member-a", "password": "password123"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "member-a", "password": "updated-password-123"}).status_code == 200

    with client.app.state.SessionLocal() as db:
        assert db.scalar(select(User).where(User.username == "member-a")).status == "ACTIVE"
