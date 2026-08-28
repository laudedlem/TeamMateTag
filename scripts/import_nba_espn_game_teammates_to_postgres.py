#!/usr/bin/env python3
"""Import strict NBA game-level teammate proofs into Supabase.

Source rows come from raw/nba_game_teammates/nba_espn_game_teammates.sqlite,
built from SportsDataverse/ESPN regular-season player boxscores. A Basketball
teammate link is valid for covered seasons only when both players logged
positive minutes for the same NBA team in the same regular-season game.
"""
from __future__ import annotations

import os
import csv
import sqlite3
import sys
import time
from collections import Counter
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

SOURCE = ROOT / "raw" / "nba_game_teammates" / "nba_espn_game_teammates.sqlite"
SCHEMA = ROOT / "db" / "cross_sport_schema_postgres.sql"
MANUAL_OVERRIDES = ROOT / "scripts" / "data" / "nba_espn_manual_id_overrides.csv"
SPORT_ID = "basketball"
SOURCE_NAME = "sportsdataverse_espn_nba_player_boxscores"


def ensure_tables(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sport_teammates (
                sport_id TEXT NOT NULL REFERENCES sports(sport_id),
                player_a_id TEXT NOT NULL,
                player_b_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL,
                PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season),
                CHECK (player_a_id < player_b_id),
                FOREIGN KEY (sport_id, player_a_id)
                    REFERENCES sport_players(sport_id, player_id),
                FOREIGN KEY (sport_id, player_b_id)
                    REFERENCES sport_players(sport_id, player_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_teammates_pair "
            "ON sport_teammates(sport_id, player_a_id, player_b_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_teammates_a "
            "ON sport_teammates(sport_id, player_a_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_teammates_b "
            "ON sport_teammates(sport_id, player_b_id)"
        )


def source_summary(src: sqlite3.Connection) -> tuple[int, int, int, int, int]:
    games = int(src.execute("SELECT COUNT(*) FROM nba_games").fetchone()[0])
    appearances = int(src.execute("SELECT COUNT(*) FROM nba_player_game_appearances").fetchone()[0])
    proofs = int(src.execute("SELECT COUNT(*) FROM nba_teammate_game_proofs").fetchone()[0])
    season_start, season_end = src.execute(
        "SELECT MIN(season), MAX(season) FROM nba_games"
    ).fetchone()
    return games, appearances, proofs, int(season_start), int(season_end)


def placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def last_name(display_name: str) -> str:
    parts = [part for part in display_name.replace(".", " ").split() if part]
    if len(parts) > 1 and parts[-1].lower() in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts = parts[:-1]
    return parts[-1] if parts else display_name


def first_name(display_name: str) -> str:
    parts = [part for part in display_name.split() if part]
    return parts[0] if parts else display_name


def copy_appearance_rollups(src: sqlite3.Connection, dst: "psycopg.Connection") -> int:
    rows = src.execute(
        """
        SELECT player_id, external_id, MAX(display_name), team_id, season,
               COUNT(*) AS games_total, MIN(game_date), MAX(game_date)
          FROM nba_player_game_appearances
         GROUP BY player_id, external_id, team_id, season
         ORDER BY season, team_id, player_id
        """
    )
    count = 0
    with dst.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS tmp_nba_appearance_rollups")
        cur.execute(
            """
            CREATE TEMP TABLE tmp_nba_appearance_rollups (
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
            "COPY tmp_nba_appearance_rollups "
            "(player_id, external_id, display_name, team_id, season, games_total, first_date, last_date) "
            "FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
                count += 1
        cur.execute("CREATE INDEX ON tmp_nba_appearance_rollups (player_id)")
        cur.execute("CREATE INDEX ON tmp_nba_appearance_rollups (team_id, season)")
    return count


def copy_proofs(src: sqlite3.Connection, dst: "psycopg.Connection") -> int:
    rows = src.execute(
        """
        SELECT player_a_id, player_b_id, team_id, season
          FROM nba_teammate_game_proofs
         ORDER BY season, team_id, player_a_id, player_b_id
        """
    )
    count = 0
    with dst.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS tmp_nba_game_teammates")
        cur.execute(
            """
            CREATE TEMP TABLE tmp_nba_game_teammates (
                player_a_id TEXT NOT NULL,
                player_b_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL
            ) ON COMMIT DROP
            """
        )
        with cur.copy(
            "COPY tmp_nba_game_teammates "
            "(player_a_id, player_b_id, team_id, season) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
                count += 1
        cur.execute("CREATE INDEX ON tmp_nba_game_teammates (player_a_id)")
        cur.execute("CREATE INDEX ON tmp_nba_game_teammates (player_b_id)")
        cur.execute("CREATE INDEX ON tmp_nba_game_teammates (team_id, season)")
    return count


def ensure_teams(dst: "psycopg.Connection") -> None:
    with dst.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sport_franchises (sport_id, franchise_id, name, active)
            SELECT %s, teams.team_id,
                   MAX(COALESCE(existing.name, teams.team_id)),
                   true
              FROM (SELECT DISTINCT team_id, season FROM tmp_nba_appearance_rollups) teams
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
              FROM tmp_nba_appearance_rollups rollups
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
                  FROM tmp_nba_appearance_rollups
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
            SELECT %s, player_id, external_id, display_name,
                   NULLIF(SPLIT_PART(display_name, ' ', 1), ''),
                   NULLIF(REGEXP_REPLACE(display_name, '^.*\\s', ''), ''),
                   debut_year, final_year, NULL
              FROM missing
            ON CONFLICT (sport_id, player_id) DO NOTHING
            """,
            (SPORT_ID, SPORT_ID),
        )
        inserted = int(cur.rowcount)
        cur.execute(
            """
            INSERT INTO sport_players_searchable
                (sport_id, player_id, display_name, disambiguation, search_key,
                 last_key, career_games, teammate_count)
            SELECT %s, p.player_id, p.display_name,
                   COALESCE(NULLIF(p.primary_pos, ''), 'NBA') || ', '
                   || COALESCE(p.debut_year::text, '?') || '-'
                   || COALESCE(p.final_year::text, '?'),
                   %s,
                   %s,
                   COALESCE(career.career_games, 0),
                   0
              FROM sport_players p
              LEFT JOIN (
                    SELECT player_id, SUM(games_total)::integer AS career_games
                      FROM tmp_nba_appearance_rollups
                     GROUP BY player_id
              ) career ON career.player_id = p.player_id
             WHERE p.sport_id = %s
               AND NOT EXISTS (
                    SELECT 1 FROM sport_players_searchable s
                     WHERE s.sport_id = p.sport_id
                       AND s.player_id = p.player_id
               )
            """,
            (SPORT_ID, "", "", SPORT_ID),
        )
        # psycopg cannot call Python normalize inside SQL, so set search keys
        # for just-inserted rows in a small client-side pass.
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
            [
                (normalize(name), normalize(last_name(name)), SPORT_ID, player_id)
                for player_id, name in rows
            ],
        )
        cur.execute(
            """
            INSERT INTO sport_player_images (sport_id, player_id, source_url)
            SELECT %s, player_id,
                   'https://cdn.nba.com/headshots/nba/latest/1040x760/' || external_id || '.png'
              FROM tmp_nba_appearance_rollups
             WHERE external_id ~ '^[0-9]+$'
            ON CONFLICT (sport_id, player_id) DO NOTHING
            """,
            (SPORT_ID,),
        )
    if inserted:
        print(f"Backfilled {inserted:,} missing Basketball proof players.")
    else:
        print("No missing Basketball proof players to backfill.")
    return inserted


def apply_verified_override_names(dst: "psycopg.Connection") -> int:
    if not MANUAL_OVERRIDES.exists():
        return 0
    rows: list[tuple[str, str, str, str]] = []
    with MANUAL_OVERRIDES.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            status = (row.get("status") or "").strip().lower()
            nba_person_id = (row.get("nba_person_id") or "").strip()
            nba_name = (row.get("nba_name") or "").strip()
            if status == "verified" and nba_person_id and nba_name:
                player_id = f"nba:{nba_person_id}"
                rows.append((nba_name, normalize(nba_name), normalize(last_name(nba_name)), player_id))
    if not rows:
        return 0
    with dst.cursor() as cur:
        cur.executemany(
            """
            UPDATE sport_players
               SET display_name = %s,
                   first_name = %s,
                   last_name = %s
             WHERE sport_id = %s
               AND player_id = %s
            """,
            [
                (name, first_name(name), last_name(name), SPORT_ID, player_id)
                for name, _search, _last, player_id in rows
            ],
        )
        cur.executemany(
            """
            UPDATE sport_players_searchable
               SET display_name = %s,
                   search_key = %s,
                   last_key = %s
             WHERE sport_id = %s
               AND player_id = %s
            """,
            [(name, search, last, SPORT_ID, player_id) for name, search, last, player_id in rows],
        )
    return len(rows)


def refresh_rollups(dst: "psycopg.Connection", season_start: int, season_end: int) -> tuple[int, int]:
    with dst.cursor() as cur:
        cur.execute(
            """
            DELETE FROM sport_appearances
             WHERE sport_id = %s
               AND season BETWEEN %s AND %s
            """,
            (SPORT_ID, season_start, season_end),
        )
        removed_appearances = int(cur.rowcount)
        cur.execute(
            """
            DELETE FROM sport_player_stints
             WHERE sport_id = %s
               AND season BETWEEN %s AND %s
            """,
            (SPORT_ID, season_start, season_end),
        )
        cur.execute(
            """
            INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
            SELECT %s, player_id, team_id, season, games_total
              FROM tmp_nba_appearance_rollups
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
              FROM tmp_nba_appearance_rollups
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
            WITH source_careers AS (
                SELECT player_id, MIN(season) AS debut_year, MAX(season) AS final_year
                  FROM tmp_nba_appearance_rollups
                 GROUP BY player_id
            )
            UPDATE sport_players p
               SET debut_year = LEAST(COALESCE(p.debut_year, source_careers.debut_year), source_careers.debut_year),
                   final_year = GREATEST(COALESCE(p.final_year, source_careers.final_year), source_careers.final_year)
              FROM source_careers
             WHERE p.sport_id = %s
               AND p.player_id = source_careers.player_id
            """,
            (SPORT_ID,),
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
    return removed_appearances, season_end - season_start + 1


def import_proofs(dst: "psycopg.Connection", season_start: int, season_end: int) -> tuple[int, int]:
    with dst.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15min'")
        cur.execute("DELETE FROM sport_teammates WHERE sport_id = %s", (SPORT_ID,))
        cur.execute(
            """
            INSERT INTO sport_teammates (sport_id, player_a_id, player_b_id, team_id, season)
            SELECT DISTINCT %s, player_a_id, player_b_id, team_id, season
              FROM tmp_nba_game_teammates
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
            (SPORT_ID, SOURCE_NAME, season_start, season_end),
        )
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
        cur.execute(
            """
            SELECT COUNT(*)
              FROM tmp_nba_game_teammates proof
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
    return inserted, unmapped


def smoke_checks(dst: "psycopg.Connection") -> None:
    checks = [
        ("LeBron James", "Dwyane Wade"),
        ("Kevin Durant", "Stephen Curry"),
        ("Nene Hilario", "James Harden"),
        ("Kenyon Martin Jr.", "James Harden"),
    ]
    with dst.cursor() as cur:
        for first, second in checks:
            cur.execute(
                """
                SELECT a.player_id, b.player_id
                  FROM sport_players a
                  JOIN sport_players b ON b.sport_id = a.sport_id
                 WHERE a.sport_id = %s
                   AND a.display_name = %s
                   AND b.display_name = %s
                 LIMIT 1
                """,
                (SPORT_ID, first, second),
            )
            row = cur.fetchone()
            if not row:
                print(f"Smoke skipped: {first} / {second} not both found.")
                continue
            cur.execute(
                """
                SELECT team_id, season
                  FROM sport_teammates
                 WHERE sport_id = %s
                   AND player_a_id = LEAST(%s, %s)
                   AND player_b_id = GREATEST(%s, %s)
                 ORDER BY season, team_id
                 LIMIT 3
                """,
                (SPORT_ID, row[0], row[1], row[0], row[1]),
            )
            print(f"Smoke {first} / {second}: {cur.fetchall()}")


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
        games, appearances, proofs, season_start, season_end = source_summary(src)
        print(
            f"Source: {games:,} games; {appearances:,} player-games; "
            f"{proofs:,} proof rows; seasons {season_start}-{season_end}"
        )
        started = time.monotonic()
        with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as dst:
            ensure_tables(dst)
            copied_rollups = copy_appearance_rollups(src, dst)
            copied_proofs = copy_proofs(src, dst)
            ensure_teams(dst)
            backfill_missing_players(dst)
            renamed = apply_verified_override_names(dst)
            removed, seasons = refresh_rollups(dst, season_start, season_end)
            inserted, unmapped = import_proofs(dst, season_start, season_end)
            smoke_checks(dst)
            dst.commit()
        print(f"Copied {copied_rollups:,} appearance rollups and {copied_proofs:,} proof rows.")
        print(f"Applied {renamed:,} verified Basketball identity names.")
        print(f"Removed {removed:,} old Basketball appearance rows for {seasons} covered seasons.")
        print(f"Imported {inserted:,} Basketball strict teammate rows.")
        print(f"Unmapped source proof rows: {unmapped:,}")
        print(f"Done in {time.monotonic() - started:.1f}s.")
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
