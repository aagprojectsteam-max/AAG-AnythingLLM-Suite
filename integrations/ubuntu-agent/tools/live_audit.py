#!/usr/bin/env python3

import argparse
import json
import os
import platform
import subprocess
import time
from pathlib import Path

ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")

# IMPORTANT:
# Only predefined commands can be executed.
# No arbitrary shell command supplied by the AI is accepted.

CHECKS = {
    "overview": [
        ["/usr/bin/uname", "-a"],
        ["/usr/bin/uptime"],
        ["/usr/bin/free", "-h"],
        ["/usr/bin/df", "-hT", "/"],
        ["/usr/bin/df", "-hT", "/mnt/data"],
    ],

    "storage": [
        ["/usr/bin/findmnt", "/"],
        ["/usr/bin/findmnt", "/mnt/data"],
        ["/usr/bin/lsblk", "-o",
         "NAME,TYPE,SIZE,FSTYPE,FSVER,LABEL,UUID,MOUNTPOINTS,RO"],
        ["/usr/bin/df", "-hT"],
    ],

    "services": [
        ["/usr/bin/systemctl", "--failed", "--no-pager"],
        ["/usr/bin/systemctl", "is-active", "docker.service"],
        ["/usr/bin/systemctl", "is-active", "aag-otzar-storage.service"],
        ["/usr/bin/systemctl", "is-active", "aag-usbclone-kingston.service"],
        ["/usr/bin/systemctl", "is-active",
         "aag-usbclone-dummy-hcd-ensure.service"],
    ],

    "docker": [
        ["/usr/bin/docker", "ps", "-a",
         "--format",
         "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
    ],

    "network": [
        ["/usr/sbin/ip", "-brief", "address"],
        ["/usr/sbin/ip", "route"],
        ["/usr/bin/ss", "-lntup"],
    ],

    "otzar": [
        ["/usr/bin/systemctl", "status",
         "aag-otzar-storage.service",
         "--no-pager", "-l"],
        ["/usr/bin/systemctl", "status",
         "aag-usbclone-kingston.service",
         "--no-pager", "-l"],
        ["/usr/bin/lsblk", "-o",
         "NAME,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,RO"],
        ["/usr/bin/findmnt", "/mnt/data"],
    ],
}


def run_command(command):
    started = time.time()

    try:
        result = subprocess.run(
            command,
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"},
        )

        return {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout[-30000:],
            "stderr": result.stderr[-10000:],
            "duration_seconds": round(time.time() - started, 3),
        }

    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "returncode": 124,
            "stdout": "",
            "stderr": "TIMEOUT after 15 seconds",
            "duration_seconds": round(time.time() - started, 3),
        }

    except Exception as exc:
        return {
            "command": command,
            "returncode": 125,
            "stdout": "",
            "stderr": repr(exc),
            "duration_seconds": round(time.time() - started, 3),
        }


def main():
    parser = argparse.ArgumentParser(
        description="AAG Ubuntu Agent read-only live diagnostics"
    )

    parser.add_argument(
        "profile",
        choices=sorted(CHECKS),
        help="Predefined read-only diagnostic profile",
    )

    args = parser.parse_args()

    if not Path("/mnt/data").is_mount():
        raise SystemExit("ERROR: /mnt/data is not mounted")

    output = {
        "schema": "aag-live-audit-v1",
        "mode": "READ_ONLY",
        "profile": args.profile,
        "timestamp": time.strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        ),
        "hostname": platform.node(),
        "results": [],
    }

    for command in CHECKS[args.profile]:
        output["results"].append(run_command(command))

    print(json.dumps(
        output,
        ensure_ascii=False,
        indent=2
    ))


if __name__ == "__main__":
    main()
