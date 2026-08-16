"""Validate daily Film Review generation against local roster constraints."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game.film_review_generator import FOOTBALL_UNITS, LINEUP_SLOTS, generate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--start", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    conn = sqlite3.connect(ROOT / "db" / "teammatetag_local.sqlite")
    failures = []
    targets = [(sport, None) for sport in LINEUP_SLOTS if sport != "football"]
    targets.extend(("football", unit) for unit in FOOTBALL_UNITS)
    for sport, unit in targets:
        passed = 0
        for offset in range(args.days):
            puzzle_day = args.start + timedelta(days=offset)
            try:
                puzzle = generate(conn, sport, puzzle_day, unit=unit)
                if len(puzzle.deck) != len(puzzle.slots):
                    raise AssertionError("wrong card count")
                if len(set(puzzle.deck)) != len(puzzle.deck):
                    raise AssertionError("duplicate player")
                if len(set(puzzle.links)) != len(puzzle.links):
                    raise AssertionError("repeated team-season link")
                if len(puzzle.links) != len(puzzle.deck) - 1:
                    raise AssertionError("wrong link count")
                passed += 1
            except (AssertionError, RuntimeError, ValueError) as error:
                label = f"{sport}:{unit}" if unit else sport
                failures.append(f"{label} {puzzle_day}: {error}")
        label = f"{sport}:{unit}" if unit else sport
        print(f"{label}: {passed}/{args.days} generated")
    if failures:
        print("Failures:")
        print("\n".join(failures))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
