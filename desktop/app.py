from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from .main_window import MainWindow
from .startup import import_existing_credentials, standalone_agent_processes, stop_processes
from .styles import APP_STYLE


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Laogu 账号资产控制中心")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)

    standalone_agents = standalone_agent_processes()
    if standalone_agents:
        process_ids = "、".join(str(process.pid) for process in standalone_agents)
        answer = QMessageBox.question(
            None,
            "检测到独立 Agent",
            "检测到命令行 Agent 正在运行（PID："
            f"{process_ids}）。\n\n"
            "为避免重复连接，是否关闭它们并继续启动桌面控制台？\n"
            "此操作不会关闭 Laogu Browser。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return 0
        failures = stop_processes(standalone_agents)
        if failures:
            QMessageBox.critical(None, "无法停止独立 Agent", "\n".join(failures))
            return 1

    credential_import = import_existing_credentials()
    if credential_import.error:
        QMessageBox.critical(None, "凭据导入失败", credential_import.error)
        return 1
    if credential_import.imported:
        QMessageBox.information(
            None,
            "凭据已导入",
            "已安全导入原 Agent 的 DPAPI 加密凭据。\n"
            "程序未解密、显示或保存明文 Token。",
        )

    window = MainWindow()
    window.show()
    return app.exec()
