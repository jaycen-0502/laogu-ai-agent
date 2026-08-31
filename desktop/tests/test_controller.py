from datetime import datetime
import hashlib
from concurrent.futures import Future
import os
from pathlib import Path
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint

from agent.account_registry import AccountRecord
from agent.automation_statistics import AutomationStatisticsStore
from agent.models import AccountStatus, BrowserStatus, LoginStatus
import desktop.controller as controller_module
from desktop.controller import DesktopController, account_to_row
from desktop.main_window import AgentReauthDialog, MainWindow, TaskConfigDialog
from desktop.workers import FunctionWorker
from agent.runtime_config import RuntimeConfig
from agent.x_tasks import ProfileSnapshotStore


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
    def __init__(self): self.metric_flushes = 0
    def start(self): pass
    def stop(self): pass
    def status(self):
        return {"server": "ONLINE", "agent": "ONLINE", "last_heartbeat": "2026-08-17T10:00:00+08:00", "last_error": ""}
    def flush_automation_metrics(self):
        self.metric_flushes += 1
        return 1


class ReauthAgentService:
    def start(self): pass
    def stop(self): pass
    def status(self):
        return {"server": "ONLINE", "agent": "REAUTH_REQUIRED", "last_heartbeat": "", "last_error": "HTTP 401"}


class OfflineGraceAgentService:
    capabilities = {"local.view", "local.browser.stop", "local.browser.control", "local.readonly.run", "automation.run"}

    def start(self): pass
    def stop(self): pass
    def status(self):
        return {
            "server": "OFFLINE",
            "agent": "OFFLINE",
            "authorization_mode": "OFFLINE_GRACE",
            "authorization_expires_at": "2026-08-27T20:00:00+08:00",
            "capabilities": sorted(self.capabilities),
            "last_heartbeat": "2026-08-24T10:00:00+08:00",
            "last_error": "timeout",
        }
    def has_capability(self, capability):
        return capability in self.capabilities


class FakeEngineClient:
    def __init__(self, source: bytes, version: str = "0.21.9"):
        self.source = source
        self.manifest = {
            "engine": "x_automation_engine",
            "version": version,
            "sha256": hashlib.sha256(source).hexdigest(),
            "size": len(source),
            "read_only": True,
            "source_url": "/api/agent/engine/source",
        }

    def fetch_engine_manifest(self):
        return dict(self.manifest)

    def fetch_engine_source(self, source_url):
        assert source_url == "/api/agent/engine/source"
        return self.source


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


def make_controller(records=None, discoveries=None, agent_service=None, profile_snapshot_store=None):
    return DesktopController(
        api=FakeApi(),
        browser_manager=FakeBrowserManager(),
        discovery=FakeDiscovery(discoveries),
        registry=FakeRegistry(records),
        task_service=FakeTaskService(),
        agent_service=agent_service,
        profile_snapshot_store=profile_snapshot_store,
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


def test_controller_merges_profile_assets_and_live_runtime_state():
    with tempfile.TemporaryDirectory() as directory:
        snapshots = ProfileSnapshotStore(Path(directory) / "profile_snapshot.json")
        snapshots.update("p-11", {
            "display_name": "Example Account",
            "bio": "Profile bio",
            "followers_count": 123,
            "following_count": 45,
            "profile_data_status": "COMPLETE",
        })
        controller = make_controller(
            records=[make_record()],
            profile_snapshot_store=snapshots,
        )
        controller.refresh_profiles()
        row = controller.list_accounts()[0]
        assert row.display_name == "Example Account"
        assert row.followers_count == 123
        assert row.following_count == 45
        assert row.runtime_running is True
        assert row.runtime_debug_ready is True


def test_account_card_displays_persisted_assets_and_live_identity():
    app = qapp()
    row = account_to_row(
        make_record(),
        {
            "display_name": "Example Account",
            "followers_count": 123,
            "following_count": 45,
            "profile_data_status": "COMPLETE",
        },
        {"profileId": "p-11", "profileName": "11", "running": True, "debugReady": True},
    )
    window = MainWindow(make_controller(records=[], agent_service=FakeAgentService()))
    window._profiles = [{"profileId": "p-11", "profileName": "11", "running": True}]
    window.set_accounts([row])
    texts = [label.text() for label in window.table.cellWidget(0, 0).findChildren(type(window.summary_label))]
    assert any("粉丝 123" in text and "关注 45" in text for text in texts)
    window.table.selectRow(0)
    app.processEvents()
    assert window.selected_profile_label.text() == "11  ·  @example"
    assert window.selected_runtime_label.text() == "运行状态：运行中"
    window.close()
    app.processEvents()


def test_scan_updates_registry_and_filters_profiles():
    marker = object()
    controller = make_controller(records=[make_record()], discoveries=[marker])
    rows = controller.scan_accounts(["p-11"])
    assert controller.discovery.requested == ["p-11"]
    assert controller.registry.updated == [marker]
    assert rows[0].profile_id == "p-11"


def test_controller_runs_only_whitelisted_read_only_task():
    controller = make_controller(records=[make_record()], agent_service=FakeAgentService())
    result = controller.run_read_only_task("p-11", "x.search", {"query": "Python"})
    assert result["status"] == "SUCCESS"
    assert controller.task_service.calls == [("p-11", "x.search", {"query": "Python"})]


def test_controller_blocks_remote_task_without_authenticated_agent():
    controller = make_controller(records=[make_record()], agent_service=None)
    import pytest

    with pytest.raises(RuntimeError, match="未连接 Web 服务器"):
        controller.run_read_only_task("p-11", "x.search", {"query": "Python"})


def test_controller_allows_stop_without_server_and_local_work_during_offline_grace():
    restricted = make_controller(records=[make_record()], agent_service=None)
    assert restricted.stop_profile("p-11") == {"ok": True}

    offline = make_controller(records=[make_record()], agent_service=OfflineGraceAgentService())
    assert offline.start_profile("p-11") == {"ok": True}
    assert offline.run_read_only_task("p-11", "x.search", {"query": "Python"})["status"] == "SUCCESS"
    permissions = offline.operation_permissions()
    assert permissions["mode"] == "OFFLINE_GRACE"
    assert "engine.update" not in permissions["capabilities"]


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
        saved = controller.set_profile_task_config(
            "p-11",
            {"keyword": "Python", "daily_task_limit": 50, "ai_reply_ratio": "0.25"},
        )
        assert saved["active"]["keyword"] == "Python"
        assert controller.get_profile_task_config("p-11")["active"]["daily_task_limit"] == 50
        assert controller.get_profile_task_config("p-11")["active"]["ai_reply_ratio"] == 0.25


def test_engine_runner_passes_ai_reply_ratio_to_engine_without_changing_constructor(monkeypatch):
    captured = {}

    class FakeEngine:
        def __init__(self, cdp_url, logger=None):
            captured["cdp_url"] = cdp_url
            captured["logger"] = logger

        async def run(self, custom_config=None):
            captured["custom_config"] = custom_config
            return {"status": "SUCCESS"}

    monkeypatch.setattr(
        controller_module,
        "_resolve_automation_engine_class",
        lambda cache_dir, engine_id: FakeEngine,
    )
    result = controller_module._run_engine_in_thread(
        "ws://127.0.0.1:9222/devtools/browser/test",
        None,
        {"keyword": "Python", "ai_reply_ratio": 0.0},
    )

    assert result["status"] == "SUCCESS"
    assert captured["custom_config"]["ai_reply_ratio"] == 0.0


def test_controller_captures_engine_future_into_external_statistics_layer():
    with tempfile.TemporaryDirectory() as directory:
        statistics = AutomationStatisticsStore(Path(directory) / "state.db")
        agent_service = FakeAgentService()
        controller = DesktopController(
            api=FakeApi(),
            browser_manager=FakeBrowserManager(),
            discovery=FakeDiscovery(),
            registry=FakeRegistry([make_record()]),
            task_service=FakeTaskService(),
            agent_service=agent_service,
            automation_statistics=statistics,
        )
        future = Future()
        future.set_result({"status": "SUCCESS", "likes": 4, "follows": 2, "views": 9})
        controller._automation_result_finished(
            future,
            run_id="run-controller",
            profile_id="p-11",
            x_account_id="123456789",
            account_tag="@example",
            started_at=datetime.now().astimezone().isoformat(),
        )
        summary = controller.task_statistics("today")
        assert summary["by_account"]["p-11"]["likes"] == 4
        assert summary["by_account"]["123456789"]["scanned_posts"] == 9
        assert agent_service.metric_flushes == 1
        assert controller.task_service.calls[-1] == (
            "p-11",
            "x.read_profile",
            {"readOnly": True, "source": "automation_finished"},
        )


def test_controller_extracts_cdp_endpoint_from_nested_start_response():
    assert DesktopController._extract_cdp_url({"data": {"debuggerPort": 9222}}) == "http://127.0.0.1:9222"
    assert DesktopController._extract_cdp_url({"result": {"cdpUrl": "http://127.0.0.1:9333"}}) == "http://127.0.0.1:9333"


def test_controller_detects_and_activates_server_engine_update_without_touching_bundle():
    source = b"class XAutomationEngine:\n    async def run(self, custom_config=None):\n        return {'version': 9}\n"
    with tempfile.TemporaryDirectory() as directory:
        agent_service = FakeAgentService()
        agent_service.server_client = FakeEngineClient(source)
        controller = make_controller(records=[], agent_service=agent_service)
        object.__setattr__(controller.settings, "engine_cache_dir", Path(directory))

        pending = controller.automation_engine_update_status()
        assert pending["update_available"] is True
        installed = controller.download_automation_engine_update()
        assert installed["downloaded"] is True
        current = controller.automation_engine_update_status()
        assert current["update_available"] is False
        assert current["remote_version"] == "0.21.9"


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


def test_desktop_minimize_surface_tracks_logs_and_restores_main_window():
    app = qapp()
    window = MainWindow(make_controller(records=[]))
    window.show()
    app.processEvents()

    window._log("浮窗日志测试")
    window._show_mini_window()
    app.processEvents()

    assert window.isHidden()
    assert window._mini_window.isVisible()
    assert "浮窗日志测试" in window._mini_window.log_output.toPlainText()

    window._restore_from_mini_window()
    app.processEvents()
    assert window.isVisible()
    assert window._mini_window.isHidden()

    window.close()
    app.processEvents()


def test_desktop_minimize_surface_reads_new_agent_log_lines(tmp_path):
    app = qapp()
    window = MainWindow(make_controller(records=[]))
    window._log_tail_path = str(tmp_path / "agent.log")
    (tmp_path / "agent.log").write_text("[12:00:00] initial agent line\n", encoding="utf-8")
    window._show_mini_window()
    app.processEvents()
    assert "initial agent line" in window._mini_window.log_output.toPlainText()

    with (tmp_path / "agent.log").open("a", encoding="utf-8") as handle:
        handle.write("[12:00:01] latest agent line\n")
    window._poll_log_file()
    assert "latest agent line" in window._mini_window.log_output.toPlainText()

    window.close()
    app.processEvents()


def test_desktop_main_log_reads_new_agent_lines_while_mini_window_is_hidden(tmp_path):
    app = qapp()
    window = MainWindow(make_controller(records=[]))
    window._log_tail_path = str(tmp_path / "agent.log")
    window._log_tail_offset = 0
    (tmp_path / "agent.log").write_text("[12:10:00] state=WAITING_SCHEDULE\n", encoding="utf-8")
    window._poll_log_file()
    app.processEvents()
    window._flush_log_buffer()

    assert window._mini_window.isHidden()
    assert "state=WAITING_SCHEDULE" in window.log_output.toPlainText()

    window.close()
    app.processEvents()


def test_desktop_mini_window_is_resizable_and_can_hide_without_stopping_agent():
    app = qapp()
    agent_service = FakeAgentService()
    window = MainWindow(make_controller(records=[], agent_service=agent_service))
    mini = window._mini_window

    assert mini.minimumWidth() == 360
    assert mini.minimumHeight() == 260
    assert mini.maximumWidth() > mini.minimumWidth()
    assert mini._edges_at(QPoint(0, 0)) == {"left", "top"}
    assert mini._edges_at(QPoint(mini.width() - 1, mini.height() - 1)) == {"right", "bottom"}

    window._show_mini_window()
    app.processEvents()
    mini.hide_requested.emit()
    app.processEvents()

    assert mini.isHidden()
    assert agent_service.metric_flushes == 0

    window.close()
    app.processEvents()


def test_dashboard_refresh_does_not_overwrite_fresh_automation_counts_with_stale_ui_values():
    app = qapp()
    window = MainWindow(make_controller(records=[]))
    window._statistics = {
        "by_account": {
            "p-11": {"likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0},
        }
    }
    window._apply_dashboard_data({
        "summary": {
            "by_account": {
                "p-11": {"likes": 36, "follows": 17, "comments": 0, "scanned_posts": 376},
            },
            "likes": 36,
            "follows": 17,
        },
        "activities": [],
        "accounts": [],
    })
    assert window._statistics["by_account"]["p-11"]["likes"] == 36
    assert window._statistics["by_account"]["p-11"]["follows"] == 17
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
    assert values["ai_reply_ratio"] == 0.15
    assert values["sleep_on_rate_limit"] is True
    assert values["schedule_mode"] == "smart"
    assert values["schedule_type"] == "once"
    assert values["schedule_timezone"] == "Asia/Shanghai"

    dialog.ai_reply_ratio_input.setCurrentIndex(0)
    assert dialog.config()["ai_reply_ratio"] == 0.0

    dialog.apply_initial({"active": {"ai_reply_ratio": 0.25}})
    assert dialog.config()["ai_reply_ratio"] == 0.25
    dialog.apply_initial({"active": {"schedule_mode": "immediate"}})
    assert dialog.config()["schedule_mode"] == "immediate"
    assert not dialog.schedule_type_input.isEnabled()
    dialog.apply_initial({"active": {"schedule_mode": "scheduled", "schedule_type": "daily", "scheduled_time": "18:30"}})
    assert dialog.config()["schedule_mode"] == "scheduled"
    assert dialog.config()["scheduled_time"] == "18:30"
    assert dialog.scheduled_time_input.isEnabled()
    dialog.close()
    app.processEvents()


def test_agent_reauth_dialog_masks_token_and_returns_new_credentials():
    app = qapp()
    dialog = AgentReauthDialog("agent-123")
    assert dialog.agent_id_input.isReadOnly()
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
    window._active_jobs = 0
    window._update_busy_state()
    assert window.stop_all_button.isEnabled()
    for button in (
        window.run_all_button,
        window.automation_button,
        window.check_login_button,
        window.read_profile_button,
        window.read_timeline_button,
        window.search_button,
    ):
        assert not button.isEnabled()
    window.close()
    app.processEvents()


def test_desktop_displays_offline_grace_and_enables_only_cached_capabilities():
    app = qapp()
    window = MainWindow(make_controller(records=[], agent_service=OfflineGraceAgentService()))
    assert "本地授权有效至 2026-08-27 20:00" in window.live_status_label.text()
    window._active_jobs = 0
    window._update_busy_state()
    assert window.run_all_button.isEnabled()
    assert window.stop_all_button.isEnabled()
    assert window.check_login_button.isEnabled()
    assert window.automation_button.isEnabled()
    assert not window.engine_update_button.isEnabled()
    window.close()
    app.processEvents()
