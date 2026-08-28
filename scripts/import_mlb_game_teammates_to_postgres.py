#!/usr/bin/env python3
"""Import strict MLB same-game teammate proofs into production Postgres."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

try:
    import psycopg
except ImportError:
    print("ERROR: install psycopg first: pip install 'psycopg[binary]'", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "raw" / "mlb_game_teammates" / "mlb_game_teammates.sqlite"
SOURCE_NAME = "mlb_statsapi_game_boxscore"


def ensure_tables(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mlb_teammate_game_proofs (
                player_a_id TEXT NOT NULL REFERENCES players(player_id),
                player_b_id TEXT NOT NULL REFERENCES players(player_id),
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL,
                shared_games INTEGER NOT NULL,
                first_game_pk INTEGER NOT NULL,
                first_game_date DATE NOT NULL,
                source TEXT,
                PRIMARY KEY (player_a_id, player_b_id, team_id, season),
                CHECK (player_a_id < player_b_id),
                FOREIGN KEY (team_id, season) REFERENCES teams(team_id, season)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_mlb_tgp_pair "
            "ON mlb_teammate_game_proofs(player_a_id, player_b_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_mlb_tgp_b_a "
            "ON mlb_teammate_game_proofs(player_b_id, player_a_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_mlb_tgp_team_season "
            "ON mlb_teammate_game_proofs(team_id, season)"
        )


def source_summary(src: sqlite3.Connection, season_start: int, season_end: int) -> tuple[int, int, int]:
    games = src.execute(
        "SELECT COUNT(*) FROM mlb_games WHERE season BETWEEN ? AND ?",
        (season_start, season_end),
    ).fetchone()[0]
    appearances = src.execute(
        "SELECT COUNT(*) FROM mlb_player_game_appearances WHERE season BETWEEN ? AND ?",
        (season_start, season_end),
    ).fetchone()[0]
    proofs = src.execute(
        "SELECT COUNT(*) FROM mlb_teammate_game_proofs WHERE season BETWEEN ? AND ?",
        (season_start, season_end),
    ).fetchone()[0]
    return int(games), int(appearances), int(proofs)


def create_temp_table(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS tmp_mlb_game_teammates")
        cur.execute(
            """
            CREATE TEMP TABLE tmp_mlb_game_teammates (
                player_a_id TEXT NOT NULL,
                player_b_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL,
                shared_games INTEGER NOT NULL,
                first_game_pk INTEGER NOT NULL,
                first_game_date DATE NOT NULL,
                source TEXT NOT NULL
            ) ON COMMIT DROP
            """
        )


def copy_sqlite_proofs(
    src: sqlite3.Connection,
    dst: "psycopg.Connection",
    season_start: int,
    season_end: int,
    batch_size: int = 10000,
) -> int:
    cur = src.execute(
        """
        SELECT player_a_id, player_b_id, team_id, season, shared_games,
               first_game_pk, first_game_date, source
          FROM mlb_teammate_game_proofs
         WHERE season BETWEEN ? AND ?
         ORDER BY season, team_id, player_a_id, player_b_id
        """,
        (season_start, season_end),
    )
    total = 0
    with dst.cursor() as pg:
        with pg.copy(
            """
            COPY tmp_mlb_game_teammates
              (player_a_id, player_b_id, team_id, season, shared_games,
               first_game_pk, first_game_date, source)
            FROM STDIN
            """
        ) as copy:
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    copy.write_row(row)
                total += len(rows)
                if total == len(rows) or total % 100000 == 0:
                    print(f"  copied {total:,} historical proofs", flush=True)
    return total


def import_historical(dst: "psycopg.Connection", season_start: int, season_end: int) -> int:
    with dst.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        cur.execute(
            "DELETE FROM mlb_teammate_game_proofs WHERE season BETWEEN %s AND %s",
            (season_start, season_end),
        )
        cur.execute(
            """
            INSERT INTO mlb_teammate_game_proofs
                (player_a_id, player_b_id, team_id, season, shared_games,
                 first_game_pk, first_game_date, source)
            SELECT DISTINCT player_a_id, player_b_id, team_id, season, shared_games,
                   first_game_pk, first_game_date, source
              FROM tmp_mlb_game_teammates
            ON CONFLICT DO NOTHING
            """
        )
        inserted = int(cur.rowcount)
        cur.execute(
            """
            INSERT INTO teammate_stint_coverage (season, coverage_type, strict, source, updated_at)
            SELECT season, 'game_boxscore', 1, %s, now()
              FROM generate_series(%s::integer, %s::integer) AS season
            ON CONFLICT (season) DO UPDATE
            SET coverage_type = EXCLUDED.coverage_type,
                strict = EXCLUDED.strict,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            (SOURCE_NAME, season_start, season_end),
        )
    return inserted


def import_live_season(dst: "psycopg.Connection", season: int) -> int:
    with dst.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        cur.execute("DELETE FROM mlb_teammate_game_proofs WHERE season = %s", (season,))
        cur.execute(
            """
            INSERT INTO mlb_teammate_game_proofs
                (player_a_id, player_b_id, team_id, season, shared_games,
                 first_game_pk, first_game_date, source)
            WITH grouped AS (
                SELECT
                    a.player_id AS player_a_id,
                    b.player_id AS player_b_id,
                    a.team_id,
                    a.season,
                    COUNT(*)::integer AS shared_games,
                    MIN(to_char(a.game_date, 'YYYY-MM-DD') || '|' || a.game_pk::text) AS first_key
                  FROM mlb_live_player_games a
                  JOIN mlb_live_player_games b
                    ON b.game_pk = a.game_pk
                   AND b.team_id = a.team_id
                   AND b.player_id > a.player_id
                 WHERE a.season = %s
                 GROUP BY a.player_id, b.player_id, a.team_id, a.season
            )
            SELECT player_a_id, player_b_id, team_id, season, shared_games,
                   split_part(first_key, '|', 2)::integer AS first_game_pk,
                   split_part(first_key, '|', 1)::date AS first_game_date,
                   %s
              FROM grouped
            ON CONFLICT DO NOTHING
            """,
            (season, SOURCE_NAME),
        )
        inserted = int(cur.rowcount)
        cur.execute(
            """
            INSERT INTO teammate_stint_coverage (season, coverage_type, strict, source, updated_at)
            VALUES (%s, 'game_boxscore', 1, %s, now())
            ON CONFLICT (season) DO UPDATE
            SET coverage_type = EXCLUDED.coverage_type,
                strict = EXCLUDED.strict,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            (season, SOURCE_NAME),
        )
    return inserted


def refresh_teammate_counts(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        cur.execute(
            """
            WITH teammate_counts AS (
                SELECT player_id, COUNT(DISTINCT teammate_id)::integer AS teammate_count
                  FROM (
                        SELECT player_a_id AS player_id, player_b_id AS teammate_id
                          FROM mlb_teammate_game_proofs
                        UNION ALL
                        SELECT player_b_id AS player_id, player_a_id AS teammate_id
                          FROM mlb_teammate_game_proofs
                  ) links
                 GROUP BY player_id
            )
            UPDATE players_searchable ps
               SET teammate_count = COALESCE(tc.teammate_count, 0)
              FROM players p
              LEFT JOIN teammate_counts tc ON tc.player_id = p.player_id
             WHERE ps.player_id = p.player_id
            """
        )


def verify(conn: "psycopg.Connection") -> list[tuple[str, int]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 'proof_rows', COUNT(*) FROM mlb_teammate_game_proofs
            UNION ALL
            SELECT 'orphan_a', COUNT(*)
              FROM mlb_teammate_game_proofs proof
              LEFT JOIN players p ON p.player_id = proof.player_a_id
             WHERE p.player_id IS NULL
            UNION ALL
            SELECT 'orphan_b', COUNT(*)
              FROM mlb_teammate_game_proofs proof
              LEFT JOIN players p ON p.player_id = proof.player_b_id
             WHERE p.player_id IS NULL
            UNION ALL
            SELECT 'covered_seasons', COUNT(*)
              FROM teammate_stint_coverage
             WHERE coverage_type = 'game_boxscore' AND strict <> 0
            """
        )
        return [(str(label), int(count)) for label, count in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--season-start", type=int, default=2000)
    parser.add_argument("--season-end", type=int, default=2025)
    parser.add_argument("--include-live-season", type=int, default=2026)
    parser.add_argument("--skip-historical", action="store_true")
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL is required", file=sys.stderr)
        return 1
    safe_url = pg_url.split("@", 1)[-1] if "@" in pg_url else pg_url
    print(f"target: {safe_url}", flush=True)

    with psycopg.connect(pg_url, autocommit=False, prepare_threshold=None) as dst:
        ensure_tables(dst)
        historical_inserted = 0
        if not args.skip_historical:
            with sqlite3.connect(args.db) as src:
                games, appearances, proofs = source_summary(src, args.season_start, args.season_end)
                print(
                    f"source: {games:,} historical games, {appearances:,} appearances, "
                    f"{proofs:,} proofs",
                    flush=True,
                )
                create_temp_table(dst)
                copied = copy_sqlite_proofs(src, dst, args.season_start, args.season_end)
                if copied != proofs:
                    print(f"warning: copied {copied:,} proof rows but source summary had {proofs:,}")
                historical_inserted = import_historical(dst, args.season_start, args.season_end)
                dst.commit()
                print(f"historical import: {historical_inserted:,} proof rows", flush=True)
        live_inserted = 0
        if not args.skip_live and args.include_live_season:
            live_inserted = import_live_season(dst, args.include_live_season)
            dst.commit()
            print(f"live {args.include_live_season} import: {live_inserted:,} proof rows", flush=True)
        refresh_teammate_counts(dst)
        dst.commit()
        print("verification:", verify(dst), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
