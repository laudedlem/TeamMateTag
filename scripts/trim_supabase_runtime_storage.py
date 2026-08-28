#!/usr/bin/env python3
"""Trim production Supabase down to runtime-only sports data.

Local SQLite/raw files are the archive of source game participation. Production
only needs compact proof graphs plus card/search rollups. This script removes
historical live player-game staging rows after verifying that the same
sport-season already has compact strict proof rows.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

try:
    import psycopg
except ImportError:
    raise SystemExit("ERROR: install psycopg first: pip install 'psycopg[binary]'")

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


CURRENT_MLB_SEASON = 2026


@dataclass(frozen=True)
class TableSize:
    table_name: str
    rows_estimate: int
    total_size: str
    table_size: str
    index_size: str


def connect() -> "psycopg.Connection":
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg.connect(url, prepare_threshold=None)


def table_exists(conn: "psycopg.Connection", table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
        return cur.fetchone()[0] is not None


def database_size(conn: "psycopg.Connection") -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        return str(cur.fetchone()[0])


def relation_sizes(conn: "psycopg.Connection") -> list[TableSize]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT relname,
                   COALESCE(n_live_tup, 0)::bigint,
                   pg_size_pretty(pg_total_relation_size(c.oid)),
                   pg_size_pretty(pg_relation_size(c.oid)),
                   pg_size_pretty(pg_indexes_size(c.oid))
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
         LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
             WHERE n.nspname = 'public'
               AND c.relkind = 'r'
               AND relname IN (
                   'sport_live_player_games',
                   'sport_live_game_imports',
                   'sport_teammates',
                   'sport_appearances',
                   'sport_player_stints',
                   'mlb_live_player_games',
                   'mlb_live_game_imports',
                   'mlb_teammate_game_proofs',
                   'appearances',
                   'player_stints'
               )
             ORDER BY pg_total_relation_size(c.oid) DESC
            """
        )
        return [TableSize(*row) for row in cur.fetchall()]


def print_sizes(conn: "psycopg.Connection", label: str) -> None:
    print(f"\n{label} database size: {database_size(conn)}", flush=True)
    for size in relation_sizes(conn):
        print(
            f"  {size.table_name}: rows~{size.rows_estimate:,}; "
            f"total {size.total_size}; table {size.table_size}; indexes {size.index_size}",
            flush=True,
        )


def sport_live_rows_ready_to_prune(conn: "psycopg.Connection", season_through: int) -> list[tuple[str, int, int]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT live.sport_id, live.season, COUNT(*)::bigint
              FROM sport_live_player_games live
             WHERE live.season <= %s
               AND EXISTS (
                   SELECT 1
                     FROM sport_teammate_stint_coverage c
                    WHERE c.sport_id = live.sport_id
                      AND c.season = live.season
                      AND c.strict <> 0
                      AND c.coverage_type = 'game_boxscore'
               )
               AND EXISTS (
                   SELECT 1
                     FROM sport_teammates t
                    WHERE t.sport_id = live.sport_id
                      AND t.season = live.season
               )
             GROUP BY live.sport_id, live.season
             ORDER BY live.sport_id, live.season
            """,
            (season_through,),
        )
        return [(str(sport), int(season), int(rows)) for sport, season, rows in cur.fetchall()]


def mlb_live_rows_ready_to_prune(conn: "psycopg.Connection", keep_mlb_season_from: int) -> list[tuple[int, int]]:
    if not table_exists(conn, "mlb_live_player_games"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT live.season, COUNT(*)::bigint
              FROM mlb_live_player_games live
             WHERE live.season < %s
               AND EXISTS (
                   SELECT 1
                     FROM teammate_stint_coverage c
                    WHERE c.season = live.season
                      AND c.strict <> 0
                      AND c.coverage_type = 'game_boxscore'
               )
               AND EXISTS (
                   SELECT 1
                     FROM mlb_teammate_game_proofs proof
                    WHERE proof.season = live.season
               )
             GROUP BY live.season
             ORDER BY live.season
            """,
            (keep_mlb_season_from,),
        )
        return [(int(season), int(rows)) for season, rows in cur.fetchall()]


def prune_sport_live_rows(conn: "psycopg.Connection", season_through: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        cur.execute(
            """
            DELETE FROM sport_live_player_games live
             WHERE live.season <= %s
               AND EXISTS (
                   SELECT 1
                     FROM sport_teammate_stint_coverage c
                    WHERE c.sport_id = live.sport_id
                      AND c.season = live.season
                      AND c.strict <> 0
                      AND c.coverage_type = 'game_boxscore'
               )
               AND EXISTS (
                   SELECT 1
                     FROM sport_teammates t
                    WHERE t.sport_id = live.sport_id
                      AND t.season = live.season
               )
            """,
            (season_through,),
        )
        removed_players = cur.rowcount
        cur.execute(
            """
            DELETE FROM sport_live_game_imports game
             WHERE game.season <= %s
               AND NOT EXISTS (
                   SELECT 1
                     FROM sport_live_player_games live
                    WHERE live.sport_id = game.sport_id
                      AND live.game_id = game.game_id
               )
            """,
            (season_through,),
        )
        removed_games = cur.rowcount
    print(
        f"removed {removed_players:,} non-baseball live player-game rows and "
        f"{removed_games:,} now-empty game-import rows",
        flush=True,
    )
    return int(removed_players)


def prune_mlb_live_rows(conn: "psycopg.Connection", keep_mlb_season_from: int) -> int:
    if not table_exists(conn, "mlb_live_player_games"):
        return 0
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        cur.execute(
            """
            DELETE FROM mlb_live_player_games live
             WHERE live.season < %s
               AND EXISTS (
                   SELECT 1
                     FROM teammate_stint_coverage c
                    WHERE c.season = live.season
                      AND c.strict <> 0
                      AND c.coverage_type = 'game_boxscore'
               )
               AND EXISTS (
                   SELECT 1
                     FROM mlb_teammate_game_proofs proof
                    WHERE proof.season = live.season
               )
            """,
            (keep_mlb_season_from,),
        )
        removed_players = cur.rowcount
        cur.execute(
            """
            DELETE FROM mlb_live_game_imports game
             WHERE game.season < %s
               AND NOT EXISTS (
                   SELECT 1
                     FROM mlb_live_player_games live
                    WHERE live.game_pk = game.game_pk
               )
            """,
            (keep_mlb_season_from,),
        )
        removed_games = cur.rowcount
    print(
        f"removed {removed_players:,} MLB live player-game rows and "
        f"{removed_games:,} now-empty game-import rows",
        flush=True,
    )
    return int(removed_players)


def vacuum(conn: "psycopg.Connection", *, full: bool = False) -> None:
    # VACUUM cannot run inside a transaction block.
    conn.commit()
    old_autocommit = conn.autocommit
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            mode = "VACUUM (FULL, ANALYZE)" if full else "VACUUM (ANALYZE)"
            for table in (
                "sport_live_player_games",
                "sport_live_game_imports",
                "mlb_live_player_games",
                "mlb_live_game_imports",
                "sport_teammates",
                "mlb_teammate_game_proofs",
            ):
                if table_exists(conn, table):
                    print(f"{mode.lower()} {table}", flush=True)
                    cur.execute(f"{mode} {table}")
    finally:
        conn.autocommit = old_autocommit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--season-through",
        type=int,
        default=2025,
        help="Prune non-baseball live rows through this season only when compact proofs exist.",
    )
    parser.add_argument(
        "--keep-mlb-season-from",
        type=int,
        default=CURRENT_MLB_SEASON,
        help="Keep MLB live staging rows for this season and later.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually delete rows.")
    parser.add_argument("--vacuum", action="store_true", help="Run VACUUM ANALYZE after deleting.")
    parser.add_argument(
        "--vacuum-full",
        action="store_true",
        help="Run VACUUM FULL ANALYZE after deleting to release physical storage. Locks each table while it runs.",
    )
    args = parser.parse_args(argv)

    with connect() as conn:
        print_sizes(conn, "before")
        sport_rows = sport_live_rows_ready_to_prune(conn, args.season_through)
        mlb_rows = mlb_live_rows_ready_to_prune(conn, args.keep_mlb_season_from)
        print("\nnon-baseball live rows ready to prune:", flush=True)
        if sport_rows:
            for sport, season, rows in sport_rows:
                print(f"  {sport} {season}: {rows:,}", flush=True)
        else:
            print("  none", flush=True)
        print("\nMLB live rows ready to prune:", flush=True)
        if mlb_rows:
            for season, rows in mlb_rows:
                print(f"  {season}: {rows:,}", flush=True)
        else:
            print("  none", flush=True)

        if not args.execute:
            print("\ndry run only; rerun with --execute to delete these source/staging rows", flush=True)
            return 0

        prune_sport_live_rows(conn, args.season_through)
        prune_mlb_live_rows(conn, args.keep_mlb_season_from)
        conn.commit()
        if args.vacuum or args.vacuum_full:
            vacuum(conn, full=args.vacuum_full)
        print_sizes(conn, "after")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
