"""Standalone Windows updater for Laogu Desktop.

Usage::

    updater.py PARENT_PID ZIP_PATH TARGET_DIR

The updater is intentionally independent from the desktop package so it can
replace files after the main process has released its Windows file handles.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import posixpath
import shutil
import subprocess
import time
import zipfile


PROTECTED_TOP_LEVEL = {
    "config",
    "agent_data",
    "logs",
    "credentials.json",
    "offline_access.json",
    "runtime_config.json",
    "visited_history_pool.json",
    "visited_history_pool.json.lock",
    "telegram_config.json",
}
_PROTECTED_TOP_LEVEL_CASEFOLD = {item.casefold() for item in PROTECTED_TOP_LEVEL}
WAIT_TIMEOUT_SECONDS = 15.0


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def wait_for_parent_exit(parent_pid: int, timeout: float = WAIT_TIMEOUT_SECONDS) -> bool:
    """Wait for the parent to disappear, returning False on timeout."""
    if parent_pid <= 0:
        return True
    deadline = time.monotonic() + max(0.0, float(timeout))
    while _process_exists(parent_pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)
    return True


def _normal_member_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def is_protected_member(name: str) -> bool:
    """Return whether an archive entry may overwrite user data."""
    normalized = posixpath.normpath(_normal_member_name(name))
    if not normalized:
        return True
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return True
    top = parts[0]
    return top.casefold() in _PROTECTED_TOP_LEVEL_CASEFOLD


def _safe_destination(target_dir: Path, member_name: str) -> Path:
    normalized = _normal_member_name(member_name)
    destination = (target_dir / normalized).resolve()
    root = target_dir.resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Unsafe archive path: {member_name}") from exc
    return destination


def apply_update(zip_path: Path, target_dir: Path) -> None:
    """Extract non-protected archive entries into ``target_dir`` safely."""
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            if is_protected_member(info.filename):
                continue
            destination = _safe_destination(target_dir, info.filename)
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            # Do not materialize symlinks supplied by an untrusted archive.
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and (mode & 0o170000) == 0o120000:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=128 * 1024)


def relaunch_client(target_dir: Path) -> subprocess.Popen:
    executable = target_dir / "Laogu-Desktop.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Client executable not found: {executable}")
    return subprocess.Popen([str(executable)], cwd=str(target_dir), close_fds=True)


def run_update(parent_pid: int, zip_path: Path, target_dir: Path) -> int:
    if not wait_for_parent_exit(parent_pid):
        return 2
    if not zip_path.is_file():
        return 3
    try:
        apply_update(zip_path, target_dir)
        relaunch_client(target_dir)
    except (OSError, ValueError, zipfile.BadZipFile):
        return 4
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a Laogu Desktop update package")
    parser.add_argument("parent_pid_pos", nargs="?", type=int)
    parser.add_argument("zip_path_pos", nargs="?", type=Path)
    parser.add_argument("target_dir_pos", nargs="?", type=Path)
    parser.add_argument("--parent-pid", "--parent_pid", dest="parent_pid_opt", type=int)
    parser.add_argument("--zip-path", "--zip_path", dest="zip_path_opt", type=Path)
    parser.add_argument("--target-dir", "--target_dir", dest="target_dir_opt", type=Path)
    parsed = parser.parse_args(argv)
    parsed.parent_pid = parsed.parent_pid_opt if parsed.parent_pid_opt is not None else parsed.parent_pid_pos
    parsed.zip_path = parsed.zip_path_opt if parsed.zip_path_opt is not None else parsed.zip_path_pos
    parsed.target_dir = parsed.target_dir_opt if parsed.target_dir_opt is not None else parsed.target_dir_pos
    if parsed.parent_pid is None or parsed.zip_path is None or parsed.target_dir is None:
        parser.error("parent_pid, zip_path, and target_dir are required")
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_update(args.parent_pid, args.zip_path, args.target_dir)


if __name__ == "__main__":
    raise SystemExit(main())
