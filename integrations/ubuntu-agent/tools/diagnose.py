#!/usr/bin/env python3
"""Read-only local dogfooding CLI for trusted diagnostic profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aag_agent.diagnostics import PROFILES, diagnose


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded read-only AAG diagnostic profile")
    parser.add_argument("profile", choices=sorted(PROFILES))
    parser.add_argument("--service")
    parser.add_argument("--manager", choices=("system", "user"))
    parser.add_argument("--pid", type=int)
    parser.add_argument("--interface")
    parser.add_argument("--path")
    parser.add_argument("--container")
    parser.add_argument("--package")
    parser.add_argument("--output", type=Path, help="Optional JSON path below runtime/diagnostics")
    args = parser.parse_args()
    destination = None
    if args.output is not None:
        root = (ROOT / "runtime/diagnostics").resolve()
        destination = args.output.resolve()
        if root != destination and root not in destination.parents:
            parser.error("--output must be below runtime/diagnostics")
    inputs = {key: value for key, value in vars(args).items() if key not in {"profile", "output"} and value is not None}
    bundle = diagnose(args.profile, inputs)
    encoded = json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if bundle["status"] in {"OBSERVED", "INDETERMINATE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
