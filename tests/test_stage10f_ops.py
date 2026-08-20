from __future__ import annotations

from pathlib import Path
import tarfile

from fastapi.testclient import TestClient

from server.config import ServerSettings
from server.main import create_app
from scripts.verify_backup import verify_backup


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_ops_metrics_is_admin_only_and_safe():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="stage10f-ops-secret-with-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
    )
    client = TestClient(create_app(settings.database_url, settings))
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Ops Workspace", "username": "admin", "password": "password123"},
    ).json()
    response = client.get("/api/admin/ops/metrics", headers=auth(bootstrap["access_token"]))
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"]["version"] == "0.18.0"
    assert payload["channels"] == {"websocket": True, "http_pull_fallback": True}
    assert payload["database"]["reachable"] is True

    member = client.post(
        "/api/users",
        headers=auth(bootstrap["access_token"]),
        json={"username": "member", "password": "password123", "role": "MEMBER", "workspace_id": bootstrap["workspace_id"]},
    )
    member_token = client.post("/api/auth/login", json={"username": "member", "password": "password123"}).json()["access_token"]
    assert client.get("/api/admin/ops/metrics", headers=auth(member_token)).status_code == 403


def test_verify_backup_rejects_unsafe_archive(tmp_path: Path):
    backup = tmp_path / "stage10g-final-2026-08-19-155144"
    backup.mkdir()
    for name in ("laogu-after-stage10g.dump", "server-after-stage10g.env", "nginx-after-stage10g"):
        (backup / name).write_text("ok", encoding="utf-8")
    with tarfile.open(backup / "source-after-stage10g.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 0
        archive.addfile(info)
    assert any("unsafe archive member" in item for item in verify_backup(backup))


def test_verify_backup_supports_any_final_stage(tmp_path: Path):
    backup = tmp_path / "stage10h-final-2026-08-19-160000"
    backup.mkdir()
    for name in ("laogu-after-stage10h.dump", "server-after-stage10h.env", "nginx-after-stage10h"):
        (backup / name).write_text("ok", encoding="utf-8")
    with tarfile.open(backup / "source-after-stage10h.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("opt/laogu-ai-agent/server/main.py")
        info.size = 0
        archive.addfile(info)
    assert verify_backup(backup) == []
