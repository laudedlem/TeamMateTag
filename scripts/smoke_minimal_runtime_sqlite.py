#!/usr/bin/env python3
"""Smoke-test the offline minimal runtime database."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "game"))
sys.path.insert(0, str(ROOT / "scripts"))

from engine import get_shared_seasons, seed_game, validate_and_apply_move  # noqa: E402
from film_review_generator import generate  # noqa: E402
import web.server as server  # noqa: E402


class SqlitePercentWrapper:
    """Let Postgres-style helper SQL run against the local SQLite artifact."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def execute(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql.replace("%s", "?"), params or ())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if not args.db.exists():
        raise SystemExit(f"missing compact runtime DB: {args.db}")

    conn = sqlite3.connect(args.db)
    try:
        link_checks = [
            ("baseball", "troutmi01", "ohtansh01", None),
            ("basketball", "nba:2544", "nba:2548", "basketball"),
            ("hockey", "nhl:8474141", "nhl:8473604", "hockey"),
            ("football", "nfl:00-0033873", "nfl:00-0030506", "football"),
        ]
        for label, first, second, sport in link_checks:
            rows = get_shared_seasons(conn, first, second, sport=sport)
            print(f"link {label}: {len(rows):,} {rows[:3]}")
            if not rows:
                raise SystemExit(f"missing known link for {label}")

        move_checks = [
            ("baseball", "troutmi01", "Shohei Ohtani"),
            ("basketball", "nba:2544", "Dwyane Wade"),
            ("hockey", "nhl:8474141", "Jonathan Toews"),
            ("football", "nfl:00-0033873", "Travis Kelce"),
        ]
        for sport, seed, guess in move_checks:
            engine_sport = None if sport == "baseball" else sport
            state = seed_game(conn, seed, sport=engine_sport)
            result = validate_and_apply_move(state, conn, guess, sport=engine_sport)
            print(f"move {sport}: {result.outcome.value} {result.shared_seasons[:2]}")
            if result.outcome.value != "valid":
                raise SystemExit(f"bad move validation for {sport}")

        baseball_puzzle = server.generate_baseball_film_review(
            SqlitePercentWrapper(conn),
            server._film_review_day(),
        )
        print(f"film baseball: {len(baseball_puzzle['deck'])} cards")
        if len(baseball_puzzle["deck"]) != 12:
            raise SystemExit("bad Baseball Film Review deck")

        for sport, unit in [
            ("basketball", None),
            ("hockey", None),
            ("football", "offense"),
            ("football", "defense"),
        ]:
            puzzle = generate(conn, sport, unit=unit)
            print(f"film {sport} {unit or 'full'}: {len(puzzle.deck)} cards")
            if len(puzzle.links) != len(puzzle.deck) - 1:
                raise SystemExit(f"bad Film Review deck for {sport} {unit or 'full'}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
