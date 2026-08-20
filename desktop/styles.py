APP_STYLE = """
QMainWindow, QWidget {
    background: #f5f7fa;
    color: #17202a;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}
QFrame#header, QFrame#statusBar {
    background: #ffffff;
    border-bottom: 1px solid #dfe4ea;
}
QLabel#title {
    font-size: 20px;
    font-weight: 600;
}
QLabel#subtitle, QLabel#summary {
    color: #667085;
}
QPushButton {
    min-height: 32px;
    padding: 0 12px;
    border: 1px solid #cfd6df;
    border-radius: 5px;
    background: #ffffff;
}
QPushButton:hover { background: #f1f5f9; }
QPushButton:pressed { background: #e7edf4; }
QPushButton:disabled { color: #98a2b3; background: #f2f4f7; }
QPushButton#primaryButton {
    background: #1769aa;
    color: white;
    border-color: #1769aa;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f8fafc;
    border: 1px solid #dfe4ea;
    border-radius: 5px;
    gridline-color: #edf0f3;
    selection-background-color: #d8eafa;
    selection-color: #17202a;
}
QHeaderView::section {
    background: #eef2f6;
    color: #344054;
    padding: 8px;
    border: none;
    border-right: 1px solid #dfe4ea;
    border-bottom: 1px solid #dfe4ea;
    font-weight: 600;
}
QPlainTextEdit {
    background: #111827;
    color: #d1d5db;
    border: 1px solid #1f2937;
    border-radius: 5px;
    font-family: Consolas;
    font-size: 12px;
}
QStatusBar { background: #ffffff; }
"""
