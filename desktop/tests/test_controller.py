from datetime import datetime, timedelta
import hashlib
from concurrent.futures import Future
import os
from pathlib import Path
import tempfile
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from PySide6.QtCore import QPoint, QDateTime

from agent.account_registry import AccountRecord
from agent.automation_safety import AutomationSafetyStore
from agent.automation_statistics import AutomationStatisticsStore
from agent.models import AccountStatus, BrowserStatus, LoginStatus
import desktop.controller as controller_module
from desktop.controller import DesktopController, account_to_row
from desktop.main_window import AccountCardWidget, AgentReauthDialog, MainWindow, TaskConfigDialog
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
        self.deleted = []

    def get_profiles(self):
        return [{"profileId": "p-11", "profileName": "11", "running": True}]

    def start_profile(self, profile_id):
        self.started.append(profile_id)
        return {"ok": True}

    def stop_profile(self, profile_id):
        self.stopped.append(profile_id)
        return {"ok": True}

    def delete_profile(self, profile_id):
        self.deleted.append(profile_id)
        return {"ok": True, "deleted": True}


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
        self.removed = []

    def list(self):
        return list(self.records)

    def remove(self, profile_id):
        self.removed.append(profile_id)
        self.records = [r for r in self.records if r.profile_id != profile_id]
        return True

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
    def has_capability(self, capability):
        return True


class FirstActivationClient:
    """Minimal credential client used to verify first-time binding."""

    def __init__(self, agent_id=""):
        self.agent_id = agent_id
        self.workspace_id = "workspace-1"
        self.device_id = "device-1"
        self.replacements = []

    def replace_agent_token(self, agent_id, agent_token):
        self.replacements.append((agent_id, agent_token))
        self.agent_id = agent_id


class FirstActivationAgentService:
    def __init__(self, agent_id=""):
        self.server_client = FirstActivationClient(agent_id)
        self.last_error = ""

    def start(self): pass
    def stop(self): pass
    def heartbeat_once(self): return True
    def status(self):
        return {"server": "ONLINE", "agent": "ONLINE", "last_error": ""}


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


def make_record(profile_id="p-11", profile_name="11"):
    now = datetime.now().astimezone()
    return AccountRecord(
        profile_id=profile_id,
        instance_id=profile_id,
        profile_name=profile_name,
        x_username="@example",
        x_account_id="123456789",
        login_status=LoginStatus.LOGGED_IN,
        browser_status=BrowserStatus.RUNNING,
        account_status=AccountStatus.VALID,
        last_checked=now,
        mapping_updated_at=now,
    )


def make_controller(records=None, discoveries=None, agent_service=None, profile_snapshot_store=None, browser_manager=None):
    return DesktopController(
        api=FakeApi(),
        browser_manager=browser_manager or FakeBrowserManager(),
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
            {
                "keyword": "Python",
                "daily_task_limit": 50,
                "ai_reply_ratio": "0.25",
                "allow_retweet": False,
                "retweet_ratio": "0.35",
            },
        )
        assert saved["active"]["keyword"] == "Python"
        assert controller.get_profile_task_config("p-11")["active"]["daily_task_limit"] == 50
        assert controller.get_profile_task_config("p-11")["active"]["ai_reply_ratio"] == 0.25
        assert controller.get_profile_task_config("p-11")["active"]["allow_retweet"] is False
        assert controller.get_profile_task_config("p-11")["active"]["retweet_ratio"] == 0.35


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
        {"keyword": "Python", "ai_reply_ratio": 0.0, "allow_retweet": False, "retweet_ratio": 0.35},
    )

    assert result["status"] == "SUCCESS"
    assert captured["custom_config"]["ai_reply_ratio"] == 0.0
    assert captured["custom_config"]["allow_retweet"] is False
    assert captured["custom_config"]["retweet_ratio"] == 0.35


def test_resolve_automation_engine_class_prioritizes_cached_default_engine(monkeypatch):
    class CachedDefaultEngine:
        pass

    monkeypatch.setattr(
        controller_module,
        "get_cached_automation_engine_class",
        lambda cache_dir, engine_id: CachedDefaultEngine if engine_id == "default" else None,
    )
    resolved = controller_module._resolve_automation_engine_class("some_cache_dir", "default")
    assert resolved is CachedDefaultEngine

    # When no cached engine exists, it falls back to bundled XAutomationEngine
    monkeypatch.setattr(
        controller_module,
        "get_cached_automation_engine_class",
        lambda cache_dir, engine_id: None,
    )
    fallback = controller_module._resolve_automation_engine_class("some_cache_dir", "default")
    assert fallback is controller_module.XAutomationEngine


def test_profile_task_config_parses_string_booleans_strictly():
    normalized = DesktopController._normalize_profile_task_config({
        "allow_like": "false",
        "allow_follow": "0",
        "dry_run": "false",
    })
    assert normalized["allow_like"] is False
    assert normalized["allow_follow"] is False
    assert normalized["dry_run"] is False
    with pytest.raises(ValueError, match="boolean"):
        DesktopController._normalize_profile_task_config({"allow_like": "maybe"})


def test_profile_task_config_normalizes_periodic_search_refresh():
    # 1. 正常布尔值与数值
    cfg1 = DesktopController._normalize_profile_task_config({
        "periodic_search_refresh_enabled": "true",
        "search_refresh_interval_minutes": "12",
    })
    assert cfg1["periodic_search_refresh_enabled"] is True
    assert cfg1["search_refresh_interval_minutes"] == 12

    # 2. 钳制边界：<1 钳制为 1，>60 钳制为 60
    cfg2 = DesktopController._normalize_profile_task_config({
        "periodic_search_refresh_enabled": False,
        "search_refresh_interval_minutes": -5,
    })
    assert cfg2["periodic_search_refresh_enabled"] is False
    assert cfg2["search_refresh_interval_minutes"] == 1

    cfg3 = DesktopController._normalize_profile_task_config({
        "periodic_search_refresh_enabled": "1",
        "search_refresh_interval_minutes": 100,
    })
    assert cfg3["periodic_search_refresh_enabled"] is True
    assert cfg3["search_refresh_interval_minutes"] == 60

    # 3. 异常非数字值 fallback 为 5
    cfg4 = DesktopController._normalize_profile_task_config({
        "search_refresh_interval_minutes": "invalid",
    })
    assert cfg4["search_refresh_interval_minutes"] == 5


def test_profile_task_config_normalizes_smart_schedule():
    # 1. 默认状态：缺省时默认为 True
    cfg1 = DesktopController._normalize_profile_task_config({})
    assert cfg1["smart_schedule_enabled"] is True

    # 2. 显式关闭：支持 bool/字符串
    cfg2 = DesktopController._normalize_profile_task_config({"smart_schedule_enabled": False})
    assert cfg2["smart_schedule_enabled"] is False

    cfg3 = DesktopController._normalize_profile_task_config({"smart_schedule_enabled": "false"})
    assert cfg3["smart_schedule_enabled"] is False

    cfg4 = DesktopController._normalize_profile_task_config({"smart_schedule_enabled": "0"})
    assert cfg4["smart_schedule_enabled"] is False

    # 3. 显式开启
    cfg5 = DesktopController._normalize_profile_task_config({"smart_schedule_enabled": "true"})
    assert cfg5["smart_schedule_enabled"] is True




def test_stale_automation_cleanup_does_not_remove_new_run_state():
    profile_id = "profile-race-test"
    try:
        with controller_module._RUNNING_LOCK:
            controller_module._RUNNING_PROFILES.add(profile_id)
        with controller_module._AUTOMATION_CONTROL_LOCK:
            controller_module._AUTOMATION_RUN_IDS[profile_id] = "new-run"
            controller_module._AUTOMATION_CONTROLS[profile_id] = object()
        controller_module._clear_automation_state(profile_id, "old-run")
        with controller_module._RUNNING_LOCK:
            assert profile_id in controller_module._RUNNING_PROFILES
        with controller_module._AUTOMATION_CONTROL_LOCK:
            assert controller_module._AUTOMATION_RUN_IDS[profile_id] == "new-run"
            assert profile_id in controller_module._AUTOMATION_CONTROLS
    finally:
        controller_module._clear_automation_state(profile_id, "new-run")


def test_controller_captures_engine_future_into_external_statistics_layer():
    with tempfile.TemporaryDirectory() as directory:
        statistics = AutomationStatisticsStore(Path(directory) / "state.db")
        safety = AutomationSafetyStore(Path(directory) / "safety.db")
        agent_service = FakeAgentService()
        controller = DesktopController(
            api=FakeApi(),
            browser_manager=FakeBrowserManager(),
            discovery=FakeDiscovery(),
            registry=FakeRegistry([make_record()]),
            task_service=FakeTaskService(),
            agent_service=agent_service,
            automation_statistics=statistics,
            automation_safety=safety,
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

    assert mini.minimumWidth() == 420
    assert mini.minimumHeight() == 200
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
    assert values["daily_task_limit"] == 100
    assert values["max_follower_threshold"] == 150
    assert values["max_engagement_threshold"] == 10_000
    assert values["ai_reply_ratio"] == 0.0
    assert values["sleep_on_rate_limit"] is True
    assert values["schedule_mode"] == "smart"
    assert values["schedule_type"] == "once"
    assert values["schedule_timezone"] == "Asia/Shanghai"

    dialog.ai_reply_ratio_input.setCurrentIndex(2)
    assert dialog.config()["ai_reply_ratio"] == 0.15

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


def test_task_config_dialog_keeps_loaded_engine_choices_when_config_arrives_late():
    app = qapp()
    engines = [
        {"engine_id": "default", "name": "默认自动化引擎", "version": "bundled"},
        {"engine_id": "x-stability-test-v0.1", "name": "X 稳定性测试 v0.1", "version": "0.2.0-test"},
    ]
    dialog = TaskConfigDialog({}, engines=engines)

    # This mirrors the asynchronous saved-config callback that previously
    # replaced the freshly loaded list with the default engine only.
    dialog.apply_initial({"active": {"engine_id": "default", "keyword": "婚活"}})

    assert dialog.engine_input.count() == 2
    assert any(
        (dialog.engine_input.itemData(index) or {}).get("engine_id") == "x-stability-test-v0.1"
        for index in range(dialog.engine_input.count())
    )
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


def test_agent_reauth_dialog_allows_first_activation_id_input():
    app = qapp()
    dialog = AgentReauthDialog("")
    assert not dialog.agent_id_input.isReadOnly()
    assert dialog.windowTitle() == "激活运行端"
    dialog.agent_id_input.setText("agent-from-web")
    dialog.agent_token_input.setText("lag_example_activation_token")
    assert dialog.credentials() == ("agent-from-web", "lag_example_activation_token")
    dialog.close()
    app.processEvents()


def test_controller_allows_first_activation_and_locks_bound_agent_id():
    service = FirstActivationAgentService()
    controller = make_controller(records=[], agent_service=service)

    result = controller.replace_agent_credentials("agent-from-web", "lag_example_activation_token")
    assert result["agent"] == "ONLINE"
    assert service.server_client.replacements == [("agent-from-web", "lag_example_activation_token")]

    try:
        controller.replace_agent_credentials("another-agent", "lag_example_activation_token")
    except ValueError as exc:
        assert "Agent ID 不允许修改" in str(exc)
    else:
        raise AssertionError("bound Agent ID must not be replaceable")


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


def test_send_telegram_summary_report_uses_today_statistics():
    rec = make_record(profile_id="p-11")
    controller = make_controller(records=[rec])
    controller.telegram_notifier.config.enabled = True
    controller.telegram_notifier.config.chat_id = "123456"

    # 模拟今日持久化统计数据（账号 11 今日赞 6 关 5 扫 41）
    controller.task_statistics = lambda period="today": {
        "by_account": {
            "p-11": {"likes": 6, "follows": 5, "comments": 0, "scanned_posts": 41, "own_followers": 33},
        }
    }
    controller._account_snapshots.clear()

    sent_messages = []
    controller.telegram_notifier.send_raw_message = lambda chat_id, text, **kw: (sent_messages.append((chat_id, text)) or (True, "OK"))

    ok, msg = controller.send_telegram_summary_report()
    assert ok
    assert len(sent_messages) == 1
    report_text = sent_messages[0][1]
    assert "今日累计关注：*5* 人" in report_text
    assert "今日累计点赞：*6* 次" in report_text
    assert "今日扫描总量：*41* 位" in report_text
    assert "[11]" in report_text


def test_delete_account_stops_profile_and_removes_from_registry():
    rec = make_record(profile_id="p-11")
    controller = make_controller(records=[rec])
    assert controller.delete_account("p-11") is True
    assert "p-11" in controller.browser_manager.stopped
    assert "p-11" in controller.browser_manager.deleted
    assert "p-11" in controller.registry.removed


def test_delete_accounts_batch():
    rec1 = make_record(profile_id="p-11")
    rec2 = make_record(profile_id="p-22")
    controller = make_controller(records=[rec1, rec2])
    count = controller.delete_accounts(["p-11", "p-22"])
    assert count == 2
    assert "p-11" in controller.browser_manager.deleted
    assert "p-22" in controller.browser_manager.deleted


def test_account_card_widget_shows_clean_stats_and_delete_button():
    app = qapp()
    rec = make_record(profile_id="p-11", profile_name="11")
    row = account_to_row(rec, None, None)
    deleted_ids = []
    from desktop.main_window import AccountCardWidget
    card = AccountCardWidget(
        row,
        {"own_followers": None, "own_following": None},
        on_select=lambda pid, mods=None: None,
        on_run=lambda pid: None,
        on_stop=lambda pid: None,
        on_config=lambda pid: None,
        on_delete=lambda pid: deleted_ids.append(pid),
    )
    # Check that None is displayed as "尚未读取", not "None"
    stats_label = card.findChild(QLabel, "accountStats")
    assert "None" not in stats_label.text()
    assert "尚未读取" in stats_label.text()

    # Check delete button
    del_btn = card.findChild(QPushButton, "miniDeleteButton")
    assert del_btn is not None
    del_btn.click()
    assert deleted_ids == ["p-11"]
    card.close()


def test_controller_group_management_and_clustering():
    controller = make_controller(records=[
        make_record(profile_id="p-1", profile_name="Acct1"),
        make_record(profile_id="p-2", profile_name="Acct2"),
    ])
    groups = controller.list_profile_groups()
    assert len(groups) == 1
    assert groups[0]["group_id"] == "node_direct"
    assert len(groups[0]["records"]) == 2

    grp = controller.create_custom_group("测试矩阵A")
    assert grp.name == "测试矩阵A"
    controller.assign_profile_to_group("p-1", grp.group_id)

    groups2 = controller.list_profile_groups()
    assert len(groups2) == 2
    assert groups2[0]["name"] == "测试矩阵A"
    assert len(groups2[0]["records"]) == 1
    assert groups2[0]["records"][0].profile_id == "p-1"

    assert not controller.is_group_collapsed(grp.group_id)
    controller.set_group_collapsed(grp.group_id, True)
    assert controller.is_group_collapsed(grp.group_id)

    controller.delete_custom_group(grp.group_id)
    groups3 = controller.list_profile_groups()
    assert len(groups3) == 1
    assert len(groups3[0]["records"]) == 2


def test_controller_group_rotation_lifecycle():
    rec1 = make_record(profile_id="p-1", profile_name="1")
    rec2 = make_record(profile_id="p-2", profile_name="2")
    controller = make_controller(records=[rec1, rec2], agent_service=FakeAgentService())

    status = controller.start_group_rotation("node_direct", "直连组", ["p-1", "p-2"])
    assert status["status"] == "RUNNING"
    assert controller.is_group_rotating("node_direct")

    controller.stop_group_rotation("node_direct")
    assert not controller.is_group_rotating("node_direct")


def test_desktop_group_view_and_accordion_collapse():
    app = qapp()
    rec1 = make_record(profile_id="p-1", profile_name="1")
    rec2 = make_record(profile_id="p-2", profile_name="2")
    controller = make_controller(records=[rec1, rec2])
    window = MainWindow(controller)

    assert window.group_filter_combo.currentData() == "__ALL_FLAT__"
    assert window.table.rowCount() == 2
    assert window.btn_expand_all.isHidden()

    idx = window.group_filter_combo.findData("__ALL_GROUPED__")
    assert idx >= 0
    window.group_filter_combo.setCurrentIndex(idx)
    app.processEvents()

    assert window.table.rowCount() == 3
    assert not window.btn_expand_all.isHidden()
    assert not window.btn_collapse_all.isHidden()

    window._collapse_all_groups()
    app.processEvents()
    assert window.table.isRowHidden(1)
    assert window.table.isRowHidden(2)
    assert not window.table.isRowHidden(0)

    window._expand_all_groups()
    app.processEvents()
    assert not window.table.isRowHidden(1)
    assert not window.table.isRowHidden(2)

    window.close()
    app.processEvents()


def test_controller_browser_cleanup_on_terminal_risk_and_error():
    import concurrent.futures
    bm = FakeBrowserManager()
    controller = make_controller(records=[make_record(profile_id="p-risk")], browser_manager=bm)

    # 1. 模拟发生 PROXY_DISCONNECTED 终端风险状态
    fut = concurrent.futures.Future()
    fut.set_result({"status": "PROXY_DISCONNECTED", "error": "Proxy dropped connection"})
    controller._automation_result_finished_impl(
        fut,
        run_id="run-risk-1",
        profile_id="p-risk",
        x_account_id="acc-risk",
        account_tag="risk-tag",
        started_at="2026-09-08T12:00:00Z",
    )
    # 验证底层浏览器被自动 stop_profile 回收
    assert "p-risk" in bm.stopped

    # 2. 模拟正常完成 SUCCESS 状态，不应额外调用 stop_profile
    bm.stopped.clear()
    fut_ok = concurrent.futures.Future()
    fut_ok.set_result({"status": "SUCCESS", "follows": 5})
    controller._automation_result_finished_impl(
        fut_ok,
        run_id="run-ok-1",
        profile_id="p-risk",
        x_account_id="acc-risk",
        account_tag="risk-tag",
        started_at="2026-09-08T12:00:00Z",
    )
    assert "p-risk" not in bm.stopped

    # 3. 验证快照读取
    snap = controller.get_account_snapshot("p-risk")
    assert snap.get("status") == "SUCCESS"
    assert snap.get("follows") == 5


def test_schedule_automation_task_arming_and_card_rendering():
    qapp()
    record = make_record(profile_id="p-sched-1", profile_name="定时测试号")
    controller = make_controller(records=[record], agent_service=FakeAgentService())

    future_run = (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:00+08:00")
    config = {
        "schedule_mode": "scheduled",
        "schedule_type": "once",
        "scheduled_at": future_run,
        "schedule_timezone": "Asia/Shanghai",
    }
    result = controller.start_automation_task("p-sched-1", config)
    assert result["status"] == "SCHEDULED"
    assert result["schedule_type"] == "once"

    # 1. 验证 list_accounts 返回的新字段
    accounts = controller.list_accounts()
    assert len(accounts) == 1
    row = accounts[0]
    assert row.schedule_mode == "scheduled"
    assert row.schedule_status == "WAITING_SCHEDULE"
    assert row.schedule_type == "once"
    assert bool(row.schedule_next_run)

    # 2. 验证 AccountCardWidget 可视化角标与按钮
    card = AccountCardWidget(
        row,
        {},
        on_select=lambda pid, m: None,
        on_run=lambda pid: None,
        on_stop=lambda pid: None,
        on_config=lambda pid: None,
    )
    state_label = card.findChild(QLabel, "tagScheduled")
    assert state_label is not None
    assert "等待定时" in state_label.text()
    stop_btn = card.findChild(QPushButton, "miniStopButton")
    assert stop_btn is not None
    assert stop_btn.text() == "取消定时"


def test_cancel_scheduled_task():
    qapp()
    record = make_record(profile_id="p-sched-cancel", profile_name="取消定时测试号")
    controller = make_controller(records=[record], agent_service=FakeAgentService())

    events = []
    controller.register_schedule_listener(lambda pid, state, data: events.append((pid, state)))

    future_run = (datetime.now() + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:00+08:00")
    config = {
        "schedule_mode": "scheduled",
        "schedule_type": "once",
        "scheduled_at": future_run,
        "schedule_timezone": "Asia/Shanghai",
    }
    controller.start_automation_task("p-sched-cancel", config)
    assert ("p-sched-cancel", "WAITING_SCHEDULE") in events

    # 用户执行取消定时
    cancelled = controller.cancel_scheduled_task("p-sched-cancel")
    assert cancelled is True
    assert ("p-sched-cancel", "CANCELLED_BY_USER") in events

    accounts = controller.list_accounts()
    assert accounts[0].schedule_status == "CANCELLED_BY_USER"


def test_schedule_watchdog_detects_due_task():
    record = make_record(profile_id="p-due-1", profile_name="到期测试号")
    controller = make_controller(records=[record], agent_service=FakeAgentService())

    past_time = (datetime.now() - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:00+08:00")
    controller.runtime_config.update(
        "p-due-1",
        {
            "schedule_mode": "scheduled",
            "schedule_type": "once",
            "schedule_status": "WAITING_SCHEDULE",
            "schedule_next_run": past_time,
            "scheduled_at": past_time,
            "schedule_timezone": "Asia/Shanghai",
        },
        mode="HOT_UPDATE",
    )

    # 运行看门狗心跳
    triggered = controller.check_scheduled_tasks_tick()
    assert "p-due-1" in triggered
    controller.stop_agent_service()


def test_task_config_dialog_past_time_auto_correction():
    qapp()
    dialog = TaskConfigDialog(initial={"active": {"schedule_mode": "scheduled", "schedule_type": "once"}})
    dialog.schedule_mode_input.setCurrentIndex(dialog.schedule_mode_input.findData("scheduled"))
    dialog.schedule_type_input.setCurrentIndex(dialog.schedule_type_input.findData("once"))

    # 设置一个过去的时间
    past_dt = QDateTime.currentDateTime().addSecs(-600)
    dialog.scheduled_at_input.setDateTime(past_dt)

    # 点击确定 (accept)
    dialog.accept()

    # 验证时间被自动修正为当前时间之后
    cfg = dialog.config()
    target_dt = datetime.fromisoformat(cfg["scheduled_at"])
    now_dt = datetime.now(target_dt.tzinfo)
    assert target_dt > now_dt


def test_claim_scheduled_execution_prevents_duplicate_triggering():
    record = make_record(profile_id="p-claim-1", profile_name="防并发测试号")
    controller = make_controller(records=[record], agent_service=FakeAgentService())

    future_run = (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:00+08:00")
    config = {
        "schedule_mode": "scheduled",
        "schedule_type": "once",
        "scheduled_at": future_run,
        "schedule_timezone": "Asia/Shanghai",
    }
    controller.start_automation_task("p-claim-1", config)

    # 1. 首次认领执行权：应当成功，并原子置为 TRIGGERING
    claimed, active = controller._claim_scheduled_execution("p-claim-1")
    assert claimed is True
    assert active.get("schedule_mode") == "scheduled"

    snapshot = controller.runtime_config.snapshot("p-claim-1")
    assert snapshot["active"]["schedule_status"] == "TRIGGERING"

    # 2. 第二次并发认领（例如看门狗或重复定时器）：应当直接被拦截拒绝
    claimed2, _ = controller._claim_scheduled_execution("p-claim-1")
    assert claimed2 is False


def test_postpone_scheduled_task_arms_delayed_timer():
    record = make_record(profile_id="p-postpone-1", profile_name="延后重试测试号")
    controller = make_controller(records=[record], agent_service=FakeAgentService())

    events = []
    controller.register_schedule_listener(lambda pid, state, data: events.append((pid, state, data)))

    config = {
        "schedule_mode": "scheduled",
        "schedule_type": "once",
        "schedule_timezone": "Asia/Shanghai",
    }
    controller._postpone_scheduled_task("p-postpone-1", config, reason="PROFILE_BUSY", delay_seconds=60)

    snapshot = controller.runtime_config.snapshot("p-postpone-1")
    assert snapshot["active"]["schedule_status"] == "DELAYED_PROFILE_BUSY"
    assert bool(snapshot["active"]["schedule_next_run"])
    assert any(e[1] == "DELAYED_PROFILE_BUSY" for e in events)


def test_account_card_rendering_triggering_state():
    qapp()
    record = make_record(profile_id="p-card-trig", profile_name="启动中测试号")
    controller = make_controller(records=[record], agent_service=FakeAgentService())
    controller.runtime_config.update(
        "p-card-trig",
        {
            "schedule_mode": "scheduled",
            "schedule_type": "once",
            "schedule_status": "TRIGGERING",
            "schedule_next_run": "2026-09-18T18:00",
        },
        mode="HOT_UPDATE",
    )
    accounts = controller.list_accounts()
    row = accounts[0]
    assert row.schedule_status == "TRIGGERING"

    card = AccountCardWidget(
        row,
        {},
        on_select=lambda pid, m: None,
        on_run=lambda pid: None,
        on_stop=lambda pid: None,
        on_config=lambda pid: None,
    )
    state_label = card.findChild(QLabel, "tagScheduled")
    assert state_label is not None
    assert "到点启动中" in state_label.text()
    stop_btn = card.findChild(QPushButton, "miniStopButton")
    assert stop_btn is not None
    assert stop_btn.text() == "取消定时"






