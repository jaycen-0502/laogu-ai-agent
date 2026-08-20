import argparse
import json
import sys

from .account_discovery import AccountDiscovery
from .account_registry import AccountRegistry
from .browser_manager import BrowserManager
from .config import load_settings
from .laogu_api import LaoguApi
from .logger import build_logger
from .laogu_hook_runner import LaoguProjectHookRunner


def _table(records) -> str:
    headers = (
        "Profile",
        "Browser",
        "Login",
        "X Username",
        "X Account ID",
        "Account Status",
    )
    rows = [
        (
            item.profile_name or item.profile_id,
            item.browser_status.value,
            item.login_status.value,
            item.x_username or "-",
            item.x_account_id or "-",
            item.account_status.value,
        )
        for item in records
    ]
    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]
    return "\n".join(
        "  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row))
        for row in [headers, *rows]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover X accounts for Laogu Profiles")
    parser.add_argument(
        "--profile-id",
        action="append",
        default=[],
        help="Limit scanning to one Profile ID; may be repeated.",
    )
    parser.add_argument("--timeout", type=int, default=None)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    settings = load_settings()
    logger = build_logger(settings.log_file)
    hook_runner = LaoguProjectHookRunner(
        node_path=settings.automation_node_path,
        runtime_dir=settings.automation_runtime_dir,
        script_path=settings.account_discovery_script,
        launch_base_url=settings.base_url,
        api_header=settings.api_header,
        api_key=settings.api_key,
        working_dir=settings.account_registry_file.parent.parent,
    )
    discovery = AccountDiscovery(
        BrowserManager(LaoguApi(settings)),
        logger,
        hook_path=settings.account_discovery_hook_path,
        discovery_url=settings.account_discovery_url,
        timeout_seconds=args.timeout or settings.default_timeout_seconds,
        max_workers=settings.max_concurrency,
        result_file=settings.account_discovery_result_file,
        hook_runner=hook_runner,
    )
    records = discovery.scan(args.profile_id)
    registry = AccountRegistry(
        settings.account_registry_file,
        settings.account_mapping_history_file,
    )
    registered = registry.update_many(records)
    print(_table(registered))
    print()
    print(
        json.dumps(
            {
                "count": len(registered),
                "items": [record.to_dict() for record in registered],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
