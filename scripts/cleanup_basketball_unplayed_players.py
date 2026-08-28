#!/usr/bin/env python3
"""Remove Basketball player shells with no appearance or strict proof data."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


SPORT_ID = "basketball"


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required in .env.", file=sys.stderr)
        return 1

    with psycopg.connect(database_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TEMP TABLE tmp_basketball_unplayed AS
                SELECT p.player_id
                  FROM sport_players p
                 WHERE p.sport_id = %s
                   AND NOT EXISTS (
                        SELECT 1 FROM sport_appearances a
                         WHERE a.sport_id = p.sport_id
                           AND a.player_id = p.player_id
                   )
                   AND NOT EXISTS (
                        SELECT 1 FROM sport_teammates t
                         WHERE t.sport_id = p.sport_id
                           AND (t.player_a_id = p.player_id OR t.player_b_id = p.player_id)
                   )
                """,
                (SPORT_ID,),
            )
            cur.execute("SELECT COUNT(*) FROM tmp_basketball_unplayed")
            total = int(cur.fetchone()[0])
            print(f"Basketball unplayed player shells: {total:,}")
            if total == 0:
                conn.rollback()
                return 0

            for table in (
                "sport_player_aliases",
                "sport_player_images",
                "sport_player_positions",
                "sport_player_season_traits",
                "sport_player_traits",
                "sport_players_searchable",
                "player_headshot_source_attempts",
                "player_headshots",
            ):
                cur.execute(
                    f"""
                    DELETE FROM {table}
                     WHERE sport_id = %s
                       AND player_id IN (SELECT player_id FROM tmp_basketball_unplayed)
                    """,
                    (SPORT_ID,),
                )
                print(f"Deleted {cur.rowcount:,} rows from {table}.")

            cur.execute(
                """
                DELETE FROM sport_players
                 WHERE sport_id = %s
                   AND player_id IN (SELECT player_id FROM tmp_basketball_unplayed)
                """,
                (SPORT_ID,),
            )
            print(f"Deleted {cur.rowcount:,} rows from sport_players.")
        conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
