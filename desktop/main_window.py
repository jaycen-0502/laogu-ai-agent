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
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QSpinBox,
)

from .controller import AccountRow, DesktopController
from .workers import FunctionWorker


class TaskConfigDialog(QDialog):
    """Per-Profile configuration for the safe read-only automation check."""

    def __init__(self, initial: dict[str, Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("配置并运行自动化")
        self.setModal(True)
        initial = initial or {}
        active = initial.get("active") if isinstance(initial.get("active"), dict) else initial
        layout = QFormLayout(self)

        self.keyword_input = QLineEdit(str(active.get("keyword") or active.get("keywords") or ""))
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
        hint.setObjectName("subtitle")
        layout.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @staticmethod
    def _spin(value: Any, default: int, minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        try:
            widget.setValue(int(value))
        except (TypeError, ValueError):
            widget.setValue(default)
        widget.setRange(minimum, maximum)
        if value is None:
            widget.setValue(default)
        return widget

    def config(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword_input.text().strip()[:200],
            "daily_task_limit": self.daily_limit_input.value(),
            "max_follower_threshold": self.follower_limit_input.value(),
            "max_engagement_threshold": self.engagement_limit_input.value(),
            "sleep_on_rate_limit": True,
        }


class AgentReauthDialog(QDialog):
    """Collect a newly rotated Agent credential without ever displaying the old token."""

    def __init__(self, agent_id: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("重新认证运行端")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QFormLayout(self)

        self.agent_id_input = QLineEdit(str(agent_id).strip())
        self.agent_id_input.setReadOnly(True)
        self.agent_id_input.setToolTip("Agent ID 由服务器签发，不能在控制中心修改")
        layout.addRow("Agent ID", self.agent_id_input)

        self.agent_token_input = QLineEdit()
        self.agent_token_input.setEchoMode(QLineEdit.Password)
        self.agent_token_input.setPlaceholderText("粘贴新生成的 Agent Token")
        layout.addRow("Agent Token", self.agent_token_input)

        hint = QLabel(
            "Agent ID 由服务器固定绑定本机，控制中心不允许修改。"
            "请只粘贴 Web 后台重新生成的新 Token；保存后由 Windows DPAPI 加密。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("subtitle")
        layout.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存并验证")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def credentials(self) -> tuple[str, str]:
        return self.agent_id_input.text().strip(), self.agent_token_input.text().strip()


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
        self._workers: set[FunctionWorker] = set()
        self._closing = False
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

    def _build_legacy_ui(self) -> None:
        self.setWindowTitle("Laogu 账号资产控制中心")
        self.setMinimumSize(410, 700)
        self.resize(440, 820)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(objectName="header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 15, 18, 12)
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

        self.live_status_label = QLabel("●  Agent is starting", objectName="liveStatus")
        self.live_status_label.setContentsMargins(18, 5, 18, 5)
        layout.addWidget(self.live_status_label)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(14, 10, 14, 12)
        content_layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.refresh_button = self._button("刷新 Profile", QStyle.SP_BrowserReload)
        self.scan_all_button = self._button("扫描全部", QStyle.SP_DialogApplyButton)
        self.scan_all_button.setObjectName("primaryButton")
        self.scan_selected_button = self._button("扫描选中", QStyle.SP_FileDialogContentsView)
        self.start_button = self._button("启动 Profile", QStyle.SP_MediaPlay)
        self.stop_button = self._button("停止 Profile", QStyle.SP_MediaStop)
        self.run_all_button = self._button("运行全部", QStyle.SP_MediaPlay)
        self.run_all_button.setObjectName("runAllButton")
        self.stop_all_button = self._button("停止全部", QStyle.SP_MediaStop)
        self.stop_all_button.setObjectName("stopAllButton")
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

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.run_all_button, 1)
        actions.addWidget(self.stop_all_button, 1)
        content_layout.addLayout(actions)

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
        self.table.setObjectName("accountTable")
        for column in (1, 3, 4, 5, 6, 7):
            self.table.setColumnHidden(column, True)
        self.table.horizontalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(48)

        # Replace legacy mojibake labels in older portable configurations with
        # stable, readable control-center copy.
        self.setWindowTitle("Laogu Control Center")
        title.setText("Laogu Control Center")
        subtitle.setText("Profile automation · read-only account operations")
        self.refresh_button.setText("Refresh")
        self.scan_all_button.setText("Scan all")
        self.scan_selected_button.setText("Scan selected")
        self.start_button.setText("Start profile")
        self.stop_button.setText("Stop profile")
        self.check_login_button.setText("登录检查")
        self.read_profile_button.setText("读取 Profile")
        self.read_timeline_button.setText("读取时间线")
        self.search_input.setPlaceholderText("输入关键词后执行只读搜索")
        self.search_button.setText("关键词搜索")
        self.scan_selected_button.setVisible(False)
        self.start_button.setVisible(False)
        self.stop_button.setVisible(False)
        self.table.setHorizontalHeaderLabels(("Account", "Profile ID", "Browser", "Login", "Username", "Account ID", "Status", "Checked"))

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
        self.activity_table.setHorizontalHeaderLabels(("Time", "Task", "Status", "Duration", "Summary"))
        self.activity_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.activity_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.activity_table.verticalHeader().setVisible(False)
        self.activity_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.activity_table.horizontalHeader().setStretchLastSection(True)

        local_panel = QWidget()
        local_layout = QVBoxLayout(local_panel)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.addWidget(QLabel("Today"))
        local_layout.addWidget(statistics_frame)
        local_layout.addWidget(QLabel("Activity log"))
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

    def _build_ui(self) -> None:
        """Build the Chinese control-center layout used by the desktop app."""
        self.setWindowTitle("老谷自动化控制中心")
        self.setMinimumSize(1040, 720)
        self.resize(1240, 820)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QFrame(objectName="header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(22, 18, 22, 14)
        title = QLabel("老谷自动化控制中心", objectName="title")
        subtitle = QLabel("统一管理浏览器档案、账号状态与只读任务", objectName="subtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        status_row = QHBoxLayout()
        self.server_state_label = QLabel("服务器：离线", objectName="summary")
        self.agent_state_label = QLabel("运行端：未配置", objectName="summary")
        self.heartbeat_label = QLabel("最近心跳：—", objectName="summary")
        for item in (self.server_state_label, self.agent_state_label, self.heartbeat_label):
            status_row.addWidget(item)
        status_row.addStretch(1)
        self.reauth_button = self._button("重新认证运行端", QStyle.SP_DialogResetButton)
        self.reauth_button.setObjectName("primaryButton")
        self.reauth_button.setVisible(False)
        status_row.addWidget(self.reauth_button)
        header_layout.addLayout(status_row)
        root_layout.addWidget(header)

        self.live_status_label = QLabel("●  运行端正在连接", objectName="liveStatus")
        self.live_status_label.setContentsMargins(22, 8, 22, 8)
        root_layout.addWidget(self.live_status_label)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 18, 24, 20)
        content_layout.setSpacing(14)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 8, 0)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)

        overview = QFrame(objectName="overviewPanel")
        overview_layout = QGridLayout(overview)
        overview_layout.setContentsMargins(14, 10, 14, 10)
        overview_layout.setHorizontalSpacing(10)
        self.stat_labels = {}
        for column, (key, label) in enumerate((("total_tasks", "今日任务"), ("success_tasks", "成功"), ("failed_tasks", "失败"), ("timeout_tasks", "超时"))):
            card = QFrame(objectName="metricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            caption = QLabel(label, objectName="metricCaption")
            value = QLabel("0", objectName="metricValue")
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            overview_layout.addWidget(card, 0, column)
            self.stat_labels[key] = value
        left_layout.addWidget(overview)

        accounts_header = QHBoxLayout()
        accounts_title = QLabel("账号运行列表", objectName="sectionTitle")
        accounts_header.addWidget(accounts_title)
        accounts_header.addStretch(1)
        self.summary_label = QLabel("0 个账号", objectName="summary")
        accounts_header.addWidget(self.summary_label)
        left_layout.addLayout(accounts_header)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setObjectName("accountTable")
        self.table.setHorizontalHeaderLabels(("账号", "档案 ID", "浏览器", "登录", "用户名", "账号 ID", "状态", "检查时间"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(72)
        for column in (1, 2, 3, 4, 5, 6, 7):
            self.table.setColumnHidden(column, True)
        left_layout.addWidget(self.table, 1)

        action_panel = QFrame(objectName="actionPanel")
        action_layout = QGridLayout(action_panel)
        action_layout.setContentsMargins(12, 12, 12, 12)
        action_layout.setVerticalSpacing(10)
        action_layout.setHorizontalSpacing(10)
        self.run_all_button = self._button("运行全部", QStyle.SP_MediaPlay)
        self.run_all_button.setObjectName("runAllButton")
        self.stop_all_button = self._button("停止全部", QStyle.SP_MediaStop)
        self.stop_all_button.setObjectName("stopAllButton")
        self.refresh_button = self._button("刷新账号", QStyle.SP_BrowserReload)
        self.scan_all_button = self._button("扫描账号", QStyle.SP_DialogApplyButton)
        self.scan_all_button.setObjectName("primaryButton")
        action_layout.addWidget(self.run_all_button, 0, 0, 1, 2)
        action_layout.addWidget(self.stop_all_button, 0, 2, 1, 2)
        action_layout.addWidget(self.refresh_button, 1, 0, 1, 2)
        action_layout.addWidget(self.scan_all_button, 1, 2, 1, 2)
        self.automation_button = self._button("配置并运行自动化", QStyle.SP_MediaPlay)
        self.automation_button.setObjectName("primaryButton")
        action_layout.addWidget(self.automation_button, 2, 0, 1, 4)
        left_layout.addWidget(action_panel)

        runtime_panel = QFrame(objectName="runtimePanel")
        runtime_layout = QVBoxLayout(runtime_panel)
        runtime_layout.setContentsMargins(16, 14, 16, 14)
        runtime_layout.setSpacing(6)
        runtime_layout.addWidget(QLabel("所选档案运行信息", objectName="sectionTitle"))
        self.selected_profile_label = QLabel("尚未选择档案", objectName="runtimeValue")
        self.selected_runtime_label = QLabel("运行状态：—", objectName="summary")
        runtime_layout.addWidget(self.selected_profile_label)
        runtime_layout.addWidget(self.selected_runtime_label)
        right_layout.addWidget(runtime_panel)

        tools_panel = QFrame(objectName="toolsPanel")
        tools_layout = QGridLayout(tools_panel)
        tools_layout.setContentsMargins(16, 14, 16, 16)
        tools_layout.setVerticalSpacing(10)
        tools_layout.setHorizontalSpacing(8)
        tools_layout.addWidget(QLabel("只读工具", objectName="sectionTitle"), 0, 0)
        self.check_login_button = self._button("登录检查", QStyle.SP_DialogApplyButton)
        self.read_profile_button = self._button("读取档案", QStyle.SP_FileDialogInfoView)
        self.read_timeline_button = self._button("读取时间线", QStyle.SP_BrowserReload)
        self.scan_selected_button = self._button("扫描选中", QStyle.SP_FileDialogContentsView)
        self.start_button = self._button("启动档案", QStyle.SP_MediaPlay)
        self.stop_button = self._button("停止档案", QStyle.SP_MediaStop)
        tools_layout.addWidget(self.check_login_button, 1, 0)
        tools_layout.addWidget(self.read_profile_button, 1, 1)
        tools_layout.addWidget(self.read_timeline_button, 2, 0)
        tools_layout.addWidget(self.scan_selected_button, 2, 1)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键词后执行只读搜索")
        self.search_input.setClearButtonEnabled(True)
        self.search_button = self._button("搜索", QStyle.SP_FileDialogContentsView)
        tools_layout.addWidget(self.search_input, 3, 0)
        tools_layout.addWidget(self.search_button, 3, 1)
        # These two legacy actions remain available through account cards and
        # selection handling, but are not allowed to crowd the compact toolbar.
        self.start_button.setVisible(False)
        self.stop_button.setVisible(False)
        right_layout.addWidget(tools_panel)

        details_tabs = QTabWidget(objectName="detailsTabs")
        self.activity_table = QTableWidget(0, 5)
        self.activity_table.setHorizontalHeaderLabels(("时间", "任务", "状态", "耗时", "摘要"))
        self.activity_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.activity_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.activity_table.verticalHeader().setVisible(False)
        self.activity_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.activity_table.horizontalHeader().setStretchLastSection(True)
        details_tabs.addTab(self.activity_table, "最近活动")

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(500)
        self.log_output.setPlaceholderText("操作日志将在这里显示")
        details_tabs.addTab(self.log_output, "操作日志")
        details_tabs.setMinimumHeight(360)
        right_layout.addWidget(details_tabs, 1)

        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([700, 460])
        content_layout.addWidget(main_splitter, 1)

        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("正在检查连接…")

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
        self.run_all_button.clicked.connect(self.start_all)
        self.stop_all_button.clicked.connect(self.stop_all)
        self.automation_button.clicked.connect(self.configure_and_run_automation)
        self.reauth_button.clicked.connect(self.reauthenticate_agent)
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

    def _refresh_agent_status_legacy(self) -> None:
        status = self.controller.server_agent_status()
        self.server_state_label.setText(f"Server: {status.get('server', 'OFFLINE')}")
        lifecycle = status.get('lifecycle')
        agent_text = f"Agent: {status.get('agent', 'OFFLINE')}"
        if lifecycle:
            agent_text += f" · {lifecycle}"
        self.agent_state_label.setText(agent_text)
        online = status.get('agent') == 'ONLINE'
        self.live_status_label.setText("●  Agent is running · connected" if online else "●  Agent is reconnecting")
        self.live_status_label.setObjectName("liveStatusOnline" if online else "liveStatus")
        self.live_status_label.style().unpolish(self.live_status_label)
        self.live_status_label.style().polish(self.live_status_label)
        heartbeat = str(status.get("last_heartbeat") or "-").replace("T", " ")[:19]
        self.heartbeat_label.setText(f"Last Heartbeat: {heartbeat}")

    def _refresh_agent_status(self) -> None:
        status = self.controller.server_agent_status()
        server = status.get("server", "OFFLINE")
        agent = status.get("agent", "OFFLINE")
        lifecycle = status.get("lifecycle")
        server_zh = {"ONLINE": "在线", "OFFLINE": "离线"}.get(server, server)
        agent_zh = {"ONLINE": "在线", "OFFLINE": "离线", "UNCONFIGURED": "未配置", "REAUTH_REQUIRED": "需要重新认证"}.get(agent, agent)
        lifecycle_zh = {"RUNNING": "运行中", "STOPPED": "已停止", "STARTING": "启动中", "STOPPING": "停止中"}.get(lifecycle, lifecycle)
        self.server_state_label.setText(f"服务器：{server_zh}")
        self.agent_state_label.setText(f"运行端：{agent_zh}" + (f" · {lifecycle_zh}" if lifecycle else ""))
        online = server == "ONLINE" and agent == "ONLINE"
        # Keep the console open for status and authentication, but disable all
        # remote control actions until the authenticated Agent is online.
        for button in (
            getattr(self, "start_button", None), getattr(self, "stop_button", None),
            getattr(self, "run_all_button", None), getattr(self, "stop_all_button", None),
            getattr(self, "automation_button", None), getattr(self, "check_login_button", None),
            getattr(self, "read_profile_button", None), getattr(self, "read_timeline_button", None),
            getattr(self, "search_button", None),
        ):
            if button is not None:
                button.setEnabled(online)
        self.reauth_button.setVisible(agent in {"REAUTH_REQUIRED", "UNCONFIGURED", "UNREGISTERED"})
        if agent == "REAUTH_REQUIRED":
            live_text = "●  服务器可达，运行端凭据需要重新认证"
        elif online:
            live_text = "●  运行端工作正常，已连接服务器"
        else:
            live_text = "●  运行端正在连接服务器"
        self.live_status_label.setText(live_text)
        self.live_status_label.setObjectName("liveStatusOnline" if online else "liveStatus")
        self.live_status_label.style().unpolish(self.live_status_label)
        self.live_status_label.style().polish(self.live_status_label)
        heartbeat = str(status.get("last_heartbeat") or "—").replace("T", " ")[:19]
        self.heartbeat_label.setText(f"最近心跳：{heartbeat}")

    def reauthenticate_agent(self) -> None:
        dialog = AgentReauthDialog(self.controller.current_agent_id(), self)
        if dialog.exec() != QDialog.Accepted:
            return
        agent_id, agent_token = dialog.credentials()
        if not agent_id or not agent_token:
            QMessageBox.warning(self, "信息不完整", "请填写 Agent ID 和新生成的 Agent Token。")
            return

        def finished(status: dict[str, str]) -> None:
            dialog.agent_token_input.clear()
            self._refresh_agent_status()
            QMessageBox.information(self, "认证成功", "运行端已连接 Web 后台服务器。")

        self._run_job(
            "重新认证运行端",
            lambda: self.controller.replace_agent_credentials(agent_id, agent_token),
            finished,
        )

    def closeEvent(self, event) -> None:
        self._closing = True
        self._agent_status_timer.stop()
        for worker in tuple(self._workers):
            for signal in (
                worker.signals.finished,
                worker.signals.error,
                worker.signals.done,
            ):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
        self._workers.clear()
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
            self._update_selected_runtime_summary()
            return
        account_item = self.table.item(rows[0].row(), 5)
        account_id = account_item.text().strip() if account_item else ""
        account_summary = self._statistics.get("by_account", {}).get(account_id)
        if account_summary:
            for key, label in self.stat_labels.items():
                label.setText(str(account_summary.get(key, 0)))
        self._update_selected_runtime_summary()

    def _update_selected_runtime_summary(self) -> None:
        profile_ids = self.selected_profile_ids()
        if len(profile_ids) != 1:
            self.selected_profile_label.setText("尚未选择档案")
            self.selected_runtime_label.setText("运行状态：—")
            return
        profile_id = profile_ids[0]
        profile = next(
            (
                item
                for item in self._profiles
                if str(item.get("profileId") or item.get("profile_id") or "") == profile_id
            ),
            {},
        )
        profile_name = str(profile.get("profileName") or profile.get("profile_name") or profile_id)
        running = bool(profile.get("running"))
        self.selected_profile_label.setText(
            f"{profile_name}  ·  {'运行中' if running else '已停止或状态未知'}"
        )
        self.selected_runtime_label.setText(f"运行状态：{'运行中' if running else '未运行'}")

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
        task_names = {
            "x.check_login": "登录检查",
            "x.read_profile": "读取档案",
            "x.read_timeline": "读取时间线",
            "x.search": "关键词搜索",
        }
        status_names = {
            "PENDING": "等待中",
            "RUNNING": "执行中",
            "SUCCESS": "成功",
            "FAILED": "失败",
            "TIMEOUT": "超时",
            "CANCELLED": "已取消",
        }
        self.activity_table.setRowCount(len(activities))
        for row, activity in enumerate(activities):
            timestamp = str(activity.get("timestamp") or "")
            activity_type = str(activity.get("activity_type") or "")
            status = str(activity.get("status") or "")
            values = (
                timestamp.replace("T", " ")[:19],
                task_names.get(activity_type, activity_type),
                status_names.get(status, status),
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
            self.table.setCellWidget(row_index, 0, self._account_card(record))
            for column, value in enumerate(values):
                if column == 0:
                    continue
                item = QTableWidgetItem(value)
                if column in (2, 3, 6):
                    item.setForeground(self._status_color(value))
                self.table.setItem(row_index, column, item)
        self.summary_label.setText(f"{len(records)} 个账号")

    def _account_card(self, record: AccountRow) -> QWidget:
        card = QWidget()
        card.setObjectName("accountCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        outer = QHBoxLayout(card)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(8)
        dot = QLabel("●")
        dot.setObjectName("onlineDot" if record.browser_status == "RUNNING" else "offlineDot")
        outer.addWidget(dot)
        info = QVBoxLayout()
        info.setSpacing(0)
        title = QLabel(record.profile_name or record.profile_id)
        title.setObjectName("accountName")
        handle = QLabel(record.x_username or record.profile_id)
        handle.setObjectName("accountHandle")
        info.addWidget(title)
        info.addWidget(handle)
        outer.addLayout(info, 1)
        state = QLabel({"RUNNING": "运行中", "STOPPED": "已停止"}.get(record.browser_status, record.browser_status))
        state.setObjectName("accountState")
        outer.addWidget(state)
        start = QPushButton("运行")
        start.setObjectName("miniRunButton")
        stop = QPushButton("停止")
        stop.setObjectName("miniStopButton")
        start.clicked.connect(lambda: self._run_profile_action("启动", self.controller.start_profile, [record.profile_id]))
        stop.clicked.connect(lambda: self._run_profile_action("停止", self.controller.stop_profile, [record.profile_id]))
        outer.addWidget(start)
        outer.addWidget(stop)
        return card

    @staticmethod
    def _status_color(status: str) -> QColor:
        if status in {"RUNNING", "LOGGED_IN", "VALID", "执行中", "成功", "运行中"}:
            return QColor("#137333")
        if status in {"ERROR", "DUPLICATE_ACCOUNT", "FAILED", "失败"}:
            return QColor("#b42318")
        if status in {"STOPPED", "NOT_LOGGED_IN", "TIMEOUT", "已停止", "超时"}:
            return QColor("#b54708")
        return QColor("#667085")

    def refresh_profiles(self) -> None:
        self._run_job("刷新浏览器档案", self.controller.refresh_profiles, self._profiles_finished)

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

    def start_all(self) -> None:
        self._run_profile_action("启动全部", self.controller.start_profile, [row.profile_id for row in self.controller.list_accounts()])

    def stop_all(self) -> None:
        self._run_profile_action("停止全部", self.controller.stop_profile, [row.profile_id for row in self.controller.list_accounts()])

    def run_check_login(self) -> None:
        self._run_read_only_task("x.check_login", "登录检查")

    def run_read_profile(self) -> None:
        self._run_read_only_task("x.read_profile", "读取档案")

    def run_read_timeline(self) -> None:
        self._run_read_only_task("x.read_timeline", "读取时间线")

    def run_x_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            QMessageBox.information(self, "请输入关键词", "请先输入要搜索的关键词。")
            return
        self._run_read_only_task("x.search", "关键词搜索", {"query": query})

    def configure_and_run_automation(self) -> None:
        profile_ids = self._require_selection(single=True)
        if not profile_ids:
            return
        profile_id = profile_ids[0]
        try:
            current = self.controller.get_profile_task_config(profile_id)
        except Exception as exc:
            self._show_error(f"读取档案配置失败：{exc}")
            return
        dialog = TaskConfigDialog(current, self)
        if dialog.exec() != QDialog.Accepted:
            return
        config = dialog.config()
        self._run_job(
            f"配置并运行自动化：档案 {profile_id}",
            lambda: self.controller.start_automation_task(profile_id, config),
            lambda result: self._automation_finished(profile_id, result),
        )

    def _automation_finished(self, profile_id: str, result: Any) -> None:
        status = str(result.get("status") or "UNKNOWN") if isinstance(result, dict) else "UNKNOWN"
        self.statusBar().showMessage(f"档案 {profile_id} 自动化检查完成：{status}")
        self._log(f"{status}  档案 {profile_id} 自动化检查（只读）")
        self._load_local_statistics()

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
            f"{label}：档案 {profile_id}",
            lambda: self.controller.run_read_only_task(profile_id, task_type, params),
            lambda result: self._read_only_task_finished(label, profile_id, result),
        )

    def _read_only_task_finished(self, label: str, profile_id: str, result: Any) -> None:
        status = str(result.get("status") or "UNKNOWN") if isinstance(result, dict) else "UNKNOWN"
        status_text = {
            "SUCCESS": "成功",
            "FAILED": "失败",
            "TIMEOUT": "超时",
            "RUNNING": "执行中",
            "UNKNOWN": "未知",
        }.get(status, status)
        self.statusBar().showMessage(f"{label}完成：{status_text}")
        self._log(f"{status_text}  {label} 档案 {profile_id}")
        self._load_local_statistics()

    def _run_profile_action(
        self, action: str, function: Callable[[str], dict[str, Any]], profile_ids: list[str] | None = None
    ) -> None:
        selected = profile_ids or self._require_selection(single=True)
        if not selected:
            return
        if len(selected) > 1:
            self._run_job(
                f"{action} {len(selected)} 个档案",
                lambda: [function(profile_id) for profile_id in selected],
                lambda _result: (self.statusBar().showMessage(f"{action}完成"), self.refresh_profiles()),
            )
            return
        profile_id = selected[0]
        self._run_job(
            f"{action}档案 {profile_id}",
            lambda: function(profile_id),
            lambda result: self._profile_action_finished(action, profile_id, result),
        )

    def _require_selection(self, *, single: bool = False) -> list[str]:
        profile_ids = self.selected_profile_ids()
        if not profile_ids:
            QMessageBox.information(self, "请选择档案", "请先在账号列表中选择一个浏览器档案。")
            return []
        if single and len(profile_ids) > 1:
            QMessageBox.information(self, "请选择一个档案", "启动或停止时只能选择一个浏览器档案。")
            return []
        return profile_ids

    def _run_job(
        self,
        label: str,
        function: Callable[[], Any],
        on_finished: Callable[[Any], None],
    ) -> None:
        if self._closing:
            return
        self._active_jobs += 1
        self._update_busy_state()
        self._log(f"RUNNING  {label}")
        worker = FunctionWorker(function)
        self._workers.add(worker)
        worker.signals.finished.connect(on_finished)
        worker.signals.error.connect(lambda message: self._job_failed(label, message))
        worker.signals.done.connect(lambda: self._job_done(worker))
        self.thread_pool.start(worker)

    def _job_done(self, worker: FunctionWorker) -> None:
        self._workers.discard(worker)
        if self._closing:
            return
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
            self.automation_button,
            self.reauth_button,
        ):
            button.setEnabled(not busy)
        if busy:
            self.statusBar().showMessage("任务执行中...")

    def _health_finished(self, result: Any) -> None:
        self.statusBar().showMessage("Laogu Browser API 已连接")
        self._log("成功  Laogu Browser API 已连接")
        self.refresh_profiles()

    def _profiles_finished(self, profiles: list[dict[str, Any]]) -> None:
        self._profiles = profiles
        self._update_selected_runtime_summary()
        self.statusBar().showMessage(f"已发现 {len(profiles)} 个浏览器档案")
        self._log(f"成功  刷新浏览器档案，共 {len(profiles)} 个")

    def _accounts_finished(self, records: list[AccountRow]) -> None:
        self.set_accounts(records)
        self.statusBar().showMessage(f"账号扫描完成，共 {len(records)} 条资产记录")
        self._log(f"成功  账号扫描完成，共 {len(records)} 条")
        self._load_local_statistics()

    def _profile_action_finished(
        self, action: str, profile_id: str, result: dict[str, Any]
    ) -> None:
        self.statusBar().showMessage(f"档案 {profile_id} {action}请求完成")
        self._log(f"成功  {action}档案 {profile_id}")
        self.refresh_profiles()

    def _job_failed(self, label: str, message: str) -> None:
        self.statusBar().showMessage(f"{label}失败")
        self._log(f"失败  {label}：{message}")
        self._show_error(f"{label}失败\n\n{message}")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "操作失败", message)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{timestamp}] {message}")
