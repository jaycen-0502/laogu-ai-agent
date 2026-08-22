from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile

from agent.agent_service import AgentService, AgentStateStore
from agent.automation_statistics import AutomationStatisticsStore
from agent.server_client import ServerClientError


class FakeClient:
    agent_id = "agent-1"

    def __init__(self):
        self.online = True
        self.tasks = []
        self.results = []

    def heartbeat(self, payload):
        if not self.online: raise ServerClientError("offline")
        return {"ok": True}

    def sync_accounts(self, items):
        if not self.online: raise ServerClientError("offline")
        return {"synced": len(items)}

    def pull_tasks(self, limit=10):
        if not self.online: raise ServerClientError("offline")
        return list(self.tasks)

    def send_result(self, payload):
        if not self.online: raise ServerClientError("offline")
        self.results.append(payload)
        return {"ok": True}

    def send_automation_metric(self, payload):
        if not self.online: raise ServerClientError("offline")
        self.results.append(payload)
        return {"ok": True}


class FakeCommandClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.commands = []
        self.command_acks = []
        self.command_results = []

    def pull_commands(self, limit=10):
        return list(self.commands[:limit])

    def acknowledge_command(self, command_id, status="ACKNOWLEDGED"):
        self.command_acks.append((command_id, status))
        return {"ok": True}

    def complete_command(self, command_id, status, result=None, error=None):
        self.command_results.append((command_id, status, result, error))
        return {"ok": True}


class FakeRegistry:
    def list(self): return []


class FakeTaskManager:
    def list_tasks(self): return []


class FakeTask:
    task_id = "task-1"; profile_id = "p1"; error = ""; result = {"ok": True}; elapsed_time = 0.1
    started_at = datetime.now().astimezone(); finished_at = datetime.now().astimezone()
    class Status: value = "SUCCESS"
    status = Status()


class FakeTaskService:
    task_manager = FakeTaskManager()
    def __init__(self): self.executions = 0; self.batch_sizes = []
    def prepare_server_task(self, payload, **_kwargs):
        task = FakeTask()
        task.task_id = payload["task_id"]
        task.profile_id = payload["profile_id"]
        return task
    def run_prepared_server_tasks(self, tasks):
        self.batch_sizes.append(len(tasks))
        self.executions += len(tasks)
        return tasks


def test_offline_result_is_cached_recovered_and_duplicate_not_reexecuted():
    with tempfile.TemporaryDirectory() as temp_dir:
        client = FakeClient(); tasks = FakeTaskService()
        client.tasks = [{"task_id": "task-1", "profile_id": "p1", "task_type": "x.check_login", "params": {}, "timeout": 30}]
        service = AgentService(client, tasks, FakeRegistry(), AgentStateStore(Path(temp_dir) / "state.db"))
        original_send = client.send_result
        calls = {"count": 0}
        def fail_once(payload):
            calls["count"] += 1
            if calls["count"] == 1: raise ServerClientError("offline")
            return original_send(payload)
        client.send_result = fail_once
        service.pull_and_execute_once()
        assert tasks.executions == 1
        assert len(service.state_store.pending()) == 1
        client.tasks = []
        service.flush_results()
        assert len(service.state_store.pending()) == 0
        client.tasks = [{"task_id": "task-1", "profile_id": "p1", "task_type": "x.check_login", "params": {}, "timeout": 30}]
        service.pull_and_execute_once()
        assert tasks.executions == 1


def test_server_disconnect_does_not_stop_local_components():
    with tempfile.TemporaryDirectory() as temp_dir:
        client = FakeClient(); client.online = False
        tasks = FakeTaskService()
        service = AgentService(client, tasks, FakeRegistry(), AgentStateStore(Path(temp_dir) / "state.db"))
        assert service.cycle_once() is False
        assert service.status()["server"] == "OFFLINE"
        assert tasks.executions == 0


def test_automation_metrics_remain_queued_offline_and_upload_after_reconnect():
    with tempfile.TemporaryDirectory() as temp_dir:
        client = FakeClient()
        store = AutomationStatisticsStore(Path(temp_dir) / "state.db")
        store.record_result(
            run_id="run-1", profile_id="p1", x_account_id="x1",
            started_at=datetime.now().astimezone().isoformat(),
            result={"status": "SUCCESS", "likes": 2, "views": 5},
        )
        service = AgentService(
            client, FakeTaskService(), FakeRegistry(),
            AgentStateStore(Path(temp_dir) / "state.db"),
            automation_statistics=store,
        )
        client.online = False
        assert service.flush_automation_metrics() == 0
        assert len(store.pending()) == 1
        client.online = True
        assert service.flush_automation_metrics() == 1
        assert store.pending() == []


def test_pulled_tasks_are_submitted_as_one_concurrent_batch():
    with tempfile.TemporaryDirectory() as temp_dir:
        client = FakeClient(); tasks = FakeTaskService()
        client.tasks = [
            {"task_id": "task-a", "profile_id": "p1", "task_type": "x.check_login", "params": {}, "timeout": 30},
            {"task_id": "task-b", "profile_id": "p2", "task_type": "x.check_login", "params": {}, "timeout": 30},
        ]
        service = AgentService(client, tasks, FakeRegistry(), AgentStateStore(Path(temp_dir) / "state.db"))
        results = service.pull_and_execute_once()
        assert tasks.batch_sizes == [2]
        assert {item["task_id"] for item in results} == {"task-a", "task-b"}
    assert {item["task_id"] for item in client.results} == {"task-a", "task-b"}


def test_profile_commands_are_acked_and_completed():
    with tempfile.TemporaryDirectory() as temp_dir:
        client = FakeCommandClient()
        client.commands = [{"command_id": "command-1", "command_type": "REFRESH_PROFILE", "profile_id": "p1"}]

        class Dispatcher:
            def dispatch(self, command):
                assert command["profile_id"] == "p1"
                return {"worker_state": "IDLE"}

        service = AgentService(
            client,
            FakeTaskService(),
            FakeRegistry(),
            AgentStateStore(Path(temp_dir) / "state.db"),
            command_dispatcher=Dispatcher(),
        )
        result = service.process_commands_once()
        assert result == [{"command_id": "command-1", "status": "SUCCESS", "result": {"worker_state": "IDLE"}}]
        assert client.command_acks == [("command-1", "RUNNING")]
        assert client.command_results == [("command-1", "SUCCESS", {"worker_state": "IDLE"}, None)]
