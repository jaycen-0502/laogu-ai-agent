from __future__ import annotations

from datetime import datetime
import json
import socket
import sqlite3
from contextlib import contextmanager
from pathlib import Path
import threading
from typing import Any

from .models import TaskStatus
from .profile_worker import ProfileWorkerError, ProfileWorkerManager
from .runtime_config import RuntimeConfig
from .server_client import ServerClient, ServerClientError
from common.release import VERSION


class AgentStateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS processed_tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, uploaded INTEGER NOT NULL DEFAULT 0)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT payload FROM processed_tasks WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, task_id: str, payload: dict[str, Any], *, uploaded: bool = False) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO processed_tasks(task_id,payload,uploaded) VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload", (task_id, json.dumps(payload, ensure_ascii=False), int(uploaded)))

    def mark_uploaded(self, task_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE processed_tasks SET uploaded=1 WHERE task_id=?", (task_id,))

    def pending(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT payload FROM processed_tasks WHERE uploaded=0 ORDER BY rowid").fetchall()
        return [json.loads(row[0]) for row in rows]


class AgentService:
    def __init__(
        self,
        server_client: ServerClient,
        task_service,
        account_registry,
        state_store: AgentStateStore,
        *,
        agent_name: str = "Laogu Windows Agent",
        client_version: str = VERSION,
        heartbeat_interval: int = 30,
        user_token: str = "",
        command_dispatcher=None,
    ):
        self.server_client = server_client
        self.task_service = task_service
        self.account_registry = account_registry
        self.state_store = state_store
        self.agent_name = agent_name
        self.client_version = client_version
        self.heartbeat_interval = max(5, heartbeat_interval)
        self.user_token = user_token
        self.command_dispatcher = command_dispatcher
        self.server_status = "OFFLINE"
        self.agent_status = "UNREGISTERED" if not server_client.agent_id else "OFFLINE"
        self.last_heartbeat = ""
        self.last_error = ""
        self.command_channel = "HTTP_PULL"
        self.websocket_reconnects = 0
        self.last_channel_change = ""
        self.lifecycle = "STOPPED"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._command_thread: threading.Thread | None = None
        self._websocket_connected = threading.Event()

    def ensure_registered(self) -> bool:
        if self.server_client.agent_id:
            return True
        if not self.user_token:
            self.last_error = "Agent registration requires a user enrollment token"
            return False
        self.server_client.register(agent_name=self.agent_name, machine_name=socket.gethostname(), client_version=self.client_version, user_token=self.user_token)
        self.agent_status = "OFFLINE"
        return True

    def heartbeat_once(self) -> bool:
        if not self.ensure_registered():
            return False
        running = sum(1 for task in self.task_service.task_manager.list_tasks() if task.status is TaskStatus.RUNNING)
        timestamp = datetime.now().astimezone().isoformat()
        self.server_client.heartbeat({"agent_id": self.server_client.agent_id, "device_id": getattr(self.server_client, "device_id", ""), "client_version": self.client_version, "status": "ONLINE", "profile_count": len(self.account_registry.list()), "running_task_count": running, "timestamp": timestamp})
        self.server_status = "ONLINE"; self.agent_status = "ONLINE"; self.last_heartbeat = timestamp; self.last_error = ""
        return True

    def sync_accounts_once(self) -> bool:
        items = [item.to_dict() for item in self.account_registry.list()]
        self.server_client.sync_accounts(items)
        self.server_status = "ONLINE"
        return True

    def flush_results(self) -> int:
        uploaded = 0
        for payload in self.state_store.pending():
            self.server_client.send_result(payload)
            self.state_store.mark_uploaded(str(payload["task_id"]))
            uploaded += 1
        return uploaded

    def pull_and_execute_once(self) -> list[dict[str, Any]]:
        self.flush_results()
        results = []
        prepared = []
        for incoming in self.server_client.pull_tasks():
            task_id = str(incoming["task_id"])
            historical = self.state_store.get(task_id)
            if historical is not None:
                self.server_client.send_result(historical)
                self.state_store.mark_uploaded(task_id)
                results.append(historical)
                continue
            script_bundle = None
            cancel_check = None
            if str(incoming.get("task_type")) == "script.execute":
                try:
                    script_bundle = self.server_client.fetch_task_script(task_id)
                except ServerClientError as exc:
                    if exc.status_code == 409:
                        payload = self._cancelled_payload(incoming)
                        self.state_store.save(task_id, payload)
                        results.append(payload)
                        continue
                    raise
                cancel_check = lambda task_id=task_id: self.server_client.task_status(task_id) == "CANCELLED"
            task = self.task_service.prepare_server_task(
                incoming,
                script_bundle=script_bundle,
                cancel_check=cancel_check,
            )
            prepared.append((incoming, task))

        if prepared:
            completed = {
                task.task_id: task
                for task in self.task_service.run_prepared_server_tasks(
                    [task for _, task in prepared]
                )
            }
        else:
            completed = {}

        for incoming, prepared_task in prepared:
            task_id = str(incoming["task_id"])
            task = completed[prepared_task.task_id]
            payload = {
                "task_id": task_id,
                "agent_id": self.server_client.agent_id,
                "profile_id": task.profile_id,
                "status": task.status.value,
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "finished_at": task.finished_at.isoformat() if task.finished_at else None,
                "duration": task.elapsed_time,
                "result": task.result,
                "error": task.error or None,
            }
            self.state_store.save(task_id, payload)
            try:
                self.server_client.send_result(payload)
            except ServerClientError as exc:
                self.server_status = "ONLINE" if exc.status_code == 401 else "OFFLINE"
                if exc.status_code == 401:
                    self.agent_status = "REAUTH_REQUIRED"
            else:
                self.state_store.mark_uploaded(task_id)
            results.append(payload)
        return results

    def process_commands_once(self) -> list[dict[str, Any]]:
        if self.command_dispatcher is None or not hasattr(self.server_client, "pull_commands"):
            return []
        if self._websocket_connected.is_set():
            return []
        completed: list[dict[str, Any]] = []
        try:
            commands = self.server_client.pull_commands()
            self.last_error = ""
        except ServerClientError as exc:
            self.server_status = "OFFLINE"
            self.last_error = str(exc)
            return completed
        for command in commands:
            command_id = str(command.get("command_id") or "")
            if not command_id:
                continue
            try:
                self.server_client.acknowledge_command(command_id, "RUNNING")
                result = self.command_dispatcher.dispatch(command)
            except ProfileWorkerError as exc:
                error = str(exc)[:500]
                try:
                    self.server_client.complete_command(command_id, "FAILED", error=error)
                except ServerClientError as upload_error:
                    self.server_status = "OFFLINE"
                    self.last_error = str(upload_error)
                completed.append({"command_id": command_id, "status": "FAILED", "error": error})
            except ServerClientError as exc:
                self.server_status = "OFFLINE"
                self.last_error = str(exc)
            except Exception as exc:
                error = str(exc)[:500]
                try:
                    self.server_client.complete_command(command_id, "FAILED", error=error)
                except ServerClientError as upload_error:
                    self.server_status = "OFFLINE"
                    self.last_error = str(upload_error)
                completed.append({"command_id": command_id, "status": "FAILED", "error": error})
            else:
                try:
                    self.server_client.complete_command(command_id, "SUCCESS", result=result)
                except ServerClientError as exc:
                    self.server_status = "OFFLINE"
                    self.last_error = str(exc)
                completed.append({"command_id": command_id, "status": "SUCCESS", "result": result})
        return completed

    def _cancelled_payload(self, incoming: dict[str, Any]) -> dict[str, Any]:
        timestamp = datetime.now().astimezone().isoformat()
        return {
            "task_id": str(incoming["task_id"]),
            "agent_id": self.server_client.agent_id,
            "profile_id": str(incoming["profile_id"]),
            "status": "CANCELLED",
            "started_at": None,
            "finished_at": timestamp,
            "duration": 0,
            "result": None,
            "error": "Script task cancelled",
        }

    def cycle_once(self) -> bool:
        try:
            if not self.heartbeat_once():
                return False
            self.sync_accounts_once()
            self.pull_and_execute_once()
            self.process_commands_once()
            return True
        except ServerClientError as exc:
            self.server_status = "ONLINE" if exc.status_code == 401 else "OFFLINE"
            self.agent_status = "REAUTH_REQUIRED" if exc.status_code == 401 else "OFFLINE"
            self.last_error = str(exc)
            return False

    def status(self) -> dict[str, str]:
        return {"server": self.server_status, "agent": self.agent_status, "lifecycle": self.lifecycle, "execution_mode": "EMBEDDED_DESKTOP", "command_channel": self.command_channel, "websocket_reconnects": str(self.websocket_reconnects), "last_channel_change": self.last_channel_change, "last_heartbeat": self.last_heartbeat, "last_error": self.last_error}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.lifecycle = "RUNNING"
        if self.command_dispatcher is not None and hasattr(self.server_client, "run_command_socket"):
            self._command_thread = threading.Thread(target=self._command_loop, name="laogu-agent-command-channel", daemon=True)
            self._command_thread.start()
        self._thread = threading.Thread(target=self._loop, name="laogu-agent-service", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._command_thread:
            self._command_thread.join(timeout=5)
        if self._thread:
            self._thread.join(timeout=5)
        self.lifecycle = "STOPPED"

    def _dispatch_socket_command(self, command: dict[str, Any]) -> dict[str, Any]:
        return self.command_dispatcher.dispatch(command)

    def _command_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.server_client.run_command_socket(
                    self._dispatch_socket_command,
                    stop_event=self._stop,
                    on_connected=lambda: (self._websocket_connected.set(), setattr(self, "command_channel", "WEBSOCKET")),
                )
            except ServerClientError as exc:
                self._websocket_connected.clear()
                self.command_channel = "HTTP_PULL"
                self.websocket_reconnects += 1
                self.last_channel_change = datetime.now().astimezone().isoformat()
                self.last_error = str(exc)
                self._stop.wait(5)
            else:
                self._websocket_connected.clear()
                self.command_channel = "HTTP_PULL"
                self.last_channel_change = datetime.now().astimezone().isoformat()
                self._stop.wait(1)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.cycle_once()
            self._stop.wait(self.heartbeat_interval)


def build_agent_service(task_service, account_registry):
    from .config import load_settings
    from .server_client import CredentialStore

    settings = load_settings()
    if not settings.server_url:
        return None
    client = ServerClient(settings.server_url, CredentialStore(settings.agent_credentials_file))
    if settings.server_agent_id and settings.server_agent_token:
        client.replace_agent_token(settings.server_agent_id, settings.server_agent_token)
    return AgentService(
        client,
        task_service,
        account_registry,
        AgentStateStore(settings.agent_state_file),
        heartbeat_interval=settings.agent_heartbeat_seconds,
        user_token=settings.server_enrollment_token,
        client_version=VERSION,
        command_dispatcher=ProfileWorkerManager(
            task_service.task_manager.browser_manager,
            task_service=task_service,
            runtime_config=RuntimeConfig(settings.agent_state_file.with_name("runtime_config.json")),
        ),
    )
