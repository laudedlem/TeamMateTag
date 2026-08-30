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
    OR lower(replace(name, '-', ' ')) LIKE '%young star%'
    OR lower(replace(name, '-', ' ')) LIKE '%rookie challenge%'
    OR lower(name) IN ('world', 'usa')
    OR lower(name) IN ('ogs', 'stripes')
    OR lower(name) LIKE 'team %'
"""


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def main() -> None:
    with sqlite3.connect(DATABASE) as conn:
        has_sport_teammates = table_exists(conn, "sport_teammates")
        affected_players = [
            row[0]
            for row in conn.execute(
                f"""SELECT DISTINCT player_id FROM sport_appearances
                     WHERE sport_id='basketball' AND (team_id, season) IN (
                           SELECT team_id, season FROM sport_teams
                            WHERE sport_id='basketball' AND ({EXHIBITION_SQL})
                     )"""
            ).fetchall()
        ]
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
                "DELETE FROM sport_player_stints WHERE sport_id='basketball' AND team_id=? AND season=?",
                (team_id, season),
            )
            if has_sport_teammates:
                conn.execute(
                    "DELETE FROM sport_teammates WHERE sport_id='basketball' AND team_id=? AND season=?",
                    (team_id, season),
                )
        conn.execute(
            f"DELETE FROM sport_teams WHERE sport_id='basketball' AND ({EXHIBITION_SQL})",
        )
        if affected_players:
            placeholders = ",".join("?" for _ in affected_players)
            conn.execute(
                f"""DELETE FROM sport_players_searchable
                     WHERE sport_id='basketball'
                       AND player_id IN ({placeholders})
                       AND NOT EXISTS (
                           SELECT 1 FROM sport_appearances a
                            WHERE a.sport_id=sport_players_searchable.sport_id
                              AND a.player_id=sport_players_searchable.player_id
                       )""",
                affected_players,
            )
        conn.execute(
            """DELETE FROM sport_players_searchable
                 WHERE sport_id='basketball'
                   AND NOT EXISTS (
                       SELECT 1 FROM sport_appearances a
                        WHERE a.sport_id=sport_players_searchable.sport_id
                          AND a.player_id=sport_players_searchable.player_id
                   )"""
        )
    print(f"Removed {len(team_rows)} NBA exhibition team-seasons.")


if __name__ == "__main__":
    main()
