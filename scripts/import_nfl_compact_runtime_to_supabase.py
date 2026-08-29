#!/usr/bin/env python3
"""Import compact local Football runtime data into Supabase.

This loader uses raw/nfl_game_teammates/nfl_compact_runtime_int.sqlite, which
already contains refined catalog rows and integer-key same-game proof rows.
It does not upload raw boxscore/snap/player-game data.
"""
from __future__ import annotations

import argparse
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
DEFAULT_DB = ROOT / "raw" / "nfl_game_teammates" / "nfl_compact_runtime_int.sqlite"
SPORT_ID = "football"

CATALOG_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sport_franchises", ("sport_id", "franchise_id", "name", "active")),
    ("sport_teams", ("sport_id", "team_id", "season", "franchise_id", "name")),
    (
        "sport_players",
        (
            "sport_id",
            "player_id",
            "external_id",
            "display_name",
            "first_name",
            "last_name",
            "birth_year",
            "debut_year",
            "final_year",
            "primary_pos",
        ),
    ),
    ("sport_appearances", ("sport_id", "player_id", "team_id", "season", "games_total")),
    (
        "sport_player_stints",
        (
            "sport_id",
            "player_id",
            "team_id",
            "season",
            "first_unit",
            "last_unit",
            "first_label",
            "last_label",
            "source",
        ),
    ),
    ("sport_player_positions", ("sport_id", "player_id", "position", "games")),
    (
        "sport_teammate_stint_coverage",
        ("sport_id", "season", "coverage_type", "strict", "source"),
    ),
    (
        "sport_players_searchable",
        (
            "sport_id",
            "player_id",
            "display_name",
            "disambiguation",
            "search_key",
            "last_key",
            "career_games",
            "teammate_count",
        ),
    ),
    ("sport_player_images", ("sport_id", "player_id", "source_url", "content_type")),
)


def database_url() -> str:
    return os.environ.get("DIRECT_URL") or os.environ.get("DATABASE_URL") or ""


def source_count(src: sqlite3.Connection, table: str) -> int:
    return int(src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def pg_scalar(cur: "psycopg.Cursor", sql: str, params: tuple = ()) -> int:
    return int(cur.execute(sql, params).fetchone()[0])


def db_size(cur: "psycopg.Cursor") -> str:
    return cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]


def ensure_compact_schema(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT relkind
              FROM pg_class
             WHERE relnamespace = 'public'::regnamespace
               AND relname = 'sport_teammates'
            """
        )
        row = cur.fetchone()
        if row is None or row[0] != "v":
            raise RuntimeError("sport_teammates must be the compact compatibility view before importing Football")
        cur.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_name IN ('compact_player_keys', 'compact_team_keys', 'compact_sport_teammates')
             GROUP BY table_schema
            HAVING COUNT(*) = 3
            """
        )
        if cur.fetchone() is None:
            raise RuntimeError("compact proof tables are missing")


def clear_football(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM compact_sport_teammates WHERE sport_id = %s", (SPORT_ID,))
        cur.execute("DELETE FROM compact_player_keys WHERE scope = %s", (SPORT_ID,))
        cur.execute("DELETE FROM compact_team_keys WHERE scope = %s", (SPORT_ID,))
        for table in (
            "sport_player_images",
            "sport_players_searchable",
            "sport_player_positions",
            "sport_player_stints",
            "sport_teammate_stint_coverage",
            "sport_appearances",
            "sport_player_aliases",
            "sport_data_provenance",
            "sport_player_season_traits",
            "sport_player_traits",
        ):
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                 WHERE table_schema = 'public' AND table_name = %s
                """,
                (table,),
            )
            if cur.fetchone() is not None:
                cur.execute(f"DELETE FROM {table} WHERE sport_id = %s", (SPORT_ID,))
        cur.execute("DELETE FROM sport_players WHERE sport_id = %s", (SPORT_ID,))
        cur.execute("DELETE FROM sport_teams WHERE sport_id = %s", (SPORT_ID,))
        cur.execute("DELETE FROM sport_franchises WHERE sport_id = %s", (SPORT_ID,))


def copy_catalog_table(src: sqlite3.Connection, dst: "psycopg.Connection", table: str, columns: tuple[str, ...]) -> int:
    rows = src.execute(
        f"SELECT {', '.join(columns)} FROM {table} ORDER BY {', '.join(columns[: min(3, len(columns))])}"
    )
    quoted = ", ".join(f'"{column}"' for column in columns)
    count = 0
    with dst.cursor() as cur:
        with cur.copy(f"COPY {table} ({quoted}) FROM STDIN") as copy:
            while True:
                batch = rows.fetchmany(5000)
                if not batch:
                    break
                for row in batch:
                    copy.write_row(tuple(row))
                count += len(batch)
    return count


def copy_catalog(src: sqlite3.Connection, dst: "psycopg.Connection") -> dict[str, int]:
    counts: dict[str, int] = {}
    with dst.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sports (sport_id, display_name, league_name, active, first_season, last_season)
            VALUES (%s, 'Football', 'NFL', true, 2000, 2025)
            ON CONFLICT (sport_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                league_name = EXCLUDED.league_name,
                active = true,
                first_season = EXCLUDED.first_season,
                last_season = EXCLUDED.last_season
            """,
            (SPORT_ID,),
        )
    for table, columns in CATALOG_TABLES:
        started = time.monotonic()
        counts[table] = copy_catalog_table(src, dst, table, columns)
        print(f"{table:34} {counts[table]:>9,} rows  {time.monotonic() - started:5.1f}s", flush=True)
    return counts


def load_key_maps(src: sqlite3.Connection, dst: "psycopg.Connection") -> tuple[dict[int, int], dict[int, int]]:
    player_ids = src.execute(
        "SELECT player_key, player_id FROM compact_player_keys ORDER BY player_key"
    ).fetchall()
    team_ids = src.execute(
        "SELECT team_key, team_id, season FROM compact_team_keys ORDER BY team_key"
    ).fetchall()
    with dst.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO compact_player_keys (scope, player_id)
            VALUES (%s, %s)
            ON CONFLICT (scope, player_id) DO NOTHING
            """,
            [(SPORT_ID, player_id) for _local_key, player_id in player_ids],
        )
        cur.executemany(
            """
            INSERT INTO compact_team_keys (scope, team_id, season)
            VALUES (%s, %s, %s)
            ON CONFLICT (scope, team_id, season) DO NOTHING
            """,
            [(SPORT_ID, team_id, season) for _local_key, team_id, season in team_ids],
        )
        cur.execute(
            "SELECT player_key, player_id FROM compact_player_keys WHERE scope = %s",
            (SPORT_ID,),
        )
        remote_players = {player_id: int(player_key) for player_key, player_id in cur.fetchall()}
        cur.execute(
            "SELECT team_key, team_id, season FROM compact_team_keys WHERE scope = %s",
            (SPORT_ID,),
        )
        remote_teams = {(team_id, int(season)): int(team_key) for team_key, team_id, season in cur.fetchall()}

    player_map = {int(local_key): remote_players[player_id] for local_key, player_id in player_ids}
    team_map = {int(local_key): remote_teams[(team_id, int(season))] for local_key, team_id, season in team_ids}
    return player_map, team_map


def copy_proofs(
    src: sqlite3.Connection,
    dst: "psycopg.Connection",
    player_map: dict[int, int],
    team_map: dict[int, int],
) -> int:
    rows = src.execute(
        """
        SELECT player_a_key, player_b_key, team_key, season
          FROM compact_sport_teammates
         ORDER BY season, team_key, player_a_key, player_b_key
        """
    )
    count = 0
    with dst.cursor() as cur:
        with cur.copy(
            """
            COPY compact_sport_teammates
                (sport_id, player_a_key, player_b_key, team_key, season)
            FROM STDIN
            """
        ) as copy:
            while True:
                batch = rows.fetchmany(50000)
                if not batch:
                    break
                for a_key, b_key, team_key, season in batch:
                    copy.write_row(
                        (
                            SPORT_ID,
                            player_map[int(a_key)],
                            player_map[int(b_key)],
                            team_map[int(team_key)],
                            int(season),
                        )
                    )
                count += len(batch)
                if count == len(batch) or count % 250000 == 0:
                    print(f"compact_sport_teammates         {count:>9,} rows copied", flush=True)
    return count


def analyze(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        for table in (
            "compact_player_keys",
            "compact_team_keys",
            "compact_sport_teammates",
            "sport_players",
            "sport_teams",
            "sport_appearances",
            "sport_player_stints",
            "sport_players_searchable",
        ):
            cur.execute(f"ANALYZE {table}")


def verify(conn: "psycopg.Connection") -> dict[str, int]:
    checks: dict[str, int] = {}
    with conn.cursor() as cur:
        queries = {
            "football_view_rows": "SELECT COUNT(*) FROM sport_teammates WHERE sport_id = 'football'",
            "football_compact_rows": "SELECT COUNT(*) FROM compact_sport_teammates WHERE sport_id = 'football'",
            "football_players": "SELECT COUNT(*) FROM sport_players WHERE sport_id = 'football'",
            "football_appearances": "SELECT COUNT(*) FROM sport_appearances WHERE sport_id = 'football'",
            "football_coverage": "SELECT COUNT(*) FROM sport_teammate_stint_coverage WHERE sport_id = 'football' AND coverage_type = 'game_boxscore'",
            "missing_player_a": """
                SELECT COUNT(*)
                  FROM sport_teammates t
                  LEFT JOIN sport_players p
                    ON p.sport_id = t.sport_id AND p.player_id = t.player_a_id
                 WHERE t.sport_id = 'football' AND p.player_id IS NULL
            """,
            "missing_player_b": """
                SELECT COUNT(*)
                  FROM sport_teammates t
                  LEFT JOIN sport_players p
                    ON p.sport_id = t.sport_id AND p.player_id = t.player_b_id
                 WHERE t.sport_id = 'football' AND p.player_id IS NULL
            """,
            "missing_team": """
                SELECT COUNT(*)
                  FROM sport_teammates t
                  LEFT JOIN sport_teams team
                    ON team.sport_id = t.sport_id
                   AND team.team_id = t.team_id
                   AND team.season = t.season
                 WHERE t.sport_id = 'football' AND team.team_id IS NULL
            """,
        }
        for label, sql in queries.items():
            checks[label] = pg_scalar(cur, sql)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"missing local compact Football DB: {args.db}", file=sys.stderr)
        return 1
    url = database_url()
    if not url:
        print("DIRECT_URL or DATABASE_URL is required in .env", file=sys.stderr)
        return 1

    src = sqlite3.connect(args.db)
    try:
        expected = {
            "sport_players": source_count(src, "sport_players"),
            "sport_appearances": source_count(src, "sport_appearances"),
            "sport_teammates": source_count(src, "compact_sport_teammates"),
        }
        print(f"source: {args.db}", flush=True)
        print(f"source size: {args.db.stat().st_size / 1024 / 1024:.1f} MB", flush=True)
        print(f"source counts: {expected}", flush=True)
        if not args.execute:
            print("dry run only. Re-run with --execute to import Football.", flush=True)
            return 0

        with psycopg.connect(url, autocommit=False, prepare_threshold=None) as dst:
            ensure_compact_schema(dst)
            with dst.cursor() as cur:
                print(f"database size before: {db_size(cur)}", flush=True)
            clear_football(dst)
            copy_catalog(src, dst)
            player_map, team_map = load_key_maps(src, dst)
            proofs = copy_proofs(src, dst, player_map, team_map)
            if proofs != expected["sport_teammates"]:
                raise RuntimeError(f"proof copy mismatch: {proofs:,} vs {expected['sport_teammates']:,}")
            checks = verify(dst)
            if checks["missing_player_a"] or checks["missing_player_b"] or checks["missing_team"]:
                raise RuntimeError(f"verification failed: {checks}")
            dst.commit()
            print(f"verification before vacuum: {checks}", flush=True)

        with psycopg.connect(url, autocommit=True, prepare_threshold=None) as dst:
            analyze(dst)
            checks = verify(dst)
            with dst.cursor() as cur:
                print(f"verification after vacuum: {checks}", flush=True)
                print(f"database size after: {db_size(cur)}", flush=True)
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
