#!/usr/bin/env python3
"""Emergency remove Football runtime/catalog data from Supabase.

This is for quota recovery only. It does not touch local raw/SQLite Football
data, so the compact production catalog can be rebuilt after cleanup.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from urllib.parse import quote

import requests

try:
    import psycopg
except ImportError:
    psycopg = None

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


SPORT = "football"


@dataclass(frozen=True)
class DeleteTarget:
    table: str
    filter_sql: str
    rest_filter: str | None = None


TARGETS = [
    DeleteTarget("sport_online_queue", "sport_id = %s"),
    DeleteTarget("sport_online_invites", "sport_id = %s"),
    DeleteTarget("guest_random_playoff_conditions", "sport_id = %s"),
    DeleteTarget("sport_online_games", "sport_id = %s"),
    DeleteTarget("sport_player_usage", "sport_id = %s"),
    DeleteTarget("player_headshot_source_attempts", "sport_id = %s"),
    DeleteTarget("player_headshots", "sport_id = %s"),
    DeleteTarget("sport_teammate_exclusions", "sport_id = %s"),
    DeleteTarget("sport_teammates", "sport_id = %s"),
    DeleteTarget("sport_live_player_games", "sport_id = %s"),
    DeleteTarget("sport_live_game_imports", "sport_id = %s"),
    DeleteTarget("sport_player_season_traits", "sport_id = %s"),
    DeleteTarget("sport_player_traits", "sport_id = %s"),
    DeleteTarget("sport_player_positions", "sport_id = %s"),
    DeleteTarget("sport_player_images", "sport_id = %s"),
    DeleteTarget("sport_players_searchable", "sport_id = %s"),
    DeleteTarget("sport_player_aliases", "sport_id = %s"),
    DeleteTarget("sport_player_external_ids", "sport_id = %s"),
    DeleteTarget("sport_data_provenance", "sport_id = %s"),
    DeleteTarget("sport_appearances", "sport_id = %s"),
    DeleteTarget("sport_player_stints", "sport_id = %s"),
    DeleteTarget("sport_teammate_stint_coverage", "sport_id = %s"),
    DeleteTarget("sport_players", "sport_id = %s"),
    DeleteTarget("sport_teams", "sport_id = %s"),
    DeleteTarget("sport_franchises", "sport_id = %s"),
]


def connect() -> "psycopg.Connection":
    if psycopg is None:
        raise RuntimeError("psycopg is not installed")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    return psycopg.connect(url, prepare_threshold=None)


def table_exists(conn: "psycopg.Connection", table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        return cur.fetchone()[0] is not None


def sql_count(conn: "psycopg.Connection", target: DeleteTarget) -> int | None:
    if not table_exists(conn, target.table):
        return None
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {target.table} WHERE {target.filter_sql}", (SPORT,))
        return int(cur.fetchone()[0])


def sql_delete(conn: "psycopg.Connection", target: DeleteTarget) -> int | None:
    if not table_exists(conn, target.table):
        return None
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {target.table} WHERE {target.filter_sql}", (SPORT,))
        return int(cur.rowcount)


def sql_purge(*, execute: bool, vacuum_full: bool) -> bool:
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '20min'")
            total = 0
            for target in TARGETS:
                if execute:
                    count = sql_delete(conn, target)
                    action = "deleted"
                else:
                    count = sql_count(conn, target)
                    action = "would delete"
                if count is None:
                    print(f"{target.table}: missing, skipped", flush=True)
                    continue
                total += count
                print(f"{target.table}: {action} {count:,}", flush=True)
            if execute:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE sports SET active = false WHERE sport_id = %s",
                        (SPORT,),
                    )
                conn.commit()
            print(f"total rows {'deleted' if execute else 'eligible'}: {total:,}", flush=True)
            if execute and vacuum_full:
                conn.commit()
                conn.autocommit = True
                with conn.cursor() as cur:
                    for target in TARGETS:
                        if table_exists(conn, target.table):
                            print(f"VACUUM FULL ANALYZE {target.table}", flush=True)
                            cur.execute(f"VACUUM (FULL, ANALYZE) {target.table}")
            return True
    except Exception as exc:
        print(f"SQL purge unavailable: {exc}", flush=True)
        return False


def rest_headers() -> dict[str, str]:
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required for REST purge")
    return {
        "apikey": key,
        "authorization": f"Bearer {key}",
        "prefer": "return=minimal",
    }


def rest_base_url() -> str:
    url = os.environ.get("SUPABASE_URL")
    if not url:
        raise RuntimeError("SUPABASE_URL is required for REST purge")
    return url.rstrip("/") + "/rest/v1"


def rest_delete_table(session: requests.Session, base_url: str, target: DeleteTarget, *, execute: bool) -> bool:
    filter_expr = target.rest_filter or f"sport_id=eq.{quote(SPORT)}"
    url = f"{base_url}/{target.table}?{filter_expr}"
    if execute:
        response = session.delete(url, timeout=120)
        action = "delete"
    else:
        response = session.get(
            url + "&select=sport_id",
            headers={**session.headers, "range": "0-0", "prefer": "count=exact"},
            timeout=60,
        )
        action = "count"
    if response.status_code in {404}:
        print(f"{target.table}: missing or not exposed over REST, skipped", flush=True)
        return True
    if response.status_code not in {200, 204}:
        print(f"{target.table}: REST {action} failed {response.status_code}: {response.text[:500]}", flush=True)
        return False
    count = response.headers.get("content-range", "").split("/")[-1] if not execute else "unknown"
    print(f"{target.table}: REST {'deleted' if execute else 'eligible'} {count}", flush=True)
    return True


def rest_purge(*, execute: bool) -> bool:
    try:
        base_url = rest_base_url()
        with requests.Session() as session:
            session.headers.update(rest_headers())
            ok = True
            for target in TARGETS:
                ok = rest_delete_table(session, base_url, target, execute=execute) and ok
            if execute:
                response = session.patch(
                    f"{base_url}/sports?sport_id=eq.{quote(SPORT)}",
                    json={"active": False},
                    timeout=60,
                )
                if response.status_code not in {200, 204}:
                    print(f"sports: REST deactivate failed {response.status_code}: {response.text[:500]}", flush=True)
                    ok = False
            return ok
    except Exception as exc:
        print(f"REST purge unavailable: {exc}", flush=True)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Actually delete Football rows.")
    parser.add_argument("--rest", action="store_true", help="Use Supabase REST instead of SQL.")
    parser.add_argument("--sql", action="store_true", help="Use SQL only.")
    parser.add_argument("--vacuum-full", action="store_true", help="Run VACUUM FULL after SQL delete.")
    args = parser.parse_args(argv)

    if not args.execute:
        print("dry run only; add --execute to delete Football data", flush=True)

    if args.rest and args.vacuum_full:
        print("--vacuum-full only works through SQL, not REST", flush=True)

    if args.rest:
        return 0 if rest_purge(execute=args.execute) else 1
    if args.sql:
        return 0 if sql_purge(execute=args.execute, vacuum_full=args.vacuum_full) else 1

    if sql_purge(execute=args.execute, vacuum_full=args.vacuum_full):
        return 0
    print("falling back to Supabase REST", flush=True)
    return 0 if rest_purge(execute=args.execute) else 1


if __name__ == "__main__":
    raise SystemExit(main())
