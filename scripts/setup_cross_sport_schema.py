"""Create the additive Postgres tables used for non-baseball sports.

Run from the repository root after DATABASE_URL has been set in .env:
    python scripts/setup_cross_sport_schema.py
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "db" / "cross_sport_schema_postgres.sql"


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required. Set it in .env first.")

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(
        database_url, autocommit=True, prepare_threshold=None,
    ) as conn:
        conn.execute("SET default_transaction_read_only = off")
        conn.execute(schema)
    print("Cross-sport schema is ready: basketball, hockey, and football.")


if __name__ == "__main__":
    main()
