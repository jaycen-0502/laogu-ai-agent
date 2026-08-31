"""High-trust Fluent light theme for the Laogu Windows control center."""

APP_STYLE = """
QMainWindow, QWidget#rootWidget {
    background: #F3F6FA;
    color: #172033;
    font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}
QWidget {
    color: #172033;
    font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}
QLabel { color: #344054; background: transparent; }
QDialog { background: #FFFFFF; color: #172033; }
QToolTip {
    color: #F8FAFC; background: #172033; border: 1px solid #344054;
    padding: 7px 9px; border-radius: 6px;
}

/* Header and product identity */
QFrame#header { background: #FFFFFF; border: none; border-bottom: 1px solid #E1E7F0; }
QLabel#headerBrandMark {
    min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px;
    border: none; qproperty-alignment: AlignCenter;
}
QLabel#title { color: #101828; font-size: 21px; font-weight: 700; }
QLabel#subtitle { color: #667085; font-size: 12px; font-weight: 400; }

/* Connection state */
QLabel#statusBadge {
    color: #475467; background: #F8FAFC; border: 1px solid #DDE4EE;
    border-radius: 9px; padding: 6px 10px; font-size: 12px; font-weight: 600;
}
QLabel#statusBadge[state="online"] { color: #067647; background: #ECFDF3; border-color: #ABEFC6; }
QLabel#statusBadge[state="offline"] { color: #667085; background: #F8FAFC; border-color: #DDE4EE; }
QLabel#statusBadge[state="warning"] { color: #B54708; background: #FFFAEB; border-color: #FEDF89; }
QLabel#statusBadge[state="neutral"] { color: #475467; background: #FFFFFF; border-color: #DDE4EE; }
QLabel#liveStatus, QLabel#liveStatusOnline, QLabel#liveStatusError {
    background: #F8FAFC; border: none; border-bottom: 1px solid #E1E7F0;
    padding: 7px 24px; font-size: 12px; font-weight: 600;
}
QLabel#liveStatus { color: #667085; }
QLabel#liveStatusOnline { color: #067647; background: #F0FDF4; }
QLabel#liveStatusError { color: #B42318; background: #FEF3F2; }

/* Buttons */
QPushButton {
    min-height: 36px; padding: 0 14px; color: #344054; background: #FFFFFF;
    border: 1px solid #C9D3E1; border-radius: 7px; font-weight: 600;
}
QPushButton:hover { color: #172033; background: #F8FAFC; border-color: #98A7BA; }
QPushButton:pressed { color: #172033; background: #EEF2F7; border-color: #7C8DA3; }
QPushButton:focus { border: 2px solid #84ADFF; padding-left: 13px; padding-right: 13px; }
QPushButton:disabled { color: #98A2B3; background: #F2F4F7; border-color: #E4E7EC; }
QPushButton#primaryButton, QPushButton#runAllButton, QPushButton#dialogPrimaryButton {
    color: #FFFFFF; background: #2457D6; border: 1px solid #2457D6; font-weight: 700;
}
QPushButton#primaryButton:hover, QPushButton#runAllButton:hover, QPushButton#dialogPrimaryButton:hover {
    color: #FFFFFF; background: #1D4ED8; border-color: #1D4ED8;
}
QPushButton#primaryButton:pressed, QPushButton#runAllButton:pressed, QPushButton#dialogPrimaryButton:pressed {
    background: #1E40AF; border-color: #1E40AF;
}
QPushButton#stopAllButton {
    color: #B42318; background: #FFF8F7; border: 1px solid #FDA29B; font-weight: 700;
}
QPushButton#stopAllButton:hover { color: #912018; background: #FEF3F2; border-color: #F97066; }
QPushButton#reauthButton {
    min-height: 32px; padding: 0 13px; color: #93370D; background: #FFFAEB;
    border: 1px solid #FEC84B; border-radius: 8px; font-size: 12px; font-weight: 700;
}
QPushButton#reauthButton:hover { color: #7A2E0E; background: #FEF0C7; border-color: #F79009; }
QPushButton#miniRunButton, QPushButton#miniStopButton, QPushButton#miniConfigButton {
    min-height: 28px; padding: 0 10px; border-radius: 6px; font-size: 12px; font-weight: 600;
}
QPushButton#miniRunButton { color: #067647; background: #ECFDF3; border-color: #ABEFC6; }
QPushButton#miniRunButton:hover { background: #D1FADF; border-color: #47CD89; }
QPushButton#miniStopButton { color: #B42318; background: #FEF3F2; border-color: #FECDCA; }
QPushButton#miniStopButton:hover { background: #FEE4E2; border-color: #FDA29B; }
QPushButton#miniConfigButton { color: #475467; background: #F8FAFC; border-color: #D0D5DD; }
QPushButton#miniConfigButton:hover { color: #2457D6; background: #EFF4FF; border-color: #B2CCFF; }

/* Inputs */
QLineEdit, QSpinBox, QDateTimeEdit, QTimeEdit, QComboBox {
    min-height: 36px; color: #172033; background: #FFFFFF;
    border: 1px solid #C9D3E1; border-radius: 7px;
}
QLineEdit, QSpinBox, QDateTimeEdit, QTimeEdit { padding: 0 11px; }
QComboBox { padding: 0 34px 0 11px; }
QLineEdit:hover, QSpinBox:hover, QDateTimeEdit:hover, QTimeEdit:hover, QComboBox:hover { border-color: #98A7BA; }
QLineEdit:focus, QSpinBox:focus, QDateTimeEdit:focus, QTimeEdit:focus, QComboBox:focus {
    background: #FFFFFF; border: 2px solid #528BFF;
}
QLineEdit:read-only { color: #667085; background: #F2F4F7; border-color: #E4E7EC; }
QLineEdit:disabled, QSpinBox:disabled, QDateTimeEdit:disabled, QTimeEdit:disabled, QComboBox:disabled {
    color: #98A2B3; background: #F2F4F7; border-color: #E4E7EC;
}
QComboBox::drop-down { width: 30px; border: none; border-left: 1px solid #E4E7EC; }
QComboBox QAbstractItemView {
    color: #172033; background: #FFFFFF; border: 1px solid #C9D3E1;
    selection-color: #173B8F; selection-background-color: #EAF0FF; padding: 4px; outline: none;
}

/* Scroll bars */
QScrollBar:vertical { width: 10px; margin: 4px 2px 4px 0; background: transparent; }
QScrollBar::handle:vertical { min-height: 44px; background: #C4CEDA; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #98A7BA; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0; background: transparent; }
QScrollBar:horizontal { height: 10px; margin: 0 4px 2px 4px; background: transparent; }
QScrollBar::handle:horizontal { min-width: 44px; background: #C4CEDA; border-radius: 5px; }
QScrollBar::handle:horizontal:hover { background: #98A7BA; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { width: 0; background: transparent; }

/* Panels and hierarchy */
QFrame#overviewPanel, QFrame#actionPanel, QFrame#toolsPanel, QFrame#runtimePanel {
    background: #FFFFFF; border: 1px solid #DDE4EE; border-radius: 9px;
}
QFrame#metricCard {
    background: #FAFBFD; border: 1px solid #E4E9F1;
    border-left: 3px solid #98A7BA; border-radius: 7px;
}
QFrame#metricCard[tone="total_tasks"] { border-left-color: #528BFF; }
QFrame#metricCard[tone="success_tasks"] { border-left-color: #17B26A; }
QFrame#metricCard[tone="failed_tasks"] { border-left-color: #F04438; }
QFrame#metricCard[tone="timeout_tasks"] { border-left-color: #F79009; }
QLabel#metricCaption { color: #667085; font-size: 11px; font-weight: 600; }
QLabel#metricValue { color: #101828; font-size: 27px; font-weight: 700; }
QLabel#sectionTitle { color: #101828; font-size: 15px; font-weight: 700; }
QLabel#sectionEyebrow { color: #475467; font-size: 11px; font-weight: 700; }
QLabel#sectionHint { color: #7C899B; font-size: 11px; }
QLabel#runtimeValue { color: #2457D6; font-size: 15px; font-weight: 700; }
QLabel#summary { color: #667085; font-size: 12px; font-weight: 600; }

/* Account cards */
QFrame#accountCard { background: #FFFFFF; border: 1px solid #DDE4EE; border-radius: 8px; }
QFrame#accountCard:hover { background: #FAFCFF; border-color: #9DB8F8; }
QFrame#accountCard_selected {
    background: #F4F7FF; border: 1px solid #84ADFF;
    border-left: 3px solid #2457D6; border-radius: 8px;
}
QFrame#accountCard_selected:hover { background: #EEF4FF; border-color: #528BFF; }
QLabel#accountName { color: #101828; font-size: 14px; font-weight: 700; }
QLabel#accountHandle { color: #667085; font-size: 12px; }
QLabel#accountStats { color: #667085; font-size: 11px; font-weight: 500; }
QLabel#onlineDot { color: #17B26A; font-size: 15px; }
QLabel#offlineDot { color: #98A2B3; font-size: 15px; }
QLabel#tagRunning, QLabel#tagStopped {
    border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 700;
}
QLabel#tagRunning { color: #067647; background: #ECFDF3; border: 1px solid #ABEFC6; }
QLabel#tagStopped { color: #667085; background: #F2F4F7; border: 1px solid #E4E7EC; }

/* Tables and tabs */
QTableWidget {
    color: #344054; background: transparent; border: none;
    gridline-color: #EAECF0; outline: none;
}
QTableWidget::item { border: none; padding: 3px 6px; }
QTableWidget::item:selected { color: #173B8F; background: #EAF0FF; }
QTableWidget::item:alternate { background: #F8FAFC; }
QTableWidget QTableCornerButton::section { background: #F8FAFC; border: none; }
QHeaderView::section {
    color: #475467; background: #F8FAFC; border: none;
    border-bottom: 1px solid #E4E7EC; padding: 8px 10px; font-size: 12px; font-weight: 700;
}
QTabWidget#detailsTabs::pane {
    background: #FFFFFF; border: 1px solid #DDE4EE; border-radius: 9px; top: -1px;
}
QTabBar::tab {
    min-height: 20px; padding: 9px 16px; margin-right: 4px;
    color: #667085; background: transparent; border: 1px solid transparent;
    border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px; font-weight: 600;
}
QTabBar::tab:hover { color: #344054; background: #F8FAFC; }
QTabBar::tab:selected {
    color: #173B8F; background: #FFFFFF; border-color: #DDE4EE;
    border-top: 2px solid #2457D6;
}

/* Professional log surface */
QPlainTextEdit {
    color: #344054; background: #F7F9FC; border: none; padding: 12px;
    font-family: "Cascadia Mono", "Cascadia Code", "Consolas", monospace; font-size: 12px;
}
QPlainTextEdit#mainLogOutput {
    color: #243B53; background: #F6F8FC;
    selection-color: #173B8F; selection-background-color: #DCE7FF;
    border: 1px solid #E1E7F0; border-radius: 8px; padding: 14px;
    font-family: "Cascadia Mono", "Cascadia Code", "Consolas", monospace; font-size: 12px;
}
QPlainTextEdit#mainLogOutput:focus { border: 1px solid #9DB8F8; }
QPlainTextEdit#mainLogOutput QScrollBar::handle:vertical { background: #B9C5D3; }
QPlainTextEdit#mainLogOutput QScrollBar::handle:vertical:hover { background: #8FA0B4; }
QSplitter::handle { background: transparent; width: 8px; }
QSplitter::handle:hover { background: #DCE7FF; }
QStatusBar {
    color: #667085; background: #FFFFFF; border-top: 1px solid #E1E7F0; font-size: 12px;
}

/* Dialogs */
QFrame#dialogHeader { background: #F8FAFC; border: none; border-bottom: 1px solid #E4E7EC; }
QFrame#dialogFormSurface { background: #FFFFFF; border: none; }
QLabel#dialogTitle { color: #101828; font-size: 18px; font-weight: 700; }
QLabel#dialogDescription { color: #667085; font-size: 12px; }
QLabel#dialogHint {
    color: #475467; background: #F8FAFC; border: 1px solid #E4E7EC;
    border-radius: 7px; padding: 9px 11px; font-size: 12px; font-weight: 400;
}
QFormLayout QLabel { color: #475467; font-weight: 600; }
QDialogButtonBox { padding-top: 4px; }
QDialogButtonBox QPushButton { min-width: 96px; min-height: 36px; }

/* Compact always-on-top log window */
QWidget#miniLogWindow {
    color: #172033; background: #F3F6FA;
    border: 1px solid #C9D3E1; border-radius: 10px;
}
QLabel#miniBrandMark {
    min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
    border: none; qproperty-alignment: AlignCenter;
}
QLabel#miniTitle { color: #101828; font-size: 15px; font-weight: 700; }
QLabel#miniSubtitle { color: #7C899B; font-size: 10px; font-weight: 600; }
QLabel#miniStatus, QLabel#miniStatusOnline {
    padding: 4px 8px; border-radius: 8px; font-size: 11px; font-weight: 700;
}
QLabel#miniStatus { color: #667085; background: #EEF2F6; border: 1px solid #DDE4EE; }
QLabel#miniStatusOnline { color: #067647; background: #ECFDF3; border: 1px solid #ABEFC6; }
QPushButton#miniWindowActionButton, QPushButton#miniWindowCloseButton {
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
    padding: 0; color: #667085; background: #FFFFFF;
    border: 1px solid #DDE4EE; border-radius: 7px;
}
QPushButton#miniWindowActionButton:hover { color: #2457D6; background: #EFF4FF; border-color: #B2CCFF; }
QPushButton#miniWindowCloseButton:hover { color: #B42318; background: #FEF3F2; border-color: #FECDCA; }
QLabel#miniMetrics {
    color: #344054; background: #FFFFFF; border: 1px solid #DDE4EE;
    border-radius: 8px; padding: 8px 10px; font-size: 12px; font-weight: 700;
}
QLabel#miniSectionTitle { color: #344054; font-size: 12px; font-weight: 700; }
QLabel#miniAutoFollow { color: #7C899B; font-size: 10px; font-weight: 600; }
QPlainTextEdit#miniLogOutput {
    color: #344054; background: #FFFFFF;
    selection-color: #173B8F; selection-background-color: #DCE7FF;
    border: 1px solid #DDE4EE; border-radius: 8px; padding: 10px; font-size: 11px;
}
QPushButton#miniPrimaryButton, QPushButton#miniSecondaryButton, QPushButton#miniDangerButton {
    min-height: 34px; padding: 0 12px; border-radius: 7px; font-size: 12px; font-weight: 700;
}
QPushButton#miniPrimaryButton { color: #FFFFFF; background: #2457D6; border-color: #2457D6; }
QPushButton#miniPrimaryButton:hover { color: #FFFFFF; background: #1D4ED8; border-color: #1D4ED8; }
QPushButton#miniSecondaryButton { color: #344054; background: #FFFFFF; border-color: #C9D3E1; }
QPushButton#miniSecondaryButton:hover { color: #2457D6; background: #EFF4FF; border-color: #B2CCFF; }
QPushButton#miniSecondaryButton:checked { color: #173B8F; background: #EAF0FF; border-color: #84ADFF; }
QPushButton#miniDangerButton { color: #B42318; background: #FFFFFF; border-color: #FDA29B; }
QPushButton#miniDangerButton:hover { color: #912018; background: #FEF3F2; border-color: #F97066; }
"""
