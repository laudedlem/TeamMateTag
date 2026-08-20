"""Rebuild stored Film Review puzzles for selected sports/date range.

This is used when Film Review rules or generator quality change enough that
old archived tapes should no longer be considered canonical.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
load_dotenv(ROOT / ".env")

import server  # noqa: E402


DEFAULT_SPORTS = ("baseball", "basketball", "hockey")


def parse_day(value: str | None) -> date:
    if not value:
        return datetime.now(server.CENTRAL_TIME).date()
    return date.fromisoformat(value)


def day_range(start: date, end: date):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def reset_existing(conn, sports: list[str], start: date, end: date) -> None:
    game_ids = [
        row[0] for row in conn.execute(
            """SELECT game_id FROM film_review_daily_attempts
                WHERE sport_id = ANY(%s) AND puzzle_date BETWEEN %s AND %s""",
            (sports, start, end),
        ).fetchall()
    ]
    conn.execute(
        "DELETE FROM film_review_daily_attempts WHERE sport_id = ANY(%s) AND puzzle_date BETWEEN %s AND %s",
        (sports, start, end),
    )
    conn.execute(
        "DELETE FROM film_review_daily_puzzles WHERE sport_id = ANY(%s) AND puzzle_date BETWEEN %s AND %s",
        (sports, start, end),
    )
    for sport in sports:
        prefixes = [f"{sport}_{day.isoformat()}%" for day in day_range(start, end)]
        if prefixes:
            conn.execute(
                "DELETE FROM fr_results WHERE sport_id=%s AND puzzle_id LIKE ANY(%s)",
                (sport, prefixes),
            )
    if game_ids:
        conn.execute("DELETE FROM fr_games WHERE game_id = ANY(%s)", (game_ids,))


def build_puzzle(conn, sport: str, puzzle_day: date, unit: str | None) -> dict:
    puzzle = server._build_film_review_puzzle_with_history(conn, sport, puzzle_day, unit)
    if sport == "baseball":
        shared = server._fr_compute_shared(conn, list(puzzle["deck"]))
    else:
        deck = list(puzzle["deck"])
        shared = [server._sport_fr_shared(conn, sport, deck[index], deck[index + 1])
                  for index in range(len(deck) - 1)]
    if any(not pair for pair in shared):
        raise RuntimeError(f"{sport} {puzzle_day} {unit or ''} has unresolved Film Review pair")
    server._store_daily_film_review_puzzle(conn, sport, puzzle_day, unit, puzzle)
    return puzzle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=server.FILM_REVIEW_EPOCH.isoformat())
    parser.add_argument("--end", default="")
    parser.add_argument("--sport", action="append", choices=("baseball", "basketball", "hockey", "football"))
    parser.add_argument("--include-football", action="store_true")
    parser.add_argument("--no-reset", action="store_true")
    args = parser.parse_args()

    start = parse_day(args.start)
    end = parse_day(args.end)
    sports = args.sport or list(DEFAULT_SPORTS)
    if args.include_football and "football" not in sports:
        sports.append("football")

    server.ensure_runtime_schema()
    built = []
    with server.db() as conn:
        if not args.no_reset:
            reset_existing(conn, sports, start, end)
        for sport in sports:
            units = ("offense", "defense") if sport == "football" else (None,)
            for puzzle_day in day_range(start, end):
                for unit in units:
                    puzzle = build_puzzle(conn, sport, puzzle_day, unit)
                    built.append((sport, puzzle_day.isoformat(), unit or "", len(puzzle["deck"])))
                    print(f"built {sport:10} {puzzle_day.isoformat()} {unit or 'default':8} {len(puzzle['deck'])} cards", flush=True)
    print(f"Rebuilt {len(built)} Film Review puzzle rows.")


if __name__ == "__main__":
    main()
