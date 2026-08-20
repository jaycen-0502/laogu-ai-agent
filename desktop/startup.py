from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable

import psutil

from agent.config import PROJECT_ROOT
from agent.server_client import protect_agent_directory


@dataclass(frozen=True)
class CredentialImportResult:
    imported: bool = False
    source: Path | None = None
    error: str = ""


def standalone_agent_processes() -> list[psutil.Process]:
    """Return separately launched ``agent.service_main`` processes only."""
    current_pid = os.getpid()
    matches: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            if process.pid == current_pid:
                continue
            command_line = " ".join(process.info.get("cmdline") or []).lower()
            if "agent.service_main" in command_line:
                matches.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return matches


def stop_processes(processes: Iterable[psutil.Process], timeout: float = 5.0) -> list[str]:
    """Stop standalone Agents and return human-readable failures."""
    selected = list(processes)
    failures: list[str] = []
    waiting: list[psutil.Process] = []

    for process in selected:
        try:
            process.terminate()
            waiting.append(process)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            failures.append(f"PID {process.pid}: 无法停止（{exc}）")

    if waiting:
        _, alive = psutil.wait_procs(waiting, timeout=timeout)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, OSError) as exc:
                failures.append(f"PID {process.pid}: 无法强制停止（{exc}）")
        if alive:
            _, still_alive = psutil.wait_procs(alive, timeout=timeout)
            failures.extend(f"PID {process.pid}: 停止超时" for process in still_alive)

    return failures


def _credential_candidates() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    legacy_project = os.getenv("LAOGU_LEGACY_PROJECT", "").strip()
    if legacy_project:
        candidates.append(Path(legacy_project) / "agent_data" / "credentials.json")
    candidates.extend(
        (
            home / "Desktop" / "laogu-ai-agent" / "agent_data" / "credentials.json",
            home / "OneDrive" / "Desktop" / "laogu-ai-agent" / "agent_data" / "credentials.json",
        )
    )
    return candidates


def import_existing_credentials(
    candidates: Iterable[Path] | None = None,
) -> CredentialImportResult:
    """Import an existing DPAPI-protected credential file on first launch."""
    target = PROJECT_ROOT / "agent_data" / "credentials.json"
    if target.exists():
        return CredentialImportResult()

    try:
        target_resolved = target.resolve()
    except OSError:
        target_resolved = target.absolute()

    for source_value in candidates if candidates is not None else _credential_candidates():
        source = Path(source_value)
        if not source.is_file():
            continue
        try:
            if source.resolve() == target_resolved:
                continue
            raw = source.read_bytes()
            payload = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return CredentialImportResult(source=source, error=f"无法读取旧凭据文件：{exc}")

        if not isinstance(payload, dict):
            return CredentialImportResult(source=source, error="旧凭据文件格式无效")
        if "agent_token" in payload:
            return CredentialImportResult(
                source=source,
                error="拒绝导入含明文 agent_token 的凭据文件，请重新注册 Agent",
            )
        if not isinstance(payload.get("agent_token_protected"), str) or not payload[
            "agent_token_protected"
        ].strip():
            return CredentialImportResult(
                source=source,
                error="旧凭据文件缺少 DPAPI 加密的 agent_token_protected",
            )

        temporary_name = ""
        try:
            protect_agent_directory(target.parent)
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix="credentials-", suffix=".tmp", dir=target.parent, delete=False
            ) as temporary:
                temporary.write(raw)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, target)
            return CredentialImportResult(imported=True, source=source)
        except OSError as exc:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            return CredentialImportResult(source=source, error=f"导入旧凭据失败：{exc}")
        except Exception as exc:
            return CredentialImportResult(source=source, error=f"无法保护凭据目录：{exc}")

    return CredentialImportResult()
