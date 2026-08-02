#!/usr/bin/env python3
"""
migrate_to_postgres.py - one-way load from the local SQLite snapshot
into a Supabase Postgres database.

Reads DATABASE_URL from the environment (Supabase pooler connection URI).
Applies db/schema_postgres.sql, then COPY-loads every static-data table.
Idempotent: TRUNCATEs each table before loading.

Run after creating a fresh Supabase project, and again whenever the local
SQLite is rebuilt (annual Lahman pull, etc.).

Usage:
    set DATABASE_URL=postgresql://postgres.xxxx:PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres
    python scripts/migrate_to_postgres.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

try:
    import psycopg
except ImportError:
    print(
        "psycopg not installed. Run: pip install 'psycopg[binary]'",
        file=sys.stderr,
    )
    sys.exit(1)

# Load DATABASE_URL etc. from .env if present.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = ROOT / "db" / "base2nerdle.sqlite"
SCHEMA_PATH = ROOT / "db" / "schema_postgres.sql"

# Order matters: tables with foreign keys come after their referents.
STATIC_TABLES = [
    "franchises",
    "teams",
    "players",
    "appearances",
    "players_searchable",
    "nickname_search",
    "player_nicknames",
    # data_provenance intentionally skipped: SQLite tolerated duplicate
    # (source, NULL) rows from successive ETL runs but Postgres won't.
    # Nothing in the runtime queries this table, so we leave it empty.
]


def coerce(table: str, row: sqlite3.Row) -> tuple:
    """SQLite -> Postgres type coercions. Most columns map identically;
    franchises.active is INTEGER 0/1 in SQLite but BOOLEAN in Postgres."""
    if table == "franchises":
        d = dict(row)
        if d.get("active") is not None:
            d["active"] = bool(d["active"])
        return tuple(d[c] for c in row.keys())
    return tuple(row)


def main() -> int:
    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: set DATABASE_URL (Supabase connection URI) in the environment",
              file=sys.stderr)
        return 1

    if not SQLITE_PATH.exists():
        print(f"ERROR: {SQLITE_PATH} not found. Run the ETL pipeline first.",
              file=sys.stderr)
        return 1

    safe_url = pg_url.split("@", 1)[-1] if "@" in pg_url else pg_url
    print(f"source: {SQLITE_PATH}")
    print(f"target: {safe_url}")

    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    with psycopg.connect(pg_url, autocommit=False) as dst:
        print("\napplying schema (db/schema_postgres.sql)...")
        with dst.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
            # The live game derives links from appearances. This obsolete
            # materialized pair graph is prohibitively large on Supabase Free.
            cur.execute("DROP TABLE IF EXISTS teammates CASCADE")
        dst.commit()

        for table in STATIC_TABLES:
            t0 = time.monotonic()
            try:
                sqlite_rows = list(src.execute(f"SELECT * FROM {table}"))
            except sqlite3.OperationalError as e:
                print(f"  SKIP {table}: {e}")
                continue
            if not sqlite_rows:
                print(f"  empty {table}, skipping")
                continue

            cols = list(sqlite_rows[0].keys())
            col_list = ", ".join(f'"{c}"' for c in cols)

            with dst.cursor() as cur:
                cur.execute(f"TRUNCATE {table} CASCADE")
                # COPY is far faster than executemany for big tables like teammates.
                with cur.copy(f"COPY {table} ({col_list}) FROM STDIN") as cp:
                    for row in sqlite_rows:
                        cp.write_row(coerce(table, row))
                count = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            dst.commit()
            print(f"  {table:25s} {count:>8,} rows  ({time.monotonic() - t0:.1f}s)")

    print("\nmigration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
