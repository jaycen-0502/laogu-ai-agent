"""2026 Modern Light SaaS Premium QSS Theme for Laogu Control Center."""

APP_STYLE = """
/* ================= 全局基础配置 ================= */
QMainWindow, QWidget {
    background: #F4F5F7;
    color: #1E293B;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}

QLabel {
    background: transparent;
    color: #334155;
}

QDialog {
    background: #FFFFFF;
    color: #0F172A;
}

QToolTip {
    color: #F8FAFC;
    background: #0F172A;
    border: 1px solid #334155;
    padding: 6px 8px;
    border-radius: 6px;
}

/* ================= 顶栏 Header ================= */
QFrame#header {
    background: #FFFFFF;
    border-bottom: 1px solid #E2E8F0;
}

QLabel#title {
    font-size: 20px;
    font-weight: 800;
    color: #0F172A;
    letter-spacing: -0.3px;
}

QLabel#subtitle {
    color: #64748B;
    font-size: 12px;
}

/* ================= 状态徽章与心跳条 ================= */
QLabel#statusBadge {
    background: #F1F5F9;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 4px 12px;
    color: #475569;
    font-size: 12px;
    font-weight: 600;
}

QLabel#liveStatus, QLabel#liveStatusOnline, QLabel#liveStatusError {
    background: #FFFFFF;
    border-bottom: 1px solid #E2E8F0;
    padding: 7px 26px;
    font-weight: 600;
    font-size: 12px;
}

QLabel#liveStatus { color: #64748B; }
QLabel#liveStatusOnline { color: #059669; background: #ECFDF5; }
QLabel#liveStatusError { color: #DC2626; background: #FEF2F2; }

/* ================= 通用按钮样式 ================= */
QPushButton {
    min-height: 34px;
    padding: 0 16px;
    color: #334155;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
    background: #FFFFFF;
    font-weight: 600;
}

QPushButton:hover {
    background: #F8FAFC;
    border-color: #94A3B8;
    color: #0F172A;
}

QPushButton:pressed {
    background: #E2E8F0;
}

QPushButton:focus {
    border: 2px solid #93C5FD;
    padding-left: 15px;
    padding-right: 15px;
}

QPushButton:disabled {
    color: #94A3B8;
    background: #F1F5F9;
    border-color: #E2E8F0;
}

/* 核心主按钮（品牌蓝色） */
QPushButton#primaryButton, QPushButton#runAllButton {
    font-weight: 700;
    color: #FFFFFF;
    background: #2563EB;
    border: 1px solid #1D4ED8;
    border-radius: 8px;
}

QPushButton#primaryButton:hover, QPushButton#runAllButton:hover {
    background: #3B82F6;
    border-color: #2563EB;
}

QPushButton#primaryButton:pressed, QPushButton#runAllButton:pressed {
    background: #1E40AF;
}

/* 危险/停止按钮（柔和红） */
QPushButton#stopAllButton {
    color: #FFFFFF;
    font-weight: 700;
    background: #EF4444;
    border: 1px solid #DC2626;
    border-radius: 8px;
}

QPushButton#stopAllButton:hover {
    background: #F87171;
}

/* 🔑 重新认证按钮（警示琥珀橙 - 永远醒目） */
QPushButton#reauthButton {
    color: #FFFFFF;
    font-weight: 700;
    background: #F59E0B;
    border: 1px solid #D97706;
    min-height: 30px;
    padding: 0 14px;
    border-radius: 15px;
    font-size: 12px;
}

QPushButton#reauthButton:hover {
    background: #D97706;
    border-color: #B45309;
}

/* 账号卡片小按钮 */
QPushButton#miniRunButton, QPushButton#miniStopButton, QPushButton#miniConfigButton {
    min-height: 28px;
    padding: 0 10px;
    font-size: 12px;
    border-radius: 6px;
    font-weight: 600;
}

QPushButton#miniRunButton {
    color: #059669;
    background: #ECFDF5;
    border: 1px solid #A7F3D0;
}
QPushButton#miniRunButton:hover { background: #D1FAE5; border-color: #059669; }

QPushButton#miniStopButton {
    color: #DC2626;
    background: #FEF2F2;
    border: 1px solid #FECACA;
}
QPushButton#miniStopButton:hover { background: #FEE2E2; border-color: #DC2626; }

QPushButton#miniConfigButton {
    color: #475569;
    background: #F1F5F9;
    border: 1px solid #CBD5E1;
}
QPushButton#miniConfigButton:hover { background: #E2E8F0; color: #0F172A; }

/* ================= 输入框 ================= */
QLineEdit, QSpinBox {
    min-height: 34px;
    padding: 0 12px;
    color: #0F172A;
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
}

QLineEdit:focus, QSpinBox:focus {
    border: 2px solid #2563EB;
    background: #FFFFFF;
}

QComboBox {
    min-height: 34px;
    padding: 0 34px 0 12px;
    color: #0F172A;
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
}
QComboBox:hover { border-color: #94A3B8; background: #F8FAFC; }
QComboBox:focus { border: 2px solid #2563EB; padding-left: 11px; }
QComboBox::drop-down {
    width: 30px;
    border: none;
    border-left: 1px solid #E2E8F0;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}
QComboBox QAbstractItemView {
    color: #0F172A;
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    selection-background-color: #EFF6FF;
    selection-color: #1D4ED8;
    padding: 4px;
}

QScrollBar:vertical {
    width: 10px;
    margin: 4px 3px 4px 0;
    background: transparent;
}
QScrollBar::handle:vertical {
    min-height: 44px;
    background: #CBD5E1;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover { background: #94A3B8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}
QScrollBar:horizontal {
    height: 10px;
    margin: 0 4px 3px 4px;
    background: transparent;
}
QScrollBar::handle:horizontal {
    min-width: 44px;
    background: #CBD5E1;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover { background: #94A3B8; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    width: 0;
    background: transparent;
}

/* ================= 容器与卡片 ================= */
QFrame#overviewPanel, QFrame#actionPanel, QFrame#toolsPanel, QFrame#runtimePanel {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
}

QFrame#metricCard {
    background: #F8FAFC;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
}

QLabel#metricCaption { color: #64748B; font-size: 12px; font-weight: 500; }
QLabel#metricValue { color: #0F172A; font-size: 26px; font-weight: 800; }
QLabel#sectionTitle { color: #0F172A; font-size: 15px; font-weight: 700; }
QLabel#runtimeValue { color: #2563EB; font-size: 14px; font-weight: 700; }

/* ================= 账号列表双行卡片 ================= */
QFrame#accountCard {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
}

QFrame#accountCard:hover {
    border-color: #2563EB;
    background: #F8FAFC;
}

QFrame#accountCard_selected {
    background: #EFF6FF;
    border: 1px solid #60A5FA;
    border-radius: 10px;
}

QFrame#accountCard_selected:hover {
    background: #DBEAFE;
    border-color: #2563EB;
}

QLabel#accountName { color: #0F172A; font-size: 14px; font-weight: 700; }
QLabel#accountHandle { color: #64748B; font-size: 12px; }

QLabel#onlineDot { color: #10B981; font-size: 16px; }
QLabel#offlineDot { color: #94A3B8; font-size: 16px; }

QLabel#tagRunning {
    color: #059669;
    background: #ECFDF5;
    border: 1px solid #A7F3D0;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
}

QLabel#tagStopped {
    color: #64748B;
    background: #F1F5F9;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}

/* ================= 表格容器与 Tab ================= */
QTableWidget {
    background: transparent;
    border: none;
    outline: none;
}
QTableWidget::item { border: none; padding: 2px; }
QTableWidget::item:selected { background: transparent; }
QTableWidget QTableCornerButton::section { background: transparent; border: none; }

QTabWidget#detailsTabs::pane {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
    top: -1px;
}

QTabBar::tab {
    padding: 8px 18px;
    color: #64748B;
    background: #F1F5F9;
    border: 1px solid #E2E8F0;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 4px;
    font-weight: 600;
}

QTabBar::tab:selected {
    color: #2563EB;
    background: #FFFFFF;
}

/* ================= 终端日志框 ================= */
QPlainTextEdit {
    background: #F8FAFC;
    color: #1E293B;
    border: none;
    padding: 12px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    line-height: 1.5;
}

QSplitter::handle { background: #E2E8F0; width: 4px; }
QSplitter::handle:hover { background: #93C5FD; }
QStatusBar { background: #FFFFFF; color: #64748B; border-top: 1px solid #E2E8F0; }
QDialogButtonBox QPushButton { min-width: 92px; min-height: 34px; }
QDialog QLabel#subtitle {
    color: #64748B;
    background: #F8FAFC;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 8px 10px;
}
QFormLayout QLabel { color: #475569; font-weight: 600; }
"""
