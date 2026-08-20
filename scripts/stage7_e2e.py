from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import tempfile
import urllib.request

from agent.account_registry import AccountRegistry
from agent.agent_service import AgentService, AgentStateStore
from agent.config import load_settings
from agent.server_client import CredentialStore, ServerClient
from agent.task_service import TaskService


def request(base_url: str, path: str, payload=None, token: str = ""):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base_url.rstrip("/") + path, data=body, headers=headers, method="POST" if payload is not None else "GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:18080")
    parser.add_argument("--profile-id", required=True)
    args = parser.parse_args()
    settings = load_settings()
    bootstrap = request(args.server, "/api/auth/bootstrap", {"workspace_name": "E2E Studio", "username": "e2e-admin", "password": "e2e-password-123"})
    user_token = bootstrap["access_token"]
    with tempfile.TemporaryDirectory() as temporary:
        client = ServerClient(args.server, CredentialStore(Path(temporary) / "credentials.json"))
        registration = client.register(agent_name="E2E Windows Agent", machine_name="E2E-PC", client_version="0.7.0", user_token=user_token)
        registry = AccountRegistry(settings.account_registry_file, settings.account_mapping_history_file)
        service = AgentService(client, TaskService(), registry, AgentStateStore(Path(temporary) / "state.db"))
        heartbeat = service.heartbeat_once()
        account_sync = service.sync_accounts_once()
        created = request(args.server, "/api/tasks", {"profile_id": args.profile_id, "task_type": "x.check_login", "params": {}, "timeout": 30}, user_token)
        execution = service.pull_and_execute_once()
        duplicate_result = client.send_result(execution[0])
        stored = request(args.server, f"/api/tasks/{created['task_id']}", token=user_token)
        activities = request(args.server, "/api/activities", token=user_token)
        statistics = request(args.server, "/api/statistics", token=user_token)
        print(json.dumps({
            "registration": registration,
            "heartbeat": heartbeat,
            "account_sync": account_sync,
            "created_task": created,
            "execution": execution,
            "duplicate_result_idempotent": duplicate_result.get("idempotent"),
            "stored_task": stored,
            "activity_count": len(activities),
            "statistics": statistics,
            "verified_at": datetime.now().astimezone().isoformat(),
        }, ensure_ascii=False, indent=2))
        return 0 if stored.get("status") == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
