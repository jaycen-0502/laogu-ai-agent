from __future__ import annotations

import argparse
import signal
import sys
import time

from .account_registry import AccountRegistry
from .agent_service import build_agent_service
from .config import load_settings
from .task_service import TaskService


def main() -> int:
    parser = argparse.ArgumentParser(description="Laogu Windows Agent service")
    parser.add_argument("--once", action="store_true", help="Run one heartbeat/sync/pull cycle")
    args = parser.parse_args()
    settings = load_settings()
    registry = AccountRegistry(settings.account_registry_file, settings.account_mapping_history_file)
    service = build_agent_service(TaskService(), registry)
    if service is None:
        print("LAOGU_SERVER_URL is not configured", file=sys.stderr)
        return 2
    if args.once:
        return 0 if service.cycle_once() else 1

    stopping = False
    def stop(*_args):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    service.start()
    try:
        while not stopping:
            time.sleep(0.5)
    finally:
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
