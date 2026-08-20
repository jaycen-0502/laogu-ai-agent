import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any


class LaoguHookRunnerError(RuntimeError):
    pass


class LaoguHookCancelledError(LaoguHookRunnerError):
    pass


class LaoguProjectHookRunner:
    """Execute a project-owned Hook with Laogu's installed Automation Runner."""

    def __init__(
        self,
        *,
        node_path: Path,
        runtime_dir: Path,
        script_path: Path,
        launch_base_url: str,
        api_header: str = "",
        api_key: str = "",
        working_dir: Path,
    ):
        self.node_path = node_path
        self.runtime_dir = runtime_dir
        self.script_path = script_path
        self.launch_base_url = launch_base_url.rstrip("/")
        self.api_header = api_header
        self.api_key = api_key
        self.working_dir = working_dir

    def run_account_discovery(
        self,
        *,
        profile_id: str,
        url: str,
        timeout_seconds: int,
        hook_path: str = "",
    ) -> dict[str, Any]:
        del hook_path
        return self._run_script(
            profile_id=profile_id,
            script_id="discover-x-account-readonly",
            params={"url": url, "timeoutMs": timeout_seconds * 1000, "readOnly": True},
            timeout_seconds=timeout_seconds,
            artifact_name="account_discovery",
            script_path=self.script_path,
        )

    def run_read_only_task(
        self,
        *,
        profile_id: str,
        task_type: str,
        params: dict[str, Any],
        timeout_seconds: int,
        script_path: Path,
    ) -> dict[str, Any]:
        if task_type not in {"x.check_login", "x.read_profile", "x.read_timeline", "x.search"}:
            raise ValueError(f"Unsupported read-only task type: {task_type}")
        return self._run_script(
            profile_id=profile_id,
            script_id=f"readonly-{task_type.replace('.', '-')}",
            params={**params, "taskType": task_type, "timeoutMs": timeout_seconds * 1000, "readOnly": True},
            timeout_seconds=timeout_seconds,
            artifact_name="x_readonly",
            script_path=script_path,
        )

    def run_managed_script(
        self,
        *,
        profile_id: str,
        task_id: str,
        params: dict[str, Any],
        timeout_seconds: int,
        script_path: Path,
        cancel_check=None,
    ) -> dict[str, Any]:
        return self._run_script(
            profile_id=profile_id,
            script_id=f"managed-{task_id}",
            params={**params, "timeoutMs": timeout_seconds * 1000},
            timeout_seconds=timeout_seconds,
            artifact_name="managed_scripts",
            script_path=script_path,
            cancel_check=cancel_check,
        )

    def _run_script(
        self,
        *,
        profile_id: str,
        script_id: str,
        params: dict[str, Any],
        timeout_seconds: int,
        artifact_name: str,
        script_path: Path,
        cancel_check=None,
    ) -> dict[str, Any]:
        self._validate_paths(script_path)
        artifacts = self.working_dir / "logs" / "artifacts" / artifact_name
        temporary_dir = self.working_dir / "logs" / "tmp"
        artifacts.mkdir(parents=True, exist_ok=True)
        temporary_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "taskType": "script",
            "taskKey": profile_id,
            "scriptId": script_id,
            "scriptPath": str(script_path),
            "selector": {"profileId": profile_id},
            "params": params,
            "launchBaseUrl": self.launch_base_url,
            "launchAuthHeader": self.api_header if self.api_key else "",
            "launchAuthValue": self.api_key,
            "artifactDir": str(artifacts),
            "runtimeDir": str(self.runtime_dir),
        }

        payload_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="automation-task-",
                dir=temporary_dir,
                delete=False,
            ) as payload_file:
                json.dump(payload, payload_file, ensure_ascii=False)
                payload_path = Path(payload_file.name)

            command = [str(self.node_path), str(self.runtime_dir / "runner.cjs"), str(payload_path)]
            if cancel_check is None:
                completed = subprocess.run(
                    command,
                    cwd=self.working_dir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds + 8,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                completed = self._run_cancellable(command, timeout_seconds + 8, cancel_check)
        except subprocess.TimeoutExpired as exc:
            raise LaoguHookRunnerError(
                f"Read-only account Hook timed out after {timeout_seconds}s"
            ) from exc
        finally:
            if payload_path is not None:
                payload_path.unlink(missing_ok=True)

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise LaoguHookRunnerError(
                f"Laogu Automation Runner failed with exit code "
                f"{completed.returncode}: {detail[:500]}"
            )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LaoguHookRunnerError(
                f"Laogu Automation Runner returned invalid JSON: "
                f"{completed.stdout[:500]}"
            ) from exc
        if not isinstance(response, dict):
            raise LaoguHookRunnerError("Laogu Automation Runner response is not an object")
        return response

    def _run_cancellable(self, command: list[str], timeout_seconds: int, cancel_check) -> subprocess.CompletedProcess:
        process = subprocess.Popen(
            command,
            cwd=self.working_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                try:
                    cancelled = bool(cancel_check())
                except Exception:
                    cancelled = False
                if cancelled:
                    process.terminate()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    raise LaoguHookCancelledError("Script task cancelled")
                if time.monotonic() >= deadline:
                    process.terminate()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    raise subprocess.TimeoutExpired(command, timeout_seconds)

    def _validate_paths(self, script_path: Path | None = None) -> None:
        required = {
            "Node executable": self.node_path,
            "Automation runtime": self.runtime_dir,
            "Automation runner": self.runtime_dir / "runner.cjs",
            "Hook script": script_path or self.script_path,
        }
        missing = [f"{label}: {path}" for label, path in required.items() if not path.exists()]
        if missing:
            raise LaoguHookRunnerError("Missing required runtime path(s): " + "; ".join(missing))
