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
import sys
import time
import traceback
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


def _log(target_dir: Path | None, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}\n"
    try:
        if sys.stdout is not None:
            sys.stdout.write(line)
            sys.stdout.flush()
    except Exception:
        pass
    if target_dir is not None:
        try:
            log_file = target_dir / "updater.log"
            with log_file.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


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
    # Extra brief grace period for Windows kernel handle closure
    time.sleep(0.5)
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
            try:
                with archive.open(info, "r") as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, length=128 * 1024)
            except (PermissionError, OSError):
                # When updater.exe is running, Windows prevents in-place file replacement.
                # Write to .new or skip safely so the rest of the application updates and restarts.
                if destination.name.lower() in {"updater.exe", "updater.py"}:
                    try:
                        temp_new = destination.with_suffix(destination.suffix + ".new")
                        with archive.open(info, "r") as source, temp_new.open("wb") as output:
                            shutil.copyfileobj(source, output, length=128 * 1024)
                        _log(target_dir, f"Locked self-file {destination.name}, wrote to {temp_new.name}")
                    except Exception:
                        pass
                    continue
                raise


def relaunch_client(target_dir: Path) -> subprocess.Popen | None:
    executable = target_dir / "Laogu-Desktop.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Client executable not found: {executable}")

    try:
        os.chdir(str(target_dir))
    except Exception:
        pass

    # 1. On Windows, use CreateProcess (subprocess.Popen) with detached flags and SW_SHOWNORMAL
    if sys.platform == "win32":
        try:
            flags = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 1  # SW_SHOWNORMAL (1) - ensures the GUI window is visible!
            proc = subprocess.Popen(
                [str(executable)],
                cwd=str(target_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
                startupinfo=startupinfo,
            )
            _log(target_dir, f"Relaunched via subprocess.Popen (PID={proc.pid}): {executable}")
            # Give Windows kernel time to spin up the process before updater exits
            time.sleep(1.2)
            exit_code = proc.poll()
            if exit_code is not None:
                _log(target_dir, f"Warning: relaunched process exited immediately with code {exit_code}")
            else:
                _log(target_dir, f"Relaunched process (PID={proc.pid}) is running healthy")
            return proc
        except Exception as exc:
            _log(target_dir, f"subprocess.Popen failed ({exc}), falling back to os.startfile")

    # 2. Fallback to os.startfile (ShellExecute)
    if hasattr(os, "startfile"):
        try:
            os.startfile(str(executable))
            _log(target_dir, f"Relaunched via os.startfile: {executable}")
            time.sleep(1.2)
            return None
        except Exception as exc:
            _log(target_dir, f"os.startfile failed: {exc}")
            raise

    # 3. Non-Windows POSIX fallback
    proc = subprocess.Popen(
        [str(executable)],
        cwd=str(target_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    _log(target_dir, f"Relaunched via Popen: {executable}")
    time.sleep(1.0)
    return proc


def run_update(parent_pid: int, zip_path: Path, target_dir: Path) -> int:
    _log(target_dir, f"Updater started. parent_pid={parent_pid}, zip={zip_path.name}, target={target_dir}")
    if not wait_for_parent_exit(parent_pid):
        _log(target_dir, f"Warning: parent pid {parent_pid} wait timed out, proceeding anyway")
    else:
        _log(target_dir, f"Parent pid {parent_pid} exited cleanly")

    if not zip_path.is_file():
        _log(target_dir, f"Error: update archive not found at {zip_path}")
        return 3
    try:
        _log(target_dir, "Applying update archive entries...")
        apply_update(zip_path, target_dir)
        _log(target_dir, "Update extracted successfully")
        _log(target_dir, "Relaunching client...")
        relaunch_client(target_dir)
        _log(target_dir, "Client relaunch triggered successfully")
    except Exception as exc:
        _log(target_dir, f"Update execution failed: {exc}\n{traceback.format_exc()}")
        return 4
    finally:
        try:
            zip_path.unlink()
            _log(target_dir, f"Cleaned up {zip_path.name}")
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
