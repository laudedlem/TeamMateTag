#!/usr/bin/env python3
"""Legacy direct NFL live updater.

Use update_nfl_compact_live.py for normal updates so snap rows stay local and
Supabase receives only compact runtime rows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_nfl_snap_teammates import main as build_snap_source  # noqa: E402
from import_nfl_snap_teammates_to_postgres import main as import_snap_source  # noqa: E402


RELEASE_API = "https://api.github.com/repos/nflverse/nflverse-data/releases/tags/snap_counts"
LIVE_SOURCE = ROOT / "raw" / "nfl_game_teammates" / "nfl_snap_teammates_live.sqlite"


def default_nfl_season(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month >= 8 else today.year - 1


def snap_asset_available(season: int) -> tuple[bool, int]:
    with urlopen(RELEASE_API, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    asset_name = f"snap_counts_{season}.csv"
    for asset in data.get("assets", []):
        if asset.get("name") == asset_name:
            size = int(asset.get("size") or 0)
            return size > 1000, size
    return False, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=default_nfl_season())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.dry_run and os.environ.get("TEAMMATETAG_ALLOW_LEGACY_NFL_LIVE_UPDATER") != "1":
        print(
            "ERROR: update_nfl_live_data.py is a legacy direct-to-Supabase updater. "
            "Use scripts/update_nfl_compact_live.py.",
            file=sys.stderr,
        )
        return 2

    available, size = snap_asset_available(args.season)
    if not available:
        print(f"nflverse snap_counts_{args.season}.csv is not available yet; size={size}; no-op")
        return 0

    print(f"updating NFL snap data for season {args.season}; asset size={size:,}")
    build_snap_source(
        [
            "--season-start",
            str(args.season),
            "--season-end",
            str(args.season),
            "--output",
            str(LIVE_SOURCE),
        ]
    )
    import_args = [
        "--source",
        str(LIVE_SOURCE),
        "--season-start",
        str(args.season),
        "--season-end",
        str(args.season),
    ]
    if args.dry_run:
        import_args.append("--dry-run")
    import_snap_source(import_args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
