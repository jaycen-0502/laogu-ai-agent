from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from agent.models import Task, TaskStatus
from agent.script_runner import ScriptRunner, ScriptRunnerError
from agent.task_manager import TaskManager
from common.script_validation import ScriptValidationError, source_sha256, validate_script_source
from server.config import ServerSettings
from server.main import create_app


SOURCE_V1 = """module.exports.run = async ({ useBrowser, log, params }) => {
  log("script started");
  const url = "https://example.com";
  const timeoutMs = Number(params.timeoutMs) || 30000;
  const runtime = await useBrowser();
  const page = runtime.page;
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
  const title = await page.title();
  const finalUrl = page.url();
  log("script finished");
  return { success: true, title, url: finalUrl, params };
};
"""
SOURCE_V2 = SOURCE_V1.replace("script started", "script version 2")
SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string", "maxLength": 100},
        "max_items": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "required": ["keyword"],
    "additionalProperties": False,
}


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_env():
    settings = ServerSettings(database_url="sqlite://", jwt_secret="script-center-test-secret-more-than-32-bytes", jwt_expire_minutes=60, agent_offline_seconds=90)
    client = TestClient(create_app(settings.database_url, settings))
    boot = client.post("/api/auth/bootstrap", json={"workspace_name": "Studio A", "username": "admin", "password": "password123"}).json()
    admin = boot["access_token"]
    client.post("/api/users", headers=auth(admin), json={"username": "owner-a", "password": "password123", "role": "OWNER", "workspace_id": boot["workspace_id"]})
    client.post("/api/users", headers=auth(admin), json={"username": "member-a", "password": "password123", "role": "MEMBER", "workspace_id": boot["workspace_id"]})
    owner = client.post("/api/auth/login", json={"username": "owner-a", "password": "password123"}).json()["access_token"]
    member = client.post("/api/auth/login", json={"username": "member-a", "password": "password123"}).json()["access_token"]
    workspace_b = client.post("/api/workspaces", headers=auth(admin), json={"name": "Studio B"}).json()["id"]
    client.post("/api/users", headers=auth(admin), json={"username": "owner-b", "password": "password123", "role": "OWNER", "workspace_id": workspace_b})
    owner_b = client.post("/api/auth/login", json={"username": "owner-b", "password": "password123"}).json()["access_token"]
    agent = client.post("/api/agents/register", headers=auth(owner), json={"agent_name": "Agent A", "machine_name": "SCRIPT-PC-A", "client_version": "0.8.0"}).json()
    agent_b = client.post("/api/agents/register", headers=auth(owner_b), json={"agent_name": "Agent B", "machine_name": "SCRIPT-PC-B", "client_version": "0.8.0"}).json()
    checked = datetime.now(timezone.utc).isoformat()
    items = [
        {"profile_id": "11", "instance_id": "i11", "browser_status": "RUNNING", "last_checked": checked},
        {"profile_id": "22", "instance_id": "i22", "browser_status": "RUNNING", "last_checked": checked},
    ]
    client.post("/api/accounts/sync", headers=auth(agent["agent_token"]), json={"agent_id": agent["agent_id"], "items": items})
    client.post("/api/accounts/sync", headers=auth(agent_b["agent_token"]), json={"agent_id": agent_b["agent_id"], "items": [{"profile_id": "99", "instance_id": "i99", "browser_status": "RUNNING", "last_checked": checked}]})
    return {"client": client, "admin": admin, "owner": owner, "member": member, "owner_b": owner_b, "workspace_id": boot["workspace_id"], "workspace_b": workspace_b, "agent": agent, "agent_b": agent_b}


def create_script(env, token_key="owner"):
    response = env["client"].post(
        "/api/scripts",
        headers=auth(env[token_key]),
        json={"name": "Example只读测试", "description": "打开Example Domain", "source": SOURCE_V1, "params_schema": SCHEMA},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_registry_version_history_and_rollback_are_immutable():
    env = make_env(); client = env["client"]
    script = create_script(env)
    assert script["status"] == "DISABLED" and script["current_version"] == 1
    assert script["current_version_detail"]["sha256"] == source_sha256(SOURCE_V1)
    script_id = script["script_id"]
    v2 = client.post(f"/api/scripts/{script_id}/versions", headers=auth(env["owner"]), json={"source": SOURCE_V2, "params_schema": SCHEMA}).json()
    assert v2["version"] == 2 and v2["source"] == SOURCE_V2
    rollback = client.post(f"/api/scripts/{script_id}/versions/{script['current_version_detail']['script_version_id']}/rollback", headers=auth(env["owner"])).json()
    assert rollback["version"] == 3 and rollback["source"] == SOURCE_V1
    versions = client.get(f"/api/scripts/{script_id}/versions", headers=auth(env["owner"])).json()
    assert [item["version"] for item in versions] == [3, 2, 1]
    actions = {item["action"] for item in client.get("/api/audit?paged=true&page_size=100", headers=auth(env["owner"])).json()["items"]}
    assert {"SCRIPT_CREATED", "SCRIPT_UPDATED", "SCRIPT_VERSION_CREATED", "SCRIPT_ROLLBACK"}.issubset(actions)


def test_workspace_and_member_permissions_and_disabled_execution():
    env = make_env(); client = env["client"]; script = create_script(env); script_id = script["script_id"]
    assert client.get(f"/api/scripts/{script_id}", headers=auth(env["member"])).status_code == 200
    assert client.post(f"/api/scripts/{script_id}/versions", headers=auth(env["member"]), json={"source": SOURCE_V2, "params_schema": SCHEMA}).status_code == 403
    assert client.get(f"/api/scripts/{script_id}", headers=auth(env["owner_b"])).status_code == 404
    assert client.patch(f"/api/scripts/{script_id}", headers=auth(env["owner_b"]), json={"status": "ENABLED"}).status_code == 404
    assert client.post(f"/api/scripts/{script_id}/execute", headers=auth(env["member"]), json={"profile_ids": ["11"], "params": {"keyword": "test"}}).status_code == 409


def test_execute_multi_profile_params_agent_fetch_idempotency_and_stop():
    env = make_env(); client = env["client"]; script = create_script(env); script_id = script["script_id"]
    client.patch(f"/api/scripts/{script_id}", headers=auth(env["owner"]), json={"status": "ENABLED"})
    invalid = client.post(f"/api/scripts/{script_id}/execute", headers=auth(env["member"]), json={"profile_ids": ["11"], "params": {"keyword": "test", "extra": True}})
    assert invalid.status_code == 422
    executed = client.post(f"/api/scripts/{script_id}/execute", headers=auth(env["member"]), json={"profile_ids": ["11", "22"], "params": {"keyword": "test", "max_items": 2}, "timeout": 20})
    assert executed.status_code == 200 and executed.json()["count"] == 2
    first, second = executed.json()["items"]
    assert first["task_id"] != second["task_id"] and {first["profile_id"], second["profile_id"]} == {"11", "22"}
    bundle = client.get(f"/api/agent/tasks/{first['task_id']}/script", headers=auth(env["agent"]["agent_token"]))
    assert bundle.status_code == 200 and source_sha256(bundle.json()["source"]) == bundle.json()["sha256"]
    result_payload = {"task_id": first["task_id"], "agent_id": env["agent"]["agent_id"], "profile_id": first["profile_id"], "status": "SUCCESS", "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(), "duration": 0.2, "result": {"success": True, "logs": ["script started", "script finished"]}}
    first_result = client.post("/api/tasks/result", headers=auth(env["agent"]["agent_token"]), json=result_payload).json()
    second_result = client.post("/api/tasks/result", headers=auth(env["agent"]["agent_token"]), json=result_payload).json()
    assert first_result["idempotent"] is False and second_result["idempotent"] is True
    detail = client.get(f"/api/tasks/{first['task_id']}", headers=auth(env["owner"])).json()
    assert detail["activity"]["logs"] == ["script started", "script finished"]
    stopped = client.post(f"/api/tasks/{second['task_id']}/cancel", headers=auth(env["owner"])).json()
    assert stopped["status"] == "CANCELLED"
    assert client.get(f"/api/agent/tasks/{second['task_id']}/script", headers=auth(env["agent"]["agent_token"])).status_code == 409
    runs = client.get("/api/script-runs?page=1&page_size=20", headers=auth(env["owner"])).json()
    assert runs["total"] == 2 and all(item["script_name"] == "Example只读测试" for item in runs["items"])
    assert client.post("/api/tasks", headers=auth(env["owner"]), json={"profile_id": "11", "task_type": "script.execute", "params": {}}).status_code == 422
    actions = {item["action"] for item in client.get("/api/audit?paged=true&page_size=100", headers=auth(env["owner"])).json()["items"]}
    assert {"SCRIPT_ENABLED", "SCRIPT_EXECUTED", "SCRIPT_STOPPED"}.issubset(actions)


def test_multi_profile_routing_is_atomic_before_task_creation():
    env = make_env(); client = env["client"]; script = create_script(env); script_id = script["script_id"]
    client.patch(f"/api/scripts/{script_id}", headers=auth(env["owner"]), json={"status": "ENABLED"})
    response = client.post(f"/api/scripts/{script_id}/execute", headers=auth(env["owner"]), json={"profile_ids": ["11", "missing"], "params": {"keyword": "test"}})
    assert response.status_code == 404
    assert client.get("/api/script-runs", headers=auth(env["owner"])).json()["total"] == 0


def test_all_required_script_audit_actions_are_recorded():
    env = make_env(); client = env["client"]; script = create_script(env); script_id = script["script_id"]
    version = client.post(f"/api/scripts/{script_id}/versions", headers=auth(env["owner"]), json={"source": SOURCE_V2, "params_schema": SCHEMA}).json()
    client.post(f"/api/scripts/{script_id}/versions/{version['script_version_id']}/rollback", headers=auth(env["owner"]))
    client.patch(f"/api/scripts/{script_id}", headers=auth(env["owner"]), json={"status": "ENABLED"})
    tasks = client.post(f"/api/scripts/{script_id}/execute", headers=auth(env["owner"]), json={"profile_ids": ["11", "22"], "params": {"keyword": "audit"}}).json()["items"]
    failed = tasks[0]
    client.post("/api/tasks/result", headers=auth(env["agent"]["agent_token"]), json={"task_id": failed["task_id"], "agent_id": env["agent"]["agent_id"], "profile_id": failed["profile_id"], "status": "FAILED", "finished_at": datetime.now(timezone.utc).isoformat(), "error": "deliberate test failure"})
    client.post(f"/api/tasks/{tasks[1]['task_id']}/cancel", headers=auth(env["owner"]))
    client.patch(f"/api/scripts/{script_id}", headers=auth(env["owner"]), json={"status": "DISABLED"})
    actions = {item["action"] for item in client.get("/api/audit?paged=true&page_size=100", headers=auth(env["owner"])).json()["items"]}
    assert {
        "SCRIPT_CREATED", "SCRIPT_UPDATED", "SCRIPT_ENABLED", "SCRIPT_DISABLED",
        "SCRIPT_VERSION_CREATED", "SCRIPT_ROLLBACK", "SCRIPT_EXECUTED",
        "SCRIPT_STOPPED", "SCRIPT_FAILED",
    }.issubset(actions)


def test_agent_cannot_fetch_another_workspace_script():
    env = make_env(); client = env["client"]; script = create_script(env); script_id = script["script_id"]
    client.patch(f"/api/scripts/{script_id}", headers=auth(env["owner"]), json={"status": "ENABLED"})
    task = client.post(f"/api/scripts/{script_id}/execute", headers=auth(env["owner"]), json={"profile_ids": ["11"], "params": {"keyword": "test"}}).json()["items"][0]
    assert client.get(f"/api/agent/tasks/{task['task_id']}/script", headers=auth(env["agent_b"]["agent_token"])).status_code == 404


class FakeManagedHook:
    def __init__(self):
        self.paths = []

    def run_managed_script(self, **kwargs):
        path = Path(kwargs["script_path"])
        assert path.exists()
        self.paths.append(path)
        return {"ok": True, "status": "success", "result": {"success": True, "title": "Example Domain", "url": "https://example.com/"}, "logs": ["script started", "script finished"]}


def local_script_task(source=SOURCE_V1, digest=None):
    task = Task(task_id="script-task-1", profile_id="11", profile_name="11", url="", timeout_seconds=30, task_type="script.execute", params={"script_id": "s1", "script_version_id": "v1", "params": {"keyword": "test"}})
    task.metadata["script_bundle"] = {"script_id": "s1", "script_version_id": "v1", "language": "javascript", "source": source, "sha256": digest or source_sha256(source), "params_schema": SCHEMA}
    return task


def test_script_runner_sha256_temp_cleanup_logs_and_task_isolation(tmp_path):
    hook = FakeManagedHook(); runner = ScriptRunner(hook, tmp_path)
    result = runner.execute(local_script_task())
    assert result["result"]["title"] == "Example Domain" and result["logs"] == ["script started", "script finished"]
    assert hook.paths and not hook.paths[0].exists()
    with pytest.raises(ScriptRunnerError, match="SHA256"):
        runner.execute(local_script_task(digest="0" * 64))

    class Executor:
        def execute(self, task):
            if task.profile_id == "11":
                raise ScriptRunnerError("deliberate script failure")
            return {"success": True}

    manager = TaskManager(object(), logging.getLogger("script-isolation"), max_workers=2, task_executor=Executor())
    failed = manager.create_task(profile_id="11", profile_name="11", url="", timeout_seconds=30, task_type="script.execute", params={"script_id": "s1", "script_version_id": "v1", "params": {}})
    succeeded = manager.create_task(profile_id="22", profile_name="22", url="", timeout_seconds=30, task_type="script.execute", params={"script_id": "s1", "script_version_id": "v1", "params": {}})
    statuses = {task.profile_id: task.status for task in manager.run_concurrent([failed, succeeded])}
    assert statuses == {"11": TaskStatus.FAILED, "22": TaskStatus.SUCCESS}


@pytest.mark.parametrize(
    "dangerous",
    [
        "require('child_process')",
        "module.require('fs')",
        "module['require']('fs')",
        "globalThis['process']",
        "([]).filter.constructor('return process')()",
        "eval('1 + 1')",
    ],
)
def test_script_source_rejects_node_and_dynamic_execution(dangerous):
    source = f"module.exports.run = async ({{ useBrowser }}) => {{ {dangerous}; }};"
    with pytest.raises(ScriptValidationError):
        validate_script_source(source)
