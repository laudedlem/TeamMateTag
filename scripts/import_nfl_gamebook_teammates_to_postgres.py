#!/usr/bin/env python3
"""Import cleaned 2000-2012 NFL Game Book participation proofs into Supabase."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from import_nfl_snap_teammates_to_postgres import main as import_football_source  # noqa: E402


SOURCE = ROOT / "raw" / "nfl_game_teammates" / "nfl_gamebook_teammates.sqlite"
SOURCE_NAME = "official_nfl_gamebook_participation"
SOURCE_URL = "https://www.nfl.com/games/"


def main(argv: list[str] | None = None) -> int:
    args = [
        "--source",
        str(SOURCE),
        "--season-start",
        "2000",
        "--season-end",
        "2012",
        "--source-name",
        SOURCE_NAME,
        "--source-url",
        SOURCE_URL,
    ]
    if argv:
        args.extend(argv)
    return import_football_source(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
