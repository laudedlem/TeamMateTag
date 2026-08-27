#!/usr/bin/env python3
"""Import strict NHL game-level teammate proofs into Supabase.

Source rows come from raw/nhl_game_teammates/nhl_game_teammates.sqlite, built
from official NHL regular-season boxscores. A Hockey teammate link is valid
only when both players had TOI greater than zero for the same NHL team in the
same regular-season game.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

import psycopg

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "raw" / "nhl_game_teammates" / "nhl_game_teammates.sqlite"
SCHEMA = ROOT / "db" / "cross_sport_schema_postgres.sql"
SPORT_ID = "hockey"
SOURCE_NAME = "nhl_api_web_gamecenter_boxscore"


def ensure_tables(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sport_teammates (
                sport_id TEXT NOT NULL REFERENCES sports(sport_id),
                player_a_id TEXT NOT NULL,
                player_b_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL,
                PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season),
                CHECK (player_a_id < player_b_id),
                FOREIGN KEY (sport_id, player_a_id)
                    REFERENCES sport_players(sport_id, player_id),
                FOREIGN KEY (sport_id, player_b_id)
                    REFERENCES sport_players(sport_id, player_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_teammates_pair "
            "ON sport_teammates(sport_id, player_a_id, player_b_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_teammates_a "
            "ON sport_teammates(sport_id, player_a_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_teammates_b "
            "ON sport_teammates(sport_id, player_b_id)"
        )


def source_summary(src: sqlite3.Connection) -> tuple[int, int, int, int, int]:
    games = src.execute("SELECT COUNT(*) FROM nhl_games").fetchone()[0]
    appearances = src.execute("SELECT COUNT(*) FROM nhl_player_game_appearances").fetchone()[0]
    proofs = src.execute("SELECT COUNT(*) FROM nhl_teammate_game_proofs").fetchone()[0]
    season_start, season_end = src.execute(
        "SELECT MIN(season), MAX(season) FROM nhl_games"
    ).fetchone()
    return int(games), int(appearances), int(proofs), int(season_start), int(season_end)


def copy_proofs(src: sqlite3.Connection, dst: "psycopg.Connection") -> int:
    rows = src.execute(
        """
        SELECT player_a_id, player_b_id, team_id, season
          FROM nhl_teammate_game_proofs
         ORDER BY season, team_id, player_a_id, player_b_id
        """
    )
    count = 0
    with dst.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE tmp_nhl_game_teammates (
                player_a_nhl_id TEXT NOT NULL,
                player_b_nhl_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL
            ) ON COMMIT DROP
            """
        )
        with cur.copy(
            "COPY tmp_nhl_game_teammates (player_a_nhl_id, player_b_nhl_id, team_id, season) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(tuple(row))
                count += 1
    return count


def import_proofs(dst: "psycopg.Connection", season_start: int, season_end: int) -> tuple[int, int]:
    with dst.cursor() as cur:
        cur.execute("DELETE FROM sport_teammates WHERE sport_id = %s", (SPORT_ID,))
        cur.execute(
            """
            INSERT INTO sport_teammates (sport_id, player_a_id, player_b_id, team_id, season)
            SELECT DISTINCT
                   %s AS sport_id,
                   LEAST(pa.player_id, pb.player_id) AS player_a_id,
                   GREATEST(pa.player_id, pb.player_id) AS player_b_id,
                   proof.team_id,
                   proof.season
              FROM tmp_nhl_game_teammates proof
              JOIN sport_players pa
                ON pa.sport_id = %s
               AND (pa.player_id = proof.player_a_nhl_id
                    OR pa.external_id = replace(proof.player_a_nhl_id, 'nhl:', ''))
              JOIN sport_players pb
                ON pb.sport_id = %s
               AND (pb.player_id = proof.player_b_nhl_id
                    OR pb.external_id = replace(proof.player_b_nhl_id, 'nhl:', ''))
             WHERE pa.player_id <> pb.player_id
            ON CONFLICT DO NOTHING
            """,
            (SPORT_ID, SPORT_ID, SPORT_ID),
        )
        inserted = cur.rowcount
        cur.execute(
            """
            INSERT INTO sport_teammate_stint_coverage
                (sport_id, season, coverage_type, strict, source, updated_at)
            SELECT %s, season, 'game_boxscore', 1, %s, now()
              FROM generate_series(%s, %s) AS season
            ON CONFLICT (sport_id, season) DO UPDATE
            SET coverage_type = EXCLUDED.coverage_type,
                strict = EXCLUDED.strict,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            (SPORT_ID, SOURCE_NAME, season_start, season_end),
        )
        cur.execute(
            """
            WITH teammate_counts AS (
                SELECT player_id, COUNT(DISTINCT teammate_id)::integer AS teammate_count
                  FROM (
                        SELECT player_a_id AS player_id, player_b_id AS teammate_id
                          FROM sport_teammates WHERE sport_id = %s
                        UNION ALL
                        SELECT player_b_id AS player_id, player_a_id AS teammate_id
                          FROM sport_teammates WHERE sport_id = %s
                  ) links
                 GROUP BY player_id
            )
            UPDATE sport_players_searchable ps
               SET teammate_count = COALESCE(tc.teammate_count, 0)
              FROM sport_players p
              LEFT JOIN teammate_counts tc ON tc.player_id = p.player_id
             WHERE ps.sport_id = %s
               AND p.sport_id = ps.sport_id
               AND p.player_id = ps.player_id
            """,
            (SPORT_ID, SPORT_ID, SPORT_ID),
        )
        cur.execute(
            """
            SELECT COUNT(*)
              FROM tmp_nhl_game_teammates proof
             WHERE NOT EXISTS (
                   SELECT 1 FROM sport_players pa
                    WHERE pa.sport_id = %s
                      AND (pa.player_id = proof.player_a_nhl_id
                           OR pa.external_id = replace(proof.player_a_nhl_id, 'nhl:', ''))
             )
                OR NOT EXISTS (
                   SELECT 1 FROM sport_players pb
                    WHERE pb.sport_id = %s
                      AND (pb.player_id = proof.player_b_nhl_id
                           OR pb.external_id = replace(proof.player_b_nhl_id, 'nhl:', ''))
             )
            """,
            (SPORT_ID, SPORT_ID),
        )
        unmapped = int(cur.fetchone()[0])
    return int(inserted), unmapped


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required in .env.", file=sys.stderr)
        return 1
    if not SOURCE.exists():
        print(f"Missing source database: {SOURCE}", file=sys.stderr)
        return 1

    src = sqlite3.connect(SOURCE)
    try:
        games, appearances, proofs, season_start, season_end = source_summary(src)
        print(
            f"Source: {games:,} games; {appearances:,} player-games; "
            f"{proofs:,} proof rows; seasons {season_start}-{season_end}"
        )
        started = time.monotonic()
        with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as dst:
            ensure_tables(dst)
            copied = copy_proofs(src, dst)
            inserted, unmapped = import_proofs(dst, season_start, season_end)
            dst.commit()
        print(f"Copied {copied:,} source proofs.")
        print(f"Imported {inserted:,} Hockey strict teammate rows.")
        print(f"Unmapped source proof rows: {unmapped:,}")
        print(f"Done in {time.monotonic() - started:.1f}s.")
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
