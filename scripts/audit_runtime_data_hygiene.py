#!/usr/bin/env python3
"""Audit the local-first runtime data contract.

This intentionally favors loud failures over quiet drift. Raw/source data may
be large under raw/, but production-facing jobs should publish only compact
runtime rows after local derivation.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

try:
    import psycopg
except ImportError:
    psycopg = None

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
COMPACT_DB = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
MLB_LIVE_DB = ROOT / "raw" / "mlb_live_runtime" / "mlb_live_2026.sqlite"
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def check(condition: bool, label: str, failures: list[str]) -> None:
    prefix = "OK" if condition else "FAIL"
    print(f"{prefix}: {label}")
    if not condition:
        failures.append(label)


def sqlite_count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def audit_local_compact(failures: list[str]) -> None:
    check(COMPACT_DB.exists(), f"compact runtime exists: {COMPACT_DB}", failures)
    if not COMPACT_DB.exists():
        return
    with sqlite3.connect(COMPACT_DB) as conn:
        for scope in ("baseball", "basketball", "hockey", "football"):
            teammate_rows = sqlite_count(
                conn,
                "SELECT COUNT(*) FROM teammate_team_seasons WHERE scope = ?",
                (scope,),
            )
            check(teammate_rows > 0, f"{scope} compact teammate rows present ({teammate_rows:,})", failures)
        check(
            sqlite_count(conn, "SELECT COUNT(*) FROM player_playoff_traits") > 0,
            "baseball playoff traits are built locally",
            failures,
        )
        check(
            sqlite_count(conn, "SELECT COUNT(*) FROM player_powerup_qualifications") > 0,
            "baseball powerup qualifications are built locally",
            failures,
        )
        missing_players = sqlite_count(
            conn,
            """
            SELECT COUNT(*)
              FROM teammate_team_seasons ts
              LEFT JOIN compact_player_keys pa ON pa.player_key = ts.player_a_key
              LEFT JOIN compact_player_keys pb ON pb.player_key = ts.player_b_key
             WHERE pa.player_key IS NULL OR pb.player_key IS NULL
            """,
        )
        missing_teams = sqlite_count(
            conn,
            """
            SELECT COUNT(*)
              FROM teammate_team_seasons ts
              LEFT JOIN compact_team_keys tk ON tk.team_key = ts.team_key
             WHERE tk.team_key IS NULL
            """,
        )
        check(missing_players == 0, "compact teammate rows have valid player keys", failures)
        check(missing_teams == 0, "compact teammate rows have valid team keys", failures)


def audit_mlb_live(failures: list[str]) -> None:
    check(MLB_LIVE_DB.exists(), f"local MLB live runtime exists: {MLB_LIVE_DB}", failures)
    if not MLB_LIVE_DB.exists():
        return
    with sqlite3.connect(MLB_LIVE_DB) as conn:
        check(
            sqlite_count(conn, "SELECT COUNT(*) FROM mlb_live_player_games WHERE season = 2026") > 0,
            "MLB 2026 player-game source rows are local",
            failures,
        )
        check(
            sqlite_count(conn, "SELECT COUNT(*) FROM mlb_teammate_game_proofs WHERE season = 2026") > 0,
            "MLB 2026 compact proofs are derived locally",
            failures,
        )
        check(
            sqlite_count(conn, "SELECT COUNT(*) FROM mlb_live_player_game_stats WHERE season = 2026") > 0,
            "MLB 2026 stat source rows are local",
            failures,
        )
        check(
            sqlite_count(conn, "SELECT COUNT(*) FROM player_powerup_qualifications WHERE season = 2026") > 0,
            "MLB 2026 derived powerup qualifications are local",
            failures,
        )


def audit_workflows(failures: list[str]) -> None:
    mlb_workflow = (WORKFLOW_DIR / "update-mlb-live-data.yml").read_text(encoding="utf-8")
    check("update_mlb_compact_live.py" in mlb_workflow, "MLB scheduled workflow uses compact updater", failures)
    check("update_mlb_live_data.py" not in mlb_workflow, "MLB scheduled workflow does not call legacy direct updater", failures)
    workflow_expectations = {
        "update-nba-live-data.yml": ("update_cross_sport_compact_live.py basketball", "update_nba_live_data.py"),
        "update-nhl-live-data.yml": ("update_cross_sport_compact_live.py hockey", "update_nhl_live_data.py"),
        "update-nfl-live-data.yml": ("update_nfl_compact_live.py", "update_nfl_live_data.py"),
    }
    for filename, (compact_call, legacy_call) in workflow_expectations.items():
        text = (WORKFLOW_DIR / filename).read_text(encoding="utf-8")
        check(compact_call in text, f"{filename} uses compact updater", failures)
        check(legacy_call not in text, f"{filename} does not call legacy direct updater", failures)


def audit_supabase(failures: list[str]) -> None:
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DIRECT_URL")
    if not database_url:
        print("SKIP: DATABASE_URL/DIRECT_URL not set; Supabase staging audit not run")
        return
    if psycopg is None:
        print("SKIP: psycopg not installed; Supabase staging audit not run")
        return
    with psycopg.connect(database_url, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            for table in ("mlb_live_player_games", "mlb_live_game_imports"):
                exists = cur.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]
                if exists:
                    rows = int(cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    check(rows == 0, f"Supabase {table} staging rows pruned ({rows:,})", failures)
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
            print(f"INFO: Supabase database size {cur.fetchone()[0]}")


def main() -> int:
    failures: list[str] = []
    audit_local_compact(failures)
    audit_mlb_live(failures)
    audit_workflows(failures)
    audit_supabase(failures)
    if failures:
        print("\nData hygiene audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nData hygiene audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
