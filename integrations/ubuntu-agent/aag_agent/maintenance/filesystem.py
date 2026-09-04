"""Bounded metadata traversal for top directories and largest files."""

from __future__ import annotations

import heapq
import os
import stat
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import ScanBudget
from .models import Envelope, StructuredError
from .mounts import MountRecord, read_mountinfo, select_backing_mount
from .policy import PolicyError, ProtectedResourcePolicy


@dataclass(frozen=True)
class ScanLimits:
    max_duration_seconds: float
    max_depth: int
    max_entries: int
    result_limit: int
    minimum_file_size: int

    @classmethod
    def from_budget(cls, budget: ScanBudget, overrides: dict[str, Any] | None = None) -> "ScanLimits":
        values: dict[str, int | float] = {
            "max_duration_seconds": budget.max_duration_seconds,
            "max_depth": budget.max_depth,
            "max_entries": budget.max_entries,
            "result_limit": budget.result_limit,
            "minimum_file_size": budget.minimum_file_size,
        }
        overrides = overrides or {}
        if set(overrides) - set(values):
            raise ValueError("unknown_scan_limit")
        for name, requested in overrides.items():
            if isinstance(requested, bool) or not isinstance(requested, (int, float)) or requested <= 0:
                raise ValueError(f"invalid_{name}")
            if requested > values[name]:
                raise ValueError(f"{name}_exceeds_profile_budget")
            values[name] = requested
        return cls(
            max_duration_seconds=float(values["max_duration_seconds"]),
            max_depth=int(values["max_depth"]),
            max_entries=int(values["max_entries"]),
            result_limit=int(values["result_limit"]),
            minimum_file_size=int(values["minimum_file_size"]),
        )


def display_path(path: str | Path) -> str:
    value = os.fsdecode(os.fspath(path))
    rendered: list[str] = []
    for character in value:
        category = unicodedata.category(character)
        if category.startswith("C") or character in {"\x1b", "\n", "\r", "\t"}:
            rendered.append(f"\\u{ord(character):04x}")
        else:
            rendered.append(character)
    return "".join(rendered)


@dataclass(frozen=True)
class FileRecord:
    path: str
    display_path: str
    logical_bytes: int
    allocated_bytes: int
    device: int
    inode: int
    links: int
    mtime_ns: int
    mode: int
    sparse: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Aggregate:
    logical_bytes: int = 0
    allocated_bytes: int = 0
    files: int = 0
    directories: int = 0
    symlinks: int = 0
    special: int = 0

    def add(self, *, logical: int, allocated: int, kind: str) -> None:
        self.logical_bytes += logical
        self.allocated_bytes += allocated
        if kind == "file":
            self.files += 1
        elif kind == "directory":
            self.directories += 1
        elif kind == "symlink":
            self.symlinks += 1
        else:
            self.special += 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanOutcome:
    root: Path
    root_device: int
    mount_identity: str
    limits: ScanLimits
    total: Aggregate = field(default_factory=Aggregate)
    top: dict[str, Aggregate] = field(default_factory=dict)
    candidates: list[FileRecord] = field(default_factory=list)
    largest: list[FileRecord] = field(default_factory=list)
    errors: list[StructuredError] = field(default_factory=list)
    entries_examined: int = 0
    hardlink_entries_skipped: int = 0
    hardlink_allocated_bytes_skipped: int = 0
    mount_boundaries_skipped: list[str] = field(default_factory=list)
    exclusions_skipped: list[str] = field(default_factory=list)
    limits_reached: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


def _allocated(st: os.stat_result) -> int:
    return max(0, int(getattr(st, "st_blocks", 0)) * 512)


def _kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "special"


def _mount_identity(record: MountRecord | None, device: int) -> str:
    if record is None:
        return f"st_dev:{device}:mount_unknown"
    return f"{record.major_minor}:{record.source}:{record.filesystem_type}:{record.root}"


def scan_tree(
    path: str | Path,
    policy: ProtectedResourcePolicy,
    limits: ScanLimits,
    *,
    scan_mode: str = "metadata",
    collect_candidates: bool = False,
    mount_records: list[MountRecord] | None = None,
    cancel_event: threading.Event | None = None,
) -> ScanOutcome:
    root = policy.validate_scope(path)
    decision = policy.classify(root)
    if scan_mode not in decision.allowed_scan_modes:
        raise PolicyError("scan_mode_blocked_by_policy")
    try:
        root_stat = root.lstat()
    except FileNotFoundError as exc:
        raise PolicyError("scan_root_missing") from exc
    except PermissionError as exc:
        raise PolicyError("scan_root_permission_denied") from exc
    records = mount_records if mount_records is not None else read_mountinfo()
    backing = select_backing_mount(root, records)
    outcome = ScanOutcome(root, root_stat.st_dev, _mount_identity(backing, root_stat.st_dev), limits)
    started = time.monotonic()
    deadline = started + limits.max_duration_seconds
    seen_inodes: set[tuple[int, int]] = set()
    nested_mounts = {
        Path(record.mount_point).resolve(strict=False)
        for record in records
        if Path(record.mount_point).resolve(strict=False) != root
        and root in Path(record.mount_point).resolve(strict=False).parents
    }
    largest_heap: list[tuple[int, str, FileRecord]] = []

    def reached(name: str) -> bool:
        if name not in outcome.limits_reached:
            outcome.limits_reached.append(name)
        return True

    stack: list[tuple[Path, int, str]] = [(root, 0, str(root))]
    while stack:
        if cancel_event is not None and cancel_event.is_set():
            reached("cancelled")
            break
        if time.monotonic() >= deadline:
            reached("max_duration_seconds")
            break
        current, depth, top_key = stack.pop()
        if current != root and current in nested_mounts:
            outcome.mount_boundaries_skipped.append(str(current))
            continue
        if current != root and policy.is_excluded(current):
            outcome.exclusions_skipped.append(str(current))
            continue
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            outcome.errors.append(StructuredError("entry_vanished", "Entry disappeared during scan", path=str(current), operation="lstat"))
            continue
        except PermissionError:
            outcome.errors.append(StructuredError("permission_denied", "Permission denied", path=str(current), operation="lstat"))
            continue
        except OSError as exc:
            outcome.errors.append(StructuredError("lstat_failed", str(exc), path=str(current), operation="lstat"))
            continue
        outcome.entries_examined += 1
        if outcome.entries_examined > limits.max_entries:
            reached("max_entries")
            break
        kind = _kind(current_stat.st_mode)
        logical = current_stat.st_size if kind == "file" else 0
        allocated = _allocated(current_stat)
        if kind == "file" and current_stat.st_nlink > 1:
            inode_key = (current_stat.st_dev, current_stat.st_ino)
            if inode_key in seen_inodes:
                outcome.hardlink_entries_skipped += 1
                outcome.hardlink_allocated_bytes_skipped += allocated
                logical = 0
                allocated = 0
            else:
                seen_inodes.add(inode_key)
        outcome.total.add(logical=logical, allocated=allocated, kind=kind)
        if current != root:
            aggregate = outcome.top.setdefault(top_key, Aggregate())
            aggregate.add(logical=logical, allocated=allocated, kind=kind)

        if kind == "file":
            record = FileRecord(
                path=os.fsdecode(os.fspath(current)),
                display_path=display_path(current),
                logical_bytes=current_stat.st_size,
                allocated_bytes=_allocated(current_stat),
                device=current_stat.st_dev,
                inode=current_stat.st_ino,
                links=current_stat.st_nlink,
                mtime_ns=current_stat.st_mtime_ns,
                mode=current_stat.st_mode,
                sparse=_allocated(current_stat) < current_stat.st_size,
            )
            key = (record.allocated_bytes, record.path, record)
            if len(largest_heap) < limits.result_limit:
                heapq.heappush(largest_heap, key)
            elif key[:2] > largest_heap[0][:2]:
                heapq.heapreplace(largest_heap, key)
            if collect_candidates and record.logical_bytes >= limits.minimum_file_size:
                outcome.candidates.append(record)
            continue
        if kind != "directory" or depth >= limits.max_depth:
            if kind == "directory" and depth >= limits.max_depth:
                reached("max_depth")
            continue
        try:
            with os.scandir(current) as iterator:
                remaining = max(0, limits.max_entries - outcome.entries_examined)
                entries = []
                for index, entry in enumerate(iterator):
                    if index >= remaining:
                        reached("max_entries")
                        break
                    entries.append(entry)
        except PermissionError:
            outcome.errors.append(StructuredError("permission_denied", "Directory cannot be read", path=str(current), operation="scandir"))
            continue
        except FileNotFoundError:
            outcome.errors.append(StructuredError("entry_vanished", "Directory disappeared during scan", path=str(current), operation="scandir"))
            continue
        except OSError as exc:
            outcome.errors.append(StructuredError("scandir_failed", str(exc), path=str(current), operation="scandir"))
            continue
        entries.sort(key=lambda entry: os.fsencode(entry.name))
        for entry in reversed(entries):
            child = Path(entry.path)
            child_top = str(child) if current == root else top_key
            stack.append((child, depth + 1, child_top))

    outcome.largest = [item[2] for item in sorted(largest_heap, key=lambda item: (item[0], item[1]), reverse=True)]
    outcome.candidates.sort(key=lambda item: (item.logical_bytes, item.path))
    outcome.elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    return outcome


def _envelope_for_scan(tool: str, path: str | Path, policy: ProtectedResourcePolicy, limits: ScanLimits) -> Envelope:
    return Envelope(tool, scope={"path": str(path), "limits": asdict(limits)}, policy_fingerprint=policy.fingerprint)


def _public_scan(outcome: ScanOutcome) -> dict[str, Any]:
    return {
        "root": str(outcome.root),
        "root_display": display_path(outcome.root),
        "mount_identity": outcome.mount_identity,
        "root_device": outcome.root_device,
        "totals": outcome.total.to_dict(),
        "top": [
            {"path": path, "display_path": display_path(path), **aggregate.to_dict()}
            for path, aggregate in sorted(outcome.top.items(), key=lambda item: (-item[1].allocated_bytes, item[0]))
        ][:outcome.limits.result_limit],
        "largest_files": [item.to_dict() for item in outcome.largest],
        "hardlinks": {
            "duplicate_entries_not_counted": outcome.hardlink_entries_skipped,
            "allocated_bytes_not_double_counted": outcome.hardlink_allocated_bytes_skipped,
        },
        "mount_boundaries_skipped": sorted(outcome.mount_boundaries_skipped),
        "exclusions_skipped": sorted(outcome.exclusions_skipped),
        "elapsed_ms": outcome.elapsed_ms,
    }


def scan_envelope(
    tool: str,
    path: str | Path,
    policy: ProtectedResourcePolicy,
    limits: ScanLimits,
    *,
    view: str,
    mount_records: list[MountRecord] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    envelope = _envelope_for_scan(tool, path, policy, limits)
    try:
        outcome = scan_tree(path, policy, limits, scan_mode="summary" if view == "top" else "metadata", mount_records=mount_records, cancel_event=cancel_event)
    except (PolicyError, ValueError, RuntimeError) as exc:
        envelope.error(StructuredError(str(exc), "Scan rejected or unavailable", path=str(path), operation="scan", recoverable=False))
        return envelope.finish(failed=True)
    envelope.data["completeness"]["entries_examined"] = outcome.entries_examined
    for error in outcome.errors:
        envelope.error(error)
    for limit in outcome.limits_reached:
        envelope.limit(limit)
    public = _public_scan(outcome)
    if view == "largest":
        public = {key: value for key, value in public.items() if key not in {"top"}}
    elif view == "top":
        public = {key: value for key, value in public.items() if key not in {"largest_files"}}
    envelope.data["result"] = public
    envelope.data["observations"].append({
        "observation_id": "filesystem:scan-summary",
        "kind": "filesystem_scan",
        "value": public["totals"],
        "source": "os.scandir+lstat",
        "unit": "bytes",
        "path": str(outcome.root),
        "confidence": "confirmed" if not outcome.errors and not outcome.limits_reached else "medium",
    })
    return envelope.finish()
