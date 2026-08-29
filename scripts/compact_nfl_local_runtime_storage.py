#!/usr/bin/env python3
"""Compact the local Football runtime proof store with integer keys.

Input is the refined local Football runtime DB, not raw boxscore/snap data.
Output keeps the same gameplay-facing view names while storing the million-plus
teammate proof rows as small integer keys.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "raw" / "nfl_game_teammates" / "nfl_compact_runtime.sqlite"
DEFAULT_OUTPUT = ROOT / "raw" / "nfl_game_teammates" / "nfl_compact_runtime_int.sqlite"

CATALOG_TABLES = (
    "sport_franchises",
    "sport_teams",
    "sport_players",
    "sport_appearances",
    "sport_player_stints",
    "sport_player_positions",
    "sport_teammate_stint_coverage",
    "sport_players_searchable",
    "sport_player_images",
)


def table_sql(src: sqlite3.Connection, table: str) -> str:
    row = src.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row is None or not row[0]:
        raise RuntimeError(f"missing source table: {table}")
    return str(row[0])


def copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> int:
    dst.execute(table_sql(src, table))
    columns = [row[1] for row in src.execute(f"PRAGMA table_info({table})")]
    column_list = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = src.execute(f"SELECT {column_list} FROM {table}")
    count = 0
    with dst:
        dst.executemany(
            f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
            rows,
        )
        count = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return int(count)


def create_compact_tables(dst: sqlite3.Connection) -> None:
    dst.executescript(
        """
        CREATE TABLE compact_player_keys (
            player_key INTEGER PRIMARY KEY,
            player_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE compact_team_keys (
            team_key INTEGER PRIMARY KEY,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            UNIQUE (team_id, season)
        );
        CREATE TABLE compact_sport_teammates (
            player_a_key INTEGER NOT NULL,
            player_b_key INTEGER NOT NULL,
            team_key INTEGER NOT NULL,
            season INTEGER NOT NULL,
            PRIMARY KEY (player_a_key, player_b_key, team_key, season)
        ) WITHOUT ROWID;
        """
    )


def compact_proofs(src: sqlite3.Connection, dst: sqlite3.Connection) -> dict[str, int]:
    src_count = int(src.execute("SELECT COUNT(*) FROM sport_teammates").fetchone()[0])
    with dst:
        dst.execute(
            """
            INSERT INTO compact_player_keys (player_id)
            SELECT player_id
              FROM (
                    SELECT player_a_id AS player_id FROM src.sport_teammates
                    UNION
                    SELECT player_b_id AS player_id FROM src.sport_teammates
              )
             ORDER BY player_id
            """
        )
        dst.execute(
            """
            INSERT INTO compact_team_keys (team_id, season)
            SELECT team_id, season
              FROM src.sport_teammates
             GROUP BY team_id, season
             ORDER BY season, team_id
            """
        )
        dst.execute(
            """
            INSERT INTO compact_sport_teammates
                (player_a_key, player_b_key, team_key, season)
            SELECT pa.player_key, pb.player_key, team.team_key, t.season
              FROM src.sport_teammates t
              JOIN compact_player_keys pa ON pa.player_id = t.player_a_id
              JOIN compact_player_keys pb ON pb.player_id = t.player_b_id
              JOIN compact_team_keys team
                ON team.team_id = t.team_id AND team.season = t.season
            """
        )
        dst.execute(
            """
            CREATE VIEW sport_teammates AS
            SELECT 'football' AS sport_id,
                   pa.player_id AS player_a_id,
                   pb.player_id AS player_b_id,
                   team.team_id,
                   c.season
              FROM compact_sport_teammates c
              JOIN compact_player_keys pa ON pa.player_key = c.player_a_key
              JOIN compact_player_keys pb ON pb.player_key = c.player_b_key
              JOIN compact_team_keys team ON team.team_key = c.team_key
            """
        )
        dst.execute(
            "CREATE INDEX idx_compact_nfl_player_lookup ON compact_player_keys(player_id, player_key)"
        )
        dst.execute(
            "CREATE INDEX idx_compact_nfl_team_lookup ON compact_team_keys(team_id, season, team_key)"
        )
        dst.execute(
            "CREATE INDEX idx_compact_nfl_reverse ON compact_sport_teammates(player_b_key, player_a_key)"
        )
        dst.execute(
            "CREATE INDEX idx_nfl_appearances_player ON sport_appearances(sport_id, player_id)"
        )
        dst.execute(
            "CREATE INDEX idx_nfl_appearances_team ON sport_appearances(sport_id, team_id, season)"
        )
        dst.execute(
            "CREATE INDEX idx_nfl_search_key ON sport_players_searchable(sport_id, search_key)"
        )
    dst.execute("VACUUM")

    compact_count = int(dst.execute("SELECT COUNT(*) FROM compact_sport_teammates").fetchone()[0])
    if compact_count != src_count:
        raise RuntimeError(f"proof row mismatch: {src_count:,} source vs {compact_count:,} compact")
    return {
        "proof_rows": compact_count,
        "player_keys": int(dst.execute("SELECT COUNT(*) FROM compact_player_keys").fetchone()[0]),
        "team_keys": int(dst.execute("SELECT COUNT(*) FROM compact_team_keys").fetchone()[0]),
    }


def verify(dst: sqlite3.Connection) -> dict[str, int]:
    checks: dict[str, int] = {}
    for label, sql in {
        "view_rows": "SELECT COUNT(*) FROM sport_teammates",
        "missing_player_a": """
            SELECT COUNT(*)
              FROM sport_teammates t
              LEFT JOIN sport_players p
                ON p.sport_id = t.sport_id AND p.player_id = t.player_a_id
             WHERE p.player_id IS NULL
        """,
        "missing_player_b": """
            SELECT COUNT(*)
              FROM sport_teammates t
              LEFT JOIN sport_players p
                ON p.sport_id = t.sport_id AND p.player_id = t.player_b_id
             WHERE p.player_id IS NULL
        """,
        "missing_team": """
            SELECT COUNT(*)
              FROM sport_teammates t
              LEFT JOIN sport_teams team
                ON team.sport_id = t.sport_id
               AND team.team_id = t.team_id
               AND team.season = t.season
             WHERE team.team_id IS NULL
        """,
    }.items():
        checks[label] = int(dst.execute(sql).fetchone()[0])
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"missing input: {args.input}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()

    src = sqlite3.connect(args.input)
    dst = sqlite3.connect(args.output)
    try:
        dst.execute("PRAGMA journal_mode = OFF")
        dst.execute("PRAGMA synchronous = OFF")
        dst.execute("PRAGMA temp_store = MEMORY")
        dst.execute("ATTACH DATABASE ? AS src", (str(args.input),))
        copied = {table: copy_table(src, dst, table) for table in CATALOG_TABLES}
        create_compact_tables(dst)
        compact = compact_proofs(src, dst)
        checks = verify(dst)
    finally:
        src.close()
        dst.close()

    print(f"input: {args.input}")
    print(f"input_size_mb: {args.input.stat().st_size / 1024 / 1024:.1f}")
    print(f"output: {args.output}")
    print(f"output_size_mb: {args.output.stat().st_size / 1024 / 1024:.1f}")
    for table, count in copied.items():
        print(f"{table}: {count:,}")
    for key, count in compact.items():
        print(f"{key}: {count:,}")
    for key, count in checks.items():
        print(f"{key}: {count:,}")
    if any(checks[key] for key in ("missing_player_a", "missing_player_b", "missing_team")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
