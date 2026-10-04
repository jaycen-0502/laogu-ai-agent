# -*- coding: utf-8 -*-
"""Persistent profile/group store with atomic JSON writes and indexes."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

logger = logging.getLogger("laogu.agent.group_manager")

GroupView = dict[str, Any]


@dataclass
class GroupRecord:
    """A user-defined profile group."""

    group_id: str
    name: str
    is_custom: bool = True
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GroupRecord:
        if not isinstance(data, Mapping):
            raise TypeError("group record must be a mapping")
        return cls(
            group_id=str(data.get("group_id") or ""),
            name=str(data.get("name") or "").strip(),
            is_custom=bool(data.get("is_custom", True)),
            created_at=str(data.get("created_at") or ""),
        )


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory fsync for durable rename metadata."""
    try:
        directory_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        # Windows commonly does not allow opening a directory this way.
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON via a unique same-directory temp file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(
                payload,
                fp,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())

        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove temp file %s", temp_path)
        raise


class ProfileGroupStore:
    """Thread-safe persistent store for profiles and custom groups.

    The in-process lock does not coordinate multiple application processes.
    If multiple processes can write the same file, add an inter-process lock
    around the complete read-modify-write transaction.
    """

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._custom_groups: dict[str, GroupRecord] = {}
        self._profile_to_group: dict[str, str] = {}
        self._group_to_profiles: dict[str, set[str]] = {}
        self._group_name_to_id: dict[str, str] = {}
        self._collapsed_groups: set[str] = set()
        self._load()

    @staticmethod
    def _name_key(name: str) -> str:
        return name.strip().casefold()

    def _rebuild_indexes_locked(self) -> None:
        self._group_to_profiles = {
            gid: set() for gid in self._custom_groups
        }
        self._group_name_to_id = {}
        for gid, group in self._custom_groups.items():
            name_key = self._name_key(group.name)
            if name_key and name_key not in self._group_name_to_id:
                self._group_name_to_id[name_key] = gid

        for profile_id, group_id in self._profile_to_group.items():
            if group_id in self._group_to_profiles:
                self._group_to_profiles[group_id].add(profile_id)

    def _load(self) -> None:
        with self._lock:
            if not self.file_path.exists():
                return
            try:
                raw = json.loads(self.file_path.read_text(encoding="utf-8"))
                if not isinstance(raw, Mapping):
                    raise ValueError("root JSON value must be an object")

                groups_data = raw.get("custom_groups", {})
                if not isinstance(groups_data, Mapping):
                    raise ValueError("custom_groups must be an object")

                groups: dict[str, GroupRecord] = {}
                for raw_gid, raw_group in groups_data.items():
                    try:
                        gid = str(raw_gid)
                        group = GroupRecord.from_dict(raw_group)
                        group.group_id = gid
                        if not gid or not group.name:
                            raise ValueError("group id/name cannot be empty")
                        groups[gid] = group
                    except (TypeError, ValueError) as exc:
                        logger.warning("Skip invalid group %r: %s", raw_gid, exc)

                profile_data = raw.get("profile_to_group", {})
                if not isinstance(profile_data, Mapping):
                    profile_data = {}
                profile_map = {
                    str(profile_id): str(group_id)
                    for profile_id, group_id in profile_data.items()
                    if str(group_id) in groups
                }

                collapsed_data = raw.get("collapsed_groups", [])
                if not isinstance(collapsed_data, (list, tuple, set)):
                    collapsed_data = []

                self._custom_groups = groups
                self._profile_to_group = profile_map
                self._collapsed_groups = {
                    str(group_id) for group_id in collapsed_data
                }
                self._rebuild_indexes_locked()
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                logger.exception(
                    "Failed to load profile groups from %s",
                    self.file_path,
                )
                # Do not partially retain data after a failed load.
                self._custom_groups.clear()
                self._profile_to_group.clear()
                self._collapsed_groups.clear()
                self._rebuild_indexes_locked()

    def _state_snapshot_locked(
        self,
    ) -> tuple[dict[str, GroupRecord], dict[str, str], set[str]]:
        return (
            {
                gid: replace(group)
                for gid, group in self._custom_groups.items()
            },
            dict(self._profile_to_group),
            set(self._collapsed_groups),
        )

    def _restore_state_locked(
        self,
        state: tuple[dict[str, GroupRecord], dict[str, str], set[str]],
    ) -> None:
        groups, profile_map, collapsed = state
        self._custom_groups = groups
        self._profile_to_group = profile_map
        self._collapsed_groups = collapsed
        self._rebuild_indexes_locked()

    def _save_locked(self) -> None:
        payload = {
            "custom_groups": {
                gid: group.to_dict()
                for gid, group in self._custom_groups.items()
            },
            "profile_to_group": dict(self._profile_to_group),
            "collapsed_groups": sorted(self._collapsed_groups),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write_json(self.file_path, payload)

    def _commit_locked(self, mutation: Any) -> None:
        old_state = self._state_snapshot_locked()
        try:
            mutation()
            self._save_locked()
        except Exception:
            self._restore_state_locked(old_state)
            raise

    def list_custom_groups(self) -> list[GroupRecord]:
        with self._lock:
            groups = [replace(group) for group in self._custom_groups.values()]
        return sorted(groups, key=lambda group: (group.created_at or group.name))

    def create_group(self, name: str) -> GroupRecord:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("分组名称不能为空")

        with self._lock:
            existing_id = self._group_name_to_id.get(self._name_key(clean_name))
            if existing_id is not None:
                return replace(self._custom_groups[existing_id])

            group_id = f"grp_{uuid4().hex[:10]}"
            while group_id in self._custom_groups:
                group_id = f"grp_{uuid4().hex[:10]}"
            record = GroupRecord(
                group_id=group_id,
                name=clean_name,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            def mutation() -> None:
                self._custom_groups[group_id] = record
                self._rebuild_indexes_locked()

            self._commit_locked(mutation)
            return replace(record)

    def delete_group(self, group_id: str) -> bool:
        gid = str(group_id)
        with self._lock:
            if gid not in self._custom_groups:
                return False

            def mutation() -> None:
                del self._custom_groups[gid]
                for profile_id in self._group_to_profiles.get(gid, set()):
                    self._profile_to_group.pop(profile_id, None)
                self._collapsed_groups.discard(gid)
                self._rebuild_indexes_locked()

            self._commit_locked(mutation)
            return True

    def rename_group(self, group_id: str, new_name: str) -> bool:
        gid = str(group_id)
        clean_name = str(new_name or "").strip()
        if not clean_name:
            raise ValueError("分组名称不能为空")

        with self._lock:
            group = self._custom_groups.get(gid)
            if group is None:
                return False
            existing_id = self._group_name_to_id.get(self._name_key(clean_name))
            if existing_id is not None and existing_id != gid:
                raise ValueError("分组名称已存在")

            def mutation() -> None:
                group.name = clean_name
                self._rebuild_indexes_locked()

            self._commit_locked(mutation)
            return True

    def assign_profile(self, profile_id: str, group_id: str | None) -> None:
        pid = str(profile_id)
        gid = str(group_id) if group_id else None

        with self._lock:
            def mutation() -> None:
                old_gid = self._profile_to_group.pop(pid, None)
                if old_gid in self._group_to_profiles:
                    self._group_to_profiles[old_gid].discard(pid)
                if gid in self._custom_groups:
                    self._profile_to_group[pid] = gid
                    self._group_to_profiles.setdefault(gid, set()).add(pid)

            self._commit_locked(mutation)

    def batch_assign_profiles(
        self,
        profile_ids: Sequence[str],
        group_id: str | None,
    ) -> None:
        gid = str(group_id) if group_id else None
        profile_ids_clean = list(dict.fromkeys(str(pid) for pid in profile_ids))

        with self._lock:
            def mutation() -> None:
                for pid in profile_ids_clean:
                    old_gid = self._profile_to_group.pop(pid, None)
                    if old_gid in self._group_to_profiles:
                        self._group_to_profiles[old_gid].discard(pid)
                    if gid in self._custom_groups:
                        self._profile_to_group[pid] = gid
                        self._group_to_profiles.setdefault(gid, set()).add(pid)

            self._commit_locked(mutation)

    def get_assigned_group_id(self, profile_id: str) -> str | None:
        with self._lock:
            gid = self._profile_to_group.get(str(profile_id))
            return gid if gid in self._custom_groups else None

    def is_group_collapsed(self, group_id: str) -> bool:
        with self._lock:
            return str(group_id) in self._collapsed_groups

    def set_group_collapsed(self, group_id: str, collapsed: bool) -> None:
        gid = str(group_id)
        with self._lock:
            def mutation() -> None:
                if collapsed:
                    self._collapsed_groups.add(gid)
                else:
                    self._collapsed_groups.discard(gid)

            self._commit_locked(mutation)

    def set_all_collapsed(
        self,
        group_ids: Sequence[str],
        collapsed: bool,
    ) -> None:
        ids = {str(group_id) for group_id in group_ids}
        with self._lock:
            def mutation() -> None:
                if collapsed:
                    self._collapsed_groups.update(ids)
                else:
                    self._collapsed_groups.clear()

            self._commit_locked(mutation)

    @staticmethod
    def _record_text(record: Any, field_name: str) -> str:
        value = getattr(record, field_name, "")
        return str(value or "").strip()

    def cluster_accounts(self, records: Sequence[Any]) -> list[GroupView]:
        """Assign records to custom groups or proxy-node buckets.

        Let G be custom groups, P be persisted assignments, R be records, and
        B be non-empty result buckets.  This implementation is
        O(G + P + R + B log B), with average O(1) dictionary bucket lookup.
        """
        with self._lock:
            custom_groups = {
                gid: replace(group)
                for gid, group in self._custom_groups.items()
            }
            profile_map = dict(self._profile_to_group)
            collapsed = set(self._collapsed_groups)

        groups: dict[str, GroupView] = {
            gid: {
                "group_id": gid,
                "name": group.name,
                "is_custom": True,
                "node_name": "",
                "records": [],
                "collapsed": gid in collapsed,
            }
            for gid, group in custom_groups.items()
        }

        def get_bucket(
            group_id: str,
            name: str,
            node_name: str,
            is_custom: bool = False,
        ) -> GroupView:
            bucket = groups.get(group_id)
            if bucket is None:
                bucket = {
                    "group_id": group_id,
                    "name": name,
                    "is_custom": is_custom,
                    "node_name": node_name,
                    "records": [],
                    "collapsed": group_id in collapsed,
                }
                groups[group_id] = bucket
            return bucket

        for record in records:
            profile_id = self._record_text(record, "profile_id")
            assigned_id = profile_map.get(profile_id)
            if assigned_id in groups:
                groups[assigned_id]["records"].append(record)
                continue

            proxy_name = self._record_text(record, "proxy_name")
            proxy_host = self._record_text(record, "proxy_host")
            proxy_port = self._record_text(record, "proxy_port")

            if proxy_name and proxy_name != "直连":
                node_name = proxy_name
                node_id = f"node_{node_name}"
                bucket = get_bucket(
                    node_id,
                    f"节点: {node_name}",
                    node_name,
                )
            elif proxy_host and proxy_port:
                node_name = f"{proxy_host}:{proxy_port}"
                node_id = f"node_{proxy_host}_{proxy_port}"
                bucket = get_bucket(
                    node_id,
                    f"节点: {node_name}",
                    node_name,
                )
            else:
                bucket = get_bucket(
                    "node_direct",
                    "🌐 直连 / 本地网络",
                    "直连",
                )
            bucket["records"].append(record)

        result: list[GroupView] = []
        for bucket in groups.values():
            if not bucket["is_custom"] and not bucket["records"]:
                continue
            bucket["count"] = len(bucket["records"])
            result.append(bucket)

        def sort_key(item: GroupView) -> tuple[int, str]:
            if item["is_custom"]:
                order = 0
            elif item["group_id"] == "node_direct":
                order = 2
            else:
                order = 1
            return order, str(item["name"]).casefold()

        result.sort(key=sort_key)
        return result
