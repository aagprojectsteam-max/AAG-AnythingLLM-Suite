#!/usr/bin/env python3
"""Read-only AAG maturity and integrity report; no mutation options."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aag_agent.maturity import MaturityVerifier


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify AAG maturity and integrity read-only")
    parser.add_argument("--live", action="store_true", help="also run the exact read-only Bridge state/health observation")
    args = parser.parse_args(argv)
    result = MaturityVerifier().run(live=args.live)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS_WITH_EXPLICIT_BOUNDARIES" else 2


if __name__ == "__main__":
    raise SystemExit(main())
