from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import os
import socket
import sys
import time
from typing import Any, Callable

from PySide6.QtCore import QDateTime, QEasingCurve, QEvent, QItemSelection, QItemSelectionModel, QObject, QPoint, QPropertyAnimation, QRect, QSize, Qt, QThread, QThreadPool, QTime, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QMouseEvent, QColor, QKeySequence, QShortcut, QKeyEvent, QResizeEvent, QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QSystemTrayIcon,
    QTimeEdit,
)

from .controller import AccountRow, DesktopController
from .branding import application_icon
from .iconography import line_icon
from .workers import FunctionWorker
from .updater import ReleaseInfo, UpdateCheckWorker, UpdateNoticeDialog
from .styles import APP_STYLE
from common.release import VERSION
from agent.blacklist_filter import (
    DEFAULT_BLACKLIST_CATEGORIES,
    DEFAULT_BLACKLIST_WORDS,
    DEFAULT_CHECK_OPTIONS,
    clean_target_creators,
    parse_blacklist_input,
)


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


class AnimatedMultiSelectMenu(QFrame):
    """向下平滑展开展示选项的现代多选菜单组件（带流畅缓动动效与实时摘要状态）。"""

    def __init__(self, title: str, checkboxes: list[QCheckBox], default_expanded: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("animatedMultiSelect")
        self.setStyleSheet("""
            QFrame#animatedMultiSelect {
                background: #FFFFFF;
                border: 1px solid #C9D3E1;
                border-radius: 7px;
            }
            QFrame#animatedMultiSelect:hover {
                border-color: #98A7BA;
            }
            QPushButton#multiSelectHeader {
                background: #F8FAFC;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                text-align: left;
                min-height: 32px;
            }
            QPushButton#multiSelectHeader:hover {
                background: #EEF4FF;
            }
        """)

        self._checkboxes = checkboxes
        self._is_expanded = default_expanded

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.header_btn = QPushButton(objectName="multiSelectHeader")
        header_layout = QHBoxLayout(self.header_btn)
        header_layout.setContentsMargins(4, 0, 4, 0)
        header_layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #172033; font-weight: 600; font-size: 12px;")

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("color: #2457D6; background: #EFF4FF; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600;")

        self.arrow_label = QLabel("▼")
        self.arrow_label.setStyleSheet("color: #667085; font-size: 10px; font-weight: bold;")

        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.summary_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.arrow_label)
        main_layout.addWidget(self.header_btn)

        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(12, 6, 12, 8)
        self.container_layout.setSpacing(6)

        for cb in checkboxes:
            self.container_layout.addWidget(cb)
            cb.toggled.connect(lambda _: self._update_summary())

        main_layout.addWidget(self.container)

        self.header_btn.clicked.connect(self.toggle_expand)
        self._update_summary()

        self._anim = QPropertyAnimation(self.container, b"maximumHeight")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        if not default_expanded:
            self.container.setMaximumHeight(0)
            self.container.setVisible(False)
            self.arrow_label.setText("▼")
        else:
            self.container.setMaximumHeight(260)
            self.container.setVisible(True)
            self.arrow_label.setText("▲")

    def _update_summary(self) -> None:
        checked = [cb.text().split("（")[0].replace("过滤", "").strip() for cb in self._checkboxes if cb.isChecked()]
        total = len(self._checkboxes)
        if len(checked) == total:
            self.summary_label.setText(f"全部开启 ({total}项)")
            self.summary_label.setStyleSheet("color: #067647; background: #ECFDF3; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600;")
        elif len(checked) == 0:
            self.summary_label.setText("全部关闭")
            self.summary_label.setStyleSheet("color: #667085; background: #F2F4F7; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600;")
        else:
            items_str = "/".join(c[:4] for c in checked)
            self.summary_label.setText(f"已选 {len(checked)} 项: {items_str}")
            self.summary_label.setStyleSheet("color: #2457D6; background: #EFF4FF; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600;")

    def toggle_expand(self) -> None:
        self._is_expanded = not self._is_expanded
        self.arrow_label.setText("▲" if self._is_expanded else "▼")

        target_h = self.container_layout.sizeHint().height() + 16
        if self._is_expanded:
            self.container.setVisible(True)
            self._anim.stop()
            self._anim.setStartValue(self.container.height())
            self._anim.setEndValue(target_h)
            self._anim.start()
        else:
            self._anim.stop()
            self._anim.setStartValue(self.container.height())
            self._anim.setEndValue(0)
            self._anim.finished.connect(self._on_collapse_finished)
            self._anim.start()

    def _on_collapse_finished(self) -> None:
        if not self._is_expanded:
            self.container.setVisible(False)


class SegmentedPillControl(QFrame):
    """现代滑槽分段胶囊控件 (Segmented Pills Control)，与底层 QComboBox 双向绑定同步。"""

    ACTIVE_STYLE = (
        "QPushButton { background: #FFFFFF; color: #1570EF; font-weight: 600; "
        "border: 1px solid #D0D5DD; border-radius: 6px; padding: 5px 12px; font-size: 12px; "
        "font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; }"
        "QPushButton:hover { background: #F8FAFC; }"
    )
    INACTIVE_STYLE = (
        "QPushButton { background: transparent; color: #475467; font-weight: 500; "
        "border: none; border-radius: 6px; padding: 5px 12px; font-size: 12px; "
        "font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; }"
        "QPushButton:hover { background: #E4E7EC; color: #101828; }"
    )

    def __init__(
        self,
        combo_target: QComboBox,
        items: list[tuple[str, str, str]],
        parent: QWidget | None = None,
    ):
        """items: [(label, value, icon), ...]"""
        super().__init__(parent)
        self.setObjectName("segmentedPillTrack")
        self._target = combo_target
        self._buttons: list[QPushButton] = []

        self.setStyleSheet("""
            QFrame#segmentedPillTrack {
                background: #F2F4F7;
                border: 1px solid #EAECF0;
                border-radius: 8px;
                padding: 2px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        for idx, (label, val, icon) in enumerate(items):
            text = f"{icon} {label}".strip() if icon else label
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, i=idx: self._on_btn_clicked(i))
            layout.addWidget(btn)
            self._buttons.append(btn)

        self._target.currentIndexChanged.connect(self._sync_from_combo)
        self._sync_from_combo(self._target.currentIndex())

    def _on_btn_clicked(self, index: int) -> None:
        self._target.setCurrentIndex(index)

    def _sync_from_combo(self, index: int) -> None:
        for idx, btn in enumerate(self._buttons):
            btn.setStyleSheet(self.ACTIVE_STYLE if idx == index else self.INACTIVE_STYLE)


SAFETY_HINTS: dict[str, tuple[str, str]] = {
    "dry_run": (
        "仅预演浏览",
        "开启后仅模拟正常搜索与信息流浏览翻页，严格禁止点赞、关注、评论或私信等任何写操作，适合新手熟悉流程或排查网络代理稳定性。",
    ),
    "filter_menu": (
        "账号特征过滤库",
        "集成防踩坑多维规则：自动跳过蓝勾/金勾付费机构认证大V、识别戏仿/粉丝应援账号、自动过滤带 Bot 标记的机器号，聚焦高互动真人受众。",
    ),
    "filter_verified": (
        "过滤蓝勾/金勾认证账号",
        "自动识别并跳过带有蓝勾、金勾或已认证徽章的账号，避免对大V或机构进行无效打扰，提高触达转化率。",
    ),
    "filter_parody": (
        "过滤戏仿/非官方标签账号",
        "自动识别并跳过标记为戏仿（Parody）、粉丝应援（Fan Account）、非公式的账号，保障受众真实度。",
    ),
    "filter_bot": (
        "过滤机器人账号",
        "自动识别并跳过用户名、推特 ID 中包含 bot、_bot、机器人等自动化特征的账号，防止与机器人互刷消耗配额。",
    ),
    "filter_blacklist": (
        "全字段敏感词/黑名单过滤",
        "开启后对候选博主的昵称、ID、个人简介与最新推文进行全文本扫描，只要命中敏感特征词立即一票否决跳过；关闭后不校验敏感词。可在主界面【敏感词库】自由配置词条与一键总开关。",
    ),
    "warmup_ramp": (
        "新号阶梯递增养号",
        "为新账号提供仿生升温保护：第1天20、第2天35、第3天55、第4天80... 按活跃天数逐步提升上限，彻底避免一开机就高频操作导致的秒封。",
    ),
    "follow_burst_cooling": (
        "高频爆发降频冷却",
        "连续多次关注后原地小憩 90~150 秒，模拟人类生理喘息，打破机器脚本高频节奏特征，稳健避封。",
    ),
    "soft_landing": (
        "单日限额软着陆",
        "当单日关注进度达到 85% 后，自动拉长动作微等待间隔，模拟人类打瞌睡疲惫变慢，平滑结束当日任务。",
    ),
    "smart_unfollow": (
        "关注比健康维护",
        "每次运行开始时扫描自身关注列表，优雅释放已关注超期（如5天）但未回关的博主，保持粉丝/关注比例健康美观。",
    ),
    "cold_start_warmup": (
        "冷启动真人预热",
        "在正式执行拓客任务前，先在首页信息流自然滚动浏览指定时长（如45秒），消灭一开机就直奔搜索的典型爬虫指纹。",
    ),
    "casual_tweet": (
        "消痕生活发推",
        "在批次休息闲逛时，模拟真人随机发布1条生活推文，稀释高频互动比例，打破营销号单一行为模式。",
    ),
    "cdp_timezone": (
        "CDP 动态时区与语言覆盖",
        "通过 CDP 底层协议动态注入时区与语言覆盖，彻底消灭 JS 检测出的宿主机北京时间(UTC+8)与海外代理IP冲突的穿帮风控风险。",
    ),
    "cdp_geo": (
        "同步对齐 GPS 经纬度",
        "根据目标时区城市（如东京、纽约、伦敦）同步注入精确 GPS 经纬度，防止网站通过 HTML5 Geolocation API 检测到地理位置矛盾。",
    ),
    "graphql_risk": (
        "推特接口致命风控自动熔断",
        "当捕获到推特自动化判定 (Code 226)、人机验证挑战 (Code 399) 或账号锁定时，自动熔断暂停当前任务并向 Telegram 报警。",
    ),
    "cloud_dedup": (
        "工作室跨设备防撞车去重",
        "多台电脑、多个操作员通过统一的工作室协同码共享去重历史，彻底避免不同账号对同一个目标博主或受众重复打扰。",
    ),
    "advanced_limits": (
        "高级互动限制",
        "可独立控制是否允许点赞、关注、回复、收藏、转推，并支持设置每种操作的独立单日上限（默认不限，跟随策略预设）。",
    ),
    "natural_roaming": (
        "自然摸鱼与行为噪声注入",
        "在自动化任务批次间或周期性自动切回 X 首页【为你推荐】摸鱼闲逛 15~25 秒，打乱持续搜索的单一行为轨迹，彻底打破 X 平台的图灵风控检测。",
    ),
    "pre_click_guard": (
        "推文卡片前置指标快速质检",
        "在鼠标点击进博主主页前，先在推文卡片层快速质检默认头像、纯数字乱码号与纯外链推文；毫秒级跳过垃圾号，100% 节省单日宝贵互动配额。",
    ),
    "search_pagination_refresh": (
        "最新流深滚翻页自愈机制",
        "解决在「最新」推文流中向下滚动过深导致 X 官方接口卡死、加载变慢或返回空白列表的顽疾；深滚达限或断流时自动平滑回顶刷新，持续捕获刚出炉的最新推文。",
    ),
    "graphql_scout_filter": (
        "底层数据流极速透视预筛 (实验性功能)",
        "自动旁路嗅探推特 SearchTimeline 底层 GraphQL 响应流，在内存中毫秒级提取博主完整档案（粉丝数、关注比、注册天数、Bio），预先淘汰垃圾营销号与无效页面跳转。（默认开启，自由选择关闭）",
    ),
    "smart_newbie_recognition": (
        "真人新人智能识别 (新手小白精准吸纳)",
        "推特官方为新注册用户默认分配带 7~8 位随机数字的用户名（如 @Name12345678）。很多初级真实新手因未修改用户名而被传统脚本误杀。开启本功能后，系统不会粗暴淘汰数字后缀，而是综合研判其是否拥有正常生活头像与真实原创推文，智能吸纳极高回关率的初级新手真人，仅在【超长数字 + 默认初始头像】或【超长数字 + 纯推广外链】等双重劣质特征重叠时进行一票否决淘汰。",
    ),
}

class SmartSuffixFilter(QObject):
    """
    智能后缀输入助手：
    1. 当光标位于不可编辑的后缀区域或文本末尾时，用户按下退格键 (Backspace)，自动跳转至数字末尾执行退格删除，解决卡在固定后缀无法删除的痛点。
    2. 当用户点击输入框或获得焦点时，若光标落入后缀区域，自动平滑吸附至数字末端，方便直接追加或修改输入。
    3. 当光标位于后缀区域时直接输入数字或小数点，自动将光标前置至数字末尾插入，避免输入被后缀阻挡。
    """
    def __init__(self, spin: QAbstractSpinBox):
        super().__init__(spin)
        self.spin = spin

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            prefix_len = len(getattr(self.spin, "prefix", lambda: "")())
            clean_len = len(getattr(self.spin, "cleanText", lambda: "")())
            num_end = prefix_len + clean_len
            if event.key() == Qt.Key.Key_Backspace:
                if hasattr(obj, "hasSelectedText") and not obj.hasSelectedText() and hasattr(obj, "cursorPosition"):
                    if obj.cursorPosition() >= num_end:
                        obj.setCursorPosition(num_end)
                        obj.backspace()
                        return True
            elif hasattr(event, "text") and event.text() and event.text() in "0123456789.":
                if hasattr(obj, "hasSelectedText") and not obj.hasSelectedText() and hasattr(obj, "cursorPosition"):
                    if obj.cursorPosition() > num_end:
                        obj.setCursorPosition(num_end)
        elif event.type() in (QEvent.Type.MouseButtonRelease, QEvent.Type.FocusIn):
            def _adjust():
                if not hasattr(self.spin, "cleanText") or not hasattr(obj, "cursorPosition"):
                    return
                prefix_len = len(getattr(self.spin, "prefix", lambda: "")())
                clean_len = len(getattr(self.spin, "cleanText", lambda: "")())
                num_end = prefix_len + clean_len
                if obj.cursorPosition() > num_end:
                    obj.setCursorPosition(num_end)
            QTimer.singleShot(0, _adjust)
        return super().eventFilter(obj, event)


class TaskConfigDialog(QDialog):
    """配置单账号或多账号自动化任务的参数对话框 - 已融入批次间隔时间控制"""

    def __init__(self, initial: dict[str, Any] | None = None, parent: QWidget | None = None, engines: list[dict[str, Any]] | None = None):
        super().__init__(parent)
        self.setWindowTitle("配置自动化任务")
        self.setModal(True)
        self.setMinimumSize(780, 520)
        self.resize(860, 650)
        self.setSizeGripEnabled(True)
        initial = initial or {}
        active = initial.get("active") if isinstance(initial.get("active"), dict) else initial

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        dialog_header = QFrame(objectName="dialogHeader")
        dialog_header_layout = QVBoxLayout(dialog_header)
        dialog_header_layout.setContentsMargins(24, 12, 24, 8)
        dialog_header_layout.setSpacing(3)
        dialog_header_layout.addWidget(QLabel("自动化任务配置", objectName="dialogTitle"))
        dialog_header_layout.addWidget(QLabel("先设置运行配置与筛选条件；互动权限和上限集中在“安全限制”。", objectName="dialogDescription"))
        shell.addWidget(dialog_header)

        self.config_tabs = QTabWidget(objectName="taskConfigTabs")
        run_scroll, run_form = self._scrollable_form()
        self.run_form = run_form

        safety_scroll = QScrollArea(objectName="taskConfigScroll")
        safety_scroll.setFrameShape(QFrame.Shape.NoFrame)
        safety_scroll.setWidgetResizable(True)
        safety_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        safety_surface = QFrame(objectName="dialogFormSurface")
        safety_layout = QVBoxLayout(safety_surface)
        safety_layout.setContentsMargins(24, 12, 24, 12)
        safety_layout.setSpacing(10)
        safety_scroll.setWidget(safety_surface)
        self.safety_form = safety_layout

        self.config_tabs.addTab(run_scroll, "运行配置")
        self.config_tabs.addTab(safety_scroll, "安全限制")
        shell.addWidget(self.config_tabs, 1)

        # ----------------------------------------------------
        # Tab 0: 运行配置 (双列下拉 + 滑槽胶囊 + 策略徽章)
        # ----------------------------------------------------
        self.engine_input = QComboBox()
        self.engine_input.setMinimumHeight(32)
        self.engine_input.setFixedWidth(240)
        self._engine_choices: list[dict[str, Any]] = []
        self.engine_access_notice = QLabel()
        self.engine_access_notice.setWordWrap(True)
        self.engine_access_notice.setObjectName("dialogHint")
        self.engine_access_notice.setVisible(False)
        selected_engine = str(active.get("engine_id") or "default")
        self.set_engines(engines, selected_engine)

        self.outreach_mode_input = QComboBox()
        self.outreach_mode_input.setMinimumHeight(32)
        self.outreach_mode_input.setFixedWidth(280)
        self.outreach_mode_input.addItem("关键词推文拓客（默认）", "keyword")
        self.outreach_mode_input.addItem("🔗 关键词种子·无限顺藤摸瓜（A➜B➜C 链式裂变）", "keyword_chain")
        self.outreach_mode_input.addItem("👥 扫博主粉丝模式（批量深挖目标博主粉丝群）", "target_followers")
        self.outreach_mode_input.addItem("纯私域关系网拓客（从我的关注列表出发）", "network_hop")
        mode_val = str(active.get("outreach_mode") or "keyword").lower()
        mode_idx = self.outreach_mode_input.findData(mode_val)
        self.outreach_mode_input.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)

        # Row 1: 自动化引擎与拓客工作模式双列收纳
        engine_and_mode_row = QWidget()
        engine_and_mode_layout = QHBoxLayout(engine_and_mode_row)
        engine_and_mode_layout.setContentsMargins(0, 0, 0, 0)
        engine_and_mode_layout.setSpacing(8)

        engine_box = QWidget()
        engine_box_layout = QVBoxLayout(engine_box)
        engine_box_layout.setContentsMargins(0, 0, 0, 0)
        engine_box_layout.setSpacing(2)
        engine_box_layout.addWidget(self.engine_input)
        engine_box_layout.addWidget(self.engine_access_notice)

        outreach_mode_label = QLabel("拓客模式")
        outreach_mode_label.setFixedWidth(65)
        outreach_mode_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        outreach_mode_label.setStyleSheet("color: #344054; font-weight: 500;")

        engine_and_mode_layout.addWidget(engine_box)
        engine_and_mode_layout.addSpacing(14)
        engine_and_mode_layout.addWidget(outreach_mode_label)
        engine_and_mode_layout.addWidget(self.outreach_mode_input)
        engine_and_mode_layout.addStretch(1)

        run_form.addRow("自动化引擎", engine_and_mode_row)

        # Row 2: 运行模式（现代滑槽分段轨道）
        self.schedule_mode_input = QComboBox()
        self.schedule_mode_input.setMinimumHeight(32)
        self.schedule_mode_input.addItem("智能时段（保持现有规则）", "smart")
        self.schedule_mode_input.addItem("立即执行", "immediate")
        self.schedule_mode_input.addItem("自定义定时", "scheduled")
        self.schedule_mode_input.setVisible(False)

        self.schedule_pills = SegmentedPillControl(
            self.schedule_mode_input,
            [
                ("智能时段 (推荐)", "smart", "🕒"),
                ("立即执行", "immediate", "⚡"),
                ("自定义定时", "scheduled", "📅"),
            ],
        )

        schedule_row = QWidget()
        schedule_layout = QHBoxLayout(schedule_row)
        schedule_layout.setContentsMargins(0, 0, 0, 0)
        schedule_layout.setSpacing(10)
        schedule_layout.addWidget(self.schedule_pills)
        schedule_layout.addWidget(self.schedule_mode_input)

        self.schedule_mode_tip = QLabel("💡 智能规避深夜与午休，模拟真实作息")
        self.schedule_mode_tip.setStyleSheet("color: #0284C7; font-size: 11px;")
        schedule_layout.addWidget(self.schedule_mode_tip)
        schedule_layout.addStretch(1)

        run_form.addRow("运行模式", schedule_row)

        self.schedule_type_input = QComboBox()
        self.schedule_type_input.setMinimumHeight(32)
        self.schedule_type_input.addItem("单次执行", "once")
        self.schedule_type_input.addItem("每天执行", "daily")
        run_form.addRow("定时类型", self.schedule_type_input)

        self.scheduled_at_input = QDateTimeEdit(QDateTime.currentDateTime().addSecs(300))
        self.scheduled_at_input.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.scheduled_at_input.setCalendarPopup(True)
        self.scheduled_at_input.setMinimumDateTime(QDateTime.currentDateTime())
        self.scheduled_at_input.setMinimumHeight(32)
        run_form.addRow("单次执行时间", self.scheduled_at_input)

        self.scheduled_time_input = QTimeEdit(QTime.currentTime().addSecs(300))
        self.scheduled_time_input.setDisplayFormat("HH:mm")
        self.scheduled_time_input.setMinimumHeight(32)
        run_form.addRow("每日执行时间", self.scheduled_time_input)

        self.schedule_timezone_label = QLabel("Asia/Shanghai（北京时间）")
        run_form.addRow("定时时区", self.schedule_timezone_label)

        self.schedule_hint_label = QLabel()
        self.schedule_hint_label.setStyleSheet("color: #0284c7; font-size: 11px; padding: 2px 0;")
        self.schedule_hint_label.setWordWrap(True)
        run_form.addRow("定时预估", self.schedule_hint_label)

        self.schedule_mode_input.currentIndexChanged.connect(self._update_schedule_controls)
        self.schedule_type_input.currentIndexChanged.connect(self._update_schedule_controls)
        self.scheduled_at_input.dateTimeChanged.connect(lambda: self._update_schedule_controls())
        self.scheduled_time_input.timeChanged.connect(lambda: self._update_schedule_controls())

        # Row 3: 运行策略预设（滑槽胶囊 + 策略说明徽章）
        self.execution_preset_input = QComboBox()
        self.execution_preset_input.setMinimumHeight(32)
        self.execution_preset_input.addItem("🛡️ 安全防风控模式（默认 · 推荐）", "safe")
        self.execution_preset_input.addItem("⚡ 极速高通量模式（Turbo 300+ 冲刺）", "turbo")
        preset_val = str(active.get("execution_preset") or active.get("speed_mode") or "safe").lower()
        preset_idx = self.execution_preset_input.findData(preset_val)
        self.execution_preset_input.setCurrentIndex(preset_idx if preset_idx >= 0 else 0)
        self.execution_preset_input.setVisible(False)

        self.preset_pills = SegmentedPillControl(
            self.execution_preset_input,
            [
                ("安全防风控", "safe", "🛡️"),
                ("极速冲刺 Turbo", "turbo", "⚡"),
            ],
        )

        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(12)
        preset_layout.addWidget(self.preset_pills)
        preset_layout.addWidget(self.execution_preset_input)

        self.preset_badge = QLabel("🛡️ 单日配额 50 人 · 平滑拟人微等待 · 稳健避封")
        self.preset_badge.setStyleSheet(
            "color: #344054; background: #F2F4F7; border: 1px solid #D0D5DD; border-radius: 4px; padding: 4px 8px; font-size: 11px;"
        )
        preset_layout.addWidget(self.preset_badge)
        preset_layout.addStretch(1)

        run_form.addRow("运行策略预设", preset_row)

        def _update_preset_badge(idx: int):
            preset_data = self.execution_preset_input.itemData(idx)
            if preset_data == "turbo":
                self.preset_badge.setText("⚡ 单日配额 300+ 人 · 极速吞吐 · 适合老号冲刺")
                self.preset_badge.setStyleSheet(
                    "color: #B54708; background: #FFFAEB; border: 1px solid #FEDF89; border-radius: 4px; padding: 4px 8px; font-size: 11px;"
                )
            else:
                self.preset_badge.setText("🛡️ 单日配额 50 人 · 平滑拟人微等待 · 稳健避封")
                self.preset_badge.setStyleSheet(
                    "color: #344054; background: #F2F4F7; border: 1px solid #D0D5DD; border-radius: 4px; padding: 4px 8px; font-size: 11px;"
                )

        self.execution_preset_input.currentIndexChanged.connect(_update_preset_badge)
        _update_preset_badge(self.execution_preset_input.currentIndex())

        # Row 4: 智能作息
        self.smart_schedule_enabled_input = QCheckBox("开启智能人类作息（上午 08-12、下午 14-18、晚间 18-22，中午与深夜自动休息）")
        self.smart_schedule_enabled_input.setChecked(bool(active.get("smart_schedule_enabled", True)))
        self.smart_schedule_enabled_input.setToolTip("开启后，无论是立即执行还是定时执行，都将根据目标受众时区的人类作息进行智能保护（上午 08:00~12:00、下午 14:00~18:00、晚间 18:00~22:00 正常工作，中午 12:00~14:00 与深夜 22:00~08:00 自动休眠）；关闭后全时段全速运行，跳过所有休息等待，满足随时测试与持续拓客需求。")
        run_form.addRow("智能作息", self.smart_schedule_enabled_input)

        # 目标博主配置区（用于扫博主粉丝模式）
        raw_targets = active.get("target_creators") or []
        if isinstance(raw_targets, (list, tuple, set)):
            target_text = "\n".join(str(t) for t in raw_targets if t)
        else:
            target_text = str(raw_targets or "")
        self.target_creators_input = QPlainTextEdit(target_text)
        self.target_creators_input.setPlaceholderText("填入目标博主 ID（例如：@elonmusk, @OpenAI, natfriedman），支持换行或逗号分隔，系统将自动剥离 @ 符号")
        self.target_creators_input.setMinimumHeight(68)
        self.target_creators_input.setMaximumHeight(110)

        self.max_followers_per_target_input = self._spin(active.get("max_followers_per_target"), 50, 1, 10_000)
        self.max_followers_per_target_input.setFixedWidth(130)
        self.max_followers_per_target_input.setSuffix(" 人/博主")
        self.max_followers_per_target_input.setToolTip("每个目标博主最多扫描抓取多少位粉丝后自动切换到下一位目标博主。")

        self.target_creators_widget = QWidget()
        target_layout = QVBoxLayout(self.target_creators_widget)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(4)
        target_layout.addWidget(self.target_creators_input)
        target_hint = QLabel("💡 支持填入多个目标博主（每行或逗号一个），系统将依次打开其粉丝列表深度探寻并过滤互动。")
        target_hint.setStyleSheet("color: #0284c7; font-size: 11px;")
        target_hint.setWordWrap(True)
        target_layout.addWidget(target_hint)

        run_form.addRow("目标博主列表", self.target_creators_widget)
        run_form.addRow("单博主扫描上限", self.max_followers_per_target_input)

        # Row 5: 检索关键词（全宽输入框）
        raw_kw = str(active.get("keyword") or active.get("keywords") or "")
        self.keyword_input = QLineEdit(raw_kw)
        self.keyword_input.setMaxLength(500)
        self.keyword_input.setMinimumHeight(32)
        self.keyword_input.setPlaceholderText("留空默认浏览首页推荐流；或输入关键词如：Web3, AI, #出海（逗号分隔多词）")
        run_form.addRow("检索关键词", self.keyword_input)

        def _on_mode_changed():
            mode = self.outreach_mode_input.currentData()
            is_target_followers = (mode == "target_followers")
            if hasattr(self, "run_form") and hasattr(self.run_form, "setRowVisible"):
                self.run_form.setRowVisible(self.target_creators_widget, is_target_followers)
                self.run_form.setRowVisible(self.max_followers_per_target_input, is_target_followers)
            else:
                self.target_creators_widget.setVisible(is_target_followers)
                self.max_followers_per_target_input.setVisible(is_target_followers)
                target_label = run_form.labelForField(self.target_creators_widget)
                if target_label:
                    target_label.setVisible(is_target_followers)
                max_f_label = run_form.labelForField(self.max_followers_per_target_input)
                if max_f_label:
                    max_f_label.setVisible(is_target_followers)

            if mode == "network_hop":
                self.keyword_input.setPlaceholderText("（纯私域关系网拓客模式下无需关键词，将自动从自身关注列表出发）")
            elif mode == "keyword_chain":
                self.keyword_input.setPlaceholderText("请输入首个引流种子搜索关键词（留空默认使用首页推荐推文作为种子）")
            elif is_target_followers:
                self.keyword_input.setPlaceholderText("（扫博主粉丝模式下无需关键词，将自动使用上方目标博主列表）")
            else:
                self.keyword_input.setPlaceholderText("留空默认浏览首页推荐流；或输入关键词如：Web3, AI, #出海（逗号分隔多词）")
            self._update_chain_hop_controls()

        self.outreach_mode_input.currentIndexChanged.connect(_on_mode_changed)
        _on_mode_changed()

        # 任务配额节奏合并 (单日任务上限 + 批次间隔时间)
        quota_box = QWidget()
        quota_layout = QHBoxLayout(quota_box)
        quota_layout.setContentsMargins(0, 0, 0, 0)
        quota_layout.setSpacing(8)

        self.daily_limit_input = self._spin(active.get("daily_task_limit"), 100, 1, 10_000)
        self.daily_limit_input.setFixedWidth(130)
        self.daily_limit_input.setSuffix(" 人")
        self.daily_limit_input.setToolTip("全天处理条目的主限制目标人数（默认 100 人，保留自定义填写）。")

        self.batch_interval_input = self._spin(active.get("batch_interval_minutes"), 15, 1, 1440)
        self.batch_interval_input.setFixedWidth(130)
        self.batch_interval_input.setSuffix(" 分钟")
        self.batch_interval_input.setToolTip("每次执行批次之间的休息缓冲时间。")

        daily_label = QLabel("全天目标 ≤")
        daily_label.setFixedWidth(70)
        batch_label = QLabel("每批间隔")
        batch_label.setFixedWidth(70)

        quota_layout.addWidget(daily_label)
        quota_layout.addWidget(self.daily_limit_input)
        quota_layout.addSpacing(18)
        quota_layout.addWidget(batch_label)
        quota_layout.addWidget(self.batch_interval_input)
        quota_layout.addStretch(1)
        run_form.addRow("任务配额节奏", quota_box)

        # 动作延时区间设置 (4~8s 随机延时)
        delay_widget = QWidget()
        delay_layout = QHBoxLayout(delay_widget)
        delay_layout.setContentsMargins(0, 0, 0, 0)
        delay_layout.setSpacing(8)

        self.action_min_delay_input = QDoubleSpinBox()
        self.action_min_delay_input.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.action_min_delay_input.setRange(1.0, 60.0)
        self.action_min_delay_input.setDecimals(1)
        self.action_min_delay_input.setSingleStep(0.5)
        self.action_min_delay_input.setSuffix(" 秒")
        self.action_min_delay_input.setMinimumHeight(36)
        self.action_min_delay_input.setFixedWidth(130)
        try:
            self.action_min_delay_input.setValue(float(active.get("action_min_delay", 4.0)))
        except (TypeError, ValueError):
            self.action_min_delay_input.setValue(4.0)
        TaskConfigDialog._install_smart_suffix_filter(self.action_min_delay_input)

        self.action_max_delay_input = QDoubleSpinBox()
        self.action_max_delay_input.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.action_max_delay_input.setRange(1.0, 120.0)
        self.action_max_delay_input.setDecimals(1)
        self.action_max_delay_input.setSingleStep(0.5)
        self.action_max_delay_input.setSuffix(" 秒")
        self.action_max_delay_input.setMinimumHeight(36)
        self.action_max_delay_input.setFixedWidth(130)
        try:
            self.action_max_delay_input.setValue(float(active.get("action_max_delay", 8.0)))
        except (TypeError, ValueError):
            self.action_max_delay_input.setValue(8.0)
        TaskConfigDialog._install_smart_suffix_filter(self.action_max_delay_input)

        min_delay_label = QLabel("最小延时")
        min_delay_label.setFixedWidth(70)
        max_delay_label = QLabel("最大延时")
        max_delay_label.setFixedWidth(70)

        delay_layout.addWidget(min_delay_label)
        delay_layout.addWidget(self.action_min_delay_input)
        delay_layout.addSpacing(18)
        delay_layout.addWidget(max_delay_label)
        delay_layout.addWidget(self.action_max_delay_input)
        delay_layout.addStretch(1)

        run_form.addRow("动作随机延时", delay_widget)

        # 拟人防封保护合并 (间隔随机抖动 + 真人度自学习)
        anti_risk_box = QWidget()
        anti_risk_layout = QHBoxLayout(anti_risk_box)
        anti_risk_layout.setContentsMargins(0, 0, 0, 0)
        anti_risk_layout.setSpacing(14)

        self.batch_jitter_enabled_input = QCheckBox("批次随机抖动（防周期特征）")
        self.batch_jitter_enabled_input.setChecked(bool(active.get("batch_jitter_enabled", True)))
        self.batch_jitter_enabled_input.setToolTip("安全模式下在基准时间上随机浮动 -2 ~ +4 分钟（外加 10~45 秒微扰动）；极速模式下附加 5~35 秒离散微等待，彻底打散固定周期。")

        self.authenticity_learning_input = QCheckBox("真人度自学习闭环（动态强化特征）")
        self.authenticity_learning_input.setChecked(bool(active.get("authenticity_learning_enabled", True)))
        self.authenticity_learning_input.setToolTip("开启后，自动通过 Base64 隔离过滤垃圾/追星/广告号，提取日本生活特征，并在48小时后根据回关状态自进化 (+3 / -1)。")

        anti_risk_layout.addWidget(self.batch_jitter_enabled_input)
        anti_risk_layout.addWidget(self.authenticity_learning_input)
        anti_risk_layout.addStretch(1)
        run_form.addRow("拟人防封机制", anti_risk_box)

        refresh_widget = QWidget()
        refresh_layout = QHBoxLayout(refresh_widget)
        refresh_layout.setContentsMargins(0, 0, 0, 0)
        refresh_layout.setSpacing(10)

        self.periodic_search_refresh_enabled_input = QCheckBox("定时刷新最新搜索流（捕获1~3分钟内最新推文）")
        self.periodic_search_refresh_enabled_input.setChecked(bool(active.get("periodic_search_refresh_enabled", True)))
        self.periodic_search_refresh_enabled_input.setToolTip("开启后，脚本在搜索页面将按设定时间间隔自动刷新回到最新推文流或点击最新帖子气泡，获取刚发布的1~3分钟内最新内容；不开启时默认保持原本的持续向下滚动探索逻辑。（默认开启，自由选择关闭）")

        self.search_refresh_interval_label = QLabel("刷新间隔", objectName="compactFieldLabel")
        self.search_refresh_interval_input = self._spin(active.get("search_refresh_interval_minutes", 15), 15, 1, 60)
        self.search_refresh_interval_input.setFixedWidth(100)
        self.search_refresh_interval_input.setSuffix(" 分钟")
        self.search_refresh_interval_input.setToolTip("定时刷新搜索流的间隔时间（1~60分钟，默认15分钟）。达到设定时间后自动返回顶部或刷新获取最新推文。")

        def _sync_refresh_state():
            is_enabled = self.periodic_search_refresh_enabled_input.isChecked()
            self.search_refresh_interval_label.setEnabled(is_enabled)
            self.search_refresh_interval_input.setEnabled(is_enabled)

        self.periodic_search_refresh_enabled_input.toggled.connect(lambda _: _sync_refresh_state())
        _sync_refresh_state()

        refresh_layout.addWidget(self.periodic_search_refresh_enabled_input)
        refresh_layout.addWidget(self.search_refresh_interval_label)
        refresh_layout.addWidget(self.search_refresh_interval_input)
        refresh_layout.addStretch(1)

        run_form.addRow("实时最新内容", refresh_widget)

        def _on_preset_changed():
            is_turbo = self.execution_preset_input.currentData() == "turbo"
            if is_turbo:
                if self.daily_limit_input.value() <= 50:
                    self.daily_limit_input.setValue(350)
                if self.batch_interval_input.value() >= 12:
                    self.batch_interval_input.setValue(2)
            else:
                if self.daily_limit_input.value() >= 300:
                    self.daily_limit_input.setValue(100)
                if self.batch_interval_input.value() <= 3:
                    self.batch_interval_input.setValue(15)
        self.execution_preset_input.currentIndexChanged.connect(_on_preset_changed)

        # 受众过滤门槛（粉丝门槛与互动门槛合并为一行，双列并排）
        threshold_box = QWidget()
        threshold_layout = QHBoxLayout(threshold_box)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        threshold_layout.setSpacing(8)

        self.follower_limit_input = self._spin(active.get("max_follower_threshold"), 150, 0, 100_000_000)
        self.follower_limit_input.setFixedWidth(110)
        self.follower_limit_input.setSuffix(" 人")
        self.follower_limit_input.setToolTip("目标博主粉丝数上限（默认 150），超过此数值将被视为大V/同行忽略。")

        self.statuses_limit_input = self._spin(active.get("max_statuses_threshold"), 10_000, 0, 100_000_000)
        self.statuses_limit_input.setFixedWidth(110)
        self.statuses_limit_input.setSuffix(" 条")
        self.statuses_limit_input.setToolTip("目标博主历史发帖总量上限（默认 10000），超过此数值将被视为高频营销号忽略。")

        self.engagement_limit_input = self._spin(active.get("max_engagement_threshold"), 10_000, 0, 100_000_000)
        self.engagement_limit_input.setFixedWidth(110)
        self.engagement_limit_input.setSuffix(" 次")
        self.engagement_limit_input.setToolTip("推文总互动量（点赞+转推）上限（默认 10000），超过此数值将被视为热门广告贴忽略。")

        follower_label = QLabel("博主粉丝 ≤")
        follower_label.setFixedWidth(65)
        statuses_label = QLabel("博主发帖 ≤")
        statuses_label.setFixedWidth(65)
        engagement_label = QLabel("推文互动 ≤")
        engagement_label.setFixedWidth(65)

        threshold_layout.addWidget(follower_label)
        threshold_layout.addWidget(self.follower_limit_input)
        threshold_layout.addSpacing(14)
        threshold_layout.addWidget(statuses_label)
        threshold_layout.addWidget(self.statuses_limit_input)
        threshold_layout.addSpacing(14)
        threshold_layout.addWidget(engagement_label)
        threshold_layout.addWidget(self.engagement_limit_input)
        threshold_layout.addStretch(1)
        run_form.addRow("受众过滤门槛", threshold_box)

        # 深度互动配比（AI 评论回复与主页深入访问合并为一行，双列并排）
        interaction_box = QWidget()
        interaction_layout = QHBoxLayout(interaction_box)
        interaction_layout.setContentsMargins(0, 0, 0, 0)
        interaction_layout.setSpacing(8)

        self.ai_reply_ratio_input = QComboBox()
        self.ai_reply_ratio_input.setMinimumHeight(36)
        self.ai_reply_ratio_input.setFixedWidth(130)
        for label, ratio in (
            ("关闭", 0.0),
            ("低频 10%", 0.10),
            ("标准 15%", 0.15),
            ("高频 25%", 0.25),
        ):
            self.ai_reply_ratio_input.addItem(label, ratio)
        self._set_ai_reply_ratio(active.get("ai_reply_ratio", 0.0))
        self._apply_schedule(active)
        self._update_schedule_controls()

        self.profile_visit_ratio_input = self._ratio_spin(active.get("profile_visit_ratio"), 0.80)
        self.profile_visit_ratio_input.setFixedWidth(130)
        self.profile_visit_ratio_input.setToolTip("推文拓客时点击进入博主个人主页深度终审推文与简介的触发概率（默认 0.80 即 80%，保留自定义）。\n推荐设置 0.80~1.00；设为 1.00 时将 100% 强制逐个进主页核验，彻底杜绝漏网账号。")

        profile_visit_hint = QLabel("💡 推荐 0.80~1.00 (1.00 强制进主页)", objectName="dialogHint")
        profile_visit_hint.setStyleSheet("color: #0284c7; font-size: 11px;")

        ai_label = QLabel("AI 评论")
        ai_label.setFixedWidth(70)
        visit_label = QLabel("主页终审")
        visit_label.setFixedWidth(70)

        interaction_layout.addWidget(ai_label)
        interaction_layout.addWidget(self.ai_reply_ratio_input)
        interaction_layout.addSpacing(18)
        interaction_layout.addWidget(visit_label)
        interaction_layout.addWidget(self.profile_visit_ratio_input)
        interaction_layout.addSpacing(8)
        interaction_layout.addWidget(profile_visit_hint)
        interaction_layout.addStretch(1)
        run_form.addRow("深度互动配比", interaction_box)

        chain_hop_box = QWidget()
        chain_hop_vbox = QVBoxLayout(chain_hop_box)
        chain_hop_vbox.setContentsMargins(0, 0, 0, 0)
        chain_hop_vbox.setSpacing(6)

        # Row 1: 开关 + 来源 + 触发概率
        chain_hop_row1 = QWidget()
        chain_hop_row1_layout = QHBoxLayout(chain_hop_row1)
        chain_hop_row1_layout.setContentsMargins(0, 0, 0, 0)
        chain_hop_row1_layout.setSpacing(10)

        self.chain_hop_enabled_input = QCheckBox("开启顺藤摸瓜")
        self.chain_hop_enabled_input.setChecked(bool(active.get("chain_hop_enabled", False)))
        self.chain_hop_enabled_input.setToolTip("开启后，命中素人博主建联后，顺便进入其关系网列表寻找同好博主关注点赞。（默认关闭，自由选择开启）")

        self.chain_hop_source_label = QLabel("摸瓜来源", objectName="compactFieldLabel")
        self.chain_hop_source_input = QComboBox()
        self.chain_hop_source_input.addItem("关注者 (Followers，默认)", "followers")
        self.chain_hop_source_input.addItem("正在关注 (Following)", "following")
        raw_source = str(active.get("chain_hop_source") or "followers").strip().lower()
        self.chain_hop_source_input.setCurrentIndex(1 if raw_source == "following" else 0)
        self.chain_hop_source_input.setMinimumWidth(190)
        self.chain_hop_source_input.setToolTip("顺藤摸瓜深挖来源：【关注者 (Followers)】深挖该博主的粉丝受众；【正在关注 (Following)】深挖该博主主动关注的圈内同好好友。系统全自动适配中/日/英界面及 Tab 切换。")

        self.chain_hop_ratio_label = QLabel("触发概率", objectName="compactFieldLabel")
        self.chain_hop_ratio_input = self._ratio_spin(active.get("chain_hop_ratio"), 0.70)
        self.chain_hop_ratio_input.setFixedWidth(85)
        self.chain_hop_ratio_input.setToolTip("命中博主后进入其关系网列表挖掘同好的触发概率，默认 0.70（即 70%），可在 0.00 到 1.00 之间调整。")

        chain_hop_row1_layout.addWidget(self.chain_hop_enabled_input)
        chain_hop_row1_layout.addSpacing(6)
        chain_hop_row1_layout.addWidget(self.chain_hop_source_label)
        chain_hop_row1_layout.addWidget(self.chain_hop_source_input)
        chain_hop_row1_layout.addSpacing(10)
        chain_hop_row1_layout.addWidget(self.chain_hop_ratio_label)
        chain_hop_row1_layout.addWidget(self.chain_hop_ratio_input)
        chain_hop_row1_layout.addStretch(1)

        # Row 2: 递归深度 + 单博主关注上限
        chain_hop_row2 = QWidget()
        chain_hop_row2_layout = QHBoxLayout(chain_hop_row2)
        chain_hop_row2_layout.setContentsMargins(0, 0, 0, 0)
        chain_hop_row2_layout.setSpacing(10)

        self.max_chain_depth_label = QLabel("递归深度", objectName="compactFieldLabel")
        raw_depth = active.get("max_chain_depth")
        self.max_chain_depth_input = self._spin(raw_depth if raw_depth is not None else 2, 2, 0, 10)
        self.max_chain_depth_input.setFixedWidth(95)
        self.max_chain_depth_input.setSuffix(" 层")
        self.max_chain_depth_input.setSpecialValueText("无限制 (直到配额达成)")
        self.max_chain_depth_input.setToolTip("顺藤摸瓜关系网探索的最大递归深度，设为 0 表示无限制持续裂变直到达成配额，也可设定 1~10 层。")

        self.max_harvest_per_seed_label = QLabel("单博主关注上限", objectName="compactFieldLabel")
        raw_harvest = active.get("max_harvest_per_seed")
        self.max_harvest_per_seed_input = self._spin(raw_harvest if raw_harvest is not None else 10, 10, 1, 30)
        self.max_harvest_per_seed_input.setFixedWidth(120)
        self.max_harvest_per_seed_input.setSuffix(" 位合格人")
        self.max_harvest_per_seed_input.setToolTip("指进入该博主好友列表后【实际成功关注的合格人数上限】（遇锁推/敏感词/不合格账号直接跳过，不计入此数）。达到该上限后自动安全回退，防止过度深挖。防风控浅层拜访推荐设为 3~5 人。")

        chain_hop_row2_layout.addWidget(self.max_chain_depth_label)
        chain_hop_row2_layout.addWidget(self.max_chain_depth_input)
        chain_hop_row2_layout.addSpacing(16)
        chain_hop_row2_layout.addWidget(self.max_harvest_per_seed_label)
        chain_hop_row2_layout.addWidget(self.max_harvest_per_seed_input)
        chain_hop_row2_layout.addStretch(1)

        chain_hop_vbox.addWidget(chain_hop_row1)
        chain_hop_vbox.addWidget(chain_hop_row2)

        self.chain_hop_sub_hint = QLabel("💡 提示：默认进入目标博主的【关注者 (Followers)】列表深挖，支持切换为【正在关注】；【单博主关注上限】仅计算实际成功关注的合格人数，达标后自动安全回退。", objectName="dialogHint")
        self.chain_hop_sub_hint.setStyleSheet("color: #0284c7; font-size: 11px; padding-top: 2px;")
        self.chain_hop_sub_hint.setWordWrap(True)
        chain_hop_vbox.addWidget(self.chain_hop_sub_hint)

        self.chain_hop_note_label = QLabel(objectName="dialogHint")
        self.chain_hop_note_label.setStyleSheet("color: #0284c7; font-size: 11px; padding-top: 1px;")
        self.chain_hop_note_label.setWordWrap(True)
        self.chain_hop_note_label.setVisible(False)
        chain_hop_vbox.addWidget(self.chain_hop_note_label)

        self.chain_hop_enabled_input.toggled.connect(lambda _: self._update_chain_hop_controls())
        self._update_chain_hop_controls()

        run_form.addRow("关系网拓客", chain_hop_box)

        run_hint = QLabel("开始前请复核配置、检索条件和任务上限；定时模式会以北京时间保存。")
        run_hint.setWordWrap(True)
        run_hint.setObjectName("dialogHint")
        run_form.addRow(run_hint)

        # ========================================================
        # Tab 1: 安全限制 (双列直线绝对对齐 + 智能说明坞)
        # ========================================================
        safety_grid = QGridLayout()
        safety_grid.setContentsMargins(0, 0, 0, 0)
        safety_grid.setHorizontalSpacing(18)
        safety_grid.setVerticalSpacing(8)
        safety_grid.setColumnMinimumWidth(0, 85)   # 行标题（右对齐）
        safety_grid.setColumnMinimumWidth(1, 260)  # 第1列（垂直基准线 1）
        safety_grid.setColumnMinimumWidth(2, 280)  # 第2列（垂直基准线 2）

        def _make_safety_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet("color: #344054; font-weight: 600; font-size: 12px;")
            return lbl

        # Row 0: 预演与过滤
        safety_grid.addWidget(_make_safety_label("预演与过滤"), 0, 0)
        self.dry_run_input = QCheckBox("仅预演浏览（不产生互动）")
        self.dry_run_input.setChecked(bool(active.get("dry_run", False)))
        self.dry_run_input.setToolTip("仅搜索和浏览，不执行点赞、关注、回复、收藏或转推等任何写操作。")
        self._register_inspector_hint(self.dry_run_input, "dry_run")
        safety_grid.addWidget(self.dry_run_input, 0, 1)

        self.filter_verified_input = QCheckBox("过滤蓝勾 / 金勾大V账号")
        self.filter_verified_input.setChecked(bool(active.get("filter_verified_accounts", True)))
        self.filter_verified_input.setToolTip("自动识别并跳过带有蓝勾、金勾或已认证徽章的账号，避免对大V进行无效打扰。")
        self._register_inspector_hint(self.filter_verified_input, "filter_verified")

        self.filter_parody_input = QCheckBox("过滤戏仿 / 应援标签账号")
        self.filter_parody_input.setChecked(bool(active.get("filter_parody_accounts", True)))
        self.filter_parody_input.setToolTip("自动识别并跳过标记为戏仿（Parody）、粉丝应援（Fan Account）、非公式的账号。")
        self._register_inspector_hint(self.filter_parody_input, "filter_parody")

        self.filter_bot_input = QCheckBox("过滤机器特征账号 (Bot)")
        self.filter_bot_input.setChecked(bool(active.get("filter_bot_accounts", True)))
        self.filter_bot_input.setToolTip("自动识别并跳过用户名、推特 ID 中包含 bot、_bot、机器人等自动化特征的账号。")
        self.filter_blacklist_input = QCheckBox("全字段敏感词/黑名单过滤")
        self.filter_blacklist_input.setChecked(bool(active.get("blacklist_filter_enabled", True)))
        self.filter_blacklist_input.setToolTip("开启后，对候选博主的名字、ID、Bio与最新推文进行敏感词扫描，命中任意词一票否决跳过；关闭后不校验敏感词。可在主界面【敏感词库】配置词条。")
        self._register_inspector_hint(self.filter_blacklist_input, "filter_blacklist")

        self.account_filter_menu = AnimatedMultiSelectMenu(
            "🛡️ 账号与敏感词过滤库",
            [self.filter_verified_input, self.filter_parody_input, self.filter_bot_input, self.filter_blacklist_input],
            default_expanded=False,
        )
        self.account_filter_menu.setFixedWidth(280)
        self._register_inspector_hint(self.account_filter_menu.header_btn, "filter_menu")
        safety_grid.addWidget(self.account_filter_menu, 0, 2)

        # Row 1: 养号与节奏
        safety_grid.addWidget(_make_safety_label("养号与节奏"), 1, 0)
        self.warmup_ramp_enabled_input = QCheckBox("新号阶梯递增养号")
        self.warmup_ramp_enabled_input.setChecked(bool(active.get("warmup_ramp_enabled", False)))
        self.warmup_ramp_enabled_input.setToolTip("按账号历史活跃天数自适应分配每日上限，避免新账号高频动作触发平台风控拦截。")
        self._register_inspector_hint(self.warmup_ramp_enabled_input, "warmup_ramp")
        safety_grid.addWidget(self.warmup_ramp_enabled_input, 1, 1)

        self.follow_burst_cooling_enabled_input = QCheckBox("高频爆发降频冷却")
        self.follow_burst_cooling_enabled_input.setChecked(bool(active.get("follow_burst_cooling_enabled", True)))
        self.follow_burst_cooling_enabled_input.setToolTip("当短时间内连续关注达到安全阈值时，自动挂机 90~150 秒进行拟人喘息，避免短时关注峰值触发风控降权。")
        self._register_inspector_hint(self.follow_burst_cooling_enabled_input, "follow_burst_cooling")
        safety_grid.addWidget(self.follow_burst_cooling_enabled_input, 1, 2)

        # Row 2: 避封与维护
        safety_grid.addWidget(_make_safety_label("避封与维护"), 2, 0)
        self.soft_landing_enabled_input = QCheckBox("单日限额软着陆降速")
        self.soft_landing_enabled_input.setChecked(bool(active.get("enable_soft_landing", active.get("soft_landing_enabled", True))))
        self.soft_landing_enabled_input.setToolTip("当单日关注量接近总目标（85%以上）时，自动适度拉长动作缓冲间隔，模拟人类疲惫变慢，平滑结束当日任务以规避平台风控。")
        self._register_inspector_hint(self.soft_landing_enabled_input, "soft_landing")
        safety_grid.addWidget(self.soft_landing_enabled_input, 2, 1)

        smart_unfollow_widget = QWidget()
        smart_unfollow_layout = QHBoxLayout(smart_unfollow_widget)
        smart_unfollow_layout.setContentsMargins(0, 0, 0, 0)
        smart_unfollow_layout.setSpacing(8)
        self.smart_unfollow_enabled_input = QCheckBox("关注比健康维护")
        self.smart_unfollow_enabled_input.setChecked(bool(active.get("smart_unfollow_enabled", False)))
        self.smart_unfollow_enabled_input.setToolTip("开启后，每次运行开始时扫描自身关注列表，优雅释放已关注超期但未回关的博主。")
        self._register_inspector_hint(self.smart_unfollow_enabled_input, "smart_unfollow")

        self.unfollow_threshold_days_input = self._spin(active.get("unfollow_threshold_days"), 5, 1, 90)
        self.unfollow_threshold_days_input.setFixedWidth(90)
        self.unfollow_threshold_days_input.setSuffix(" 天未回关")
        self.unfollow_threshold_days_input.setEnabled(self.smart_unfollow_enabled_input.isChecked())
        smart_unfollow_layout.addWidget(self.smart_unfollow_enabled_input)
        smart_unfollow_layout.addWidget(self.unfollow_threshold_days_input)
        smart_unfollow_layout.addStretch(1)
        self.smart_unfollow_enabled_input.toggled.connect(self.unfollow_threshold_days_input.setEnabled)
        safety_grid.addWidget(smart_unfollow_widget, 2, 2)

        # Row 3: 拟人消痕
        safety_grid.addWidget(_make_safety_label("拟人消痕"), 3, 0)
        cold_start_warmup_widget = QWidget()
        cold_start_warmup_layout = QHBoxLayout(cold_start_warmup_widget)
        cold_start_warmup_layout.setContentsMargins(0, 0, 0, 0)
        cold_start_warmup_layout.setSpacing(8)
        self.cold_start_warmup_enabled_input = QCheckBox("冷启动真人预热")
        self.cold_start_warmup_enabled_input.setChecked(bool(active.get("cold_start_warmup_enabled", True)))
        self.cold_start_warmup_enabled_input.setToolTip("开启后，在正式拓客前先在首页信息流中自然滚动浏览随机时长（默认 45~60 秒，不固定），大幅降低一开机就高频操作的风控风险。")
        self._register_inspector_hint(self.cold_start_warmup_enabled_input, "cold_start_warmup")

        self.cold_start_warmup_min_input = self._spin(active.get("cold_start_warmup_min_seconds", 45), 45, 10, 300)
        self.cold_start_warmup_min_input.setFixedWidth(62)
        self.cold_start_warmup_min_input.setSuffix(" 秒")
        self.cold_start_warmup_min_input.setToolTip("冷启动预热随机区间的最小秒数（默认 45 秒）。")

        self.cold_start_warmup_sep_label = QLabel("~")
        self.cold_start_warmup_sep_label.setStyleSheet("color: #64748b; font-weight: bold;")

        raw_max = active.get("cold_start_warmup_max_seconds") or active.get("cold_start_warmup_duration_seconds")
        self.cold_start_warmup_max_input = self._spin(raw_max if raw_max is not None else 60, 60, 10, 300)
        self.cold_start_warmup_max_input.setFixedWidth(62)
        self.cold_start_warmup_max_input.setSuffix(" 秒")
        self.cold_start_warmup_max_input.setToolTip("冷启动预热随机区间的最大秒数（默认 60 秒）。")

        # 保持与旧接口兼容的别名引用
        self.cold_start_warmup_duration_input = self.cold_start_warmup_max_input

        is_warmup_on = self.cold_start_warmup_enabled_input.isChecked()
        self.cold_start_warmup_min_input.setEnabled(is_warmup_on)
        self.cold_start_warmup_sep_label.setEnabled(is_warmup_on)
        self.cold_start_warmup_max_input.setEnabled(is_warmup_on)

        cold_start_warmup_layout.addWidget(self.cold_start_warmup_enabled_input)
        cold_start_warmup_layout.addWidget(self.cold_start_warmup_min_input)
        cold_start_warmup_layout.addWidget(self.cold_start_warmup_sep_label)
        cold_start_warmup_layout.addWidget(self.cold_start_warmup_max_input)
        cold_start_warmup_layout.addStretch(1)

        def _sync_warmup_state(enabled: bool):
            self.cold_start_warmup_min_input.setEnabled(enabled)
            self.cold_start_warmup_sep_label.setEnabled(enabled)
            self.cold_start_warmup_max_input.setEnabled(enabled)

        self.cold_start_warmup_enabled_input.toggled.connect(_sync_warmup_state)
        safety_grid.addWidget(cold_start_warmup_widget, 3, 1)

        casual_tweet_widget = QWidget()
        casual_tweet_layout = QHBoxLayout(casual_tweet_widget)
        casual_tweet_layout.setContentsMargins(0, 0, 0, 0)
        casual_tweet_layout.setSpacing(8)
        self.casual_tweet_enabled_input = QCheckBox("消痕生活发推")
        self.casual_tweet_enabled_input.setChecked(bool(active.get("casual_tweet_enabled", False)))
        self.casual_tweet_enabled_input.setToolTip("在首页看帖消痕时模拟真人发布生活/日常推文，稀释高频互动比例，打破纯营销小号特征。")
        self._register_inspector_hint(self.casual_tweet_enabled_input, "casual_tweet")

        self.casual_tweet_topic_input = QLineEdit(str(active.get("casual_tweet_topic") or ""))
        self.casual_tweet_topic_input.setPlaceholderText("选填话题: #日常")
        self.casual_tweet_topic_input.setMaxLength(100)
        self.casual_tweet_topic_input.setFixedWidth(135)
        self.casual_tweet_topic_input.setEnabled(self.casual_tweet_enabled_input.isChecked())
        casual_tweet_layout.addWidget(self.casual_tweet_enabled_input)
        casual_tweet_layout.addWidget(self.casual_tweet_topic_input)
        casual_tweet_layout.addStretch(1)
        self.casual_tweet_enabled_input.toggled.connect(self.casual_tweet_topic_input.setEnabled)
        safety_grid.addWidget(casual_tweet_widget, 3, 2)

        # Row 4: 时区语言对齐 (跨列整行)
        safety_grid.addWidget(_make_safety_label("时区语言对齐"), 4, 0)
        timezone_override_box = QWidget()
        timezone_layout = QHBoxLayout(timezone_override_box)
        timezone_layout.setContentsMargins(0, 0, 0, 0)
        timezone_layout.setSpacing(10)

        self.cdp_timezone_override_enabled_input = QCheckBox("启用 CDP 动态覆盖")
        self.cdp_timezone_override_enabled_input.setChecked(bool(active.get("cdp_timezone_override_enabled", True)))
        self.cdp_timezone_override_enabled_input.setToolTip("开启后，自动化脚本在连接浏览器时，将通过 CDP 底层协议动态注入时区与语言覆盖，彻底消灭 JS 检测出的宿主机北京时间(UTC+8)与海外代理IP冲突的穿帮风控风险；关闭时沿用浏览器原有系统设置。")
        self._register_inspector_hint(self.cdp_timezone_override_enabled_input, "cdp_timezone")

        self.cdp_override_timezone_input = QComboBox()
        self.cdp_override_timezone_input.setMinimumHeight(30)
        self.cdp_override_timezone_input.setFixedWidth(260)
        for label, val in (
            ("🎯 自动探测代理归属地（推荐）", "auto"),
            ("🇯🇵 日本东京 (Asia/Tokyo · ja-JP)", "Asia/Tokyo"),
            ("🇺🇸 美国东部 (America/New_York · en-US)", "America/New_York"),
            ("🇺🇸 美国西部 (America/Los_Angeles · en-US)", "America/Los_Angeles"),
            ("🇭🇰 中国香港 (Asia/Hong_Kong · zh-HK)", "Asia/Hong_Kong"),
            ("🇹🇼 中国台湾 (Asia/Taipei · zh-TW)", "Asia/Taipei"),
            ("🇸🇬 新加坡 (Asia/Singapore · en-SG)", "Asia/Singapore"),
            ("🇬🇧 英国伦敦 (Europe/London · en-GB)", "Europe/London"),
            ("🇰🇷 韩国首尔 (Asia/Seoul · ko-KR)", "Asia/Seoul"),
            ("🇩🇪 德国柏林 (Europe/Berlin · de-DE)", "Europe/Berlin"),
        ):
            self.cdp_override_timezone_input.addItem(label, val)

        tz_val = str(active.get("cdp_override_timezone") or "auto").strip()
        tz_idx = self.cdp_override_timezone_input.findData(tz_val)
        self.cdp_override_timezone_input.setCurrentIndex(tz_idx if tz_idx >= 0 else 0)
        self.cdp_override_timezone_input.setEnabled(self.cdp_timezone_override_enabled_input.isChecked())

        self.cdp_geolocation_override_enabled_input = QCheckBox("同步对齐 GPS 经纬度")
        self.cdp_geolocation_override_enabled_input.setChecked(bool(active.get("cdp_geolocation_override_enabled", True)))
        self.cdp_geolocation_override_enabled_input.setToolTip("开启后，将根据目标时区城市（如东京、纽约、伦敦）同步注入精确 GPS 经纬度，防止网站通过 HTML5 Geolocation API 检测到地理位置矛盾。")
        self.cdp_geolocation_override_enabled_input.setEnabled(self.cdp_timezone_override_enabled_input.isChecked())
        self._register_inspector_hint(self.cdp_geolocation_override_enabled_input, "cdp_geo")

        def _update_cdp_timezone_controls(enabled: bool):
            self.cdp_override_timezone_input.setEnabled(enabled)
            self.cdp_geolocation_override_enabled_input.setEnabled(enabled)

        self.cdp_timezone_override_enabled_input.toggled.connect(_update_cdp_timezone_controls)

        timezone_layout.addWidget(self.cdp_timezone_override_enabled_input)
        timezone_layout.addWidget(self.cdp_override_timezone_input)
        timezone_layout.addWidget(self.cdp_geolocation_override_enabled_input)
        timezone_layout.addStretch(1)
        safety_grid.addWidget(timezone_override_box, 4, 1, 1, 2)

        # Row 5: 致命风控熔断 (跨列整行)
        safety_grid.addWidget(_make_safety_label("风控熔断保护"), 5, 0)
        graphql_risk_box = QWidget()
        graphql_risk_layout = QHBoxLayout(graphql_risk_box)
        graphql_risk_layout.setContentsMargins(0, 0, 0, 0)
        graphql_risk_layout.setSpacing(10)

        self.graphql_risk_pause_enabled_input = QCheckBox("推特致命风控自动熔断（Code 226/399/326/64 自动暂停防封）")
        self.graphql_risk_pause_enabled_input.setChecked(bool(active.get("graphql_risk_pause_enabled", True)))
        self.graphql_risk_pause_enabled_input.setToolTip(
            "【默认开启】开启后，当接口捕获到推特自动化判定 (Code 226)、人机验证挑战 (Code 399) 或账号锁定时，"
            "自动安全熔断暂停当前账号任务，防止继续运行导致封禁；同时第一时间向 Telegram 推送报警。\n"
            "关闭后，拦截到风控时仅发送 Telegram 报警与控制台提示，不自动挂起任务（任务继续推进）。"
        )
        self._register_inspector_hint(self.graphql_risk_pause_enabled_input, "graphql_risk")

        graphql_risk_badge = QLabel("🛡️ 自动暂停 + TG 报警")
        graphql_risk_badge.setStyleSheet("color: #344054; background: #F2F4F7; border: 1px solid #D0D5DD; border-radius: 4px; padding: 2px 8px; font-size: 11px;")

        graphql_risk_layout.addWidget(self.graphql_risk_pause_enabled_input)
        graphql_risk_layout.addWidget(graphql_risk_badge)
        graphql_risk_layout.addStretch(1)
        safety_grid.addWidget(graphql_risk_box, 5, 1, 1, 2)

        # Row 6: 工作室协同 (跨列整行)
        safety_grid.addWidget(_make_safety_label("工作室协同"), 6, 0)
        studio_box = QWidget()
        studio_layout = QHBoxLayout(studio_box)
        studio_layout.setContentsMargins(0, 0, 0, 0)
        studio_layout.setSpacing(10)

        global_st = {}
        parent_w = self.parent()
        ctrl = getattr(parent_w, "controller", None) or getattr(self, "controller", None)
        if ctrl and hasattr(ctrl, "get_studio_token_config"):
            try:
                global_st = ctrl.get_studio_token_config()
            except Exception:
                pass

        init_st_token = str(active.get("cloud_dedup_studio_token") or active.get("studio_token") or global_st.get("studio_token") or "")
        init_st_enabled = bool(active.get("cloud_dedup_enabled", bool(init_st_token) or global_st.get("enabled", False)))

        self.cloud_dedup_enabled_input = QCheckBox("跨设备防撞车去重")
        self.cloud_dedup_enabled_input.setChecked(init_st_enabled)
        self.cloud_dedup_enabled_input.setToolTip("开启后，多台电脑/多账号通过工作室协同码共享去重历史，防重复打扰。")
        self._register_inspector_hint(self.cloud_dedup_enabled_input, "cloud_dedup")

        self.cloud_dedup_studio_token_input = QLineEdit(init_st_token)
        self.cloud_dedup_studio_token_input.setPlaceholderText("填入工作室协同码 (Studio Token)，例如：std_xxxxxxxx")
        self.cloud_dedup_studio_token_input.setMinimumHeight(30)
        self.cloud_dedup_studio_token_input.setFixedWidth(340)

        studio_layout.addWidget(self.cloud_dedup_enabled_input)
        studio_layout.addWidget(self.cloud_dedup_studio_token_input)
        studio_layout.addStretch(1)
        safety_grid.addWidget(studio_box, 6, 1, 1, 2)

        # Row 7: 高阶拟人防封 (双列并排，自然摸鱼 + 卡片质检，带底部小字标明开启好处)
        safety_grid.addWidget(_make_safety_label("高阶拟人防封"), 7, 0)

        natural_roaming_box = QWidget()
        natural_roaming_vbox = QVBoxLayout(natural_roaming_box)
        natural_roaming_vbox.setContentsMargins(0, 0, 0, 0)
        natural_roaming_vbox.setSpacing(2)
        self.natural_roaming_enabled_input = QCheckBox("自然摸鱼与行为噪声注入")
        self.natural_roaming_enabled_input.setChecked(bool(active.get("natural_roaming_enabled", True)))
        self.natural_roaming_enabled_input.setToolTip("开启后，在批次间或周期性自动切回 X 首页【为你推荐】摸鱼闲逛 15~25 秒，打乱持续搜索的单一轨迹，彻底打破图灵风控检测。")
        self._register_inspector_hint(self.natural_roaming_enabled_input, "natural_roaming")
        natural_roaming_hint = QLabel("💡 批次间切回首页摸鱼 15~25 秒，打乱单一轨迹，打破图灵风控", objectName="dialogHint")
        natural_roaming_hint.setStyleSheet("color: #0284c7; font-size: 11px;")
        natural_roaming_hint.setWordWrap(True)
        natural_roaming_vbox.addWidget(self.natural_roaming_enabled_input)
        natural_roaming_vbox.addWidget(natural_roaming_hint)
        safety_grid.addWidget(natural_roaming_box, 7, 1)

        pre_click_guard_box = QWidget()
        pre_click_guard_vbox = QVBoxLayout(pre_click_guard_box)
        pre_click_guard_vbox.setContentsMargins(0, 0, 0, 0)
        pre_click_guard_vbox.setSpacing(2)
        self.pre_click_guard_enabled_input = QCheckBox("推文卡片前置指标快速质检")
        self.pre_click_guard_enabled_input.setChecked(bool(active.get("pre_click_guard_enabled", True)))
        self.pre_click_guard_enabled_input.setToolTip("开启后，在鼠标点击进主页前，快速在推文卡片层识别默认头像、纯数字乱码号及纯外链推文，毫秒级跳过，100% 节省单日宝贵互动配额。")
        self._register_inspector_hint(self.pre_click_guard_enabled_input, "pre_click_guard")
        pre_click_guard_hint = QLabel("💡 进主页前毫秒级拦截默认头像与数字乱码号，零耗时节省配额", objectName="dialogHint")
        pre_click_guard_hint.setStyleSheet("color: #0284c7; font-size: 11px;")
        pre_click_guard_hint.setWordWrap(True)
        pre_click_guard_vbox.addWidget(self.pre_click_guard_enabled_input)
        pre_click_guard_vbox.addWidget(pre_click_guard_hint)
        safety_grid.addWidget(pre_click_guard_box, 7, 2)

        # Row 8: 最新流自愈 (跨列整行，最新流深滚翻页自愈，带小字标明好处)
        safety_grid.addWidget(_make_safety_label("最新流自愈"), 8, 0)
        search_pagination_box = QWidget()
        search_pagination_vbox = QVBoxLayout(search_pagination_box)
        search_pagination_vbox.setContentsMargins(0, 0, 0, 0)
        search_pagination_vbox.setSpacing(2)
        self.search_pagination_refresh_enabled_input = QCheckBox("最新流深滚翻页自愈机制（防深滚卡死/空白）")
        self.search_pagination_refresh_enabled_input.setChecked(bool(active.get("search_pagination_refresh_enabled", True)))
        self.search_pagination_refresh_enabled_input.setToolTip("开启后，在「最新」推文流中当向下深滚超过8~10屏或出现断流时，自动平滑重置回顶部重新拉取，解决加载卡死与空白，源源不断捕获新鲜推文。")
        self._register_inspector_hint(self.search_pagination_refresh_enabled_input, "search_pagination_refresh")
        search_pagination_hint = QLabel("💡 连续深滚达限或断流时自动平滑回顶重载最新流，解决推文加载变慢与接口空白，持续捕获刚出炉的最新推文", objectName="dialogHint")
        search_pagination_hint.setStyleSheet("color: #0284c7; font-size: 11px;")
        search_pagination_hint.setWordWrap(True)
        search_pagination_vbox.addWidget(self.search_pagination_refresh_enabled_input)
        search_pagination_vbox.addWidget(search_pagination_hint)
        safety_grid.addWidget(search_pagination_box, 8, 1, 1, 2)

        # Row 9: 底层数据流透视预筛 (实验性功能·可选开关，默认关闭)
        safety_grid.addWidget(_make_safety_label("底层数据透视"), 9, 0)
        graphql_scout_box = QWidget()
        graphql_scout_vbox = QVBoxLayout(graphql_scout_box)
        graphql_scout_vbox.setContentsMargins(0, 0, 0, 0)
        graphql_scout_vbox.setSpacing(2)
        self.graphql_scout_filter_enabled_input = QCheckBox("底层数据流极速透视预筛（实验性功能）")
        self.graphql_scout_filter_enabled_input.setChecked(bool(active.get("graphql_scout_filter_enabled", True)))
        self.graphql_scout_filter_enabled_input.setToolTip("开启后，自动挂接推特底层 SearchTimeline 响应流，在内存中瞬间对推文作者粉丝数、关注比、注册天数、Bio 进行全量质检与打分，提前淘汰垃圾营销号与无效页面跳转。（默认开启，自由选择关闭）")
        self._register_inspector_hint(self.graphql_scout_filter_enabled_input, "graphql_scout_filter")
        graphql_scout_hint = QLabel("💡 零额外发包，直接从推特底层数据包毫秒级预筛博主档案，跳过90%垃圾号与主页跳转（默认开启，自由选择关闭）", objectName="dialogHint")
        graphql_scout_hint.setStyleSheet("color: #0284c7; font-size: 11px;")
        graphql_scout_hint.setWordWrap(True)
        graphql_scout_vbox.addWidget(self.graphql_scout_filter_enabled_input)
        graphql_scout_vbox.addWidget(graphql_scout_hint)
        safety_grid.addWidget(graphql_scout_box, 9, 1, 1, 2)

        # Row 10: 真人新人智能识别 (自定义开启，吸纳带 7~8 位系统随机数字的真实小白新手)
        safety_grid.addWidget(_make_safety_label("新人智能识别"), 10, 0)
        smart_newbie_box = QWidget()
        smart_newbie_vbox = QVBoxLayout(smart_newbie_box)
        smart_newbie_vbox.setContentsMargins(0, 0, 0, 0)
        smart_newbie_vbox.setSpacing(2)
        self.smart_newbie_recognition_enabled_input = QCheckBox("🌱 真人新人智能识别（吸纳带系统随机数字的高价值真实新手）")
        self.smart_newbie_recognition_enabled_input.setChecked(bool(active.get("smart_newbie_recognition_enabled", True)))
        self.smart_newbie_recognition_enabled_input.setToolTip("推特官方为新注册用户默认生成含 7~8 位随机数字的用户名（如 @Name12345678）。开启后，系统不会粗暴淘汰数字后缀，而是综合研判其是否拥有正常生活头像与原创推文，智能吸纳高互动率的初级新手真人，仅在重叠默认头像或营销广告时进行拦截。")
        self._register_inspector_hint(self.smart_newbie_recognition_enabled_input, "smart_newbie_recognition")
        smart_newbie_hint = QLabel("💡 结合头像与原创推文智能识别小白新手，放行高回关率潜力号，仅拦截无头像水军", objectName="dialogHint")
        smart_newbie_hint.setStyleSheet("color: #0284c7; font-size: 11px;")
        smart_newbie_hint.setWordWrap(True)
        smart_newbie_vbox.addWidget(self.smart_newbie_recognition_enabled_input)
        smart_newbie_vbox.addWidget(smart_newbie_hint)
        safety_grid.addWidget(smart_newbie_box, 10, 1, 1, 2)

        # Row 11: 极速省流防卡 (自定义开启，阻断大体积视频与HLS流切片加载，降低约60%内存与显存)
        safety_grid.addWidget(_make_safety_label("视频流阻断"), 11, 0)
        block_video_box = QWidget()
        block_video_vbox = QVBoxLayout(block_video_box)
        block_video_vbox.setContentsMargins(0, 0, 0, 0)
        block_video_vbox.setSpacing(2)
        self.block_video_streams_input = QCheckBox("⚡ 阻断非必要大体积视频流（降 60% 内存，防多开卡屏）")
        self.block_video_streams_input.setChecked(bool(active.get("block_video_streams", True)))
        self.block_video_streams_input.setToolTip("开启后，在底层网络与CDP协议直接阻断 video.twimg.com 及 mp4/m3u8 视频切片下载。不影响博主头像、图片和文字展示，立竿见影降低约 60% 内存与显存消耗，彻底防止多窗口卡顿卡死。（默认开启，可自由切换）")
        self._register_inspector_hint(self.block_video_streams_input, "block_video_streams")
        block_video_hint = QLabel("💡 静默阻断视频流缓冲与播放，保留博主头像与图文，降低约60%内存与显存占用，多开流畅不卡顿（默认勾选开启）", objectName="dialogHint")
        block_video_hint.setStyleSheet("color: #0284c7; font-size: 11px;")
        block_video_hint.setWordWrap(True)
        block_video_vbox.addWidget(self.block_video_streams_input)
        block_video_vbox.addWidget(block_video_hint)
        safety_grid.addWidget(block_video_box, 11, 1, 1, 2)

        safety_layout.addLayout(safety_grid)

        advanced_limits = QFrame(objectName="advancedInteractionLimits")
        advanced_limits_layout = QVBoxLayout(advanced_limits)
        advanced_limits_layout.setContentsMargins(0, 0, 0, 0)
        advanced_limits_layout.setSpacing(8)
        self.advanced_interaction_toggle = QToolButton(objectName="advancedInteractionToggle")
        self.advanced_interaction_toggle.setText("高级互动限制（可选，默认不限）")
        self.advanced_interaction_toggle.setCheckable(True)
        self.advanced_interaction_toggle.setChecked(False)
        self.advanced_interaction_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_interaction_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_interaction_toggle.setAccessibleName("展开高级互动限制")
        self._register_inspector_hint(self.advanced_interaction_toggle, "advanced_limits")
        advanced_limits_layout.addWidget(self.advanced_interaction_toggle)

        self.advanced_interaction_content = QWidget()
        self.advanced_interaction_content.setVisible(False)
        self.advanced_interaction_content.setMaximumHeight(0)
        self.advanced_interaction_content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        advanced_content_layout = QVBoxLayout(self.advanced_interaction_content)
        advanced_content_layout.setContentsMargins(10, 4, 10, 10)
        advanced_content_layout.setSpacing(10)

        permissions = QWidget()
        permissions_layout = QGridLayout(permissions)
        permissions_layout.setContentsMargins(0, 0, 0, 0)
        permissions_layout.setHorizontalSpacing(16)
        permissions_layout.setVerticalSpacing(8)
        self.action_permission_inputs: dict[str, QCheckBox] = {}
        default_allowed = {"like": True, "follow": True, "reply": False, "bookmark": False}
        for index, (action, label) in enumerate((("like", "点赞"), ("follow", "关注"), ("reply", "回复"), ("bookmark", "收藏"))):
            checkbox = QCheckBox(label)
            checkbox.setChecked(bool(active.get(f"allow_{action}", default_allowed.get(action, False))))
            self.action_permission_inputs[action] = checkbox
            permissions_layout.addWidget(checkbox, index // 3, index % 3)
        permission_row = QWidget()
        permission_layout = QFormLayout(permission_row)
        permission_layout.setContentsMargins(0, 0, 0, 0)
        permission_layout.setHorizontalSpacing(20)
        permission_layout.setVerticalSpacing(0)
        permission_layout.addRow("允许操作", permissions)
        advanced_content_layout.addWidget(permission_row)

        retweet_settings = QWidget()
        retweet_layout = QHBoxLayout(retweet_settings)
        retweet_layout.setContentsMargins(0, 0, 0, 0)
        retweet_layout.setSpacing(10)
        self.allow_retweet_input = QCheckBox("允许转推")
        self.allow_retweet_input.setChecked(bool(active.get("allow_retweet", False)))
        self.allow_retweet_input.setAccessibleName("允许转推")
        self.allow_retweet_input.setToolTip("关闭后，自动化不会执行转推；已设置的概率会保留。")
        self.retweet_ratio_input = self._ratio_spin(active.get("retweet_ratio"), 0.10)
        self.retweet_ratio_input.setAccessibleName("转推触发概率")
        self.retweet_ratio_input.setToolTip("每个符合条件的条目触发转推的概率，范围 0.00 到 1.00。")
        retweet_layout.addWidget(self.allow_retweet_input)
        retweet_layout.addWidget(QLabel("触发概率", objectName="compactFieldLabel"))
        retweet_layout.addWidget(self.retweet_ratio_input)
        retweet_layout.addStretch(1)
        retweet_row = QWidget()
        retweet_form = QFormLayout(retweet_row)
        retweet_form.setContentsMargins(0, 0, 0, 0)
        retweet_form.setHorizontalSpacing(20)
        retweet_form.setVerticalSpacing(0)
        retweet_form.addRow("转推策略", retweet_settings)
        advanced_content_layout.addWidget(retweet_row)
        self.allow_retweet_input.toggled.connect(self._set_retweet_probability_enabled)
        self._set_retweet_probability_enabled(self.allow_retweet_input.isChecked())

        self.daily_like_limit_input = self._spin(active.get("daily_likes_limit"), 0, 0, 100_000)
        self.daily_follow_limit_input = self._spin(active.get("daily_follows_limit"), 0, 0, 100_000)
        self.daily_reply_limit_input = self._spin(active.get("daily_replies_limit"), 0, 0, 100_000)
        self.daily_bookmark_limit_input = self._spin(active.get("daily_bookmarks_limit"), 0, 0, 100_000)
        self.daily_retweet_limit_input = self._spin(active.get("daily_retweets_limit"), 0, 0, 100_000)
        for widget in (self.daily_like_limit_input, self.daily_follow_limit_input, self.daily_reply_limit_input, self.daily_bookmark_limit_input, self.daily_retweet_limit_input):
            widget.setSpecialValueText("不限制")
        action_limits = QWidget()
        action_limits_layout = QGridLayout(action_limits)
        action_limits_layout.setContentsMargins(0, 0, 0, 0)
        action_limits_layout.setHorizontalSpacing(10)
        action_limits_layout.setVerticalSpacing(8)
        for index, (label, widget) in enumerate((
            ("点赞", self.daily_like_limit_input),
            ("关注", self.daily_follow_limit_input),
            ("回复", self.daily_reply_limit_input),
            ("收藏", self.daily_bookmark_limit_input),
            ("转推", self.daily_retweet_limit_input),
        )):
            row = index // 2
            column = (index % 2) * 2
            label_widget = QLabel(label, objectName="compactFieldLabel")
            action_limits_layout.addWidget(label_widget, row, column)
            action_limits_layout.addWidget(widget, row, column + 1)
        limit_row = QWidget()
        limit_layout = QFormLayout(limit_row)
        limit_layout.setContentsMargins(0, 0, 0, 0)
        limit_layout.setHorizontalSpacing(20)
        limit_layout.setVerticalSpacing(0)
        limit_layout.addRow("每日互动上限", action_limits)
        advanced_content_layout.addWidget(limit_row)

        safety_hint = QLabel("“单日任务上限”是当天处理条目的主限制。此处每种互动上限仅作可选的二次保险，不会替代主限制。")
        safety_hint.setWordWrap(True)
        safety_hint.setObjectName("dialogHint")
        advanced_content_layout.addWidget(safety_hint)

        advanced_hint = QLabel("默认全部为“不限制”。填写大于 0 的数值后才启用对应的二次上限；已保存的原有数值会保持不变。")
        advanced_hint.setWordWrap(True)
        advanced_hint.setObjectName("dialogHint")
        advanced_content_layout.addWidget(advanced_hint)
        advanced_limits_layout.addWidget(self.advanced_interaction_content)
        self.advanced_interaction_toggle.toggled.connect(self._set_advanced_interaction_limits_visible)
        has_custom_advanced = any(
            active.get(f"allow_{action}") is False
            for action in ("like", "follow", "reply", "bookmark", "retweet")
        ) or any(
            int(active.get(f"daily_{action}s_limit") or 0) > 0
            for action in ("like", "follow", "replie", "bookmark", "retweet")
        )
        self.advanced_interaction_toggle.setChecked(bool(has_custom_advanced))
        self._set_advanced_interaction_limits_visible(bool(has_custom_advanced))
        safety_layout.addWidget(advanced_limits)

        # 智能说明坞 (Inspector Dock)
        inspector_dock = QFrame(objectName="inspectorDockA")
        inspector_dock.setStyleSheet("""
            QFrame#inspectorDockA {
                background: #F0F9FF;
                border: 1px solid #BAE6FD;
                border-radius: 8px;
            }
            QFrame#inspectorDockA QLabel {
                border: none;
                background: transparent;
            }
        """)
        dock_layout = QHBoxLayout(inspector_dock)
        dock_layout.setContentsMargins(14, 10, 14, 10)
        dock_layout.setSpacing(10)

        dock_icon = QLabel("💡")
        dock_icon.setStyleSheet("font-size: 18px;")
        dock_layout.addWidget(dock_icon)

        dock_text_vbox = QVBoxLayout()
        dock_text_vbox.setContentsMargins(0, 0, 0, 0)
        dock_text_vbox.setSpacing(3)
        self.dock_title_label = QLabel("当前项说明（鼠标滑过或点击任意项自动切换讲解）：")
        self.dock_title_label.setStyleSheet("color: #0284C7; font-size: 12px; font-weight: 600;")
        self.dock_desc_label = QLabel("【高频爆发降频冷却】：连续多次关注后原地小憩 90~150 秒，模拟人类生理喘息，打破机器脚本高频特征，防封效果极佳。")
        self.dock_desc_label.setStyleSheet("color: #0369A1; font-size: 11px;")
        self.dock_desc_label.setWordWrap(True)
        dock_text_vbox.addWidget(self.dock_title_label)
        dock_text_vbox.addWidget(self.dock_desc_label)
        dock_layout.addLayout(dock_text_vbox, 1)

        safety_layout.addWidget(inspector_dock)
        safety_layout.addStretch(1)

        dialog_footer = QFrame(objectName="dialogFooter")
        footer_layout = QHBoxLayout(dialog_footer)
        footer_layout.setContentsMargins(24, 8, 24, 10)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if self.ok_button:
            self.ok_button.setText("保存并运行")
            self.ok_button.setObjectName("dialogPrimaryButton")
        if self.cancel_button:
            self.cancel_button.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer_layout.addStretch(1)
        footer_layout.addWidget(buttons)
        shell.addWidget(dialog_footer)

        self._update_schedule_controls()

    def _register_inspector_hint(self, widget: QWidget, key: str) -> None:
        widget._inspector_key = key
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Enter, QEvent.Type.FocusIn):
            key = getattr(watched, "_inspector_key", None)
            if key and hasattr(self, "dock_title_label") and hasattr(self, "dock_desc_label"):
                hint = SAFETY_HINTS.get(key)
                if hint:
                    title, desc = hint
                    self.dock_title_label.setText(f"当前项说明：{title}")
                    self.dock_desc_label.setText(desc)
        return super().eventFilter(watched, event)

    def accept(self) -> None:
        mode = str(self.outreach_mode_input.currentData() or "keyword")
        if mode in ("keyword", "keyword_chain"):
            kw = self.keyword_input.text().strip()
            if not kw and getattr(self, "_prompt_on_empty_keyword", True) and not os.environ.get("PYTEST_CURRENT_TEST"):
                btn_action = "启动" if self.schedule_mode_input.currentData() == "immediate" else "保存"
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("未填写检索关键词")
                msg_box.setIcon(QMessageBox.Icon.Question)
                msg_box.setText(
                    "<b>您当前未填写目标检索关键词。</b><br><br>"
                    "• <b>如果留空直接启动</b>：系统将自动浏览 X 首页【为你推荐 (For You)】热门推文流进行全真拟人互动拓客。<br>"
                    "• <b>如果需要精准获客</b>：建议输入目标业务关键词（如：Web3、AI、行业话题标签等，支持逗号分隔多词）。<br><br>"
                    f"是否确认留空并直接{btn_action}？"
                )
                btn_fill = msg_box.addButton("✍️ 去填写关键词", QMessageBox.ButtonRole.RejectRole)
                btn_proceed = msg_box.addButton(f"🚀 直接{btn_action}", QMessageBox.ButtonRole.AcceptRole)
                msg_box.setDefaultButton(btn_fill)
                msg_box.exec()
                if msg_box.clickedButton() != btn_proceed:
                    if hasattr(self, "config_tabs"):
                        self.config_tabs.setCurrentIndex(0)
                    self.keyword_input.setFocus()
                    return

        if self.schedule_mode_input.currentData() == "scheduled" and self.schedule_type_input.currentData() == "once":
            now_dt = QDateTime.currentDateTime()
            if self.scheduled_at_input.dateTime() <= now_dt:
                adjusted = now_dt.addSecs(60)
                self.scheduled_at_input.setDateTime(adjusted)
        super().accept()

    def _set_advanced_interaction_limits_visible(self, visible: bool) -> None:
        self.advanced_interaction_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )
        self.advanced_interaction_toggle.setAccessibleName(
            "收起高级互动限制" if visible else "展开高级互动限制"
        )
        self.advanced_interaction_content.setVisible(visible)
        if visible:
            self.advanced_interaction_content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            self.advanced_interaction_content.setMaximumHeight(16777215)
        else:
            self.advanced_interaction_content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
            self.advanced_interaction_content.setMaximumHeight(0)

    def _set_retweet_probability_enabled(self, allowed: bool) -> None:
        self.retweet_ratio_input.setEnabled(bool(allowed))
        self.retweet_ratio_input.setAccessibleDescription(
            "转推已关闭，触发概率不可编辑" if not allowed else "转推触发概率，范围 0.00 到 1.00"
        )

    def _update_chain_hop_controls(self) -> None:
        if not hasattr(self, "chain_hop_enabled_input") or not hasattr(self, "outreach_mode_input"):
            return
        mode = self.outreach_mode_input.currentData()
        if mode == "keyword_chain":
            self.chain_hop_enabled_input.blockSignals(True)
            self.chain_hop_enabled_input.setChecked(True)
            self.chain_hop_enabled_input.setEnabled(False)
            self.chain_hop_enabled_input.blockSignals(False)

            self.chain_hop_ratio_input.blockSignals(True)
            self.chain_hop_ratio_input.setValue(1.00)
            self.chain_hop_ratio_input.setEnabled(False)
            self.chain_hop_ratio_label.setEnabled(False)
            self.chain_hop_ratio_input.blockSignals(False)

            self.max_chain_depth_input.blockSignals(True)
            self.max_chain_depth_input.setValue(0)
            self.max_chain_depth_input.setEnabled(False)
            self.max_chain_depth_label.setEnabled(False)
            self.max_chain_depth_input.blockSignals(False)

            self.max_harvest_per_seed_input.setEnabled(True)
            self.max_harvest_per_seed_label.setEnabled(True)
            self.chain_hop_source_input.setEnabled(True)
            self.chain_hop_source_label.setEnabled(True)

            self.chain_hop_sub_hint.setVisible(True)
            self.chain_hop_note_label.setText("🔗 已由【无限顺藤摸瓜】模式全局接管（A➜B➜C 持续深挖直到单日配额达成，可自定义单博主采摘上限）")
            self.chain_hop_note_label.setVisible(True)
        elif mode == "target_followers":
            self.chain_hop_enabled_input.blockSignals(True)
            self.chain_hop_enabled_input.setChecked(False)
            self.chain_hop_enabled_input.setEnabled(False)
            self.chain_hop_enabled_input.blockSignals(False)

            self.chain_hop_ratio_input.setEnabled(False)
            self.chain_hop_ratio_label.setEnabled(False)
            self.max_chain_depth_input.setEnabled(False)
            self.max_chain_depth_label.setEnabled(False)
            self.max_harvest_per_seed_input.setEnabled(False)
            self.max_harvest_per_seed_label.setEnabled(False)
            self.chain_hop_source_input.setEnabled(False)
            self.chain_hop_source_label.setEnabled(False)

            self.chain_hop_sub_hint.setVisible(False)
            self.chain_hop_note_label.setText("👥 当前为【扫博主粉丝模式】，将聚焦深挖目标博主公开粉丝群")
            self.chain_hop_note_label.setVisible(True)
        elif mode == "network_hop":
            self.chain_hop_enabled_input.blockSignals(True)
            self.chain_hop_enabled_input.setChecked(True)
            self.chain_hop_enabled_input.setEnabled(False)
            self.chain_hop_enabled_input.blockSignals(False)

            self.chain_hop_ratio_input.setEnabled(True)
            self.chain_hop_ratio_label.setEnabled(True)
            self.max_chain_depth_input.setEnabled(True)
            self.max_chain_depth_label.setEnabled(True)
            self.max_harvest_per_seed_input.setEnabled(True)
            self.max_harvest_per_seed_label.setEnabled(True)
            self.chain_hop_source_input.setEnabled(True)
            self.chain_hop_source_label.setEnabled(True)

            self.chain_hop_sub_hint.setVisible(True)
            self.chain_hop_note_label.setText("🌱 从账号自身列表出发，向外挖掘人脉关系网")
            self.chain_hop_note_label.setVisible(True)
        else:
            self.chain_hop_enabled_input.setEnabled(True)
            enabled = self.chain_hop_enabled_input.isChecked()
            self.chain_hop_ratio_input.setEnabled(enabled)
            self.chain_hop_ratio_label.setEnabled(enabled)
            self.max_chain_depth_input.setEnabled(enabled)
            self.max_chain_depth_label.setEnabled(enabled)
            self.max_harvest_per_seed_input.setEnabled(enabled)
            self.max_harvest_per_seed_label.setEnabled(enabled)
            self.chain_hop_source_input.setEnabled(enabled)
            self.chain_hop_source_label.setEnabled(enabled)
            self.chain_hop_sub_hint.setVisible(enabled)
            self.chain_hop_note_label.setVisible(False)

    @staticmethod
    def _scrollable_form() -> tuple[QScrollArea, QFormLayout]:
        surface = QFrame(objectName="dialogFormSurface")
        form = QFormLayout(surface)
        form.setContentsMargins(24, 4, 24, 4)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(4)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        scroll = QScrollArea(objectName="taskConfigScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(surface)
        return scroll, form

    def set_engines(self, engines: list[dict[str, Any]] | None, selected_engine: str | None = None) -> None:
        current = self.engine_input.currentData() or {}
        target_id = str(selected_engine or current.get("engine_id") or "default")
        if engines is not None:
            self._engine_choices = [dict(item) for item in engines if isinstance(item, dict)]
        choices = self._engine_choices or [{"engine_id": "default", "name": "默认自动化引擎", "description": "内置 x_automation_engine.py"}]
        authorization_enforced = any(bool(item.get("authorization_enforced")) for item in choices)
        selected_is_available = any(str(item.get("engine_id") or "") == target_id for item in choices)
        if target_id and not selected_is_available and not authorization_enforced:
            choices = [{"engine_id": target_id, "name": str(current.get("engine_name") or target_id)}] + list(choices)
        if authorization_enforced and target_id and not selected_is_available:
            notice = "已保存的自动化引擎已不再获管理员授权，已切换为可用配置；请保存后再运行。"
        else:
            notice = ""
        self.engine_access_notice.setText(notice)
        self.engine_access_notice.setVisible(bool(notice))
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
        raw_targets = active.get("target_creators") or []
        if isinstance(raw_targets, (list, tuple, set)):
            self.target_creators_input.setPlainText("\n".join(str(t) for t in raw_targets if t))
        else:
            self.target_creators_input.setPlainText(str(raw_targets or ""))
        try:
            self.max_followers_per_target_input.setValue(
                50 if active.get("max_followers_per_target") is None else int(active.get("max_followers_per_target"))
            )
        except (TypeError, ValueError):
            self.max_followers_per_target_input.setValue(50)
        try:
            self.action_min_delay_input.setValue(
                4.0 if active.get("action_min_delay") is None else float(active.get("action_min_delay"))
            )
        except (TypeError, ValueError):
            self.action_min_delay_input.setValue(4.0)
        try:
            self.action_max_delay_input.setValue(
                8.0 if active.get("action_max_delay") is None else float(active.get("action_max_delay"))
            )
        except (TypeError, ValueError):
            self.action_max_delay_input.setValue(8.0)
        for widget, key, default in (
            (self.daily_limit_input, "daily_task_limit", 100),
            (self.batch_interval_input, "batch_interval_minutes", 15),
            (self.follower_limit_input, "max_follower_threshold", 150),
            (self.statuses_limit_input, "max_statuses_threshold", 10_000),
            (self.engagement_limit_input, "max_engagement_threshold", 10_000),
            (self.daily_like_limit_input, "daily_likes_limit", 0),
            (self.daily_follow_limit_input, "daily_follows_limit", 0),
            (self.daily_reply_limit_input, "daily_replies_limit", 0),
            (self.daily_bookmark_limit_input, "daily_bookmarks_limit", 0),
            (self.daily_retweet_limit_input, "daily_retweets_limit", 0),
        ):
            try:
                widget.setValue(default if active.get(key) is None else int(active.get(key)))
            except (TypeError, ValueError):
                widget.setValue(default)
        self._set_ai_reply_ratio(active.get("ai_reply_ratio", 0.0))
        self._set_ratio_spin(self.profile_visit_ratio_input, active.get("profile_visit_ratio"), 0.80)
        mode_val = str(active.get("outreach_mode") or "keyword").lower()
        mode_idx = self.outreach_mode_input.findData(mode_val)
        self.outreach_mode_input.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
        preset_val = str(active.get("execution_preset") or active.get("speed_mode") or "safe").lower()
        preset_idx = self.execution_preset_input.findData(preset_val)
        self.execution_preset_input.setCurrentIndex(preset_idx if preset_idx >= 0 else 0)
        self.dry_run_input.setChecked(bool(active.get("dry_run", False)))
        self.filter_verified_input.setChecked(bool(active.get("filter_verified_accounts", True)))
        self.filter_parody_input.setChecked(bool(active.get("filter_parody_accounts", True)))
        self.filter_bot_input.setChecked(bool(active.get("filter_bot_accounts", True)))
        if hasattr(self, "filter_blacklist_input"):
            self.filter_blacklist_input.setChecked(bool(active.get("blacklist_filter_enabled", True)))
        self.batch_jitter_enabled_input.setChecked(bool(active.get("batch_jitter_enabled", True)))
        self.warmup_ramp_enabled_input.setChecked(bool(active.get("warmup_ramp_enabled", False)))
        self.follow_burst_cooling_enabled_input.setChecked(bool(active.get("follow_burst_cooling_enabled", True)))
        self.soft_landing_enabled_input.setChecked(bool(active.get("enable_soft_landing", active.get("soft_landing_enabled", True))))
        self.cold_start_warmup_enabled_input.setChecked(bool(active.get("cold_start_warmup_enabled", True)))
        try:
            min_s = int(active.get("cold_start_warmup_min_seconds", 45) if active.get("cold_start_warmup_min_seconds") is not None else 45)
            self.cold_start_warmup_min_input.setValue(min_s)
        except (TypeError, ValueError):
            self.cold_start_warmup_min_input.setValue(45)
        try:
            raw_max = active.get("cold_start_warmup_max_seconds") or active.get("cold_start_warmup_duration_seconds")
            max_s = int(raw_max if raw_max is not None else 60)
            self.cold_start_warmup_max_input.setValue(max_s)
        except (TypeError, ValueError):
            self.cold_start_warmup_max_input.setValue(60)
        is_warmup_on = self.cold_start_warmup_enabled_input.isChecked()
        self.cold_start_warmup_min_input.setEnabled(is_warmup_on)
        self.cold_start_warmup_sep_label.setEnabled(is_warmup_on)
        self.cold_start_warmup_max_input.setEnabled(is_warmup_on)
        self.casual_tweet_enabled_input.setChecked(bool(active.get("casual_tweet_enabled", False)))
        self.casual_tweet_topic_input.setText(str(active.get("casual_tweet_topic") or ""))
        self.casual_tweet_topic_input.setEnabled(self.casual_tweet_enabled_input.isChecked())
        self.smart_unfollow_enabled_input.setChecked(bool(active.get("smart_unfollow_enabled", False)))
        try:
            self.unfollow_threshold_days_input.setValue(
                5 if active.get("unfollow_threshold_days") is None else int(active.get("unfollow_threshold_days"))
            )
        except (TypeError, ValueError):
            self.unfollow_threshold_days_input.setValue(5)
        self.unfollow_threshold_days_input.setEnabled(self.smart_unfollow_enabled_input.isChecked())
        self.cdp_timezone_override_enabled_input.setChecked(bool(active.get("cdp_timezone_override_enabled", True)))
        tz_val = str(active.get("cdp_override_timezone") or "auto").strip()
        tz_idx = self.cdp_override_timezone_input.findData(tz_val)
        self.cdp_override_timezone_input.setCurrentIndex(tz_idx if tz_idx >= 0 else 0)
        self.cdp_override_timezone_input.setEnabled(self.cdp_timezone_override_enabled_input.isChecked())
        self.cdp_geolocation_override_enabled_input.setChecked(bool(active.get("cdp_geolocation_override_enabled", True)))
        self.cdp_geolocation_override_enabled_input.setEnabled(self.cdp_timezone_override_enabled_input.isChecked())
        self.graphql_risk_pause_enabled_input.setChecked(bool(active.get("graphql_risk_pause_enabled", True)))
        self.smart_schedule_enabled_input.setChecked(bool(active.get("smart_schedule_enabled", True)))
        self.periodic_search_refresh_enabled_input.setChecked(bool(active.get("periodic_search_refresh_enabled", True)))
        try:
            self.search_refresh_interval_input.setValue(
                15 if active.get("search_refresh_interval_minutes") is None else int(active.get("search_refresh_interval_minutes"))
            )
        except (TypeError, ValueError):
            self.search_refresh_interval_input.setValue(15)
        self.search_refresh_interval_label.setEnabled(self.periodic_search_refresh_enabled_input.isChecked())
        self.search_refresh_interval_input.setEnabled(self.periodic_search_refresh_enabled_input.isChecked())
        self.chain_hop_enabled_input.setChecked(bool(active.get("chain_hop_enabled", False)))
        self._set_ratio_spin(self.chain_hop_ratio_input, active.get("chain_hop_ratio"), 0.70)
        try:
            self.max_chain_depth_input.setValue(
                2 if active.get("max_chain_depth") is None else int(active.get("max_chain_depth"))
            )
        except (TypeError, ValueError):
            self.max_chain_depth_input.setValue(2)
        try:
            self.max_harvest_per_seed_input.setValue(
                10 if active.get("max_harvest_per_seed") is None else int(active.get("max_harvest_per_seed"))
            )
        except (TypeError, ValueError):
            self.max_harvest_per_seed_input.setValue(10)
        raw_source = str(active.get("chain_hop_source") or "followers").strip().lower()
        self.chain_hop_source_input.setCurrentIndex(1 if raw_source == "following" else 0)
        self._update_chain_hop_controls()
        default_allowed = {"like": True, "follow": True, "reply": False, "bookmark": False}
        for action, checkbox in self.action_permission_inputs.items():
            checkbox.setChecked(bool(active.get(f"allow_{action}", default_allowed.get(action, False))))
        self.allow_retweet_input.setChecked(bool(active.get("allow_retweet", False)))
        self._set_ratio_spin(self.retweet_ratio_input, active.get("retweet_ratio"), 0.10)
        self._set_retweet_probability_enabled(self.allow_retweet_input.isChecked())
        self._apply_schedule(active)
        st_tok = str(active.get("cloud_dedup_studio_token") or active.get("studio_token") or "")
        if st_tok:
            self.cloud_dedup_studio_token_input.setText(st_tok)
        if "cloud_dedup_enabled" in active:
            self.cloud_dedup_enabled_input.setChecked(bool(active["cloud_dedup_enabled"]))
        if hasattr(self, "natural_roaming_enabled_input"):
            self.natural_roaming_enabled_input.setChecked(bool(active.get("natural_roaming_enabled", True)))
        if hasattr(self, "pre_click_guard_enabled_input"):
            self.pre_click_guard_enabled_input.setChecked(bool(active.get("pre_click_guard_enabled", True)))
        if hasattr(self, "search_pagination_refresh_enabled_input"):
            self.search_pagination_refresh_enabled_input.setChecked(bool(active.get("search_pagination_refresh_enabled", True)))
        if hasattr(self, "graphql_scout_filter_enabled_input"):
            self.graphql_scout_filter_enabled_input.setChecked(bool(active.get("graphql_scout_filter_enabled", True)))
        if hasattr(self, "smart_newbie_recognition_enabled_input"):
            self.smart_newbie_recognition_enabled_input.setChecked(bool(active.get("smart_newbie_recognition_enabled", True)))
        if hasattr(self, "block_video_streams_input"):
            self.block_video_streams_input.setChecked(bool(active.get("block_video_streams", True)))
        # Do not discard the server/cached choices already loaded by the
        # parallel engine-list request. This used to make a published engine
        # flash briefly, then disappear when the task config callback arrived.
        self.set_engines(None, str(active.get("engine_id") or "default"))

    def _apply_schedule(self, active: dict[str, Any]) -> None:
        mode = str(active.get("schedule_mode") or "smart").lower()
        mode_index = self.schedule_mode_input.findData(mode)
        self.schedule_mode_input.setCurrentIndex(mode_index if mode_index >= 0 else 0)

        schedule_type = str(active.get("schedule_type") or "once").lower()
        type_index = self.schedule_type_input.findData(schedule_type)
        self.schedule_type_input.setCurrentIndex(type_index if type_index >= 0 else 0)

        scheduled_at = str(active.get("scheduled_at") or "").strip()
        # The scheduler is explicitly Beijing time, independent of the Windows
        # system timezone used to render this dialog.
        parsed = QDateTime.fromString(scheduled_at[:16], "yyyy-MM-ddTHH:mm") if scheduled_at else QDateTime()
        if parsed.isValid() and parsed > QDateTime.currentDateTime().addSecs(30):
            self.scheduled_at_input.setDateTime(parsed)
        else:
            self.scheduled_at_input.setDateTime(QDateTime.currentDateTime().addSecs(300))

        scheduled_time = QTime.fromString(str(active.get("scheduled_time") or ""), "HH:mm")
        if scheduled_time.isValid():
            self.scheduled_time_input.setTime(scheduled_time)
        self._update_schedule_controls()

    def _update_schedule_controls(self) -> None:
        mode = self.schedule_mode_input.currentData()
        scheduled = (mode == "scheduled")
        daily = self.schedule_type_input.currentData() == "daily"
        self.schedule_type_input.setEnabled(scheduled)
        self.scheduled_at_input.setEnabled(scheduled and not daily)
        self.scheduled_time_input.setEnabled(scheduled and daily)
        self.schedule_timezone_label.setEnabled(scheduled)

        # 动态隐藏/展示定时相关行，立即执行与智能时段下免去冗余占位，彻底消除上下滚动条
        for widget, should_show in (
            (self.schedule_type_input, scheduled),
            (self.scheduled_at_input, scheduled and not daily),
            (self.scheduled_time_input, scheduled and daily),
            (self.schedule_timezone_label, scheduled),
            (self.schedule_hint_label, scheduled),
        ):
            if hasattr(self, "run_form") and hasattr(self.run_form, "setRowVisible"):
                self.run_form.setRowVisible(widget, should_show)
            else:
                widget.setVisible(should_show)
                if hasattr(self, "run_form"):
                    lbl = self.run_form.labelForField(widget)
                    if lbl:
                        lbl.setVisible(should_show)

        if hasattr(self, "schedule_mode_tip"):
            if mode == "scheduled":
                self.schedule_mode_tip.setText("💡 自定义执行时间与周期，到点自动唤醒")
            elif mode == "smart":
                self.schedule_mode_tip.setText("💡 智能规避深夜与午休，模拟真实作息")
            else:
                self.schedule_mode_tip.setText("💡 立即启动自动化，不等待时段与排期")

        if hasattr(self, "ok_button") and self.ok_button is not None:
            if mode == "scheduled":
                self.ok_button.setText("保存定时排期")
            elif mode == "smart":
                self.ok_button.setText("保存智能时段")
            else:
                self.ok_button.setText("保存并立即运行")

        if hasattr(self, "schedule_hint_label") and scheduled:
            if daily:
                target_time = self.scheduled_time_input.time()
                now_time = QTime.currentTime()
                time_str = target_time.toString("HH:mm")
                if target_time > now_time:
                    self.schedule_hint_label.setText(f"💡 每日定时：将于今天 {time_str} 首次自动执行，之后每天准时启动。")
                else:
                    self.schedule_hint_label.setText(f"💡 每日定时：今日时间已过，将于明天 {time_str} 首次自动执行，之后每天准时启动。")
            else:
                target_dt = self.scheduled_at_input.dateTime()
                now_dt = QDateTime.currentDateTime()
                if target_dt > now_dt:
                    diff_sec = now_dt.secsTo(target_dt)
                    hours = diff_sec // 3600
                    mins = (diff_sec % 3600) // 60
                    time_str = target_dt.toString("yyyy-MM-dd HH:mm")
                    self.schedule_hint_label.setText(f"💡 单次定时：将于 {time_str} 执行（约 {hours} 小时 {mins} 分钟后），到点自动唤醒。")
                else:
                    self.schedule_hint_label.setText("⚠️ 单次定时：所选时间已早于当前时间，保存时将自动修正为未来时间。")

    def _set_ai_reply_ratio(self, value: Any) -> None:
        try:
            target = float(value)
        except (TypeError, ValueError):
            target = 0.0
        for index in range(self.ai_reply_ratio_input.count()):
            if abs(float(self.ai_reply_ratio_input.itemData(index)) - target) < 1e-9:
                self.ai_reply_ratio_input.setCurrentIndex(index)
                return
        self.ai_reply_ratio_input.setCurrentIndex(0)

    @staticmethod
    def _install_smart_suffix_filter(widget: QAbstractSpinBox) -> None:
        try:
            line_edit = widget.lineEdit()
            if line_edit:
                flt = SmartSuffixFilter(widget)
                line_edit.installEventFilter(flt)
                setattr(widget, "_smart_suffix_filter", flt)
        except Exception:
            pass

    @staticmethod
    def _spin(value: Any, default: int, minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        widget.setRange(minimum, maximum)
        widget.setMinimumHeight(36)
        try:
            widget.setValue(default if value is None else int(value))
        except (TypeError, ValueError):
            widget.setValue(default)
        TaskConfigDialog._install_smart_suffix_filter(widget)
        return widget

    @staticmethod
    def _ratio_spin(value: Any, default: float) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        widget.setRange(0.0, 1.0)
        widget.setDecimals(2)
        widget.setSingleStep(0.05)
        widget.setMinimumHeight(36)
        TaskConfigDialog._set_ratio_spin(widget, value, default)
        TaskConfigDialog._install_smart_suffix_filter(widget)
        return widget

    @staticmethod
    def _set_ratio_spin(widget: QDoubleSpinBox, value: Any, default: float) -> None:
        try:
            parsed = float(default if value is None or isinstance(value, bool) else value)
        except (TypeError, ValueError):
            parsed = float(default)
        widget.setValue(max(0.0, min(1.0, parsed)))

    def config(self) -> dict[str, Any]:
        engine = self.engine_input.currentData() or {"engine_id": "default", "engine_name": "默认自动化引擎"}
        raw_kw = self.keyword_input.text().strip()[:500]
        mode = str(self.outreach_mode_input.currentData() or "keyword")
        chain_hop_enabled = True if mode in ("keyword_chain", "network_hop") else self.chain_hop_enabled_input.isChecked()
        chain_hop_ratio = 1.0 if mode == "keyword_chain" else float(self.chain_hop_ratio_input.value())
        max_chain_depth = 0 if mode == "keyword_chain" else int(self.max_chain_depth_input.value())

        return {
            "engine_id": str(engine.get("engine_id") or "default"),
            "engine_name": str(engine.get("engine_name") or "默认自动化引擎"),
            "schedule_mode": str(self.schedule_mode_input.currentData() or "smart"),
            "schedule_type": str(self.schedule_type_input.currentData() or "once"),
            "scheduled_at": self.scheduled_at_input.dateTime().toString("yyyy-MM-ddTHH:mm:00+08:00"),
            "scheduled_time": self.scheduled_time_input.time().toString("HH:mm"),
            "schedule_timezone": "Asia/Shanghai",
            "execution_preset": str(self.execution_preset_input.currentData() or "safe"),
            "outreach_mode": mode,
            "target_creators": clean_target_creators(self.target_creators_input.toPlainText()),
            "max_followers_per_target": self.max_followers_per_target_input.value(),
            "action_min_delay": min(float(self.action_min_delay_input.value()), float(self.action_max_delay_input.value())),
            "action_max_delay": max(float(self.action_min_delay_input.value()), float(self.action_max_delay_input.value())),
            "keyword": raw_kw,
            "daily_task_limit": self.daily_limit_input.value(),
            "batch_interval_minutes": self.batch_interval_input.value(),
            "max_follower_threshold": self.follower_limit_input.value(),
            "max_statuses_threshold": self.statuses_limit_input.value(),
            "max_engagement_threshold": self.engagement_limit_input.value(),
            "ai_reply_ratio": float(self.ai_reply_ratio_input.currentData()),
            "profile_visit_ratio": float(self.profile_visit_ratio_input.value()),
            "chain_hop_enabled": chain_hop_enabled,
            "chain_hop_ratio": chain_hop_ratio,
            "max_chain_depth": max_chain_depth,
            "max_harvest_per_seed": int(self.max_harvest_per_seed_input.value()),
            "chain_hop_source": str(self.chain_hop_source_input.currentData() or "followers"),
            "sleep_on_rate_limit": True,
            "dry_run": self.dry_run_input.isChecked(),
            "warmup_ramp_enabled": self.warmup_ramp_enabled_input.isChecked(),
            "casual_tweet_enabled": self.casual_tweet_enabled_input.isChecked(),
            "casual_tweet_topic": self.casual_tweet_topic_input.text().strip()[:200],
            "smart_unfollow_enabled": self.smart_unfollow_enabled_input.isChecked(),
            "unfollow_threshold_days": self.unfollow_threshold_days_input.value(),
            "batch_jitter_enabled": self.batch_jitter_enabled_input.isChecked(),
            "smart_schedule_enabled": self.smart_schedule_enabled_input.isChecked(),
            "periodic_search_refresh_enabled": self.periodic_search_refresh_enabled_input.isChecked(),
            "search_refresh_interval_minutes": self.search_refresh_interval_input.value(),
            "authenticity_learning_enabled": self.authenticity_learning_input.isChecked() if hasattr(self, "authenticity_learning_input") else True,
            "authenticity_min_score": 60,
            "follow_burst_cooling_enabled": self.follow_burst_cooling_enabled_input.isChecked(),
            "enable_soft_landing": self.soft_landing_enabled_input.isChecked(),
            "soft_landing_enabled": self.soft_landing_enabled_input.isChecked(),
            "cold_start_warmup_enabled": self.cold_start_warmup_enabled_input.isChecked(),
            "cold_start_warmup_min_seconds": min(self.cold_start_warmup_min_input.value(), self.cold_start_warmup_max_input.value()),
            "cold_start_warmup_max_seconds": max(self.cold_start_warmup_min_input.value(), self.cold_start_warmup_max_input.value()),
            "cold_start_warmup_duration_seconds": max(self.cold_start_warmup_min_input.value(), self.cold_start_warmup_max_input.value()),
            "filter_verified_accounts": self.filter_verified_input.isChecked(),
            "filter_parody_accounts": self.filter_parody_input.isChecked(),
            "filter_bot_accounts": self.filter_bot_input.isChecked(),
            "blacklist_filter_enabled": self.filter_blacklist_input.isChecked() if hasattr(self, "filter_blacklist_input") else True,
            **{f"allow_{action}": checkbox.isChecked() for action, checkbox in self.action_permission_inputs.items()},
            "allow_retweet": self.allow_retweet_input.isChecked(),
            "retweet_ratio": float(self.retweet_ratio_input.value()),
            "daily_likes_limit": self.daily_like_limit_input.value(),
            "daily_follows_limit": self.daily_follow_limit_input.value(),
            "daily_replies_limit": self.daily_reply_limit_input.value(),
            "daily_bookmarks_limit": self.daily_bookmark_limit_input.value(),
            "daily_retweets_limit": self.daily_retweet_limit_input.value(),
            "cloud_dedup_enabled": self.cloud_dedup_enabled_input.isChecked(),
            "cloud_dedup_studio_token": self.cloud_dedup_studio_token_input.text().strip(),
            "studio_token": self.cloud_dedup_studio_token_input.text().strip(),
            "cdp_timezone_override_enabled": self.cdp_timezone_override_enabled_input.isChecked(),
            "cdp_override_timezone": str(self.cdp_override_timezone_input.currentData() or "auto"),
            "cdp_geolocation_override_enabled": self.cdp_geolocation_override_enabled_input.isChecked(),
            "graphql_risk_pause_enabled": self.graphql_risk_pause_enabled_input.isChecked(),
            "natural_roaming_enabled": self.natural_roaming_enabled_input.isChecked() if hasattr(self, "natural_roaming_enabled_input") else True,
            "pre_click_guard_enabled": self.pre_click_guard_enabled_input.isChecked() if hasattr(self, "pre_click_guard_enabled_input") else True,
            "search_pagination_refresh_enabled": self.search_pagination_refresh_enabled_input.isChecked() if hasattr(self, "search_pagination_refresh_enabled_input") else True,
            "graphql_scout_filter_enabled": self.graphql_scout_filter_enabled_input.isChecked() if hasattr(self, "graphql_scout_filter_enabled_input") else True,
            "smart_newbie_recognition_enabled": self.smart_newbie_recognition_enabled_input.isChecked() if hasattr(self, "smart_newbie_recognition_enabled_input") else True,
            "block_video_streams": self.block_video_streams_input.isChecked() if hasattr(self, "block_video_streams_input") else True,
        }


def format_default_blacklist_text() -> str:
    """Format default blacklist words with organized category comments."""
    lines = []
    for cat_name, word_list in DEFAULT_BLACKLIST_CATEGORIES.items():
        lines.append(f"# === 【{cat_name}】 ===")
        lines.extend(word_list)
        lines.append("")
    return "\n".join(lines).strip()


def format_blacklist_text_from_words(words: list[str]) -> str:
    """Format blacklist words categorized into clean commented sections."""
    lines = []
    classified: set[str] = set()
    for cat_name, cat_defaults in DEFAULT_BLACKLIST_CATEGORIES.items():
        cat_words = [w for w in words if w in cat_defaults]
        if cat_words:
            lines.append(f"# === 【{cat_name}】 ===")
            lines.extend(cat_words)
            lines.append("")
            classified.update(cat_words)

    custom_words = [w for w in words if w not in classified]
    if custom_words:
        lines.append("# === 【⭐ 用户自定义专属特征词】 ===")
        lines.extend(custom_words)
        lines.append("")

    return "\n".join(lines).strip()


class FlowLayout(QLayout):
    """Standard Qt FlowLayout for wrapping widgets dynamically based on available width."""

    def __init__(self, parent=None, margin=0, spacing=8):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._item_list: list[Any] = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self._item_list.append(item)

    def count(self):
        return len(self._item_list)

    def itemAt(self, index):
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()
        if spacing < 0:
            spacing = 8

        for item in self._item_list:
            space_x = spacing
            space_y = spacing
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y()


BLACKLIST_CATEGORY_DEFS = [
    {
        "id": "ol",
        "name": "🌸 日区假冒OL / 擦边引流 / 援交色情",
        "short_name": "🌸 假冒OL/擦边",
        "chip_bg": "#FFF5F7",
        "chip_border": "#FBCFE8",
        "chip_text": "#9D174D",
        "chip_hover_bg": "#FCE7F3",
        "badge_bg": "#FDF2F8",
        "badge_text": "#BE185D",
    },
    {
        "id": "finance",
        "name": "📈 金融灰产 / 荐股诈骗 / 杀猪盘",
        "short_name": "📈 金融诈骗",
        "chip_bg": "#FFFBEB",
        "chip_border": "#FDE68A",
        "chip_text": "#92400E",
        "chip_hover_bg": "#FEF3C7",
        "badge_bg": "#FEF3C7",
        "badge_text": "#B45309",
    },
    {
        "id": "underage",
        "name": "🔞 未成年 / 涉学生 / 涉黄灰色",
        "short_name": "🔞 未成年/涉黄",
        "chip_bg": "#FEF2F2",
        "chip_border": "#FECACA",
        "chip_text": "#991B1B",
        "chip_hover_bg": "#FEE2E2",
        "badge_bg": "#FEE2E2",
        "badge_text": "#B91C1C",
    },
    {
        "id": "custom",
        "name": "⭐ 用户自定义专属特征词",
        "short_name": "⭐ 自定义词",
        "chip_bg": "#F0F9FF",
        "chip_border": "#BAE6FD",
        "chip_text": "#0369A1",
        "chip_hover_bg": "#E0F2FE",
        "badge_bg": "#E0F2FE",
        "badge_text": "#0284C7",
    },
]


class ChipCloseBtn(QToolButton):
    """极简原生字形 × 按钮：无白底圆盘，平时与分类文字浑然一体，悬浮时浮现微红小圆光晕."""

    def __init__(self, color: str = "#BE185D", size: int = 12, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("chipCloseBtn")
        self._size = size
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ico_normal = line_icon("close", color, max(6, size - 5))
        self.ico_hover = line_icon("close", "#FFFFFF", max(6, size - 5))
        self.setIcon(self.ico_normal)
        self.setStyleSheet(f"""
            QToolButton, QToolButton#chipCloseBtn, QPushButton#chipCloseBtn {{
                background: transparent;
                border: none;
                border-radius: {size // 2}px;
                padding: 0px;
                margin: 0px;
                min-width: {size}px;
                max-width: {size}px;
                min-height: {size}px;
                max-height: {size}px;
            }}
            QToolButton:hover, QToolButton#chipCloseBtn:hover, QPushButton#chipCloseBtn:hover {{
                background: #EF4444;
                border: none;
            }}
        """)

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def enterEvent(self, event: Any) -> None:
        self.setIcon(self.ico_hover)
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self.setIcon(self.ico_normal)
        super().leaveEvent(event)


class BlacklistConfigDialog(QDialog):
    """动态全字段敏感词与黑名单过滤库配置对话框 (高密度流式气泡矩阵 + 快捷录入 + 分类分段器)."""

    def __init__(self, controller: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("敏感词与黑名单过滤库配置")
        self.setModal(True)
        self.setMinimumWidth(720)
        self.setMinimumHeight(500)
        self.resize(840, 660)
        self.setSizeGripEnabled(True)

        current_cfg = self.controller.get_blacklist_config() if hasattr(self.controller, "get_blacklist_config") else {
            "enabled": True,
            "blacklist_words": list(DEFAULT_BLACKLIST_WORDS),
            "check_options": dict(DEFAULT_CHECK_OPTIONS),
        }
        is_bl_enabled = bool(current_cfg.get("enabled", True))
        raw_words = current_cfg.get("blacklist_words")
        if raw_words is None or set(raw_words) == set(DEFAULT_BLACKLIST_WORDS):
            self._words = list(DEFAULT_BLACKLIST_WORDS)
        else:
            self._words = list(raw_words)

        self._active_filter = "all"
        self._is_text_mode = False

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        # 1. 顶部 Header
        dialog_header = QFrame(objectName="dialogHeader")
        d_layout = QVBoxLayout(dialog_header)
        d_layout.setContentsMargins(24, 14, 24, 12)
        d_layout.setSpacing(3)
        d_layout.addWidget(QLabel("🛡️ 敏感词与黑名单过滤库（一票否决制）", objectName="dialogTitle"))
        d_layout.addWidget(
            QLabel("无论在推文流、顺藤摸瓜还是扫博主粉丝模式下，任何一个勾选字段命中敏感词，立即一票否决跳过，严禁触发关注或点赞！", objectName="dialogDescription")
        )
        shell.addWidget(dialog_header)

        # 2. 中部核心交互区
        content_widget = QWidget()
        c_layout = QVBoxLayout(content_widget)
        c_layout.setContentsMargins(20, 14, 20, 12)
        c_layout.setSpacing(10)

        # 行 1: 总开关 + 3 个全字段勾选
        switch_row = QHBoxLayout()
        switch_row.setContentsMargins(0, 0, 0, 0)
        switch_row.setSpacing(16)

        self.blacklist_enabled_cb = QCheckBox("启用敏感词与黑名单过滤（一键总开关）")
        self.blacklist_enabled_cb.setStyleSheet("font-size: 13px; font-weight: bold; color: #0f172a;")
        self.blacklist_enabled_cb.setChecked(is_bl_enabled)
        self.blacklist_enabled_cb.setToolTip("开启后：执行全字段特征词扫描，命中一票否决跳过；关闭后：全局停用敏感词过滤，0 拦截。")

        check_opts = current_cfg.get("check_options") or dict(DEFAULT_CHECK_OPTIONS)
        self.check_name_cb = QCheckBox("检查名字与 ID")
        self.check_name_cb.setChecked(bool(check_opts.get("check_name", True)))
        self.check_name_cb.setToolTip("对候选用户的显示昵称和 @ 用户名进行敏感词扫描")

        self.check_bio_cb = QCheckBox("检查个人简介 (Bio)")
        self.check_bio_cb.setChecked(bool(check_opts.get("check_bio", True)))
        self.check_bio_cb.setToolTip("对候选用户的个人资料简介进行敏感词扫描")

        self.check_tweets_cb = QCheckBox("检查最新推文")
        self.check_tweets_cb.setChecked(bool(check_opts.get("check_tweets", True)))
        self.check_tweets_cb.setToolTip("深入候选用户主页核验其最新发布的推文正文")

        switch_row.addWidget(self.blacklist_enabled_cb)
        switch_row.addSpacing(12)
        switch_row.addWidget(self.check_name_cb)
        switch_row.addWidget(self.check_bio_cb)
        switch_row.addWidget(self.check_tweets_cb)
        switch_row.addStretch(1)
        c_layout.addLayout(switch_row)

        # 行 2: 快捷录入栏 (Quick Add Bar) - 高质感 Modern Card
        add_card = QFrame()
        add_card.setStyleSheet("background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 8px;")
        add_layout = QHBoxLayout(add_card)
        add_layout.setContentsMargins(12, 6, 8, 6)
        add_layout.setSpacing(10)

        add_icon = QLabel("⚡ 快捷录入:")
        add_icon.setStyleSheet("font-weight: 700; color: #2563EB; font-size: 13px;")
        add_layout.addWidget(add_icon)

        self.quick_add_input = QLineEdit()
        self.quick_add_input.setPlaceholderText("输入敏感词（支持逗号或空格分隔多个词），按 Enter 回车键即可秒级录入并自动排序...")
        self.quick_add_input.setStyleSheet("""
            QLineEdit {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                color: #1E293B;
            }
            QLineEdit:focus {
                background: #FFFFFF;
                border: 2px solid #3B82F6;
            }
        """)
        self.quick_add_input.returnPressed.connect(self._on_add_words)
        add_layout.addWidget(self.quick_add_input, 1)

        self.quick_add_btn = QPushButton("＋ 添加 (Enter)")
        self.quick_add_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB;
                color: white;
                font-weight: 700;
                font-size: 12px;
                border-radius: 6px;
                padding: 7px 18px;
                border: none;
            }
            QPushButton:hover {
                background: #1D4ED8;
            }
        """)
        self.quick_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quick_add_btn.clicked.connect(self._on_add_words)
        add_layout.addWidget(self.quick_add_btn)

        c_layout.addWidget(add_card)

        # 行 3: 分类胶囊切换器 (Pills Filter Bar) + 视图切换按钮
        pills_bar = QHBoxLayout()
        pills_bar.setContentsMargins(0, 0, 0, 0)
        pills_bar.setSpacing(6)

        self.pill_buttons: dict[str, QPushButton] = {}
        pill_items = [("all", "全部词条")] + [(c["id"], c["short_name"]) for c in BLACKLIST_CATEGORY_DEFS]
        for pid, name in pill_items:
            btn = QPushButton(name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, target=pid: self._set_active_filter(target))
            pills_bar.addWidget(btn)
            self.pill_buttons[pid] = btn

        pills_bar.addStretch(1)

        self.mode_toggle_btn = QPushButton("📝 批量文本模式")
        self.mode_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_toggle_btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF;
                color: #334155;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 600;
                padding: 5px 12px;
            }
            QPushButton:hover {
                background: #F1F5F9;
                color: #0F172A;
                border-color: #94A3B8;
            }
        """)
        self.mode_toggle_btn.clicked.connect(self._toggle_view_mode)
        pills_bar.addWidget(self.mode_toggle_btn)

        c_layout.addLayout(pills_bar)

        # 行 4: 气泡流展示面板 (Tag Matrix Area)
        self.matrix_scroll = QScrollArea(objectName="blacklistConfigScroll")
        self.matrix_scroll.setWidgetResizable(True)
        self.matrix_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.matrix_scroll.setStyleSheet("QScrollArea#blacklistConfigScroll { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; }")

        self.matrix_surface = QWidget()
        self.matrix_scroll.setWidget(self.matrix_surface)
        c_layout.addWidget(self.matrix_scroll, 1)

        # 隐藏的纯文本编辑器（批量模式下使用，并用于与单元测试完全兼容）
        self.words_editor = QPlainTextEdit()
        self.words_editor.setPlaceholderText("每行或以逗号分隔一个敏感词/特征词，支持 # 注释行，例如：\n# === 自定义 ===\n注目すべき銘柄\nAI関連")
        self.words_editor.setVisible(False)
        self.words_editor.setStyleSheet("font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; border: 1px solid #CBD5E1; border-radius: 8px; padding: 10px;")
        c_layout.addWidget(self.words_editor, 1)

        self._sync_words_to_editor()

        # 行 5: 快捷操作与统计工具栏
        tools_layout = QHBoxLayout()
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(10)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("color: #64748B; font-size: 12px; font-weight: 500;")
        tools_layout.addWidget(self.summary_label)
        tools_layout.addStretch(1)

        for btn_name, cb in [
            ("恢复出厂特征词", self._reset_to_defaults),
            ("清空自定义词", self._clear_custom),
            ("清空全部词库", self._clear_all),
        ]:
            tb = QPushButton(btn_name)
            tb.setStyleSheet("""
                QPushButton {
                    background: #FFFFFF;
                    border: 1px solid #CBD5E1;
                    border-radius: 6px;
                    color: #475569;
                    font-size: 11px;
                    padding: 4px 10px;
                    min-height: 24px;
                }
                QPushButton:hover {
                    background: #F8FAFC;
                    color: #0F172A;
                    border-color: #94A3B8;
                }
            """)
            tb.clicked.connect(cb)
            tools_layout.addWidget(tb)

        c_layout.addLayout(tools_layout)

        # 行 6: 提示栏
        self.hint_label = QLabel("💡 默认预设包含常见日区杀猪盘/高风险股票特征词。只要命中任意一个词，立即标记为跳过，严禁触发关注或点赞。配置持久化保存至 config.json，下次启动自动恢复。")
        self.hint_label.setWordWrap(True)
        self.hint_label.setObjectName("dialogHint")
        c_layout.addWidget(self.hint_label)

        def _on_master_toggle(enabled: bool) -> None:
            self.check_name_cb.setEnabled(enabled)
            self.check_bio_cb.setEnabled(enabled)
            self.check_tweets_cb.setEnabled(enabled)
            self.quick_add_input.setEnabled(enabled)
            self.quick_add_btn.setEnabled(enabled)
            self.matrix_scroll.setEnabled(enabled)
            self.words_editor.setEnabled(enabled)
            for btn in self.pill_buttons.values():
                btn.setEnabled(enabled)
            self.mode_toggle_btn.setEnabled(enabled)
            if enabled:
                self.hint_label.setText("💡 敏感词过滤已开启：只要命中任意特征词，立即标记为一票否决跳过，严禁触发关注或点赞。配置持久化保存至 config.json。")
                self.hint_label.setStyleSheet("color: #475467; font-size: 11px;")
            else:
                self.hint_label.setText("⚪ 【一键停用中】敏感词过滤当前已关闭：所有自动化任务将不再核验敏感词，全面放开关注与互动！如需重新启用请勾选上方总开关。")
                self.hint_label.setStyleSheet("color: #d97706; font-size: 11px; font-weight: bold;")

        self.blacklist_enabled_cb.toggled.connect(_on_master_toggle)
        _on_master_toggle(is_bl_enabled)

        shell.addWidget(content_widget, 1)

        # 3. 固底保存栏 (Fixed Footer)
        dialog_footer = QFrame(objectName="dialogFooter")
        footer_layout = QHBoxLayout(dialog_footer)
        footer_layout.setContentsMargins(24, 12, 24, 14)
        footer_layout.setSpacing(12)

        tip_footer = QLabel("💡 鼠标滑过特征词右侧 × 即可秒级剔除；输入框支持 Enter 键即时填入并自动排序。")
        tip_footer.setStyleSheet("color: #64748B; font-size: 11px;")
        footer_layout.addWidget(tip_footer)
        footer_layout.addStretch(1)

        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setFixedWidth(84)
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_btn)

        save_btn = QPushButton("保存配置")
        save_btn.setObjectName("primaryButton")
        save_btn.setMinimumHeight(36)
        save_btn.setFixedWidth(104)
        save_btn.clicked.connect(self._save)
        footer_layout.addWidget(save_btn)

        shell.addWidget(dialog_footer)

        self._render_matrix()

    def _categorize(self) -> dict[str, list[str]]:
        res: dict[str, list[str]] = {"ol": [], "finance": [], "underage": [], "custom": []}
        cat_values = list(DEFAULT_BLACKLIST_CATEGORIES.values())
        ol_defaults = cat_values[0] if len(cat_values) > 0 else []
        fin_defaults = cat_values[1] if len(cat_values) > 1 else []
        under_defaults = cat_values[2] if len(cat_values) > 2 else []

        for w in self._words:
            if w in ol_defaults:
                res["ol"].append(w)
            elif w in fin_defaults:
                res["finance"].append(w)
            elif w in under_defaults:
                res["underage"].append(w)
            else:
                res["custom"].append(w)
        return res

    def _create_chip(self, word: str, cat_def: dict[str, str]) -> QFrame:
        chip = QFrame()
        chip.setObjectName("keywordChip")
        chip.setFixedHeight(22)
        chip.setStyleSheet(f"""
            QFrame, QFrame#keywordChip {{
                background: {cat_def['chip_bg']};
                border: 1px solid {cat_def['chip_border']};
                border-radius: 11px;
                min-height: 20px;
                max-height: 22px;
            }}
            QFrame:hover, QFrame#keywordChip:hover {{
                border-color: {cat_def['badge_text']};
            }}
        """)
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(2)

        lbl = QLabel(word)
        lbl.setStyleSheet(f"color: {cat_def['chip_text']}; font-size: 11px; font-weight: 500; border: none; background: transparent; padding: 0; margin: 0;")
        layout.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        del_btn = ChipCloseBtn(color=cat_def['badge_text'], size=12, parent=chip)
        del_btn.setToolTip(f"剔除特征词「{word}」")
        del_btn.clicked.connect(lambda _, target=word: self._delete_word(target))
        layout.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return chip

    _create_corner_chip = _create_chip

    def _delete_word(self, target: str) -> None:
        if target in self._words:
            self._words.remove(target)
            self._sync_words_to_editor()
            self._render_matrix()

    def _on_add_words(self) -> None:
        raw = self.quick_add_input.text().strip()
        if not raw:
            return
        import re
        tokens = [t.strip() for t in re.split(r"[,;，；\s]+", raw) if t.strip()]
        added = False
        for t in tokens:
            if t not in self._words:
                self._words.append(t)
                added = True
        if added:
            self._words.sort()
            self._sync_words_to_editor()
            self._render_matrix()
        self.quick_add_input.clear()

    def _set_active_filter(self, filter_id: str) -> None:
        self._active_filter = filter_id
        self._render_matrix()

    def _render_matrix(self) -> None:
        # Re-apply active styles to pills
        for pid, btn in self.pill_buttons.items():
            if pid == self._active_filter:
                btn.setStyleSheet("""
                    background: #0F172A;
                    color: #FFFFFF;
                    font-weight: 700;
                    font-size: 12px;
                    border-radius: 14px;
                    padding: 5px 14px;
                    border: 1px solid #0F172A;
                """)
            else:
                btn.setStyleSheet("""
                    background: #F8FAFC;
                    color: #475569;
                    font-weight: 500;
                    font-size: 12px;
                    border-radius: 14px;
                    padding: 5px 14px;
                    border: 1px solid #E2E8F0;
                """)

        # Create brand new clean matrix_surface
        new_surface = QWidget()
        m_layout = QVBoxLayout(new_surface)
        m_layout.setContentsMargins(14, 12, 14, 12)
        m_layout.setSpacing(12)

        categorized = self._categorize()

        # Update pill counts
        total_count = len(self._words)
        self.pill_buttons["all"].setText(f"全部词条 ({total_count})")
        for cat in BLACKLIST_CATEGORY_DEFS:
            cid = cat["id"]
            cnt = len(categorized.get(cid, []))
            self.pill_buttons[cid].setText(f"{cat['short_name']} ({cnt})")

        custom_cnt = len(categorized.get("custom", []))
        factory_cnt = total_count - custom_cnt
        self.summary_label.setText(f"📊 词库统计：当前共 {total_count} 个特征词（内置出厂 {factory_cnt} 个，自定义 {custom_cnt} 个）")

        if self._active_filter == "all":
            cats_to_show = [c for c in BLACKLIST_CATEGORY_DEFS if categorized.get(c["id"])]
        else:
            cats_to_show = [c for c in BLACKLIST_CATEGORY_DEFS if c["id"] == self._active_filter]

        if not cats_to_show or all(not categorized.get(c["id"]) for c in cats_to_show):
            empty_lbl = QLabel("（当前分类暂无特征词，可在上方输入框添加新词）")
            empty_lbl.setStyleSheet("color: #94A3B8; font-size: 12px; padding: 24px 0;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            m_layout.addWidget(empty_lbl)
        else:
            for cat in cats_to_show:
                cid = cat["id"]
                words = categorized.get(cid, [])
                if not words and self._active_filter == "all":
                    continue

                group_card = QFrame()
                group_card.setStyleSheet("""
                    QFrame#groupCard {
                        background: #FFFFFF;
                        border: 1px solid #F1F5F9;
                        border-radius: 8px;
                    }
                """)
                group_card.setObjectName("groupCard")
                gc_layout = QVBoxLayout(group_card)
                gc_layout.setContentsMargins(8, 6, 8, 8)
                gc_layout.setSpacing(8)

                gh = QHBoxLayout()
                gh.setSpacing(8)
                c_title = QLabel(cat["name"])
                c_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #1E293B;")
                gh.addWidget(c_title)

                cnt_lbl = QLabel(f"{len(words)} 项")
                cnt_lbl.setStyleSheet(f"""
                    background: {cat['badge_bg']};
                    color: {cat['badge_text']};
                    font-size: 11px;
                    font-weight: 700;
                    border-radius: 10px;
                    padding: 2px 8px;
                """)
                gh.addWidget(cnt_lbl)
                gh.addStretch(1)
                gc_layout.addLayout(gh)

                flow_container = QWidget()
                flow_container.setStyleSheet("background: transparent;")
                flow = FlowLayout(flow_container, margin=0, spacing=4)
                for w in words:
                    chip = self._create_chip(w, cat)
                    flow.addWidget(chip)
                gc_layout.addWidget(flow_container)

                m_layout.addWidget(group_card)

        m_layout.addStretch(1)
        self.matrix_scroll.setWidget(new_surface)
        self.matrix_surface = new_surface

    def _sync_words_to_editor(self) -> None:
        self.words_editor.blockSignals(True)
        self.words_editor.setPlainText(format_blacklist_text_from_words(self._words))
        self.words_editor.blockSignals(False)

    def _toggle_view_mode(self) -> None:
        self._is_text_mode = not self._is_text_mode
        if self._is_text_mode:
            self._sync_words_to_editor()
            self.matrix_scroll.setVisible(False)
            self.words_editor.setVisible(True)
            self.mode_toggle_btn.setText("🏷️ 切换气泡流矩阵")
        else:
            parsed = parse_blacklist_input(self.words_editor.toPlainText())
            self._words = list(dict.fromkeys(parsed))
            self.words_editor.setVisible(False)
            self.matrix_scroll.setVisible(True)
            self.mode_toggle_btn.setText("📝 批量文本模式")
            self._render_matrix()

    def _reset_to_defaults(self) -> None:
        self.blacklist_enabled_cb.setChecked(True)
        self._words = list(DEFAULT_BLACKLIST_WORDS)
        self._sync_words_to_editor()
        self.check_name_cb.setChecked(True)
        self.check_bio_cb.setChecked(True)
        self.check_tweets_cb.setChecked(True)
        self._render_matrix()

    def _clear_custom(self) -> None:
        categorized = self._categorize()
        custom_words = set(categorized.get("custom", []))
        self._words = [w for w in self._words if w not in custom_words]
        self._sync_words_to_editor()
        self._render_matrix()

    def _clear_all(self) -> None:
        self._words = []
        self._sync_words_to_editor()
        self._render_matrix()

    def _save(self) -> None:
        enabled = self.blacklist_enabled_cb.isChecked()
        editor_text = self.words_editor.toPlainText().strip()
        expected_text = format_blacklist_text_from_words(self._words).strip()
        if self._is_text_mode or editor_text != expected_text:
            words = parse_blacklist_input(editor_text)
        else:
            words = list(self._words)

        self._words = list(dict.fromkeys(words))
        self._sync_words_to_editor()

        data = {
            "enabled": enabled,
            "blacklist_words": words,
            "check_options": {
                "check_name": self.check_name_cb.isChecked(),
                "check_bio": self.check_bio_cb.isChecked(),
                "check_tweets": self.check_tweets_cb.isChecked(),
            },
        }
        if hasattr(self.controller, "save_blacklist_config"):
            self.controller.save_blacklist_config(data)
        state_text = "已启用" if enabled else "已停用"
        QMessageBox.information(
            self,
            "保存成功",
            f"敏感词过滤已成功设置为【{state_text}】（共 {len(words)} 个特征词）！\n配置已持久化保存至 config.json，所有自动化任务即刻生效。",
        )
        self.accept()


class TelegramConfigDialog(QDialog):
    """Telegram 机器人自动化战报设置对话框 (支持统一官方机器人与私人ID绑定)."""

    def __init__(self, controller: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Telegram 机器人通知设置")
        self.setModal(True)
        self.setMinimumWidth(560)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        dialog_header = QFrame(objectName="dialogHeader")
        d_layout = QVBoxLayout(dialog_header)
        d_layout.setContentsMargins(24, 20, 24, 18)
        d_layout.setSpacing(4)
        d_layout.addWidget(QLabel("Telegram 机器人通知配置", objectName="dialogTitle"))
        d_layout.addWidget(
            QLabel("绑定 Telegram 机器人，当账号达成当日任务或手动汇总时，私密接收战报", objectName="dialogDescription")
        )
        shell.addWidget(dialog_header)

        form_surface = QFrame(objectName="dialogFormSurface")
        form = QFormLayout(form_surface)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(14)

        # 读取当前配置
        cfg = self.controller.get_telegram_config() if hasattr(self.controller, "get_telegram_config") else {}

        self.enabled_checkbox = QCheckBox("启用 Telegram 战报通知")
        self.enabled_checkbox.setChecked(bool(cfg.get("enabled", False)))
        form.addRow("", self.enabled_checkbox)

        self.bot_mode_combo = QComboBox()
        self.bot_mode_combo.setMinimumHeight(32)
        self.bot_mode_combo.addItem("🤖 使用官方统一机器人（推荐 · 零申请开箱即用）", "official")
        self.bot_mode_combo.addItem("⚙️ 使用自定义私有机器人（适合团队/大客户）", "custom")
        use_custom = bool(cfg.get("use_custom_bot", False))
        self.bot_mode_combo.setCurrentIndex(1 if use_custom else 0)
        form.addRow("机器人来源", self.bot_mode_combo)

        self.bot_token_input = QLineEdit(str(cfg.get("bot_token") or ""))
        self.bot_token_input.setPlaceholderText("例如：6872345678:AAH...")
        self.bot_token_input.setMinimumHeight(32)
        self.bot_token_row_label = QLabel("私有 Bot Token")
        form.addRow(self.bot_token_row_label, self.bot_token_input)

        self.chat_id_input = QLineEdit(str(cfg.get("chat_id") or ""))
        self.chat_id_input.setPlaceholderText("例如：123456789 (您的 Telegram 数字 ID)")
        self.chat_id_input.setMinimumHeight(32)
        form.addRow("您的 Chat ID", self.chat_id_input)

        tip_label = QLabel(
            "💡 提示：Telegram 规定须先与机器人互动一次。请在 Telegram 搜索 @xchengxutz_bot 并点击底部的【Start】(开始)。"
        )
        tip_label.setStyleSheet("color: #64748B; font-size: 12px;")
        form.addRow("", tip_label)

        self.proxy_input = QLineEdit(str(cfg.get("proxy_url") or ""))
        self.proxy_input.setPlaceholderText("选填：开TUN模式请留空；v2rayN填 socks5://127.0.0.1:10808 或 http://127.0.0.1:10809")
        self.proxy_input.setMinimumHeight(32)
        form.addRow("网络代理", self.proxy_input)

        proxy_tip = QLabel("💡 代理说明：开启 v2rayN 的 TUN 虚拟网卡模式时可直接留空直连；常规模式可填 socks5 或 http 代理。")
        proxy_tip.setStyleSheet("color: #94A3B8; font-size: 11px;")
        form.addRow("", proxy_tip)

        self.notify_on_finish_cb = QCheckBox("单账号完成当日指标时自动发送战报")
        self.notify_on_finish_cb.setChecked(bool(cfg.get("notify_on_account_finish", True)))
        self.notify_on_risk_alert_cb = QCheckBox("触发账号风控拦截/人机验证时第一时间告警（默认开启）")
        self.notify_on_risk_alert_cb.setChecked(bool(cfg.get("notify_on_risk_alert", True)))

        auto_notify_box = QWidget()
        auto_notify_vbox = QVBoxLayout(auto_notify_box)
        auto_notify_vbox.setContentsMargins(0, 0, 0, 0)
        auto_notify_vbox.setSpacing(6)
        auto_notify_vbox.addWidget(self.notify_on_finish_cb)
        auto_notify_vbox.addWidget(self.notify_on_risk_alert_cb)
        form.addRow("自动通知", auto_notify_box)

        shell.addWidget(form_surface)

        def _update_mode_visibility():
            is_cust = self.bot_mode_combo.currentData() == "custom"
            self.bot_token_input.setVisible(is_cust)
            self.bot_token_row_label.setVisible(is_cust)

        self.bot_mode_combo.currentIndexChanged.connect(_update_mode_visibility)
        _update_mode_visibility()

        footer = QFrame(objectName="dialogFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 16, 24, 16)
        footer_layout.setSpacing(12)

        self.test_button = QPushButton("🔔 发送测试消息")
        self.test_button.setMinimumHeight(34)
        self.test_button.clicked.connect(self._send_test)
        footer_layout.addWidget(self.test_button)

        footer_layout.addStretch(1)

        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(34)
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_btn)

        save_btn = QPushButton("保存配置")
        save_btn.setObjectName("primaryButton")
        save_btn.setMinimumHeight(34)
        save_btn.clicked.connect(self._save)
        footer_layout.addWidget(save_btn)

        shell.addWidget(footer)

    def _send_test(self):
        chat_id = self.chat_id_input.text().strip()
        if not chat_id:
            QMessageBox.warning(self, "提示", "请先输入您的 Telegram Chat ID！")
            return
        use_custom = self.bot_mode_combo.currentData() == "custom"
        token = self.bot_token_input.text().strip()
        proxy = self.proxy_input.text().strip()

        self.test_button.setEnabled(False)
        self.test_button.setText("正在发送…")
        QApplication.processEvents()

        ok, msg = self.controller.send_telegram_test_message(
            chat_id=chat_id,
            bot_token=token,
            proxy_url=proxy,
            use_custom=use_custom,
        )
        self.test_button.setEnabled(True)
        self.test_button.setText("🔔 发送测试消息")

        if ok:
            QMessageBox.information(self, "成功", "测试消息已成功送达您的 Telegram！请检查手机。")
        else:
            QMessageBox.warning(self, "发送失败", f"消息推送失败：\n\n{msg}")

    def _save(self):
        chat_id = self.chat_id_input.text().strip()
        is_enabled = self.enabled_checkbox.isChecked()
        if is_enabled and not chat_id:
            QMessageBox.warning(self, "提示", "开启 Telegram 通知时，Chat ID 不能为空！")
            return

        cfg_data = {
            "enabled": is_enabled,
            "use_custom_bot": self.bot_mode_combo.currentData() == "custom",
            "bot_token": self.bot_token_input.text().strip(),
            "chat_id": chat_id,
            "proxy_url": self.proxy_input.text().strip(),
            "notify_on_account_finish": self.notify_on_finish_cb.isChecked(),
            "notify_on_all_finish": True,
            "notify_on_risk_alert": self.notify_on_risk_alert_cb.isChecked(),
        }
        if hasattr(self.controller, "save_telegram_config"):
            self.controller.save_telegram_config(cfg_data)
        self.accept()


class StudioTokenConfigDialog(QDialog):
    """工作室协同码 (Studio Token) 跨设备防重复去重配置对话框."""

    def __init__(self, controller: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("工作室协同码配置")
        self.setModal(True)
        self.setMinimumWidth(560)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        dialog_header = QFrame(objectName="dialogHeader")
        d_layout = QVBoxLayout(dialog_header)
        d_layout.setContentsMargins(24, 20, 24, 18)
        d_layout.setSpacing(4)
        d_layout.addWidget(QLabel("👥 工作室协同码配置 (Studio Token)", objectName="dialogTitle"))
        d_layout.addWidget(
            QLabel("多台电脑客户端填入同一协同码，即可实现跨设备实时去重，彻底杜绝团队多号撞车", objectName="dialogDescription")
        )
        shell.addWidget(dialog_header)

        form_surface = QFrame(objectName="dialogFormSurface")
        form = QFormLayout(form_surface)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(14)

        cfg = self.controller.get_studio_token_config() if hasattr(self.controller, "get_studio_token_config") else {}

        self.enabled_checkbox = QCheckBox("启用跨设备协同去重（工作室防撞车模式）")
        self.enabled_checkbox.setChecked(bool(cfg.get("enabled", bool(cfg.get("studio_token")))))
        form.addRow("", self.enabled_checkbox)

        self.token_input = QLineEdit(str(cfg.get("studio_token") or ""))
        self.token_input.setPlaceholderText("粘贴从 Web 管理后台获取的工作室协同码，如：std_xxxxxxxx")
        self.token_input.setMinimumHeight(32)
        form.addRow("工作室协同码", self.token_input)

        self.server_input = QLineEdit(str(cfg.get("server_url") or "https://api.jaycwl.org"))
        self.server_input.setPlaceholderText("默认：https://api.jaycwl.org")
        self.server_input.setMinimumHeight(32)
        form.addRow("云端服务地址", self.server_input)

        hint = QLabel("💡 开启后，各电脑自动化在关注或点赞博主前，会自动向云端去重池申请临时独占锁；建联成功后自动确认，避免团队不同电脑上的账号同时打扰同一位博主。")
        hint.setWordWrap(True)
        hint.setObjectName("dialogHint")
        form.addRow(hint)

        shell.addWidget(form_surface, 1)

        btn_box = QHBoxLayout()
        btn_box.setContentsMargins(24, 16, 24, 20)
        btn_box.addStretch(1)

        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(36)
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(cancel_btn)

        save_btn = QPushButton("保存配置")
        save_btn.setObjectName("primaryButton")
        save_btn.setMinimumHeight(36)
        save_btn.clicked.connect(self._save)
        btn_box.addWidget(save_btn)

        shell.addLayout(btn_box)

    def _save(self) -> None:
        token = self.token_input.text().strip()
        enabled = self.enabled_checkbox.isChecked()
        if enabled and not token:
            QMessageBox.warning(self, "请填写协同码", "开启跨设备协同去重时，必须填写工作室协同码。")
            return

        data = {
            "enabled": enabled,
            "studio_token": token,
            "server_url": self.server_input.text().strip() or "https://api.jaycwl.org",
        }
        if hasattr(self.controller, "save_studio_token_config"):
            self.controller.save_studio_token_config(data)
        QMessageBox.information(self, "保存成功", "工作室协同码已成功保存并生效！所有账号将默认共享此协同防撞车池。")
        self.accept()


class AgentReauthDialog(QDialog):
    """重新认证 Agent Token 对话框"""

    def __init__(self, agent_id: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        existing_agent_id = str(agent_id).strip()
        first_activation = not bool(existing_agent_id)
        title = "激活运行端" if first_activation else "重新认证运行端"
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(520)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        dialog_header = QFrame(objectName="dialogHeader")
        dialog_header_layout = QVBoxLayout(dialog_header)
        dialog_header_layout.setContentsMargins(24, 20, 24, 18)
        dialog_header_layout.setSpacing(4)
        dialog_header_layout.addWidget(QLabel(title, objectName="dialogTitle"))
        description = (
            "请输入 Web 后台生成的 Agent ID 和一次性 Agent Token，完成首次设备绑定。"
            if first_activation
            else "替换失效凭据并恢复与服务器的安全连接"
        )
        dialog_header_layout.addWidget(QLabel(description, objectName="dialogDescription"))
        shell.addWidget(dialog_header)

        form_surface = QFrame(objectName="dialogFormSurface")
        form = QFormLayout(form_surface)
        form.setContentsMargins(24, 20, 24, 20)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(12)
        shell.addWidget(form_surface)

        self.agent_id_input = QLineEdit(existing_agent_id)
        self.agent_id_input.setReadOnly(not first_activation)
        self.agent_id_input.setMinimumHeight(32)
        self.agent_id_input.setToolTip(
            "从 Web 后台复制服务器签发的 Agent ID"
            if first_activation
            else "Agent ID 已完成设备绑定，不能在控制中心修改"
        )
        self.agent_id_input.setPlaceholderText("从 Web 后台复制 Agent ID")
        form.addRow("Agent ID", self.agent_id_input)

        self.agent_token_input = QLineEdit()
        self.agent_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.agent_token_input.setMinimumHeight(32)
        self.agent_token_input.setPlaceholderText("粘贴新生成的 Agent Token")
        form.addRow("Agent Token", self.agent_token_input)

        hint_text = (
            "凭据保存后将通过 Windows DPAPI 加密保存；首次激活后 Agent ID 会锁定在本机。"
            if first_activation
            else "凭据保存后将通过加密传输，验证通过后系统自动恢复联机。"
        )
        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setObjectName("dialogHint")
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_button:
            save_button.setText("保存并验证")
            save_button.setObjectName("dialogPrimaryButton")
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
        on_select: Callable[..., None],
        on_run: Callable[[str], None],
        on_stop: Callable[[str], None],
        on_config: Callable[[str], None],
        on_delete: Callable[[str], None] | None = None,
        on_toggle_select: Callable[[str, bool], None] | None = None,
        on_cancel_schedule: Callable[[str], None] | None = None,
        on_run_now: Callable[[str], None] | None = None,
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

        auto_running = bool(getattr(record, "automation_running", False))
        running = bool(record.runtime_running and record.runtime_debug_ready) or auto_running
        sched_mode = str(getattr(record, "schedule_mode", "") or "smart")
        sched_status = str(getattr(record, "schedule_status", "") or "")
        sched_next = str(getattr(record, "schedule_next_run", "") or "")
        sched_type = str(getattr(record, "schedule_type", "") or "once")
        is_scheduled_waiting = (sched_mode == "scheduled" and sched_status in {"WAITING_SCHEDULE", "DELAYED_PROFILE_BUSY"})
        is_triggering = (sched_mode == "scheduled" and sched_status == "TRIGGERING")

        self.checkbox = QCheckBox()
        self.checkbox.setObjectName("accountCheckbox")
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkbox.setToolTip("选中/取消选中（支持多选批量操作）")
        if on_toggle_select is not None:
            self.checkbox.toggled.connect(lambda checked: on_toggle_select(self.profile_id, checked))
        top_row.addWidget(self.checkbox)

        dot = QLabel("●")
        if auto_running:
            dot.setObjectName("onlineDot")
            dot.setToolTip("自动化正在运行中")
        elif is_triggering or is_scheduled_waiting:
            dot.setObjectName("scheduleDot")
            dot.setToolTip(f"定时任务排期中 (预计执行: {sched_next or '到点自动运行'})")
        elif running:
            dot.setObjectName("onlineDot")
            dot.setToolTip("浏览器运行中")
        else:
            dot.setObjectName("offlineDot")
            dot.setToolTip("浏览器已停止")
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
        proxy_label = record.proxy_name or record.proxy_id
        proxy_endpoint = ":".join(part for part in (record.proxy_host, record.proxy_port) if part)
        proxy_text = f" · 代理 {proxy_label}" if proxy_label else ""
        if proxy_endpoint:
            proxy_text += f" ({proxy_endpoint})"
        sub_info = QLabel(f"{handle_str} · {login_str}{account_id}{proxy_text}")
        sub_info.setObjectName("accountHandle")
        sub_info.setToolTip(f"Profile ID: {record.profile_id}")
        
        info.addWidget(name)
        info.addWidget(sub_info)
        top_row.addLayout(info, 1)

        if auto_running:
            state_text = "⚡ 自动化运行中"
            state_obj = "tagAutoRunning"
            state_tip = "当前档案正在后台运行自动化拓客"
        elif is_triggering:
            state_text = "⏳ 到点启动中…"
            state_obj = "tagScheduled"
            state_tip = "定时任务已到期，正在拉起浏览器并启动自动化"
        elif is_scheduled_waiting:
            time_str = ""
            if "T" in sched_next:
                time_str = sched_next.split("T")[-1][:5]
            elif ":" in sched_next:
                time_str = sched_next[:5]
            if sched_type == "daily":
                state_text = f"⏱️ 每日定时 ({time_str})" if time_str else "⏱️ 每日定时"
            else:
                state_text = f"⏱️ 等待定时 ({time_str})" if time_str else "⏱️ 等待定时"
            state_obj = "tagScheduled"
            state_tip = f"定时任务排期中，预计执行时间：{sched_next}，到点自动唤醒"
        elif running:
            state_text = "浏览器运行中"
            state_obj = "tagRunning"
            state_tip = "浏览器已打开"
        else:
            state_text = "已停止"
            state_obj = "tagStopped"
            state_tip = "档案未运行"

        state = QLabel(state_text)
        state.setObjectName(state_obj)
        state.setToolTip(state_tip)
        top_row.addWidget(state)

        if is_scheduled_waiting or is_triggering:
            cancel_cb = on_cancel_schedule if on_cancel_schedule is not None else on_stop
            run_cb = on_run_now if on_run_now is not None else on_run
            button_specs = [
                ("立即执行", "miniRunButton", run_cb, "play", "#059669"),
                ("取消定时", "miniStopButton", cancel_cb, "stop", "#DC2626"),
                ("配置", "miniConfigButton", on_config, "settings", "#475569"),
            ]
        else:
            button_specs = [
                ("运行", "miniRunButton", on_run, "play", "#059669"),
                ("停止", "miniStopButton", on_stop, "stop", "#DC2626"),
                ("配置", "miniConfigButton", on_config, "settings", "#475569"),
            ]
        if on_delete is not None:
            button_specs.append(("删除", "miniDeleteButton", on_delete, "trash", "#DC2626"))

        for text, object_name, callback, icon_name, icon_color in button_specs:
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
        if own_followers is None or str(own_followers).strip().lower() == "none":
            own_followers = account_stats.get("own_followers", account_stats.get("followers", "尚未读取"))
        if own_followers is None or str(own_followers).strip().lower() == "none":
            own_followers = "尚未读取"

        own_following = record.following_count
        if own_following is None or str(own_following).strip().lower() == "none":
            own_following = account_stats.get("own_following", account_stats.get("following", "尚未读取"))
        if own_following is None or str(own_following).strip().lower() == "none":
            own_following = "尚未读取"

        likes = account_stats.get("likes", 0)
        follows = account_stats.get("follows", 0)
        comments = account_stats.get("comments", 0)
        scanned_posts = account_stats.get("scanned_posts", 0)

        bottom_row = QHBoxLayout()
        stats_label = QLabel(
            f"数据：粉丝 {own_followers} · 关注 {own_following}  |  今日：赞 {likes} · 关 {follows} · 评 {comments} · 扫 {scanned_posts}"
        )
        stats_label.setObjectName("accountStats")
        bottom_row.addWidget(stats_label)
        bottom_row.addStretch(1)

        main_layout.addLayout(bottom_row)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._on_select(self.profile_id, event.modifiers())
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setObjectName("accountCard_selected" if selected else "accountCard")
        if hasattr(self, "checkbox"):
            self.checkbox.blockSignals(True)
            self.checkbox.setChecked(selected)
            self.checkbox.blockSignals(False)
        self.style().unpolish(self)
        self.style().polish(self)


class MiniAccountCardWidget(QFrame):
    """极紧凑双列矩阵单卡片组件（双拼缝合胶囊 + 底置渐变微光进度条）"""

    inspect_requested = Signal(str)

    def __init__(self, profile_id: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.profile_id = profile_id
        self.setObjectName("miniAccountCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(46)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # 顶部信息行
        top_widget = QWidget(self)
        top_widget.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(top_widget)
        hl.setContentsMargins(6, 4, 6, 2)
        hl.setSpacing(4)

        # 双拼缝合胶囊容器
        self.pill_container = QWidget(top_widget)
        self.pill_container.setStyleSheet("background: transparent;")
        pill_layout = QHBoxLayout(self.pill_container)
        pill_layout.setContentsMargins(0, 0, 0, 0)
        pill_layout.setSpacing(0)

        self.pill_label = QLabel(profile_id, self.pill_container)
        self.pill_label.setStyleSheet(
            "color: #1E40AF; background: #EEF2FF; border-radius: 9px; padding: 1px 6px; "
            "font-weight: bold; font-size: 10.5px; border: 1px solid #C7D2FE;"
        )
        pill_layout.addWidget(self.pill_label)

        self.tag_label = QLabel("", self.pill_container)
        pill_layout.addWidget(self.tag_label)

        hl.addWidget(self.pill_container)

        # 状态徽章药丸
        self.status_label = QLabel("● 待命中", top_widget)
        self.status_label.setStyleSheet(
            "color: #475569; background: #F1F5F9; border: 1px solid #CBD5E1; "
            "border-radius: 5px; padding: 1px 5px; font-size: 10px; font-weight: 600;"
        )
        hl.addWidget(self.status_label)

        hl.addStretch(1)

        # 紧凑数据胶囊（同时展示关注数量与点赞数据）
        self.stats_label = QLabel("", top_widget)
        self.stats_label.setStyleSheet(
            "color: #475569; background: #F1F5F9; border-radius: 5px; padding: 1px 5px; font-size: 10px; font-weight: 600;"
        )
        hl.addWidget(self.stats_label)

        # 定向诊断按钮
        self.inspect_btn = QPushButton(top_widget)
        self.inspect_btn.setObjectName("miniInspectButton")
        self.inspect_btn.setIcon(line_icon("search", "#64748B", 12))
        self.inspect_btn.setFixedSize(20, 20)
        self.inspect_btn.setToolTip("查看该账号定向诊断日志与状态")
        self.inspect_btn.clicked.connect(lambda: self.inspect_requested.emit(self.profile_id))
        hl.addWidget(self.inspect_btn)

        card_layout.addWidget(top_widget, 1)

        # 底部内嵌微光进度条容器（左右内缩 8px，距下边 4px，高度 4px 精确渲染）
        pbar_box = QWidget(self)
        pbar_box.setStyleSheet("background: transparent;")
        pbar_l = QHBoxLayout(pbar_box)
        pbar_l.setContentsMargins(8, 0, 8, 4)
        pbar_l.setSpacing(0)

        self.progress_bar = QProgressBar(pbar_box)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(
            "QProgressBar { background: #E2E8F0; border: none; border-radius: 2px; } "
            "QProgressBar::chunk { background: #94A3B8; border-radius: 2px; }"
        )
        pbar_l.addWidget(self.progress_bar)
        card_layout.addWidget(pbar_box)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.inspect_requested.emit(self.profile_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def update_data(
        self,
        index_str: str,
        tag: str,
        status_text: str,
        status_type: str,
        stats_text: str = "",
        progress: str = "",
        progress_pct: int = 0,
    ) -> None:
        clean_tag = tag.strip().strip("[]【】 ") if tag else ""
        self.pill_label.setText(index_str)

        if clean_tag:
            self.pill_label.setStyleSheet(
                "color: #1E40AF; background: #EEF2FF; "
                "border-top-left-radius: 9px; border-bottom-left-radius: 9px; "
                "border-top-right-radius: 0px; border-bottom-right-radius: 0px; "
                "padding: 1px 6px; font-weight: bold; font-size: 10.5px; "
                "border: 1px solid #C7D2FE; border-right: none;"
            )
            self.tag_label.setText(clean_tag)
            self.tag_label.setStyleSheet(
                "color: #92400E; background: #FEF3C7; "
                "border-top-right-radius: 9px; border-bottom-right-radius: 9px; "
                "border-top-left-radius: 0px; border-bottom-left-radius: 0px; "
                "padding: 1px 5px; font-weight: 600; font-size: 9.5px; "
                "border: 1px solid #FDE68A;"
            )
            self.tag_label.show()
        else:
            self.pill_label.setStyleSheet(
                "color: #1E40AF; background: #EEF2FF; "
                "border-radius: 9px; padding: 1px 6px; font-weight: bold; font-size: 10.5px; "
                "border: 1px solid #C7D2FE;"
            )
            self.tag_label.hide()

        if stats_text:
            self.stats_label.setText(stats_text)
            self.stats_label.show()
        else:
            self.stats_label.hide()

        # 计算进度百分比
        pct = 0
        if progress_pct is not None and int(progress_pct) > 0:
            pct = min(100, max(0, int(progress_pct)))
            if pct < 2:
                pct = 2
        else:
            prog_source = progress
            if not prog_source and stats_text:
                import re
                m = re.search(r"(\d+)\s*/\s*(\d+)", stats_text)
                if m:
                    prog_source = f"{m.group(1)}/{m.group(2)}"
            if prog_source and "/" in prog_source:
                try:
                    parts = prog_source.split("/")
                    cur_v = float(parts[0].strip())
                    tot_v = float(parts[1].strip())
                    if tot_v > 0:
                        pct = int(min(100, max(0, (cur_v / tot_v) * 100)))
                        if cur_v > 0 and pct < 2:
                            pct = 2
                except Exception:
                    pct = 0

        if pct == 0 and progress_pct:
            pct = min(100, max(0, int(progress_pct)))
            if int(progress_pct) > 0 and pct < 2:
                pct = 2

        self.progress_bar.setValue(pct)

        # 状态胶囊徽章与底置渐变微光进度条着色 (天空蓝到翡翠绿微光渐变)
        gradient_chunk_style = (
            "QProgressBar { background: #E2E8F0; border: none; border-radius: 2px; } "
            "QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38BDF8, stop:1 #10B981); border-radius: 2px; }"
        )
        if status_type == "active":
            self.setObjectName("miniAccountCard")
            self.status_label.setText(f"● {status_text}" if not status_text.startswith("●") else status_text)
            self.status_label.setStyleSheet(
                "color: #047857; background: #ECFDF5; border: 1px solid #A7F3D0; "
                "border-radius: 5px; padding: 1px 5px; font-size: 10px; font-weight: 600;"
            )
            self.progress_bar.setStyleSheet(gradient_chunk_style)
        elif status_type == "idle":
            self.setObjectName("miniAccountCard")
            self.status_label.setText(f"⏳ {status_text}" if not status_text.startswith("⏳") else status_text)
            self.status_label.setStyleSheet(
                "color: #B45309; background: #FFFBEB; border: 1px solid #FDE68A; "
                "border-radius: 5px; padding: 1px 5px; font-size: 10px; font-weight: 600;"
            )
            self.progress_bar.setStyleSheet(gradient_chunk_style)
        elif status_type == "error":
            self.setObjectName("miniAccountCard_error")
            self.status_label.setText(f"🔴 {status_text}" if not status_text.startswith("🔴") else status_text)
            self.status_label.setStyleSheet(
                "color: #B91C1C; background: #FEF2F2; border: 1px solid #FECDD3; "
                "border-radius: 5px; padding: 1px 5px; font-size: 10px; font-weight: 600;"
            )
            self.progress_bar.setStyleSheet(
                "QProgressBar { background: #FEE2E2; border: none; border-radius: 2px; } "
                "QProgressBar::chunk { background: #EF4444; border-radius: 2px; }"
            )
        else:
            self.setObjectName("miniAccountCard")
            self.status_label.setText(f"⚪ {status_text}" if not status_text.startswith("⚪") else status_text)
            self.status_label.setStyleSheet(
                "color: #475569; background: #F1F5F9; border: 1px solid #CBD5E1; "
                "border-radius: 5px; padding: 1px 5px; font-size: 10px; font-weight: 600;"
            )
            self.progress_bar.setStyleSheet(gradient_chunk_style)

        self.style().unpolish(self)
        self.style().polish(self)


class MiniLogWindow(QWidget):
    """20 账号矩阵多开悬浮看板（始终置顶、双列紧凑、多号异常聚合、定向诊断）"""

    restore_requested = Signal()
    hide_requested = Signal()
    stop_all_requested = Signal()
    exit_requested = Signal()
    retry_profile_requested = Signal(str)
    stop_profile_requested = Signal(str)
    test_proxy_requested = Signal(str)
    open_profile_log_requested = Signal(str)
    skip_profile_requested = Signal(str)
    retry_all_abnormal_requested = Signal()

    def __init__(self):
        super().__init__(None)
        self.setObjectName("miniLogWindow")
        self.setWindowTitle("老谷 账号矩阵监控")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowOpacity(1.0)
        self.setWindowIcon(application_icon())
        self.setMinimumSize(420, 200)
        self.resize(630, 230)

        self._drag_offset: QPoint | None = None
        self._resize_edges: set[str] = set()
        self._resize_margin = 10
        self._is_collapsed: bool = False
        self._semi_transparent: bool = False
        self._stay_on_top: bool = True
        self._bubble_alert: bool = True
        self._user_manually_resized: bool = False
        self._last_account_count: int = -1

        # 贴边自动隐藏（默认开启 · 药丸胶囊 + 优先红色报警提示）
        self._auto_hide_enabled: bool = True
        self._dock_edge: str | None = None  # None, "right", "top"
        self._is_dock_collapsed: bool = False
        self._dock_anim: QPropertyAnimation | None = None
        self._expanded_geometry: QRect | None = None
        self._suppress_expand_until: float = 0.0
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self._on_leave_timer_timeout)
        self._dock_pulse_timer = QTimer(self)
        self._dock_pulse_timer.setInterval(700)
        self._dock_pulse_timer.timeout.connect(self._on_dock_pulse_tick)
        self._dock_pulse_state: bool = False
        self._dock_watch_timer = QTimer(self)
        self._dock_watch_timer.setInterval(250)
        self._dock_watch_timer.timeout.connect(self._on_dock_watch_tick)

        self._cards_by_pid: dict[str, MiniAccountCardWidget] = {}
        self._records_by_pid: dict[str, AccountRow] = {}
        self._recent_logs: deque[str] = deque(maxlen=500)
        self._abnormal_pids: list[str] = []
        self._active_abnormal_pid: str | None = None
        self._active_diagnose_pid: str | None = None
        self._tray_icon: QSystemTrayIcon | None = None
        self.controller: Any | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 8, 10, 10)
        root_layout.setSpacing(0)

        # ----------------- 1. 极简微胶囊折叠态 (36px/40px) -----------------
        self.capsule_bar = QWidget(self)
        self.capsule_bar.setObjectName("miniCapsuleBar")
        self.capsule_bar.setFixedHeight(36)
        self.capsule_bar.hide()

        cap_shadow = QGraphicsDropShadowEffect(self.capsule_bar)
        cap_shadow.setBlurRadius(14)
        cap_shadow.setColor(QColor(0, 0, 0, 40))
        cap_shadow.setOffset(0, 3)
        self.capsule_bar.setGraphicsEffect(cap_shadow)

        cap_layout = QHBoxLayout(self.capsule_bar)
        cap_layout.setContentsMargins(10, 0, 8, 0)
        cap_layout.setSpacing(8)

        cap_led = QLabel("●", self)
        cap_led.setStyleSheet("color: #16A34A; font-size: 13px;")
        cap_layout.addWidget(cap_led)

        cap_title = QLabel("老谷 账号矩阵", self)
        cap_title.setStyleSheet("color: #0F172A; font-weight: bold; font-size: 12px;")
        cap_layout.addWidget(cap_title)

        self.capsule_summary_label = QLabel("● 0活跃 ⏳ 0休眠 | 触达: 0 · 赞: 0", self)
        self.capsule_summary_label.setStyleSheet("color: #475569; font-size: 11px;")
        cap_layout.addWidget(self.capsule_summary_label, 1)

        btn_cap_expand = QPushButton("展开面板 ▾", self)
        btn_cap_expand.setObjectName("miniPrimaryButton")
        btn_cap_expand.setFixedHeight(24)
        btn_cap_expand.setToolTip("展开面板：点击还原为完整多开矩阵看板")
        btn_cap_expand.clicked.connect(lambda: self.toggle_collapse(False))
        cap_layout.addWidget(btn_cap_expand)

        btn_cap_main = QPushButton("主窗口", self)
        btn_cap_main.setObjectName("miniSecondaryButton")
        btn_cap_main.setFixedHeight(24)
        btn_cap_main.setToolTip("返回主窗口：切换并打开控制中心大窗口")
        btn_cap_main.clicked.connect(self.restore_requested.emit)
        cap_layout.addWidget(btn_cap_main)

        root_layout.addWidget(self.capsule_bar)

        # ----------------- 2. 完整面板 (双列矩阵 + 报警卡 + 抽屉) -----------------
        self.full_panel = QWidget(self)
        self.full_panel.setObjectName("miniFullPanel")
        panel_shadow = QGraphicsDropShadowEffect(self.full_panel)
        panel_shadow.setBlurRadius(16)
        panel_shadow.setColor(QColor(0, 0, 0, 45))
        panel_shadow.setOffset(0, 4)
        self.full_panel.setGraphicsEffect(panel_shadow)

        panel_layout = QVBoxLayout(self.full_panel)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(6)

        # Top Header Bar
        header = QHBoxLayout()
        header.setSpacing(8)

        brand_mark = QLabel(objectName="miniBrandMark")
        brand_mark.setPixmap(application_icon().pixmap(24, 24))
        header.addWidget(brand_mark)

        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title_block.setContentsMargins(0, 0, 10, 0)
        self.title_lbl = QLabel("老谷 账号矩阵监控", objectName="miniTitle")
        self.title_lbl.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700;")
        title_block.addWidget(self.title_lbl)
        self.sub_lbl = QLabel("多账号协同运行中", objectName="miniSubtitle")
        self.sub_lbl.setStyleSheet("color: #64748B; font-size: 10px;")
        title_block.addWidget(self.sub_lbl)
        header.addLayout(title_block)

        # Mode Tab Switchers
        self.tab_matrix_btn = QPushButton("▦ 矩阵看板", self)
        self.tab_matrix_btn.setObjectName("miniTabButton")
        self.tab_matrix_btn.setCheckable(True)
        self.tab_matrix_btn.setChecked(True)
        self.tab_matrix_btn.setToolTip("矩阵看板：查看多账号双列紧凑状态卡片")
        self.tab_matrix_btn.clicked.connect(lambda: self._switch_stack_page(0))
        header.addWidget(self.tab_matrix_btn)

        self.tab_log_btn = QPushButton("📋 运行日志", self)
        self.tab_log_btn.setObjectName("miniTabButton")
        self.tab_log_btn.setCheckable(True)
        self.tab_log_btn.setChecked(False)
        self.tab_log_btn.setToolTip("运行日志：查看控制中心全局实时数据流输出")
        self.tab_log_btn.clicked.connect(lambda: self._switch_stack_page(1))
        header.addWidget(self.tab_log_btn)

        header.addStretch(1)

        # 贴边自动隐藏自定义开关 (默认开启 · 药丸样式)
        self.btn_auto_hide = QPushButton("⚡ 贴边: 开", self)
        self.btn_auto_hide.setObjectName("miniAutoHideBtn")
        self.btn_auto_hide.setCheckable(True)
        self.btn_auto_hide.setChecked(True)
        self.btn_auto_hide.setToolTip("贴边自动隐藏：已开启（默认）。拖动到屏幕最右边或最上边会自动收缩为微型药丸，鼠标移入展开，移出收缩。点击关闭。")
        self.btn_auto_hide.clicked.connect(self._toggle_auto_hide)
        header.addWidget(self.btn_auto_hide)

        # Tool buttons: 📌 置顶, 🌓 半透明, ▬ 折叠, ↗ 主窗口, × 关闭
        self.btn_pin = self._make_tool_btn("📌", "窗口置顶：点击切换是否始终保持在屏幕最顶层")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(True)
        self.btn_pin.clicked.connect(self._toggle_stay_on_top)
        header.addWidget(self.btn_pin)

        self.btn_opacity = self._make_tool_btn("🌓", "透光模式：点击切换半透明模式（鼠标移开变透亮，移入恢复实色）")
        self.btn_opacity.setCheckable(True)
        self.btn_opacity.setChecked(False)
        self.btn_opacity.clicked.connect(self._toggle_opacity_mode)
        header.addWidget(self.btn_opacity)

        self.btn_collapse = self._make_tool_btn("▬", "贴边折叠：点击立即收缩为屏幕边缘微型状态药丸")
        self.btn_collapse.clicked.connect(lambda: self._collapse_to_dock(animate=True))
        header.addWidget(self.btn_collapse)

        self.btn_restore = self._make_tool_btn("↗", "返回主窗口：还原并切换到控制中心大窗口")
        self.btn_restore.clicked.connect(self.restore_requested.emit)
        header.addWidget(self.btn_restore)

        self.exit_button = QPushButton("×", self)
        self.exit_button.setObjectName("miniWindowCloseButton")
        self.exit_button.setToolTip("完全退出：关闭悬浮看板并停止所有账号自动化任务")
        self.exit_button.setIcon(line_icon("close", "#64748B", 14))
        self.exit_button.setText("")
        self.exit_button.clicked.connect(self.exit_requested.emit)
        header.addWidget(self.exit_button)

        panel_layout.addLayout(header)

        # Global Metrics Summary Strip
        metrics_bar = QHBoxLayout()
        metrics_bar.setSpacing(6)
        self.metrics_label = QLabel("● 0活跃  ⏳ 0休眠  |  总触达 0  ·  总赞 0  ·  拦截过滤 0", self)
        self.metrics_label.setObjectName("miniMetrics")
        metrics_bar.addWidget(self.metrics_label, 1)

        self.btn_stop_all = QPushButton("停止全部", self)
        self.btn_stop_all.setObjectName("miniDangerButton")
        self.btn_stop_all.setToolTip("紧急停止：立即终止全部账号的运行任务")
        self.btn_stop_all.clicked.connect(self.show_stop_all_overlay)
        metrics_bar.addWidget(self.btn_stop_all)
        panel_layout.addLayout(metrics_bar)

        # ----------------- 异常聚合吸顶卡 (严格全宽对齐 · 紧凑双行，高度仅 48-52px) -----------------
        self.alert_widget = QWidget(self)
        self.alert_widget.setObjectName("miniAlertCard")
        alert_vbox = QVBoxLayout(self.alert_widget)
        alert_vbox.setContentsMargins(8, 4, 8, 4)
        alert_vbox.setSpacing(4)

        # 第 1 行: 标题标牌 + 异常账号选项卡 + 简要原因 + 一键重试全部 (两端严格齐平)
        alert_r1 = QHBoxLayout()
        alert_r1.setContentsMargins(0, 0, 0, 0)
        alert_r1.setSpacing(6)
        self.alert_title = QLabel("⚠️ 异常拦截", self.alert_widget)
        self.alert_title.setFixedHeight(20)
        self.alert_title.setStyleSheet("color: #991B1B; background: #FFE4E6; border: 1px solid #FCA5A5; border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;")
        alert_r1.addWidget(self.alert_title)

        self.error_tabs_widget = QWidget(self.alert_widget)
        self.error_tabs_layout = QHBoxLayout(self.error_tabs_widget)
        self.error_tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.error_tabs_layout.setSpacing(5)
        alert_r1.addWidget(self.error_tabs_widget)

        self.alert_detail_label = QLabel("", self.alert_widget)
        self.alert_detail_label.setStyleSheet("color: #7F1D1D; font-size: 10px; font-weight: 500;")
        alert_r1.addWidget(self.alert_detail_label)
        alert_r1.addStretch(1)

        self.btn_retry_all_abnormal = QPushButton(" ⚡ 一键重试全部", self.alert_widget)
        self.btn_retry_all_abnormal.setFixedHeight(20)
        self.btn_retry_all_abnormal.setStyleSheet(
            "background: #DC2626; color: #FFFFFF; font-size: 10px; font-weight: bold; border: none; border-radius: 4px; padding: 0 8px;"
        )
        self.btn_retry_all_abnormal.setToolTip("一键重试：批量重新启动所有遇到异常阻碍的账号")
        self.btn_retry_all_abnormal.clicked.connect(self.retry_all_abnormal_requested.emit)
        alert_r1.addWidget(self.btn_retry_all_abnormal)
        alert_vbox.addLayout(alert_r1)

        # 第 2 行: 5大应急处置与日志诊断按钮 100% 全宽等宽网格分布，两端严格对齐，零空白缝隙
        alert_r2 = QHBoxLayout()
        alert_r2.setContentsMargins(0, 0, 0, 0)
        alert_r2.setSpacing(5)

        self.btn_rescue_retry = QPushButton(" 单号重试", self.alert_widget)
        self.btn_rescue_retry.setIcon(line_icon("refresh", "#DC2626", 12))
        self.btn_rescue_retry.setFixedHeight(21)
        self.btn_rescue_retry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_rescue_retry.setStyleSheet(
            "background: #FFFFFF; color: #DC2626; border: 1px solid #FCA5A5; border-radius: 4px; font-size: 10px; font-weight: 600; padding: 0 4px;"
        )
        self.btn_rescue_retry.setToolTip("单号重试：重新启动当前选中的受阻账号")
        self.btn_rescue_retry.clicked.connect(self._on_rescue_retry_clicked)
        alert_r2.addWidget(self.btn_rescue_retry)

        self.btn_rescue_test_proxy = QPushButton(" 测试代理", self.alert_widget)
        self.btn_rescue_test_proxy.setIcon(line_icon("globe", "#475569", 12))
        self.btn_rescue_test_proxy.setFixedHeight(21)
        self.btn_rescue_test_proxy.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_rescue_test_proxy.setStyleSheet(
            "background: #FFFFFF; color: #475569; border: 1px solid #CBD5E1; border-radius: 4px; font-size: 10px; padding: 0 4px;"
        )
        self.btn_rescue_test_proxy.setToolTip("测试代理：检测当前账号对应代理节点的网络连通性")
        self.btn_rescue_test_proxy.clicked.connect(self._on_rescue_test_proxy_clicked)
        alert_r2.addWidget(self.btn_rescue_test_proxy)

        self.btn_rescue_skip = QPushButton(" 临时跳过", self.alert_widget)
        self.btn_rescue_skip.setIcon(line_icon("pause", "#475569", 11))
        self.btn_rescue_skip.setFixedHeight(21)
        self.btn_rescue_skip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_rescue_skip.setStyleSheet(
            "background: #FFFFFF; color: #475569; border: 1px solid #CBD5E1; border-radius: 4px; font-size: 10px; padding: 0 4px;"
        )
        self.btn_rescue_skip.setToolTip("临时跳过：挂起跳过当前账号，让其他正常账号继续执行")
        self.btn_rescue_skip.clicked.connect(self._on_rescue_skip_clicked)
        alert_r2.addWidget(self.btn_rescue_skip)

        self.btn_rescue_copy = QPushButton(" 复制全部日志", self.alert_widget)
        self.btn_rescue_copy.setIcon(line_icon("copy", "#1D4ED8", 12))
        self.btn_rescue_copy.setFixedHeight(21)
        self.btn_rescue_copy.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_rescue_copy.setStyleSheet(
            "background: #EFF6FF; color: #1D4ED8; border: 1px solid #BFDBFE; border-radius: 4px; font-size: 10px; font-weight: 600; padding: 0 4px;"
        )
        self.btn_rescue_copy.setToolTip("复制全部日志：一键复制当前及所有受阻账号的报错堆栈、代理状态及最新运行日志")
        self.btn_rescue_copy.clicked.connect(self._on_rescue_copy_clicked)
        alert_r2.addWidget(self.btn_rescue_copy)

        self.btn_rescue_diag = QPushButton(" 查看诊断", self.alert_widget)
        self.btn_rescue_diag.setIcon(line_icon("search", "#334155", 12))
        self.btn_rescue_diag.setFixedHeight(21)
        self.btn_rescue_diag.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_rescue_diag.setStyleSheet(
            "background: #FFFFFF; color: #334155; border: 1px solid #CBD5E1; border-radius: 4px; font-size: 10px; padding: 0 4px;"
        )
        self.btn_rescue_diag.setToolTip("排查诊断：进入该账号的专属报错与日志排查抽屉")
        self.btn_rescue_diag.clicked.connect(self._on_rescue_diag_clicked)
        alert_r2.addWidget(self.btn_rescue_diag)

        alert_vbox.addLayout(alert_r2)

        self.alert_widget.hide()  # hidden initially until an account reports error
        panel_layout.addWidget(self.alert_widget)

        # ----------------- 3. 内容切换 Stack -----------------
        self.stack = QStackedWidget(self)

        # Page 0: 2-column Matrix HUD (形态 1)
        matrix_scroll = QScrollArea(self)
        matrix_scroll.setObjectName("miniScrollArea")
        matrix_scroll.setWidgetResizable(True)
        matrix_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        matrix_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        matrix_scroll.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical { width: 6px; background: transparent; margin: 2px 0 2px 0; border: none; }"
            "QScrollBar::track:vertical { background: transparent; border: none; }"
            "QScrollBar::handle:vertical { background: #CBD5E1; min-height: 36px; border-radius: 3px; }"
            "QScrollBar::handle:vertical:hover { background: #94A3B8; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0px; background: transparent; border: none; }"
        )

        self.matrix_container = QWidget()
        self.matrix_container.setObjectName("miniMatrixContainer")
        self.matrix_grid = QGridLayout(self.matrix_container)
        self.matrix_grid.setContentsMargins(2, 2, 8, 2)
        self.matrix_grid.setSpacing(6)
        self.matrix_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        matrix_scroll.setWidget(self.matrix_container)
        self.stack.addWidget(matrix_scroll)

        # Page 1: Global Logs
        log_page = QWidget(self)
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(4)

        log_head = QHBoxLayout()
        log_head.addWidget(QLabel("全局实时数据流", self, styleSheet="color: #334155; font-size: 11px; font-weight: bold;"))
        log_head.addStretch(1)
        self.pause_button = QPushButton("暂停滚动", self)
        self.pause_button.setObjectName("miniSecondaryButton")
        self.pause_button.setCheckable(True)
        self.pause_button.setFixedHeight(22)
        self.pause_button.setToolTip("暂停滚动：冻结屏幕以便仔细查看日志")
        self.pause_button.toggled.connect(self._set_paused)
        log_head.addWidget(self.pause_button)
        log_layout.addLayout(log_head)

        self.log_output = QPlainTextEdit(objectName="miniLogOutput")
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(80)
        self.log_output.setPlaceholderText("等待控制中心日志…")
        self.log_output.textChanged.connect(self._scroll_to_latest)
        log_layout.addWidget(self.log_output, 1)
        self.stack.addWidget(log_page)

        # Page 2: Targeted Diagnostic Drawer (形态 3)
        diag_page = QWidget(self)
        diag_layout = QVBoxLayout(diag_page)
        diag_layout.setContentsMargins(0, 0, 0, 0)
        diag_layout.setSpacing(6)

        diag_header_row = QHBoxLayout()
        self.diag_header_label = QLabel("【单号专属报错诊断中心】", self)
        self.diag_header_label.setStyleSheet("color: #1D4ED8; font-size: 12px; font-weight: bold;")
        diag_header_row.addWidget(self.diag_header_label)
        diag_header_row.addStretch(1)

        self.btn_diag_copy_all = QPushButton("📋 复制全部日志", self)
        self.btn_diag_copy_all.setObjectName("miniSecondaryButton")
        self.btn_diag_copy_all.setFixedHeight(22)
        self.btn_diag_copy_all.setToolTip("复制全部日志：一键复制当前诊断终端的所有输出到剪贴板")
        self.btn_diag_copy_all.clicked.connect(self._on_diag_copy_all_clicked)
        diag_header_row.addWidget(self.btn_diag_copy_all)

        self.btn_diag_open_notepad = QPushButton("📂 用记事本打开该号日志", self)
        self.btn_diag_open_notepad.setObjectName("miniSecondaryButton")
        self.btn_diag_open_notepad.setFixedHeight(22)
        self.btn_diag_open_notepad.setToolTip("记事本打开：在系统文本编辑器中查看该账号完整历史日志")
        self.btn_diag_open_notepad.clicked.connect(self._on_diag_open_notepad_clicked)
        diag_header_row.addWidget(self.btn_diag_open_notepad)
        diag_layout.addLayout(diag_header_row)

        self.diag_terminal = QPlainTextEdit(objectName="miniDiagTerminal")
        self.diag_terminal.setReadOnly(True)
        self.diag_terminal.setMaximumBlockCount(50)
        diag_layout.addWidget(self.diag_terminal, 1)

        diag_actions = QHBoxLayout()
        diag_actions.setSpacing(6)

        self.btn_diag_retry = QPushButton("🔄 立即单号重试", self)
        self.btn_diag_retry.setObjectName("miniPrimaryButton")
        self.btn_diag_retry.setToolTip("立即重试：重新发起当前账号的自动化任务")
        self.btn_diag_retry.clicked.connect(self._on_diag_retry_clicked)
        diag_actions.addWidget(self.btn_diag_retry)

        self.btn_diag_skip = QPushButton("⏸️ 临时跳过此号", self)
        self.btn_diag_skip.setObjectName("miniSecondaryButton")
        self.btn_diag_skip.setToolTip("临时跳过：暂时跳过该账号，返回矩阵看板")
        self.btn_diag_skip.clicked.connect(self._on_diag_skip_clicked)
        diag_actions.addWidget(self.btn_diag_skip)

        diag_actions.addStretch(1)

        self.btn_diag_back = QPushButton("↩ 返回矩阵看板", self)
        self.btn_diag_back.setObjectName("miniSecondaryButton")
        self.btn_diag_back.setToolTip("返回看板：关闭诊断抽屉，返回多账号矩阵视图")
        self.btn_diag_back.clicked.connect(lambda: self._switch_stack_page(0))
        diag_actions.addWidget(self.btn_diag_back)
        diag_layout.addLayout(diag_actions)

        self.stack.addWidget(diag_page)

        panel_layout.addWidget(self.stack, 1)
        root_layout.addWidget(self.full_panel, 1)

        # ----------------- 边缘智能吸附状态药丸 (Dock Pill) -----------------
        self.dock_pill = QFrame(self)
        self.dock_pill.setObjectName("miniDockPill")
        self.dock_pill.hide()
        self.dock_pill.setGraphicsEffect(None)

        self.dock_pill_vbox = QVBoxLayout(self.dock_pill)
        self.dock_pill_vbox.setContentsMargins(4, 6, 4, 6)
        self.dock_pill_vbox.setSpacing(1)
        self.dock_pill_vbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.dock_pill_icon = QLabel("●", self.dock_pill)
        self.dock_pill_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dock_pill_icon.setStyleSheet("color: #22C55E; font-size: 14px; font-weight: bold; background: transparent; border: none;")
        self.dock_pill_vbox.addWidget(self.dock_pill_icon)

        self.dock_pill_label = QLabel("20号", self.dock_pill)
        self.dock_pill_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dock_pill_label.setStyleSheet("color: #FFFFFF; font-size: 10px; font-weight: bold; background: transparent; border: none;")
        self.dock_pill_vbox.addWidget(self.dock_pill_label)

        self.dock_pill_sub = QLabel("正常", self.dock_pill)
        self.dock_pill_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dock_pill_sub.setStyleSheet("color: #94A3B8; font-size: 9px; background: transparent; border: none;")
        self.dock_pill_vbox.addWidget(self.dock_pill_sub)

        def _on_dock_pill_press(e: QMouseEvent) -> None:
            if e.button() == Qt.MouseButton.LeftButton:
                self._suppress_expand_until = 0.0
                self._expand_from_dock()
                e.accept()
                return
            QFrame.mousePressEvent(self.dock_pill, e)
        self.dock_pill.mousePressEvent = _on_dock_pill_press

        def _on_dock_pill_enter(e: Any) -> None:
            if getattr(self, "_is_dock_collapsed", False):
                if time.monotonic() >= getattr(self, "_suppress_expand_until", 0.0):
                    self._expand_from_dock()
            try:
                QFrame.enterEvent(self.dock_pill, e)
            except Exception:
                pass
        self.dock_pill.enterEvent = _on_dock_pill_enter

        # ----------------- HUD 内部一体化蒙层浮层 -----------------
        self._stop_overlay = QFrame(self)
        self._stop_overlay.setObjectName("miniModalOverlay")
        self._stop_overlay.hide()

        # 点击遮罩空白区域（卡片外）平滑退出
        def _on_overlay_mouse_press(event: QMouseEvent) -> None:
            if hasattr(self, "_stop_card") and self._stop_card:
                card_geom = self._stop_card.geometry()
                if not card_geom.contains(event.position().toPoint()):
                    self.hide_stop_all_overlay()
                    event.accept()
                    return
            QFrame.mousePressEvent(self._stop_overlay, event)

        self._stop_overlay.mousePressEvent = _on_overlay_mouse_press

        # 蒙层中央安全卡片
        self._stop_card = QFrame(self._stop_overlay)
        self._stop_card.setObjectName("miniModalCard")
        self._stop_card.setFixedWidth(380)

        card_shadow = QGraphicsDropShadowEffect(self._stop_card)
        card_shadow.setBlurRadius(24)
        card_shadow.setColor(QColor(15, 23, 42, 110))
        card_shadow.setOffset(0, 8)
        self._stop_card.setGraphicsEffect(card_shadow)

        card_vbox = QVBoxLayout(self._stop_card)
        card_vbox.setContentsMargins(20, 18, 20, 18)
        card_vbox.setSpacing(10)

        # 顶行: 盾牌图标 + 标题
        modal_top = QHBoxLayout()
        modal_top.setSpacing(10)

        badge = QLabel(self._stop_card)
        badge.setFixedSize(32, 32)
        badge.setStyleSheet("background: #FEE2E2; border: 1px solid #FECDD3; border-radius: 8px;")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(line_icon("shield", "#DC2626", 18).pixmap(18, 18))
        modal_top.addWidget(badge)

        modal_title = QLabel("确认停止全部任务？", self._stop_card)
        modal_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #0F172A;")
        modal_top.addWidget(modal_title)
        modal_top.addStretch(1)
        card_vbox.addLayout(modal_top)

        # 提示文案
        modal_desc = QLabel("即将终止全部正在运行的账号浏览器及自动化触达任务。所有未完成进度均会自动安全保存。", self._stop_card)
        modal_desc.setWordWrap(True)
        modal_desc.setStyleSheet("font-size: 11px; color: #64748B; line-height: 1.4;")
        card_vbox.addWidget(modal_desc)

        # 底部操作栏: 取消 + 确认停止全部
        modal_actions = QHBoxLayout()
        modal_actions.setSpacing(10)
        modal_actions.addStretch(1)

        self.btn_modal_cancel = QPushButton("取消 (Esc)", self._stop_card)
        self.btn_modal_cancel.setObjectName("miniModalCancel")
        self.btn_modal_cancel.setFixedHeight(28)
        self.btn_modal_cancel.setToolTip("取消操作并返回当前监控窗")
        self.btn_modal_cancel.clicked.connect(self.hide_stop_all_overlay)
        modal_actions.addWidget(self.btn_modal_cancel)

        self.btn_modal_confirm = QPushButton(" 确认停止全部", self._stop_card)
        self.btn_modal_confirm.setObjectName("miniModalConfirm")
        self.btn_modal_confirm.setIcon(line_icon("stop", "#FFFFFF", 12))
        self.btn_modal_confirm.setFixedHeight(28)
        self.btn_modal_confirm.setToolTip("安全终止全部账号与浏览器任务")
        self.btn_modal_confirm.clicked.connect(self._on_stop_all_confirmed)
        modal_actions.addWidget(self.btn_modal_confirm)

        card_vbox.addLayout(modal_actions)

    @staticmethod
    def _make_tool_btn(text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("miniWindowActionButton")
        btn.setToolTip(tooltip)
        btn.setFixedSize(26, 26)
        return btn

    def set_tray_icon(self, tray: QSystemTrayIcon | None) -> None:
        self._tray_icon = tray

    def toggle_collapse(self, collapse: bool) -> None:
        self._is_collapsed = collapse
        if collapse:
            self._collapse_to_dock(animate=True)
        else:
            self._expand_from_dock(animate=True)

    def _toggle_stay_on_top(self) -> None:
        self._stay_on_top = self.btn_pin.isChecked()
        flags = self.windowFlags()
        if self._stay_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
            self.btn_pin.setToolTip("窗口置顶：已开启（始终保持在最顶层，点击取消）")
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
            self.btn_pin.setToolTip("窗口置顶：已关闭（点击开启始终置顶）")
        self.setWindowFlags(flags)
        self.show()

    def _toggle_opacity_mode(self) -> None:
        self._semi_transparent = self.btn_opacity.isChecked()
        if self._semi_transparent:
            self.setWindowOpacity(0.85)
            self.btn_opacity.setToolTip("透光模式：已开启（85%半透明，移入实色，点击关闭）")
        else:
            self.setWindowOpacity(1.0)
            self.btn_opacity.setToolTip("透光模式：已关闭（点击开启 85% 半透明透光模式）")

    def _apply_dock_pill_style(self, alert_pulse: bool = False) -> None:
        """应用边缘药丸的无边缝切边样式，彻底避免阴影导致的字体彩边与边缘露边缝隙"""
        if not hasattr(self, "dock_pill") or not self.dock_pill:
            return
        is_top = (getattr(self, "_dock_edge", None) == "top")
        err_count = len(getattr(self, "_abnormal_pids", []))

        # 彻底移除 QGraphicsDropShadowEffect，恢复 Windows 原生 ClearType 超清晰字体
        self.dock_pill.setGraphicsEffect(None)

        if err_count > 0:
            bg = "#DC2626" if not alert_pulse else "#991B1B"
            border = "#FECACA" if not alert_pulse else "#F87171"
            if is_top:
                css = f"""
                    QFrame#miniDockPill {{
                        background: {bg};
                        border-left: 1.5px solid {border};
                        border-right: 1.5px solid {border};
                        border-bottom: 1.5px solid {border};
                        border-top: none;
                        border-top-left-radius: 0px;
                        border-top-right-radius: 0px;
                        border-bottom-left-radius: 8px;
                        border-bottom-right-radius: 8px;
                    }}
                """
            else:
                css = f"""
                    QFrame#miniDockPill {{
                        background: {bg};
                        border-top: 1.5px solid {border};
                        border-bottom: 1.5px solid {border};
                        border-left: 1.5px solid {border};
                        border-right: none;
                        border-top-left-radius: 8px;
                        border-bottom-left-radius: 8px;
                        border-top-right-radius: 0px;
                        border-bottom-right-radius: 0px;
                    }}
                """
        else:
            if is_top:
                css = """
                    QFrame#miniDockPill {
                        background: #0F172A;
                        border-left: 1.5px solid #22C55E;
                        border-right: 1.5px solid #22C55E;
                        border-bottom: 1.5px solid #22C55E;
                        border-top: none;
                        border-top-left-radius: 0px;
                        border-top-right-radius: 0px;
                        border-bottom-left-radius: 8px;
                        border-bottom-right-radius: 8px;
                    }
                """
            else:
                css = """
                    QFrame#miniDockPill {
                        background: #0F172A;
                        border-top: 1.5px solid #22C55E;
                        border-bottom: 1.5px solid #22C55E;
                        border-left: 1.5px solid #22C55E;
                        border-right: none;
                        border-top-left-radius: 8px;
                        border-bottom-left-radius: 8px;
                        border-top-right-radius: 0px;
                        border-bottom-right-radius: 0px;
                    }
                """
        self.dock_pill.setStyleSheet(css)

    def _set_dock_shadow(self, color: QColor) -> None:
        pass

    def _on_dock_pulse_tick(self) -> None:
        """报错状态下的绯红光晕呼吸脉冲"""
        if not getattr(self, "_is_dock_collapsed", False) or not self._abnormal_pids:
            return
        self._dock_pulse_state = not self._dock_pulse_state
        self._apply_dock_pill_style(alert_pulse=self._dock_pulse_state)

    def _toggle_auto_hide(self) -> None:
        """自定义切换贴边自动隐藏开关"""
        self._auto_hide_enabled = not self._auto_hide_enabled
        self.btn_auto_hide.setChecked(self._auto_hide_enabled)
        if self._auto_hide_enabled:
            self.btn_auto_hide.setText("⚡ 贴边: 开")
            self.btn_auto_hide.setStyleSheet(
                "QPushButton#miniAutoHideBtn { background: #EFF6FF; color: #1D4ED8; border: 1px solid #BFDBFE; "
                "border-radius: 6px; font-size: 10px; font-weight: 600; min-height: 24px; max-height: 24px; padding: 0 8px; } "
                "QPushButton#miniAutoHideBtn:hover { background: #DBEAFE; }"
            )
            self.btn_auto_hide.setToolTip("贴边自动隐藏：已开启（默认）。拖动到屏幕最右边或最上边会自动收缩为微型药丸，鼠标移入展开，移出收缩。点击关闭。")
            self._check_edge_docking()
        else:
            self.btn_auto_hide.setText("⚡ 贴边: 关")
            self.btn_auto_hide.setStyleSheet(
                "QPushButton#miniAutoHideBtn { background: #F1F5F9; color: #64748B; border: 1px solid #CBD5E1; "
                "border-radius: 6px; font-size: 10px; font-weight: 500; min-height: 24px; max-height: 24px; padding: 0 8px; } "
                "QPushButton#miniAutoHideBtn:hover { background: #E2E8F0; }"
            )
            self.btn_auto_hide.setToolTip("贴边自动隐藏：已关闭。窗口贴近边缘不会自动收缩。点击开启。")
            if hasattr(self, "_dock_watch_timer"):
                self._dock_watch_timer.stop()
            if self._is_dock_collapsed:
                self._expand_from_dock(animate=False)
            self._dock_edge = None

    def _check_edge_docking(self) -> None:
        """检测窗口是否贴近屏幕最右边或最上边"""
        if not getattr(self, "_auto_hide_enabled", True) or getattr(self, "_is_dock_collapsed", False):
            return
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or self.screen() or QGuiApplication.primaryScreen()
        if not screen:
            return
        s_geom = screen.availableGeometry()
        cur_geom = self.geometry()

        # 计算窗口右缘、顶缘与屏幕边缘的物理像素距离（支持用户推过屏幕边缘）
        dist_right = s_geom.right() - cur_geom.right()
        near_right = (dist_right <= 40) and (cur_geom.center().x() > s_geom.center().x())

        dist_top = cur_geom.top() - s_geom.top()
        near_top = (dist_top <= 40) and (cur_geom.center().y() < s_geom.center().y())

        # 拐角处优先选距离边缘更近的一侧
        if near_top and near_right:
            if abs(dist_top) <= abs(dist_right):
                near_right = False
            else:
                near_top = False

        if near_right:
            self._dock_edge = "right"
            target_y = min(max(s_geom.top() + 10, cur_geom.y()), s_geom.bottom() - cur_geom.height() - 10)
            self._expanded_geometry = QRect(s_geom.right() - cur_geom.width() + 1, target_y, cur_geom.width(), cur_geom.height())
            self.setGeometry(self._expanded_geometry)
            if hasattr(self, "_dock_watch_timer") and not self._dock_watch_timer.isActive():
                self._dock_watch_timer.start()
        elif near_top:
            self._dock_edge = "top"
            target_x = min(max(s_geom.left() + 10, cur_geom.x()), s_geom.right() - cur_geom.width() - 10)
            self._expanded_geometry = QRect(target_x, s_geom.top(), cur_geom.width(), cur_geom.height())
            self.setGeometry(self._expanded_geometry)
            if hasattr(self, "_dock_watch_timer") and not self._dock_watch_timer.isActive():
                self._dock_watch_timer.start()
        else:
            # 远离边缘 (> 50px)，自动解除贴边
            if dist_right > 50 and dist_top > 50:
                self._dock_edge = None
                if hasattr(self, "_dock_watch_timer"):
                    self._dock_watch_timer.stop()

    def _refresh_dock_pill_ui(self) -> None:
        """刷新边缘微型状态药丸：正常态绿灯 vs 报错态优先高亮绯红呼吸脉冲"""
        if not hasattr(self, "dock_pill") or not self.dock_pill:
            return
        total = len(self._records_by_pid)
        err_count = len(self._abnormal_pids)

        is_top = (self._dock_edge == "top")
        if is_top:
            self.dock_pill_vbox.setDirection(QBoxLayout.Direction.LeftToRight)
            self.dock_pill_vbox.setContentsMargins(14, 0, 14, 0)
            self.dock_pill_vbox.setSpacing(6)
        else:
            self.dock_pill_vbox.setDirection(QBoxLayout.Direction.TopToBottom)
            self.dock_pill_vbox.setContentsMargins(4, 6, 4, 6)
            self.dock_pill_vbox.setSpacing(2)

        self._apply_dock_pill_style(alert_pulse=False)

        if err_count > 0:
            # 优先红色报警提示
            self.dock_pill_icon.setText("⚠️")
            self.dock_pill_icon.setStyleSheet("color: #FFFFFF; font-size: 13px; font-weight: bold; background: transparent; border: none;")
            if is_top:
                self.dock_pill_label.setText(f"{err_count}个账号异常阻断")
                self.dock_pill_label.setStyleSheet("color: #FFFFFF; font-size: 11px; font-weight: bold; background: transparent; border: none;")
                self.dock_pill_sub.setText("· 点击排查")
                self.dock_pill_sub.setStyleSheet("color: #FEE2E2; font-size: 10px; background: transparent; border: none;")
            else:
                self.dock_pill_label.setText(f"{err_count}异常")
                self.dock_pill_label.setStyleSheet("color: #FFFFFF; font-size: 9px; font-weight: bold; background: transparent; border: none;")
                self.dock_pill_sub.setText("需排查")
                self.dock_pill_sub.setStyleSheet("color: #FEE2E2; font-size: 8px; font-weight: bold; background: transparent; border: none;")
            self.dock_pill.setToolTip(f"🚨 老谷矩阵监控：发现 {err_count} 个账号异常阻断！\n鼠标碰触立即滑出并进入紧急修复。")
            if not self._dock_pulse_timer.isActive():
                self._dock_pulse_timer.start()
        else:
            # 正常全绿
            self.dock_pill_icon.setText("●")
            self.dock_pill_icon.setStyleSheet("color: #22C55E; font-size: 13px; font-weight: bold; background: transparent; border: none;")
            if is_top:
                self.dock_pill_label.setText(f"老谷矩阵 · {total}号" if total > 0 else "老谷矩阵监控")
                self.dock_pill_label.setStyleSheet("color: #FFFFFF; font-size: 11px; font-weight: bold; background: transparent; border: none;")
                self.dock_pill_sub.setText("协同中")
                self.dock_pill_sub.setStyleSheet("color: #94A3B8; font-size: 10px; background: transparent; border: none;")
            else:
                self.dock_pill_label.setText(f"{total}号" if total > 0 else "矩阵")
                self.dock_pill_label.setStyleSheet("color: #FFFFFF; font-size: 10px; font-weight: bold; background: transparent; border: none;")
                self.dock_pill_sub.setText("正常")
                self.dock_pill_sub.setStyleSheet("color: #94A3B8; font-size: 9px; background: transparent; border: none;")
            self.dock_pill.setToolTip(f"老谷矩阵监控：当前管理 {total} 个账号，高并发协同运行中。\n鼠标碰触立即滑出完整看板。")
            if self._dock_pulse_timer.isActive():
                self._dock_pulse_timer.stop()

        if getattr(self, "_is_dock_collapsed", False):
            screen = self.screen() or QGuiApplication.primaryScreen()
            s_geom = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
            if self._dock_edge == "right":
                pw = 56 if err_count > 0 else 48
                ph = 68
                py = min(max(s_geom.top() + 10, self.y()), s_geom.bottom() - ph - 10)
                self.setGeometry(QRect(s_geom.right() - pw + 1, py, pw, ph))
                self.dock_pill.setGeometry(0, 0, pw, ph)
            elif self._dock_edge == "top":
                pw = 240 if err_count > 0 else 220
                ph = 34
                px = min(max(s_geom.left() + 10, self.x()), s_geom.right() - pw - 10)
                self.setGeometry(QRect(px, s_geom.top(), pw, ph))
                self.dock_pill.setGeometry(0, 0, pw, ph)

    def _collapse_to_dock(self, animate: bool = True) -> None:
        """平滑收缩隐藏至边缘智能药丸态"""
        if getattr(self, "_is_dock_collapsed", False):
            return
        if hasattr(self, "_stop_overlay") and self._stop_overlay and self._stop_overlay.isVisible():
            return
        if self.stack.currentIndex() == 2:
            return

        if not getattr(self, "_dock_edge", None):
            screen = self.screen() or QGuiApplication.primaryScreen()
            s_geom = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
            cur_geom = self.geometry()
            dist_right = s_geom.right() - cur_geom.right()
            dist_top = cur_geom.top() - s_geom.top()
            self._dock_edge = "top" if dist_top <= dist_right else "right"

        if hasattr(self, "_dock_anim") and self._dock_anim and self._dock_anim.state() == QPropertyAnimation.State.Running:
            self._dock_anim.stop()

        if hasattr(self, "_dock_watch_timer"):
            self._dock_watch_timer.stop()

        self._is_dock_collapsed = True
        self._suppress_expand_until = time.monotonic() + 0.8
        if self.width() >= 400 and self.height() >= 180:
            self._expanded_geometry = self.geometry()
        self.setMinimumSize(32, 32)
        self.setMaximumSize(16777215, 16777215)
        screen = self.screen() or QGuiApplication.primaryScreen()
        s_geom = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        self._refresh_dock_pill_ui()
        self.full_panel.hide()
        self.capsule_bar.hide()
        self.dock_pill.show()

        if self._dock_edge == "right":
            pw = 56 if self._abnormal_pids else 48
            ph = 68
            base_y = self._expanded_geometry.y() if self._expanded_geometry else self.y()
            py = min(max(s_geom.top() + 10, base_y + 20), s_geom.bottom() - ph - 10)
            target_rect = QRect(s_geom.right() - pw + 1, py, pw, ph)
        else:  # "top"
            pw = 240 if self._abnormal_pids else 220
            ph = 34
            base_x = self._expanded_geometry.x() if self._expanded_geometry else self.x()
            px = min(max(s_geom.left() + 10, base_x + 20), s_geom.right() - pw - 10)
            target_rect = QRect(px, s_geom.top(), pw, ph)

        self.dock_pill.setGeometry(0, 0, target_rect.width(), target_rect.height())

        self.setGeometry(target_rect)

    def _expand_from_dock(self, animate: bool = True) -> None:
        """从边缘智能药丸平滑滑出展开为完整监控看板"""
        self._leave_timer.stop()
        if not getattr(self, "_is_dock_collapsed", False):
            return

        if hasattr(self, "_dock_anim") and self._dock_anim and self._dock_anim.state() == QPropertyAnimation.State.Running:
            self._dock_anim.stop()

        self._is_dock_collapsed = False
        target_rect = getattr(self, "_expanded_geometry", None)
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or self.screen() or QGuiApplication.primaryScreen()
        s_geom = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        # 动态计算适合当前账号数量的最佳高度
        rows = max(1, (len(self._records_by_pid) + 1) // 2)
        base_h = 120 + (54 if self._abnormal_pids else 0)
        def_h = min(540, max(210, base_h + rows * 50))
        def_w = 630

        if not target_rect or target_rect.width() < 400 or target_rect.height() < 180:
            target_rect = QRect(s_geom.right() - def_w + 1, self.y(), def_w, def_h)

        target_h = max(210, target_rect.height() if getattr(self, "_user_manually_resized", False) else def_h)
        target_w = max(420, target_rect.width() if getattr(self, "_user_manually_resized", False) else def_w)

        if self._dock_edge == "right":
            target_rect.setWidth(target_w)
            target_rect.setHeight(target_h)
            target_rect.moveRight(s_geom.right() + 1)
            target_rect.moveTop(min(max(s_geom.top() + 5, target_rect.top()), s_geom.bottom() - target_rect.height() - 5))
        elif self._dock_edge == "top":
            target_rect.setWidth(target_w)
            target_rect.setHeight(target_h)
            target_rect.moveTop(s_geom.top())
            target_rect.moveLeft(min(max(s_geom.left() + 5, target_rect.left()), s_geom.right() - target_rect.width() - 5))

        self.dock_pill.hide()
        self.full_panel.show()
        self.setMaximumSize(16777215, 16777215)
        self.setMinimumSize(420, 200)
        self.setGeometry(target_rect)

        if getattr(self, "_dock_edge", None) and hasattr(self, "_dock_watch_timer") and not self._dock_watch_timer.isActive():
            self._dock_watch_timer.start()

    def _on_dock_watch_tick(self) -> None:
        """周期性检查光标是否已离开窗口，保证贴边状态下 100% 可靠缩回"""
        if not getattr(self, "_auto_hide_enabled", True) or not getattr(self, "_dock_edge", None):
            if hasattr(self, "_dock_watch_timer"):
                self._dock_watch_timer.stop()
            return
        if getattr(self, "_is_dock_collapsed", False):
            return
        if hasattr(self, "_dock_anim") and self._dock_anim and self._dock_anim.state() == QPropertyAnimation.State.Running:
            return
        if self._drag_offset is not None or self._resize_edges:
            return
        if hasattr(self, "_stop_overlay") and self._stop_overlay and self._stop_overlay.isVisible():
            return
        if self.stack.currentIndex() == 2:
            return

        cursor_pos = QCursor.pos()
        if not self.geometry().contains(cursor_pos):
            if not self._leave_timer.isActive():
                self._leave_timer.start(350)
        else:
            if self._leave_timer.isActive():
                self._leave_timer.stop()

    def _on_leave_timer_timeout(self) -> None:
        """鼠标离开 350ms 防抖计时结束，执行收缩"""
        cursor_pos = QCursor.pos()
        if self.geometry().contains(cursor_pos):
            return
        if hasattr(self, "_stop_overlay") and self._stop_overlay and self._stop_overlay.isVisible():
            return
        if self.stack.currentIndex() == 2:
            return
        self._collapse_to_dock()

    def enterEvent(self, event: QEvent) -> None:
        self._leave_timer.stop()
        if self._semi_transparent:
            self.setWindowOpacity(1.0)
        if getattr(self, "_is_dock_collapsed", False):
            if time.monotonic() >= getattr(self, "_suppress_expand_until", 0.0):
                self._expand_from_dock()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if self._semi_transparent:
            self.setWindowOpacity(0.85)
        if not self._resize_edges and self._drag_offset is None:
            self.unsetCursor()

        # 贴边自动隐藏判定（默认开启，鼠标移开延时 350ms 自动收缩至边缘）
        if (
            getattr(self, "_auto_hide_enabled", True)
            and getattr(self, "_dock_edge", None)
            and not getattr(self, "_is_dock_collapsed", False)
            and not (hasattr(self, "_stop_overlay") and self._stop_overlay and self._stop_overlay.isVisible())
            and self.stack.currentIndex() != 2
        ):
            self._leave_timer.start(350)

        super().leaveEvent(event)

    def _switch_stack_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.tab_matrix_btn.setChecked(index == 0)
        self.tab_log_btn.setChecked(index == 1)

    def _set_paused(self, paused: bool) -> None:
        self.pause_button.setText("继续滚动" if paused else "暂停滚动")

    def _scroll_to_latest(self) -> None:
        if not self.pause_button.isChecked():
            scrollbar = self.log_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def set_connection(self, text: str, online: bool) -> None:
        pass

    def set_metrics(self, running: int, success: int, failed: int) -> None:
        self.metrics_label.setText(f"● {running}活跃  ⏳ 0休眠  |  总触达 {success}  ·  总赞 {success * 2}  ·  拦截过滤 {failed}")
        self.capsule_summary_label.setText(f"● {running}活跃 | 触达: {success} · 拦截: {failed}")

    def append_log(self, line: str) -> None:
        clean = str(line).strip()
        if clean:
            self._recent_logs.append(clean)
            if not self.pause_button.isChecked():
                self.log_output.appendPlainText(clean)
                self._scroll_to_latest()
            # If diagnose drawer is showing for an active account, append matching lines
            if self.stack.currentIndex() == 2 and self._active_diagnose_pid:
                if self._active_diagnose_pid in clean:
                    self.diag_terminal.appendPlainText(clean)

    def seed_logs(self, lines: list[str]) -> None:
        clean_lines = [str(line).strip() for line in lines if str(line).strip()]
        self._recent_logs.extend(clean_lines)
        if not self.pause_button.isChecked():
            self.log_output.setPlainText("\n".join(clean_lines))
            self._scroll_to_latest()

    def update_accounts_state(
        self,
        records: list[AccountRow],
        statistics: dict[str, Any] | None = None,
        by_account_stats: dict[str, Any] | None = None,
        recent_logs: list[str] | None = None,
    ) -> None:
        """核心差量刷新：动态渲染 20 账号卡片、智能检测异常并吸顶提醒"""
        records = records or []
        self._records_by_pid = {r.profile_id: r for r in records}
        stats = statistics or {}
        by_acct = by_account_stats or {}

        if recent_logs:
            self._recent_logs.extend(recent_logs)

        # 1. 统计活跃/休眠/异常
        active_count = 0
        idle_count = 0
        abnormal_pids: list[str] = []

        for record in records:
            pid = record.profile_id
            auto_running = bool(getattr(record, "automation_running", False))
            is_active = bool(record.runtime_running and record.runtime_debug_ready) or auto_running
            proxy_stat = str(getattr(record, "proxy_status", "") or "").upper()
            sched_status = str(getattr(record, "schedule_status", "") or "")

            is_error = proxy_stat in {"ERROR", "TIMEOUT", "DISCONNECTED"} or ("FAILED" in sched_status)
            if is_error:
                abnormal_pids.append(pid)
            elif is_active:
                active_count += 1
            else:
                idle_count += 1

        # 2. 检查是否有新异常并触发气泡提醒
        new_errors = set(abnormal_pids) - set(self._abnormal_pids)
        if new_errors and self._tray_icon and self._bubble_alert:
            sample_pid = list(new_errors)[0]
            rec = self._records_by_pid.get(sample_pid)
            p_name = rec.profile_name if rec else sample_pid
            self._tray_icon.showMessage(
                "老谷控制中心 · 异常提醒",
                f"账号 [{p_name}] 代理或连接中断，已平滑保护挂起！",
                QSystemTrayIcon.MessageIcon.Warning,
                3500,
            )

        self._abnormal_pids = abnormal_pids
        if not self._active_abnormal_pid or self._active_abnormal_pid not in abnormal_pids:
            self._active_abnormal_pid = abnormal_pids[0] if abnormal_pids else None

        # 3. 更新全局指标条与动态副标题
        total_accounts = len(records)
        if total_accounts > 0:
            self.sub_lbl.setText(f"实时管理 {total_accounts} 个账号 · 高并发协同")
        else:
            self.sub_lbl.setText("多账号协同运行中")

        # 从当前实际呈现的唯一账号记录中精确核算（确保与下方卡片 100% 绝对一致，杜绝任何外部重复计算）
        sum_follows = 0
        sum_likes = 0
        sum_target = 0
        for record in records:
            acct_keys = [record.profile_id, getattr(record, "x_account_id", ""), getattr(record, "x_username", ""), getattr(record, "profile_name", "")]
            acct_data = {}
            for k in acct_keys:
                if k and k in by_acct and isinstance(by_acct[k], dict):
                    acct_data.update(by_acct[k])
            f_val = int(acct_data.get("follows", 0) or 0)
            l_val = int(acct_data.get("likes", 0) or 0)
            p_val = int(acct_data.get("processed_count", 0) or 0)
            auto_running = bool(getattr(record, "automation_running", False))
            is_active = bool(record.runtime_running and record.runtime_debug_ready) or auto_running
            if auto_running or is_active or f_val > 0 or l_val > 0 or p_val > 0:
                t_val = int(acct_data.get("target_val", 0) or 0)
                if t_val <= 0:
                    try:
                        cfg = self.controller.get_profile_task_config(record.profile_id) if hasattr(self, "controller") and hasattr(self.controller, "get_profile_task_config") else {}
                        active_cfg = cfg.get("active") if isinstance(cfg.get("active"), dict) else {}
                        t_val = int(cfg.get("daily_follows_limit") or active_cfg.get("daily_follows_limit") or cfg.get("daily_task_limit") or active_cfg.get("daily_task_limit") or cfg.get("task_limit") or 100)
                    except Exception:
                        t_val = 100
                sum_follows += f_val
                sum_likes += l_val
                sum_target += t_val

        if sum_target > 0:
            total_target = sum_target
            total_follows = sum_follows
            likes_count = sum_likes
        else:
            total_target = int(stats.get("total_target", 0) or 0)
            total_follows = int(stats.get("follows", 0) or 0)
            likes_count = int(stats.get("likes", stats.get("likes_count", 0)) or 0)

        filtered_count = int(stats.get("filtered_count", 0) or 0)
        if total_target > 0:
            pct_total = int(min(100, max(0, (total_follows / total_target) * 100)))
            target_str = f"/{total_target} ({pct_total}%)"
            cap_target_str = f"/{total_target}"
        else:
            target_str = ""
            cap_target_str = ""

        self.metrics_label.setText(
            f"● {active_count}活跃  ⏳ {idle_count}休眠  |  总关 {total_follows}{target_str}  ·  总赞 {likes_count}  ·  拦截 {filtered_count}"
        )
        self.capsule_summary_label.setText(
            f"● {active_count}活跃 ⏳ {idle_count}休眠 | 关: {total_follows}{cap_target_str} · 赞: {likes_count}"
        )

        # 针对账号数量自适应调整窗口高度 (避免 2 个账号时留出大片空白，多账号时自适应展开)
        if total_accounts > 0 and not self._is_collapsed and not getattr(self, "_is_dock_collapsed", False):
            rows = (total_accounts + 1) // 2
            base_h = 120 + (54 if abnormal_pids else 0)
            target_h = min(540, max(210, base_h + rows * 52))
            count_changed = (getattr(self, "_last_account_count", -1) != total_accounts)
            if count_changed:
                self._last_account_count = total_accounts
                self._user_manually_resized = False

            if not getattr(self, "_user_manually_resized", False):
                if abs(self.height() - target_h) >= 15:
                    self.resize(self.width(), target_h)

        # 4. 刷新异常聚合吸顶卡 (形态 2) 与边缘智能药丸 (Dock Pill)
        self._refresh_alert_banner()
        self._refresh_dock_pill_ui()

        # 5. 双列对称更新多账号卡片 (动态自适应任意数量)
        current_pids = {r.profile_id for r in records}
        for pid, card in list(self._cards_by_pid.items()):
            if pid not in current_pids:
                card.hide()

        for i, record in enumerate(records):
            pid = record.profile_id
            row = i // 2
            col = i % 2
            if pid not in self._cards_by_pid:
                card = MiniAccountCardWidget(pid, self.matrix_container)
                card.inspect_requested.connect(self._open_account_diagnosis)
                self.matrix_grid.addWidget(card, row, col)
                self._cards_by_pid[pid] = card
            else:
                card = self._cards_by_pid[pid]
                self.matrix_grid.addWidget(card, row, col)
                card.show()

            # 提取卡片指标
            idx_str = f"{i+1:02d}号"
            tag = (record.profile_name or "").split(" ")[-1] if " " in (record.profile_name or "") else ""
            if len(tag) > 4:
                tag = tag[:3]

            proxy_stat = str(getattr(record, "proxy_status", "") or "").upper()
            sched_status = str(getattr(record, "schedule_status", "") or "")
            auto_running = bool(getattr(record, "automation_running", False))
            is_active = bool(record.runtime_running and record.runtime_debug_ready) or auto_running

            if proxy_stat in {"ERROR", "TIMEOUT", "DISCONNECTED"}:
                status_text = "代理中断"
                status_type = "error"
            elif "WAITING" in sched_status:
                status_text = "休眠中"
                status_type = "idle"
            elif is_active:
                status_text = "互动中"
                status_type = "active"
            else:
                status_text = "待命中"
                status_type = "idle"

            # 多别名容错查找该账号统计
            acct_keys = [record.profile_id, getattr(record, "x_account_id", ""), getattr(record, "x_username", ""), getattr(record, "profile_name", "")]
            acct_data = {}
            for k in acct_keys:
                if k and k in by_acct and isinstance(by_acct[k], dict):
                    acct_data.update(by_acct[k])

            follows = int(acct_data.get("follows", 0) or 0)
            likes = int(acct_data.get("likes", 0) or 0)
            processed = int(acct_data.get("processed_count", 0) or 0)
            progress = str(acct_data.get("progress", "") or "").strip()
            progress_pct = int(acct_data.get("progress_pct", 0) or 0)
            target_val = int(acct_data.get("target_val", 0) or 0)

            is_running_task = bool(auto_running or is_active)
            has_today_records = bool(follows > 0 or likes > 0 or processed > 0)
            should_display_task = is_running_task or has_today_records

            if not should_display_task:
                # 核心机制：只有跑任务的时候，或者当日有跑过记录的时候才显示；其余待命账号不展示配额目标与进度
                stats_text = ""
                progress = ""
                progress_pct = 0
            else:
                # 跑任务中或当日有记录：根据输入的任务数量解析配额分母
                if not progress or "/" not in progress or target_val <= 0:
                    try:
                        cfg = self.controller.get_profile_task_config(record.profile_id) if hasattr(self, "controller") and hasattr(self.controller, "get_profile_task_config") else {}
                        active_cfg = cfg.get("active") if isinstance(cfg.get("active"), dict) else {}
                        t_lim = int(
                            cfg.get("daily_follows_limit")
                            or active_cfg.get("daily_follows_limit")
                            or cfg.get("daily_task_limit")
                            or active_cfg.get("daily_task_limit")
                            or cfg.get("task_limit")
                            or 0
                        )
                        target_val = t_lim if t_lim > 0 else 100
                        progress = f"{follows}/{target_val}"
                        progress_pct = int(min(100, max(0, (follows / target_val) * 100)))
                        if follows > 0 and progress_pct < 2:
                            progress_pct = 2
                    except Exception:
                        if target_val <= 0:
                            target_val = 100
                        progress = f"{follows}/{target_val}"

                # 格式化指标文本（清晰呈现关注数量与点赞数据：根据输入的任务数量或当日跑过记录呈现）
                if "/" in progress:
                    stats_text = f"关 {progress} · 赞 {likes}"
                elif target_val > 0:
                    stats_text = f"关 {follows}/{target_val} · 赞 {likes}"
                else:
                    stats_text = f"关 {follows} · 赞 {likes}"

            card.update_data(
                index_str=idx_str,
                tag=tag,
                status_text=status_text,
                status_type=status_type,
                stats_text=stats_text,
                progress=progress,
                progress_pct=progress_pct,
            )
            # 悬停提示
            if should_display_task:
                card.setToolTip(f"【{idx_str}】今日关注进度: {progress or f'{follows}/{target_val}'} ({progress_pct}%) · 点赞: {likes}")
            else:
                card.setToolTip(f"【{idx_str}】待命中 · 暂未启动任务")

    def _refresh_alert_banner(self) -> None:
        """刷新形态 2 突发异常聚合卡片（严格两端对齐 · 紧凑双行，高度仅 48-52px）"""
        if not self._abnormal_pids:
            self.alert_widget.hide()
            return

        self.alert_widget.show()
        err_count = len(self._abnormal_pids)
        self.alert_title.setText(f"⚠️ 异常拦截 ({err_count})")

        # 清除旧的 tab 按钮（彻底隐藏并脱离父级，杜绝重叠重绘）
        while self.error_tabs_layout.count() > 0:
            item = self.error_tabs_layout.takeAt(0)
            if item.widget():
                w = item.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()

        # 重建异常账号选项卡（紧凑智能显示，防止生硬省略号）
        for pid in self._abnormal_pids[:5]:
            rec = self._records_by_pid.get(pid)
            p_name = rec.profile_name if rec else pid
            clean_name = p_name.replace("账号", " ").strip()
            parts = clean_name.split()
            if len(parts) >= 2:
                display_name = f"{parts[0]} {parts[1]}"
            else:
                display_name = clean_name
            if len(display_name) > 8:
                display_name = display_name[:7] + "…"

            is_cur = (pid == self._active_abnormal_pid)
            btn = QPushButton(f"● {display_name}" if is_cur else display_name, self.error_tabs_widget)
            btn.setFixedHeight(20)
            btn.setCheckable(True)
            btn.setToolTip(f"点击切换查看 {p_name} 的受阻状态")
            btn.setChecked(is_cur)
            if is_cur:
                btn.setStyleSheet("background: #EF4444; color: #FFFFFF; border: none; border-radius: 4px; font-size: 10px; font-weight: bold; padding: 0 6px;")
            else:
                btn.setStyleSheet("background: #FFFFFF; color: #991B1B; border: 1px solid #FECDD3; border-radius: 4px; font-size: 10px; padding: 0 6px;")
            btn.clicked.connect(lambda checked=False, target_pid=pid: self._set_active_abnormal_pid(target_pid))
            self.error_tabs_layout.addWidget(btn)

        # 更新选中账号的简明信息
        active_pid = self._active_abnormal_pid or self._abnormal_pids[0]
        rec = self._records_by_pid.get(active_pid)
        port_info = f"端口 {rec.proxy_port}" if rec and rec.proxy_port else "代理异常"

        self.alert_detail_label.setText(f"{port_info} 超时 · 熔断保护")

    def _set_active_abnormal_pid(self, pid: str) -> None:
        self._active_abnormal_pid = pid
        self._refresh_alert_banner()

    def _on_rescue_retry_clicked(self) -> None:
        if self._active_abnormal_pid:
            self.retry_profile_requested.emit(self._active_abnormal_pid)

    def _on_rescue_copy_clicked(self) -> None:
        """一键复制所有突发异常受阻账号战报与全部日志"""
        if not self._abnormal_pids:
            return

        report_lines = [
            "================ 老谷控制中心 · 突发异常综合诊断战报 ================",
            f"● 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"● 异常账号总数: {len(self._abnormal_pids)} 个",
            "------------------------- 受阻账号清单 -------------------------",
        ]

        target_keys = set(self._abnormal_pids)
        for ab_id in self._abnormal_pids:
            rec = self._records_by_pid.get(ab_id)
            name = rec.profile_name if rec else ab_id
            p_stat = str(getattr(rec, "proxy_status", "") or "").upper() if rec else "UNKNOWN"
            port = getattr(rec, "proxy_port", "") or "未知端口"
            s_stat = getattr(rec, "schedule_status", "") or "未知"
            marker = " (当前查看)" if ab_id == self._active_abnormal_pid else ""
            report_lines.append(f"● [{name}] ({ab_id}){marker}: 代理={p_stat}, 端口={port}, 调度状态={s_stat}")
            if rec and rec.profile_name:
                target_keys.add(rec.profile_name)

        report_lines.append("------------------------- 关联异常与报错日志流 -------------------------")
        matched_logs = []
        err_keywords = ("错误", "失败", "超时", "异常", "中断", "熔断", "Error", "ERROR", "Exception", "Timeout", "ProxyError")
        for line in self._recent_logs:
            is_matched = any(k in line for k in target_keys)
            is_err_kw = any(w in line for w in err_keywords)
            if is_matched or is_err_kw:
                matched_logs.append(line)

        if matched_logs:
            report_lines.extend(matched_logs)
        elif self._recent_logs:
            report_lines.extend(list(self._recent_logs))
        else:
            report_lines.append("(暂无该账号实时数据流，任务已在连接前平滑熔断安全挂起)")

        # 附加上下文日志
        other_logs = [line for line in self._recent_logs if line not in matched_logs]
        if other_logs:
            report_lines.append("------------------------- 最近全局运行日志上下文 -------------------------")
            report_lines.extend(other_logs[-50:])

        report_lines.append("================================================================")

        full_text = "\n".join(report_lines)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(full_text)

        self.btn_rescue_copy.setText(" 已复制全部!")
        self.btn_rescue_copy.setIcon(line_icon("check", "#FFFFFF", 12))
        self.btn_rescue_copy.setStyleSheet(
            "background: #059669; color: #FFFFFF; font-size: 10px; font-weight: bold; border: none; border-radius: 4px; padding: 0 4px;"
        )
        QTimer.singleShot(2500, self._restore_rescue_copy_btn)

    def _restore_rescue_copy_btn(self) -> None:
        self.btn_rescue_copy.setText(" 复制全部日志")
        self.btn_rescue_copy.setIcon(line_icon("copy", "#1D4ED8", 12))
        self.btn_rescue_copy.setStyleSheet(
            "background: #EFF6FF; color: #1D4ED8; border: 1px solid #BFDBFE; border-radius: 4px; font-size: 10px; font-weight: 600; padding: 0 4px;"
        )

    def _on_diag_copy_all_clicked(self) -> None:
        """一键复制诊断抽屉终端内的全部输出"""
        text = self.diag_terminal.toPlainText().strip()
        if not text:
            text = f"[{self._active_diagnose_pid}] 暂无诊断终端日志记录。"
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        self.btn_diag_copy_all.setText(" 已复制全部日志!")
        self.btn_diag_copy_all.setIcon(line_icon("check", "#FFFFFF", 12))
        self.btn_diag_copy_all.setStyleSheet("background: #059669; color: #FFFFFF; border: none; border-radius: 4px; font-size: 10px; font-weight: bold; padding: 0 8px;")
        QTimer.singleShot(2500, self._restore_diag_copy_btn)

    def _restore_diag_copy_btn(self) -> None:
        self.btn_diag_copy_all.setText(" 复制全部日志")
        self.btn_diag_copy_all.setIcon(line_icon("copy", "#1D4ED8", 13))
        self.btn_diag_copy_all.setStyleSheet("background: #EFF6FF; color: #1D4ED8; border: 1px solid #BFDBFE; border-radius: 4px; font-size: 10px; font-weight: 600; padding: 0 8px;")

    def _on_rescue_diag_clicked(self) -> None:
        if self._active_abnormal_pid:
            self._open_account_diagnosis(self._active_abnormal_pid)

    def _on_rescue_skip_clicked(self) -> None:
        if self._active_abnormal_pid:
            self.skip_profile_requested.emit(self._active_abnormal_pid)
            if self._active_abnormal_pid in self._abnormal_pids:
                self._abnormal_pids.remove(self._active_abnormal_pid)
            self._active_abnormal_pid = self._abnormal_pids[0] if self._abnormal_pids else None
            self._refresh_alert_banner()

    def _on_rescue_test_proxy_clicked(self) -> None:
        if self._active_abnormal_pid:
            self.test_proxy_requested.emit(self._active_abnormal_pid)

    def _open_account_diagnosis(self, profile_id: str) -> None:
        """进入形态 3：单号定向诊断抽屉"""
        self._active_diagnose_pid = profile_id
        rec = self._records_by_pid.get(profile_id)
        name = rec.profile_name if rec else profile_id

        self.diag_header_label.setText(f"【{name} 专属报错诊断抽屉】")

        # 筛选匹配该号的日志
        matched = [line for line in self._recent_logs if profile_id in line or name in line]
        if not matched:
            matched = list(self._recent_logs)[-8:] if self._recent_logs else ["暂无该账号异常日志记录"]

        formatted = (
            f"=== 账号诊断上下文: {name} (ID: {profile_id}) ===\n"
            f"代理状态: {getattr(rec, 'proxy_status', 'UNKNOWN')} | 端口: {getattr(rec, 'proxy_port', '未配置')}\n"
            f"--------------------------------------------------\n"
            + "\n".join(matched[-10:])
        )
        self.diag_terminal.setPlainText(formatted)
        self.stack.setCurrentIndex(2)

    def _on_diag_retry_clicked(self) -> None:
        if self._active_diagnose_pid:
            self.retry_profile_requested.emit(self._active_diagnose_pid)
            self._switch_stack_page(0)

    def _on_diag_skip_clicked(self) -> None:
        if self._active_diagnose_pid:
            self.skip_profile_requested.emit(self._active_diagnose_pid)
            self._switch_stack_page(0)

    def _on_diag_open_notepad_clicked(self) -> None:
        if self._active_diagnose_pid:
            self.open_profile_log_requested.emit(self._active_diagnose_pid)

    def show_at_bottom_right(self, available_geometry: Any) -> None:
        margin = 18
        self.move(
            available_geometry.right() - self.width() - margin + 1,
            available_geometry.bottom() - self.height() - margin + 1,
        )
        self.show()
        self.raise_()
        self.activateWindow()
        self._dock_edge = None

    def _edges_at(self, point: QPoint) -> set[str]:
        edges: set[str] = set()
        if point.x() <= self._resize_margin:
            edges.add("left")
        elif point.x() >= self.width() - self._resize_margin:
            edges.add("right")
        if point.y() <= self._resize_margin:
            edges.add("top")
        elif point.y() >= self.height() - self._resize_margin:
            edges.add("bottom")
        return edges

    def _update_resize_cursor(self, edges: set[str]) -> None:
        if edges in ({"left", "top"}, {"right", "bottom"}):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges in ({"right", "top"}, {"left", "bottom"}):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges & {"left", "right"}:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges & {"top", "bottom"}:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # 双击标题栏在贴边药丸与完整面板间切换
            if getattr(self, "_is_dock_collapsed", False):
                self._expand_from_dock(animate=True)
            else:
                self._collapse_to_dock(animate=True)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(event.position().toPoint())
            if edges:
                self._resize_edges = edges
                self._drag_offset = None
                event.accept()
                return
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resize_edges and event.buttons() & Qt.MouseButton.LeftButton:
            point = event.globalPosition().toPoint()
            geometry = self.geometry()
            if "left" in self._resize_edges:
                geometry.setLeft(min(point.x(), geometry.right() - self.minimumWidth() + 1))
            if "right" in self._resize_edges:
                geometry.setRight(max(point.x(), geometry.left() + self.minimumWidth() - 1))
            if "top" in self._resize_edges:
                geometry.setTop(min(point.y(), geometry.bottom() - self.minimumHeight() + 1))
            if "bottom" in self._resize_edges:
                geometry.setBottom(max(point.y(), geometry.top() + self.minimumHeight() - 1))
            self.setGeometry(geometry)
            event.accept()
            return
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        self._update_resize_cursor(self._edges_at(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resize_edges:
            self._user_manually_resized = True
        self._drag_offset = None
        self._resize_edges.clear()
        self._update_resize_cursor(set())
        super().mouseReleaseEvent(event)
        self._check_edge_docking()

        # 如果贴边成功，松开鼠标立即平滑吸附折叠，给予即时操作反馈
        if getattr(self, "_auto_hide_enabled", True) and getattr(self, "_dock_edge", None) and not getattr(self, "_is_dock_collapsed", False):
            self._collapse_to_dock(animate=True)

    def show_stop_all_overlay(self) -> None:
        """显示 HUD 内部一体化蒙层浮层"""
        if self._is_collapsed:
            self.toggle_collapse(False)
        if not hasattr(self, "_stop_overlay") or not self._stop_overlay:
            return
        self._stop_overlay.setGeometry(0, 0, self.width(), self.height())
        self._stop_card.adjustSize()
        cw = self._stop_card.width()
        ch = self._stop_card.height()
        self._stop_card.move((self.width() - cw) // 2, max(30, (self.height() - ch) // 2))
        self._stop_overlay.show()
        self._stop_overlay.raise_()

    def hide_stop_all_overlay(self) -> None:
        """隐藏 HUD 内部一体化蒙层浮层"""
        if hasattr(self, "_stop_overlay") and self._stop_overlay:
            self._stop_overlay.hide()

    def _on_stop_all_confirmed(self) -> None:
        """确认执行停止全部"""
        self.hide_stop_all_overlay()
        self.stop_all_requested.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if hasattr(self, "_stop_overlay") and self._stop_overlay and self._stop_overlay.isVisible():
            if event.key() == Qt.Key.Key_Escape:
                self.hide_stop_all_overlay()
                event.accept()
                return
            elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._on_stop_all_confirmed()
                event.accept()
                return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_stop_overlay") and self._stop_overlay and self._stop_overlay.isVisible():
            self._stop_overlay.setGeometry(0, 0, self.width(), self.height())
            cw = self._stop_card.width()
            ch = self._stop_card.height()
            self._stop_card.move((self.width() - cw) // 2, max(30, (self.height() - ch) // 2))
        if hasattr(self, "dock_pill") and self.dock_pill and getattr(self, "_is_dock_collapsed", False):
            self.dock_pill.setGeometry(0, 0, self.width(), self.height())


class GroupHeaderWidget(QFrame):
    """分组折叠横条组件：支持下拉展示与折叠合并、同节点IP与自定义组标识、组内防风控轮换调度"""

    def __init__(
        self,
        group_id: str,
        group_name: str,
        is_custom: bool,
        account_count: int,
        collapsed: bool,
        on_toggle_collapse: Callable[[str, bool], None],
        on_start_rotation: Callable[[str, str], None],
        on_stop_rotation: Callable[[str], None],
        on_rename_group: Callable[[str, str], None] | None = None,
        on_delete_group: Callable[[str], None] | None = None,
        rotation_status: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.group_id = group_id
        self.group_name = group_name
        self.is_custom = is_custom
        self.account_count = account_count
        self.collapsed = collapsed
        self.on_toggle_collapse = on_toggle_collapse
        self.on_start_rotation = on_start_rotation
        self.on_stop_rotation = on_stop_rotation
        self.on_rename_group = on_rename_group
        self.on_delete_group = on_delete_group

        self.setObjectName("groupHeaderFrame")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        accent_color = "#2563EB" if is_custom else "#0891B2"
        self.setStyleSheet(f"""
            QFrame#groupHeaderFrame {{
                background: #EEF3FA;
                border: 1px solid #D0DCEB;
                border-left: 4px solid {accent_color};
                border-radius: 6px;
            }}
            QFrame#groupHeaderFrame:hover {{
                background: #E4ECF7;
                border-color: #B8CDE6;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        # 折叠展开指示箭头
        self.arrow_label = QLabel("▼" if not collapsed else "▶")
        self.arrow_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #475569;")
        layout.addWidget(self.arrow_label)

        # 图标与组名称
        icon_str = "📁" if is_custom else "🌐"
        self.title_label = QLabel(f"{icon_str}  {group_name}")
        self.title_label.setStyleSheet("font-weight: 700; font-size: 13px; color: #0F172A;")
        layout.addWidget(self.title_label)

        # 分组标签类型
        tag_text = "自定义分组" if is_custom else "同节点 IP 归类"
        tag_bg = "#DBEAFE" if is_custom else "#CFFAFE"
        tag_color = "#1E40AF" if is_custom else "#0E7490"
        self.tag_label = QLabel(tag_text)
        self.tag_label.setStyleSheet(f"""
            background: {tag_bg};
            color: {tag_color};
            font-size: 11px;
            font-weight: 600;
            padding: 2px 7px;
            border-radius: 4px;
        """)
        layout.addWidget(self.tag_label)

        # 账号数量统计
        self.count_label = QLabel(f"({account_count} 个账号)")
        self.count_label.setStyleSheet("font-size: 12px; color: #64748B;")
        layout.addWidget(self.count_label)

        # 动态轮换实时状态提示
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 11px; color: #0284C7; font-weight: 600;")
        layout.addWidget(self.status_label)

        layout.addStretch(1)

        # 组内轮换控制按钮
        self.rotate_btn = QPushButton("⚡ 组内串行轮换")
        self.rotate_btn.setObjectName("miniRunButton")
        self.rotate_btn.setToolTip("防风控调度：组内多账号按序单批次排队执行，换号自然微静默")
        self.rotate_btn.setMinimumHeight(26)
        self.rotate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rotate_btn.clicked.connect(self._toggle_rotation)
        layout.addWidget(self.rotate_btn)

        # 自定义分组特有的重命名与删除按钮
        if is_custom:
            if on_rename_group:
                self.rename_btn = QPushButton("✏ 重命名")
                self.rename_btn.setObjectName("miniConfigButton")
                self.rename_btn.setMinimumHeight(26)
                self.rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.rename_btn.clicked.connect(lambda checked=False: self.on_rename_group(self.group_id, self.group_name))
                layout.addWidget(self.rename_btn)
            if on_delete_group:
                self.delete_btn = QPushButton("🗑 删除")
                self.delete_btn.setObjectName("miniDeleteButton")
                self.delete_btn.setMinimumHeight(26)
                self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.delete_btn.clicked.connect(lambda checked=False: self.on_delete_group(self.group_id))
                layout.addWidget(self.delete_btn)

        self.update_rotation_status(rotation_status or {})

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_collapse()
        super().mousePressEvent(event)

    def toggle_collapse(self) -> None:
        self.collapsed = not self.collapsed
        self.arrow_label.setText("▼" if not self.collapsed else "▶")
        if self.on_toggle_collapse:
            self.on_toggle_collapse(self.group_id, self.collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        self.arrow_label.setText("▼" if not collapsed else "▶")

    def _toggle_rotation(self) -> None:
        if self.rotate_btn.text().startswith("⏹"):
            if self.on_stop_rotation:
                self.on_stop_rotation(self.group_id)
        else:
            if self.on_start_rotation:
                self.on_start_rotation(self.group_id, self.group_name)

    def update_rotation_status(self, status_data: dict[str, Any]) -> None:
        st = str(status_data.get("status") or "IDLE").upper()
        msg = str(status_data.get("last_message") or "")
        if st == "RUNNING":
            self.rotate_btn.setText("⏹ 停止轮换")
            self.rotate_btn.setObjectName("miniStopButton")
            self.rotate_btn.setStyleSheet("color: #B42318; background: #FEF3F2; border: 1px solid #FECDCA; font-weight: 600; font-size: 12px; border-radius: 6px; padding: 0 10px;")
            display_msg = msg if msg else "🔄 组内排队轮换中..."
            if len(display_msg) > 36:
                display_msg = display_msg[:34] + "..."
            self.status_label.setText(display_msg)
            self.status_label.setToolTip(msg)
        else:
            self.rotate_btn.setText("⚡ 组内串行轮换")
            self.rotate_btn.setObjectName("miniRunButton")
            self.rotate_btn.setStyleSheet("color: #067647; background: #ECFDF3; border: 1px solid #ABEFC6; font-weight: 600; font-size: 12px; border-radius: 6px; padding: 0 10px;")
            if st == "COMPLETED":
                self.status_label.setText("🎉 今日配额均已达成")
                self.status_label.setToolTip(msg)
            elif st == "STOPPED":
                self.status_label.setText("⏹ 轮换已停止")
                self.status_label.setToolTip(msg)
            else:
                self.status_label.setText("")
                self.status_label.setToolTip("")


class MainWindow(QMainWindow):
    engine_progress_signal = Signal(int, str)
    rotation_status_signal = Signal(str, str, dict)
    schedule_event_signal = Signal(str, str, dict)

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
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_notice_dialog: UpdateNoticeDialog | None = None
        self.engine_progress_signal.connect(self._engine_progress)
        self.rotation_status_signal.connect(self._on_rotation_status_received)
        self.schedule_event_signal.connect(self._handle_schedule_event_in_main_thread)
        if hasattr(self.controller, "register_schedule_listener"):
            self.controller.register_schedule_listener(
                lambda pid, st, data: self.schedule_event_signal.emit(pid, st, data)
            )
        self._group_row_ranges: dict[str, list[int]] = {}
        self._group_header_widgets: dict[str, GroupHeaderWidget] = {}
        self._last_account_records: list[AccountRow] = []
        self._group_version: int = 0
        self._closing = False
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
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
        self._auto_scroll_main_log = True
        self._unread_logs_count = 0
        self._log_idle_timer = QTimer(self)
        self._log_idle_timer.setSingleShot(True)
        self._log_idle_timer.setInterval(10000)
        self._log_idle_timer.timeout.connect(self._scroll_main_log_to_bottom_and_resume)
        settings = getattr(self.controller, "settings", None)
        self._log_tail_path = os.fspath(getattr(settings, "log_file", ""))
        self._log_tail_active_path = self._log_tail_path
        self._log_tail_offset = 0
        self._log_tail_partial = ""
        self._log_tail_timer = QTimer(self)
        self._log_tail_timer.setInterval(700)
        self._log_tail_timer.timeout.connect(self._poll_log_file)
        self._mini_window = MiniLogWindow()
        self._mini_window.controller = self.controller
        self._mini_window.restore_requested.connect(self._restore_from_mini_window)
        self._mini_window.hide_requested.connect(self._hide_mini_window)
        self._mini_window.stop_all_requested.connect(self._confirm_stop_all_from_mini)
        self._mini_window.exit_requested.connect(self._force_exit_application)
        self._mini_window.retry_profile_requested.connect(self._retry_profile_from_mini)
        self._mini_window.stop_profile_requested.connect(self._stop_profile_from_mini)
        self._mini_window.skip_profile_requested.connect(self._skip_profile_from_mini)
        self._mini_window.test_proxy_requested.connect(self._test_profile_proxy_from_mini)
        self._mini_window.open_profile_log_requested.connect(self._open_profile_log_from_mini)
        self._mini_window.retry_all_abnormal_requested.connect(self._retry_all_abnormal_from_mini)
        self._tray_icon: QSystemTrayIcon | None = None
        self._setup_tray_icon()

        self._build_ui()
        self._setup_stdout_redirect()
        self._read_log_file_tail(seed=True)
        self._log_tail_timer.start()
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
        self._statistics_timer.start(2000)

        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.setInterval(3000)
        self.auto_refresh_timer.timeout.connect(self._auto_refresh_profile_snapshots)
        self.auto_refresh_timer.start()

        self._schedule_watchdog_timer = QTimer(self)
        self._schedule_watchdog_timer.setInterval(5000)
        self._schedule_watchdog_timer.timeout.connect(self._schedule_watchdog_tick)
        self._schedule_watchdog_timer.start()

        QTimer.singleShot(100, lambda: self._run_job("检查 API 连接", self.controller.health, self._health_finished))

    def _handle_schedule_event_in_main_thread(self, profile_id: str, state: str, data: dict) -> None:
        if state == "WAITING_SCHEDULE":
            next_run = data.get("scheduled_for", "")
            self.statusBar().showMessage(f"档案 {profile_id} 定时任务已排期，将于 {next_run} 执行")
            self._log(f"[自动化调度] 档案 {profile_id} 定时任务已排期，将在 {next_run} 到点自动执行")
        elif state in {"TRIGGERED", "TRIGGERING"}:
            self.statusBar().showMessage(f"档案 {profile_id} 定时任务已到点，正在唤醒浏览器...")
            self._log(f"[自动化调度] ⏰ 档案 {profile_id} 定时任务已到点，正在唤醒并执行")
        elif state == "STARTED":
            self.statusBar().showMessage(f"档案 {profile_id} 自动化拓客引擎已成功启动")
            self._log(f"[自动化调度] ⚡ 档案 {profile_id} 自动化拓客引擎已启动运行")
        elif state == "DELAYED_PROFILE_BUSY":
            retry_at = data.get("retry_at", "")
            reason = data.get("reason", "")
            reason_hint = "（启动缓冲重试）" if reason == "START_RETRY" else "（档案忙碌）"
            self.statusBar().showMessage(f"档案 {profile_id} {reason_hint}，定时任务延后至 {retry_at} 重试")
            self._log(f"[自动化调度] ⏳ 档案 {profile_id} {reason_hint}，定时任务延后至 {retry_at} 重试")
        elif state == "CANCELLED_BY_USER":
            self.statusBar().showMessage(f"档案 {profile_id} 定时任务已取消")
            self._log(f"[自动化调度] 档案 {profile_id} 定时任务已取消")
        elif state == "FAILED":
            err = str(data.get("error", ""))
            self.statusBar().showMessage(f"档案 {profile_id} 定时任务触发失败: {err[:50]}")
            self._log(f"[自动化调度] ❌ 档案 {profile_id} 定时任务触发失败: {err}")
        self._load_registry()

    def _schedule_watchdog_tick(self) -> None:
        try:
            if hasattr(self.controller, "check_scheduled_tasks_tick"):
                triggered = self.controller.check_scheduled_tasks_tick()
                if triggered:
                    self._log(f"[看门狗巡检] ⏰ 捕获到期任务并唤醒: {', '.join(triggered)}")
                    self._load_registry()
        except Exception:
            pass

    def _cancel_profile_schedule(self, profile_id: str) -> None:
        if hasattr(self.controller, "cancel_scheduled_task"):
            self.controller.cancel_scheduled_task(profile_id)
            self.statusBar().showMessage(f"已取消档案 {profile_id} 的定时任务")
            self._log(f"[定时调度] 用户已取消档案 {profile_id} 的定时任务")
            self._load_registry()

    def _run_profile_schedule_now(self, profile_id: str) -> None:
        config = self.controller.get_profile_task_config(profile_id) if hasattr(self.controller, "get_profile_task_config") else {}
        active = config.get("active") if isinstance(config, dict) else config
        if isinstance(active, dict) and active:
            active_copy = dict(active)
            active_copy["schedule_mode"] = "immediate"
            record = self._account_rows_by_id.get(profile_id)
            account_name = (record.profile_name if record else "") or profile_id
            active_copy["account_tag"] = account_name
            self._run_job(
                f"立即执行定时任务：{account_name} ({profile_id})",
                lambda: self.controller.start_automation_task(profile_id, active_copy),
                lambda result: self._automation_finished(profile_id, result),
            )
        else:
            self.configure_and_run_automation(profile_id)

    def _apply_drop_shadow(self, widget: QWidget) -> None:
        """为面板组件注入柔和悬浮阴影"""
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 12))
        shadow.setOffset(0, 4)
        widget.setGraphicsEffect(shadow)

    def _auto_refresh_profile_snapshots(self) -> None:
        try:
            snapshot_path = os.fspath(self.controller.settings.profile_snapshot_file)
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
            if path != self._log_tail_active_path:
                self._log_tail_active_path = path
                self._log_tail_offset = 0
                self._log_tail_partial = ""
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
                    merged = [line for line in recent if line not in lines] + lines
                    self._mini_window.seed_logs(merged[-20:])
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
                    self._queue_log(clean)
        except (OSError, UnicodeError):
            return

    def _poll_log_file(self) -> None:
        if self._closing:
            return
        self._read_log_file_tail()

    def _flush_log_buffer(self) -> None:
        self._log_flush_scheduled = False
        if not self._pending_log_lines or self._closing:
            return
        lines = list(self._pending_log_lines)
        self._pending_log_lines.clear()
        self.log_output.appendPlainText("\n".join(lines))
        if self._auto_scroll_main_log:
            self._scroll_main_log_to_latest()
        else:
            self._unread_logs_count += len(lines)
            self._update_scroll_to_bottom_btn_text()

    def _scroll_main_log_to_latest(self) -> None:
        if hasattr(self, "log_output"):
            scrollbar = self.log_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _update_scroll_to_bottom_btn_text(self) -> None:
        if not hasattr(self, "scroll_to_bottom_btn"):
            return
        if self._unread_logs_count > 99:
            self.scroll_to_bottom_btn.setText("⬇️ 回到底部 (99+)")
        elif self._unread_logs_count > 0:
            self.scroll_to_bottom_btn.setText(f"⬇️ 回到底部 (+{self._unread_logs_count})")
        else:
            self.scroll_to_bottom_btn.setText("⬇️ 回到底部")
        self._reposition_scroll_to_bottom_btn()

    def _reposition_scroll_to_bottom_btn(self) -> None:
        if not hasattr(self, "scroll_to_bottom_btn") or not hasattr(self, "log_output"):
            return
        self.scroll_to_bottom_btn.adjustSize()
        btn_w = self.scroll_to_bottom_btn.width()
        btn_h = self.scroll_to_bottom_btn.height()
        x = max(10, self.log_output.width() - btn_w - 28)
        y = max(10, self.log_output.height() - btn_h - 18)
        self.scroll_to_bottom_btn.move(x, y)
        self.scroll_to_bottom_btn.raise_()

    def _scroll_main_log_to_bottom_and_resume(self) -> None:
        self._auto_scroll_main_log = True
        self._unread_logs_count = 0
        if hasattr(self, "_log_idle_timer"):
            self._log_idle_timer.stop()
        if hasattr(self, "scroll_to_bottom_btn"):
            self.scroll_to_bottom_btn.hide()
        if hasattr(self, "log_output"):
            self._scroll_main_log_to_latest()

    def _on_main_log_scroll_changed(self, value: int) -> None:
        if getattr(self, "_closing", False) or not hasattr(self, "log_output"):
            return
        scrollbar = self.log_output.verticalScrollBar()
        distance_from_bottom = scrollbar.maximum() - value
        if distance_from_bottom <= 20:
            if not self._auto_scroll_main_log:
                self._auto_scroll_main_log = True
                self._unread_logs_count = 0
                if hasattr(self, "_log_idle_timer"):
                    self._log_idle_timer.stop()
                if hasattr(self, "scroll_to_bottom_btn"):
                    self.scroll_to_bottom_btn.hide()
        else:
            if self._auto_scroll_main_log:
                self._auto_scroll_main_log = False
                self._update_scroll_to_bottom_btn_text()
                if hasattr(self, "scroll_to_bottom_btn"):
                    self.scroll_to_bottom_btn.show()
                    self.scroll_to_bottom_btn.raise_()
            if hasattr(self, "_log_idle_timer"):
                self._log_idle_timer.start(10000)

    def _on_details_tab_changed(self, index: int) -> None:
        if index == 0:
            self._scroll_main_log_to_bottom_and_resume()

    def _build_ui(self) -> None:
        self.setWindowTitle(f"老谷自动化控制中心 v{VERSION}")
        self.setMinimumSize(1160, 800)
        self.resize(1320, 900)
        self.menuBar().hide()
        self.check_update_action = QAction("检查更新", self)

        root = QWidget()
        root.setObjectName("rootWidget")  # <--- 重要：限制灰色背景范围，解决白底灰色穿透阴影问题
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 15, 24, 15)
        header_layout.setSpacing(14)

        brand_mark = QLabel(objectName="headerBrandMark")
        brand_mark.setPixmap(application_icon().pixmap(44, 44))
        header_layout.addWidget(brand_mark)
        
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(QLabel("老谷自动化控制中心", objectName="title"))
        version_badge = QLabel(f"v{VERSION}", objectName="headerVersionBadge")
        version_badge.setStyleSheet(
            "QLabel {"
            "  color: #10b981;"
            "  background: rgba(16, 185, 129, 0.12);"
            "  border: 1px solid rgba(16, 185, 129, 0.3);"
            "  border-radius: 9px;"
            "  padding: 1px 7px;"
            "  font-size: 11px;"
            "  font-weight: 600;"
            "}"
        )
        title_row.addWidget(version_badge)
        title_row.addStretch(1)
        title_box.addLayout(title_row)
        title_box.addWidget(QLabel("浏览器档案 · 账号状态 · 自动化任务", objectName="subtitle"))
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

        self.telegram_config_button = QPushButton("🔔 TG通知")
        self.telegram_config_button.setObjectName("telegramButton")
        self.telegram_config_button.setMinimumHeight(32)
        self.telegram_config_button.setToolTip("配置 Telegram 机器人自动化战报通知")
        self.telegram_config_button.clicked.connect(self._open_telegram_config)
        status_box.addWidget(self.telegram_config_button)

        self.studio_token_button = QPushButton("👥 协同码")
        self.studio_token_button.setObjectName("studioTokenButton")
        self.studio_token_button.setMinimumHeight(32)
        self.studio_token_button.setToolTip("配置工作室协同码 (Studio Token)，开启跨设备协同防撞车去重")
        self.studio_token_button.clicked.connect(self._open_studio_token_config)
        status_box.addWidget(self.studio_token_button)

        self.blacklist_config_button = QPushButton("🛡️ 敏感词库")
        self.blacklist_config_button.setObjectName("blacklistButton")
        self.blacklist_config_button.setMinimumHeight(32)
        self.blacklist_config_button.setToolTip("配置全局动态敏感词与黑名单过滤库（一票否决制）")
        self.blacklist_config_button.clicked.connect(self._open_blacklist_config)
        self._update_blacklist_button_state()
        status_box.addWidget(self.blacklist_config_button)

        self.check_update_button = QPushButton("🔄 检查更新")
        self.check_update_button.setObjectName("checkUpdateButton")
        self.check_update_button.setMinimumHeight(32)
        self.check_update_button.setToolTip("检查控制中心在线版本更新")
        self.check_update_button.clicked.connect(self.check_for_updates)
        status_box.addWidget(self.check_update_button)

        header_layout.addLayout(status_box)
        root_layout.addWidget(header)

        self.live_status_label = QLabel("●  运行端正在连接服务器…", objectName="liveStatus")
        self.live_status_label.setContentsMargins(24, 7, 24, 7)
        root_layout.addWidget(self.live_status_label)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 18, 20, 18)
        
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
        metrics.setContentsMargins(12, 12, 12, 12)
        metrics.setHorizontalSpacing(8)
        self.stat_labels: dict[str, QLabel] = {}
        for column, (key, text) in enumerate((("total_tasks", "今日任务"), ("success_tasks", "成功"), ("failed_tasks", "失败"), ("timeout_tasks", "超时"))):
            card = QFrame(objectName="metricCard")
            card.setProperty("tone", key)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(13, 10, 13, 10)
            card_layout.setSpacing(2)
            card_layout.addWidget(QLabel(text, objectName="metricCaption"))
            value = QLabel("0", objectName="metricValue")
            card_layout.addWidget(value)
            metrics.addWidget(card, 0, column)
            self.stat_labels[key] = value
        left.addWidget(overview)

        account_heading = QHBoxLayout()
        account_title_box = QVBoxLayout()
        account_title_box.setSpacing(1)
        account_title_box.addWidget(QLabel("账号资产", objectName="sectionTitle"))
        account_title_box.addWidget(QLabel("选择档案后可配置任务或执行运行操作", objectName="sectionHint"))
        account_heading.addLayout(account_title_box)
        account_heading.addStretch(1)

        self.btn_select_all = QPushButton("☑ 全选")
        self.btn_select_all.setObjectName("miniSecondaryButton")
        self.btn_select_all.setMinimumHeight(28)
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.setToolTip("选中当前列表中的全部账号档案")
        self.btn_select_all.clicked.connect(self.select_all_profiles)
        account_heading.addWidget(self.btn_select_all)

        self.btn_clear_sel = QPushButton("☐ 清除选中")
        self.btn_clear_sel.setObjectName("miniSecondaryButton")
        self.btn_clear_sel.setMinimumHeight(28)
        self.btn_clear_sel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_sel.setToolTip("取消选中当前所有已勾选账号")
        self.btn_clear_sel.clicked.connect(self.clear_all_selection)
        account_heading.addWidget(self.btn_clear_sel)

        self.summary_label = QLabel("0 个账号", objectName="summary")
        account_heading.addWidget(self.summary_label)
        left.addLayout(account_heading)

        # 分组与同 IP 轮换控制栏
        group_bar = QFrame(objectName="groupControlBar")
        group_bar.setStyleSheet("""
            QFrame#groupControlBar {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
        """)
        group_layout = QHBoxLayout(group_bar)
        group_layout.setContentsMargins(10, 6, 10, 6)
        group_layout.setSpacing(8)

        group_label = QLabel("📁 分组视图:")
        group_label.setStyleSheet("font-weight: 700; color: #334155; font-size: 13px;")
        group_layout.addWidget(group_label)

        self.group_filter_combo = QComboBox()
        self.group_filter_combo.setMinimumHeight(30)
        self.group_filter_combo.setMinimumWidth(220)
        self.group_filter_combo.addItem("📋 全部账号 (平铺总览)", "__ALL_FLAT__")
        self.group_filter_combo.addItem("📑 全部账号 (按分组层级折叠展开)", "__ALL_GROUPED__")
        self.group_filter_combo.currentIndexChanged.connect(self._on_group_filter_changed)
        group_layout.addWidget(self.group_filter_combo)

        self.cb_select_all_header = QCheckBox("全选当前")
        self.cb_select_all_header.setStyleSheet("font-weight: 600; color: #334155; font-size: 12px; margin-left: 4px;")
        self.cb_select_all_header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cb_select_all_header.setToolTip("全选或取消全选当前视图下所有账号档案")
        self.cb_select_all_header.toggled.connect(self._on_header_select_all_toggled)
        group_layout.addWidget(self.cb_select_all_header)

        self.btn_create_group = QPushButton("+ 新建分组")
        self.btn_create_group.setIcon(line_icon("plus", "#2457D6", 14))
        self.btn_create_group.setMinimumHeight(28)
        self.btn_create_group.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_create_group.setToolTip("创建一个新的自定义分组，可将账号移入该组集中调度")
        self.btn_create_group.clicked.connect(lambda: self._prompt_create_custom_group())
        group_layout.addWidget(self.btn_create_group)

        self.btn_expand_all = QPushButton("全部展开")
        self.btn_expand_all.setMinimumHeight(28)
        self.btn_expand_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand_all.setToolTip("展开所有分组子行")
        self.btn_expand_all.clicked.connect(self._expand_all_groups)
        self.btn_expand_all.setVisible(False)
        group_layout.addWidget(self.btn_expand_all)

        self.btn_collapse_all = QPushButton("全部合并")
        self.btn_collapse_all.setMinimumHeight(28)
        self.btn_collapse_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_collapse_all.setToolTip("折叠合并所有分组子行")
        self.btn_collapse_all.clicked.connect(self._collapse_all_groups)
        self.btn_collapse_all.setVisible(False)
        group_layout.addWidget(self.btn_collapse_all)

        group_layout.addStretch(1)

        self.group_rotation_status_label = QLabel("")
        self.group_rotation_status_label.setStyleSheet("font-size: 12px; color: #0284C7; font-weight: 600;")
        group_layout.addWidget(self.group_rotation_status_label)

        self.btn_toggle_group_rotation = QPushButton("⚡ 组内轮换调度")
        self.btn_toggle_group_rotation.setObjectName("miniRunButton")
        self.btn_toggle_group_rotation.setMinimumHeight(28)
        self.btn_toggle_group_rotation.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_group_rotation.setToolTip("防风控串行轮换：组内多账号同节点排队单批次执行，换号自然微静默")
        self.btn_toggle_group_rotation.clicked.connect(self._toggle_current_group_rotation)
        group_layout.addWidget(self.btn_toggle_group_rotation)

        left.addWidget(group_bar)

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
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self.table)
        self.delete_shortcut.activated.connect(self._confirm_and_delete_selected_profiles)
        for column in range(1, len(self.HEADERS)):
            self.table.setColumnHidden(column, True)
        left.addWidget(self.table, 1)

        action_panel = QFrame(objectName="actionPanel")
        self._apply_drop_shadow(action_panel)
        action_layout = QVBoxLayout(action_panel)
        action_layout.setContentsMargins(14, 14, 14, 14)
        action_layout.setSpacing(10)
        action_layout.addWidget(QLabel("任务控制", objectName="sectionEyebrow"))
        
        self.automation_button = QPushButton("配置并运行自动化")
        self.automation_button.setObjectName("primaryButton")
        self.automation_button.setMinimumHeight(42)
        self.automation_button.setToolTip("为选中的档案设置参数并提交自动化任务")
        action_layout.addWidget(self.automation_button)

        safety_actions = QHBoxLayout()
        safety_actions.setSpacing(8)
        self.pause_automation_button = self._button("暂停任务", "pause")
        self.resume_automation_button = self._button("恢复任务", "play")
        self.cancel_automation_button = self._button("取消任务", "stop")
        for button in (self.pause_automation_button, self.resume_automation_button, self.cancel_automation_button):
            button.setMinimumHeight(30)
            safety_actions.addWidget(button, 1)
        action_layout.addLayout(safety_actions)

        self.engine_update_button = QPushButton("检查脚本更新")
        self.engine_update_button.setIcon(line_icon("update", "#475569"))
        self.engine_update_button.setMinimumHeight(32)
        self.engine_update_button.setToolTip("检查 Web 后台发布的自动化脚本；确认后下载并激活")
        self.engine_update_label = QLabel("自动化脚本：尚未检查", objectName="summary")
        update_row = QHBoxLayout()
        update_row.setSpacing(8)
        update_row.addWidget(self.engine_update_label, 1)
        update_row.addWidget(self.engine_update_button)

        self.send_report_button = QPushButton("📊 推送当前战报")
        self.send_report_button.setObjectName("sendReportButton")
        self.send_report_button.setMinimumHeight(32)
        self.send_report_button.setToolTip("手动汇总当前所有账号数据并立即推送大盘战报到 Telegram")
        self.send_report_button.clicked.connect(self._send_telegram_summary_report)
        update_row.addWidget(self.send_report_button)

        action_layout.addLayout(update_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.run_all_button = self._button("运行全部", "play", "#FFFFFF")
        self.run_all_button.setObjectName("runAllButton")
        self.stop_all_button = self._button("停止全部", "stop", "#FFFFFF")
        self.stop_all_button.setObjectName("stopAllButton")
        self.refresh_button = self._button("刷新账号", "refresh")
        self.refresh_button.setToolTip("从本地老谷浏览器读取档案列表（日常使用推荐）")
        self.scan_all_button = self._button("扫描底层", "scan")
        self.scan_all_button.setToolTip("通过底层 Node 探针探测账号（需本地安装 Node.js 组件）")
        self.delete_selected_button = self._button("删除选中", "trash", "#DC2626")
        self.delete_selected_button.setObjectName("deleteSelectedButton")
        self.delete_selected_button.setToolTip("从控制中心和老谷浏览器中删除选中的档案实例")
        
        for button in (self.run_all_button, self.stop_all_button, self.refresh_button, self.scan_all_button, self.delete_selected_button):
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
        runtime_layout.setContentsMargins(16, 14, 16, 14)
        runtime_layout.setSpacing(5)
        runtime_layout.addWidget(QLabel("当前档案", objectName="sectionEyebrow"))
        self.selected_profile_label = QLabel("尚未选择档案", objectName="runtimeValue")
        self.selected_runtime_label = QLabel("运行状态：—", objectName="summary")
        runtime_layout.addWidget(self.selected_profile_label)
        runtime_layout.addWidget(self.selected_runtime_label)
        right.addWidget(runtime)

        tools = QFrame(objectName="toolsPanel")
        self._apply_drop_shadow(tools)
        tools_layout = QGridLayout(tools)
        tools_layout.setContentsMargins(12, 10, 12, 12)
        tools_layout.setSpacing(6)
        tools_title = QLabel("只读工具", objectName="sectionEyebrow")
        tools_title.setToolTip("这些操作仅读取账号与页面状态，不会修改自动化配置")
        tools_layout.addWidget(tools_title, 0, 0, 1, 2)
        
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
        tools.setMaximumHeight(170)
        right.addWidget(tools)

        tabs = QTabWidget(objectName="detailsTabs")
        self.details_tabs = tabs
        self.log_output = QPlainTextEdit()
        self.log_output.setObjectName("mainLogOutput")
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(2000)
        self.log_output.setMinimumHeight(360)
        self.log_output.verticalScrollBar().setSingleStep(20)
        self.log_output.setPlaceholderText("系统控制台日志将在这里实时显示…")
        self.log_output.setAccessibleName("系统控制台实时日志")
        self.log_output.verticalScrollBar().valueChanged.connect(self._on_main_log_scroll_changed)
        self.log_output.viewport().setMouseTracking(True)
        self.log_output.installEventFilter(self)
        self.log_output.viewport().installEventFilter(self)
        self.log_output.verticalScrollBar().installEventFilter(self)

        self.scroll_to_bottom_btn = QPushButton("⬇️ 回到底部", self.log_output)
        self.scroll_to_bottom_btn.setObjectName("logScrollToBottomBtn")
        self.scroll_to_bottom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scroll_to_bottom_btn.setToolTip("跳转到最新日志并恢复实时滚屏跟随")
        self.scroll_to_bottom_btn.setStyleSheet("""
            QPushButton#logScrollToBottomBtn {
                background: #1E293B;
                color: #38BDF8;
                border: 1px solid #334155;
                border-radius: 13px;
                min-height: 24px;
                max-height: 26px;
                padding: 0 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#logScrollToBottomBtn:hover {
                background: #0F172A;
                color: #7DD3FC;
                border-color: #38BDF8;
            }
            QPushButton#logScrollToBottomBtn:pressed {
                background: #020617;
                color: #0284C7;
            }
        """)
        shadow = QGraphicsDropShadowEffect(self.scroll_to_bottom_btn)
        shadow.setBlurRadius(8)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(0, 2)
        self.scroll_to_bottom_btn.setGraphicsEffect(shadow)
        self.scroll_to_bottom_btn.clicked.connect(self._scroll_main_log_to_bottom_and_resume)
        self.scroll_to_bottom_btn.installEventFilter(self)
        self.scroll_to_bottom_btn.hide()

        tabs.addTab(self.log_output, "系统控制台日志")
        tabs.currentChanged.connect(self._on_details_tab_changed)

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
        self.activity_table.setObjectName("activityTable")
        tabs.addTab(self.activity_table, "近期活动记录")

        right.addWidget(tabs, 1)
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([820, 480])
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
        self.delete_selected_button.clicked.connect(self._confirm_and_delete_selected_profiles)
        self.automation_button.clicked.connect(self.configure_and_run_automation)
        self.pause_automation_button.clicked.connect(self.pause_selected_automation)
        self.resume_automation_button.clicked.connect(self.resume_selected_automation)
        self.cancel_automation_button.clicked.connect(self.cancel_selected_automation)
        self.engine_update_button.clicked.connect(self.check_automation_engine_update)
        self.check_update_action.triggered.connect(self.check_for_updates)
        self.reauth_button.clicked.connect(self.reauthenticate_agent)
        
        self.check_login_button.clicked.connect(lambda: self._run_read_only_task("x.check_login", "登录检查"))
        self.read_profile_button.clicked.connect(lambda: self._run_read_only_task("x.read_profile", "读取档案"))
        self.read_timeline_button.clicked.connect(lambda: self._run_read_only_task("x.read_timeline", "读取时间线"))
        self.search_button.clicked.connect(self.run_x_search)
        self.search_input.returnPressed.connect(self.run_x_search)
        
        self.table.itemSelectionChanged.connect(self._account_selection_changed)

    def _update_server_url(self) -> str:
        settings = getattr(self.controller, "settings", None)
        configured = str(getattr(settings, "server_url", "") or "").strip()
        if configured:
            return configured.rstrip("/")
        try:
            studio = self.controller.get_studio_token_config()
            configured = str(studio.get("server_url") or "").strip()
        except Exception:
            configured = ""
        return configured.rstrip("/") or "https://api.jaycwl.org"

    def check_for_updates(self) -> None:
        """Start an online release check."""
        if self._update_check_worker is not None and self._update_check_worker.isRunning():
            return
        server_url = self._update_server_url()
        if hasattr(self, "check_update_button"):
            self.check_update_button.setEnabled(False)
            self.check_update_button.setText("🔄 检查中…")
        if hasattr(self, "check_update_action"):
            self.check_update_action.setEnabled(False)
        self.statusBar().showMessage("正在检查在线版本…")
        worker = UpdateCheckWorker(server_url, VERSION, parent=self)
        self._update_check_worker = worker
        worker.check_finished.connect(lambda release: self._on_update_check_finished(release, server_url))
        worker.check_failed.connect(self._on_update_check_failed)
        worker.finished.connect(self._on_update_check_thread_finished)
        worker.start()

    def _on_update_check_finished(self, release: object, server_url: str) -> None:
        if not isinstance(release, ReleaseInfo):
            self._on_update_check_failed("更新服务器返回了无效数据")
            return
        if not release.has_update:
            self.statusBar().showMessage(f"当前已是最新版本 ({VERSION})", 4000)
            QMessageBox.information(self, "检查更新", f"当前已是最新版本（{VERSION}）\n暂无可用更新。")
            return
        if release.latest_version in UpdateNoticeDialog.ignored_versions:
            self.statusBar().showMessage(f"已忽略版本 {release.latest_version}", 4000)
            return
        self._update_notice_dialog = UpdateNoticeDialog(release, server_url=server_url, parent=self)
        self._update_notice_dialog.exec()
        self._update_notice_dialog.deleteLater()
        self._update_notice_dialog = None

    def _on_update_check_failed(self, message: str) -> None:
        self.statusBar().showMessage("在线版本检查失败", 5000)
        display_msg = str(message)
        if "403" in display_msg:
            display_msg = "更新服务访问受限（HTTP 403），可能受到网络安全策略拦截，请检查网络设置或稍后重试。"
        QMessageBox.warning(self, "检查更新", display_msg)

    def _on_update_check_thread_finished(self) -> None:
        worker = self._update_check_worker
        if hasattr(self, "check_update_button"):
            self.check_update_button.setEnabled(True)
            self.check_update_button.setText("🔄 检查更新")
        if hasattr(self, "check_update_action"):
            self.check_update_action.setEnabled(True)
        if worker is not None:
            worker.deleteLater()
        self._update_check_worker = None

    def _load_registry(self) -> None:
        try:
            self._refresh_group_combo()
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
        self.server_state_label.setProperty("state", "online" if server == "ONLINE" else "offline")
        self.agent_state_label.setProperty("state", "online" if agent == "ONLINE" else ("warning" if agent == "REAUTH_REQUIRED" else "offline"))

        needs_reauth = agent in {"UNCONFIGURED", "UNREGISTERED", "REAUTH_REQUIRED"}
        self.reauth_button.setText("激活运行端" if agent in {"UNCONFIGURED", "UNREGISTERED"} else "重新认证")
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
        self.heartbeat_label.setProperty("state", "neutral")
        for label in (self.server_state_label, self.agent_state_label, self.heartbeat_label):
            label.style().unpolish(label)
            label.style().polish(label)
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
        selection_model = self.table.selectionModel()
        if not selection_model:
            return ids
        selected_rows = sorted(list({index.row() for index in selection_model.selectedRows()}))
        for row in selected_rows:
            item = self.table.item(row, 1)
            if item and item.text().strip():
                ids.append(item.text().strip())
        return ids

    def _select_profile(self, profile_id: str, modifiers: Qt.KeyboardModifiers | None = None) -> None:
        target_row = -1
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item and item.text() == profile_id:
                target_row = row
                break
        if target_row < 0:
            return

        mods = modifiers or Qt.KeyboardModifier.NoModifier
        selection_model = self.table.selectionModel()
        if not selection_model:
            self.table.selectRow(target_row)
            return

        index = self.table.model().index(target_row, 0)
        if mods & Qt.KeyboardModifier.ControlModifier:
            selection_model.select(
                index,
                QItemSelectionModel.SelectionFlag.Toggle | QItemSelectionModel.SelectionFlag.Rows,
            )
            selection_model.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            current_row = selection_model.currentIndex().row()
            if current_row < 0:
                current_row = target_row
            start_r = min(current_row, target_row)
            end_r = max(current_row, target_row)
            top_left = self.table.model().index(start_r, 0)
            bottom_right = self.table.model().index(end_r, self.table.columnCount() - 1)
            selection = QItemSelection(top_left, bottom_right)
            selection_model.select(
                selection,
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
        else:
            self.table.selectRow(target_row)
            selection_model.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)

    def _toggle_select_profile(self, profile_id: str, checked: bool) -> None:
        selection_model = self.table.selectionModel()
        if not selection_model:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item and item.text() == profile_id:
                index = self.table.model().index(row, 0)
                flag = (
                    QItemSelectionModel.SelectionFlag.Select
                    if checked
                    else QItemSelectionModel.SelectionFlag.Deselect
                )
                selection_model.select(index, flag | QItemSelectionModel.SelectionFlag.Rows)
                break

    def _render_account_row(self, row: int, record: AccountRow, by_account_stats: dict[str, Any]) -> None:
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
            on_delete=self._confirm_and_delete_profile,
            on_toggle_select=self._toggle_select_profile,
            on_cancel_schedule=self._cancel_profile_schedule,
            on_run_now=self._run_profile_schedule_now,
        )
        self.table.setCellWidget(row, 0, card)
        self.table.setRowHeight(row, 78)

    def set_accounts(self, records: list[AccountRow]) -> None:
        records = records or []
        self._last_account_records = records
        self._account_rows_by_id = {record.profile_id: record for record in records}
        by_account_stats = self._statistics.get("by_account", {}) if isinstance(self._statistics, dict) else {}

        current_mode = self.group_filter_combo.currentData() if hasattr(self, "group_filter_combo") else "__ALL_FLAT__"
        if not current_mode:
            current_mode = "__ALL_FLAT__"

        signature = (
            current_mode,
            self._group_version,
            tuple(
                (
                    record.profile_id,
                    record.profile_name,
                    record.browser_status,
                    record.login_status,
                    record.x_username,
                    record.x_account_id,
                    record.account_status,
                    record.last_checked,
                    getattr(record, "schedule_mode", ""),
                    getattr(record, "schedule_status", ""),
                    getattr(record, "schedule_next_run", ""),
                    getattr(record, "automation_running", False),
                    getattr(record, "runtime_running", False),
                    repr({key: by_account_stats.get(key) for key in (record.profile_id, record.x_account_id, record.x_username, record.profile_name)}),
                )
                for record in records
            )
        )
        if signature == self._account_view_signature:
            self.summary_label.setText(f"{len(records)} 个账号")
            self._update_mini_metrics()
            return
        self._account_view_signature = signature

        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self._group_header_widgets.clear()
        self._group_row_ranges.clear()

        try:
            if current_mode == "__ALL_FLAT__":
                self.table.setRowCount(len(records))
                for row, record in enumerate(records):
                    self._render_account_row(row, record, by_account_stats)
                    self.table.setRowHidden(row, False)
            elif current_mode == "__ALL_GROUPED__":
                clusters = self.controller.list_profile_groups(records) if hasattr(self.controller, "list_profile_groups") else []
                total_rows = sum(1 + len(g.get("records", [])) for g in clusters)
                self.table.setRowCount(total_rows)

                curr_row = 0
                for g in clusters:
                    gid = str(g.get("group_id") or "")
                    gname = str(g.get("name") or "")
                    is_custom = bool(g.get("is_custom", False))
                    g_recs = g.get("records", [])
                    collapsed = bool(g.get("collapsed", False))

                    header_row = curr_row
                    curr_row += 1

                    for col in range(len(self.HEADERS)):
                        empty_item = QTableWidgetItem("")
                        empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
                        self.table.setItem(header_row, col, empty_item)

                    acct_rows = []
                    for record in g_recs:
                        r = curr_row
                        acct_rows.append(r)
                        curr_row += 1
                        self._render_account_row(r, record, by_account_stats)
                        self.table.setRowHidden(r, collapsed)

                    self._group_row_ranges[gid] = acct_rows

                    header_widget = GroupHeaderWidget(
                        group_id=gid,
                        group_name=gname,
                        is_custom=is_custom,
                        account_count=len(g_recs),
                        collapsed=collapsed,
                        on_toggle_collapse=self._toggle_group_collapse,
                        on_start_rotation=self._start_group_rotation_action,
                        on_stop_rotation=self._stop_group_rotation_action,
                        on_rename_group=self._prompt_rename_custom_group if is_custom else None,
                        on_delete_group=self._prompt_delete_custom_group if is_custom else None,
                        rotation_status=self.controller.get_group_rotation_status(gid) if hasattr(self.controller, "get_group_rotation_status") else {},
                    )
                    self._group_header_widgets[gid] = header_widget
                    self.table.setCellWidget(header_row, 0, header_widget)
                    self.table.setRowHeight(header_row, 44)
                    self.table.setRowHidden(header_row, False)
            else:
                clusters = self.controller.list_profile_groups(records) if hasattr(self.controller, "list_profile_groups") else []
                matching = [g for g in clusters if g.get("group_id") == current_mode]
                filtered_recs = matching[0]["records"] if matching else []
                self.table.setRowCount(len(filtered_recs))
                for row, record in enumerate(filtered_recs):
                    self._render_account_row(row, record, by_account_stats)
                    self.table.setRowHidden(row, False)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)

        self.summary_label.setText(f"{len(records)} 个账号")
        self._update_mini_metrics()
        self._account_selection_changed()

    def _refresh_group_combo(self) -> None:
        if not hasattr(self, "group_filter_combo"):
            return
        current_data = self.group_filter_combo.currentData() or "__ALL_FLAT__"
        self.group_filter_combo.blockSignals(True)
        self.group_filter_combo.clear()

        self.group_filter_combo.addItem("📋 全部账号 (平铺总览)", "__ALL_FLAT__")
        self.group_filter_combo.addItem("📑 全部账号 (按分组层级折叠展开)", "__ALL_GROUPED__")

        if hasattr(self.controller, "list_profile_groups"):
            recs = self._last_account_records or (self.controller.list_accounts() if hasattr(self.controller, "list_accounts") else [])
            clusters = self.controller.list_profile_groups(recs)
            custom_clusters = [c for c in clusters if c.get("is_custom")]
            node_clusters = [c for c in clusters if not c.get("is_custom")]

            if custom_clusters:
                for c in custom_clusters:
                    self.group_filter_combo.addItem(f"📁 [自定义] {c['name']} ({c['count']}个)", c["group_id"])

            if node_clusters:
                for c in node_clusters:
                    self.group_filter_combo.addItem(f"🌐 [同节点] {c['name']} ({c['count']}个)", c["group_id"])

        matched_idx = 0
        for idx in range(self.group_filter_combo.count()):
            if self.group_filter_combo.itemData(idx) == current_data:
                matched_idx = idx
                break
        self.group_filter_combo.setCurrentIndex(matched_idx)
        self.group_filter_combo.blockSignals(False)
        self._update_group_bar_actions()

    def _update_group_bar_actions(self) -> None:
        if not hasattr(self, "group_filter_combo"):
            return
        current_mode = self.group_filter_combo.currentData() or "__ALL_FLAT__"
        is_grouped = current_mode == "__ALL_GROUPED__"
        self.btn_expand_all.setVisible(is_grouped)
        self.btn_collapse_all.setVisible(is_grouped)

        if current_mode in ("__ALL_FLAT__", "__ALL_GROUPED__"):
            self.btn_toggle_group_rotation.setText("⚡ 启动组轮换")
            self.btn_toggle_group_rotation.setObjectName("miniRunButton")
            self.btn_toggle_group_rotation.setStyleSheet("color: #067647; background: #ECFDF3; border: 1px solid #ABEFC6;")
            self.group_rotation_status_label.setText("")
        else:
            is_rotating = self.controller.is_group_rotating(current_mode) if hasattr(self.controller, "is_group_rotating") else False
            status_data = self.controller.get_group_rotation_status(current_mode) if hasattr(self.controller, "get_group_rotation_status") else {}
            if is_rotating:
                self.btn_toggle_group_rotation.setText("⏹ 停止组轮换")
                self.btn_toggle_group_rotation.setObjectName("miniStopButton")
                self.btn_toggle_group_rotation.setStyleSheet("color: #B42318; background: #FEF3F2; border: 1px solid #FECDCA;")
                msg = status_data.get("last_message") or "🔄 正在调度轮换..."
                if len(msg) > 30:
                    msg = msg[:28] + "..."
                self.group_rotation_status_label.setText(msg)
            else:
                self.btn_toggle_group_rotation.setText("⚡ 启动组轮换")
                self.btn_toggle_group_rotation.setObjectName("miniRunButton")
                self.btn_toggle_group_rotation.setStyleSheet("color: #067647; background: #ECFDF3; border: 1px solid #ABEFC6;")
                self.group_rotation_status_label.setText("")

    def _on_group_filter_changed(self, index: int) -> None:
        self._update_group_bar_actions()
        self._account_view_signature = None
        self.set_accounts(self._last_account_records or (self.controller.list_accounts() if hasattr(self.controller, "list_accounts") else []))

    def _toggle_group_collapse(self, group_id: str, collapsed: bool) -> None:
        if hasattr(self.controller, "set_group_collapsed"):
            self.controller.set_group_collapsed(group_id, collapsed)
        rows = self._group_row_ranges.get(group_id, [])
        self.table.setUpdatesEnabled(False)
        try:
            for r in rows:
                self.table.setRowHidden(r, collapsed)
        finally:
            self.table.setUpdatesEnabled(True)

    def _expand_all_groups(self) -> None:
        all_gids = list(self._group_row_ranges.keys())
        if hasattr(self.controller, "set_all_groups_collapsed"):
            self.controller.set_all_groups_collapsed(all_gids, False)
        self.table.setUpdatesEnabled(False)
        try:
            for gid, rows in self._group_row_ranges.items():
                for r in rows:
                    self.table.setRowHidden(r, False)
                if gid in self._group_header_widgets:
                    self._group_header_widgets[gid].set_collapsed(False)
        finally:
            self.table.setUpdatesEnabled(True)

    def _collapse_all_groups(self) -> None:
        all_gids = list(self._group_row_ranges.keys())
        if hasattr(self.controller, "set_all_groups_collapsed"):
            self.controller.set_all_groups_collapsed(all_gids, True)
        self.table.setUpdatesEnabled(False)
        try:
            for gid, rows in self._group_row_ranges.items():
                for r in rows:
                    self.table.setRowHidden(r, True)
                if gid in self._group_header_widgets:
                    self._group_header_widgets[gid].set_collapsed(True)
        finally:
            self.table.setUpdatesEnabled(True)

    def _prompt_create_custom_group(self, profile_ids: list[str] | None = None) -> None:
        name, ok = QInputDialog.getText(self, "新建自定义分组", "请输入分组名称（例如：欧美区矩阵、备用号池）：")
        if not ok or not name.strip():
            return
        clean_name = name.strip()
        try:
            rec = self.controller.create_custom_group(clean_name)
            if profile_ids:
                self.controller.batch_assign_profiles_to_group(profile_ids, rec.group_id)
            self._group_version += 1
            self.refresh_account_view()
            self.statusBar().showMessage(f"已成功创建分组：{rec.name}", 4000)
        except Exception as e:
            QMessageBox.warning(self, "创建失败", f"创建分组失败：{e}")

    def _prompt_rename_custom_group(self, group_id: str, old_name: str) -> None:
        new_name, ok = QInputDialog.getText(self, "重命名分组", "请输入新的分组名称：", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        try:
            self.controller.rename_custom_group(group_id, new_name.strip())
            self._group_version += 1
            self.refresh_account_view()
            self.statusBar().showMessage(f"分组已重命名为：{new_name.strip()}", 4000)
        except Exception as e:
            QMessageBox.warning(self, "重命名失败", f"重命名分组失败：{e}")

    def _prompt_delete_custom_group(self, group_id: str) -> None:
        reply = QMessageBox.question(
            self,
            "确认删除分组",
            "确定要删除该自定义分组吗？\n\n注意：组内账号不会被删除，将自动恢复按节点或直连网络归类。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.controller.delete_custom_group(group_id)
        self._group_version += 1
        self.refresh_account_view()
        self.statusBar().showMessage("分组已删除，名下账号已恢复自动归类", 4000)

    def _batch_assign_to_group(self, profile_ids: list[str], group_id: str | None) -> None:
        self.controller.batch_assign_profiles_to_group(profile_ids, group_id)
        self._group_version += 1
        self.refresh_account_view()
        msg = "已移出自定义分组，恢复按节点自动归类" if not group_id else "已成功将账号移入选定分组"
        self.statusBar().showMessage(msg, 4000)

    def _toggle_current_group_rotation(self) -> None:
        current_mode = self.group_filter_combo.currentData() if hasattr(self, "group_filter_combo") else "__ALL_FLAT__"
        if current_mode in ("__ALL_FLAT__", "__ALL_GROUPED__"):
            clusters = self.controller.list_profile_groups(self._last_account_records or (self.controller.list_accounts() if hasattr(self.controller, "list_accounts") else []))
            eligible = [c for c in clusters if c.get("records")]
            if not eligible:
                QMessageBox.information(self, "提示", "当前没有可供轮换调度的分组。")
                return
            items = [f"{c['name']} ({len(c['records'])} 个账号)" for c in eligible]
            chosen, ok = QInputDialog.getItem(self, "启动组内轮换调度", "请选择要启动轮换调度的分组：", items, 0, False)
            if ok and chosen:
                idx = items.index(chosen)
                target = eligible[idx]
                self._start_group_rotation_action(target["group_id"], target["name"])
        else:
            if self.controller.is_group_rotating(current_mode):
                self._stop_group_rotation_action(current_mode)
            else:
                group_name = self.group_filter_combo.currentText().split("(")[0].strip()
                self._start_group_rotation_action(current_mode, group_name)

    def _start_group_rotation_action(self, group_id: str, group_name: str) -> None:
        clusters = self.controller.list_profile_groups(self._last_account_records or (self.controller.list_accounts() if hasattr(self.controller, "list_accounts") else []))
        matching = [g for g in clusters if g["group_id"] == group_id]
        if not matching or not matching[0]["records"]:
            QMessageBox.information(self, "提示", f"分组【{group_name}】名下暂无可用账号。")
            return
        count = len(matching[0]["records"])
        reply = QMessageBox.question(
            self,
            "启动组内防风控串行轮换",
            f"确定启动分组【{group_name}】的轮换调度吗？\n\n"
            f"• 组内共 {count} 个账号，将按序单批次排队执行\n"
            f"• 同一时间仅有 1 个账号在网络上活跃（单 IP 防并发检测）\n"
            f"• 每次换号自动执行 25~45 秒自然真人微静默\n"
            f"• 全组账号自动巡检直到今日配额全部达成",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def status_callback(gid: str, st: str, snap: dict[str, Any]) -> None:
            self.rotation_status_signal.emit(gid, st, snap)

        try:
            self.controller.start_group_rotation(
                group_id=group_id,
                group_name=group_name,
                on_status_change=status_callback,
            )
            self._update_group_bar_actions()
            if group_id in self._group_header_widgets:
                self._group_header_widgets[group_id].update_rotation_status({"status": "RUNNING", "last_message": "启动中..."})
            self.statusBar().showMessage(f"已启动分组【{group_name}】串行轮换调度", 4000)
        except Exception as e:
            QMessageBox.warning(self, "启动失败", f"启动分组轮换失败：{e}")

    def _stop_group_rotation_action(self, group_id: str) -> None:
        self.controller.stop_group_rotation(group_id)
        self._update_group_bar_actions()
        if group_id in self._group_header_widgets:
            self._group_header_widgets[group_id].update_rotation_status({"status": "STOPPED", "last_message": "轮换已停止"})
        self.statusBar().showMessage("分组轮换调度已停止", 4000)

    def _on_rotation_status_received(self, group_id: str, status: str, snapshot: dict[str, Any]) -> None:
        if group_id in self._group_header_widgets:
            self._group_header_widgets[group_id].update_rotation_status(snapshot)
        current_group = self.group_filter_combo.currentData() if hasattr(self, "group_filter_combo") else ""
        if current_group == group_id:
            self._update_group_bar_actions()
        msg = snapshot.get("last_message") or ""
        if msg:
            self.statusBar().showMessage(f"[分组轮换] {msg}", 5000)

    def refresh_account_view(self) -> None:
        self._account_view_signature = None
        self._refresh_group_combo()
        self.set_accounts(self._last_account_records or (self.controller.list_accounts() if hasattr(self.controller, "list_accounts") else []))

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
        try:
            if hasattr(self.controller, "rotation_manager") and hasattr(self.controller.rotation_manager, "stop_all"):
                self.controller.rotation_manager.stop_all()
                for gid, widget in self._group_header_widgets.items():
                    widget.update_rotation_status({"status": "STOPPED", "last_message": "轮换已停止"})
                self._update_group_bar_actions()
        except Exception:
            pass
        self._run_profile_action("停止全部", self.controller.stop_profile, [row.profile_id for row in self.controller.list_accounts()])

    def _confirm_and_delete_profile(self, profile_id: str) -> None:
        account = self._account_rows_by_id.get(profile_id)
        name = (account.profile_name if account and account.profile_name else profile_id)
        reply = QMessageBox.question(
            self,
            "确认删除档案",
            f"确定要删除档案「{name}」吗？\n\n"
            "此操作将终止该档案可能正在执行的自动化任务，并从控制中心和老谷浏览器中移除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def do_delete():
            return self.controller.delete_account(profile_id)

        def on_done(success: bool):
            if success:
                self.statusBar().showMessage(f"已成功删除档案：{name}", 4000)
                self._load_registry()
            else:
                self.statusBar().showMessage(f"删除档案失败：{name}", 4000)

        self._run_job(f"删除档案 {name}", do_delete, on_done)

    def _confirm_and_delete_selected_profiles(self) -> None:
        ids = self.selected_profile_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先在列表中选中要删除的账号档案（支持勾选多选或按住 Ctrl 点击）。")
            return

        names = []
        for pid in ids:
            acct = self._account_rows_by_id.get(pid)
            names.append(acct.profile_name if acct and acct.profile_name else pid)
        display_names = "、".join(names[:5]) + (f" 等共 {len(names)} 个" if len(names) > 5 else "")

        reply = QMessageBox.question(
            self,
            "确认批量删除",
            f"确定要删除选中的 {len(ids)} 个档案实例（{display_names}）吗？\n\n"
            "此操作将终止所选档案所有正在运行的自动化任务，并从控制中心和老谷浏览器中移除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def do_delete():
            return self.controller.delete_accounts(ids)

        def on_done(count: int):
            self.statusBar().showMessage(f"已成功删除 {count} 个档案实例", 4000)
            self._load_registry()

        self._run_job(f"批量删除 {len(ids)} 个档案", do_delete, on_done)

    def _show_table_context_menu(self, pos: QPoint) -> None:
        ids = self.selected_profile_ids()
        menu = QMenu(self)
        if ids:
            count = len(ids)
            label = f"启动选中的 {count} 个档案" if count > 1 else "启动此档案"
            run_action = menu.addAction(line_icon("play", "#059669", 14), label)
            run_action.triggered.connect(lambda: self._run_profile_action("启动", self.controller.start_profile, ids))

            stop_label = f"停止选中的 {count} 个档案" if count > 1 else "停止此档案"
            stop_action = menu.addAction(line_icon("stop", "#DC2626", 14), stop_label)
            stop_action.triggered.connect(lambda: self._run_profile_action("停止", self.controller.stop_profile, ids))

            if count == 1:
                cfg_action = menu.addAction(line_icon("settings", "#475569", 14), "配置自动化任务")
                cfg_action.triggered.connect(lambda: self.configure_and_run_automation(ids[0]))

            menu.addSeparator()
            # 📁 移入自定义分组子菜单
            group_menu = menu.addMenu(line_icon("folder", "#2457D6", 14), f"📁 移入自定义分组 ({count} 个档案)")
            custom_groups = self.controller.list_custom_groups() if hasattr(self.controller, "list_custom_groups") else []
            if custom_groups:
                for grp in custom_groups:
                    g_action = group_menu.addAction(f"📁 {grp.name}")
                    g_action.triggered.connect(lambda checked=False, gid=grp.group_id: self._batch_assign_to_group(ids, gid))
                group_menu.addSeparator()
            new_grp_action = group_menu.addAction("➕ 新建分组并移入...")
            new_grp_action.triggered.connect(lambda checked=False: self._prompt_create_custom_group(ids))
            unassign_action = group_menu.addAction("🌐 恢复按节点自动归类 (移出自定义组)")
            unassign_action.triggered.connect(lambda checked=False: self._batch_assign_to_group(ids, None))
            menu.addSeparator()
            del_label = f"🗑 删除选中的 {count} 个档案 (Delete)" if count > 1 else "🗑 删除此档案 (Delete)"
            del_action = menu.addAction(line_icon("trash", "#DC2626", 14), del_label)
            del_action.triggered.connect(self._confirm_and_delete_selected_profiles)
            menu.addSeparator()

        select_all_action = menu.addAction("全选所有档案")
        select_all_action.triggered.connect(self.table.selectAll)
        if ids:
            clear_sel_action = menu.addAction("取消全选")
            clear_sel_action.triggered.connect(self.table.clearSelection)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _open_automation_config_async(self, target_id: str, account_name: str) -> None:
        if target_id in self._config_load_in_flight:
            self.statusBar().showMessage("正在读取配置，请稍候…")
            return
        self._config_load_in_flight.add(target_id)

        # 💡 同步直接读取本地档案配置，杜绝异步回调覆写用户界面选项的竞态 Bug
        initial_cfg = self.controller.get_profile_task_config(target_id) if hasattr(self.controller, "get_profile_task_config") else {}
        dialog = TaskConfigDialog(initial_cfg or {}, self, self._automation_engines_cache)
        self._config_dialogs[target_id] = dialog
        self.statusBar().showMessage("配置窗口已打开…")

        if self._automation_engines_cache is None and hasattr(self.controller, "list_automation_engines"):
            def apply_engines(value: Any) -> None:
                engines = value if isinstance(value, list) else []
                if engines:
                    self._automation_engines_cache = list(engines)
                    if dialog.isVisible():
                        dialog.set_engines(engines)
                self.statusBar().showMessage("自动化引擎列表已刷新")

            self._run_background(self.controller.list_automation_engines, apply_engines)

        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self._config_dialogs.pop(target_id, None)
        self._config_load_in_flight.discard(target_id)
        if not accepted:
            return
        config = dialog.config()
        config["account_tag"] = account_name
        is_scheduled = (config.get("schedule_mode") == "scheduled")
        job_label = f"保存定时排期：{account_name} ({target_id})" if is_scheduled else f"启动自动化引擎：{account_name} ({target_id})"
        self._run_job(
            job_label,
            lambda: self.controller.start_automation_task(target_id, config),
            lambda result: self._automation_finished(target_id, result),
        )

    def configure_and_run_automation(self, profile_id: str | None = None) -> None:
        if isinstance(profile_id, str) and profile_id:
            target_id = profile_id
        else:
            ids = self.selected_profile_ids()
            if not ids:
                QMessageBox.information(self, "提示", "请先在列表中勾选要配置的账号档案。")
                return
            target_id = ids[0]
            if len(ids) > 1:
                self.statusBar().showMessage(f"多选提示：正在为首个勾选档案 [{target_id}] 打开配置面板…", 4000)

        if target_id in self._config_load_in_flight:
            self.statusBar().showMessage("正在读取配置，请稍候…")
            return
        record = self._account_rows_by_id.get(target_id)
        account_name = (record.profile_name if record else "") or target_id
        self._open_automation_config_async(target_id, account_name)

    def _run_selected_automation_control(self, label: str, function: Callable[[str], Any]) -> None:
        ids = self._require_selection(single=False)
        if not ids:
            return

        def batch_action():
            results = {}
            for pid in ids:
                try:
                    results[pid] = function(pid)
                except Exception as exc:
                    results[pid] = {"status": "ERROR", "error": str(exc)}
            return results

        def on_finished(results: dict[str, Any]):
            success_pids = [pid for pid, r in results.items() if r.get("status") != "ERROR"]
            fail_pids = [pid for pid, r in results.items() if r.get("status") == "ERROR"]
            if len(ids) == 1:
                pid = ids[0]
                status = str(results.get(pid, {}).get("status") or "UNKNOWN")
                self._log(f"[自动化保护] profile={pid} action={label} state={status}")
                self.statusBar().showMessage(f"档案 {pid} 自动化：{label}（{status}）", 5000)
            else:
                msg = f"[自动化控制] 批量{label}完成：已选 {len(ids)} 个账号，成功 {len(success_pids)} 个" + (f"，失败 {len(fail_pids)} 个" if fail_pids else "")
                self._log(msg)
                self.statusBar().showMessage(msg, 5000)

            # 暂停/恢复/取消操作完成后，自动关闭勾选状态（关闭勾）
            self.clear_all_selection()
            self.refresh_account_view()

        title = f"{label}自动化：{ids[0]}" if len(ids) == 1 else f"批量{label}自动化：{len(ids)}个档案"
        self._run_job(title, batch_action, on_finished)

    def pause_selected_automation(self) -> None:
        self._run_selected_automation_control("暂停", self.controller.pause_automation_task)

    def resume_selected_automation(self) -> None:
        self._run_selected_automation_control("恢复", self.controller.resume_automation_task)

    def cancel_selected_automation(self) -> None:
        self._run_selected_automation_control("取消", self.controller.cancel_automation_task)

    def select_all_profiles(self) -> None:
        self.table.selectAll()

    def clear_all_selection(self) -> None:
        self.table.clearSelection()

    def _on_header_select_all_toggled(self, checked: bool) -> None:
        if checked:
            self.select_all_profiles()
        else:
            self.clear_all_selection()

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
            is_stop_action = "停止" in label or "stop" in label.lower()
            def safe_action():
                errors = []
                for pid in selected:
                    try:
                        function(pid)
                    except Exception as e:
                        err_str = str(e)
                        if "10061" in err_str or "拒绝" in err_str or "Cannot connect" in err_str or "not found" in err_str.lower() or "404" in err_str:
                            pass
                        elif is_stop_action:
                            self._log(f"[提示] 停止档案 {pid} 忽略异常: {err_str}")
                        else:
                            errors.append((pid, err_str))
                if errors:
                    raise RuntimeError(f"档案 {errors[0][0]} 操作失败: {errors[0][1]}")
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
        for button in (self.pause_automation_button, self.resume_automation_button, self.cancel_automation_button):
            button.setEnabled(not busy and "automation.run" in self._capabilities)
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
        id_set = set(ids)
        for row in range(self.table.rowCount()):
            card = self.table.cellWidget(row, 0)
            if isinstance(card, AccountCardWidget):
                card.set_selected(card.profile_id in id_set)

        # 同步全选复选框状态
        if hasattr(self, "cb_select_all_header") and self.cb_select_all_header:
            card_count = sum(1 for r in range(self.table.rowCount()) if isinstance(self.table.cellWidget(r, 0), AccountCardWidget))
            self.cb_select_all_header.blockSignals(True)
            self.cb_select_all_header.setChecked(bool(card_count > 0 and len(ids) == card_count))
            self.cb_select_all_header.blockSignals(False)

        # 同步总览统计
        total_accounts = len(self._account_rows_by_id)
        if hasattr(self, "summary_label") and self.summary_label:
            if ids:
                self.summary_label.setText(f"{total_accounts} 个账号 (已选 {len(ids)})")
            else:
                self.summary_label.setText(f"{total_accounts} 个账号")

        if not ids:
            self.selected_profile_label.setText("尚未选择档案")
            self.selected_runtime_label.setText("运行状态：—")
            return
        if len(ids) > 1:
            names = []
            for pid in ids:
                acct = self._account_rows_by_id.get(pid)
                names.append(acct.profile_name if acct and acct.profile_name else pid)
            display_str = "、".join(names[:4]) + (f" 等共 {len(ids)} 个" if len(ids) > 4 else "")
            self.selected_profile_label.setText(f"已选中 {len(ids)} 个档案（{display_str}）")
            self.selected_profile_label.setToolTip(f"选中 ID: {', '.join(ids)}")
            self.selected_runtime_label.setText(f"运行状态：多选就绪（支持批量暂停/恢复/取消/启停/删除选中 {len(ids)} 个）")
            return
        pid = ids[0]
        account = self._account_rows_by_id.get(pid)
        profile = next((item for item in self._profiles if str(item.get("profileId") or item.get("profile_id") or "") == pid), {})
        name = str(profile.get("profileName") or profile.get("profile_name") or (account.profile_name if account else "") or pid)
        handle = account.x_username if account and account.x_username else "未绑定 X 账号"
        running = bool(profile.get("running")) if profile else bool(account and account.runtime_running)
        self.selected_profile_label.setText(f"{name}  ·  {handle}")
        self.selected_profile_label.setToolTip(f"Profile ID: {pid}")
        runtime_text = f"运行状态：{'运行中' if running else '未启动'}"
        try:
            safety = self.controller.automation_safety_status(pid)
            risk = safety.get("risk") if isinstance(safety, dict) else {}
            safety_state = str((risk or {}).get("status") or "READY")
            safety_labels = {
                "PAUSED": "自动化已暂停",
                "CHALLENGE_REQUIRED": "自动化需人工验证",
                "RATE_LIMITED": "自动化已因访问受限暂停",
                "NOT_LOGGED_IN": "自动化需重新登录",
                "CANCEL_REQUESTED": "自动化正在取消",
            }
            if safety.get("running"):
                runtime_text += " · 自动化运行中"
            elif safety_state in safety_labels:
                runtime_text += f" · {safety_labels[safety_state]}"
        except Exception:
            pass
        self.selected_runtime_label.setText(runtime_text)

    def _automation_finished(self, profile_id: str, result: Any) -> None:
        status = result.get("status", "SUCCESS") if isinstance(result, dict) else "SUCCESS"
        error_msg = str(result.get("error", "")) if isinstance(result, dict) else ""
        target_url = str(result.get("url", "")) if isinstance(result, dict) else ""
        self._log(f"[自动化状态] profile={profile_id} state={status}")
        if error_msg:
            self._log(f"[自动化状态] profile={profile_id} error={error_msg[:300]}")

        if status == "CHALLENGE_REQUIRED" or "account/access" in error_msg or "account/access" in target_url:
            self.statusBar().showMessage(f"档案 {profile_id} 触发人机验证，任务已自动终止")
            self._log(f"风控警报：档案 {profile_id} 遇到人机验证 (account/access)，自动化已停止")

            QMessageBox.warning(
                self,
                "触发 X 平台人机验证",
                f"档案【{profile_id}】在运行时触发了 Cloudflare / X 平台人机验证。\n\n"
                f"出于账号安全保护，自动化任务已【强制终止】。\n\n"
                f"请切到对应的浏览器窗口手动完成验证，完成后即可重新启动。"
            )
            self._load_local_statistics()
            return

        if status == "NEEDS_ATTENTION":
            reason = str(result.get("reason") or error_msg or "账号存在需要人工确认的保护状态") if isinstance(result, dict) else ""
            self.statusBar().showMessage(f"档案 {profile_id} 需要恢复后重新核验")
            self._log(f"档案 {profile_id} 被本地保护状态拦截：{reason}")
            prompt = QMessageBox(self)
            prompt.setIcon(QMessageBox.Icon.Warning)
            prompt.setWindowTitle("自动化需要确认")
            prompt.setText("此账号保留了此前的保护状态，自动化尚未启动。")
            prompt.setInformativeText(
                f"原因：{reason}\n\n"
                "选择“恢复并重新核验”会清除旧状态，并立即打开 X 首页重新检查登录；"
                "如果实际未登录，系统仍会再次停止任务。"
            )
            retry_button = prompt.addButton("恢复并重新核验", QMessageBox.ButtonRole.AcceptRole)
            prompt.addButton("暂不恢复", QMessageBox.ButtonRole.RejectRole)
            prompt.exec()
            if prompt.clickedButton() is retry_button:
                self._resume_and_retry_automation(profile_id)
            self._load_local_statistics()
            self._load_registry()
            return

        if status == "LOGIN_STATUS_UNKNOWN":
            self.statusBar().showMessage(f"档案 {profile_id} 登录状态待验证，可检查页面后重试")
            self._log(f"档案 {profile_id} 未能核验登录态，未暂停账号；请确认 X 首页加载后重试")
            self._load_local_statistics()
            self._load_registry()
            return

        if status == "SCHEDULED":
            sched_for = str(result.get("scheduled_for", "")) if isinstance(result, dict) else ""
            sched_type = str(result.get("schedule_type", "once")) if isinstance(result, dict) else "once"
            type_desc = "每日定时" if sched_type == "daily" else "单次定时"
            self.statusBar().showMessage(f"档案 {profile_id} {type_desc}已成功排期：将于 {sched_for} 自动启动")
            self._log(f"档案 {profile_id} {type_desc}已成功排期，预计启动时间: {sched_for}")
            self._load_registry()
            return

        self.statusBar().showMessage(f"档案 {profile_id} 自动化任务已下发：{status}")
        self._log(f"档案 {profile_id} 自动化任务下发完成，状态: {status}")

        # 💡【核心修复】任务完成后同时刷新账号资产与今日数据统计
        self._load_local_statistics()
        self._load_registry()

    def _resume_and_retry_automation(self, profile_id: str) -> None:
        """Run only after the operator explicitly confirms recovery in the UI."""
        def resume_and_start() -> dict[str, Any]:
            self.controller.resume_automation_task(profile_id)
            snapshot = self.controller.get_profile_task_config(profile_id)
            config = snapshot.get("active") if isinstance(snapshot, dict) else None
            if not isinstance(config, dict):
                raise RuntimeError("未找到已保存的自动化配置，请重新打开配置窗口后再试")
            return self.controller.start_automation_task(profile_id, dict(config))

        self._run_job(
            f"恢复并重新核验自动化：{profile_id}",
            resume_and_start,
            lambda retry_result: self._automation_finished(profile_id, retry_result),
        )

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
        if "10061" in message or "拒绝" in message or "Cannot connect" in message or ("停止" in label and ("not found" in message.lower() or "404" in message)):
            self._log(f"[提示] 目标浏览器服务不可达或已处于停止状态: {message}")
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
        by_account_stats = self._statistics.get("by_account", {}) if isinstance(self._statistics, dict) else {}
        self._mini_window.update_accounts_state(
            records=records,
            statistics=self._statistics,
            by_account_stats=by_account_stats,
            recent_logs=list(self._recent_log_lines)[-30:],
        )

    def _retry_profile_from_mini(self, profile_id: str) -> None:
        self._run_profile_action("重试账号", self.controller.start_profile, [profile_id])

    def _stop_profile_from_mini(self, profile_id: str) -> None:
        self._run_profile_action("停止账号", self.controller.stop_profile, [profile_id])

    def _skip_profile_from_mini(self, profile_id: str) -> None:
        self._log(f"账号 [{profile_id}] 已在浮窗跳过当前轮次调度。")

    def _test_profile_proxy_from_mini(self, profile_id: str) -> None:
        self._log(f"正在测试账号 [{profile_id}] 代理连通性...")
        rec = self._account_rows_by_id.get(profile_id)
        if rec and rec.proxy_host and rec.proxy_port:
            try:
                import socket
                s = socket.create_connection((rec.proxy_host, int(rec.proxy_port)), timeout=3)
                s.close()
                self._log(f"账号 [{profile_id}] 代理端口 {rec.proxy_port} 握手正常。")
            except Exception as e:
                self._log(f"账号 [{profile_id}] 代理检测失败: {e}")
        else:
            self._log(f"账号 [{profile_id}] 未配置有效代理主机或端口。")

    def _open_profile_log_from_mini(self, profile_id: str) -> None:
        path = self._log_tail_path
        if path and os.path.exists(path):
            try:
                os.startfile(path)
            except Exception as e:
                self._log(f"打开日志文件失败: {e}")

    def _retry_all_abnormal_from_mini(self) -> None:
        pids = [
            pid for pid, r in self._account_rows_by_id.items()
            if str(getattr(r, "proxy_status", "")).upper() in {"ERROR", "TIMEOUT", "DISCONNECTED"}
            or not (r.runtime_running and r.runtime_debug_ready)
        ]
        if pids:
            self._run_profile_action("批量重试异常账号", self.controller.start_profile, pids)
        else:
            self._log("当前无异常账号需重试。")

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

    def _restore_from_mini_window(self) -> None:
        self._mini_window.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._scroll_main_log_to_bottom_and_resume()

    def _setup_tray_icon(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(application_icon(), self)
        tray.setToolTip("老谷控制中心（后台运行）")
        menu = QMenu()
        open_action = QAction("打开控制中心", menu)
        open_action.triggered.connect(self._restore_from_mini_window)
        menu.addAction(open_action)
        mini_action = QAction("显示日志浮窗", menu)
        mini_action.triggered.connect(self._show_mini_window)
        menu.addAction(mini_action)
        menu.addSeparator()
        exit_action = QAction("退出", menu)
        exit_action.triggered.connect(self._force_exit_application)
        menu.addAction(exit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray_icon = tray
        if hasattr(self, "_mini_window") and self._mini_window:
            self._mini_window.set_tray_icon(tray)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_mini_window()

    def _hide_mini_window(self) -> None:
        """隐藏监控浮窗，但不停止后台 Agent 或主窗口生命周期。"""
        self._mini_window.hide()
        if self._tray_icon is not None:
            self._tray_icon.showMessage(
                "老谷控制中心",
                "控制中心已隐藏到后台，双击托盘图标可恢复。",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def _confirm_stop_all_from_mini(self) -> None:
        """来自悬浮监控窗的停止全部请求：已由悬浮窗内部蒙层完成二次确认，直接执行安全停止全部。"""
        self.stop_all()

    def _open_telegram_config(self) -> None:
        dialog = TelegramConfigDialog(self.controller, parent=self)
        dialog.exec()

    def _open_studio_token_config(self) -> None:
        dialog = StudioTokenConfigDialog(self.controller, parent=self)
        dialog.exec()

    def _open_blacklist_config(self) -> None:
        dialog = BlacklistConfigDialog(self.controller, parent=self)
        dialog.exec()
        self._update_blacklist_button_state()

    def _update_blacklist_button_state(self) -> None:
        if not hasattr(self, "blacklist_config_button"):
            return
        cfg = self.controller.get_blacklist_config() if hasattr(self.controller, "get_blacklist_config") else {}
        enabled = bool(cfg.get("enabled", True))
        words_count = len(cfg.get("blacklist_words") or [])
        if enabled:
            self.blacklist_config_button.setText("🛡️ 敏感词库")
            self.blacklist_config_button.setToolTip(f"全字段动态敏感词库（当前状态：已启用 · 共 {words_count} 个特征词）")
        else:
            self.blacklist_config_button.setText("⚪ 敏感词库 (已停用)")
            self.blacklist_config_button.setToolTip("全字段动态敏感词库（当前状态：一键停用中 · 不过滤敏感词）")

    def _send_telegram_summary_report(self) -> None:
        cfg = self.controller.get_telegram_config() if hasattr(self.controller, "get_telegram_config") else {}
        if not cfg.get("enabled"):
            res = QMessageBox.question(
                self,
                "提示",
                "当前尚未开启 Telegram 战报通知，是否立即前往配置？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if res == QMessageBox.StandardButton.Yes:
                self._open_telegram_config()
            return

        self.send_report_button.setEnabled(False)
        self.send_report_button.setText("正在推送…")
        QApplication.processEvents()

        ok, msg = self.controller.send_telegram_summary_report()
        self.send_report_button.setEnabled(True)
        self.send_report_button.setText("📊 推送当前战报")

        if ok:
            QMessageBox.information(self, "推送成功", "当前大盘战报已成功推送至您的 Telegram！请在手机查收。")
        else:
            QMessageBox.warning(self, "推送失败", f"大盘战报推送失败: {msg}\n\n请检查网络连接或在【TG通知】中配置本地代理。")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if hasattr(self, "log_output") and watched is self.log_output:
            if event.type() == QEvent.Type.Resize:
                self._reposition_scroll_to_bottom_btn()
        elif hasattr(self, "log_output") and (
            watched is self.log_output.viewport()
            or watched is self.log_output.verticalScrollBar()
            or (hasattr(self, "scroll_to_bottom_btn") and watched is self.scroll_to_bottom_btn)
        ):
            if event.type() in (
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.Wheel,
                QEvent.Type.KeyPress,
            ):
                if not getattr(self, "_auto_scroll_main_log", True) and not getattr(self, "_closing", False):
                    if hasattr(self, "_log_idle_timer"):
                        self._log_idle_timer.start(10000)
        return super().eventFilter(watched, event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and not self._closing:
            if self.isMinimized():
                self._scroll_main_log_to_bottom_and_resume()
                QTimer.singleShot(0, self._show_mini_window)

    def _force_exit_application(self) -> None:
        """从悬浮窗或托盘一键安全完整退出整个程序"""
        self.close()
        app = QApplication.instance()
        if app:
            app.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if getattr(self, "_closing", False):
            event.accept()
            return
        self._closing = True

        # 1. 立即停止所有轮询定时器
        if hasattr(self, "_log_tail_timer"):
            self._log_tail_timer.stop()
        if hasattr(self, "_log_idle_timer"):
            self._log_idle_timer.stop()
        if hasattr(self, "_agent_status_timer"):
            self._agent_status_timer.stop()
        if hasattr(self, "_statistics_timer"):
            self._statistics_timer.stop()
        if hasattr(self, "auto_refresh_timer"):
            self.auto_refresh_timer.stop()

        # 2. 彻底关闭并销毁悬浮小窗与系统托盘
        if hasattr(self, "_mini_window") and self._mini_window:
            try:
                self._mini_window.hide()
                self._mini_window.close()
                self._mini_window.deleteLater()
            except Exception:
                pass

        if hasattr(self, "_tray_icon") and self._tray_icon:
            try:
                self._tray_icon.hide()
                self._tray_icon.deleteLater()
                self._tray_icon = None
            except Exception:
                pass

        # 3. 断开所有 Worker 信号
        for worker in tuple(getattr(self, "_workers", ())):
            for sig in (worker.signals.finished, worker.signals.error, worker.signals.done):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
        self._workers.clear()

        for worker in tuple(getattr(self, "_background_workers", ())):
            for sig in (worker.signals.finished, worker.signals.error, worker.signals.done):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
        self._background_workers.clear()

        # 4. 快速终止后台控制器与自动化服务
        try:
            self.controller.stop_agent_service()
        except Exception:
            pass

        # 5. 还原标准流重定向
        if getattr(sys, "stdout", None) is getattr(self, "stdout_stream", None):
            sys.stdout = getattr(self, "_original_stdout", sys.__stdout__)
        if getattr(sys, "stderr", None) is getattr(self, "stdout_stream", None):
            sys.stderr = getattr(self, "_original_stderr", sys.__stderr__)

        event.accept()

        # 6. 通知 Qt 循环立即退出，避免残留
        app = QApplication.instance()
        if app:
            app.quit()
