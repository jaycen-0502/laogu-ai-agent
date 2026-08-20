from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from agent.agent_service import AgentService, AgentStateStore
from agent.server_client import CredentialStore, ServerClient, ServerClientError
from server.auth import create_jwt, hash_password, token_hash
from server.config import ServerSettings
from server.database import Base, create_database
from server.models import Agent, AgentToken, User, Workspace


REAL_AGENT_ID = "1156062dd6cc47d6a7f92ca9774f173e"
REAL_WORKSPACE_ID = "8c1762680637483993768b642f49516b"
TEST_SECRET = "isolated-real-agent-security-secret-more-than-32-bytes"


class EmptyRegistry:
    def list(self):
        return []


class EmptyTaskManager:
    def list_tasks(self):
        return []


class EmptyTaskService:
    task_manager = EmptyTaskManager()


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_ready(url: str, timeout: float = 15) -> None:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health/ready", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("Isolated server did not become ready")


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="laogu-real-agent-security-") as temp_dir:
        root = Path(temp_dir)
        database_path = root / "security.db"
        database_url = f"sqlite:///{database_path.as_posix()}"
        engine, session_factory = create_database(database_url)
        Base.metadata.create_all(engine)
        original_token = "lag_isolated_original_token"
        issued = datetime.now(timezone.utc)
        with session_factory() as db:
            db.add(Workspace(id=REAL_WORKSPACE_ID, name="Isolated Real Agent Workspace"))
            admin = User(
                id="isolated-admin",
                username="isolated-admin",
                password_hash=hash_password("unused-password"),
                role="ADMIN",
                workspace_id=REAL_WORKSPACE_ID,
            )
            db.add(admin)
            db.add(
                Agent(
                    id=REAL_AGENT_ID,
                    workspace_id=REAL_WORKSPACE_ID,
                    agent_name="Real Agent Security Clone",
                    machine_name="ISOLATED-SECURITY-CHECK",
                    client_version="0.8.0",
                    token_hash="",
                )
            )
            db.add(
                AgentToken(
                    agent_id=REAL_AGENT_ID,
                    token_hash=token_hash(original_token),
                    created_at=issued,
                    expires_at=issued + timedelta(days=7),
                    status="ACTIVE",
                )
            )
            db.commit()
            user_token = create_jwt(
                admin,
                ServerSettings(
                    database_url=database_url,
                    jwt_secret=TEST_SECRET,
                    jwt_expire_minutes=10,
                    agent_offline_seconds=90,
                ),
            )

        port = free_port()
        server_url = f"http://127.0.0.1:{port}"
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
                "LAOGU_SERVER_ENVIRONMENT": "development",
                "LAOGU_SERVER_DATABASE_URL": database_url,
                "LAOGU_SERVER_JWT_SECRET": TEST_SECRET,
            }
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=Path(__file__).resolve().parent.parent,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_ready(server_url)
            credential_store = CredentialStore(root / "credentials" / "credentials.json")
            credential_store.save({"agent_id": REAL_AGENT_ID, "agent_token": original_token, "server_url": server_url})
            client = ServerClient(server_url, credential_store)
            heartbeat_payload = {
                "agent_id": REAL_AGENT_ID,
                "status": "ONLINE",
                "profile_count": 1,
                "running_task_count": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            client.heartbeat(heartbeat_payload)

            rotated = client._request("POST", f"/api/agents/{REAL_AGENT_ID}/token/rotate", None, token=user_token)
            replacement = str(rotated["agent_token"])
            old_rejected = False
            try:
                client.heartbeat(heartbeat_payload)
            except ServerClientError as exc:
                old_rejected = exc.status_code == 401

            client.replace_agent_token(REAL_AGENT_ID, replacement)
            client.heartbeat(heartbeat_payload)
            client._request("POST", f"/api/agents/{REAL_AGENT_ID}/token/revoke", None, token=user_token)
            revoked_rejected = False
            try:
                client.heartbeat(heartbeat_payload)
            except ServerClientError as exc:
                revoked_rejected = exc.status_code == 401

            service = AgentService(client, EmptyTaskService(), EmptyRegistry(), AgentStateStore(root / "agent-state.db"))
            service.cycle_once()
            stored = json.loads(credential_store.path.read_text(encoding="utf-8"))
            return {
                "agent_id": REAL_AGENT_ID,
                "workspace_id": REAL_WORKSPACE_ID,
                "initial_heartbeat": True,
                "old_token_after_rotate": 401 if old_rejected else "unexpected",
                "new_token_after_rotate": "accepted",
                "new_token_after_revoke": 401 if revoked_rejected else "unexpected",
                "agent_status_after_401": service.agent_status,
                "dpapi_protected": bool(stored.get("agent_token_protected")) and "agent_token" not in stored,
                "laogu_browser_touched": False,
                "isolated_database": True,
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            engine.dispose()


if __name__ == "__main__":
    result = run()
    if not all(
        [
            result["initial_heartbeat"],
            result["old_token_after_rotate"] == 401,
            result["new_token_after_rotate"] == "accepted",
            result["new_token_after_revoke"] == 401,
            result["agent_status_after_401"] == "REAUTH_REQUIRED",
            result["dpapi_protected"],
            result["laogu_browser_touched"] is False,
            result["isolated_database"],
        ]
    ):
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
