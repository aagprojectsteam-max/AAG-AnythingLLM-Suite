"""SQLite summary snapshots, compatible growth, retention, and anomalies."""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .filesystem import ScanOutcome
from .models import utc_now

SCHEMA_VERSION = 2


class HistoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotRecord:
    snapshot_id: int
    scan_id: str
    created_at: str
    root: str
    mount_identity: str
    config_fingerprint: str
    policy_fingerprint: str
    size_dimension: str
    completeness: str
    error_count: int
    totals: dict[str, Any]
    entries: dict[str, dict[str, Any]]


class HistoryStore:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 3000) -> None:
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms

    def _connect(self, *, writable: bool) -> sqlite3.Connection:
        if writable:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        else:
            if not self.path.exists():
                raise HistoryError("history_database_missing")
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _check(connection: sqlite3.Connection) -> None:
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise HistoryError("history_database_corrupt") from exc
        if result is None or result[0] != "ok":
            raise HistoryError("history_database_corrupt")

    def migrate(self) -> None:
        try:
            connection = self._connect(writable=True)
            with connection:
                self._check(connection)
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise HistoryError("history_schema_too_new")
                if version == 0:
                    connection.executescript("""
                        CREATE TABLE IF NOT EXISTS maintenance_snapshots (
                            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            scan_id TEXT NOT NULL UNIQUE,
                            created_at TEXT NOT NULL,
                            root TEXT NOT NULL,
                            mount_identity TEXT NOT NULL,
                            config_fingerprint TEXT NOT NULL,
                            policy_fingerprint TEXT NOT NULL,
                            size_dimension TEXT NOT NULL CHECK(size_dimension IN ('logical','allocated')),
                            completeness TEXT NOT NULL CHECK(completeness IN ('complete','partial','failed')),
                            error_count INTEGER NOT NULL CHECK(error_count >= 0),
                            totals_json TEXT NOT NULL,
                            entries_json TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_maintenance_snapshots_root_time
                            ON maintenance_snapshots(root, snapshot_id DESC);
                        PRAGMA user_version=1;
                    """)
                if version < 2:
                    connection.executescript("""
                        CREATE TABLE IF NOT EXISTS maintenance_metrics (
                            metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            captured_at TEXT NOT NULL,
                            profile TEXT NOT NULL,
                            config_fingerprint TEXT NOT NULL,
                            completeness TEXT NOT NULL,
                            metrics_json TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_maintenance_metrics_profile_time
                            ON maintenance_metrics(profile, metric_id DESC);
                        PRAGMA user_version=2;
                    """)
        except sqlite3.DatabaseError as exc:
            raise HistoryError("history_database_corrupt") from exc
        finally:
            if "connection" in locals():
                connection.close()

    def save(
        self,
        scan_id: str,
        outcome: ScanOutcome,
        *,
        config_fingerprint: str,
        policy_fingerprint: str,
        completeness: str,
        error_count: int,
        size_dimension: str = "allocated",
        retention: int = 90,
        created_at: str | None = None,
    ) -> int:
        if size_dimension not in {"logical", "allocated"}:
            raise HistoryError("invalid_size_dimension")
        self.migrate()
        totals = outcome.total.to_dict()
        entries = {path: aggregate.to_dict() for path, aggregate in sorted(outcome.top.items())}
        try:
            connection = self._connect(writable=True)
            with connection:
                cursor = connection.execute(
                    """INSERT INTO maintenance_snapshots
                       (scan_id, created_at, root, mount_identity, config_fingerprint,
                        policy_fingerprint, size_dimension, completeness, error_count,
                        totals_json, entries_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scan_id,
                        created_at or utc_now(),
                        str(outcome.root),
                        outcome.mount_identity,
                        config_fingerprint,
                        policy_fingerprint,
                        size_dimension,
                        completeness,
                        error_count,
                        json.dumps(totals, sort_keys=True, separators=(",", ":")),
                        json.dumps(entries, sort_keys=True, separators=(",", ":")),
                    ),
                )
                snapshot_id = int(cursor.lastrowid)
                connection.execute(
                    """DELETE FROM maintenance_snapshots
                       WHERE root = ? AND snapshot_id NOT IN (
                         SELECT snapshot_id FROM maintenance_snapshots
                         WHERE root = ? ORDER BY snapshot_id DESC LIMIT ?
                       )""",
                    (str(outcome.root), str(outcome.root), retention),
                )
            return snapshot_id
        except sqlite3.IntegrityError as exc:
            raise HistoryError("duplicate_or_invalid_snapshot") from exc
        except sqlite3.DatabaseError as exc:
            raise HistoryError("history_write_failed") from exc
        finally:
            if "connection" in locals():
                connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> SnapshotRecord:
        try:
            return SnapshotRecord(
                snapshot_id=row["snapshot_id"],
                scan_id=row["scan_id"],
                created_at=row["created_at"],
                root=row["root"],
                mount_identity=row["mount_identity"],
                config_fingerprint=row["config_fingerprint"],
                policy_fingerprint=row["policy_fingerprint"],
                size_dimension=row["size_dimension"],
                completeness=row["completeness"],
                error_count=row["error_count"],
                totals=json.loads(row["totals_json"]),
                entries=json.loads(row["entries_json"]),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HistoryError("history_row_corrupt") from exc

    def list(self, root: str, *, limit: int = 100) -> list[SnapshotRecord]:
        try:
            connection = self._connect(writable=False)
            self._check(connection)
            rows = connection.execute(
                "SELECT * FROM maintenance_snapshots WHERE root=? ORDER BY snapshot_id DESC LIMIT ?",
                (root, limit),
            ).fetchall()
            return [self._record(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise HistoryError("history_read_failed") from exc
        finally:
            if "connection" in locals():
                connection.close()

    @staticmethod
    def compatibility(current: SnapshotRecord, previous: SnapshotRecord) -> list[str]:
        fields = {
            "root": (current.root, previous.root),
            "mount_identity": (current.mount_identity, previous.mount_identity),
            "config_fingerprint": (current.config_fingerprint, previous.config_fingerprint),
            "policy_fingerprint": (current.policy_fingerprint, previous.policy_fingerprint),
            "size_dimension": (current.size_dimension, previous.size_dimension),
        }
        return [name for name, values in fields.items() if values[0] != values[1]]

    @staticmethod
    def compare(current: SnapshotRecord, previous: SnapshotRecord) -> dict[str, Any]:
        incompatibilities = HistoryStore.compatibility(current, previous)
        if incompatibilities:
            return {
                "comparable": False,
                "incompatibilities": incompatibilities,
                "current_snapshot_id": current.snapshot_id,
                "previous_snapshot_id": previous.snapshot_id,
                "confidence": "unknown",
            }
        dimension_key = f"{current.size_dimension}_bytes"
        current_total = int(current.totals.get(dimension_key, 0))
        previous_total = int(previous.totals.get(dimension_key, 0))
        delta = current_total - previous_total
        percent = (delta / previous_total * 100) if previous_total else None
        paths = sorted(set(current.entries) | set(previous.entries))
        contributors = []
        for path in paths:
            before = int(previous.entries.get(path, {}).get(dimension_key, 0))
            after = int(current.entries.get(path, {}).get(dimension_key, 0))
            contributors.append({
                "path": path,
                "previous_bytes": before,
                "current_bytes": after,
                "delta_bytes": after - before,
                "state": "new" if path not in previous.entries else ("removed" if path not in current.entries else "existing"),
            })
        contributors.sort(key=lambda item: (-abs(item["delta_bytes"]), item["path"]))
        complete = current.completeness == previous.completeness == "complete"
        return {
            "comparable": True,
            "incompatibilities": [],
            "current_snapshot_id": current.snapshot_id,
            "previous_snapshot_id": previous.snapshot_id,
            "current_at": current.created_at,
            "previous_at": previous.created_at,
            "size_dimension": current.size_dimension,
            "current_bytes": current_total,
            "previous_bytes": previous_total,
            "delta_bytes": delta,
            "percentage_change": round(percent, 3) if percent is not None and math.isfinite(percent) else None,
            "contributors": contributors,
            "confidence": "high" if complete else "low",
            "completeness": {"current": current.completeness, "previous": previous.completeness},
        }

    def latest_growth(self, root: str, *, anomaly_mad_multiplier: float = 4.0) -> dict[str, Any]:
        snapshots = self.list(root, limit=100)
        if len(snapshots) < 2:
            return {"comparable": False, "reason": "insufficient_history", "available_snapshots": len(snapshots), "confidence": "unknown"}
        current = snapshots[0]
        skipped: list[dict[str, Any]] = []
        for candidate in snapshots[1:]:
            incompatibilities = self.compatibility(current, candidate)
            if not incompatibilities:
                result = self.compare(current, candidate)
                result["incompatible_newer_snapshots_skipped"] = skipped
                result["anomaly"] = self._anomaly(snapshots, current, anomaly_mad_multiplier)
                return result
            skipped.append({"snapshot_id": candidate.snapshot_id, "incompatibilities": incompatibilities})
        return {"comparable": False, "reason": "no_compatible_previous_snapshot", "incompatible_snapshots": skipped, "confidence": "unknown"}

    def save_metrics(
        self,
        profile: str,
        metrics: dict[str, Any],
        *,
        config_fingerprint: str,
        completeness: str,
        retention: int = 180,
    ) -> int:
        self.migrate()
        scalar_metrics = {
            key: value
            for key, value in metrics.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
        }
        try:
            connection = self._connect(writable=True)
            with connection:
                cursor = connection.execute(
                    """INSERT INTO maintenance_metrics
                       (captured_at, profile, config_fingerprint, completeness, metrics_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (utc_now(), profile, config_fingerprint, completeness, json.dumps(scalar_metrics, sort_keys=True, separators=(",", ":"))),
                )
                metric_id = int(cursor.lastrowid)
                connection.execute(
                    """DELETE FROM maintenance_metrics
                       WHERE profile = ? AND metric_id NOT IN (
                         SELECT metric_id FROM maintenance_metrics
                         WHERE profile = ? ORDER BY metric_id DESC LIMIT ?
                       )""",
                    (profile, profile, retention),
                )
            return metric_id
        except sqlite3.DatabaseError as exc:
            raise HistoryError("metric_history_write_failed") from exc
        finally:
            if "connection" in locals():
                connection.close()

    def metric_baseline(self, profile: str, *, config_fingerprint: str, limit: int = 30) -> dict[str, Any]:
        try:
            connection = self._connect(writable=False)
            self._check(connection)
            rows = connection.execute(
                """SELECT metrics_json, completeness, config_fingerprint
                   FROM maintenance_metrics WHERE profile=?
                   ORDER BY metric_id DESC LIMIT ?""",
                (profile, limit),
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise HistoryError("metric_history_read_failed") from exc
        finally:
            if "connection" in locals():
                connection.close()
        compatible = [row for row in rows if row["config_fingerprint"] == config_fingerprint and row["completeness"] != "failed"]
        values: dict[str, list[float]] = {}
        try:
            for row in compatible:
                for key, value in json.loads(row["metrics_json"]).items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        values.setdefault(key, []).append(float(value))
        except (json.JSONDecodeError, TypeError) as exc:
            raise HistoryError("metric_history_row_corrupt") from exc
        return {
            "samples": len(compatible),
            "metrics": {
                key: {
                    "median": statistics.median(samples),
                    "minimum": min(samples),
                    "maximum": max(samples),
                    "median_absolute_deviation": statistics.median([abs(value - statistics.median(samples)) for value in samples]),
                }
                for key, samples in sorted(values.items())
            },
        }

    @staticmethod
    def _anomaly(snapshots: Iterable[SnapshotRecord], current: SnapshotRecord, multiplier: float) -> dict[str, Any]:
        compatible = [item for item in snapshots if not HistoryStore.compatibility(current, item)]
        compatible.sort(key=lambda item: item.snapshot_id)
        key = f"{current.size_dimension}_bytes"
        deltas = [int(right.totals.get(key, 0)) - int(left.totals.get(key, 0)) for left, right in zip(compatible, compatible[1:])]
        if len(deltas) < 5:
            return {"status": "insufficient_history", "samples": len(deltas)}
        historical = deltas[:-1]
        observed = deltas[-1]
        median = statistics.median(historical)
        deviations = [abs(value - median) for value in historical]
        mad = statistics.median(deviations)
        score = abs(observed - median) / mad if mad else (float("inf") if observed != median else 0.0)
        return {
            "status": "anomalous" if score >= multiplier else "within_baseline",
            "samples": len(deltas),
            "observed_delta_bytes": observed,
            "rolling_median_delta_bytes": median,
            "median_absolute_deviation": mad,
            "mad_score": score if math.isfinite(score) else "infinite",
            "configured_mad_multiplier": multiplier,
        }
