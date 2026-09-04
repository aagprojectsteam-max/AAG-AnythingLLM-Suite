from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from aag_agent.maintenance.history import HistoryError, HistoryStore
from tests.maintenance_helpers import fake_outcome


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "history.sqlite3"
        self.store = HistoryStore(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def _save(self, scan_id, total, *, policy="policy", config="config", mount="8:1:/dev/test:ext4:/", completeness="complete", retention=90, children=None):
        return self.store.save(
            scan_id,
            fake_outcome(self.root, total=total, children=children, mount_identity=mount),
            config_fingerprint=config,
            policy_fingerprint=policy,
            completeness=completeness,
            error_count=0 if completeness == "complete" else 1,
            retention=retention,
        )

    def test_sqlite_creation_and_schema_migration(self):
        self.store.migrate()
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("maintenance_snapshots", tables)
        self.assertIn("maintenance_metrics", tables)

    def test_migrates_existing_v1_database(self):
        with sqlite3.connect(self.path) as connection:
            connection.executescript("""
              CREATE TABLE maintenance_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL, root TEXT NOT NULL, mount_identity TEXT NOT NULL,
                config_fingerprint TEXT NOT NULL, policy_fingerprint TEXT NOT NULL,
                size_dimension TEXT NOT NULL, completeness TEXT NOT NULL,
                error_count INTEGER NOT NULL, totals_json TEXT NOT NULL, entries_json TEXT NOT NULL
              );
              PRAGMA user_version=1;
            """)
        self.store.migrate()
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='maintenance_metrics'").fetchone())

    def test_compatible_comparison_and_deterministic_deltas(self):
        self._save("one", 100, children={"a": 60, "removed": 40})
        self._save("two", 160, children={"a": 100, "new": 60})
        result = self.store.latest_growth(str(self.root))
        self.assertTrue(result["comparable"])
        self.assertEqual(result["delta_bytes"], 60)
        contributors = {Path(item["path"]).name: item for item in result["contributors"]}
        self.assertEqual(contributors["new"]["state"], "new")
        self.assertEqual(contributors["removed"]["state"], "removed")
        self.assertEqual(result, self.store.latest_growth(str(self.root)))

    def test_incompatible_policy_and_changed_mount_are_not_compared(self):
        self._save("one", 100, policy="old", mount="old")
        self._save("two", 200, policy="new", mount="new")
        result = self.store.latest_growth(str(self.root))
        self.assertFalse(result["comparable"])
        fields = result["incompatible_snapshots"][0]["incompatibilities"]
        self.assertIn("policy_fingerprint", fields); self.assertIn("mount_identity", fields)

    def test_partial_scan_lowers_confidence(self):
        self._save("one", 100)
        self._save("two", 120, completeness="partial")
        self.assertEqual(self.store.latest_growth(str(self.root))["confidence"], "low")

    def test_retention(self):
        for index in range(5):
            self._save(f"scan-{index}", index + 1, retention=3)
        self.assertEqual(len(self.store.list(str(self.root))), 3)

    def test_corrupt_database_fails_without_replacement(self):
        self.path.write_bytes(b"not sqlite")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(HistoryError, "corrupt"):
            self.store.migrate()
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_read_behavior(self):
        self._save("one", 100)
        errors = []
        results = []
        def reader():
            try:
                results.append(self.store.list(str(self.root)))
            except Exception as exc:  # test captures thread outcome
                errors.append(exc)
        threads = [threading.Thread(target=reader) for _ in range(5)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertFalse(errors)
        self.assertEqual([len(value) for value in results], [1] * 5)

    def test_metric_baseline_uses_compatible_summary_history(self):
        for value in (10.0, 20.0, 30.0):
            self.store.save_metrics("performance", {"load_1m": value, "ignored": None}, config_fingerprint="cfg", completeness="complete")
        baseline = self.store.metric_baseline("performance", config_fingerprint="cfg")
        self.assertEqual(baseline["samples"], 3)
        self.assertEqual(baseline["metrics"]["load_1m"]["median"], 20.0)


if __name__ == "__main__": unittest.main()
