from datetime import datetime
import os
from pathlib import Path
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from agent.account_registry import AccountRecord
from agent.models import AccountStatus, BrowserStatus, LoginStatus
from desktop.controller import DesktopController, account_to_row
from desktop.main_window import AgentReauthDialog, MainWindow, TaskConfigDialog
from desktop.workers import FunctionWorker
from agent.runtime_config import RuntimeConfig


class FakeApi:
    def health(self):
        return {"ok": True}


class FakeBrowserManager:
    def __init__(self):
        self.started = []
        self.stopped = []

    def get_profiles(self):
        return [{"profileId": "p-11", "profileName": "11", "running": True}]

    def start_profile(self, profile_id):
        self.started.append(profile_id)
        return {"ok": True}

    def stop_profile(self, profile_id):
        self.stopped.append(profile_id)
        return {"ok": True}


class FakeDiscovery:
    def __init__(self, discoveries=None):
        self.discoveries = discoveries or []
        self.requested = None

    def scan(self, profile_ids=None):
        self.requested = profile_ids
        return self.discoveries


class FakeRegistry:
    def __init__(self, records=None):
        self.records = records or []
        self.updated = None

    def list(self):
        return list(self.records)

    def update_many(self, discoveries):
        self.updated = discoveries
        return self.records


class FakeStatistics:
    def summary(self, period):
        return {"period": period, "total_tasks": 4, "success_tasks": 3, "failed_tasks": 1, "timeout_tasks": 0, "by_account": {}}

    def recent_activities(self, limit):
        return []


class FakeTaskService:
    def __init__(self):
        self.statistics = FakeStatistics()
        self.calls = []

    def run(self, profile_id, task_type, params=None):
        self.calls.append((profile_id, task_type, params))
        return FakeTask(profile_id, task_type, params)


class FakeTask:
    def __init__(self, profile_id, task_type, params):
        self.profile_id = profile_id
        self.task_type = task_type
        self.params = params or {}

    def to_dict(self):
        return {
            "profile_id": self.profile_id,
            "task_type": self.task_type,
            "params": self.params,
            "status": "SUCCESS",
        }


class FakeAgentService:
    def start(self): pass
    def stop(self): pass
    def status(self):
        return {"server": "ONLINE", "agent": "ONLINE", "last_heartbeat": "2026-08-17T10:00:00+08:00", "last_error": ""}


class ReauthAgentService:
    def start(self): pass
    def stop(self): pass
    def status(self):
        return {"server": "ONLINE", "agent": "REAUTH_REQUIRED", "last_heartbeat": "", "last_error": "HTTP 401"}


def make_record(profile_id="p-11"):
    now = datetime.now().astimezone()
    return AccountRecord(
        profile_id=profile_id,
        instance_id=profile_id,
        profile_name="11",
        x_username="@example",
        x_account_id="123456789",
        login_status=LoginStatus.LOGGED_IN,
        browser_status=BrowserStatus.RUNNING,
        account_status=AccountStatus.VALID,
        last_checked=now,
        mapping_updated_at=now,
    )


def make_controller(records=None, discoveries=None, agent_service=None):
    return DesktopController(
        api=FakeApi(),
        browser_manager=FakeBrowserManager(),
        discovery=FakeDiscovery(discoveries),
        registry=FakeRegistry(records),
        task_service=FakeTaskService(),
        agent_service=agent_service,
    )


def qapp():
    return QApplication.instance() or QApplication([])


def test_account_record_is_converted_for_table():
    row = account_to_row(make_record())
    assert row.profile_id == "p-11"
    assert row.browser_status == "RUNNING"
    assert row.x_username == "@example"
    assert row.last_checked


def test_controller_handles_empty_registry():
    controller = make_controller(records=[])
    assert controller.list_accounts() == []


def test_scan_updates_registry_and_filters_profiles():
    marker = object()
    controller = make_controller(records=[make_record()], discoveries=[marker])
    rows = controller.scan_accounts(["p-11"])
    assert controller.discovery.requested == ["p-11"]
    assert controller.registry.updated == [marker]
    assert rows[0].profile_id == "p-11"


def test_controller_runs_only_whitelisted_read_only_task():
    controller = make_controller(records=[make_record()])
    result = controller.run_read_only_task("p-11", "x.search", {"query": "Python"})
    assert result["status"] == "SUCCESS"
    assert controller.task_service.calls == [("p-11", "x.search", {"query": "Python"})]


def test_controller_persists_profile_task_config():
    with tempfile.TemporaryDirectory() as directory:
        runtime = RuntimeConfig(Path(directory) / "runtime_config.json")
        controller = DesktopController(
            api=FakeApi(),
            browser_manager=FakeBrowserManager(),
            discovery=FakeDiscovery(),
            registry=FakeRegistry(),
            task_service=FakeTaskService(),
            agent_service=None,
            runtime_config=runtime,
        )
        saved = controller.set_profile_task_config("p-11", {"keyword": "Python", "daily_task_limit": 50})
        assert saved["active"]["keyword"] == "Python"
        assert controller.get_profile_task_config("p-11")["active"]["daily_task_limit"] == 50


def test_controller_extracts_cdp_endpoint_from_nested_start_response():
    assert DesktopController._extract_cdp_url({"data": {"debuggerPort": 9222}}) == "http://127.0.0.1:9222"
    assert DesktopController._extract_cdp_url({"result": {"cdpUrl": "http://127.0.0.1:9333"}}) == "http://127.0.0.1:9333"


def test_selected_profile_ids_are_read_from_selected_rows():
    app = qapp()
    window = MainWindow(make_controller(records=[make_record()]))
    window.set_accounts([account_to_row(make_record())])
    window.table.selectRow(0)
    assert window.selected_profile_ids() == ["p-11"]
    window.close()
    app.processEvents()


def test_worker_propagates_errors():
    messages = []

    def fail():
        raise RuntimeError("expected failure")

    worker = FunctionWorker(fail)
    worker.signals.error.connect(messages.append)
    worker.run()
    assert messages == ["RuntimeError: expected failure"]


def test_desktop_statistics_are_displayed():
    app = qapp()
    window = MainWindow(make_controller(records=[]))
    assert window.stat_labels["total_tasks"].text() == "4"
    assert window.stat_labels["success_tasks"].text() == "3"
    window.close()
    app.processEvents()


def test_desktop_read_only_task_controls_are_present():
    app = qapp()
    window = MainWindow(make_controller(records=[make_record()]))
    assert window.check_login_button.text() == "登录检查"
    assert window.read_profile_button.text() == "读取档案"
    assert window.read_timeline_button.text() == "读取时间线"
    assert window.automation_button.text() == "配置并运行自动化"
    assert "关键词" in window.search_input.placeholderText()
    window.close()
    app.processEvents()


def test_task_config_dialog_has_safe_defaults_and_returns_config():
    app = qapp()
    dialog = TaskConfigDialog()
    values = dialog.config()
    assert values["daily_task_limit"] == 50
    assert values["max_follower_threshold"] == 150
    assert values["max_engagement_threshold"] == 10_000
    assert values["sleep_on_rate_limit"] is True
    dialog.close()
    app.processEvents()


def test_agent_reauth_dialog_masks_token_and_returns_new_credentials():
    app = qapp()
    dialog = AgentReauthDialog("agent-123")
    dialog.agent_token_input.setText("lag_example_replacement_token")
    assert dialog.agent_token_input.echoMode().name == "Password"
    assert dialog.credentials() == ("agent-123", "lag_example_replacement_token")
    dialog.close()
    app.processEvents()


def test_desktop_server_agent_status_is_displayed():
    app = qapp()
    window = MainWindow(make_controller(records=[], agent_service=FakeAgentService()))
    assert window.server_state_label.text() == "服务器：在线"
    assert window.agent_state_label.text() == "运行端：在线"
    assert "2026-08-17 10:00:00" in window.heartbeat_label.text()
    assert window.reauth_button.isHidden()
    window.close()
    app.processEvents()


def test_desktop_shows_reauthentication_when_server_rejects_agent():
    app = qapp()
    window = MainWindow(make_controller(records=[], agent_service=ReauthAgentService()))
    assert not window.reauth_button.isHidden()
    assert window.server_state_label.text() == "服务器：在线"
    assert window.agent_state_label.text() == "运行端：需要重新认证"
    assert "重新认证" in window.live_status_label.text()
    window.close()
    app.processEvents()
