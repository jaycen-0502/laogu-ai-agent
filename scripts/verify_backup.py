#!/usr/bin/env python3
"""Read-only verification for a Laogu stage backup directory."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import tarfile


STAGE_PATTERN = re.compile(r"^stage(?P<stage>[a-z0-9]+)-final-")


def _stage_name(directory: Path) -> str | None:
    match = STAGE_PATTERN.match(directory.name.lower())
    return match.group("stage") if match else None


def verify_backup(directory: Path) -> list[str]:
    errors: list[str] = []
    try:
        is_directory = directory.is_dir()
    except PermissionError:
        return [f"permission denied: {directory}; run this read-only check with sudo"]
    if not is_directory:
        return [f"backup directory does not exist: {directory}"]
    stage = _stage_name(directory)
    if not stage:
        return [f"cannot determine stage from backup directory name: {directory.name}"]
    required = {
        f"laogu-after-stage{stage}.dump",
        f"source-after-stage{stage}.tar.gz",
        f"server-after-stage{stage}.env",
        f"nginx-after-stage{stage}",
    }
    try:
        missing = sorted(name for name in required if not (directory / name).is_file())
    except PermissionError:
        return [f"permission denied: {directory}; run this read-only check with sudo"]
    errors.extend(f"missing required file: {name}" for name in missing)
    for name in (f"laogu-after-stage{stage}.dump", f"source-after-stage{stage}.tar.gz"):
        path = directory / name
        try:
            if path.exists() and path.stat().st_size == 0:
                errors.append(f"empty backup file: {name}")
        except PermissionError:
            return [f"permission denied: {directory}; run this read-only check with sudo"]
    source = directory / f"source-after-stage{stage}.tar.gz"
    try:
        source_is_file = source.is_file()
    except PermissionError:
        return [f"permission denied: {directory}; run this read-only check with sudo"]
    if source_is_file:
        try:
            with tarfile.open(source, "r:gz") as archive:
                for member in archive.getmembers():
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                        errors.append(f"unsafe archive member: {member.name}")
        except PermissionError:
            errors.append(f"permission denied: {source}; run this read-only check with sudo")
        except (OSError, tarfile.TarError) as exc:
            errors.append(f"invalid source archive: {type(exc).__name__}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a Laogu backup without modifying it")
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    errors = verify_backup(args.backup)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"BACKUP_OK {args.backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
