#!/usr/bin/env python3
"""Refresh local canonical headshots and deploy-ready storage artifacts.

Run this after live-season data has introduced new players. It keeps the large
image validation work local, preserves exactly one canonical image per verified
player, rebuilds the compact runtime headshot rows, and regenerates the local
file-storage mirror that can later replace Supabase Storage contents.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


SYNC_COMMANDS = {
    "baseball": [["sync_baseball_headshots.py"]],
    "basketball": [["sync_basketball_headshots.py"]],
    "hockey": [["sync_hockey_headshots.py", "--replace"]],
    "football": [["sync_football_headshots.py"], ["sync_football_headshots.py", "--replace-current-staging"]],
}


def run_step(args: list[str]) -> None:
    command = [sys.executable, str(ROOT / "scripts" / args[0]), *args[1:]]
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sports",
        nargs="+",
        choices=sorted(SYNC_COMMANDS),
        default=sorted(SYNC_COMMANDS),
        help="Sports to refresh. Defaults to all sports.",
    )
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--skip-audit", action="store_true")
    args = parser.parse_args()

    for sport in args.sports:
        print(f"\n== {sport} canonical headshots ==", flush=True)
        for base in SYNC_COMMANDS[sport]:
            command = [base[0], "--workers", str(args.workers), *base[1:]] if "--replace-current-staging" not in base else base
            run_step(command)

    print("\n== compact runtime ==", flush=True)
    run_step(["build_minimal_runtime_sqlite.py"])

    print("\n== file-storage mirror ==", flush=True)
    run_step(["build_file_storage_artifacts.py", "--sports", *args.sports])

    if not args.skip_audit:
        print("\n== hygiene audit ==", flush=True)
        run_step(["audit_runtime_data_hygiene.py"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
