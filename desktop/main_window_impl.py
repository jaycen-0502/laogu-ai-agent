from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Callable

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .controller import AccountRow, DesktopController
from .workers import FunctionWorker


class TaskConfigDialog(QDialog):
    """Edit the safe, read-only per-profile automation parameters."""

    def __init__(self, initial: dict[str, Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("配置并运行自动化")
        self.setModal(True)
        self.setMinimumWidth(460)
        active = initial.get("active", {}) if isinstance(initial, dict) else {}
        if not isinstance(active, dict):
            active = initial if isinstance(initial, dict) else {}
        layout = QFormLayout(self)
        self.keyword_input = QLineEdit(str(active.get("keyword") or active.get("keywords") or ""))
        self.keyword_input.setMaxLength(200)
        self.keyword_input.setPlaceholderText("例如：Python、AI、automation")
        layout.addRow("检索关键词", self.keyword_input)
        self.daily_limit_input = self._spin(active.get("daily_task_limit"), 50, 1, 10_000)
        layout.addRow("单日任务上限", self.daily_limit_input)
        self.follower_limit_input = self._spin(active.get("max_follower_threshold"), 150, 0, 100_000_000)
        layout.addRow("粉丝门槛上限", self.follower_limit_input)
        self.engagement_limit_input = self._spin(active.get("max_engagement_threshold"), 10_000, 0, 100_000_000)
        layout.addRow("互动/帖子门槛上限", self.engagement_limit_input)
        hint = QLabel("仅执行浏览、读取和条件筛选；不会自动关注、点赞、评论、发帖或发送消息。")
        hint.setWordWrap(True)
        layout.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @staticmethod
    def _spin(value: Any, default: int, minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        try:
            widget.setValue(default if value is None else int(value))
        except (TypeError, ValueError):
            widget.setValue(default)
        return widget

    def config(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword_input.text().strip(),
            "daily_task_limit": self.daily_limit_input.value(),
            "max_follower_threshold": self.follower_limit_input.value(),
            "max_engagement_threshold": self.engagement_limit_input.value(),
            "sleep_on_rate_limit": True,
        }


class AgentReauthDialog(QDialog):
    """Collect a newly rotated Agent credential without displaying old tokens."""

    def __init__(self, agent_id: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("重新认证运行端")
        self.setModal(True)
        layout = QFormLayout(self)
        self.agent_id_input = QLineEdit(str(agent_id).strip())
        self.agent_id_input.setPlaceholderText("从 Web 后台复制 Agent ID")
        layout.addRow("Agent ID", self.agent_id_input)
        self.agent_token_input = QLineEdit()
        self.agent_token_input.setEchoMode(QLineEdit.Password)
        self.agent_token_input.setPlaceholderText("粘贴新生成的 Agent Token")
        layout.addRow("Agent Token", self.agent_token_input)
        hint = QLabel("Token 将由 Windows DPAPI 加密保存，界面和日志不会回显。")
        hint.setWordWrap(True)
        layout.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存并验证")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def credentials(self) -> tuple[str, str]:
        return self.agent_id_input.text().strip(), self.agent_token_input.text().strip()


class MainWindow(QMainWindow):
    HEADERS = ("账号", "档案 ID", "浏览器", "登录", "用户名", "账号 ID", "状态", "检查时间")

    def __init__(self, controller: DesktopController | None = None):
        super().__init__()
        self.controller = controller or DesktopController()
        self.thread_pool = QThreadPool.globalInstance()
        self._workers: set[FunctionWorker] = set()
        self._closing = False
        self._active_jobs = 0
        self._statistics: dict[str, Any] = {}
        self._profiles: list[dict[str, Any]] = []
        self._build_ui()
        self._wire_events()
        self._load_registry()
        self._load_local_statistics()
        self._run_job("检查 Laogu 连接", self.controller.health, self._health_finished)
        self._agent_status_timer = QTimer(self)
        self._agent_status_timer.timeout.connect(self._refresh_agent_status)
        self._agent_status_timer.start(5000)
        self._refresh_agent_status()

    def _build_ui(self) -> None:
        self.setWindowTitle("老谷自动化控制中心")
        self.setMinimumSize(1040, 720)
        self.resize(1240, 820)
        root = QWidget()
        root_layout = QVBoxLayout(root)
        header = QVBoxLayout()
        title = QLabel("老谷自动化控制中心")
        title.setObjectName("title")
        subtitle = QLabel("统一管理浏览器档案、账号状态与只读任务")
        subtitle.setObjectName("subtitle")
        header.addWidget(title)
        header.addWidget(subtitle)
        status = QHBoxLayout()
        self.server_state_label = QLabel("服务器：离线")
        self.agent_state_label = QLabel("运行端：未配置")
        self.heartbeat_label = QLabel("最近心跳：—")
        status.addWidget(self.server_state_label)
        status.addWidget(self.agent_state_label)
        status.addWidget(self.heartbeat_label)
        status.addStretch(1)
        self.reauth_button = self._button("重新认证运行端", QStyle.SP_DialogResetButton)
        self.reauth_button.setVisible(False)
        status.addWidget(self.reauth_button)
        header.addLayout(status)
        root_layout.addLayout(header)
        self.live_status_label = QLabel("● 运行端正在连接")
        root_layout.addWidget(self.live_status_label)

        splitter = QSplitter(Qt.Horizontal)
        left = QVBoxLayout()
        left_widget = QWidget(); left_widget.setLayout(left)
        metrics = QGridLayout()
        self.stat_labels: dict[str, QLabel] = {}
        for col, (key, text) in enumerate((("total_tasks", "今日任务"), ("success_tasks", "成功"), ("failed_tasks", "失败"), ("timeout_tasks", "超时"))):
            value = QLabel("0")
            value.setObjectName("metricValue")
            metrics.addWidget(QLabel(text), 0, col)
            metrics.addWidget(value, 1, col)
            self.stat_labels[key] = value
        left.addLayout(metrics)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("账号运行列表"))
        heading.addStretch(1)
        self.summary_label = QLabel("0 个账号")
        heading.addWidget(self.summary_label)
        left.addLayout(heading)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        left.addWidget(self.table, 1)
        actions = QGridLayout()
        self.refresh_button = self._button("刷新账号", QStyle.SP_BrowserReload)
        self.scan_all_button = self._button("扫描账号", QStyle.SP_DialogApplyButton)
        self.scan_selected_button = self._button("扫描选中", QStyle.SP_FileDialogContentsView)
        self.start_button = self._button("启动档案", QStyle.SP_MediaPlay)
        self.stop_button = self._button("停止档案", QStyle.SP_MediaStop)
        self.run_all_button = self._button("启动全部", QStyle.SP_MediaPlay)
        self.stop_all_button = self._button("停止全部", QStyle.SP_MediaStop)
        for idx, button in enumerate((self.refresh_button, self.scan_all_button, self.scan_selected_button, self.start_button, self.stop_button, self.run_all_button, self.stop_all_button)):
            actions.addWidget(button, idx // 2, idx % 2)
        left.addLayout(actions)
        splitter.addWidget(left_widget)

        right = QVBoxLayout()
        right_widget = QWidget(); right_widget.setLayout(right)
        self.selected_profile_label = QLabel("尚未选择档案")
        self.selected_runtime_label = QLabel("运行状态：—")
        right.addWidget(self.selected_profile_label)
        right.addWidget(self.selected_runtime_label)
        tools = QGridLayout()
        self.check_login_button = self._button("登录检查", QStyle.SP_DialogApplyButton)
        self.read_profile_button = self._button("读取档案", QStyle.SP_FileDialogInfoView)
        self.read_timeline_button = self._button("读取时间线", QStyle.SP_BrowserReload)
        self.automation_button = self._button("配置并运行自动化", QStyle.SP_MediaPlay)
        self.search_input = QLineEdit(); self.search_input.setPlaceholderText("输入关键词执行只读搜索")
        self.search_button = self._button("搜索", QStyle.SP_FileDialogContentsView)
        for pos, button in enumerate((self.check_login_button, self.read_profile_button, self.read_timeline_button, self.automation_button)):
            tools.addWidget(button, pos // 2, pos % 2)
        tools.addWidget(self.search_input, 2, 0); tools.addWidget(self.search_button, 2, 1)
        right.addLayout(tools)
        tabs = QTabWidget()
        self.activity_table = QTableWidget(0, 5)
        self.activity_table.setHorizontalHeaderLabels(("时间", "任务", "状态", "耗时", "摘要"))
        self.activity_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tabs.addTab(self.activity_table, "最近活动")
        self.log_output = QPlainTextEdit(); self.log_output.setReadOnly(True); self.log_output.setMaximumBlockCount(500)
        tabs.addTab(self.log_output, "控制台日志")
        right.addWidget(tabs, 1)
        splitter.addWidget(right_widget)
        splitter.setSizes([700, 500])
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("正在检查连接…")

    @staticmethod
    def _button(text: str, icon: QStyle.StandardPixmap) -> QPushButton:
        button = QPushButton(text)
        button.setIcon(QApplication.instance().style().standardIcon(icon))
        return button

    def _wire_events(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_profiles)
        self.scan_all_button.clicked.connect(self.scan_all)
        self.scan_selected_button.clicked.connect(self.scan_selected)
        self.run_all_button.clicked.connect(self.start_all)
        self.stop_all_button.clicked.connect(self.stop_all)
        self.start_button.clicked.connect(self.start_selected)
        self.stop_button.clicked.connect(self.stop_selected)
        self.automation_button.clicked.connect(self.configure_and_run_automation)
        self.reauth_button.clicked.connect(self.reauthenticate_agent)
        self.check_login_button.clicked.connect(lambda: self._run_read_only_task("x.check_login", "登录检查"))
        self.read_profile_button.clicked.connect(lambda: self._run_read_only_task("x.read_profile", "读取档案"))
        self.read_timeline_button.clicked.connect(lambda: self._run_read_only_task("x.read_timeline", "读取时间线"))
        self.search_button.clicked.connect(self.run_x_search)
        self.search_input.returnPressed.connect(self.run_x_search)
        self.table.itemSelectionChanged.connect(self._account_selection_changed)

    def _load_registry(self) -> None:
        try: self.set_accounts(self.controller.list_accounts())
        except Exception as exc: self._show_error(f"读取账号失败：{exc}")

    def _load_local_statistics(self) -> None:
        try:
            self.set_statistics(self.controller.task_statistics("today"))
            self.set_activities(self.controller.recent_activities(20))
        except Exception as exc: self._log(f"读取统计失败：{exc}")

    def _refresh_agent_status(self) -> None:
        status = self.controller.server_agent_status() or {}
        server, agent = status.get("server", "OFFLINE"), status.get("agent", "OFFLINE")
        server_text = {"ONLINE": "在线", "OFFLINE": "离线"}.get(server, server)
        agent_text = {"ONLINE": "在线", "OFFLINE": "离线", "UNCONFIGURED": "未配置", "REAUTH_REQUIRED": "需要重新认证"}.get(agent, agent)
        self.server_state_label.setText(f"服务器：{server_text}")
        self.agent_state_label.setText(f"运行端：{agent_text}")
        self.reauth_button.setVisible(agent in {"UNCONFIGURED", "UNREGISTERED", "REAUTH_REQUIRED"})
        if agent == "REAUTH_REQUIRED":
            live_text = "● 服务器可达，运行端需要重新认证"
        elif agent == "ONLINE":
            live_text = "● 运行端已连接服务器"
        else:
            live_text = "● 运行端正在连接服务器"
        self.live_status_label.setText(live_text)
        heartbeat = str(status.get("last_heartbeat") or "—").replace("T", " ")[:19]
        self.heartbeat_label.setText(f"最近心跳：{heartbeat}")

    def reauthenticate_agent(self) -> None:
        dialog = AgentReauthDialog(self.controller.current_agent_id(), self)
        if dialog.exec() != QDialog.Accepted: return
        agent_id, token = dialog.credentials()
        if not agent_id or not token:
            QMessageBox.warning(self, "信息不完整", "请填写 Agent ID 和新生成的 Agent Token。"); return
        self._run_job("重新认证运行端", lambda: self.controller.replace_agent_credentials(agent_id, token), lambda _: self._refresh_agent_status())
        dialog.agent_token_input.clear()

    def set_statistics(self, summary: dict[str, Any]) -> None:
        self._statistics = summary or {}
        for key, label in self.stat_labels.items(): label.setText(str(self._statistics.get(key, 0)))

    def set_activities(self, activities: list[dict[str, Any]]) -> None:
        self.activity_table.setRowCount(0)
        for row, activity in enumerate(activities or []):
            self.activity_table.insertRow(row)
            values = (str(activity.get("timestamp", "")).replace("T", " ")[:19], str(activity.get("activity_type", "")), str(activity.get("status", "")), f"{float(activity.get('duration') or 0):.3f}s", str(activity.get("summary", "")))
            for col, value in enumerate(values): self.activity_table.setItem(row, col, QTableWidgetItem(value))

    def selected_profile_ids(self) -> list[str]:
        result = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 1)
            if item and item.text().strip(): result.append(item.text().strip())
        return result

    def set_accounts(self, records: list[AccountRow]) -> None:
        self.table.setRowCount(len(records or []))
        for row, record in enumerate(records or []):
            values = (record.profile_name or "-", record.profile_id, record.browser_status, record.login_status, record.x_username or "-", record.x_account_id or "-", record.account_status, record.last_checked or "-")
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value)); self.table.setItem(row, col, item)
        self.summary_label.setText(f"{len(records or [])} 个账号")

    def _require_selection(self, single: bool = False) -> list[str]:
        ids = self.selected_profile_ids()
        if not ids: QMessageBox.information(self, "请选择档案", "请先选择一个浏览器档案。")
        elif single and len(ids) > 1: QMessageBox.information(self, "请选择一个档案", "此操作只能选择一个档案。")
        return ids if ids and (not single or len(ids) == 1) else []

    def refresh_profiles(self): self._run_job("刷新档案", self.controller.refresh_profiles, lambda result: self._profiles_finished(result))
    def scan_all(self): self._run_job("扫描账号", self.controller.scan_accounts, lambda result: self.set_accounts(result))
    def scan_selected(self):
        ids = self._require_selection()
        if ids: self._run_job("扫描选中账号", lambda: self.controller.scan_accounts(ids), lambda result: self.set_accounts(result))
    def start_all(self): self._run_profile_action("启动全部", self.controller.start_profile, [x.profile_id for x in self.controller.list_accounts()])
    def stop_all(self): self._run_profile_action("停止全部", self.controller.stop_profile, [x.profile_id for x in self.controller.list_accounts()])
    def start_selected(self): self._run_profile_action("启动", self.controller.start_profile, self._require_selection(single=True))
    def stop_selected(self): self._run_profile_action("停止", self.controller.stop_profile, self._require_selection(single=True))
    def run_x_search(self):
        query = self.search_input.text().strip()
        if query: self._run_read_only_task("x.search", "关键词搜索", {"query": query})
        else: QMessageBox.information(self, "请输入关键词", "请先输入搜索关键词。")

    def configure_and_run_automation(self):
        ids = self._require_selection(single=True)
        if not ids: return
        profile_id = ids[0]
        try: initial = self.controller.get_profile_task_config(profile_id)
        except Exception as exc: self._show_error(f"读取档案配置失败：{exc}"); return
        dialog = TaskConfigDialog(initial, self)
        if dialog.exec() == QDialog.Accepted:
            self._run_job(f"运行自动化：{profile_id}", lambda: self.controller.start_automation_task(profile_id, dialog.config()), lambda result: self._log(f"自动化完成：{result}"))

    def _run_read_only_task(self, task_type: str, label: str, params: dict[str, Any] | None = None):
        ids = self._require_selection(single=True)
        if ids: self._run_job(label, lambda: self.controller.run_read_only_task(ids[0], task_type, params), lambda result: self._log(f"{label}：{result}"))

    def _run_profile_action(self, label: str, function: Callable[[str], Any], ids: list[str] | None = None):
        selected = ids or self._require_selection()
        if selected: self._run_job(label, lambda: [function(item) for item in selected], lambda _: self.refresh_profiles())

    def _run_job(self, label: str, function: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        if self._closing: return
        self._active_jobs += 1; self._update_busy_state(); self._log(f"执行中：{label}")
        worker = FunctionWorker(function); self._workers.add(worker)
        worker.signals.finished.connect(callback); worker.signals.error.connect(lambda message: self._job_failed(label, message)); worker.signals.done.connect(lambda: self._job_done(worker))
        self.thread_pool.start(worker)

    def _job_done(self, worker: FunctionWorker):
        self._workers.discard(worker); self._active_jobs = max(0, self._active_jobs - 1); self._update_busy_state()
    def _update_busy_state(self):
        busy = self._active_jobs > 0
        for button in (self.refresh_button, self.scan_all_button, self.scan_selected_button, self.start_button, self.stop_button, self.run_all_button, self.stop_all_button, self.check_login_button, self.read_profile_button, self.read_timeline_button, self.search_button, self.automation_button, self.reauth_button): button.setEnabled(not busy)
    def _health_finished(self, _result): self.statusBar().showMessage("Laogu Browser API 已连接"); self.refresh_profiles()
    def _profiles_finished(self, profiles): self._profiles = profiles or []; self.statusBar().showMessage(f"已发现 {len(self._profiles)} 个档案")
    def _account_selection_changed(self):
        ids = self.selected_profile_ids()
        if len(ids) != 1:
            self.selected_profile_label.setText("尚未选择档案")
            self.selected_runtime_label.setText("运行状态：—")
            return
        profile_id = ids[0]
        profile = next(
            (item for item in self._profiles
             if str(item.get("profileId") or item.get("profile_id") or "") == profile_id),
            {},
        )
        name = str(profile.get("profileName") or profile.get("profile_name") or profile_id)
        running = bool(profile.get("running"))
        state = "运行中" if running else "未运行或状态未知"
        self.selected_profile_label.setText(f"{name} · {state}")
        self.selected_runtime_label.setText(f"运行状态：{state}")
    def _automation_finished(self, profile_id: str, result: Any) -> None:
        status = result.get("status", "UNKNOWN") if isinstance(result, dict) else "UNKNOWN"
        self.statusBar().showMessage(f"档案 {profile_id} 自动化完成：{status}")
        self._log(f"自动化完成：档案 {profile_id}，状态 {status}")
        self._load_local_statistics()
    def _read_only_task_finished(self, label: str, profile_id: str, result: Any) -> None:
        status = result.get("status", "UNKNOWN") if isinstance(result, dict) else "UNKNOWN"
        self.statusBar().showMessage(f"{label}完成：{status}")
        self._log(f"{label}：档案 {profile_id}，状态 {status}")
        self._load_local_statistics()
    def _profile_action_finished(self, action: str, profile_id: str, _result: Any) -> None:
        self.statusBar().showMessage(f"档案 {profile_id}{action}完成")
        self.refresh_profiles()
    def _job_failed(self, label, message): self._log(f"失败：{label}：{message}"); self._show_error(f"{label}失败\n\n{message}")
    def _show_error(self, message): QMessageBox.critical(self, "操作失败", message)
    def _log(self, message): self.log_output.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")

    @staticmethod
    def _status_color(status: str) -> QColor:
        if status in {"RUNNING", "LOGGED_IN", "VALID", "成功", "运行中"}:
            return QColor("#137333")
        if status in {"ERROR", "FAILED", "失败"}:
            return QColor("#b42318")
        if status in {"STOPPED", "NOT_LOGGED_IN", "TIMEOUT", "超时"}:
            return QColor("#b54708")
        return QColor("#667085")

    def closeEvent(self, event) -> None:
        self._closing = True
        self._agent_status_timer.stop()
        self.controller.stop_agent_service()
        event.accept()
