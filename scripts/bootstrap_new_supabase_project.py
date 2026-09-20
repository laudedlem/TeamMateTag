#!/usr/bin/env python3
"""Create the TeamMateTag schema in a blank Supabase project.

Use only after ``preflight_compact_supabase_rebuild.py`` passes. The target
database URL is read from TEAMMATETAG_TARGET_DATABASE_URL so it is never
written into this repository or printed by the script.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILES = (
    ROOT / "db" / "schema_postgres.sql",
    ROOT / "db" / "cross_sport_schema_postgres.sql",
)
ADJACENCY_SCHEMA = ROOT / "db" / "compact_proof_adjacency_postgres.sql"
POSTGREST_DISABLED_DATA_API_WORKAROUND = ROOT / "db" / "postgrest_disabled_data_api_workaround.sql"


def execute_file(conn: psycopg.Connection, path: Path) -> None:
    print(f"applying {path.name}", flush=True)
    conn.execute(path.read_text(encoding="utf-8"))
    conn.commit()


def main() -> int:
    target_url = os.environ.get("TEAMMATETAG_TARGET_DATABASE_URL")
    if not target_url:
        raise SystemExit("ERROR: TEAMMATETAG_TARGET_DATABASE_URL is required")
    for path in (*SCHEMA_FILES, ADJACENCY_SCHEMA, POSTGREST_DISABLED_DATA_API_WORKAROUND):
        if not path.exists():
            raise SystemExit(f"ERROR: missing schema file: {path}")

    # ensure_runtime_schema owns the mutable app tables. Set this before the
    # module import; dotenv does not overwrite an already-exported target URL.
    os.environ["DATABASE_URL"] = target_url
    os.environ["DIRECT_URL"] = target_url
    os.environ["TEAMMATETAG_AUTO_MIGRATE"] = "1"
    with psycopg.connect(target_url, autocommit=False, prepare_threshold=None) as conn:
        for path in SCHEMA_FILES:
            execute_file(conn, path)

    sys.path.insert(0, str(ROOT))
    from web import server  # noqa: PLC0415

    server.ensure_runtime_schema()
    with psycopg.connect(target_url, autocommit=False, prepare_threshold=None) as conn:
        execute_file(conn, ADJACENCY_SCHEMA)
        execute_file(conn, POSTGREST_DISABLED_DATA_API_WORKAROUND)
        conn.execute("ANALYZE compact_player_keys")
        conn.execute("ANALYZE compact_team_keys")
        conn.execute("ANALYZE compact_teammate_adjacency")
        conn.commit()
    print("PASS: blank project schema is ready for compact runtime import.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
