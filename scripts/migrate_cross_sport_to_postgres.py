#!/usr/bin/env python3
"""Copy the compact non-baseball game catalog from SQLite to Supabase.

This deliberately migrates only the runtime tables. Raw source archives,
identity-review history, honor observations, and locally cached headshots stay
on the development machine. The teammate graph is calculated from the indexed
appearance table instead of materializing every player pair.

Run from the repository root after DATABASE_URL has been set in .env:

    python scripts/migrate_cross_sport_to_postgres.py
"""
from __future__ import annotations

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
SOURCE = ROOT / "db" / "teammatetag_local.sqlite"
SCHEMA = ROOT / "db" / "cross_sport_schema_postgres.sql"
SPORTS = ("basketball", "football", "hockey")
EXHIBITION_TEAM_SQL = """
(
    replace(lower(source.name), '-', ' ') LIKE '%all star%'
    OR replace(lower(source.name), '-', ' ') LIKE '%rising star%'
    OR lower(source.name) = 'world'
    OR (source.sport_id = 'basketball' AND lower(source.name) IN ('ogs', 'stripes'))
    OR (source.sport_id = 'basketball' AND lower(source.name) LIKE 'team %')
)
"""
TABLES = (
    ("sport_franchises", ("sport_id", "franchise_id", "name")),
    ("sport_teams", ("sport_id", "team_id", "season", "franchise_id", "name")),
    ("sport_players", ("sport_id", "player_id", "external_id", "display_name", "first_name", "last_name", "birth_year", "debut_year", "final_year", "primary_pos")),
    ("sport_player_positions", ("sport_id", "player_id", "position", "games")),
    ("sport_appearances", ("sport_id", "player_id", "team_id", "season", "games_total")),
    ("sport_player_stints", ("sport_id", "player_id", "team_id", "season", "first_unit", "last_unit", "first_label", "last_label", "source")),
    ("sport_teammate_stint_coverage", ("sport_id", "season", "coverage_type", "strict", "source")),
    ("sport_players_searchable", ("sport_id", "player_id", "display_name", "disambiguation", "search_key", "last_key", "career_games", "teammate_count")),
    ("sport_player_traits", ("sport_id", "player_id", "career_games", "career_points", "career_goals", "career_assists", "career_touchdowns", "passing_touchdowns", "rushing_touchdowns", "receiving_touchdowns", "career_sacks", "career_interceptions", "all_star_count", "mvp_count", "roty_count", "championship_count", "source", "updated_at")),
    ("sport_player_season_traits", ("sport_id", "player_id", "season", "games", "points", "goals", "assists", "touchdowns", "passing_touchdowns", "rushing_touchdowns", "receiving_touchdowns", "sacks", "interceptions", "source")),
)


def copy_rows(src: sqlite3.Connection, dst: psycopg.Connection, table: str, columns: tuple[str, ...]) -> int:
    placeholders = ", ".join("?" for _ in SPORTS)
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    source_table = f"{table} AS source"
    canonical_only = ""
    if table in {"sport_player_positions", "sport_player_traits", "sport_player_season_traits"}:
        canonical_only = """ AND EXISTS (
            SELECT 1 FROM sport_players AS player
             WHERE player.sport_id = source.sport_id
               AND player.player_id = source.player_id
        )"""
    exclusion = ""
    if table == "sport_teams":
        exclusion = f" AND NOT {EXHIBITION_TEAM_SQL}"
    elif table in {"sport_appearances", "sport_player_stints"}:
        exclusion = """ AND EXISTS (
            SELECT 1 FROM sport_teams AS team WHERE team.sport_id=source.sport_id
              AND team.team_id=source.team_id AND team.season=source.season
              AND NOT (
                  replace(lower(team.name), '-', ' ') LIKE '%all star%'
                  OR replace(lower(team.name), '-', ' ') LIKE '%rising star%'
                  OR lower(team.name) = 'world'
                  OR (team.sport_id = 'basketball' AND lower(team.name) IN ('ogs', 'stripes'))
                  OR (team.sport_id = 'basketball' AND lower(team.name) LIKE 'team %')
              )
        )"""
    params = SPORTS
    rows = src.execute(
        f"SELECT {', '.join('source.' + column for column in columns)} FROM {source_table} "
        f"WHERE source.sport_id IN ({placeholders}){canonical_only}{exclusion}",
        params,
    )
    count = 0
    with dst.cursor() as cur:
        with cur.copy(f"COPY {table} ({quoted_columns}) FROM STDIN") as copy:
            for row in rows:
                values = tuple(row)
                # SQLite preserved missing identifiers as empty strings. The
                # Postgres uniqueness constraint correctly treats those as
                # duplicate values, so represent them as SQL NULL instead.
                if table == "sport_players":
                    values = tuple(None if column == "external_id" and value == "" else value for column, value in zip(columns, values))
                copy.write_row(values)
                count += 1
    return count


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required in .env.", file=sys.stderr)
        return 1
    if not SOURCE.exists():
        print(f"Local source is missing: {SOURCE}", file=sys.stderr)
        return 1

    src = sqlite3.connect(SOURCE)
    safe_target = database_url.split("@", 1)[-1]
    print(f"Source: {SOURCE.name}")
    print(f"Target: {safe_target}")
    try:
        with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as dst:
            with dst.cursor() as cur:
                cur.execute("SET default_transaction_read_only = off")
                cur.execute(SCHEMA.read_text(encoding="utf-8"))
                # Baseball and the cross-sport runtime both derive teammate
                # links from appearances. Remove the obsolete pair graph if a
                # previous migration created it.
                cur.execute("DROP TABLE IF EXISTS teammates CASCADE")
                # Foreign-key-safe replacement for only the three incoming sports.
                for table in ("sport_teammates", "sport_player_aliases", "sport_data_provenance", "sport_player_images", "sport_player_positions", "sport_player_stints", "sport_teammate_stint_coverage"):
                    cur.execute(f"DELETE FROM {table} WHERE sport_id = ANY(%s)", (list(SPORTS),))
                for table in reversed([name for name, _ in TABLES]):
                    cur.execute(f"DELETE FROM {table} WHERE sport_id = ANY(%s)", (list(SPORTS),))
                sports_rows = src.execute(
                    "SELECT sport_id, display_name, league_name, first_season, last_season FROM sports WHERE sport_id IN (?, ?, ?)",
                    SPORTS,
                )
                for row in sports_rows:
                    cur.execute(
                        """INSERT INTO sports (sport_id, display_name, league_name, first_season, last_season)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (sport_id) DO UPDATE SET
                             display_name=EXCLUDED.display_name, league_name=EXCLUDED.league_name,
                             first_season=EXCLUDED.first_season, last_season=EXCLUDED.last_season""",
                        tuple(row),
                    )
            dst.commit()

            for table, columns in TABLES:
                started = time.monotonic()
                count = copy_rows(src, dst, table, columns)
                dst.commit()
                print(f"{table:34} {count:>8,} rows  {time.monotonic() - started:5.1f}s")

            image_rows = src.execute(
                """SELECT image.sport_id, image.player_id, image.source_url, image.content_type
                     FROM local_player_images AS image
                     JOIN sport_players AS player
                       ON player.sport_id=image.sport_id AND player.player_id=image.player_id
                    WHERE image.sport_id IN (?, ?, ?) AND image.source_url <> ''""",
                SPORTS,
            )
            started = time.monotonic()
            with dst.cursor() as cur:
                with cur.copy("COPY sport_player_images (sport_id, player_id, source_url, content_type) FROM STDIN") as copy:
                    image_count = 0
                    for row in image_rows:
                        copy.write_row(tuple(row))
                        image_count += 1
            dst.commit()
            print(f"{'sport_player_images':34} {image_count:>8,} rows  {time.monotonic() - started:5.1f}s")

            with dst.cursor() as cur:
                size = cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
            print(f"Supabase database size after import: {size}")
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
