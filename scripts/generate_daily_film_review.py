#!/usr/bin/env python3
"""Build the five compact Film Review rows before players open the hub."""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.server import (  # noqa: E402
    CENTRAL_TIME,
    _daily_film_puzzles_ready,
    _ensure_daily_film_puzzles,
    db,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, help="Central calendar date, YYYY-MM-DD")
    args = parser.parse_args()
    puzzle_day = args.date or datetime.now(CENTRAL_TIME).date()

    with db() as conn:
        generated = _ensure_daily_film_puzzles(conn, puzzle_day)
        ready = _daily_film_puzzles_ready(conn, puzzle_day)
    print(f"film review date: {puzzle_day.isoformat()}")
    print(f"generated: {generated}")
    if not ready:
        print("ERROR: one or more daily Film Review puzzles were not built.", file=sys.stderr)
        return 1
    print("film review cache: ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
