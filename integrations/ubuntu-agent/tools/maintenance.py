#!/usr/bin/env python3
"""Local typed CLI for AAG Maintenance Intelligence V1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aag_agent.maintenance import dispatch


COMMANDS = {
    "system-health": "system.health",
    "performance-snapshot": "performance.snapshot",
    "storage-overview": "storage.overview",
    "storage-top": "storage.top",
    "storage-inspect": "storage.inspect",
    "storage-largest-files": "storage.largest_files",
    "storage-snapshot": "storage.snapshot",
    "storage-growth": "storage.growth",
    "storage-duplicate-candidates": "storage.duplicate_candidates",
    "storage-duplicate-verify": "storage.duplicate_verify",
    "storage-space-discrepancy": "storage.space_discrepancy",
    "maintenance-plan": "maintenance.plan",
    "maintenance-explain": "maintenance.explain",
}

NO_PATH = {"system-health", "performance-snapshot", "storage-overview"}
PROFILE_COMMANDS = {
    "storage-top", "storage-inspect", "storage-largest-files",
    "storage-snapshot", "storage-duplicate-candidates",
    "storage-duplicate-verify", "storage-space-discrepancy", "maintenance-plan",
}
DEEP_ONLY = {"storage-duplicate-candidates", "storage-duplicate-verify", "storage-space-discrepancy"}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="AAG read-only maintenance intelligence")
    subcommands = root.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = subcommands.add_parser(name)
        if name not in NO_PATH:
            command.add_argument("path")
        if name in PROFILE_COMMANDS:
            choices = ("deep",) if name in DEEP_ONLY else ("quick", "standard", "deep")
            command.add_argument("--profile", choices=choices, default="deep" if name in DEEP_ONLY else "standard")
        if name == "maintenance-explain":
            command.add_argument("--item-id", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    operation = COMMANDS[args.command]
    arguments: dict[str, object] = {}
    if hasattr(args, "path"):
        arguments["path"] = args.path
    if hasattr(args, "profile"):
        arguments["profile"] = args.profile
    if hasattr(args, "item_id"):
        arguments["item_id"] = args.item_id
    result = dispatch(operation, arguments)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("completeness", {}).get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
