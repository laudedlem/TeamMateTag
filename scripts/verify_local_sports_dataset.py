"""Validate the locally built TeamMateTag multi-sport SQLite dataset.

Run after scripts/build_local_sports_dataset.py. This only reads the ignored
local database and never connects to Supabase.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
EXPECTED = {
    "baseball": (1871, 2025),
    "football": (1966, 2025),
    "basketball": (2002, 2025),
    "hockey": (1917, 2025),
}
KNOWN_TEAMMATES = {
    "baseball": ("Anthony Rizzo", "David Ross"),
    "football": ("Aaron Rodgers", "Davante Adams"),
    "basketball": ("LeBron James", "Dwyane Wade"),
    "hockey": ("Sidney Crosby", "Evgeni Malkin"),
}


def player_id(conn: sqlite3.Connection, sport: str, name: str) -> str:
    row = conn.execute(
        "SELECT player_id FROM sport_players WHERE sport_id = ? AND display_name = ?",
        (sport, name),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"{sport}: could not resolve {name}")
    return row[0]


def shared_teams(conn: sqlite3.Connection, sport: str, first: str, second: str) -> list[tuple[str, int]]:
    return conn.execute(
        """
        SELECT a.team_id, a.season
        FROM sport_appearances AS a
        JOIN sport_appearances AS b
          ON b.sport_id = a.sport_id
         AND b.team_id = a.team_id
         AND b.season = a.season
        WHERE a.sport_id = ? AND a.player_id = ? AND b.player_id = ?
        ORDER BY a.season
        """,
        (sport, first, second),
    ).fetchall()


def main() -> None:
    if not DATABASE.exists():
        raise SystemExit(f"Local dataset not found: {DATABASE}")
    conn = sqlite3.connect(DATABASE)
    try:
        for sport, (first_year, last_year) in EXPECTED.items():
            count, actual_first, actual_last = conn.execute(
                "SELECT COUNT(*), MIN(season), MAX(season) FROM sport_appearances WHERE sport_id = ?",
                (sport,),
            ).fetchone()
            if not count or actual_first > first_year or actual_last < last_year:
                raise RuntimeError(
                    f"{sport}: incomplete scope, found {count:,} appearances from {actual_first}-{actual_last}"
                )
            first, second = KNOWN_TEAMMATES[sport]
            connections = shared_teams(conn, sport, player_id(conn, sport, first), player_id(conn, sport, second))
            if not connections:
                raise RuntimeError(f"{sport}: {first} and {second} do not have a shared team-season")
            print(f"{sport}: {count:,} appearances, {actual_first}-{actual_last}; "
                  f"{first} / {second}: {len(connections)} shared team-season(s)")
    finally:
        conn.close()
    print("Local multi-sport dataset validation passed.")


if __name__ == "__main__":
    main()
