#!/usr/bin/env python3
"""Restore continuous Arizona-to-Utah roster rows absent from the source feed."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"


def main() -> None:
    with sqlite3.connect(DATABASE) as conn:
        players = conn.execute("""
            SELECT DISTINCT a.player_id
              FROM sport_appearances a
              JOIN sport_appearances b
                ON b.sport_id=a.sport_id AND b.player_id=a.player_id
             WHERE a.sport_id='hockey' AND a.team_id='ARI' AND a.season<=2022
               AND b.team_id='UTA' AND b.season>=2025
               AND NOT EXISTS (
                   SELECT 1 FROM sport_appearances x
                    WHERE x.sport_id=a.sport_id AND x.player_id=a.player_id
                      AND x.season IN (2023, 2024)
               )
        """).fetchall()
        for (player_id,) in players:
            # Seasons are represented by their starting year. Arizona played
            # 2023-24; Utah began in 2024-25 under its temporary identity.
            conn.execute("""INSERT OR IGNORE INTO sport_appearances
                            (sport_id, player_id, team_id, season, games_total)
                            VALUES ('hockey', ?, 'ARI', 2023, 0)""", (player_id,))
            conn.execute("""INSERT OR IGNORE INTO sport_appearances
                            (sport_id, player_id, team_id, season, games_total)
                            VALUES ('hockey', ?, 'UTA', 2024, 0)""", (player_id,))
    print(f"Patched {len(players)} continuous Arizona-to-Utah players.")


if __name__ == '__main__':
    main()
