import argparse
import json
import sys
from typing import Any

from .account_registry import AccountRegistry
from .browser_manager import BrowserManager
from .config import load_settings
from .laogu_api import LaoguApi
from .logger import build_logger
from .models import Task
from .task_manager import TaskManager


def _print_accounts(records) -> None:
    headers = (
        "Profile",
        "Profile ID",
        "Browser",
        "Login",
        "X Username",
        "X Account ID",
        "Account Status",
        "Last Checked",
    )
    rows = [
        (
            item.profile_name or "-",
            item.profile_id,
            item.browser_status.value,
            item.login_status.value,
            item.x_username or "-",
            item.x_account_id or "-",
            item.account_status.value,
            item.last_checked.isoformat(),
        )
        for item in records
    ]
    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]
    for row in [headers, *rows]:
        print(
            "  ".join(
                str(value).ljust(widths[index]) for index, value in enumerate(row)
            )
        )


def _run_account_registry_command(arguments: list[str]) -> int | None:
    if not arguments or arguments[0] not in {"accounts", "account"}:
        return None
    settings = load_settings()
    registry = AccountRegistry(
        settings.account_registry_file,
        settings.account_mapping_history_file,
    )
    if arguments[0] == "accounts":
        _print_accounts(registry.list())
        return 0
    if len(arguments) < 2 or not arguments[1].strip():
        print("PROFILE_ID is required", file=sys.stderr)
        return 2
    record = registry.get(arguments[1])
    if record is None:
        print(f"Account mapping not found: {arguments[1]}", file=sys.stderr)
        return 1
    _print_accounts([record])
    return 0


def _profile_by_name(profiles: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in profiles if str(item.get("profileName")) == name), None)


def _select_profiles(profiles: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], str]:
    first = _profile_by_name(profiles, "11")
    if first is None:
        raise RuntimeError("Required Profile 11 does not exist")

    second = _profile_by_name(profiles, "12")
    note = "Profile 12 found"
    if second is None:
        candidates = [
            item
            for item in profiles
            if item.get("profileId") != first.get("profileId")
            and str(item.get("profileName")) not in {"", "default"}
        ]
        if not candidates:
            raise RuntimeError("Profile 12 is absent and no second real Profile is available")
        second = candidates[0]
        note = f"Profile 12 absent; using discovered Profile {second.get('profileName')}"
    return first, second, note


def _make_task(
    manager: TaskManager,
    profile: dict[str, Any],
    url: str,
    timeout_seconds: int,
    scenario: str,
) -> Task:
    return manager.create_task(
        profile_id=str(profile["profileId"]),
        profile_name=str(profile["profileName"]),
        url=url,
        timeout_seconds=timeout_seconds,
        metadata={"scenario": scenario},
    )


def _run_scenario(
    manager: TaskManager,
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    timeout_seconds: int,
    isolation_failure: bool,
) -> list[Task]:
    if isolation_failure:
        scenario = "error_isolation"
        first_url = "invalid://profile-11"
        second_url = "https://example.org/?profile=" + str(second["profileName"])
    else:
        scenario = "parallel_success"
        first_url = "https://example.com/?profile=11"
        second_url = "https://example.com/?profile=" + str(second["profileName"])

    tasks = [
        _make_task(manager, first, first_url, timeout_seconds, scenario),
        _make_task(manager, second, second_url, timeout_seconds, scenario),
    ]
    return manager.run_concurrent(tasks)


def main() -> int:
    registry_result = _run_account_registry_command(sys.argv[1:])
    if registry_result is not None:
        return registry_result

    parser = argparse.ArgumentParser(description="Laogu multi-profile concurrency test")
    parser.add_argument(
        "--scenario",
        choices=("success", "isolation", "all"),
        default="all",
    )
    parser.add_argument("--timeout", type=int, default=None)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    settings = load_settings()
    timeout_seconds = args.timeout or settings.default_timeout_seconds
    logger = build_logger(settings.log_file)
    api = LaoguApi(settings)
    browser_manager = BrowserManager(api)
    task_manager = TaskManager(
        browser_manager,
        logger,
        max_workers=settings.max_concurrency,
    )

    api.health()
    profiles = browser_manager.get_profiles()
    first, second, selection_note = _select_profiles(profiles)
    report: dict[str, Any] = {
        "selection": {
            "note": selection_note,
            "first": {
                "profileId": first["profileId"],
                "profileName": first["profileName"],
            },
            "second": {
                "profileId": second["profileId"],
                "profileName": second["profileName"],
            },
        },
        "maxConcurrency": settings.max_concurrency,
        "timeoutSeconds": timeout_seconds,
        "scenarios": {},
    }

    if args.scenario in ("success", "all"):
        tasks = _run_scenario(
            task_manager,
            first,
            second,
            timeout_seconds=timeout_seconds,
            isolation_failure=False,
        )
        report["scenarios"]["parallel_success"] = [task.to_dict() for task in tasks]

    if args.scenario in ("isolation", "all"):
        tasks = _run_scenario(
            task_manager,
            first,
            second,
            timeout_seconds=timeout_seconds,
            isolation_failure=True,
        )
        report["scenarios"]["error_isolation"] = [task.to_dict() for task in tasks]

    settings.result_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
