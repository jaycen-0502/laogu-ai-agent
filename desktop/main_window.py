from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Callable

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QLineEdit,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .controller import AccountRow, DesktopController
from .workers import FunctionWorker


class MainWindow(QMainWindow):
    HEADERS = (
        "Profile",
        "Profile ID",
        "Browser",
        "Login",
        "X Username",
        "X Account ID",
        "Account Status",
        "Last Checked",
    )

    def __init__(self, controller: DesktopController | None = None):
        super().__init__()
        self.controller = controller or DesktopController()
        self.thread_pool = QThreadPool.globalInstance()
        self._active_jobs = 0
        self._profiles: list[dict[str, Any]] = []
        self._statistics: dict[str, Any] = {}
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
        self.setWindowTitle("Laogu 账号资产控制中心")
        self.resize(1260, 760)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(objectName="header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 14, 20, 14)
        title = QLabel("Laogu 账号资产控制中心", objectName="title")
        subtitle = QLabel("Profile 运行管理与 X 账号只读识别", objectName="subtitle")
        status_layout = QHBoxLayout()
        self.server_state_label = QLabel("Server: OFFLINE", objectName="summary")
        self.agent_state_label = QLabel("Agent: UNCONFIGURED", objectName="summary")
        self.heartbeat_label = QLabel("Last Heartbeat: -", objectName="summary")
        status_layout.addWidget(self.server_state_label)
        status_layout.addWidget(self.agent_state_label)
        status_layout.addWidget(self.heartbeat_label)
        status_layout.addStretch(1)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        header_layout.addLayout(status_layout)
        layout.addWidget(header)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(12)

        toolbar = QHBoxLayout()
        self.refresh_button = self._button("刷新 Profile", QStyle.SP_BrowserReload)
        self.scan_all_button = self._button("扫描全部", QStyle.SP_DialogApplyButton)
        self.scan_all_button.setObjectName("primaryButton")
        self.scan_selected_button = self._button("扫描选中", QStyle.SP_FileDialogContentsView)
        self.start_button = self._button("启动 Profile", QStyle.SP_MediaPlay)
        self.stop_button = self._button("停止 Profile", QStyle.SP_MediaStop)
        for button in (
            self.refresh_button,
            self.scan_all_button,
            self.scan_selected_button,
            self.start_button,
            self.stop_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.summary_label = QLabel("0 个账号", objectName="summary")
        toolbar.addWidget(self.summary_label)
        content_layout.addLayout(toolbar)

        read_only_toolbar = QHBoxLayout()
        read_only_toolbar.addWidget(QLabel("只读任务"))
        self.check_login_button = self._button("登录检查", QStyle.SP_DialogApplyButton)
        self.read_profile_button = self._button("读取 Profile", QStyle.SP_FileDialogInfoView)
        self.read_timeline_button = self._button("读取时间线", QStyle.SP_BrowserReload)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键词后执行只读搜索")
        self.search_input.setClearButtonEnabled(True)
        self.search_button = self._button("关键词搜索", QStyle.SP_FileDialogContentsView)
        for button in (
            self.check_login_button,
            self.read_profile_button,
            self.read_timeline_button,
        ):
            read_only_toolbar.addWidget(button)
        read_only_toolbar.addWidget(self.search_input, 1)
        read_only_toolbar.addWidget(self.search_button)
        content_layout.addLayout(read_only_toolbar)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)

        statistics_frame = QFrame()
        statistics_layout = QGridLayout(statistics_frame)
        statistics_layout.setContentsMargins(0, 0, 0, 0)
        self.stat_labels = {}
        for column, (key, label) in enumerate((
            ("total_tasks", "今日任务"),
            ("success_tasks", "成功"),
            ("failed_tasks", "失败"),
            ("timeout_tasks", "超时"),
        )):
            caption = QLabel(label, objectName="summary")
            value = QLabel("0")
            value.setStyleSheet("font-size: 20px; font-weight: 600;")
            statistics_layout.addWidget(caption, 0, column)
            statistics_layout.addWidget(value, 1, column)
            self.stat_labels[key] = value

        self.activity_table = QTableWidget(0, 5)
        self.activity_table.setHorizontalHeaderLabels(("时间", "任务类型", "状态", "耗时", "摘要"))
        self.activity_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.activity_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.activity_table.verticalHeader().setVisible(False)
        self.activity_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.activity_table.horizontalHeader().setStretchLastSection(True)

        local_panel = QWidget()
        local_layout = QVBoxLayout(local_panel)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.addWidget(QLabel("任务统计"))
        local_layout.addWidget(statistics_frame)
        local_layout.addWidget(QLabel("最近活动"))
        local_layout.addWidget(self.activity_table)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(500)
        self.log_output.setPlaceholderText("操作日志")

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(local_panel)
        splitter.addWidget(self.log_output)
        splitter.setSizes([390, 230, 140])
        content_layout.addWidget(splitter, 1)
        layout.addWidget(content, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("正在检查连接...")

    def _button(self, text: str, icon: QStyle.StandardPixmap) -> QPushButton:
        button = QPushButton(text)
        button.setIcon(self.style().standardIcon(icon))
        return button

    def _wire_events(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_profiles)
        self.scan_all_button.clicked.connect(self.scan_all)
        self.scan_selected_button.clicked.connect(self.scan_selected)
        self.start_button.clicked.connect(self.start_selected)
        self.stop_button.clicked.connect(self.stop_selected)
        self.check_login_button.clicked.connect(self.run_check_login)
        self.read_profile_button.clicked.connect(self.run_read_profile)
        self.read_timeline_button.clicked.connect(self.run_read_timeline)
        self.search_button.clicked.connect(self.run_x_search)
        self.search_input.returnPressed.connect(self.run_x_search)
        self.table.itemSelectionChanged.connect(self._account_selection_changed)
        self.activity_table.cellDoubleClicked.connect(self._show_activity_detail)

    def _load_registry(self) -> None:
        try:
            self.set_accounts(self.controller.list_accounts())
        except Exception as exc:
            self._show_error(f"读取账号资产失败: {exc}")

    def _load_local_statistics(self) -> None:
        try:
            self.set_statistics(self.controller.task_statistics("today"))
            self.set_activities(self.controller.recent_activities(20))
        except Exception as exc:
            self._log(f"FAILED   读取本地任务统计: {exc}")

    def _refresh_agent_status(self) -> None:
        status = self.controller.server_agent_status()
        self.server_state_label.setText(f"Server: {status.get('server', 'OFFLINE')}")
        self.agent_state_label.setText(f"Agent: {status.get('agent', 'OFFLINE')}")
        heartbeat = str(status.get("last_heartbeat") or "-").replace("T", " ")[:19]
        self.heartbeat_label.setText(f"Last Heartbeat: {heartbeat}")

    def closeEvent(self, event) -> None:
        self.controller.stop_agent_service()
        super().closeEvent(event)

    def set_statistics(self, summary: dict[str, Any]) -> None:
        self._statistics = summary
        for key, label in self.stat_labels.items():
            label.setText(str(summary.get(key, 0)))

    def _account_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1:
            self.set_statistics(self._statistics)
            return
        account_item = self.table.item(rows[0].row(), 5)
        account_id = account_item.text().strip() if account_item else ""
        account_summary = self._statistics.get("by_account", {}).get(account_id)
        if account_summary:
            for key, label in self.stat_labels.items():
                label.setText(str(account_summary.get(key, 0)))

    def _show_activity_detail(self, row: int, column: int) -> None:
        del column
        item = self.activity_table.item(row, 0)
        activity = item.data(Qt.UserRole) if item else None
        if not isinstance(activity, dict):
            return
        task = self.controller.task_detail(str(activity.get("task_id") or "")) or {}
        detail = {
            "x_account_id": task.get("x_account_id") or activity.get("x_account_id"),
            "task_type": task.get("task_type") or activity.get("activity_type"),
            "params": task.get("params", {}),
            "status": task.get("status") or activity.get("status"),
            "error": task.get("error", ""),
            "result": task.get("result") or activity.get("result"),
        }
        dialog = QMessageBox(self)
        dialog.setWindowTitle("任务详情")
        dialog.setText(f"任务 {activity.get('task_id', '')}")
        dialog.setDetailedText(json.dumps(detail, ensure_ascii=False, indent=2))
        dialog.exec()

    def set_activities(self, activities: list[dict[str, Any]]) -> None:
        self.activity_table.setRowCount(len(activities))
        for row, activity in enumerate(activities):
            timestamp = str(activity.get("timestamp") or "")
            values = (
                timestamp.replace("T", " ")[:19],
                str(activity.get("activity_type") or ""),
                str(activity.get("status") or ""),
                f"{float(activity.get('duration') or 0):.3f}s",
                str(activity.get("summary") or ""),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setForeground(self._status_color(value))
                if column == 0:
                    item.setData(Qt.UserRole, activity)
                self.activity_table.setItem(row, column, item)

    def selected_profile_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        result = []
        for row in rows:
            item = self.table.item(row, 1)
            if item and item.text().strip():
                result.append(item.text().strip())
        return result

    def set_accounts(self, records: list[AccountRow]) -> None:
        self.table.setRowCount(len(records))
        for row_index, record in enumerate(records):
            values = (
                record.profile_name or "-",
                record.profile_id,
                record.browser_status,
                record.login_status,
                record.x_username or "-",
                record.x_account_id or "-",
                record.account_status,
                record.last_checked or "-",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (2, 3, 6):
                    item.setForeground(self._status_color(value))
                self.table.setItem(row_index, column, item)
        self.summary_label.setText(f"{len(records)} 个账号")

    @staticmethod
    def _status_color(status: str) -> QColor:
        if status in {"RUNNING", "LOGGED_IN", "VALID"}:
            return QColor("#137333")
        if status in {"ERROR", "DUPLICATE_ACCOUNT"}:
            return QColor("#b42318")
        if status in {"STOPPED", "NOT_LOGGED_IN"}:
            return QColor("#b54708")
        return QColor("#667085")

    def refresh_profiles(self) -> None:
        self._run_job("刷新 Profile", self.controller.refresh_profiles, self._profiles_finished)

    def scan_all(self) -> None:
        self._run_job("扫描全部账号", self.controller.scan_accounts, self._accounts_finished)

    def scan_selected(self) -> None:
        profile_ids = self._require_selection()
        if profile_ids:
            self._run_job(
                f"扫描选中账号: {', '.join(profile_ids)}",
                lambda: self.controller.scan_accounts(profile_ids),
                self._accounts_finished,
            )

    def start_selected(self) -> None:
        self._run_profile_action("启动", self.controller.start_profile)

    def stop_selected(self) -> None:
        self._run_profile_action("停止", self.controller.stop_profile)

    def run_check_login(self) -> None:
        self._run_read_only_task("x.check_login", "登录检查")

    def run_read_profile(self) -> None:
        self._run_read_only_task("x.read_profile", "读取 Profile")

    def run_read_timeline(self) -> None:
        self._run_read_only_task("x.read_timeline", "读取时间线")

    def run_x_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            QMessageBox.information(self, "请输入关键词", "请先输入要搜索的关键词。")
            return
        self._run_read_only_task("x.search", "关键词搜索", {"query": query})

    def _run_read_only_task(
        self,
        task_type: str,
        label: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        profile_ids = self._require_selection(single=True)
        if not profile_ids:
            return
        profile_id = profile_ids[0]
        self._run_job(
            f"{label}: Profile {profile_id}",
            lambda: self.controller.run_read_only_task(profile_id, task_type, params),
            lambda result: self._read_only_task_finished(label, profile_id, result),
        )

    def _read_only_task_finished(self, label: str, profile_id: str, result: Any) -> None:
        status = str(result.get("status") or "UNKNOWN") if isinstance(result, dict) else "UNKNOWN"
        self.statusBar().showMessage(f"{label}完成: {status}")
        self._log(f"{status}  {label} Profile {profile_id}")
        self._load_local_statistics()

    def _run_profile_action(
        self, action: str, function: Callable[[str], dict[str, Any]]
    ) -> None:
        profile_ids = self._require_selection(single=True)
        if not profile_ids:
            return
        profile_id = profile_ids[0]
        self._run_job(
            f"{action} Profile {profile_id}",
            lambda: function(profile_id),
            lambda result: self._profile_action_finished(action, profile_id, result),
        )

    def _require_selection(self, *, single: bool = False) -> list[str]:
        profile_ids = self.selected_profile_ids()
        if not profile_ids:
            QMessageBox.information(self, "请选择 Profile", "请先在账号表中选择 Profile。")
            return []
        if single and len(profile_ids) > 1:
            QMessageBox.information(self, "请选择一个 Profile", "启动或停止时只能选择一个 Profile。")
            return []
        return profile_ids

    def _run_job(
        self,
        label: str,
        function: Callable[[], Any],
        on_finished: Callable[[Any], None],
    ) -> None:
        self._active_jobs += 1
        self._update_busy_state()
        self._log(f"RUNNING  {label}")
        worker = FunctionWorker(function)
        worker.signals.finished.connect(on_finished)
        worker.signals.error.connect(lambda message: self._job_failed(label, message))
        worker.signals.done.connect(self._job_done)
        self.thread_pool.start(worker)

    def _job_done(self) -> None:
        self._active_jobs = max(0, self._active_jobs - 1)
        self._update_busy_state()

    def _update_busy_state(self) -> None:
        busy = self._active_jobs > 0
        for button in (
            self.refresh_button,
            self.scan_all_button,
            self.scan_selected_button,
            self.start_button,
            self.stop_button,
            self.check_login_button,
            self.read_profile_button,
            self.read_timeline_button,
            self.search_button,
        ):
            button.setEnabled(not busy)
        if busy:
            self.statusBar().showMessage("任务执行中...")

    def _health_finished(self, result: Any) -> None:
        self.statusBar().showMessage("Laogu Browser API 已连接")
        self._log("SUCCESS  Laogu Browser API 已连接")
        self.refresh_profiles()

    def _profiles_finished(self, profiles: list[dict[str, Any]]) -> None:
        self._profiles = profiles
        self.statusBar().showMessage(f"已发现 {len(profiles)} 个 Profile")
        self._log(f"SUCCESS  刷新 Profile，共 {len(profiles)} 个")

    def _accounts_finished(self, records: list[AccountRow]) -> None:
        self.set_accounts(records)
        self.statusBar().showMessage(f"账号扫描完成，共 {len(records)} 条资产记录")
        self._log(f"SUCCESS  账号扫描完成，共 {len(records)} 条")
        self._load_local_statistics()

    def _profile_action_finished(
        self, action: str, profile_id: str, result: dict[str, Any]
    ) -> None:
        self.statusBar().showMessage(f"Profile {profile_id} {action}请求完成")
        self._log(f"SUCCESS  {action} Profile {profile_id}")
        self.refresh_profiles()

    def _job_failed(self, label: str, message: str) -> None:
        self.statusBar().showMessage(f"{label}失败")
        self._log(f"FAILED   {label}: {message}")
        self._show_error(f"{label}失败\n\n{message}")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "操作失败", message)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{timestamp}] {message}")
