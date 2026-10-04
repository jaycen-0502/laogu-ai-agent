"""Desktop OTA update workers and the update notice dialog.

The network work lives in ``QThread`` instances so checking for, and
downloading, a release never blocks the desktop event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
)


@dataclass(frozen=True)
class ReleaseInfo:
    """Public metadata returned by the update check endpoint."""

    has_update: bool
    latest_version: str
    is_mandatory: bool
    release_notes: str
    download_url: str
    sha256: str
    file_size: int

    @classmethod
    def from_mapping(cls, payload: Any) -> "ReleaseInfo":
        """Create a release value while tolerating extra API fields."""
        data = payload if isinstance(payload, dict) else {}
        try:
            file_size = max(0, int(data.get("file_size", 0) or 0))
        except (TypeError, ValueError):
            file_size = 0
        return cls(
            has_update=bool(data.get("has_update", False)),
            latest_version=str(data.get("latest_version") or ""),
            is_mandatory=bool(data.get("is_mandatory", False)),
            release_notes=str(data.get("release_notes") or ""),
            download_url=str(data.get("download_url") or ""),
            sha256=str(data.get("sha256") or "").strip().lower(),
            file_size=file_size,
        )


def _display_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(size)} B"


class UpdateCheckWorker(QThread):
    """Fetch release metadata without blocking the Qt GUI thread."""

    check_finished = Signal(object)
    check_failed = Signal(str)

    def __init__(
        self,
        server_url: str,
        current_version: str,
        channel: str = "stable",
        parent=None,
    ) -> None:
        if parent is None and channel is not None and not isinstance(channel, str):
            parent, channel = channel, "stable"
        super().__init__(parent)
        self.server_url = str(server_url or "").strip().rstrip("/")
        self.current_version = str(current_version or "").strip()
        self.channel = str(channel or "stable").strip() or "stable"

    def run(self) -> None:
        if not self.server_url:
            self.check_failed.emit("更新服务器地址未配置")
            return
        query = urlencode({"current_version": self.current_version, "channel": self.channel})
        endpoint = f"{self.server_url}/api/v1/app/check-update?{query}"
        request = Request(endpoint, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=6) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status < 200 or status >= 300:
                    raise RuntimeError(f"更新服务器返回 HTTP {status}")
                import json

                payload = json.loads(response.read().decode("utf-8"))
            self.check_finished.emit(ReleaseInfo.from_mapping(payload))
        except HTTPError as exc:
            self.check_failed.emit(f"更新检查失败（HTTP {exc.code}）")
        except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            message = str(exc).strip() or exc.__class__.__name__
            self.check_failed.emit(f"更新检查失败：{message}")
        except Exception as exc:  # pragma: no cover - defensive boundary for GUI code
            self.check_failed.emit(f"更新检查失败：{exc}")


class DownloadWorker(QThread):
    """Stream, hash, and atomically install an OTA package."""

    progress_changed = Signal(int, int)
    download_error = Signal(str)
    download_success = Signal(object)

    BUFFER_SIZE = 128 * 1024

    def __init__(
        self,
        download_url: str,
        expected_sha256: str,
        target_dir: str | os.PathLike[str] | None = None,
        total_bytes: int = 0,
        server_url: str = "",
        parent=None,
    ) -> None:
        if parent is None and server_url is not None and not isinstance(server_url, str):
            parent, server_url = server_url, ""
        if parent is None and total_bytes is not None and not isinstance(total_bytes, (int, float, str)):
            parent, total_bytes = total_bytes, 0
        super().__init__(parent)
        self.download_url = str(download_url or "").strip()
        self.expected_sha256 = str(expected_sha256 or "").strip().lower()
        self.target_dir = Path(target_dir).expanduser() if target_dir else Path.cwd()
        self.total_bytes = max(0, int(total_bytes or 0))
        self.server_url = str(server_url or "").strip().rstrip("/")

    def _resolved_url(self) -> str:
        if self.download_url.startswith(("http://", "https://")):
            return self.download_url
        base = f"{self.server_url}/" if self.server_url else ""
        return urljoin(base, self.download_url)

    def run(self) -> None:
        temporary: Path | None = None
        try:
            url = self._resolved_url()
            if not url:
                raise RuntimeError("升级包下载地址为空")
            self.target_dir.mkdir(parents=True, exist_ok=True)
            final_path = self.target_dir / "latest_update.zip"
            temporary = self.target_dir / "latest_update.zip.part"
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

            request = Request(url, headers={"Accept": "application/zip, application/octet-stream"}, method="GET")
            digest = hashlib.sha256()
            downloaded = 0
            with urlopen(request, timeout=30) as response, temporary.open("wb") as output:
                header_size = response.headers.get("Content-Length")
                try:
                    total = max(0, int(header_size or self.total_bytes or 0))
                except (TypeError, ValueError):
                    total = self.total_bytes
                self.progress_changed.emit(0, total)
                while True:
                    if self.isInterruptionRequested():
                        raise RuntimeError("升级包下载已取消")
                    chunk = response.read(self.BUFFER_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    self.progress_changed.emit(downloaded, total)

            actual_sha256 = digest.hexdigest().lower()
            if not self.expected_sha256:
                raise RuntimeError("服务器未提供升级包 SHA256")
            if actual_sha256 != self.expected_sha256:
                raise RuntimeError(
                    f"升级包校验失败：期望 {self.expected_sha256}，实际 {actual_sha256}"
                )
            os.replace(temporary, final_path)
            self.download_success.emit(final_path)
        except HTTPError as exc:
            self._fail(f"升级包下载失败（HTTP {exc.code}）", temporary)
        except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            self._fail(str(exc).strip() or exc.__class__.__name__, temporary)
        except Exception as exc:  # pragma: no cover - defensive boundary for GUI code
            self._fail(str(exc).strip() or exc.__class__.__name__, temporary)

    def _fail(self, message: str, temporary: Path | None) -> None:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        self.download_error.emit(message)


class UpdateNoticeDialog(QDialog):
    """Compact, keyboard-friendly release notice and download flow."""

    ignored_versions: set[str] = set()

    def __init__(
        self,
        release: ReleaseInfo,
        server_url: str = "",
        target_dir: str | os.PathLike[str] | None = None,
        parent=None,
    ) -> None:
        # Preserve the conventional ``QDialog(release, parent)`` calling form.
        if parent is None and server_url is not None and not isinstance(server_url, (str, bytes, os.PathLike)):
            parent, server_url = server_url, ""
        super().__init__(parent)
        self.release = release
        self.server_url = str(server_url or "").strip().rstrip("/")
        self.target_dir = Path(target_dir) if target_dir else self._default_target_dir()
        self.download_worker: DownloadWorker | None = None
        self.download_path: Path | None = None
        self.setWindowTitle("发现新版本")
        self.setFixedSize(520, 420)
        self.setModal(True)

        self.version_label = QLabel(f"新版本  {release.latest_version}")
        self.version_label.setObjectName("updateVersionLabel")
        self.version_label.setStyleSheet("font-size: 22px; font-weight: 700; color: #172033;")

        self.size_label = QLabel(f"升级包大小：{_display_size(release.file_size)}")
        self.size_label.setStyleSheet("color: #667085;")
        self.notes_browser = QTextBrowser()
        self.notes_browser.setOpenExternalLinks(True)
        self.notes_browser.setPlaceholderText("暂无更新日志")
        self.notes_browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        try:
            self.notes_browser.setMarkdown(release.release_notes or "暂无更新日志")
        except AttributeError:  # pragma: no cover - compatibility with older Qt builds
            self.notes_browser.setPlainText(release.release_notes or "暂无更新日志")

        self.progress_label = QLabel("准备下载升级包…")
        self.progress_label.setVisible(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)

        self.upgrade_button = QPushButton("立即升级")
        self.upgrade_button.setDefault(True)
        self.upgrade_button.setMinimumHeight(36)
        self.later_button = QPushButton("稍后再说")
        self.later_button.setMinimumHeight(36)
        self.ignore_button = QPushButton("忽略此版本")
        self.ignore_button.setMinimumHeight(36)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(self.version_label)
        layout.addWidget(self.size_label)
        layout.addWidget(self.notes_browser, 1)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.ignore_button)
        buttons.addStretch(1)
        buttons.addWidget(self.later_button)
        buttons.addWidget(self.upgrade_button)
        layout.addLayout(buttons)

        self.upgrade_button.clicked.connect(self._start_download)
        self.later_button.clicked.connect(self.reject)
        self.ignore_button.clicked.connect(self._ignore_release)

        if release.is_mandatory:
            self.later_button.setEnabled(False)
            self.ignore_button.setEnabled(False)
            self.setWindowTitle("必须更新")

    @staticmethod
    def _default_target_dir() -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent

    def _ignore_release(self) -> None:
        self.ignored_versions.add(self.release.latest_version)
        self.done(QDialog.DialogCode.Rejected)

    def _start_download(self) -> None:
        if self.download_worker is not None and self.download_worker.isRunning():
            return
        self.upgrade_button.setEnabled(False)
        self.later_button.setEnabled(False)
        self.ignore_button.setEnabled(False)
        self.progress_label.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_label.setText("正在下载升级包…")
        self.download_worker = DownloadWorker(
            self.release.download_url,
            self.release.sha256,
            self.target_dir,
            total_bytes=self.release.file_size,
            server_url=self.server_url,
            parent=self,
        )
        self.download_worker.progress_changed.connect(self._download_progress)
        self.download_worker.download_error.connect(self._download_failed)
        self.download_worker.download_success.connect(self._download_succeeded)
        self.download_worker.finished.connect(self._download_thread_finished)
        self.download_worker.start()

    def _download_progress(self, current: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(min(100, int(current * 100 / total)))
            self.progress_label.setText(f"正在下载：{_display_size(current)} / {_display_size(total)}")
        else:
            self.progress_bar.setRange(0, 0)
            self.progress_label.setText(f"正在下载：{_display_size(current)}")

    def _download_failed(self, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText("下载失败")
        self.upgrade_button.setEnabled(True)
        self.later_button.setEnabled(not self.release.is_mandatory)
        self.ignore_button.setEnabled(not self.release.is_mandatory)
        QMessageBox.warning(self, "升级失败", message)

    def _download_succeeded(self, path: object) -> None:
        self.download_path = Path(path)
        self.progress_bar.setValue(100)
        self.progress_label.setText("下载完成，等待重启")
        answer = QMessageBox.question(
            self,
            "下载完成",
            "升级包已下载并通过校验，是否立即重启完成升级？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes and self.download_path:
            if self._launch_updater(self.download_path):
                self.accept()
                app = QApplication.instance()
                if app is not None:
                    app.quit()

    def _download_thread_finished(self) -> None:
        # Keep the worker referenced until Qt has emitted its finished signal.
        if self.download_worker is not None:
            self.download_worker.deleteLater()

    def _launch_updater(self, zip_path: Path) -> bool:
        updater_dir = self.target_dir
        updater_exe = updater_dir / "updater.exe"
        updater_py = updater_dir / "updater.py"
        if updater_exe.exists():
            command = [str(updater_exe)]
        elif updater_py.exists():
            command = [sys.executable, str(updater_py)]
        else:
            QMessageBox.warning(self, "无法重启", "未找到 updater.exe，请手动重启应用完成升级。")
            return False
        command.extend([str(os.getpid()), str(zip_path), str(updater_dir)])
        try:
            subprocess.Popen(command, cwd=str(updater_dir), close_fds=True)
        except OSError as exc:
            QMessageBox.warning(self, "无法重启", f"启动升级程序失败：{exc}")
            return False
        return True
