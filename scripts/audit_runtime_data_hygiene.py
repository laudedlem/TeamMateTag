#!/usr/bin/env python3
"""Audit the local-first runtime data contract.

This intentionally favors loud failures over quiet drift. Raw/source data may
be large under raw/, but production-facing jobs should publish only compact
runtime rows after local derivation.
"""
from __future__ import annotations

import os
import json
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
BASEBALL_HEADSHOT_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "baseball_headshots.sqlite"
BASKETBALL_HEADSHOT_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "basketball_headshots.sqlite"
HOCKEY_HEADSHOT_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "hockey_headshots.sqlite"
FOOTBALL_HEADSHOT_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "football_headshots.sqlite"
FILE_STORAGE_ROOT = ROOT / "raw" / "file_storage"
FILE_STORAGE_HEADSHOT_ROOT = FILE_STORAGE_ROOT / "player-headshots"
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def check(condition: bool, label: str, failures: list[str]) -> None:
    prefix = "OK" if condition else "FAIL"
    print(f"{prefix}: {label}")
    if not condition:
        failures.append(label)


def sqlite_count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def audit_file_storage_artifacts(failures: list[str]) -> None:
    if not COMPACT_DB.exists():
        check(False, "compact runtime is required before auditing file-storage coverage", failures)
        return
    manifest_path = FILE_STORAGE_ROOT / "manifest.json"
    check(manifest_path.exists(), f"deploy-ready file storage manifest exists: {manifest_path}", failures)
    for sport, max_mb in (("baseball", 25), ("basketball", 18), ("hockey", 35), ("football", 70)):
        sport_dir = FILE_STORAGE_HEADSHOT_ROOT / sport
        sport_manifest = FILE_STORAGE_ROOT / "manifests" / "headshots" / f"{sport}.json"
        check(sport_dir.exists(), f"{sport} optimized headshot folder exists", failures)
        check(sport_manifest.exists(), f"{sport} optimized headshot manifest exists", failures)
        if not sport_dir.exists() or not sport_manifest.exists():
            continue
        files = list(sport_dir.glob("*.webp"))
        payload = load_json(sport_manifest)
        rows = payload.get("rows") or []
        row_ids = {row.get("player_id") for row in rows}
        folder_mb = path_size(sport_dir) / 1024 / 1024
        check(len(files) == len(rows), f"{sport} optimized image count matches manifest ({len(files):,})", failures)
        check(folder_mb <= max_mb, f"{sport} optimized headshots stay compact ({folder_mb:.1f} MB <= {max_mb} MB)", failures)
        with sqlite3.connect(COMPACT_DB) as conn:
            runtime_ids = {
                player_id
                for player_id, in conn.execute(
                    "SELECT player_id FROM runtime_players WHERE scope = ?",
                    (sport,),
                )
            }
        missing = runtime_ids - row_ids
        if sport == "football":
            football_missing_manifest = FILE_STORAGE_ROOT / "manifests" / "headshots" / "football_missing.json"
            check(football_missing_manifest.exists(), "football missing-headshot manifest exists", failures)
            listed_missing = set()
            if football_missing_manifest.exists():
                listed_missing = {row.get("player_id") for row in (load_json(football_missing_manifest).get("rows") or [])}
            check(
                not (runtime_ids - row_ids - listed_missing),
                f"football optimized manifest plus missing list covers runtime players ({len(row_ids | listed_missing):,}/{len(runtime_ids):,})",
                failures,
            )
        else:
            check(not missing, f"{sport} optimized manifest covers runtime players ({len(runtime_ids) - len(missing):,}/{len(runtime_ids):,})", failures)
    gameplay_dir = FILE_STORAGE_ROOT / "teammatetag-runtime" / "gameplay"
    for filename in (
        "baseball_player_playoff_traits.json",
        "baseball_player_powerup_qualifications.json",
        "runtime_coverage.json",
    ):
        check((gameplay_dir / filename).exists(), f"static gameplay artifact exists: {filename}", failures)


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
        covered_headshots = {
            player_id
            for player_id, in conn.execute(
                """
                SELECT player_id
                  FROM runtime_headshots
                 WHERE scope = 'baseball'
                   AND player_id IN ('betanyu01', 'cairomi01', 'tuckemi01', 'greento02', 'olivejo01')
                   AND status = 'verified'
                   AND COALESCE(source_url, fallback_url, '') <> ''
                """
            )
        }
        check(
            covered_headshots == {'betanyu01', 'cairomi01', 'tuckemi01', 'greento02', 'olivejo01'},
            "known Baseball Film Review headshot gaps are covered locally",
            failures,
        )
        baseball_players = sqlite_count(conn, "SELECT COUNT(*) FROM runtime_players WHERE scope = 'baseball'")
        baseball_verified_headshots = sqlite_count(
            conn,
            """
            SELECT COUNT(*)
              FROM runtime_players p
              JOIN runtime_headshots h
                ON h.scope = p.scope
               AND h.player_id = p.player_id
             WHERE p.scope = 'baseball'
               AND h.status = 'verified'
               AND COALESCE(h.source_url, h.fallback_url, '') <> ''
            """,
        )
        baseball_missing_headshots = baseball_players - baseball_verified_headshots
        check(
            baseball_missing_headshots <= 100,
            (
                "baseball verified headshot gaps stay targeted "
                f"({baseball_missing_headshots:,}/{baseball_players:,} missing)"
            ),
            failures,
        )
        check(BASEBALL_HEADSHOT_REGISTRY_DB.exists(), "canonical Baseball headshot registry exists locally", failures)
        if BASEBALL_HEADSHOT_REGISTRY_DB.exists():
            with sqlite3.connect(BASEBALL_HEADSHOT_REGISTRY_DB) as registry:
                registry_players = sqlite_count(registry, "SELECT COUNT(*) FROM baseball_headshots")
                registry_verified = sqlite_count(
                    registry,
                    """
                    SELECT COUNT(*)
                      FROM baseball_headshots
                     WHERE status = 'verified'
                       AND COALESCE(source_url, fallback_url, '') <> ''
                    """,
                )
            check(
                registry_players >= baseball_players,
                f"canonical Baseball registry covers runtime players ({registry_players:,}/{baseball_players:,})",
                failures,
            )
            check(
                registry_verified == baseball_verified_headshots,
                "canonical Baseball registry and runtime verified counts match",
                failures,
            )
        basketball_players = sqlite_count(conn, "SELECT COUNT(*) FROM runtime_players WHERE scope = 'basketball'")
        basketball_verified_headshots = sqlite_count(
            conn,
            """
            SELECT COUNT(*)
              FROM runtime_players p
              JOIN runtime_headshots h
                ON h.scope = p.scope
               AND h.player_id = p.player_id
             WHERE p.scope = 'basketball'
               AND h.status = 'verified'
               AND COALESCE(h.source_url, h.fallback_url, '') <> ''
            """,
        )
        basketball_missing_headshots = basketball_players - basketball_verified_headshots
        check(
            basketball_missing_headshots == 0,
            (
                "basketball verified headshot gaps stay closed "
                f"({basketball_missing_headshots:,}/{basketball_players:,} missing)"
            ),
            failures,
        )
        check(BASKETBALL_HEADSHOT_REGISTRY_DB.exists(), "canonical Basketball headshot registry exists locally", failures)
        if BASKETBALL_HEADSHOT_REGISTRY_DB.exists():
            with sqlite3.connect(BASKETBALL_HEADSHOT_REGISTRY_DB) as registry:
                registry_players = sqlite_count(registry, "SELECT COUNT(*) FROM basketball_headshots")
                registry_verified = sqlite_count(
                    registry,
                    """
                    SELECT COUNT(*)
                      FROM basketball_headshots
                     WHERE status = 'verified'
                       AND COALESCE(source_url, '') <> ''
                    """,
                )
            check(
                registry_players >= basketball_players,
                f"canonical Basketball registry covers runtime players ({registry_players:,}/{basketball_players:,})",
                failures,
            )
            check(
                registry_verified == basketball_verified_headshots,
                "canonical Basketball registry and runtime verified counts match",
                failures,
            )
        hockey_players = sqlite_count(conn, "SELECT COUNT(*) FROM runtime_players WHERE scope = 'hockey'")
        hockey_verified_headshots = sqlite_count(
            conn,
            """
            SELECT COUNT(*)
              FROM runtime_players p
              JOIN runtime_headshots h
                ON h.scope = p.scope
               AND h.player_id = p.player_id
             WHERE p.scope = 'hockey'
               AND h.status = 'verified'
               AND COALESCE(h.source_url, h.fallback_url, '') <> ''
            """,
        )
        hockey_missing_headshots = hockey_players - hockey_verified_headshots
        check(
            hockey_missing_headshots == 0,
            (
                "hockey verified headshot gaps stay closed "
                f"({hockey_missing_headshots:,}/{hockey_players:,} missing)"
            ),
            failures,
        )
        check(HOCKEY_HEADSHOT_REGISTRY_DB.exists(), "canonical Hockey headshot registry exists locally", failures)
        if HOCKEY_HEADSHOT_REGISTRY_DB.exists():
            with sqlite3.connect(HOCKEY_HEADSHOT_REGISTRY_DB) as registry:
                registry_players = sqlite_count(registry, "SELECT COUNT(*) FROM hockey_headshots")
                registry_verified = sqlite_count(
                    registry,
                    """
                    SELECT COUNT(*)
                      FROM hockey_headshots
                     WHERE status = 'verified'
                       AND COALESCE(source_url, '') <> ''
                    """,
                )
            check(
                registry_players >= hockey_players,
                f"canonical Hockey registry covers runtime players ({registry_players:,}/{hockey_players:,})",
                failures,
            )
            check(
                registry_verified == hockey_verified_headshots,
                "canonical Hockey registry and runtime verified counts match",
                failures,
            )
        football_players = sqlite_count(conn, "SELECT COUNT(*) FROM runtime_players WHERE scope = 'football'")
        football_verified_headshots = sqlite_count(
            conn,
            """
            SELECT COUNT(*)
              FROM runtime_players p
              JOIN runtime_headshots h
                ON h.scope = p.scope
               AND h.player_id = p.player_id
             WHERE p.scope = 'football'
               AND h.status = 'verified'
               AND COALESCE(h.source_url, h.fallback_url, '') <> ''
            """,
        )
        football_priority_players = 0
        football_priority_verified = 0
        check(FOOTBALL_HEADSHOT_REGISTRY_DB.exists(), "canonical Football headshot registry exists locally", failures)
        if FOOTBALL_HEADSHOT_REGISTRY_DB.exists():
            with sqlite3.connect(FOOTBALL_HEADSHOT_REGISTRY_DB) as registry:
                registry_players = sqlite_count(registry, "SELECT COUNT(*) FROM football_headshots")
                registry_verified = sqlite_count(
                    registry,
                    """
                    SELECT COUNT(*)
                      FROM football_headshots
                     WHERE status = 'verified'
                       AND COALESCE(source_url, local_path, '') <> ''
                    """,
                )
                registry_priority_accounted = sqlite_count(
                    registry,
                    "SELECT COUNT(*) FROM football_headshots WHERE career_games >= 50",
                )
                registry_priority_verified = sqlite_count(
                    registry,
                    """
                    SELECT COUNT(*)
                      FROM football_headshots
                     WHERE career_games >= 50
                       AND status = 'verified'
                       AND COALESCE(source_url, local_path, '') <> ''
                    """,
                )
                football_priority_players = registry_priority_accounted
                football_priority_verified = registry_priority_verified
            check(
                registry_players >= football_players,
                f"canonical Football registry covers runtime players ({registry_players:,}/{football_players:,})",
                failures,
            )
            check(
                registry_verified == football_verified_headshots,
                "canonical Football registry and runtime verified counts match",
                failures,
            )
            check(
                registry_priority_accounted >= football_priority_verified,
                (
                    "canonical Football registry accounts for 50+ game players "
                    f"({registry_priority_accounted:,}/{football_priority_verified:,} verified)"
                ),
                failures,
            )
        check(
            football_priority_verified >= 3900,
            (
                "football 50+ game headshot coverage stays high "
                f"({football_priority_verified:,}/{football_priority_players:,} verified)"
            ),
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
    for legacy_script in (
        "update_mlb_live_data.py",
        "update_nba_live_data.py",
        "update_nhl_live_data.py",
        "update_nfl_live_data.py",
        "import_mlb_game_teammates_to_postgres.py",
        "import_nba_espn_game_teammates_to_postgres.py",
        "import_nba_legacy_game_teammates_to_postgres.py",
        "import_nhl_game_teammates_to_postgres.py",
        "import_nfl_snap_teammates_to_postgres.py",
        "import_nfl_gamebook_teammates_to_postgres.py",
        "migrate_to_postgres.py",
        "migrate_cross_sport_to_postgres.py",
        "load_playoff_powerup_data.py",
        "load_playoff_win_condition_data.py",
        "compact_supabase_proof_storage.py",
        "purge_football_from_supabase.py",
        "trim_supabase_runtime_storage.py",
        "expand_baseball_history.py",
        "build_nfl_headshot_priority_review.py",
        "collect_nfl_espn_catalog_headshots.py",
        "collect_nfl_headshot_review_candidates.py",
        "collect_nfl_priority_source_candidates.py",
        "export_nfl_headshot_priority.py",
        "import_nfl_reviewed_headshots.py",
        "refresh_nflverse_headshots.py",
        "report_nfl_headshot_gaps.py",
        "resolve_nfl_espn_headshots.py",
        "resolve_nfl_footballdb_headshots.py",
        "resolve_nfl_thesportsdb_headshots.py",
        "resolve_nfl_web_image_headshots.py",
        "resolve_nfl_wikimedia_headshots.py",
        "supplement_nfl_priority_colleges.py",
        "supplement_nfl_reference_ids.py",
        "supplement_nfl_roster_identities.py",
    ):
        check(not (ROOT / "scripts" / legacy_script).exists(), f"{legacy_script} has been deleted", failures)
    for legacy_path in (
        "raw/nfl_headshot_review",
        "raw/nfl_priority_source_review",
        "raw/nfl_headshot_priority_50plus.csv",
        "raw/nfl_headshot_priority_50plus_review.html",
        "raw/nfl_headshot_priority_list.csv",
        "raw/nfl_unresolved_headshots_2026-08-15.csv",
        "raw/nfl_unresolved_headshots_2026-08-16.csv",
        "raw/nfl_50plus_photo_research_top10.csv",
    ):
        check(not (ROOT / legacy_path).exists(), f"{legacy_path} has been deleted", failures)
    for legacy_sql in (
        "purge_football_runtime.sql",
        "emergency_free_sport_index_space.sql",
    ):
        check(not (ROOT / "scripts" / "sql" / legacy_sql).exists(), f"{legacy_sql} has been deleted", failures)


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
    audit_file_storage_artifacts(failures)
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
