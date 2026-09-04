"""Bounded native /proc and /sys performance snapshot."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .models import Confidence, Envelope, Inference, Observation, StructuredError
from .policy import ProtectedResourcePolicy


@dataclass(frozen=True)
class ProcSample:
    cpu: tuple[int, ...]
    vmstat: dict[str, int]
    diskstats: dict[str, tuple[int, ...]]
    processes: dict[int, dict[str, Any]]


def _read_text(path: Path, maximum: int = 1_000_000) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        return stream.read(maximum)


def _key_values(text: str, *, suffix_multiplier: dict[str, int] | None = None) -> dict[str, int]:
    values: dict[str, int] = {}
    suffix_multiplier = suffix_multiplier or {"kB": 1024}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        if len(parts) > 1:
            value *= suffix_multiplier.get(parts[1], 1)
        values[key] = value
    return values


def _cpu_values(text: str) -> tuple[int, ...]:
    for line in text.splitlines():
        if line.startswith("cpu "):
            try:
                return tuple(int(value) for value in line.split()[1:])
            except ValueError as exc:
                raise ValueError("malformed_proc_stat") from exc
    raise ValueError("cpu_line_missing")


def _vmstat(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                result[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return result


def _diskstats(text: str) -> dict[str, tuple[int, ...]]:
    result: dict[str, tuple[int, ...]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        try:
            result[parts[2]] = tuple(int(value) for value in parts[3:])
        except ValueError:
            continue
    return result


def _process_stat(text: str) -> tuple[str, int]:
    closing = text.rfind(")")
    opening = text.find("(")
    if opening < 0 or closing < opening:
        raise ValueError("malformed_process_stat")
    name = text[opening + 1:closing]
    fields = text[closing + 2:].split()
    if len(fields) < 13:
        raise ValueError("malformed_process_stat")
    return name, int(fields[11]) + int(fields[12])


def _processes(proc_root: Path, maximum: int = 4096) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    try:
        entries = sorted((entry for entry in proc_root.iterdir() if entry.name.isdigit()), key=lambda entry: int(entry.name))[:maximum]
    except OSError:
        return result
    for entry in entries:
        try:
            name, ticks = _process_stat(_read_text(entry / "stat", 64_000))
            status = _key_values(_read_text(entry / "status", 128_000))
            io_values = _key_values(_read_text(entry / "io", 64_000), suffix_multiplier={}) if (entry / "io").is_file() else {}
            cgroup = _read_text(entry / "cgroup", 64_000).splitlines()[:8] if (entry / "cgroup").is_file() else []
            result[int(entry.name)] = {
                "pid": int(entry.name),
                "name": name,
                "cpu_ticks": ticks,
                "rss_bytes": status.get("VmRSS", 0),
                "read_bytes": io_values.get("read_bytes", 0),
                "write_bytes": io_values.get("write_bytes", 0),
                "cgroup": cgroup,
            }
        except (OSError, ValueError):
            continue
    return result


def _sample(proc_root: Path) -> ProcSample:
    return ProcSample(
        cpu=_cpu_values(_read_text(proc_root / "stat")),
        vmstat=_vmstat(_read_text(proc_root / "vmstat")),
        diskstats=_diskstats(_read_text(proc_root / "diskstats")),
        processes=_processes(proc_root),
    )


def _pressure(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    text = _read_text(path, 32_000)
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        values: dict[str, float | int] = {}
        for part in parts[1:]:
            if "=" not in part:
                continue
            key, raw = part.split("=", 1)
            try:
                values[key] = int(raw) if key == "total" else float(raw)
            except ValueError:
                continue
        result[parts[0]] = values
    return result


def _thermal(sys_root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    thermal_root = sys_root / "class/thermal"
    try:
        zones = sorted(thermal_root.glob("thermal_zone*"))[:64]
    except OSError:
        return result
    for zone in zones:
        try:
            raw = int(_read_text(zone / "temp", 64).strip())
            kind = _read_text(zone / "type", 256).strip() if (zone / "type").is_file() else zone.name
            result.append({"zone": zone.name, "type": kind, "temperature_c": round(raw / 1000 if abs(raw) > 1000 else raw, 2)})
        except (OSError, ValueError):
            continue
    return result


def _throttling(sys_root: Path) -> dict[str, Any]:
    files = sorted((sys_root / "devices/system/cpu").glob("cpu[0-9]*/thermal_throttle/*_throttle_count"))[:256]
    counts: dict[str, int] = {}
    for path in files:
        try:
            counts[str(path.relative_to(sys_root))] = int(_read_text(path, 128).strip())
        except (OSError, ValueError):
            continue
    return {
        "available": bool(counts),
        "counters": counts,
        "throttling_observed": any(value > 0 for value in counts.values()) if counts else None,
    }


def _batteries(sys_root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    root = sys_root / "class/power_supply"
    try:
        supplies = sorted(root.iterdir())[:32]
    except OSError:
        return result
    for supply in supplies:
        try:
            kind = _read_text(supply / "type", 128).strip()
        except OSError:
            continue
        if kind != "Battery":
            continue
        item: dict[str, Any] = {"name": supply.name}
        for key in ("capacity", "cycle_count", "energy_full", "energy_full_design", "charge_full", "charge_full_design"):
            try:
                item[key] = int(_read_text(supply / key, 128).strip())
            except (OSError, ValueError):
                continue
        full = item.get("energy_full", item.get("charge_full"))
        design = item.get("energy_full_design", item.get("charge_full_design"))
        item["health_percent"] = round(full / design * 100, 2) if full and design else None
        result.append(item)
    return result


def _gpus(sys_root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        cards = sorted((sys_root / "class/drm").glob("card[0-9]*"))[:16]
    except OSError:
        return result
    for card in cards:
        item = {"card": card.name}
        for key in ("vendor", "device"):
            try:
                item[key] = _read_text(card / "device" / key, 128).strip()
            except OSError:
                item[key] = None
        result.append(item)
    return result


def performance_snapshot(
    policy: ProtectedResourcePolicy,
    *,
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
    sample_seconds: float = 0.12,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    envelope = Envelope("performance.snapshot", scope={"sample_seconds": sample_seconds}, policy_fingerprint=policy.fingerprint)
    coverage = {
        "cpu": False, "memory": False, "pressure": False, "disk_activity": False,
        "process_cpu": False, "process_rss": False, "process_io": False,
        "thermal": False, "battery": False, "gpu": False,
        "thermal_throttling": False, "failed_services": False,
        "critical_kernel_logs": False,
    }
    try:
        first = _sample(proc_root)
        sleep(sample_seconds)
        second = _sample(proc_root)
    except (OSError, ValueError) as exc:
        envelope.error(StructuredError("proc_sample_failed", str(exc), operation="proc_sample", recoverable=False))
        return envelope.finish(failed=True)

    cpu_deltas = [max(0, right - left) for left, right in zip(first.cpu, second.cpu)]
    total_delta = sum(cpu_deltas)
    idle_delta = sum(cpu_deltas[index] for index in (3, 4) if index < len(cpu_deltas))
    iowait_delta = cpu_deltas[4] if len(cpu_deltas) > 4 else 0
    cpu_util = ((total_delta - idle_delta) / total_delta * 100) if total_delta else None
    iowait = (iowait_delta / total_delta * 100) if total_delta else None
    coverage["cpu"] = cpu_util is not None

    try:
        load_parts = _read_text(proc_root / "loadavg", 1024).split()
        load = [float(value) for value in load_parts[:3]]
    except (OSError, ValueError):
        load = []
        envelope.error(StructuredError("loadavg_unavailable", "Load average unavailable", operation="loadavg"))
    try:
        meminfo = _key_values(_read_text(proc_root / "meminfo", 128_000))
        memory_total = meminfo.get("MemTotal", 0)
        memory_available = meminfo.get("MemAvailable", 0)
        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", 0)
        coverage["memory"] = memory_total > 0
    except OSError:
        meminfo = {}
        memory_total = memory_available = swap_total = swap_free = 0
        envelope.error(StructuredError("meminfo_unavailable", "Memory information unavailable", operation="meminfo"))

    pressures: dict[str, Any] = {}
    for name in ("cpu", "memory", "io"):
        try:
            pressures[name] = _pressure(proc_root / "pressure" / name)
        except OSError:
            pressures[name] = None
    coverage["pressure"] = any(value is not None for value in pressures.values())

    vm_delta = {
        key: max(0, second.vmstat.get(key, 0) - first.vmstat.get(key, 0))
        for key in ("pswpin", "pswpout", "pgmajfault")
    }
    disks: list[dict[str, Any]] = []
    for name in sorted(set(first.diskstats) & set(second.diskstats)):
        left, right = first.diskstats[name], second.diskstats[name]
        if len(left) < 10 or len(right) < 10:
            continue
        read_sectors = max(0, right[2] - left[2])
        written_sectors = max(0, right[6] - left[6])
        io_ms = max(0, right[9] - left[9])
        if read_sectors or written_sectors or io_ms:
            disks.append({"device": name, "read_bytes": read_sectors * 512, "write_bytes": written_sectors * 512, "io_time_ms": io_ms})
    disks.sort(key=lambda item: (-(item["read_bytes"] + item["write_bytes"]), item["device"]))
    coverage["disk_activity"] = bool(second.diskstats)

    process_rows: list[dict[str, Any]] = []
    clock_ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    for pid in sorted(set(first.processes) & set(second.processes)):
        before = first.processes[pid]
        after = second.processes[pid]
        cpu_ticks = max(0, after["cpu_ticks"] - before["cpu_ticks"])
        row = dict(after)
        row["cpu_percent_one_core"] = round(cpu_ticks / max(sample_seconds * clock_ticks, 0.001) * 100, 2)
        row["read_bytes_delta"] = max(0, after["read_bytes"] - before["read_bytes"])
        row["write_bytes_delta"] = max(0, after["write_bytes"] - before["write_bytes"])
        process_rows.append(row)
    top_cpu = sorted(process_rows, key=lambda item: (-item["cpu_percent_one_core"], item["pid"]))[:10]
    top_rss = sorted(process_rows, key=lambda item: (-item["rss_bytes"], item["pid"]))[:10]
    top_io = sorted(process_rows, key=lambda item: (-(item["read_bytes_delta"] + item["write_bytes_delta"]), item["pid"]))[:10]
    coverage["process_cpu"] = bool(process_rows)
    coverage["process_rss"] = bool(process_rows)
    coverage["process_io"] = bool(process_rows)

    thermal = _thermal(sys_root)
    throttling = _throttling(sys_root)
    batteries = _batteries(sys_root)
    gpus = _gpus(sys_root)
    coverage["thermal"] = bool(thermal)
    coverage["thermal_throttling"] = throttling["available"]
    coverage["battery"] = bool(batteries)
    coverage["gpu"] = bool(gpus)

    io_pressure = (((pressures.get("io") or {}).get("some") or {}).get("avg10"))
    memory_pressure = (((pressures.get("memory") or {}).get("some") or {}).get("avg10"))
    top_writer = top_io[0] if top_io and top_io[0]["write_bytes_delta"] > 0 else None
    inferences: list[dict[str, Any]] = []
    if iowait is not None and iowait >= 10 and io_pressure is not None and io_pressure >= 5 and top_writer:
        inferences.append(Inference("inference:io-contention", "Storage contention is the likely current bottleneck", ("performance:cpu", "performance:pressure", "performance:process-io"), Confidence.HIGH).to_dict())
    elif cpu_util is not None and cpu_util >= 90 and top_cpu and top_cpu[0]["cpu_percent_one_core"] >= 50:
        inferences.append(Inference("inference:cpu-contention", "CPU contention is a likely current bottleneck", ("performance:cpu", "performance:process-cpu"), Confidence.HIGH).to_dict())
    elif memory_total and memory_available / memory_total < 0.1 and (vm_delta["pswpin"] + vm_delta["pswpout"]) > 0 and memory_pressure is not None and memory_pressure > 0:
        inferences.append(Inference("inference:memory-pressure", "Memory pressure and swap churn are a likely contributor", ("performance:memory", "performance:pressure", "performance:vmstat"), Confidence.HIGH).to_dict())
    else:
        inferences.append(Inference("inference:no-single-bottleneck", "The bounded sample does not establish one dominant bottleneck", ("performance:cpu", "performance:memory", "performance:pressure"), Confidence.LOW).to_dict())

    metrics = {
        "cpu_utilization_percent": round(cpu_util, 2) if cpu_util is not None else None,
        "io_wait_percent": round(iowait, 2) if iowait is not None else None,
        "load_1m": load[0] if load else None,
        "load_per_cpu": (load[0] / (os.cpu_count() or 1)) if load else None,
        "memory_available_percent": round(memory_available / memory_total * 100, 2) if memory_total else None,
        "swap_used_percent": round((swap_total - swap_free) / swap_total * 100, 2) if swap_total else 0.0,
        "swap_in_delta": vm_delta["pswpin"],
        "swap_out_delta": vm_delta["pswpout"],
        "major_fault_delta": vm_delta["pgmajfault"],
        "cpu_pressure_avg10": (((pressures.get("cpu") or {}).get("some") or {}).get("avg10")),
        "memory_pressure_avg10": memory_pressure,
        "io_pressure_avg10": io_pressure,
        "maximum_temperature_c": max((item["temperature_c"] for item in thermal), default=None),
    }
    envelope.data["observations"] = [
        Observation("performance:cpu", "cpu_sample", {"utilization_percent": metrics["cpu_utilization_percent"], "io_wait_percent": metrics["io_wait_percent"], "load_average": load}, source="/proc/stat+/proc/loadavg").to_dict(),
        Observation("performance:memory", "memory", {"total_bytes": memory_total, "available_bytes": memory_available, "swap_total_bytes": swap_total, "swap_free_bytes": swap_free}, source="/proc/meminfo", unit="bytes").to_dict(),
        Observation("performance:pressure", "pressure", pressures, source="/proc/pressure").to_dict(),
        Observation("performance:vmstat", "vmstat_delta", vm_delta, source="/proc/vmstat").to_dict(),
        Observation("performance:process-cpu", "top_processes_cpu", top_cpu, source="/proc/<pid>/stat").to_dict(),
        Observation("performance:process-io", "top_processes_io", top_io, source="/proc/<pid>/io").to_dict(),
    ]
    envelope.data["inferences"] = inferences
    available = sum(1 for value in coverage.values() if value)
    envelope.data["result"] = {
        "metrics": metrics,
        "disk_activity": disks[:20],
        "top_processes": {"cpu": top_cpu, "rss": top_rss, "io": top_io},
        "thermal": thermal,
        "thermal_throttling": throttling,
        "battery": batteries,
        "gpu": gpus,
        "coverage": {
            "percent": round(available / len(coverage) * 100, 1),
            "areas": coverage,
            "unknown_areas": [name for name, value in coverage.items() if not value],
        },
        "sample_seconds": sample_seconds,
    }
    if available < len(coverage):
        envelope.limit("partial_collector_coverage")
    return envelope.finish()
