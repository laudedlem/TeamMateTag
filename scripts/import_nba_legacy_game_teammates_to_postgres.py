#!/usr/bin/env python3
"""Import strict NBA teammate proofs for the 2000-01 and 2001-02 gap seasons.

SportsDataverse/ESPN player boxscores begin at season key 2002, so this importer
uses the local NBA-ID PlayerStatistics-derived proof DB only for season keys
2000 and 2001. It coexists with the ESPN importer, which owns 2002-2025.
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
sys.path.insert(0, str(ROOT / "scripts"))

from name_normalize import normalize  # noqa: E402

SOURCE = ROOT / "raw" / "nba_game_teammates" / "nba_game_teammates.sqlite"
SCHEMA = ROOT / "db" / "cross_sport_schema_postgres.sql"
SPORT_ID = "basketball"
SOURCE_NAME = "nba_player_statistics_game_boxscore_legacy_gap"
SEASON_START = 2000
SEASON_END = 2001


def last_name(display_name: str) -> str:
    parts = [part for part in display_name.replace(".", " ").split() if part]
    if len(parts) > 1 and parts[-1].lower() in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts = parts[:-1]
    return parts[-1] if parts else display_name


def first_name(display_name: str) -> str:
    parts = [part for part in display_name.split() if part]
    return parts[0] if parts else display_name


def ensure_tables(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))


def source_summary(src: sqlite3.Connection) -> tuple[int, int, int]:
    games = int(src.execute(
        "SELECT COUNT(*) FROM nba_games WHERE season BETWEEN ? AND ?",
        (SEASON_START, SEASON_END),
    ).fetchone()[0])
    appearances = int(src.execute(
        "SELECT COUNT(*) FROM nba_player_game_appearances WHERE season BETWEEN ? AND ?",
        (SEASON_START, SEASON_END),
    ).fetchone()[0])
    proofs = int(src.execute(
        "SELECT COUNT(*) FROM nba_teammate_game_proofs WHERE season BETWEEN ? AND ?",
        (SEASON_START, SEASON_END),
    ).fetchone()[0])
    return games, appearances, proofs


def copy_appearance_rollups(src: sqlite3.Connection, dst: "psycopg.Connection") -> int:
    rows = src.execute(
        """
        SELECT player_id, external_id, MAX(display_name), team_id, season,
               COUNT(*) AS games_total, MIN(game_date), MAX(game_date)
          FROM nba_player_game_appearances
         WHERE season BETWEEN ? AND ?
         GROUP BY player_id, external_id, team_id, season
         ORDER BY season, team_id, player_id
        """,
        (SEASON_START, SEASON_END),
    )
    count = 0
    with dst.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS tmp_nba_legacy_appearance_rollups")
        cur.execute(
            """
            CREATE TEMP TABLE tmp_nba_legacy_appearance_rollups (
                player_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL,
                games_total INTEGER NOT NULL,
                first_date DATE NOT NULL,
                last_date DATE NOT NULL
            ) ON COMMIT DROP
            """
        )
        with cur.copy(
            "COPY tmp_nba_legacy_appearance_rollups "
            "(player_id, external_id, display_name, team_id, season, games_total, first_date, last_date) "
            "FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
                count += 1
        cur.execute("CREATE INDEX ON tmp_nba_legacy_appearance_rollups (player_id)")
        cur.execute("CREATE INDEX ON tmp_nba_legacy_appearance_rollups (team_id, season)")
    return count


def copy_proofs(src: sqlite3.Connection, dst: "psycopg.Connection") -> int:
    rows = src.execute(
        """
        SELECT player_a_id, player_b_id, team_id, season
          FROM nba_teammate_game_proofs
         WHERE season BETWEEN ? AND ?
         ORDER BY season, team_id, player_a_id, player_b_id
        """,
        (SEASON_START, SEASON_END),
    )
    count = 0
    with dst.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS tmp_nba_legacy_game_teammates")
        cur.execute(
            """
            CREATE TEMP TABLE tmp_nba_legacy_game_teammates (
                player_a_id TEXT NOT NULL,
                player_b_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL
            ) ON COMMIT DROP
            """
        )
        with cur.copy(
            "COPY tmp_nba_legacy_game_teammates "
            "(player_a_id, player_b_id, team_id, season) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
                count += 1
        cur.execute("CREATE INDEX ON tmp_nba_legacy_game_teammates (player_a_id)")
        cur.execute("CREATE INDEX ON tmp_nba_legacy_game_teammates (player_b_id)")
        cur.execute("CREATE INDEX ON tmp_nba_legacy_game_teammates (team_id, season)")
    return count


def ensure_teams(dst: "psycopg.Connection") -> None:
    with dst.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sport_franchises (sport_id, franchise_id, name, active)
            SELECT %s, teams.team_id,
                   MAX(COALESCE(existing.name, teams.team_id)),
                   true
              FROM (SELECT DISTINCT team_id, season FROM tmp_nba_legacy_appearance_rollups) teams
              LEFT JOIN LATERAL (
                    SELECT name
                      FROM sport_teams t
                     WHERE t.sport_id = %s
                       AND t.team_id = teams.team_id
                     ORDER BY ABS(t.season - teams.season)
                     LIMIT 1
              ) existing ON true
             GROUP BY teams.team_id
            ON CONFLICT (sport_id, franchise_id) DO UPDATE
            SET name = EXCLUDED.name,
                active = true
            """,
            (SPORT_ID, SPORT_ID),
        )
        cur.execute(
            """
            INSERT INTO sport_teams (sport_id, team_id, season, franchise_id, name)
            SELECT DISTINCT %s, rollups.team_id, rollups.season, rollups.team_id,
                   COALESCE(existing.name, rollups.team_id)
              FROM tmp_nba_legacy_appearance_rollups rollups
              LEFT JOIN LATERAL (
                    SELECT name
                      FROM sport_teams t
                     WHERE t.sport_id = %s
                       AND t.team_id = rollups.team_id
                     ORDER BY ABS(t.season - rollups.season)
                     LIMIT 1
              ) existing ON true
            ON CONFLICT (sport_id, team_id, season) DO UPDATE
            SET franchise_id = EXCLUDED.franchise_id,
                name = EXCLUDED.name
            """,
            (SPORT_ID, SPORT_ID),
        )


def backfill_missing_players(dst: "psycopg.Connection") -> int:
    with dst.cursor() as cur:
        cur.execute(
            """
            WITH source_players AS (
                SELECT player_id, external_id, MAX(display_name) AS display_name,
                       MIN(season) AS debut_year, MAX(season) AS final_year,
                       SUM(games_total)::integer AS career_games
                  FROM tmp_nba_legacy_appearance_rollups
                 GROUP BY player_id, external_id
            ),
            missing AS (
                SELECT source_players.*
                  FROM source_players
                  LEFT JOIN sport_players p
                    ON p.sport_id = %s
                   AND p.player_id = source_players.player_id
                 WHERE p.player_id IS NULL
            )
            INSERT INTO sport_players
                (sport_id, player_id, external_id, display_name, first_name, last_name,
                 debut_year, final_year, primary_pos)
            SELECT %s, player_id, external_id, display_name, '', '',
                   debut_year, final_year, NULL
              FROM missing
            ON CONFLICT (sport_id, player_id) DO NOTHING
            """,
            (SPORT_ID, SPORT_ID),
        )
        inserted = int(cur.rowcount)
        cur.execute(
            """
            SELECT p.player_id, p.display_name
              FROM sport_players p
             WHERE p.sport_id = %s
               AND (COALESCE(p.first_name, '') = '' OR COALESCE(p.last_name, '') = '')
            """,
            (SPORT_ID,),
        )
        rows = cur.fetchall()
        cur.executemany(
            """
            UPDATE sport_players
               SET first_name = %s,
                   last_name = %s
             WHERE sport_id = %s
               AND player_id = %s
            """,
            [(first_name(name), last_name(name), SPORT_ID, player_id) for player_id, name in rows],
        )
        cur.execute(
            """
            INSERT INTO sport_players_searchable
                (sport_id, player_id, display_name, disambiguation, search_key,
                 last_key, career_games, teammate_count)
            SELECT %s, p.player_id, p.display_name,
                   COALESCE(NULLIF(p.primary_pos, ''), 'NBA') || ', '
                   || COALESCE(p.debut_year::text, '?') || '-'
                   || COALESCE(p.final_year::text, '?'),
                   '',
                   '',
                   COALESCE(career.career_games, 0),
                   0
              FROM sport_players p
              LEFT JOIN (
                    SELECT player_id, SUM(games_total)::integer AS career_games
                      FROM tmp_nba_legacy_appearance_rollups
                     GROUP BY player_id
              ) career ON career.player_id = p.player_id
             WHERE p.sport_id = %s
            ON CONFLICT (sport_id, player_id) DO NOTHING
            """,
            (SPORT_ID, SPORT_ID),
        )
        cur.execute(
            """
            SELECT p.player_id, p.display_name
              FROM sport_players p
              JOIN sport_players_searchable s
                ON s.sport_id = p.sport_id AND s.player_id = p.player_id
             WHERE p.sport_id = %s
               AND (s.search_key = '' OR s.last_key = '')
            """,
            (SPORT_ID,),
        )
        rows = cur.fetchall()
        cur.executemany(
            """
            UPDATE sport_players_searchable
               SET search_key = %s,
                   last_key = %s
             WHERE sport_id = %s
               AND player_id = %s
            """,
            [(normalize(name), normalize(last_name(name)), SPORT_ID, player_id) for player_id, name in rows],
        )
        cur.execute(
            """
            INSERT INTO sport_player_images (sport_id, player_id, source_url)
            SELECT %s, player_id,
                   'https://cdn.nba.com/headshots/nba/latest/1040x760/' || external_id || '.png'
              FROM tmp_nba_legacy_appearance_rollups
             WHERE external_id ~ '^[0-9]+$'
            ON CONFLICT (sport_id, player_id) DO NOTHING
            """,
            (SPORT_ID,),
        )
    print(f"Backfilled {inserted:,} missing Basketball legacy proof players.")
    return inserted


def refresh_rollups(dst: "psycopg.Connection") -> tuple[int, int]:
    with dst.cursor() as cur:
        cur.execute(
            """
            DELETE FROM sport_appearances
             WHERE sport_id = %s
               AND season BETWEEN %s AND %s
            """,
            (SPORT_ID, SEASON_START, SEASON_END),
        )
        removed_appearances = int(cur.rowcount)
        cur.execute(
            """
            DELETE FROM sport_player_stints
             WHERE sport_id = %s
               AND season BETWEEN %s AND %s
            """,
            (SPORT_ID, SEASON_START, SEASON_END),
        )
        cur.execute(
            """
            INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
            SELECT %s, player_id, team_id, season, games_total
              FROM tmp_nba_legacy_appearance_rollups
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET games_total = EXCLUDED.games_total
            """,
            (SPORT_ID,),
        )
        cur.execute(
            """
            INSERT INTO sport_player_stints
                (sport_id, player_id, team_id, season, first_unit, last_unit,
                 first_label, last_label, source)
            SELECT %s, player_id, team_id, season,
                   to_char(first_date, 'YYYYMMDD')::integer,
                   to_char(last_date, 'YYYYMMDD')::integer,
                   first_date::text,
                   last_date::text,
                   %s
              FROM tmp_nba_legacy_appearance_rollups
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET first_unit = EXCLUDED.first_unit,
                last_unit = EXCLUDED.last_unit,
                first_label = EXCLUDED.first_label,
                last_label = EXCLUDED.last_label,
                source = EXCLUDED.source
            """,
            (SPORT_ID, SOURCE_NAME),
        )
        cur.execute(
            """
            WITH careers AS (
                SELECT player_id, SUM(games_total)::integer AS career_games
                  FROM sport_appearances
                 WHERE sport_id = %s
                 GROUP BY player_id
            )
            UPDATE sport_players_searchable s
               SET career_games = careers.career_games,
                   disambiguation = COALESCE(NULLIF(p.primary_pos, ''), 'NBA') || ', '
                                    || COALESCE(p.debut_year::text, '?') || '-'
                                    || COALESCE(p.final_year::text, '?')
              FROM careers
              JOIN sport_players p
                ON p.sport_id = %s AND p.player_id = careers.player_id
             WHERE s.sport_id = %s
               AND s.player_id = careers.player_id
            """,
            (SPORT_ID, SPORT_ID, SPORT_ID),
        )
    return removed_appearances, SEASON_END - SEASON_START + 1


def import_proofs(dst: "psycopg.Connection") -> tuple[int, int]:
    with dst.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15min'")
        cur.execute(
            """
            DELETE FROM sport_teammates
             WHERE sport_id = %s
               AND season BETWEEN %s AND %s
            """,
            (SPORT_ID, SEASON_START, SEASON_END),
        )
        cur.execute(
            """
            INSERT INTO sport_teammates (sport_id, player_a_id, player_b_id, team_id, season)
            SELECT DISTINCT %s, player_a_id, player_b_id, team_id, season
              FROM tmp_nba_legacy_game_teammates
            ON CONFLICT DO NOTHING
            """,
            (SPORT_ID,),
        )
        inserted = int(cur.rowcount)
        cur.execute(
            """
            INSERT INTO sport_teammate_stint_coverage
                (sport_id, season, coverage_type, strict, source, updated_at)
            SELECT %s, season, 'game_boxscore', 1, %s, now()
              FROM generate_series(%s::integer, %s::integer) AS season
            ON CONFLICT (sport_id, season) DO UPDATE
            SET coverage_type = EXCLUDED.coverage_type,
                strict = EXCLUDED.strict,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            (SPORT_ID, SOURCE_NAME, SEASON_START, SEASON_END),
        )
        cur.execute(
            """
            SELECT COUNT(*)
              FROM tmp_nba_legacy_game_teammates proof
             WHERE NOT EXISTS (
                   SELECT 1 FROM sport_players p
                    WHERE p.sport_id = %s
                      AND p.player_id = proof.player_a_id
             )
                OR NOT EXISTS (
                   SELECT 1 FROM sport_players p
                    WHERE p.sport_id = %s
                      AND p.player_id = proof.player_b_id
             )
            """,
            (SPORT_ID, SPORT_ID),
        )
        unmapped = int(cur.fetchone()[0])
        cur.execute(
            """
            WITH teammate_counts AS (
                SELECT player_id, COUNT(DISTINCT teammate_id)::integer AS teammate_count
                  FROM (
                        SELECT player_a_id AS player_id, player_b_id AS teammate_id
                          FROM sport_teammates WHERE sport_id = %s
                        UNION ALL
                        SELECT player_b_id AS player_id, player_a_id AS teammate_id
                          FROM sport_teammates WHERE sport_id = %s
                  ) links
                 GROUP BY player_id
            )
            UPDATE sport_players_searchable ps
               SET teammate_count = COALESCE(tc.teammate_count, 0)
              FROM sport_players p
              LEFT JOIN teammate_counts tc ON tc.player_id = p.player_id
             WHERE ps.sport_id = %s
               AND p.sport_id = ps.sport_id
               AND p.player_id = ps.player_id
            """,
            (SPORT_ID, SPORT_ID, SPORT_ID),
        )
    return inserted, unmapped


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required in .env.", file=sys.stderr)
        return 1
    if not SOURCE.exists():
        print(f"Missing source database: {SOURCE}", file=sys.stderr)
        return 1

    src = sqlite3.connect(SOURCE)
    try:
        games, appearances, proofs = source_summary(src)
        print(
            f"Source legacy gap: {games:,} games; {appearances:,} player-games; "
            f"{proofs:,} proof rows; seasons {SEASON_START}-{SEASON_END}"
        )
        started = time.monotonic()
        with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as dst:
            ensure_tables(dst)
            copied_rollups = copy_appearance_rollups(src, dst)
            copied_proofs = copy_proofs(src, dst)
            ensure_teams(dst)
            backfill_missing_players(dst)
            removed, seasons = refresh_rollups(dst)
            inserted, unmapped = import_proofs(dst)
            dst.commit()
        print(f"Copied {copied_rollups:,} appearance rollups and {copied_proofs:,} proof rows.")
        print(f"Removed {removed:,} old Basketball appearance rows for {seasons} legacy seasons.")
        print(f"Imported {inserted:,} Basketball legacy strict teammate rows.")
        print(f"Unmapped source proof rows: {unmapped:,}")
        print(f"Done in {time.monotonic() - started:.1f}s.")
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
