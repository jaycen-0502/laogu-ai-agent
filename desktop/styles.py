APP_STYLE = """
QMainWindow, QWidget {
    background: #17171d;
    color: #e7e8ee;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}
QLabel { background: transparent; }
QFrame#header {
    background: #22232a;
    border-bottom: 1px solid #31333d;
}
QLabel#title { font-size: 24px; font-weight: 700; color: #f8f9fc; }
QLabel#subtitle, QLabel#summary { color: #9195a5; }
QLabel#liveStatus, QLabel#liveStatusOnline {
    background: #1d2025;
    border-bottom: 1px solid #2d3038;
    color: #9298a8;
    font-weight: 600;
}
QLabel#liveStatusOnline { color: #2bd86c; }
QPushButton {
    min-height: 34px;
    padding: 0 10px;
    color: #d8dbe4;
    border: 1px solid #3a3d48;
    border-radius: 6px;
    background: #292a32;
}
QPushButton:hover { background: #343640; border-color: #4c5060; }
QPushButton:pressed { background: #202127; }
QPushButton:disabled { color: #666a77; background: #24252b; border-color: #31323a; }
QPushButton#primaryButton, QPushButton#runAllButton {
    font-weight: 700;
    color: #ffffff;
    background: #1599e8;
    border-color: #1599e8;
}
QPushButton#primaryButton:hover, QPushButton#runAllButton:hover { background: #36acee; }
QPushButton#stopAllButton {
    color: #ffd8dd;
    font-weight: 700;
    background: #512d34;
    border-color: #6b3943;
}
QPushButton#stopAllButton:hover { background: #663640; }
QPushButton#miniRunButton, QPushButton#miniStopButton {
    min-height: 25px; min-width: 42px; padding: 0 7px; border-radius: 5px;
}
QPushButton#miniRunButton { color: #d3f7e0; background: #1f5940; border-color: #2f7656; }
QPushButton#miniStopButton { color: #ffd9dd; background: #523139; border-color: #70404a; }
QLineEdit {
    min-height: 29px; padding: 0 8px; color: #e8eaf0;
    background: #22232a; border: 1px solid #393c47; border-radius: 6px;
}
QLineEdit:focus { border-color: #1599e8; }
QTableWidget#accountTable {
    background: #1b1c22; border: 1px solid #30323b; border-radius: 7px;
    alternate-background-color: #1b1c22; selection-background-color: #242b35;
}
QTableWidget#accountTable::item { padding: 0; border: none; }
QWidget#accountCard { background: #1d1e25; border-bottom: 1px solid #2a2c35; }
QWidget#accountCard:hover { background: #25272f; }
QLabel#onlineDot { color: #22d46a; font-size: 15px; }
QLabel#offlineDot { color: #818695; font-size: 15px; }
QLabel#accountName { color: #eef0f5; font-size: 14px; font-weight: 700; }
QLabel#accountHandle { color: #9297a7; font-size: 12px; }
QLabel#accountState { color: #33b7f3; font-weight: 700; }
QTableWidget {
    background: #1b1c22; color: #dfe2ea; border: 1px solid #30323b;
    alternate-background-color: #202128; gridline-color: #30323b;
    selection-background-color: #29333e; selection-color: #ffffff;
}
QHeaderView::section {
    background: #25262e; color: #999eae; padding: 7px; border: none;
    border-right: 1px solid #333540; border-bottom: 1px solid #333540; font-weight: 700;
}
QPlainTextEdit {
    background: #121319; color: #aeb5c2; border: 1px solid #30323b;
    border-radius: 7px; padding: 6px; font-family: Consolas; font-size: 11px;
}
QScrollBar:vertical { background: #1a1b21; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #454956; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QStatusBar { background: #202128; color: #9297a7; border-top: 1px solid #30323b; }
QFrame#overviewPanel, QFrame#actionPanel, QFrame#toolsPanel, QFrame#runtimePanel {
    background: #1d1f26;
    border: 1px solid #30333d;
    border-radius: 8px;
}
QFrame#metricCard {
    background: #242630;
    border: 1px solid #343744;
    border-radius: 7px;
}
QLabel#metricCaption { color: #9ca2b2; font-size: 12px; }
QLabel#metricValue { color: #f3f5f8; font-size: 28px; font-weight: 800; }
QLabel#sectionTitle { color: #f1f3f7; font-size: 16px; font-weight: 700; }
QLabel#runtimeValue { color: #f4f6fa; font-size: 15px; font-weight: 700; }
QFrame#actionPanel QPushButton { min-height: 42px; font-size: 14px; }
QFrame#toolsPanel QPushButton { min-height: 38px; }
QFrame#toolsPanel QLineEdit { min-height: 31px; }
QTabWidget#detailsTabs::pane {
    background: #1b1c22;
    border: 1px solid #30323b;
    border-radius: 7px;
    top: -1px;
}
QTabBar::tab {
    min-width: 88px;
    padding: 7px 12px;
    color: #969baa;
    background: #202128;
    border: 1px solid #30323b;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected { color: #ffffff; background: #292b34; }
QTabBar::tab:hover { color: #dfe2e9; background: #252730; }
QSplitter::handle { background: #2a2c34; width: 2px; }
"""
