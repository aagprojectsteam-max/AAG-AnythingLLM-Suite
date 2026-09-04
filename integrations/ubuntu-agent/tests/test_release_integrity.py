import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class ReleaseIntegrityTests(unittest.TestCase):
    def test_release_manifest_and_status_match_source(self):
        for line in (ROOT / "release/MANIFEST.sha256").read_text().splitlines():
            expected, relative = line.split("  ", 1)
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)
        status = json.loads((ROOT / "release/status.json").read_text())
        self.assertEqual(status["source_sha256"], hashlib.sha256((ROOT / "app/agent.py").read_bytes()).hexdigest())
        self.assertEqual(len(status["accepted_contracts"]), 1)
        self.assertEqual(status["accepted_contracts"][0]["target"], "aag-ubuntu-agent-bridge.service")


if __name__ == "__main__": unittest.main()
