from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import os
import socket
import sys
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QThread, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .controller import AccountRow, DesktopController
from .branding import application_icon
from .iconography import line_icon
from .workers import FunctionWorker
from .styles import APP_STYLE


class EmittingStream(QObject):
    text_written = Signal(str)

    def write(self, text: str) -> None:
        if text:
            self.text_written.emit(str(text))

    def flush(self) -> None:
        pass


def probe_cdp_alive(cdp_url: str, timeout: float = 0.2) -> bool:
    """精准探测物理 Socket 端口，连不上绝对返回 False"""
    if not cdp_url or not isinstance(cdp_url, str):
        return False
    try:
        clean = cdp_url.replace("http://", "").replace("https://", "").replace("ws://", "").split("/")[0]
        if ":" in clean:
            host, port = clean.split(":")
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        elif clean.isdigit():
            with socket.create_connection(("127.0.0.1", int(clean)), timeout=timeout):
                return True
    except Exception:
        pass
    return False


class TaskConfigDialog(QDialog):
    """配置单账号或多账号自动化任务的参数对话框 - 已融入批次间隔时间控制"""

    def __init__(self, initial: dict[str, Any] | None = None, parent: QWidget | None = None, engines: list[dict[str, Any]] | None = None):
        super().__init__(parent)
        self.setWindowTitle("配置自动化任务")
        self.setModal(True)
        self.setMinimumWidth(500)
        initial = initial or {}
        active = initial.get("active") if isinstance(initial.get("active"), dict) else initial

        form = QFormLayout(self)
        form.setContentsMargins(24, 20, 24, 16)
        form.setVerticalSpacing(12)

        self.engine_input = QComboBox()
        self.engine_input.setMinimumHeight(32)
        selected_engine = str(active.get("engine_id") or "default")
        self.set_engines(engines, selected_engine)
        form.addRow("自动化方案", self.engine_input)

        raw_kw = str(active.get("keyword") or active.get("keywords") or "")
        self.keyword_input = QLineEdit(raw_kw)
        self.keyword_input.setMaxLength(500)
        self.keyword_input.setMinimumHeight(32)
        self.keyword_input.setPlaceholderText("例如：(#やっぱり乃木坂だな) lang:ja 或高级检索表达式")
        form.addRow("检索关键词", self.keyword_input)

        self.daily_limit_input = self._spin(active.get("daily_task_limit"), 50, 1, 10_000)
        form.addRow("单日任务上限", self.daily_limit_input)

        self.batch_interval_input = self._spin(active.get("batch_interval_minutes"), 15, 1, 1440)
        self.batch_interval_input.setSuffix(" 分钟")
        form.addRow("批次间隔时间", self.batch_interval_input)

        self.follower_limit_input = self._spin(active.get("max_follower_threshold"), 150, 0, 100_000_000)
        form.addRow("粉丝指标门槛", self.follower_limit_input)

        self.engagement_limit_input = self._spin(active.get("max_engagement_threshold"), 10_000, 0, 100_000_000)
        form.addRow("互动/帖子门槛", self.engagement_limit_input)

        hint = QLabel("自动化引擎将在后台独立运行筛选，不会进行未经许可的违规操作。")
        hint.setWordWrap(True)
        hint.setObjectName("subtitle")
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def set_engines(self, engines: list[dict[str, Any]] | None, selected_engine: str | None = None) -> None:
        current = self.engine_input.currentData() or {}
        target_id = str(selected_engine or current.get("engine_id") or "default")
        choices = engines or [{"engine_id": "default", "name": "默认自动化引擎", "description": "内置 x_automation_engine.py"}]
        if target_id and not any(str(item.get("engine_id") or "") == target_id for item in choices):
            choices = [{"engine_id": target_id, "name": str(current.get("engine_name") or target_id)}] + list(choices)
        self.engine_input.blockSignals(True)
        self.engine_input.clear()
        for engine in choices:
            engine_id = str(engine.get("engine_id") or "default")
            name = str(engine.get("name") or engine_id)
            version = str(engine.get("version") or "")
            label = f"{name}（{version}）" if version else name
            self.engine_input.addItem(label, {"engine_id": engine_id, "engine_name": name, "engine_version": version})
        for index in range(self.engine_input.count()):
            if str(self.engine_input.itemData(index).get("engine_id")) == target_id:
                self.engine_input.setCurrentIndex(index)
                break
        self.engine_input.blockSignals(False)

    def apply_initial(self, initial: dict[str, Any] | None) -> None:
        initial = initial or {}
        active = initial.get("active") if isinstance(initial.get("active"), dict) else initial
        raw_kw = str(active.get("keyword") or active.get("keywords") or "")
        self.keyword_input.setText(raw_kw)
        for widget, key, default in (
            (self.daily_limit_input, "daily_task_limit", 50),
            (self.batch_interval_input, "batch_interval_minutes", 15),
            (self.follower_limit_input, "max_follower_threshold", 150),
            (self.engagement_limit_input, "max_engagement_threshold", 10_000),
        ):
            try:
                widget.setValue(default if active.get(key) is None else int(active.get(key)))
            except (TypeError, ValueError):
                widget.setValue(default)
        self.set_engines(None, str(active.get("engine_id") or "default"))

    @staticmethod
    def _spin(value: Any, default: int, minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setMinimumHeight(32)
        try:
            widget.setValue(default if value is None else int(value))
        except (TypeError, ValueError):
            widget.setValue(default)
        return widget

    def config(self) -> dict[str, Any]:
        engine = self.engine_input.currentData() or {"engine_id": "default", "engine_name": "默认自动化引擎"}
        raw_kw = self.keyword_input.text().strip()[:500]

        return {
            "engine_id": str(engine.get("engine_id") or "default"),
            "engine_name": str(engine.get("engine_name") or "默认自动化引擎"),
            "keyword": raw_kw,
            "daily_task_limit": self.daily_limit_input.value(),
            "batch_interval_minutes": self.batch_interval_input.value(),
            "max_follower_threshold": self.follower_limit_input.value(),
            "max_engagement_threshold": self.engagement_limit_input.value(),
            "sleep_on_rate_limit": True,
        }


class AgentReauthDialog(QDialog):
    """重新认证 Agent Token 对话框"""

    def __init__(self, agent_id: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("🔑 重新认证运行端")
        self.setModal(True)
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        form.setContentsMargins(22, 20, 22, 16)

        self.agent_id_input = QLineEdit(str(agent_id).strip())
        self.agent_id_input.setReadOnly(True)
        self.agent_id_input.setMinimumHeight(32)
        self.agent_id_input.setToolTip("Agent ID 由服务器签发，不能在控制中心修改")
        self.agent_id_input.setPlaceholderText("从 Web 后台复制 Agent ID")
        form.addRow("Agent ID", self.agent_id_input)

        self.agent_token_input = QLineEdit()
        self.agent_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.agent_token_input.setMinimumHeight(32)
        self.agent_token_input.setPlaceholderText("粘贴新生成的 Agent Token")
        form.addRow("Agent Token", self.agent_token_input)

        hint = QLabel("凭据保存后将通过加密传输，验证通过后系统自动恢复联机。")
        hint.setWordWrap(True)
        hint.setObjectName("subtitle")
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_button:
            save_button.setText("保存并验证")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def credentials(self) -> tuple[str, str]:
        return self.agent_id_input.text().strip(), self.agent_token_input.text().strip()


class AccountCardWidget(QFrame):
    """双行卡片组件：严谨校验底层真正在线的浏览器进程"""

    def __init__(
        self,
        record: AccountRow,
        stats: dict[str, Any] | None,
        on_select: Callable[[str], None],
        on_run: Callable[[str], None],
        on_stop: Callable[[str], None],
        on_config: Callable[[str], None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.profile_id = record.profile_id
        self.profile_name = record.profile_name or record.profile_id
        self._on_select = on_select
        self.setObjectName("accountCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        running = bool(record.runtime_running and record.runtime_debug_ready)

        dot = QLabel("●")
        dot.setObjectName("onlineDot" if running else "offlineDot")
        top_row.addWidget(dot)

        info = QVBoxLayout()
        info.setSpacing(1)
        name = QLabel(self.profile_name)
        name.setObjectName("accountName")
        
        handle_str = record.x_username if record.x_username and record.x_username != "-" else "未绑定 X 账号"
        login_labels = {
            "LOGGED_IN": "已登录",
            "VALID": "已登录",
            "NOT_LOGGED_IN": "未登录",
            "UNKNOWN": "未确认",
        }
        login_str = login_labels.get(str(record.login_status or "").upper(), "未确认")
        account_id = f" · ID {record.x_account_id}" if record.x_account_id else ""
        sub_info = QLabel(f"{handle_str} · {login_str}{account_id}")
        sub_info.setObjectName("accountHandle")
        sub_info.setToolTip(f"Profile ID: {record.profile_id}")
        
        info.addWidget(name)
        info.addWidget(sub_info)
        top_row.addLayout(info, 1)

        state_text = "运行中" if running else "已停止"
        state = QLabel(state_text)
        state.setObjectName("tagRunning" if running else "tagStopped")
        top_row.addWidget(state)

        for text, object_name, callback, icon_name, icon_color in (
            ("运行", "miniRunButton", on_run, "play", "#059669"),
            ("停止", "miniStopButton", on_stop, "stop", "#DC2626"),
            ("配置", "miniConfigButton", on_config, "settings", "#475569"),
        ):
            button = QPushButton(text)
            button.setObjectName(object_name)
            button.setIcon(line_icon(icon_name, icon_color, 15))
            button.setMinimumHeight(28)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, cb=callback, pid=self.profile_id: cb(pid))
            top_row.addWidget(button)

        main_layout.addLayout(top_row)

        account_stats = stats or {}
        own_followers = record.followers_count
        if own_followers is None:
            own_followers = account_stats.get("own_followers", account_stats.get("followers", "尚未读取"))
        own_following = record.following_count
        if own_following is None:
            own_following = account_stats.get("own_following", account_stats.get("following", "尚未读取"))
        likes = account_stats.get("likes", 0)
        follows = account_stats.get("follows", 0)
        comments = account_stats.get("comments", 0)
        scanned_posts = account_stats.get("scanned_posts", 0)

        bottom_row = QHBoxLayout()
        stats_label = QLabel(
            f"数据：粉丝 {own_followers} · 关注 {own_following}  |  今日：赞 {likes} · 关 {follows} · 评 {comments} · 扫 {scanned_posts}"
        )
        stats_label.setObjectName("accountHandle")
        stats_label.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
        bottom_row.addWidget(stats_label)
        bottom_row.addStretch(1)

        main_layout.addLayout(bottom_row)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._on_select(self.profile_id)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setObjectName("accountCard_selected" if selected else "accountCard")
        self.style().unpolish(self)
        self.style().polish(self)


class MiniLogWindow(QWidget):
    """Always-on-top status and log surface shown while the main window is minimized."""

    restore_requested = Signal()
    stop_all_requested = Signal()
    exit_requested = Signal()

    def __init__(self):
        super().__init__(None)
        self.setObjectName("miniLogWindow")
        self.setWindowTitle("老谷控制中心 - 日志浮窗")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowOpacity(1.0)
        self.setWindowIcon(application_icon())
        self.setFixedSize(470, 420)
        self._drag_offset: QPoint | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(9)
        brand_mark = QLabel(objectName="miniBrandMark")
        brand_mark.setPixmap(application_icon().pixmap(34, 34))
        header.addWidget(brand_mark)
        title_block = QVBoxLayout()
        title_block.setSpacing(1)
        title_block.addWidget(QLabel("老谷控制中心", objectName="miniTitle"))
        title_block.addWidget(QLabel("任务运行监控", objectName="miniSubtitle"))
        header.addLayout(title_block)
        header.addStretch(1)
        self.connection_label = QLabel("● 正在连接", objectName="miniStatus")
        header.addWidget(self.connection_label)
        self.exit_button = QPushButton("×")
        self.exit_button.setObjectName("miniWindowCloseButton")
        self.exit_button.setToolTip("关闭控制中心并退出内置 Agent")
        self.exit_button.clicked.connect(self.exit_requested.emit)
        header.addWidget(self.exit_button)
        layout.addLayout(header)

        self.metrics_label = QLabel("运行 0  ·  成功 0  ·  失败 0", objectName="miniMetrics")
        layout.addWidget(self.metrics_label)

        log_header = QHBoxLayout()
        log_header.setContentsMargins(1, 0, 1, 0)
        log_header.addWidget(QLabel("实时日志", objectName="miniSectionTitle"))
        log_header.addStretch(1)
        log_header.addWidget(QLabel("自动跟随最新", objectName="miniAutoFollow"))
        layout.addLayout(log_header)

        self.log_output = QPlainTextEdit(objectName="miniLogOutput")
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(80)
        self.log_output.setPlaceholderText("等待控制中心日志…")
        self.log_output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.log_output.textChanged.connect(self._scroll_to_latest)
        layout.addWidget(self.log_output, 1)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.pause_button = QPushButton("暂停日志")
        self.pause_button.setObjectName("miniSecondaryButton")
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip("暂停或继续浮窗日志滚动")
        self.pause_button.toggled.connect(self._set_paused)
        actions.addWidget(self.pause_button, 1)

        self.stop_button = QPushButton("停止全部")
        self.stop_button.setObjectName("miniDangerButton")
        self.stop_button.setToolTip("停止全部浏览器档案，操作前会再次确认")
        self.stop_button.clicked.connect(self.stop_all_requested.emit)
        actions.addWidget(self.stop_button, 1)

        self.restore_button = QPushButton("打开控制中心")
        self.restore_button.setObjectName("miniPrimaryButton")
        self.restore_button.setToolTip("恢复完整控制中心")
        self.restore_button.clicked.connect(self.restore_requested.emit)
        layout.addWidget(self.restore_button)
        layout.addLayout(actions)

    def _set_paused(self, paused: bool) -> None:
        self.pause_button.setText("继续日志" if paused else "暂停日志")

    def _scroll_to_latest(self) -> None:
        if not self.pause_button.isChecked():
            scrollbar = self.log_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def set_connection(self, text: str, online: bool) -> None:
        self.connection_label.setText(text)
        self.connection_label.setObjectName("miniStatusOnline" if online else "miniStatus")
        self.connection_label.style().unpolish(self.connection_label)
        self.connection_label.style().polish(self.connection_label)

    def set_metrics(self, running: int, success: int, failed: int) -> None:
        self.metrics_label.setText(f"运行 {running}  ·  成功 {success}  ·  失败 {failed}")

    def append_log(self, line: str) -> None:
        if self.pause_button.isChecked():
            return
        clean = str(line).strip()
        if clean:
            self.log_output.appendPlainText(clean)
            self._scroll_to_latest()

    def seed_logs(self, lines: list[str]) -> None:
        if self.pause_button.isChecked():
            return
        self.log_output.setPlainText("\n".join(str(line).strip() for line in lines if str(line).strip()))
        self._scroll_to_latest()

    def show_at_bottom_right(self, available_geometry: Any) -> None:
        margin = 18
        self.move(
            available_geometry.right() - self.width() - margin + 1,
            available_geometry.bottom() - self.height() - margin + 1,
        )
        self.show()
        self.raise_()
        self.activateWindow()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.restore_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    engine_progress_signal = Signal(int, str)

    HEADERS = ("账号卡片", "档案 ID", "浏览器", "登录", "用户名", "账号 ID", "状态", "最后检查")

    def __init__(self, controller: DesktopController | None = None):
        super().__init__()
        self.setStyleSheet(APP_STYLE)
        self.setWindowIcon(application_icon())

        self.controller = controller or DesktopController()
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(8)
        self._workers: set[FunctionWorker] = set()
        self._engine_progress_dialog: QProgressDialog | None = None
        self._engine_restart_required = False
        self.engine_progress_signal.connect(self._engine_progress)
        self._closing = False
        self._active_jobs = 0
        self._statistics: dict[str, Any] = {"by_account": {}}
        self._profiles: list[dict[str, Any]] = []
        self._account_rows_by_id: dict[str, AccountRow] = {}
        self._remote_online = False
        self._needs_reauth = False
        self._capabilities: set[str] = {"local.view", "local.browser.stop"}
        self._last_snapshot_mtime: float = 0.0
        self._background_workers: set[FunctionWorker] = set()
        self._status_refresh_in_flight = False
        self._dashboard_refresh_in_flight = False
        self._config_load_in_flight: set[str] = set()
        self._config_dialogs: dict[str, TaskConfigDialog] = {}
        self._automation_engines_cache: list[dict[str, Any]] | None = None
        self._account_view_signature: tuple[Any, ...] | None = None
        self._pending_log_lines: deque[str] = deque(maxlen=200)
        self._recent_log_lines: deque[str] = deque(maxlen=20)
        self._log_flush_scheduled = False
        settings = getattr(self.controller, "settings", None)
        self._log_tail_path = os.fspath(getattr(settings, "log_file", ""))
        self._log_tail_offset = 0
        self._log_tail_partial = ""
        self._log_tail_timer = QTimer(self)
        self._log_tail_timer.setInterval(700)
        self._log_tail_timer.timeout.connect(self._poll_log_file)
        self._mini_window = MiniLogWindow()
        self._mini_window.restore_requested.connect(self._restore_from_mini_window)
        self._mini_window.stop_all_requested.connect(self._confirm_stop_all_from_mini)
        self._mini_window.exit_requested.connect(self.close)

        self._build_ui()
        self._setup_stdout_redirect()
        self._wire_events()
        self._load_registry()
        self._load_local_statistics()
        try:
            self._apply_agent_status(self.controller.server_agent_status())
        except Exception:
            self._apply_agent_status({})

        self._agent_status_timer = QTimer(self)
        self._agent_status_timer.timeout.connect(self._refresh_agent_status)
        self._agent_status_timer.start(5000)

        self._statistics_timer = QTimer(self)
        self._statistics_timer.timeout.connect(self._refresh_local_statistics)
        self._statistics_timer.start(10000)

        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.setInterval(3000)
        self.auto_refresh_timer.timeout.connect(self._auto_refresh_profile_snapshots)
        self.auto_refresh_timer.start()

        QTimer.singleShot(100, lambda: self._run_job("检查 API 连接", self.controller.health, self._health_finished))

    def _apply_drop_shadow(self, widget: QWidget) -> None:
        """为面板组件注入柔和悬浮阴影"""
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 12))
        shadow.setOffset(0, 4)
        widget.setGraphicsEffect(shadow)

    def _auto_refresh_profile_snapshots(self) -> None:
        try:
            snapshot_path = os.path.join(os.getcwd(), "agent_data", "profile_snapshots.json")
            if not os.path.exists(snapshot_path):
                return
            mtime = os.path.getmtime(snapshot_path)
            if self._last_snapshot_mtime == mtime:
                return
            self._last_snapshot_mtime = mtime

            if self._dashboard_refresh_in_flight:
                return
            self._dashboard_refresh_in_flight = True
            self._run_background(
                self.controller.list_accounts,
                self._background_accounts_finished,
                lambda: setattr(self, "_dashboard_refresh_in_flight", False),
            )
        except Exception:
            pass

    def _setup_stdout_redirect(self) -> None:
        self.stdout_stream = EmittingStream()
        self.stdout_stream.text_written.connect(self._append_raw_log)
        sys.stdout = self.stdout_stream
        sys.stderr = self.stdout_stream

    def _append_raw_log(self, text: str) -> None:
        text = text.rstrip("\r\n")
        if text:
            self._queue_log(text)

    def _queue_log(self, text: str) -> None:
        line = str(text)
        self._pending_log_lines.append(line)
        self._recent_log_lines.append(line)
        self._mini_window.append_log(line)
        if self._log_flush_scheduled:
            return
        self._log_flush_scheduled = True
        QTimer.singleShot(80, self._flush_log_buffer)

    def _read_log_file_tail(self, *, seed: bool = False) -> None:
        """Incrementally mirror agent.log into the compact log window."""
        path = self._log_tail_path
        if not path:
            return
        try:
            size = os.path.getsize(path)
            if size < self._log_tail_offset:
                self._log_tail_offset = 0
                self._log_tail_partial = ""
            if seed:
                with open(path, "rb") as handle:
                    start = max(0, size - 16_384)
                    handle.seek(start)
                    raw = handle.read()
                text = raw.decode("utf-8", errors="replace")
                if start:
                    text = text.partition("\n")[2]
                self._log_tail_offset = size
                self._log_tail_partial = ""
                lines = [line.strip() for line in text.splitlines() if line.strip()][-8:]
                if lines:
                    recent = list(self._recent_log_lines)
                    merged = lines + [line for line in recent if line not in lines]
                    self._mini_window.seed_logs(merged[-8:])
                    self._recent_log_lines.clear()
                    self._recent_log_lines.extend(merged[-20:])
                return
            if size == self._log_tail_offset:
                return
            with open(path, "rb") as handle:
                handle.seek(self._log_tail_offset)
                raw = handle.read()
            self._log_tail_offset = size
            text = self._log_tail_partial + raw.decode("utf-8", errors="replace")
            chunks = text.split("\n")
            self._log_tail_partial = chunks.pop() if chunks else ""
            for line in chunks:
                clean = line.strip()
                if clean:
                    self._recent_log_lines.append(clean)
                    self._mini_window.append_log(clean)
        except (OSError, UnicodeError):
            return

    def _poll_log_file(self) -> None:
        if self._closing or not self._mini_window.isVisible():
            return
        self._read_log_file_tail()

    def _flush_log_buffer(self) -> None:
        self._log_flush_scheduled = False
        if not self._pending_log_lines or self._closing:
            return
        lines = list(self._pending_log_lines)
        self._pending_log_lines.clear()
        self.log_output.appendPlainText("\n".join(lines))

    def _scroll_main_log_to_latest(self) -> None:
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _build_ui(self) -> None:
        self.setWindowTitle("老谷自动化控制中心 - 2026 SaaS 版")
        self.setMinimumSize(1160, 800)
        self.resize(1280, 880)

        root = QWidget()
        root.setObjectName("rootWidget")  # <--- 重要：限制灰色背景范围，解决白底灰色穿透阴影问题
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 14, 24, 14)
        header_layout.setSpacing(12)

        brand_mark = QLabel(objectName="headerBrandMark")
        brand_mark.setPixmap(application_icon().pixmap(42, 42))
        header_layout.addWidget(brand_mark)
        
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(QLabel("老谷自动化控制中心", objectName="title"))
        title_box.addWidget(QLabel("统一管理浏览器档案、账号状态与全自动并发引擎", objectName="subtitle"))
        header_layout.addLayout(title_box)
        header_layout.addStretch(1)

        status_box = QHBoxLayout()
        status_box.setSpacing(8)
        self.server_state_label = QLabel("服务器：离线", objectName="statusBadge")
        self.agent_state_label = QLabel("运行端：未配置", objectName="statusBadge")
        self.heartbeat_label = QLabel("最近心跳：—", objectName="statusBadge")
        
        status_box.addWidget(self.server_state_label)
        status_box.addWidget(self.agent_state_label)
        status_box.addWidget(self.heartbeat_label)

        self.reauth_button = QPushButton("重新认证")
        self.reauth_button.setObjectName("reauthButton")
        self.reauth_button.setMinimumHeight(34)
        self.reauth_button.setVisible(False)
        status_box.addWidget(self.reauth_button)

        header_layout.addLayout(status_box)
        root_layout.addWidget(header)

        self.live_status_label = QLabel("● 运行端正在连接服务器…", objectName="liveStatus")
        self.live_status_label.setContentsMargins(24, 8, 24, 8)
        root_layout.addWidget(self.live_status_label)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 16)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        left_panel = QWidget()
        left = QVBoxLayout(left_panel)
        left.setContentsMargins(0, 0, 8, 0)
        left.setSpacing(10)

        overview = QFrame(objectName="overviewPanel")
        self._apply_drop_shadow(overview)
        metrics = QGridLayout(overview)
        metrics.setContentsMargins(14, 12, 14, 12)
        metrics.setHorizontalSpacing(10)
        self.stat_labels: dict[str, QLabel] = {}
        for column, (key, text) in enumerate((("total_tasks", "今日任务"), ("success_tasks", "成功"), ("failed_tasks", "失败"), ("timeout_tasks", "超时"))):
            card = QFrame(objectName="metricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 8, 12, 8)
            card_layout.addWidget(QLabel(text, objectName="metricCaption"))
            value = QLabel("0", objectName="metricValue")
            card_layout.addWidget(value)
            metrics.addWidget(card, 0, column)
            self.stat_labels[key] = value
        left.addWidget(overview)

        account_heading = QHBoxLayout()
        account_heading.addWidget(QLabel("账号资产列表", objectName="sectionTitle"))
        account_heading.addStretch(1)
        self.summary_label = QLabel("0 个账号", objectName="summary")
        account_heading.addWidget(self.summary_label)
        left.addLayout(account_heading)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setObjectName("accountTable")
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)  # <--- 重要：关闭斑马条纹，解决灰色穿透
        self.table.setMinimumHeight(300)           # <--- 重要：强制加大最小高度，确保列表内容显示充分
        self.table.setMouseTracking(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(80)
        self.table.verticalScrollBar().setSingleStep(20)
        for column in range(1, len(self.HEADERS)):
            self.table.setColumnHidden(column, True)
        left.addWidget(self.table, 1)

        action_panel = QFrame(objectName="actionPanel")
        self._apply_drop_shadow(action_panel)
        action_layout = QVBoxLayout(action_panel)
        action_layout.setContentsMargins(12, 12, 12, 12)
        action_layout.setSpacing(8)
        
        self.automation_button = QPushButton("配置并运行自动化")
        self.automation_button.setObjectName("primaryButton")
        self.automation_button.setMinimumHeight(40)
        self.automation_button.setToolTip("为选中的档案设置参数并提交自动化任务")
        action_layout.addWidget(self.automation_button)

        self.engine_update_button = QPushButton("检查脚本更新")
        self.engine_update_button.setIcon(line_icon("update", "#475569"))
        self.engine_update_button.setMinimumHeight(32)
        self.engine_update_button.setToolTip("检查 Web 后台发布的自动化脚本；确认后下载并激活")
        self.engine_update_label = QLabel("自动化脚本：尚未检查", objectName="summary")
        update_row = QHBoxLayout()
        update_row.setSpacing(8)
        update_row.addWidget(self.engine_update_label, 1)
        update_row.addWidget(self.engine_update_button)
        action_layout.addLayout(update_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.run_all_button = self._button("运行全部", "play", "#FFFFFF")
        self.run_all_button.setObjectName("runAllButton")
        self.stop_all_button = self._button("停止全部", "stop", "#FFFFFF")
        self.stop_all_button.setObjectName("stopAllButton")
        self.refresh_button = self._button("刷新账号", "refresh")
        self.scan_all_button = self._button("扫描底层", "scan")
        
        for button in (self.run_all_button, self.stop_all_button, self.refresh_button, self.scan_all_button):
            button.setMinimumHeight(32)
            actions.addWidget(button, 1)
        action_layout.addLayout(actions)
        left.addWidget(action_panel)

        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right = QVBoxLayout(right_panel)
        right.setContentsMargins(8, 0, 0, 0)
        right.setSpacing(10)

        runtime = QFrame(objectName="runtimePanel")
        self._apply_drop_shadow(runtime)
        runtime_layout = QVBoxLayout(runtime)
        runtime_layout.setContentsMargins(16, 12, 16, 12)
        runtime_layout.setSpacing(4)
        runtime_layout.addWidget(QLabel("当前选中档案信息", objectName="sectionTitle"))
        self.selected_profile_label = QLabel("尚未选择档案", objectName="runtimeValue")
        self.selected_runtime_label = QLabel("运行状态：—", objectName="summary")
        runtime_layout.addWidget(self.selected_profile_label)
        runtime_layout.addWidget(self.selected_runtime_label)
        right.addWidget(runtime)

        tools = QFrame(objectName="toolsPanel")
        self._apply_drop_shadow(tools)
        tools_layout = QGridLayout(tools)
        tools_layout.setContentsMargins(12, 8, 12, 8)
        tools_layout.setSpacing(5)
        tools_layout.addWidget(QLabel("只读工具箱", objectName="sectionTitle"), 0, 0, 1, 2)
        
        self.check_login_button = self._button("登录检查", "check")
        self.read_profile_button = self._button("读取档案", "profile")
        self.read_timeline_button = self._button("读取时间线", "timeline")
        self.scan_selected_button = self._button("扫描选中", "scan")
        
        for btn in (self.check_login_button, self.read_profile_button, self.read_timeline_button, self.scan_selected_button):
            btn.setMinimumHeight(28)

        tools_layout.addWidget(self.check_login_button, 1, 0)
        tools_layout.addWidget(self.read_profile_button, 1, 1)
        tools_layout.addWidget(self.read_timeline_button, 2, 0)
        tools_layout.addWidget(self.scan_selected_button, 2, 1)

        self.search_input = QLineEdit()
        self.search_input.setMinimumHeight(30)
        self.search_input.setPlaceholderText("输入关键词只读搜索")
        self.search_input.setClearButtonEnabled(True)
        self.search_button = self._button("搜索", "search")
        self.search_button.setMinimumHeight(30)
        self.search_button.setToolTip("执行只读关键词搜索")
        tools_layout.addWidget(self.search_input, 3, 0)
        tools_layout.addWidget(self.search_button, 3, 1)
        tools.setMaximumHeight(165)
        right.addWidget(tools)

        tabs = QTabWidget(objectName="detailsTabs")
        self.log_output = QPlainTextEdit()
        self.log_output.setObjectName("mainLogOutput")
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(2000)
        self.log_output.setMinimumHeight(360)
        self.log_output.verticalScrollBar().setSingleStep(20)
        self.log_output.setPlaceholderText("系统控制台日志将在这里实时显示…")
        self.log_output.textChanged.connect(self._scroll_main_log_to_latest)
        tabs.addTab(self.log_output, "系统控制台日志")

        self.activity_table = QTableWidget(0, 5)
        self.activity_table.setHorizontalHeaderLabels(("时间", "任务", "状态", "耗时", "摘要"))
        self.activity_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.activity_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.activity_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.activity_table.setShowGrid(False)
        self.activity_table.setAlternatingRowColors(True)
        self.activity_table.verticalScrollBar().setSingleStep(20)
        self.activity_table.verticalHeader().setVisible(False)
        self.activity_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.activity_table.horizontalHeader().setStretchLastSection(True)
        tabs.addTab(self.activity_table, "近期活动记录")

        right.addWidget(tabs, 1)
        splitter.addWidget(right_panel)

        splitter.setSizes([800, 480])
        content_layout.addWidget(splitter, 1)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("系统准备就绪")

    def _button(self, text: str, icon_name: str, icon_color: str = "#475569") -> QPushButton:
        button = QPushButton(text, self)
        button.setIcon(line_icon(icon_name, icon_color))
        return button

    def _wire_events(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_profiles)
        self.scan_all_button.clicked.connect(self.scan_all)
        self.scan_selected_button.clicked.connect(self.scan_selected)
        self.run_all_button.clicked.connect(self.start_all)
        self.stop_all_button.clicked.connect(self.stop_all)
        self.automation_button.clicked.connect(self.configure_and_run_automation)
        self.engine_update_button.clicked.connect(self.check_automation_engine_update)
        self.reauth_button.clicked.connect(self.reauthenticate_agent)
        
        self.check_login_button.clicked.connect(lambda: self._run_read_only_task("x.check_login", "登录检查"))
        self.read_profile_button.clicked.connect(lambda: self._run_read_only_task("x.read_profile", "读取档案"))
        self.read_timeline_button.clicked.connect(lambda: self._run_read_only_task("x.read_timeline", "读取时间线"))
        self.search_button.clicked.connect(self.run_x_search)
        self.search_input.returnPressed.connect(self.run_x_search)
        
        self.table.itemSelectionChanged.connect(self._account_selection_changed)

    def _load_registry(self) -> None:
        try:
            self.set_accounts(self.controller.list_accounts())
        except Exception as exc:
            self._show_error(f"读取账号资产失败：{exc}")

    def _load_local_statistics(self) -> None:
        try:
            summary = self.controller.task_statistics("today") or {}
            by_acc = summary.get("by_account", {}) if isinstance(summary, dict) else {}
            if "by_account" in self._statistics and isinstance(self._statistics["by_account"], dict):
                for key, val in self._statistics["by_account"].items():
                    if key in by_acc and isinstance(by_acc[key], dict):
                        by_acc[key] = {**val, **by_acc[key]}
                    else:
                        by_acc[key] = val
            summary["by_account"] = by_acc
            self.set_statistics(summary)
            self.set_activities(self.controller.recent_activities(20))
        except Exception:
            pass

    def _collect_dashboard_data(self) -> dict[str, Any]:
        return {
            "summary": self.controller.task_statistics("today") or {},
            "activities": self.controller.recent_activities(20),
            "accounts": self.controller.list_accounts(),
        }

    def _apply_dashboard_data(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        by_acc = summary.get("by_account", {}) if isinstance(summary, dict) else {}
        if "by_account" in self._statistics and isinstance(self._statistics["by_account"], dict):
            merged = dict(self._statistics["by_account"])
            for key, val in self._statistics["by_account"].items():
                if key not in merged:
                    merged[key] = val
            for key, val in by_acc.items():
                if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
                    merged[key] = {**merged[key], **val}
                else:
                    merged[key] = val
            summary["by_account"] = merged
        self.set_statistics(summary)
        self.set_activities(payload.get("activities") or [])
        self.set_accounts(payload.get("accounts") or [])

    def _background_accounts_finished(self, records: Any) -> None:
        if not self._closing:
            self.set_accounts(records if isinstance(records, list) else [])

    def _dashboard_refresh_finished(self, payload: Any) -> None:
        self._dashboard_refresh_in_flight = False
        if not self._closing:
            self._apply_dashboard_data(payload)

    def _refresh_local_statistics(self) -> None:
        if self._dashboard_refresh_in_flight or self._closing:
            return
        self._dashboard_refresh_in_flight = True
        self._run_background(
            self._collect_dashboard_data,
            self._dashboard_refresh_finished,
            lambda: setattr(self, "_dashboard_refresh_in_flight", False),
        )

    def _refresh_agent_status(self) -> None:
        if self._status_refresh_in_flight or self._closing:
            return
        self._status_refresh_in_flight = True
        self._run_background(
            self.controller.server_agent_status,
            self._apply_agent_status,
            lambda: setattr(self, "_status_refresh_in_flight", False),
        )

    def _apply_agent_status(self, status: Any) -> None:
        if self._closing:
            return
        status = status if isinstance(status, dict) else {}
        try:
            status = status or {}
        except Exception:
            status = {}
        server = status.get("server", "OFFLINE")
        agent = status.get("agent", "OFFLINE")
        authorization_mode = str(status.get("authorization_mode") or "RESTRICTED")
        self._capabilities = {str(item) for item in status.get("capabilities") or [] if str(item)}
        self._capabilities.update({"local.view", "local.browser.stop"})
        
        server_text = {"ONLINE": "在线", "OFFLINE": "离线"}.get(server, str(server))
        agent_text = {"ONLINE": "在线", "OFFLINE": "离线", "UNCONFIGURED": "未配置", "UNREGISTERED": "未注册", "REAUTH_REQUIRED": "需要重新认证"}.get(agent, str(agent))

        self.server_state_label.setText(f"服务器：{server_text}")
        self.agent_state_label.setText(f"运行端：{agent_text}")

        needs_reauth = agent in {"UNCONFIGURED", "UNREGISTERED", "REAUTH_REQUIRED"}
        self.reauth_button.setVisible(needs_reauth)

        online = server == "ONLINE" and agent == "ONLINE"
        self._remote_online = online
        self._needs_reauth = needs_reauth
        if authorization_mode == "REAUTH_REQUIRED" or agent == "REAUTH_REQUIRED":
            text = "● 凭据失效，需要重新认证"
            self.live_status_label.setObjectName("liveStatusError")
        elif authorization_mode == "ONLINE" and online:
            text = "● 运行端活跃中 · 已连接服务"
            self.live_status_label.setObjectName("liveStatusOnline")
        elif authorization_mode == "OFFLINE_GRACE":
            expires_at = str(status.get("authorization_expires_at") or "").replace("T", " ")
            expires_at = expires_at[:16] if expires_at else "未知时间"
            text = f"● 服务器离线 · 本地授权有效至 {expires_at}"
            self.live_status_label.setObjectName("liveStatus")
        elif authorization_mode == "RESTRICTED":
            text = "● 受限模式 · 仅可查看和停止浏览器档案"
            self.live_status_label.setObjectName("liveStatus")
        else:
            text = "● 运行端正在尝试连接…"
            self.live_status_label.setObjectName("liveStatus")

        self.live_status_label.setText(text)
        self.live_status_label.style().unpolish(self.live_status_label)
        self.live_status_label.style().polish(self.live_status_label)

        heartbeat = str(status.get("last_heartbeat") or "—").replace("T", " ")[:19]
        self.heartbeat_label.setText(f"最近心跳：{heartbeat}")
        self._mini_window.set_connection(
            "● 服务在线" if online else f"● {agent_text}",
            online,
        )
        self._update_busy_state()

    def reauthenticate_agent(self) -> None:
        agent_id = self.controller.current_agent_id() if hasattr(self.controller, 'current_agent_id') else ""
        dialog = AgentReauthDialog(agent_id, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        aid, token = dialog.credentials()
        if not aid or not token:
            QMessageBox.warning(self, "信息不完整", "请填写 Agent ID 和 Agent Token。")
            return
        self._run_job("重新认证运行端", lambda: self.controller.replace_agent_credentials(aid, token), lambda _r: (self._refresh_agent_status(), QMessageBox.information(self, "认证成功", "运行端已恢复连线！")))

    def check_automation_engine_update(self) -> None:
        self._run_job(
            "检查 Web 后台自动化脚本版本",
            self.controller.automation_engine_update_status,
            self._automation_engine_update_checked,
        )

    def _automation_engine_update_checked(self, status: Any) -> None:
        if not isinstance(status, dict):
            self.engine_update_label.setText("自动化脚本：版本信息无效")
            return
        remote_version = str(status.get("remote_version") or "未知")
        if not status.get("update_available"):
            installed = str(status.get("installed_version") or remote_version)
            self.engine_update_label.setText(f"自动化脚本：已是最新（{installed}）")
            self._log(f"自动化脚本已是最新版本：{installed}")
            return

        self.engine_update_label.setText(f"自动化脚本：发现新版本 {remote_version}")
        answer = QMessageBox.question(
            self,
            "发现自动化脚本更新",
            f"Web 后台发布了自动化脚本 {remote_version}。\n\n"
            "下载后会先校验 SHA-256 和只读兼容性，再激活为下一次任务使用；当前正在运行的任务不会被中断。\n\n"
            "现在下载并替换吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._engine_progress_dialog = QProgressDialog("正在下载并替换脚本…", "", 0, 0, self)
            self._engine_progress_dialog.setWindowTitle("正在更新自动化脚本")
            self._engine_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self._engine_progress_dialog.setRange(0, 100)
            self._engine_progress_dialog.setValue(0)
            self._engine_progress_dialog.setAutoClose(False)
            self._engine_progress_dialog.show()
            self._run_job(
                "下载并激活自动化脚本",
                lambda: self.controller.download_automation_engine_update(progress=self.engine_progress_signal.emit),
                self._automation_engine_update_finished,
            )

    def _engine_progress(self, value: int, message: str = "") -> None:
        if QThread.currentThread() != self.thread():
            QTimer.singleShot(0, lambda: self._engine_progress(value, message))
            return
        if self._engine_progress_dialog is None:
            return
        self._engine_progress_dialog.setValue(max(0, min(100, int(value))))
        if message:
            self._engine_progress_dialog.setLabelText(message)

    def _automation_engine_update_finished(self, status: Any) -> None:
        if self._engine_progress_dialog is not None:
            self._engine_progress_dialog.close()
            self._engine_progress_dialog.deleteLater()
            self._engine_progress_dialog = None
        if not isinstance(status, dict):
            return
        self._engine_restart_required = True
        self.engine_update_button.setText("脚本已更新（请重启）")
        self.engine_update_button.setEnabled(False)
        self.statusBar().showMessage("脚本已替换成功，请重启控制中心后使用")
        version = str(status.get("installed_version") or status.get("remote_version") or "未知")
        self.engine_update_label.setText(f"自动化脚本：已激活（{version}）")
        self._log(str(status.get("message") or "自动化脚本更新完成"))
        QMessageBox.information(
            self,
            "脚本更新完成",
            f"自动化脚本 {version} 已激活。下一次启动自动化任务时生效。",
        )

    def set_statistics(self, summary: dict[str, Any]) -> None:
        self._statistics = summary or {}
        for key, label in self.stat_labels.items():
            label.setText(str(self._statistics.get(key, 0)))
        self._update_mini_metrics()

    def set_activities(self, activities: list[dict[str, Any]]) -> None:
        self.activity_table.setRowCount(len(activities or []))
        for row, activity in enumerate(activities or []):
            values = (
                str(activity.get("timestamp", "")).replace("T", " ")[:19],
                str(activity.get("activity_type", "")),
                str(activity.get("status", "")),
                f"{float(activity.get('duration') or 0):.3f}s",
                str(activity.get("summary", "")),
            )
            for column, value in enumerate(values):
                self.activity_table.setItem(row, column, QTableWidgetItem(value))

    def selected_profile_ids(self) -> list[str]:
        ids: list[str] = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 1)
            if item and item.text().strip():
                ids.append(item.text().strip())
        return ids

    def _select_profile(self, profile_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item and item.text() == profile_id:
                self.table.selectRow(row)
                return

    def set_accounts(self, records: list[AccountRow]) -> None:
        records = records or []
        self._account_rows_by_id = {record.profile_id: record for record in records}
        by_account_stats = self._statistics.get("by_account", {}) if isinstance(self._statistics, dict) else {}
        signature = tuple(
            (
                record.profile_id,
                record.profile_name,
                record.browser_status,
                record.login_status,
                record.x_username,
                record.x_account_id,
                record.account_status,
                record.last_checked,
                repr({key: by_account_stats.get(key) for key in (record.profile_id, record.x_account_id, record.x_username, record.profile_name)}),
            )
            for record in records
        )
        if signature == self._account_view_signature:
            self.summary_label.setText(f"{len(records)} 个账号")
            self._update_mini_metrics()
            return
        self._account_view_signature = signature
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(records))
        try:
            for row, record in enumerate(records):
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
                    self.table.setItem(row, column, QTableWidgetItem(str(value)))

                acct_keys = [record.profile_id, record.x_account_id, record.x_username, record.profile_name]
                account_stat = {}
                for k in acct_keys:
                    if k and k in by_account_stats and isinstance(by_account_stats[k], dict):
                        account_stat.update(by_account_stats[k])

                card = AccountCardWidget(
                    record,
                    account_stat,
                    on_select=self._select_profile,
                    on_run=lambda pid: self._run_profile_action("启动", self.controller.start_profile, [pid]),
                    on_stop=lambda pid: self._run_profile_action("停止", self.controller.stop_profile, [pid]),
                    on_config=self.configure_and_run_automation,
                )
                self.table.setCellWidget(row, 0, card)
                self.table.setRowHeight(row, 78)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
        self.summary_label.setText(f"{len(records)} 个账号")
        self._update_mini_metrics()
        self._account_selection_changed()

    def _require_selection(self, single: bool = False) -> list[str]:
        ids = self.selected_profile_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先在账号列表中选择一个账号档案。")
            return []
        if single and len(ids) > 1:
            QMessageBox.information(self, "提示", "此操作请选择单个账号档案。")
            return []
        return ids

    def refresh_profiles(self) -> None:
        self._run_job("刷新浏览器档案", self.controller.refresh_profiles, self._profiles_finished)

    def scan_all(self) -> None:
        self._run_job("扫描底层账号", self.controller.scan_accounts, self.set_accounts)

    def scan_selected(self) -> None:
        ids = self._require_selection()
        if ids:
            self._run_job("扫描选中账号", lambda: self.controller.scan_accounts(ids), self.set_accounts)

    def start_all(self) -> None:
        self._run_profile_action("启动全部", self.controller.start_profile, [row.profile_id for row in self.controller.list_accounts()])

    def stop_all(self) -> None:
        self._run_profile_action("停止全部", self.controller.stop_profile, [row.profile_id for row in self.controller.list_accounts()])

    def _open_automation_config_async(self, target_id: str, account_name: str) -> None:
        if target_id in self._config_load_in_flight:
            self.statusBar().showMessage("正在读取配置，请稍候…")
            return
        self._config_load_in_flight.add(target_id)
        dialog = TaskConfigDialog({}, self, self._automation_engines_cache)
        self._config_dialogs[target_id] = dialog
        self.statusBar().showMessage("配置窗口已打开，正在后台刷新方案列表…")

        if self._automation_engines_cache is None and hasattr(self.controller, "list_automation_engines"):
            def apply_engines(value: Any) -> None:
                engines = value if isinstance(value, list) else []
                if engines:
                    self._automation_engines_cache = list(engines)
                    if dialog.isVisible():
                        dialog.set_engines(engines)
                self.statusBar().showMessage("自动化方案列表已刷新")

            self._run_background(self.controller.list_automation_engines, apply_engines)

        def apply_initial(value: Any) -> None:
            if isinstance(value, dict) and dialog.isVisible():
                dialog.apply_initial(value)

        self._run_background(lambda: self.controller.get_profile_task_config(target_id), apply_initial)

        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self._config_dialogs.pop(target_id, None)
        self._config_load_in_flight.discard(target_id)
        if not accepted:
            return
        config = dialog.config()
        config["account_tag"] = account_name
        self._run_job(
            f"启动自动化引擎：{account_name} ({target_id})",
            lambda: self.controller.start_automation_task(target_id, config),
            lambda result: self._automation_finished(target_id, result),
        )

    def configure_and_run_automation(self, profile_id: str | None = None) -> None:
        if isinstance(profile_id, str) and profile_id:
            target_id = profile_id
        else:
            ids = self._require_selection(single=True)
            if not ids:
                return
            target_id = ids[0]

        if target_id in self._config_load_in_flight:
            self.statusBar().showMessage("正在读取配置，请稍候…")
            return
        record = self._account_rows_by_id.get(target_id)
        account_name = (record.profile_name if record else "") or target_id
        self._open_automation_config_async(target_id, account_name)

    def run_x_search(self) -> None:
        raw_query = self.search_input.text().strip()
        if raw_query:
            self._run_read_only_task("x.search", "关键词搜索", {"query": raw_query})
        else:
            QMessageBox.information(self, "提示", "请输入有效的搜索关键词。")

    def _run_read_only_task(self, task_type: str, label: str, params: dict[str, Any] | None = None) -> None:
        ids = self._require_selection(single=True)
        if ids:
            self._run_job(f"{label}: {ids[0]}", lambda: self.controller.run_read_only_task(ids[0], task_type, params), lambda result: self._read_only_task_finished(label, ids[0], result))

    def _run_profile_action(self, label: str, function: Callable[[str], Any], ids: list[str] | None = None) -> None:
        selected = ids or self._require_selection()
        if selected:
            def safe_action():
                for pid in selected:
                    try:
                        function(pid)
                    except Exception as e:
                        err_str = str(e)
                        if "10061" in err_str or "拒绝" in err_str or "Cannot connect" in err_str:
                            pass
                        else:
                            raise e
            self._run_job(label, safe_action, lambda _r: self.refresh_profiles())

    def _run_job(self, label: str, function: Callable[[], Any], callback: Callable[[Any], None], progress_callback: Callable[[int, str], None] | None = None) -> None:
        if self._closing:
            return
        self._active_jobs += 1
        self._update_busy_state()
        self._log(f">> 开始执行: {label}")
        worker = FunctionWorker(function)
        self._workers.add(worker)
        worker.signals.finished.connect(callback)
        if progress_callback is not None:
            worker.signals.progress.connect(progress_callback)
        worker.signals.error.connect(lambda msg: self._job_failed(label, msg))
        worker.signals.done.connect(lambda: self._job_done(worker))
        self.thread_pool.start(worker)

    def _run_background(
        self,
        function: Callable[[], Any],
        callback: Callable[[Any], None],
        finished: Callable[[], None] | None = None,
    ) -> None:
        if self._closing:
            return
        worker = FunctionWorker(function)
        self._background_workers.add(worker)
        worker.signals.finished.connect(callback)
        worker.signals.done.connect(lambda: self._background_worker_done(worker, finished))
        self.thread_pool.start(worker)

    def _background_worker_done(self, worker: FunctionWorker, finished: Callable[[], None] | None) -> None:
        self._background_workers.discard(worker)
        if finished:
            finished()

    def _job_done(self, worker: FunctionWorker) -> None:
        self._workers.discard(worker)
        if not self._closing:
            self._active_jobs = max(0, self._active_jobs - 1)
            self._update_busy_state()

    def _update_busy_state(self) -> None:
        busy = self._active_jobs > 0
        for btn in (
            self.refresh_button,
            self.scan_all_button,
            self.scan_selected_button,
        ):
            btn.setEnabled(not busy)
        self.run_all_button.setEnabled(not busy and "local.browser.control" in self._capabilities)
        self.stop_all_button.setEnabled(not busy and "local.browser.stop" in self._capabilities)
        for btn in (
            self.check_login_button,
            self.read_profile_button,
            self.read_timeline_button,
            self.search_button,
        ):
            btn.setEnabled(not busy and "local.readonly.run" in self._capabilities)
        self.automation_button.setEnabled(not busy and "automation.run" in self._capabilities)
        self.engine_update_button.setEnabled(not busy and "engine.update" in self._capabilities)
        self.reauth_button.setEnabled(not busy and self._needs_reauth)

    def _health_finished(self, _result: Any) -> None:
        self._log("Laogu Browser API 连接正常")
        self._run_job("刷新 Profile 实时状态", self.controller.refresh_profiles, self._profiles_finished)

    def _profiles_finished(self, profiles: list[dict[str, Any]] | None) -> None:
        self._profiles = profiles or []
        self._load_registry()

    def _account_selection_changed(self) -> None:
        ids = self.selected_profile_ids()
        selected_id = ids[0] if len(ids) == 1 else ""
        for row in range(self.table.rowCount()):
            card = self.table.cellWidget(row, 0)
            if isinstance(card, AccountCardWidget):
                card.set_selected(bool(selected_id and card.profile_id == selected_id))
        if len(ids) != 1:
            self.selected_profile_label.setText("尚未选择档案")
            self.selected_runtime_label.setText("运行状态：—")
            return
        pid = ids[0]
        account = self._account_rows_by_id.get(pid)
        profile = next((item for item in self._profiles if str(item.get("profileId") or item.get("profile_id") or "") == pid), {})
        name = str(profile.get("profileName") or profile.get("profile_name") or (account.profile_name if account else "") or pid)
        handle = account.x_username if account and account.x_username else "未绑定 X 账号"
        running = bool(profile.get("running")) if profile else bool(account and account.runtime_running)
        self.selected_profile_label.setText(f"{name}  ·  {handle}")
        self.selected_profile_label.setToolTip(f"Profile ID: {pid}")
        self.selected_runtime_label.setText(f"运行状态：{'运行中' if running else '未启动'}")

    def _automation_finished(self, profile_id: str, result: Any) -> None:
        status = result.get("status", "SUCCESS") if isinstance(result, dict) else "SUCCESS"
        error_msg = str(result.get("error", "")) if isinstance(result, dict) else ""
        target_url = str(result.get("url", "")) if isinstance(result, dict) else ""

        if status == "CHALLENGE_REQUIRED" or "account/access" in error_msg or "account/access" in target_url:
            self.statusBar().showMessage(f"⚠️ 档案 {profile_id} 触发人机验证，任务已自动终止！")
            self._log(f"🚨 风控警报  档案 {profile_id} 遇到人机验证 (account/access)，自动化已强行停止！")

            QMessageBox.warning(
                self,
                "⚠️ 触发 X 平台人机验证",
                f"档案【{profile_id}】在运行时触发了 Cloudflare / X 平台人机验证。\n\n"
                f"出于账号安全保护，自动化任务已【强制终止】。\n\n"
                f"👉 请切到对应的浏览器窗口手动点一下验证框，完成后即可重新启动。"
            )
            self._load_local_statistics()
            return

        self.statusBar().showMessage(f"档案 {profile_id} 自动化任务已下发：{status}")
        self._log(f"档案 {profile_id} 自动化任务下发完成，状态: {status}")
        self._load_registry()

    def _find_numeric_val(self, data: Any, keys: list[str]) -> Any:
        if isinstance(data, dict):
            for k, v in data.items():
                if str(k).lower() in [x.lower() for x in keys] and (isinstance(v, (int, str)) and str(v).isdigit()):
                    return v
                res = self._find_numeric_val(v, keys)
                if res is not None:
                    return res
        elif isinstance(data, list):
            for item in data:
                res = self._find_numeric_val(item, keys)
                if res is not None:
                    return res
        return None

    def _read_only_task_finished(self, label: str, profile_id: str, result: Any) -> None:
        status = result.get("status", "SUCCESS") if isinstance(result, dict) else "SUCCESS"
        self._log(f"{label} 执行完成: {profile_id} ({status})")
        task_result = result.get("result", {}) if isinstance(result, dict) else {}
        if isinstance(task_result, dict) and task_result.get("profile_data_status") == "PARTIAL":
            self._log("[提示] 档案页面已读取，但部分账号资产暂未渲染；已保留最近一次有效数据")
        self._load_registry()

    def _job_failed(self, label: str, message: str) -> None:
        if self._engine_progress_dialog is not None:
            self._engine_progress_dialog.close()
            self._engine_progress_dialog.deleteLater()
            self._engine_progress_dialog = None
        if "10061" in message or "拒绝" in message or "Cannot connect" in message:
            self._log(f"[提示] 目标浏览器服务不可达，已重置为停止状态: {message}")
            self._load_registry()
            return
        self._log(f"[错误] {label} 失败: {message}")
        self._show_error(f"{label}失败\n\n{message}")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误提示", message)

    def _log(self, message: str) -> None:
        t = datetime.now().strftime("%H:%M:%S")
        self._queue_log(f"[{t}] {message}")

    def _update_mini_metrics(self) -> None:
        records = list(self._account_rows_by_id.values())
        running = sum(1 for record in records if record.runtime_running and record.runtime_debug_ready)
        success = int(self._statistics.get("success_tasks", 0) or 0)
        failed = int(self._statistics.get("failed_tasks", 0) or 0) + int(self._statistics.get("timeout_tasks", 0) or 0)
        self._mini_window.set_metrics(running, success, failed)

    def _show_mini_window(self) -> None:
        if self._closing:
            return
        screen = self.screen()
        if screen is None:
            from PySide6.QtWidgets import QApplication

            screen = QApplication.primaryScreen()
        if screen is None:
            return
        if self._log_tail_path:
            self._read_log_file_tail(seed=True)
        else:
            self._mini_window.seed_logs(list(self._recent_log_lines)[-6:])
        self._update_mini_metrics()
        self.hide()
        self._mini_window.show_at_bottom_right(screen.availableGeometry())
        self._log_tail_timer.start()

    def _restore_from_mini_window(self) -> None:
        self._log_tail_timer.stop()
        self._mini_window.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _confirm_stop_all_from_mini(self) -> None:
        answer = QMessageBox.question(
            self._mini_window,
            "停止全部任务",
            "确定要停止全部浏览器档案和当前自动化任务吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.stop_all()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized() and not self._closing:
            QTimer.singleShot(0, self._show_mini_window)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self._log_tail_timer.stop()
        self._mini_window.hide()
        self._mini_window.deleteLater()
        self._agent_status_timer.stop()
        self._statistics_timer.stop()
        if hasattr(self, "auto_refresh_timer"):
            self.auto_refresh_timer.stop()
        for worker in tuple(self._workers):
            for sig in (worker.signals.finished, worker.signals.error, worker.signals.done):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
        self._workers.clear()
        for worker in tuple(self._background_workers):
            for sig in (worker.signals.finished, worker.signals.error, worker.signals.done):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
        self._background_workers.clear()
        self.controller.stop_agent_service()
        event.accept()
