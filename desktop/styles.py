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
QPushButton#stopAllButton, QPushButton#deleteSelectedButton {
    color: #B42318; background: #FFF8F7; border: 1px solid #FDA29B; font-weight: 700;
}
QPushButton#stopAllButton:hover, QPushButton#deleteSelectedButton:hover { color: #912018; background: #FEF3F2; border-color: #F97066; }
QPushButton#reauthButton {
    min-height: 32px; padding: 0 13px; color: #93370D; background: #FFFAEB;
    border: 1px solid #FEC84B; border-radius: 8px; font-size: 12px; font-weight: 700;
}
QPushButton#reauthButton:hover { color: #7A2E0E; background: #FEF0C7; border-color: #F79009; }
QPushButton#miniRunButton, QPushButton#miniStopButton, QPushButton#miniConfigButton, QPushButton#miniDeleteButton {
    min-height: 28px; padding: 0 10px; border-radius: 6px; font-size: 12px; font-weight: 600;
}
QPushButton#miniRunButton { color: #067647; background: #ECFDF3; border-color: #ABEFC6; }
QPushButton#miniRunButton:hover { background: #D1FADF; border-color: #47CD89; }
QPushButton#miniStopButton { color: #B42318; background: #FEF3F2; border-color: #FECDCA; }
QPushButton#miniStopButton:hover { background: #FEE4E2; border-color: #FDA29B; }
QPushButton#miniConfigButton { color: #475467; background: #F8FAFC; border-color: #D0D5DD; }
QPushButton#miniConfigButton:hover { color: #2457D6; background: #EFF4FF; border-color: #B2CCFF; }
QPushButton#miniDeleteButton { color: #B42318; background: #FFF5F5; border-color: #FEDFDE; }
QPushButton#miniDeleteButton:hover { color: #912018; background: #FEE4E2; border-color: #FDA29B; }
QFrame#keywordChip {
    min-height: 20px;
    max-height: 22px;
}
QToolButton#chipCloseBtn, QPushButton#chipCloseBtn {
    min-height: 12px;
    max-height: 12px;
    min-width: 12px;
    max-width: 12px;
    padding: 0;
    margin: 0;
    border: none;
    border-radius: 6px;
    background: transparent;
}
QToolButton#chipCloseBtn:hover, QPushButton#chipCloseBtn:hover {
    background: #EF4444;
    border: none;
}

/* Inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QTimeEdit, QComboBox {
    min-height: 36px; color: #172033; background: #FFFFFF;
    border: 1px solid #C9D3E1; border-radius: 7px;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QTimeEdit { padding: 0 11px; }
QComboBox { padding: 0 34px 0 11px; }
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateTimeEdit:hover, QTimeEdit:hover, QComboBox:hover { border-color: #98A7BA; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus, QTimeEdit:focus, QComboBox:focus {
    background: #FFFFFF; border: 2px solid #528BFF;
}
QLineEdit:read-only { color: #667085; background: #F2F4F7; border-color: #E4E7EC; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateTimeEdit:disabled, QTimeEdit:disabled, QComboBox:disabled {
    color: #98A2B3; background: #F2F4F7; border-color: #E4E7EC;
}

/* Remove up/down stepper buttons from SpinBoxes */
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0px; height: 0px; border: none; background: transparent;
}
QSpinBox::up-arrow, QSpinBox::down-arrow,
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {
    image: none; width: 0px; height: 0px;
}
QComboBox::drop-down { width: 30px; border: none; border-left: 1px solid #E4E7EC; }
QComboBox QAbstractItemView {
    color: #172033; background: #FFFFFF; border: 1px solid #C9D3E1;
    border-radius: 8px; padding: 4px; outline: none;
}
QComboBox QAbstractItemView::item {
    min-height: 28px; padding: 4px 8px; border-radius: 5px;
}
QComboBox QAbstractItemView::item:hover {
    background-color: #F0F4FE; color: #1D4ED8;
}
QComboBox QAbstractItemView::item:selected {
    background-color: #EAF0FF; color: #173B8F; font-weight: 600;
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
QLabel#scheduleDot { color: #0284C7; font-size: 15px; }
QLabel#tagRunning, QLabel#tagStopped, QLabel#tagScheduled, QLabel#tagAutoRunning {
    border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 700;
}
QLabel#tagRunning { color: #067647; background: #ECFDF3; border: 1px solid #ABEFC6; }
QLabel#tagAutoRunning { color: #15803D; background: #DCFCE7; border: 1px solid #86EFAC; }
QLabel#tagScheduled { color: #0369A1; background: #F0F9FF; border: 1px solid #BAE6FD; }
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
QTabWidget#taskConfigTabs::pane { background: #FFFFFF; border: none; }
QTabWidget#taskConfigTabs QTabBar { background: #F8FAFC; border-bottom: 1px solid #E4E7EC; }
QTabWidget#taskConfigTabs QTabBar::tab {
    color: #667085; background: transparent; border: none; border-bottom: 2px solid transparent;
    min-width: 92px; padding: 11px 14px; font-weight: 600;
}
QTabWidget#taskConfigTabs QTabBar::tab:hover { color: #344054; background: #F2F5FA; }
QTabWidget#taskConfigTabs QTabBar::tab:selected { color: #2457D6; background: #FFFFFF; border-bottom-color: #2457D6; }
QScrollArea#taskConfigScroll { background: #FFFFFF; border: none; }
QFrame#dialogFooter { background: #FFFFFF; border: none; border-top: 1px solid #E4E7EC; }
QFrame#advancedInteractionLimits { background: #FFFFFF; border: 1px solid #E4E7EC; border-radius: 8px; }
QToolButton#advancedInteractionToggle {
    color: #344054; background: #F8FAFC; border: none; border-radius: 7px;
    min-height: 34px; padding: 0 12px; text-align: left; font-weight: 700;
}
QToolButton#advancedInteractionToggle:hover { color: #1D4ED8; background: #EEF4FF; }
QToolButton#advancedInteractionToggle:focus { border: 2px solid #84ADFF; }
QLabel#dialogHint {
    color: #475467; background: #F8FAFC; border: 1px solid #E4E7EC;
    border-radius: 7px; padding: 9px 11px; font-size: 12px; font-weight: 400;
}
QFormLayout QLabel { color: #475467; font-weight: 600; }
QLabel#compactFieldLabel { color: #667085; font-weight: 600; min-width: 28px; }
QDialogButtonBox { padding-top: 4px; }
QDialogButtonBox QPushButton { min-width: 96px; min-height: 36px; }

/* High-tech 20-account matrix floating window HUD - Option A Clean Pearl White */
QWidget#miniLogWindow {
    background: transparent;
    border: none;
}
QWidget#miniCapsuleBar {
    background: #FFFFFF;
    border: 1px solid #94A3B8;
    border-radius: 18px;
}
QWidget#miniFullPanel {
    color: #0F172A;
    background: #F8FAFC;
    border: 1px solid #CBD5E1;
    border-radius: 14px;
}
QLabel#miniBrandMark {
    min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
    border: none; qproperty-alignment: AlignCenter;
}
QLabel#miniTitle { color: #0F172A; font-size: 13px; font-weight: 700; }
QLabel#miniSubtitle { color: #64748B; font-size: 10px; font-weight: 500; }
QLabel#miniStatus, QLabel#miniStatusOnline, QLabel#miniStatusError {
    padding: 2px 7px; border-radius: 6px; font-size: 11px; font-weight: 700;
}
QLabel#miniStatus { color: #475569; background: #F1F5F9; border: 1px solid #CBD5E1; }
QLabel#miniStatusOnline { color: #047857; background: #ECFDF5; border: 1px solid #A7F3D0; }
QLabel#miniStatusError { color: #B91C1C; background: #FEF2F2; border: 1px solid #FECDD3; }

QPushButton#miniWindowActionButton, QPushButton#miniWindowCloseButton {
    min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
    padding: 0; color: #475569; background: #FFFFFF;
    border: 1px solid #CBD5E1; border-radius: 6px; font-size: 12px;
}
QPushButton#miniWindowActionButton:hover { color: #0284C7; background: #F0F9FF; border-color: #38BDF8; }
QPushButton#miniWindowActionButton:checked { color: #1D4ED8; background: #EFF6FF; border-color: #3B82F6; }
QPushButton#miniWindowCloseButton:hover { color: #DC2626; background: #FEF2F2; border-color: #F87171; }

QPushButton#miniTabButton {
    padding: 3px 10px; color: #475569; background: #FFFFFF;
    border: 1px solid #CBD5E1; border-radius: 6px; font-size: 11px; font-weight: 600;
}
QPushButton#miniTabButton:hover { color: #0F172A; background: #F1F5F9; border-color: #94A3B8; }
QPushButton#miniTabButton:checked { color: #FFFFFF; background: #2563EB; border-color: #1D4ED8; }

QLabel#miniMetrics {
    color: #334155; background: #FFFFFF; border: 1px solid #E2E8F0;
    border-radius: 6px; padding: 5px 8px; font-size: 11px; font-weight: 600;
}
QScrollArea#miniScrollArea {
    background: transparent; border: none;
}
QWidget#miniMatrixContainer {
    background: transparent;
}

/* Modern Sleek Floating Window Scrollbar */
QScrollArea#miniScrollArea QScrollBar:vertical,
QPlainTextEdit#miniLogOutput QScrollBar:vertical,
QPlainTextEdit#miniDiagTerminal QScrollBar:vertical {
    width: 6px;
    margin: 2px 0px 2px 0px;
    background: transparent;
    border: none;
}
QScrollArea#miniScrollArea QScrollBar::track:vertical,
QPlainTextEdit#miniLogOutput QScrollBar::track:vertical,
QPlainTextEdit#miniDiagTerminal QScrollBar::track:vertical {
    background: transparent;
    border: none;
}
QScrollArea#miniScrollArea QScrollBar::handle:vertical,
QPlainTextEdit#miniLogOutput QScrollBar::handle:vertical,
QPlainTextEdit#miniDiagTerminal QScrollBar::handle:vertical {
    min-height: 36px;
    background: #CBD5E1;
    border-radius: 3px;
}
QScrollArea#miniScrollArea QScrollBar::handle:vertical:hover,
QPlainTextEdit#miniLogOutput QScrollBar::handle:vertical:hover,
QPlainTextEdit#miniDiagTerminal QScrollBar::handle:vertical:hover {
    background: #94A3B8;
}
QScrollArea#miniScrollArea QScrollBar::add-line:vertical,
QScrollArea#miniScrollArea QScrollBar::sub-line:vertical,
QScrollArea#miniScrollArea QScrollBar::add-page:vertical,
QScrollArea#miniScrollArea QScrollBar::sub-page:vertical,
QPlainTextEdit#miniLogOutput QScrollBar::add-line:vertical,
QPlainTextEdit#miniLogOutput QScrollBar::sub-line:vertical,
QPlainTextEdit#miniLogOutput QScrollBar::add-page:vertical,
QPlainTextEdit#miniLogOutput QScrollBar::sub-page:vertical,
QPlainTextEdit#miniDiagTerminal QScrollBar::add-line:vertical,
QPlainTextEdit#miniDiagTerminal QScrollBar::sub-line:vertical,
QPlainTextEdit#miniDiagTerminal QScrollBar::add-page:vertical,
QPlainTextEdit#miniDiagTerminal QScrollBar::sub-page:vertical {
    height: 0px;
    background: transparent;
    border: none;
}
QScrollArea#miniScrollArea QScrollBar:horizontal,
QPlainTextEdit#miniLogOutput QScrollBar:horizontal,
QPlainTextEdit#miniDiagTerminal QScrollBar:horizontal {
    height: 0px;
    background: transparent;
}

/* Card Styles */
#miniAccountCard, QWidget#miniAccountCard, QFrame#miniAccountCard {
    background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 10px;
}
#miniAccountCard:hover, QWidget#miniAccountCard:hover, QFrame#miniAccountCard:hover {
    background: #F8FAFC; border-color: #38BDF8;
}
#miniAccountCard_error, QWidget#miniAccountCard_error, QFrame#miniAccountCard_error {
    background: #FFF1F2; border: 1px solid #FDA4AF; border-radius: 10px;
}
QPushButton#miniInspectButton {
    min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
    padding: 0; color: #64748B; background: #F8FAFC;
    border: 1px solid #CBD5E1; border-radius: 6px; font-size: 11px;
}
QPushButton#miniInspectButton:hover {
    color: #0284C7; background: #E0F2FE; border-color: #38BDF8;
}

/* Alert Banner */
QWidget#miniAlertCard {
    background: #FFF5F5; border: 1px solid #FECDD3; border-radius: 8px;
}
QWidget#miniAlertCard QPushButton {
    min-height: 20px; max-height: 22px;
}

/* Log & Terminal Output */
QPlainTextEdit#miniLogOutput, QPlainTextEdit#miniDiagTerminal {
    color: #0F172A; background: #FFFFFF;
    selection-color: #FFFFFF; selection-background-color: #2563EB;
    border: 1px solid #CBD5E1; border-radius: 6px; padding: 6px;
    font-family: Consolas, "Courier New", monospace; font-size: 11px;
}

/* Action Buttons */
QPushButton#miniPrimaryButton, QPushButton#miniSecondaryButton, QPushButton#miniDangerButton {
    min-height: 26px; padding: 0 9px; border-radius: 6px; font-size: 11px; font-weight: 600;
}
QPushButton#miniPrimaryButton { color: #FFFFFF; background: #2563EB; border: 1px solid #1D4ED8; }
QPushButton#miniPrimaryButton:hover { background: #1D4ED8; border-color: #1E40AF; }
QPushButton#miniSecondaryButton { color: #334155; background: #FFFFFF; border: 1px solid #CBD5E1; }
QPushButton#miniSecondaryButton:hover { color: #0F172A; background: #F8FAFC; border-color: #94A3B8; }
QPushButton#miniSecondaryButton:checked { color: #1D4ED8; background: #EFF6FF; border-color: #3B82F6; }
QPushButton#miniDangerButton { color: #B91C1C; background: #FEF2F2; border: 1px solid #FECDD3; }
QPushButton#miniDangerButton:hover { color: #FFFFFF; background: #DC2626; border-color: #B91C1C; }

/* In-Window Modal Overlay (Scheme B) */
QFrame#miniModalOverlay {
    background: rgba(15, 23, 42, 0.45);
    border-radius: 12px;
}
QFrame#miniModalCard {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
}
QFrame#miniModalCard QLabel {
    background: transparent;
    border: none;
}
QPushButton#miniModalCancel {
    background: #F1F5F9; color: #475569;
    border: 1px solid #CBD5E1; border-radius: 6px;
    font-size: 11px; font-weight: 500; min-height: 28px; max-height: 28px; padding: 0 16px;
}
QPushButton#miniModalCancel:hover {
    background: #E2E8F0; color: #0F172A; border-color: #94A3B8;
}
QPushButton#miniModalConfirm {
    background: #DC2626; color: #FFFFFF;
    border: none; border-radius: 6px;
    font-size: 11px; font-weight: bold; min-height: 28px; max-height: 28px; padding: 0 16px;
}
QPushButton#miniModalConfirm:hover {
    background: #B91C1C;
}

/* Edge Auto-Hide Dock Pill & Toggle Button (Proposal 1) */
QFrame#miniDockPill {
    background: #0F172A;
    border: 1.5px solid #22C55E;
    border-top-left-radius: 10px;
    border-bottom-left-radius: 10px;
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
}
QFrame#miniDockPill[edge="top"] {
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
}
QFrame#miniDockPill[alert="true"] {
    background: #DC2626;
    border: 1.5px solid #FCA5A5;
}
QPushButton#miniAutoHideBtn {
    background: #EFF6FF;
    color: #1D4ED8;
    border: 1px solid #BFDBFE;
    border-radius: 6px;
    font-size: 10px;
    font-weight: 600;
    min-height: 24px;
    max-height: 24px;
    padding: 0 8px;
}
QPushButton#miniAutoHideBtn:hover {
    background: #DBEAFE;
}
"""


