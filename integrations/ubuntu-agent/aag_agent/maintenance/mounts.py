"""Mount parsing and storage overview without double-counting autofs views."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .command import CommandRunner
from .models import Confidence, Envelope, Finding, Observation, Severity, StructuredError
from .policy import ProtectedResourcePolicy

MOUNTINFO_PATH = Path("/proc/self/mountinfo")
OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")
PSEUDO_FILESYSTEMS = {
    "autofs", "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup",
    "cgroup2", "securityfs", "debugfs", "tracefs", "pstore", "configfs",
    "mqueue", "hugetlbfs", "fusectl", "binfmt_misc",
}


def _unescape(value: str) -> str:
    return OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


@dataclass(frozen=True)
class MountRecord:
    mount_id: int
    parent_id: int
    major_minor: str
    root: str
    mount_point: str
    mount_options: tuple[str, ...]
    optional_fields: tuple[str, ...]
    filesystem_type: str
    source: str
    super_options: tuple[str, ...]

    @property
    def mount_read_only(self) -> bool:
        return "ro" in self.mount_options and "rw" not in self.mount_options

    @property
    def superblock_read_only(self) -> bool:
        return "ro" in self.super_options and "rw" not in self.super_options

    @property
    def is_pseudo(self) -> bool:
        return self.filesystem_type in PSEUDO_FILESYSTEMS

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for field in ("mount_options", "optional_fields", "super_options"):
            data[field] = list(data[field])
        return data


def parse_mountinfo(text: str) -> list[MountRecord]:
    records: list[MountRecord] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        try:
            separator = fields.index("-")
            if separator < 6 or len(fields) < separator + 4:
                raise ValueError
            records.append(MountRecord(
                mount_id=int(fields[0]),
                parent_id=int(fields[1]),
                major_minor=fields[2],
                root=_unescape(fields[3]),
                mount_point=_unescape(fields[4]),
                mount_options=tuple(fields[5].split(",")),
                optional_fields=tuple(fields[6:separator]),
                filesystem_type=fields[separator + 1],
                source=_unescape(fields[separator + 2]),
                super_options=tuple(fields[separator + 3].split(",")),
            ))
        except (ValueError, IndexError) as exc:
            raise ValueError(f"malformed_mountinfo_line:{line_number}") from exc
    return records


def read_mountinfo(path: Path = MOUNTINFO_PATH) -> list[MountRecord]:
    try:
        return parse_mountinfo(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError("mountinfo_unavailable") from exc


def _is_under(path: Path, mount: Path) -> bool:
    return path == mount or mount in path.parents


def select_backing_mount(path: str | Path, records: Iterable[MountRecord]) -> MountRecord | None:
    """Select the most-specific non-autofs backing mount for a path."""
    canonical = Path(path).resolve(strict=False)
    candidates = [record for record in records if _is_under(canonical, Path(record.mount_point).resolve(strict=False))]
    if not candidates:
        return None
    candidates.sort(
        key=lambda record: (
            len(Path(record.mount_point).parts),
            0 if record.filesystem_type == "autofs" else 1,
            record.mount_id,
        ),
        reverse=True,
    )
    return candidates[0]


def _flatten_lsblk(items: Iterable[Mapping[str, Any]], parent: str | None = None) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        current = dict(item)
        children = current.pop("children", []) or []
        if parent and not current.get("pkname"):
            current["pkname"] = parent
        flattened.append(current)
        flattened.extend(_flatten_lsblk(children, str(current.get("kname") or current.get("name") or "")))
    return flattened


def _classify_device(source: str, fstype: str, lsblk: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    item = lsblk.get(source, {})
    kind = str(item.get("type") or "unknown")
    if source.startswith("/dev/loop") or kind == "loop":
        classification = "loop"
    elif source.startswith("/dev/nbd"):
        classification = "nbd"
    elif source.startswith("/dev/mapper/") or kind in {"dm", "crypt", "lvm"}:
        classification = "device_mapper"
    elif fstype in PSEUDO_FILESYSTEMS or not source.startswith("/dev/"):
        classification = "virtual"
    elif bool(item.get("rm")):
        classification = "removable"
    else:
        classification = "block"
    return {
        "classification": classification,
        "removable": bool(item.get("rm", False)),
        "device_read_only": bool(item.get("ro", False)),
        "parent": item.get("pkname"),
        "uuid": item.get("uuid"),
        "label": item.get("label"),
    }


def storage_overview(
    policy: ProtectedResourcePolicy,
    *,
    runner: CommandRunner | None = None,
    mount_records: list[MountRecord] | None = None,
    statvfs: Callable[[str], os.statvfs_result] = os.statvfs,
) -> dict[str, Any]:
    envelope = Envelope("storage.overview", scope={}, policy_fingerprint=policy.fingerprint)
    runner = runner or CommandRunner()
    try:
        records = mount_records if mount_records is not None else read_mountinfo()
    except (RuntimeError, ValueError) as exc:
        envelope.error(StructuredError("mountinfo_unavailable", str(exc), operation="read_mountinfo", recoverable=False))
        return envelope.finish(failed=True)

    lsblk_map: dict[str, Mapping[str, Any]] = {}
    block_devices: list[dict[str, Any]] = []
    lsblk_result = runner.run("lsblk_json")
    if lsblk_result.status == "completed":
        try:
            parsed = json.loads(lsblk_result.stdout)
            for item in _flatten_lsblk(parsed.get("blockdevices", [])):
                path = item.get("path")
                if isinstance(path, str):
                    lsblk_map[path] = item
                block_devices.append({
                    "name": item.get("name"),
                    "kernel_name": item.get("kname"),
                    "path": path,
                    "type": item.get("type"),
                    "filesystem_type": item.get("fstype"),
                    "label": item.get("label"),
                    "uuid": item.get("uuid"),
                    "size_bytes": item.get("size"),
                    "read_only": bool(item.get("ro", False)),
                    "removable": bool(item.get("rm", False)),
                    "parent": item.get("pkname"),
                    "mount_points": item.get("mountpoints") or [],
                })
        except (json.JSONDecodeError, AttributeError, TypeError):
            envelope.error(StructuredError("lsblk_malformed_json", "lsblk JSON could not be parsed", operation="lsblk"))
    else:
        envelope.error(StructuredError(f"lsblk_{lsblk_result.status}", "Block-device enrichment unavailable", operation="lsblk"))

    # Duplicate targets occur with autofs. Keep the most useful backing record.
    targets: dict[str, MountRecord] = {}
    for record in records:
        previous = targets.get(record.mount_point)
        if previous is None or (previous.filesystem_type == "autofs" and record.filesystem_type != "autofs") or record.mount_id > previous.mount_id:
            targets[record.mount_point] = record

    mounts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for target, record in sorted(targets.items(), key=lambda item: (len(Path(item[0]).parts), item[0])):
        entry: dict[str, Any] = record.to_dict()
        entry.update(_classify_device(record.source, record.filesystem_type, lsblk_map))
        entry["nested"] = any(
            target != other.mount_point and _is_under(Path(target), Path(other.mount_point))
            for other in targets.values()
        )
        entry["duplicate_target_records"] = sum(1 for candidate in records if candidate.mount_point == target)
        try:
            usage = statvfs(target)
            total = usage.f_blocks * usage.f_frsize
            available = usage.f_bavail * usage.f_frsize
            free = usage.f_bfree * usage.f_frsize
            used = max(0, total - free)
            inode_total = usage.f_files
            inode_free = usage.f_ffree
            inode_used = max(0, inode_total - inode_free) if inode_total else 0
            entry.update({
                "total_bytes": total,
                "used_bytes": used,
                "available_bytes": available,
                "usage_percent": round((used / total) * 100, 2) if total else None,
                "inode_total": inode_total,
                "inode_used": inode_used,
                "inode_free": inode_free,
                "inode_usage_percent": round((inode_used / inode_total) * 100, 2) if inode_total else None,
                "statvfs_read_only": bool(usage.f_flag & getattr(os, "ST_RDONLY", 1)),
            })
        except (OSError, ValueError) as exc:
            entry["usage_error"] = type(exc).__name__
            envelope.error(StructuredError("statvfs_failed", str(exc), path=target, operation="statvfs"))
        entry["read_only"] = bool(entry.get("statvfs_read_only", False) or record.mount_read_only or entry["device_read_only"])
        entry["backing_selection"] = "non_autofs_preferred_at_same_target"
        mounts.append(entry)
        obs_id = f"mount:{record.mount_id}"
        observations.append(Observation(obs_id, "mount", {
            "target": target,
            "source": record.source,
            "filesystem_type": record.filesystem_type,
            "read_only": entry["read_only"],
            "usage_percent": entry.get("usage_percent"),
            "inode_usage_percent": entry.get("inode_usage_percent"),
        }, source="/proc/self/mountinfo+statvfs", path=target).to_dict())
        if entry["read_only"] and record.filesystem_type not in PSEUDO_FILESYSTEMS:
            envelope.data["findings"].append(Finding(
                f"finding:readonly:{record.mount_id}",
                "read_only_filesystem",
                f"Filesystem at {target} is read-only",
                Severity.CRITICAL,
                Confidence.CONFIRMED,
                (obs_id,),
            ).to_dict())

    expected: list[dict[str, Any]] = []
    for rule in policy.rules:
        if not rule.expected_mount:
            continue
        backing = select_backing_mount(rule.path, records)
        present = backing is not None and Path(backing.mount_point).resolve(strict=False) == rule.path
        expected.append({"resource_id": rule.resource_id, "path": str(rule.path), "present": present, "backing": backing.to_dict() if backing else None})
        if not present:
            envelope.data["findings"].append(Finding(
                f"finding:missing-mount:{rule.resource_id}",
                "missing_expected_mount",
                f"Expected mount is missing at {rule.path}",
                Severity.CRITICAL,
                Confidence.HIGH,
            ).to_dict())

    envelope.data["observations"] = observations
    envelope.data["completeness"]["entries_examined"] = len(records)
    envelope.data["result"] = {
        "mounts": mounts,
        "block_devices": sorted(block_devices, key=lambda item: str(item.get("path") or item.get("name") or "")),
        "expected_mounts": expected,
        "lsblk_provenance": lsblk_result.provenance(),
        "double_counting_policy": "nested_mounts_marked_and_not_aggregated",
        "autofs_policy": "most_specific_non_autofs_backing_preferred",
    }
    return envelope.finish()
