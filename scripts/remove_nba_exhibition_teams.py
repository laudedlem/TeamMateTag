#!/usr/bin/env python3
"""Remove non-franchise NBA exhibition teams from the local source catalog."""
from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
EXHIBITION_SQL = """
    lower(replace(name, '-', ' ')) LIKE '%all star%'
    OR lower(replace(name, '-', ' ')) LIKE '%rising star%'
    OR lower(name) = 'world'
    OR lower(name) IN ('ogs', 'stripes')
    OR lower(name) LIKE 'team %'
"""


def main() -> None:
    with sqlite3.connect(DATABASE) as conn:
        team_rows = conn.execute(
            f"""SELECT team_id, season FROM sport_teams
                 WHERE sport_id='basketball' AND ({EXHIBITION_SQL})"""
        ).fetchall()
        for team_id, season in team_rows:
            conn.execute(
                "DELETE FROM sport_appearances WHERE sport_id='basketball' AND team_id=? AND season=?",
                (team_id, season),
            )
        conn.execute(
            f"DELETE FROM sport_teams WHERE sport_id='basketball' AND ({EXHIBITION_SQL})",
        )
    print(f"Removed {len(team_rows)} NBA exhibition team-seasons.")


if __name__ == "__main__":
    main()
