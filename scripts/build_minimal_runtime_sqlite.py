#!/usr/bin/env python3
"""Build the smallest local runtime database needed by TeamMateTag.

This is an offline-only compiler. It reads local raw/source SQLite files and
catalog tables, then writes a separate refined runtime SQLite database under
raw/runtime_compact/. Raw game, boxscore, and snap rows are intentionally not
copied.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sqlite3
import sys
import unicodedata
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
BASEBALL_DB = ROOT / "db" / "base2nerdle.sqlite"
SPORT_CATALOG_DB = ROOT / "db" / "teammatetag_local.sqlite"
MLB_PROOFS_DB = ROOT / "raw" / "mlb_game_teammates" / "mlb_game_teammates_v2.sqlite"
MLB_LIVE_RUNTIME_DIR = ROOT / "raw" / "mlb_live_runtime"
SPORT_LIVE_RUNTIME_DIRS = {
    "basketball": ROOT / "raw" / "basketball_live_runtime",
    "hockey": ROOT / "raw" / "hockey_live_runtime",
    "football": ROOT / "raw" / "football_live_runtime",
}
NBA_PROOFS_DB = ROOT / "raw" / "nba_game_teammates" / "nba_espn_game_teammates.sqlite"
NHL_PROOFS_DB = ROOT / "raw" / "nhl_game_teammates" / "nhl_game_teammates.sqlite"
NFL_RUNTIME_DB = ROOT / "raw" / "nfl_game_teammates" / "nfl_compact_runtime_int.sqlite"
HEADSHOT_REGISTRY = ROOT / "raw" / "headshot_registry_2026-08-15.csv"
BASEBALL_HEADSHOT_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "baseball_headshots.sqlite"
BASKETBALL_HEADSHOT_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "basketball_headshots.sqlite"
HOCKEY_HEADSHOT_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "hockey_headshots.sqlite"
FOOTBALL_HEADSHOT_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "football_headshots.sqlite"
LEGACY_BASEBALL_HEADSHOT_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "baseball_ootp_headshots.sqlite"
LAHMAN_ZIP = ROOT / "raw" / "lahman_1871-2025_csv.zip"


def normalize(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", value.lower())


def bad_runtime_team_sql(alias: str = "t") -> str:
    normalized_name = f"replace(lower(COALESCE({alias}.name, '')), '-', ' ')"
    raw_name = f"lower(COALESCE({alias}.name, ''))"
    return f"""
        ({alias}.scope = 'baseball' AND {alias}.team_id IN ('AL', 'NL'))
        OR {normalized_name} LIKE '%all star%'
        OR {normalized_name} LIKE '%rising star%'
        OR {normalized_name} LIKE '%young star%'
        OR {normalized_name} LIKE '%rookie challenge%'
        OR {raw_name} IN ('world', 'usa')
        OR ({alias}.scope = 'basketball' AND {raw_name} IN ('ogs', 'stripes'))
        OR ({alias}.scope = 'basketball' AND {raw_name} LIKE 'team %')
    """


def db_size_mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024 if path.exists() else 0.0


def require(paths: list[Path]) -> None:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise SystemExit("missing required local source files:\n" + "\n".join(missing))


def attach(conn: sqlite3.Connection) -> None:
    conn.execute("ATTACH DATABASE ? AS baseball", (str(BASEBALL_DB),))
    conn.execute("ATTACH DATABASE ? AS sportcat", (str(SPORT_CATALOG_DB),))
    conn.execute("ATTACH DATABASE ? AS mlbraw", (str(MLB_PROOFS_DB),))
    for index, live_db in enumerate(sorted(MLB_LIVE_RUNTIME_DIR.glob("mlb_live_*.sqlite"))):
        conn.execute(f"ATTACH DATABASE ? AS mlblive{index}", (str(live_db),))
    for sport, folder in SPORT_LIVE_RUNTIME_DIRS.items():
        for index, live_db in enumerate(sorted(folder.glob(f"{sport}_live_*.sqlite"))):
            conn.execute(f"ATTACH DATABASE ? AS {sport}live{index}", (str(live_db),))
    conn.execute("ATTACH DATABASE ? AS nbaraw", (str(NBA_PROOFS_DB),))
    conn.execute("ATTACH DATABASE ? AS nhlraw", (str(NHL_PROOFS_DB),))
    conn.execute("ATTACH DATABASE ? AS nflrt", (str(NFL_RUNTIME_DB),))


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        CREATE TABLE runtime_players (
            scope TEXT NOT NULL,
            player_id TEXT NOT NULL,
            external_id TEXT,
            display_name TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            debut_year INTEGER,
            final_year INTEGER,
            primary_pos TEXT,
            search_key TEXT NOT NULL,
            last_key TEXT NOT NULL,
            career_games INTEGER NOT NULL DEFAULT 0,
            teammate_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, player_id)
        ) WITHOUT ROWID;

        CREATE TABLE runtime_teams (
            scope TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            franchise_id TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (scope, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE runtime_player_team_seasons (
            scope TEXT NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            games_total INTEGER NOT NULL DEFAULT 0,
            games_pitched INTEGER NOT NULL DEFAULT 0,
            games_batted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, player_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE runtime_positions (
            scope TEXT NOT NULL,
            player_id TEXT NOT NULL,
            position TEXT NOT NULL,
            games INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, player_id, position)
        ) WITHOUT ROWID;

        CREATE TABLE runtime_headshots (
            scope TEXT NOT NULL,
            player_id TEXT NOT NULL,
            source_url TEXT,
            fallback_url TEXT,
            provider TEXT,
            status TEXT NOT NULL DEFAULT 'verified',
            PRIMARY KEY (scope, player_id)
        ) WITHOUT ROWID;

        CREATE TABLE runtime_coverage (
            scope TEXT NOT NULL,
            season INTEGER NOT NULL,
            coverage_type TEXT NOT NULL,
            strict INTEGER NOT NULL DEFAULT 1,
            source TEXT,
            PRIMARY KEY (scope, season)
        ) WITHOUT ROWID;

        CREATE TABLE sport_player_stints (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            first_unit INTEGER NOT NULL DEFAULT 1,
            last_unit INTEGER NOT NULL DEFAULT 1,
            first_label TEXT,
            last_label TEXT,
            source TEXT,
            PRIMARY KEY (sport_id, player_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE runtime_player_traits (
            scope TEXT NOT NULL,
            player_id TEXT NOT NULL,
            career_games INTEGER NOT NULL DEFAULT 0,
            career_points INTEGER NOT NULL DEFAULT 0,
            career_goals INTEGER NOT NULL DEFAULT 0,
            career_assists INTEGER NOT NULL DEFAULT 0,
            career_touchdowns INTEGER NOT NULL DEFAULT 0,
            passing_touchdowns INTEGER NOT NULL DEFAULT 0,
            rushing_touchdowns INTEGER NOT NULL DEFAULT 0,
            receiving_touchdowns INTEGER NOT NULL DEFAULT 0,
            career_sacks REAL NOT NULL DEFAULT 0,
            career_interceptions INTEGER NOT NULL DEFAULT 0,
            all_star_count INTEGER NOT NULL DEFAULT 0,
            mvp_count INTEGER NOT NULL DEFAULT 0,
            roty_count INTEGER NOT NULL DEFAULT 0,
            championship_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, player_id)
        ) WITHOUT ROWID;

        CREATE TABLE runtime_player_season_peaks (
            scope TEXT NOT NULL,
            player_id TEXT NOT NULL,
            peak_points INTEGER NOT NULL DEFAULT 0,
            peak_goals INTEGER NOT NULL DEFAULT 0,
            peak_assists INTEGER NOT NULL DEFAULT 0,
            peak_touchdowns INTEGER NOT NULL DEFAULT 0,
            peak_passing_touchdowns INTEGER NOT NULL DEFAULT 0,
            peak_rushing_touchdowns INTEGER NOT NULL DEFAULT 0,
            peak_receiving_touchdowns INTEGER NOT NULL DEFAULT 0,
            peak_sacks REAL NOT NULL DEFAULT 0,
            peak_interceptions INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, player_id)
        ) WITHOUT ROWID;

        CREATE TABLE compact_player_keys (
            player_key INTEGER PRIMARY KEY,
            scope TEXT NOT NULL,
            player_id TEXT NOT NULL,
            UNIQUE (scope, player_id)
        );

        CREATE TABLE compact_team_keys (
            team_key INTEGER PRIMARY KEY,
            scope TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            UNIQUE (scope, team_id, season)
        );

        CREATE TABLE teammate_team_seasons (
            scope TEXT NOT NULL,
            player_a_key INTEGER NOT NULL,
            player_b_key INTEGER NOT NULL,
            team_key INTEGER NOT NULL,
            PRIMARY KEY (scope, player_a_key, player_b_key, team_key)
        ) WITHOUT ROWID;

        CREATE TABLE player_playoff_traits (
            player_id TEXT PRIMARY KEY,
            birth_country TEXT,
            is_japanese INTEGER NOT NULL DEFAULT 0,
            is_cuban INTEGER NOT NULL DEFAULT 0,
            is_canadian INTEGER NOT NULL DEFAULT 0,
            mvp_count INTEGER NOT NULL DEFAULT 0,
            roty_count INTEGER NOT NULL DEFAULT 0,
            gold_glove_count INTEGER NOT NULL DEFAULT 0,
            triple_crown_count INTEGER NOT NULL DEFAULT 0,
            career_hr INTEGER NOT NULL DEFAULT 0,
            world_series_rings INTEGER NOT NULL DEFAULT 0,
            team_count INTEGER NOT NULL DEFAULT 0,
            franchise_count INTEGER NOT NULL DEFAULT 0,
            season_count INTEGER NOT NULL DEFAULT 0,
            hound_dog_eligible INTEGER NOT NULL DEFAULT 0,
            journeyman_eligible INTEGER NOT NULL DEFAULT 0
        ) WITHOUT ROWID;

        CREATE TABLE player_powerup_qualifications (
            powerup_key TEXT NOT NULL,
            franchise_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            PRIMARY KEY (powerup_key, franchise_id, team_id, season, player_id)
        ) WITHOUT ROWID;

        CREATE TABLE baseball_player_positions (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            position TEXT NOT NULL,
            games INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (sport_id, player_id, position)
        ) WITHOUT ROWID;
        """
    )


def source_table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    try:
        return bool(
            conn.execute(
                f"SELECT 1 FROM {schema}.sqlite_master WHERE type IN ('table', 'view') AND name = ?",
                (table,),
            ).fetchone()
        )
    except sqlite3.OperationalError:
        return False


def attached_mlb_live_schemas(conn: sqlite3.Connection) -> list[str]:
    return [
        name
        for _seq, name, _file in conn.execute("PRAGMA database_list")
        if name.startswith("mlblive")
    ]


def attached_sport_live_schemas(conn: sqlite3.Connection, sport: str) -> list[str]:
    prefix = f"{sport}live"
    return [
        name
        for _seq, name, _file in conn.execute("PRAGMA database_list")
        if name.startswith(prefix)
    ]


def copy_baseball_catalog(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        INSERT INTO runtime_teams
        SELECT 'baseball', team_id, season, franchise_id, COALESCE(name, team_id)
          FROM baseball.teams
         WHERE season >= 2000;

        INSERT INTO runtime_player_team_seasons
        SELECT 'baseball', player_id, team_id, season, games_total,
               games_pitched, games_batted
          FROM baseball.appearances
         WHERE season >= 2000;

        INSERT INTO runtime_positions
        SELECT 'baseball', player_id, position, games
          FROM sportcat.sport_player_positions
         WHERE sport_id = 'baseball';

        INSERT INTO baseball_player_positions
        SELECT * FROM sportcat.sport_player_positions
         WHERE sport_id = 'baseball';

        INSERT INTO runtime_coverage
        SELECT 'baseball', season, coverage_type, strict, source
          FROM baseball.teammate_stint_coverage
         WHERE season >= 2000;
        """
    )
    rows = conn.execute(
        """
        SELECT p.player_id, p.mlbam_id, p.name_first, p.name_last, p.debut_year,
               p.final_year, p.primary_pos, s.display_name, s.disambiguation,
               s.search_key, s.last_key, s.career_games, s.teammate_count
          FROM baseball.players p
          LEFT JOIN baseball.players_searchable s ON s.player_id = p.player_id
         WHERE EXISTS (
               SELECT 1 FROM baseball.appearances a
                WHERE a.player_id = p.player_id AND a.season >= 2000
         )
        """
    ).fetchall()
    conn.executemany(
        """
        INSERT INTO runtime_players
            (scope, player_id, external_id, display_name, first_name, last_name,
             debut_year, final_year, primary_pos, search_key, last_key,
             career_games, teammate_count)
        VALUES ('baseball', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                pid,
                str(mlbam_id) if mlbam_id is not None else None,
                display or " ".join(part for part in (first, last) if part).strip() or pid,
                first,
                last,
                debut,
                final,
                pos,
                search_key or normalize(f"{first or ''} {last or ''}"),
                last_key or normalize(last or first or pid),
                int(career_games or 0),
                int(teammate_count or 0),
            )
            for pid, mlbam_id, first, last, debut, final, pos, display, _disambig,
            search_key, last_key, career_games, teammate_count in rows
        ],
    )
    for live_schema in attached_mlb_live_schemas(conn):
        if not source_table_exists(conn, live_schema, "appearances"):
            continue
        conn.executescript(
            f"""
            INSERT OR REPLACE INTO runtime_teams
            SELECT 'baseball', team_id, season, franchise_id, COALESCE(name, team_id)
              FROM {live_schema}.teams;

            INSERT OR REPLACE INTO runtime_player_team_seasons
            SELECT 'baseball', player_id, team_id, season, games_total,
                   games_pitched, games_batted
              FROM {live_schema}.appearances;
            """
        )
        live_rows = conn.execute(
            f"""
            SELECT p.player_id, p.mlbam_id, p.name_first, p.name_last,
                   p.debut_year, p.final_year, p.primary_pos, s.display_name,
                   s.search_key, s.last_key, s.career_games, s.teammate_count
              FROM {live_schema}.players p
              LEFT JOIN {live_schema}.players_searchable s ON s.player_id = p.player_id
             WHERE EXISTS (
                   SELECT 1 FROM {live_schema}.appearances a
                    WHERE a.player_id = p.player_id
             )
            """
        ).fetchall()
        conn.executemany(
            """
            INSERT INTO runtime_players
                (scope, player_id, external_id, display_name, first_name, last_name,
                 debut_year, final_year, primary_pos, search_key, last_key,
                 career_games, teammate_count)
            VALUES ('baseball', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, player_id) DO UPDATE SET
                external_id = COALESCE(excluded.external_id, runtime_players.external_id),
                display_name = excluded.display_name,
                first_name = COALESCE(excluded.first_name, runtime_players.first_name),
                last_name = COALESCE(excluded.last_name, runtime_players.last_name),
                debut_year = COALESCE(runtime_players.debut_year, excluded.debut_year),
                final_year = CASE
                    WHEN runtime_players.final_year IS NULL THEN excluded.final_year
                    WHEN excluded.final_year IS NULL THEN runtime_players.final_year
                    WHEN excluded.final_year > runtime_players.final_year THEN excluded.final_year
                    ELSE runtime_players.final_year
                END,
                primary_pos = COALESCE(runtime_players.primary_pos, excluded.primary_pos),
                search_key = COALESCE(NULLIF(excluded.search_key, ''), runtime_players.search_key),
                last_key = COALESCE(NULLIF(excluded.last_key, ''), runtime_players.last_key),
                career_games = MAX(runtime_players.career_games, excluded.career_games),
                teammate_count = MAX(runtime_players.teammate_count, excluded.teammate_count)
            """,
            [
                (
                    pid,
                    str(mlbam_id) if mlbam_id is not None else None,
                    display or " ".join(part for part in (first, last) if part).strip() or pid,
                    first,
                    last,
                    debut,
                    final,
                    pos,
                    search_key or normalize(f"{first or ''} {last or ''}"),
                    last_key or normalize(last or first or pid),
                    int(career_games or 0),
                    int(teammate_count or 0),
                )
                for pid, mlbam_id, first, last, debut, final, pos, display,
                search_key, last_key, career_games, teammate_count in live_rows
            ],
        )


def copy_cross_sport_catalog(conn: sqlite3.Connection, sport: str, source_schema: str) -> None:
    conn.execute(
        f"""
        INSERT OR REPLACE INTO runtime_teams
        SELECT sport_id, team_id, season, franchise_id, name
          FROM {source_schema}.sport_teams
         WHERE sport_id = ?
           AND season >= 2000
        """,
        (sport,),
    )
    conn.execute(
        f"""
        INSERT OR REPLACE INTO runtime_player_team_seasons
        SELECT sport_id, player_id, team_id, season, games_total, 0, 0
          FROM {source_schema}.sport_appearances
         WHERE sport_id = ?
           AND season >= 2000
        """,
        (sport,),
    )
    conn.execute(
        f"""
        INSERT OR REPLACE INTO runtime_positions
        SELECT sport_id, player_id, position, games
          FROM {source_schema}.sport_player_positions
         WHERE sport_id = ?
        """,
        (sport,),
    )
    conn.execute(
        f"""
        INSERT OR REPLACE INTO runtime_coverage
        SELECT sport_id, season, coverage_type, strict, source
          FROM {source_schema}.sport_teammate_stint_coverage
         WHERE sport_id = ?
           AND season >= 2000
        """,
        (sport,),
    )
    if source_table_exists(conn, source_schema, "sport_player_stints"):
        conn.execute(
            f"""
            INSERT OR REPLACE INTO sport_player_stints
                (sport_id, player_id, team_id, season, first_unit, last_unit,
                 first_label, last_label, source)
            SELECT sport_id, player_id, team_id, season, first_unit, last_unit,
                   first_label, last_label, source
              FROM {source_schema}.sport_player_stints
             WHERE sport_id = ?
               AND season >= 2000
            """,
            (sport,),
        )
    trait_schema = source_schema if source_table_exists(conn, source_schema, "sport_player_traits") else "sportcat"
    if source_table_exists(conn, trait_schema, "sport_player_traits"):
        conn.execute(
            f"""
            INSERT OR REPLACE INTO runtime_player_traits
            SELECT sport_id, player_id, career_games, career_points, career_goals,
                   career_assists, career_touchdowns, passing_touchdowns,
                   rushing_touchdowns, receiving_touchdowns, career_sacks,
                   career_interceptions, all_star_count, mvp_count, roty_count,
                   championship_count
              FROM {trait_schema}.sport_player_traits
             WHERE sport_id = ?
            """,
            (sport,),
        )
    peak_schema = source_schema if source_table_exists(conn, source_schema, "sport_player_season_traits") else "sportcat"
    if source_table_exists(conn, peak_schema, "sport_player_season_traits"):
        conn.execute(
            f"""
            INSERT OR REPLACE INTO runtime_player_season_peaks
            SELECT sport_id, player_id, MAX(points), MAX(goals), MAX(assists),
                   MAX(touchdowns), MAX(passing_touchdowns), MAX(rushing_touchdowns),
                   MAX(receiving_touchdowns), MAX(sacks), MAX(interceptions)
              FROM {peak_schema}.sport_player_season_traits
             WHERE sport_id = ?
             GROUP BY sport_id, player_id
            """,
            (sport,),
        )
    image_table = "sport_player_images" if source_table_exists(conn, source_schema, "sport_player_images") else "local_player_images"
    if source_table_exists(conn, source_schema, image_table):
        conn.execute(
            f"""
            INSERT OR REPLACE INTO runtime_headshots
                (scope, player_id, source_url, fallback_url, provider, status)
            SELECT sport_id, player_id, source_url, NULL, 'catalog', 'verified'
              FROM {source_schema}.{image_table}
             WHERE sport_id = ?
               AND source_url <> ''
            """,
            (sport,),
        )
    rows = conn.execute(
        f"""
        SELECT p.player_id, p.external_id, p.display_name, p.first_name,
               p.last_name, p.debut_year, p.final_year, p.primary_pos,
               s.search_key, s.last_key, s.career_games, s.teammate_count
          FROM {source_schema}.sport_players p
          LEFT JOIN {source_schema}.sport_players_searchable s
            ON s.sport_id = p.sport_id AND s.player_id = p.player_id
         WHERE p.sport_id = ?
           AND EXISTS (
                SELECT 1 FROM {source_schema}.sport_appearances a
                 WHERE a.sport_id = p.sport_id
                   AND a.player_id = p.player_id
                   AND a.season >= 2000
           )
        """,
        (sport,),
    ).fetchall()
    conn.executemany(
        """
        INSERT OR REPLACE INTO runtime_players
            (scope, player_id, external_id, display_name, first_name, last_name,
             debut_year, final_year, primary_pos, search_key, last_key,
             career_games, teammate_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                sport,
                pid,
                external_id,
                display or " ".join(part for part in (first, last) if part).strip() or pid,
                first,
                last,
                debut,
                final,
                pos,
                search_key or normalize(display or f"{first or ''} {last or ''}"),
                last_key or normalize(last or display or pid),
                int(career_games or 0),
                int(teammate_count or 0),
            )
            for pid, external_id, display, first, last, debut, final, pos,
            search_key, last_key, career_games, teammate_count in rows
        ],
    )
    copy_live_cross_sport(conn, sport, source_schema)


def copy_live_cross_sport(conn: sqlite3.Connection, sport: str, source_schema: str) -> None:
    baseline_max_season = conn.execute(
        f"""
        SELECT COALESCE(MAX(season), 0)
          FROM {source_schema}.sport_appearances
         WHERE sport_id = ?
        """,
        (sport,),
    ).fetchone()[0]
    for live_schema in attached_sport_live_schemas(conn, sport):
        if not source_table_exists(conn, live_schema, "sport_appearances"):
            continue
        conn.executescript(
            f"""
            INSERT OR REPLACE INTO runtime_teams
            SELECT sport_id, team_id, season, franchise_id, name
              FROM {live_schema}.sport_teams
             WHERE sport_id = '{sport}'
               AND season >= 2000;

            INSERT OR REPLACE INTO runtime_player_team_seasons
            SELECT sport_id, player_id, team_id, season, games_total, 0, 0
              FROM {live_schema}.sport_appearances
             WHERE sport_id = '{sport}'
               AND season >= 2000;

            INSERT OR REPLACE INTO runtime_positions
            SELECT sport_id, player_id, position, games
              FROM {live_schema}.sport_player_positions
             WHERE sport_id = '{sport}';

            INSERT OR REPLACE INTO runtime_coverage
            SELECT sport_id, season, coverage_type, strict, source
              FROM {live_schema}.sport_teammate_stint_coverage
             WHERE sport_id = '{sport}'
               AND season >= 2000;
            """
        )
        if source_table_exists(conn, live_schema, "sport_player_images"):
            conn.execute(
                f"""
                INSERT OR REPLACE INTO runtime_headshots
                    (scope, player_id, source_url, fallback_url, provider, status)
                SELECT sport_id, player_id, source_url, NULL, 'live_compact', 'verified'
                  FROM {live_schema}.sport_player_images
                 WHERE sport_id = ?
                   AND source_url <> ''
                """,
                (sport,),
            )
        rows = conn.execute(
            f"""
            SELECT p.player_id, p.external_id, p.display_name, p.first_name,
                   p.last_name, p.debut_year, p.final_year, p.primary_pos,
                   s.search_key, s.last_key, s.career_games, s.teammate_count
              FROM {live_schema}.sport_players p
              LEFT JOIN {live_schema}.sport_players_searchable s
                ON s.sport_id = p.sport_id AND s.player_id = p.player_id
             WHERE p.sport_id = ?
               AND EXISTS (
                    SELECT 1 FROM {live_schema}.sport_appearances a
                     WHERE a.sport_id = p.sport_id
                       AND a.player_id = p.player_id
                       AND a.season >= 2000
               )
            """,
            (sport,),
        ).fetchall()
        conn.executemany(
            """
            INSERT INTO runtime_players
                (scope, player_id, external_id, display_name, first_name, last_name,
                 debut_year, final_year, primary_pos, search_key, last_key,
                 career_games, teammate_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, player_id) DO UPDATE SET
                external_id = COALESCE(excluded.external_id, runtime_players.external_id),
                display_name = excluded.display_name,
                first_name = COALESCE(excluded.first_name, runtime_players.first_name),
                last_name = COALESCE(excluded.last_name, runtime_players.last_name),
                debut_year = COALESCE(runtime_players.debut_year, excluded.debut_year),
                final_year = CASE
                    WHEN runtime_players.final_year IS NULL THEN excluded.final_year
                    WHEN excluded.final_year IS NULL THEN runtime_players.final_year
                    WHEN excluded.final_year > runtime_players.final_year THEN excluded.final_year
                    ELSE runtime_players.final_year
                END,
                primary_pos = COALESCE(runtime_players.primary_pos, excluded.primary_pos),
                search_key = COALESCE(NULLIF(excluded.search_key, ''), runtime_players.search_key),
                last_key = COALESCE(NULLIF(excluded.last_key, ''), runtime_players.last_key),
                career_games = MAX(runtime_players.career_games, excluded.career_games),
                teammate_count = MAX(runtime_players.teammate_count, excluded.teammate_count)
            """,
            [
                (
                    sport,
                    pid,
                    external_id,
                    display or " ".join(part for part in (first, last) if part).strip() or pid,
                    first,
                    last,
                    debut,
                    final,
                    pos,
                    search_key or normalize(display or f"{first or ''} {last or ''}"),
                    last_key or normalize(last or display or pid),
                    int(career_games or 0),
                    int(teammate_count or 0),
                )
                for pid, external_id, display, first, last, debut, final, pos,
                search_key, last_key, career_games, teammate_count in rows
            ],
        )
        if source_table_exists(conn, live_schema, "sport_player_season_traits"):
            trait_rows = conn.execute(
                f"""
                SELECT player_id,
                       SUM(games), SUM(points), SUM(goals), SUM(assists),
                       MAX(points), MAX(goals), MAX(assists)
                  FROM {live_schema}.sport_player_season_traits
                 WHERE sport_id = ?
                   AND season > ?
                 GROUP BY player_id
                """,
                (sport, int(baseline_max_season or 0)),
            ).fetchall()
            conn.executemany(
                """
                INSERT INTO runtime_player_traits
                    (scope, player_id, career_games, career_points, career_goals, career_assists)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, player_id) DO UPDATE SET
                    career_games = runtime_player_traits.career_games + excluded.career_games,
                    career_points = runtime_player_traits.career_points + excluded.career_points,
                    career_goals = runtime_player_traits.career_goals + excluded.career_goals,
                    career_assists = runtime_player_traits.career_assists + excluded.career_assists
                """,
                [
                    (sport, pid, int(games or 0), int(points or 0), int(goals or 0), int(assists or 0))
                    for pid, games, points, goals, assists, _peak_points, _peak_goals, _peak_assists in trait_rows
                ],
            )
            conn.executemany(
                """
                INSERT INTO runtime_player_season_peaks
                    (scope, player_id, peak_points, peak_goals, peak_assists)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope, player_id) DO UPDATE SET
                    peak_points = MAX(runtime_player_season_peaks.peak_points, excluded.peak_points),
                    peak_goals = MAX(runtime_player_season_peaks.peak_goals, excluded.peak_goals),
                    peak_assists = MAX(runtime_player_season_peaks.peak_assists, excluded.peak_assists)
                """,
                [
                    (sport, pid, int(peak_points or 0), int(peak_goals or 0), int(peak_assists or 0))
                    for pid, _games, _points, _goals, _assists, peak_points, peak_goals, peak_assists in trait_rows
                ],
            )


def open_lahman_csv(zf: zipfile.ZipFile, name: str):
    return csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="utf-8-sig", newline=""))


def build_baseball_playoff_support(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute("DELETE FROM player_playoff_traits")
    conn.execute("DELETE FROM player_powerup_qualifications")
    player_ids = {
        row[0]
        for row in conn.execute("SELECT player_id FROM runtime_players WHERE scope = 'baseball'")
    }
    team_to_franchise = {
        (team_id, int(season)): franchise_id
        for team_id, season, franchise_id in conn.execute(
            "SELECT team_id, season, franchise_id FROM runtime_teams WHERE scope = 'baseball'"
        )
    }
    appearances = conn.execute(
        """
        SELECT player_id, team_id, season
          FROM runtime_player_team_seasons
         WHERE scope = 'baseball'
        """
    ).fetchall()
    team_counts: dict[str, set[str]] = defaultdict(set)
    franchise_counts: dict[str, set[str]] = defaultdict(set)
    season_counts: dict[str, set[int]] = defaultdict(set)
    for player_id, team_id, season in appearances:
        team_counts[player_id].add(team_id)
        season_counts[player_id].add(int(season))
        franchise_id = team_to_franchise.get((team_id, int(season)))
        if franchise_id:
            franchise_counts[player_id].add(franchise_id)

    birth_country: dict[str, str] = {}
    mvp_count = Counter()
    roty_count = Counter()
    gold_glove_count = Counter()
    triple_crown_count = Counter()
    career_hr = Counter()
    ws_rings = Counter()
    powerups: set[tuple[str, str, str, str, int]] = set()
    champions: set[tuple[str, int]] = set()
    max_lahman_batting_year = 0

    if LAHMAN_ZIP.exists():
        with zipfile.ZipFile(LAHMAN_ZIP) as zf:
            for row in open_lahman_csv(zf, "lahman_1871-2025_csv/People.csv"):
                player_id = row["playerID"]
                if player_id in player_ids:
                    birth_country[player_id] = (row.get("birthCountry") or "").strip()

            for row in open_lahman_csv(zf, "lahman_1871-2025_csv/Batting.csv"):
                player_id = row["playerID"]
                year = int(row.get("yearID") or 0)
                max_lahman_batting_year = max(max_lahman_batting_year, year)
                if player_id not in player_ids:
                    continue
                home_runs = int(row.get("HR") or 0)
                career_hr[player_id] += home_runs
                franchise_id = team_to_franchise.get((row["teamID"], year))
                if year >= 2000 and home_runs >= 40 and franchise_id:
                    powerups.add((player_id, "bubblegum", franchise_id, row["teamID"], year))

            for row in open_lahman_csv(zf, "lahman_1871-2025_csv/Pitching.csv"):
                player_id = row["playerID"]
                year = int(row.get("yearID") or 0)
                if player_id not in player_ids:
                    continue
                strikeouts = int(row.get("SO") or 0)
                franchise_id = team_to_franchise.get((row["teamID"], year))
                if year >= 2000 and strikeouts >= 200 and franchise_id:
                    powerups.add((player_id, "pine_tar", franchise_id, row["teamID"], year))

            appearances_by_player_year: dict[tuple[str, int], set[str]] = defaultdict(set)
            for player_id, team_id, season in appearances:
                appearances_by_player_year[(player_id, int(season))].add(team_id)
            for row in open_lahman_csv(zf, "lahman_1871-2025_csv/AwardsPlayers.csv"):
                player_id = row["playerID"]
                year = int(row.get("yearID") or 0)
                if player_id not in player_ids:
                    continue
                award = (row.get("awardID") or "").strip()
                if award == "Most Valuable Player":
                    mvp_count[player_id] += 1
                elif award == "Rookie of the Year":
                    roty_count[player_id] += 1
                elif award == "Gold Glove":
                    gold_glove_count[player_id] += 1
                    powerup_key = "backup_mitt"
                elif award == "Triple Crown":
                    triple_crown_count[player_id] += 1
                    powerup_key = None
                elif award == "Silver Slugger":
                    powerup_key = "bat_donut"
                else:
                    powerup_key = None
                if year >= 2000 and powerup_key:
                    for team_id in appearances_by_player_year.get((player_id, year), set()):
                        franchise_id = team_to_franchise.get((team_id, year))
                        if franchise_id:
                            powerups.add((player_id, powerup_key, franchise_id, team_id, year))

            seen_allstar = set()
            for row in open_lahman_csv(zf, "lahman_1871-2025_csv/AllstarFull.csv"):
                player_id = row["playerID"]
                year = int(row.get("yearID") or 0)
                team_id = row["teamID"]
                franchise_id = team_to_franchise.get((team_id, year))
                dedupe = (player_id, team_id, year)
                if player_id in player_ids and year >= 2000 and franchise_id and dedupe not in seen_allstar:
                    seen_allstar.add(dedupe)
                    powerups.add((player_id, "sunglasses", franchise_id, team_id, year))

            for row in open_lahman_csv(zf, "lahman_1871-2025_csv/Teams.csv"):
                season = int(row.get("yearID") or 0)
                if season >= 2000 and (row.get("WSWin") or "") == "Y":
                    champions.add((row["teamID"], season))

    for player_id, team_id, season in appearances:
        if (team_id, int(season)) in champions:
            ws_rings[player_id] += 1

    for live_schema in attached_mlb_live_schemas(conn):
        if not source_table_exists(conn, live_schema, "mlb_live_player_season_stats"):
            continue
        for player_id, team_id, season, home_runs, strikeouts in conn.execute(
            f"""
            SELECT player_id, team_id, season, home_runs, strikeouts_pitched
              FROM {live_schema}.mlb_live_player_season_stats
            """
        ):
            season = int(season)
            if season > max_lahman_batting_year:
                career_hr[player_id] += int(home_runs or 0)
            franchise_id = team_to_franchise.get((team_id, season))
            if not franchise_id:
                continue
            if int(home_runs or 0) >= 40:
                powerups.add((player_id, "bubblegum", franchise_id, team_id, season))
            if int(strikeouts or 0) >= 200:
                powerups.add((player_id, "pine_tar", franchise_id, team_id, season))

    trait_rows = []
    for player_id in sorted(player_ids):
        country = birth_country.get(player_id, "")
        is_canadian = country in {"Canada", "CAN"}
        is_japanese = country in {"Japan", "JPN"}
        is_cuban = country in {"Cuba", "CUB"}
        team_count = len(team_counts.get(player_id, set()))
        franchise_count = len(franchise_counts.get(player_id, set()))
        seasons = len(season_counts.get(player_id, set()))
        trait_rows.append(
            (
                player_id,
                country or None,
                int(is_japanese),
                int(is_cuban),
                int(is_canadian),
                int(mvp_count[player_id]),
                int(roty_count[player_id]),
                int(gold_glove_count[player_id]),
                int(triple_crown_count[player_id]),
                int(career_hr[player_id]),
                int(ws_rings[player_id]),
                team_count,
                franchise_count,
                seasons,
                int(franchise_count == 1 and seasons >= 10),
                int(team_count >= 7),
            )
        )
    conn.executemany(
        """
        INSERT INTO player_playoff_traits (
            player_id, birth_country, is_japanese, is_cuban, is_canadian,
            mvp_count, roty_count, gold_glove_count, triple_crown_count,
            career_hr, world_series_rings, team_count, franchise_count,
            season_count, hound_dog_eligible, journeyman_eligible
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        trait_rows,
    )
    conn.executemany(
        """
        INSERT INTO player_powerup_qualifications
            (player_id, powerup_key, franchise_id, team_id, season)
        VALUES (?, ?, ?, ?, ?)
        """,
        sorted(powerups),
    )
    return {
        "baseball_playoff_traits": len(trait_rows),
        "baseball_powerup_qualifications": len(powerups),
    }


def load_headshot_registry(conn: sqlite3.Connection) -> int:
    local_rows = []
    baseball_registry_db = (
        BASEBALL_HEADSHOT_REGISTRY_DB
        if BASEBALL_HEADSHOT_REGISTRY_DB.exists()
        else LEGACY_BASEBALL_HEADSHOT_REGISTRY_DB
    )
    if baseball_registry_db.exists():
        with sqlite3.connect(baseball_registry_db) as registry:
            columns = {row[1] for row in registry.execute("PRAGMA table_info(baseball_headshots)")}
            source_expr = "COALESCE(public_url, source_url)" if "source_url" in columns else "public_url"
            fallback_expr = "fallback_url" if "fallback_url" in columns else "NULL"
            local_rows = [
                (
                    "baseball",
                    player_id,
                    source_url or (f"/local-headshots/baseball/{Path(local_path).name}" if local_path else None),
                    fallback_url,
                    provider or "OOTP Facepack",
                    status or "verified",
                )
                for player_id, local_path, source_url, fallback_url, provider, status in registry.execute(
                    f"""
                    SELECT player_id, local_path, {source_expr}, {fallback_expr}, provider, status
                      FROM baseball_headshots
                     WHERE status = 'verified'
                    """
                )
            ]
    if BASKETBALL_HEADSHOT_REGISTRY_DB.exists():
        with sqlite3.connect(BASKETBALL_HEADSHOT_REGISTRY_DB) as registry:
            local_rows.extend(
                (
                    "basketball",
                    player_id,
                    source_url or (f"/local-headshots/basketball/{Path(local_path).name}" if local_path else None),
                    None,
                    provider or "Canonical Local Cache",
                    status or "verified",
                )
                for player_id, local_path, source_url, provider, status in registry.execute(
                    """
                    SELECT player_id, local_path, source_url, provider, status
                      FROM basketball_headshots
                     WHERE status = 'verified'
                    """
                )
            )
    if HOCKEY_HEADSHOT_REGISTRY_DB.exists():
        with sqlite3.connect(HOCKEY_HEADSHOT_REGISTRY_DB) as registry:
            local_rows.extend(
                (
                    "hockey",
                    player_id,
                    source_url or (f"/local-headshots/hockey/{Path(local_path).name}" if local_path else None),
                    None,
                    provider or "Canonical Local Cache",
                    status or "verified",
                )
                for player_id, local_path, source_url, provider, status in registry.execute(
                    """
                    SELECT player_id, local_path, source_url, provider, status
                      FROM hockey_headshots
                     WHERE status = 'verified'
                    """
                )
            )
    if FOOTBALL_HEADSHOT_REGISTRY_DB.exists():
        with sqlite3.connect(FOOTBALL_HEADSHOT_REGISTRY_DB) as registry:
            local_rows.extend(
                (
                    "football",
                    player_id,
                    source_url or (f"/local-headshots/football/{Path(local_path).name}" if local_path else None),
                    None,
                    provider or "Canonical Local Cache",
                    status or "verified",
                )
                for player_id, local_path, source_url, provider, status in registry.execute(
                    """
                    SELECT player_id, local_path, source_url, provider, status
                      FROM football_headshots
                     WHERE status = 'verified'
                    """
                )
            )
    if local_rows:
        canonical_scopes = sorted({row[0] for row in local_rows})
        conn.executemany("DELETE FROM runtime_headshots WHERE scope = ?", [(scope,) for scope in canonical_scopes])
        conn.executemany(
            """
            INSERT OR REPLACE INTO runtime_headshots
                (scope, player_id, source_url, fallback_url, provider, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            local_rows,
        )
    if not HEADSHOT_REGISTRY.exists():
        return len(local_rows)
    with HEADSHOT_REGISTRY.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = [
            (
                row.get("sport_id") or row.get("sport") or "",
                row.get("player_id") or "",
                row.get("source_url") or None,
                row.get("fallback_url") or None,
                row.get("provider") or "registry",
                row.get("status") or "verified",
            )
            for row in reader
        ]
    rows = [row for row in rows if row[0] and row[1]]
    canonical_sports = {"baseball", "basketball", "hockey", "football"}
    non_canonical_rows = [row for row in rows if row[0] not in canonical_sports]
    conn.executemany(
        """
        INSERT OR REPLACE INTO runtime_headshots
            (scope, player_id, source_url, fallback_url, provider, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        non_canonical_rows,
    )
    return len(rows) + len(local_rows)


def build_keys(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        INSERT INTO compact_player_keys (scope, player_id)
        SELECT scope, player_id
          FROM runtime_players
         ORDER BY scope, player_id;

        INSERT INTO compact_team_keys (scope, team_id, season)
        SELECT scope, team_id, season
          FROM runtime_teams
         ORDER BY scope, season, team_id;
        """
    )


def remove_exhibition_runtime_teams(conn: sqlite3.Connection) -> int:
    bad_team_sql = bad_runtime_team_sql("t")
    affected = conn.execute(
        f"""
        SELECT COUNT(*)
          FROM runtime_teams t
         WHERE {bad_team_sql}
        """
    ).fetchone()[0]
    conn.execute(
        f"""
        DELETE FROM runtime_player_team_seasons
         WHERE (scope, team_id, season) IN (
               SELECT scope, team_id, season
                 FROM runtime_teams t
                WHERE {bad_team_sql}
         )
        """
    )
    conn.execute(
        f"""
        DELETE FROM runtime_teams
         WHERE {bad_runtime_team_sql("runtime_teams")}
        """
    )
    return int(affected or 0)


def remove_orphan_runtime_players(conn: sqlite3.Connection) -> int:
    affected = conn.execute(
        """
        SELECT COUNT(*)
          FROM runtime_players p
         WHERE NOT EXISTS (
               SELECT 1
                 FROM runtime_player_team_seasons pts
                WHERE pts.scope = p.scope
                  AND pts.player_id = p.player_id
         )
        """
    ).fetchone()[0]
    conn.execute(
        """
        DELETE FROM sport_player_stints
         WHERE NOT EXISTS (
               SELECT 1
                 FROM runtime_player_team_seasons pts
                WHERE pts.scope = sport_player_stints.sport_id
                  AND pts.player_id = sport_player_stints.player_id
                  AND pts.team_id = sport_player_stints.team_id
                  AND pts.season = sport_player_stints.season
         )
        """
    )
    conn.execute(
        """
        DELETE FROM runtime_positions
         WHERE (scope, player_id) IN (
               SELECT p.scope, p.player_id
                 FROM runtime_players p
                WHERE NOT EXISTS (
                      SELECT 1
                        FROM runtime_player_team_seasons pts
                       WHERE pts.scope = p.scope
                         AND pts.player_id = p.player_id
                )
         )
        """
    )
    conn.execute(
        """
        DELETE FROM runtime_headshots
         WHERE (scope, player_id) IN (
               SELECT p.scope, p.player_id
                 FROM runtime_players p
                WHERE NOT EXISTS (
                      SELECT 1
                        FROM runtime_player_team_seasons pts
                       WHERE pts.scope = p.scope
                         AND pts.player_id = p.player_id
                )
         )
        """
    )
    conn.execute(
        """
        DELETE FROM runtime_players
         WHERE NOT EXISTS (
               SELECT 1
                 FROM runtime_player_team_seasons pts
                WHERE pts.scope = runtime_players.scope
                  AND pts.player_id = runtime_players.player_id
         )
        """
    )
    return int(affected or 0)


def backfill_raw_proof_catalog(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DELETE FROM runtime_player_team_seasons
         WHERE scope = 'basketball'
           AND season IN (
               SELECT DISTINCT season
                 FROM nbaraw.nba_player_game_appearances
                WHERE season >= 2000
           );

        DELETE FROM runtime_player_team_seasons
         WHERE scope = 'hockey'
           AND season IN (
               SELECT DISTINCT season
                 FROM nhlraw.nhl_player_game_appearances
                WHERE season >= 2000
           );

        DELETE FROM sport_player_stints
         WHERE sport_id = 'basketball'
           AND season IN (
               SELECT DISTINCT season
                 FROM nbaraw.nba_player_game_appearances
                WHERE season >= 2000
           );

        DELETE FROM sport_player_stints
         WHERE sport_id = 'hockey'
           AND season IN (
               SELECT DISTINCT season
                 FROM nhlraw.nhl_player_game_appearances
                WHERE season >= 2000
           );

        INSERT OR IGNORE INTO runtime_teams (scope, team_id, season, franchise_id, name)
        SELECT DISTINCT 'basketball', proof.team_id, proof.season, proof.team_id,
               COALESCE((
                   SELECT t.name FROM sportcat.sport_teams t
                    WHERE t.sport_id = 'basketball'
                      AND t.team_id = proof.team_id
                      AND t.season <= proof.season
                    ORDER BY t.season DESC
                    LIMIT 1
               ), (
                   SELECT t.name FROM sportcat.sport_teams t
                    WHERE t.sport_id = 'basketball'
                      AND t.team_id = proof.team_id
                      AND t.season > proof.season
                    ORDER BY t.season ASC
                    LIMIT 1
               ), proof.team_id)
          FROM nbaraw.nba_teammate_game_proofs proof
         WHERE proof.season >= 2000;

        INSERT OR IGNORE INTO runtime_teams (scope, team_id, season, franchise_id, name)
        SELECT DISTINCT 'hockey', proof.team_id, proof.season, proof.team_id,
               COALESCE((
                   SELECT MAX(t.name) FROM sportcat.sport_teams t
                    WHERE t.sport_id = 'hockey'
                      AND t.team_id = proof.team_id
               ), proof.team_id)
          FROM nhlraw.nhl_teammate_game_proofs proof
         WHERE proof.season >= 2000;

        INSERT OR REPLACE INTO runtime_player_team_seasons
            (scope, player_id, team_id, season, games_total, games_pitched, games_batted)
        SELECT 'basketball', player_id, team_id, season, COUNT(DISTINCT game_id)
               , 0, 0
          FROM nbaraw.nba_player_game_appearances
         WHERE season >= 2000
         GROUP BY player_id, team_id, season;

        INSERT OR REPLACE INTO runtime_player_team_seasons
            (scope, player_id, team_id, season, games_total, games_pitched, games_batted)
        SELECT 'hockey', player_id, team_id, season, COUNT(DISTINCT game_id)
               , 0, 0
          FROM nhlraw.nhl_player_game_appearances
         WHERE season >= 2000
         GROUP BY player_id, team_id, season;

        INSERT OR REPLACE INTO sport_player_stints
            (sport_id, player_id, team_id, season, first_unit, last_unit,
             first_label, last_label, source)
        SELECT 'basketball', player_id, team_id, season,
               CAST(REPLACE(MIN(game_date), '-', '') AS INTEGER),
               CAST(REPLACE(MAX(game_date), '-', '') AS INTEGER),
               MIN(game_date), MAX(game_date), 'nba_game_appearance_dates'
          FROM nbaraw.nba_player_game_appearances
         WHERE season >= 2000
         GROUP BY player_id, team_id, season;

        INSERT OR REPLACE INTO sport_player_stints
            (sport_id, player_id, team_id, season, first_unit, last_unit,
             first_label, last_label, source)
        SELECT 'hockey', player_id, team_id, season,
               CAST(REPLACE(MIN(game_date), '-', '') AS INTEGER),
               CAST(REPLACE(MAX(game_date), '-', '') AS INTEGER),
               MIN(game_date), MAX(game_date), 'nhl_game_appearance_dates'
          FROM nhlraw.nhl_player_game_appearances
         WHERE season >= 2000
         GROUP BY player_id, team_id, season;

        UPDATE runtime_teams
           SET name = CASE
               WHEN team_id = '1610612760' AND season >= 2008 THEN 'Oklahoma City Thunder'
               WHEN team_id = '1610612760' AND season < 2008 THEN 'Seattle SuperSonics'
               WHEN team_id = '1610612763' AND season >= 2001 THEN 'Memphis Grizzlies'
               WHEN team_id = '1610612763' AND season < 2001 THEN 'Vancouver Grizzlies'
               WHEN team_id = '1610612751' AND season >= 2012 THEN 'Brooklyn Nets'
               WHEN team_id = '1610612751' AND season < 2012 THEN 'New Jersey Nets'
               WHEN team_id = '1610612740' AND season >= 2013 THEN 'New Orleans Pelicans'
               ELSE name
           END
         WHERE scope = 'basketball';

        INSERT OR IGNORE INTO runtime_players
            (scope, player_id, external_id, display_name, first_name, last_name,
             debut_year, final_year, primary_pos, search_key, last_key,
             career_games, teammate_count)
        SELECT 'basketball', a.player_id, a.external_id, a.display_name,
               substr(a.display_name, 1, instr(a.display_name || ' ', ' ') - 1),
               trim(substr(a.display_name, instr(a.display_name || ' ', ' ') + 1)),
               MIN(a.season), MAX(a.season), NULL,
               '', '', COUNT(DISTINCT a.game_id), 0
          FROM nbaraw.nba_player_game_appearances a
         WHERE a.season >= 2000
           AND NOT EXISTS (
               SELECT 1 FROM runtime_players p
                WHERE p.scope = 'basketball' AND p.player_id = a.player_id
           )
         GROUP BY a.player_id, a.external_id, a.display_name;

        INSERT OR IGNORE INTO runtime_players
            (scope, player_id, external_id, display_name, first_name, last_name,
             debut_year, final_year, primary_pos, search_key, last_key,
             career_games, teammate_count)
        SELECT 'hockey', a.player_id, a.external_id, a.display_name,
               substr(a.display_name, 1, instr(a.display_name || ' ', ' ') - 1),
               trim(substr(a.display_name, instr(a.display_name || ' ', ' ') + 1)),
               MIN(a.season), MAX(a.season), MAX(a.position),
               '', '', COUNT(DISTINCT a.game_id), 0
          FROM nhlraw.nhl_player_game_appearances a
         WHERE a.season >= 2000
           AND NOT EXISTS (
               SELECT 1 FROM runtime_players p
                WHERE p.scope = 'hockey' AND p.player_id = a.player_id
           )
         GROUP BY a.player_id, a.external_id, a.display_name;
        """
    )
    conn.execute(
        """
        UPDATE runtime_players
           SET search_key = CASE WHEN search_key = '' THEN lower(replace(display_name, ' ', '')) ELSE search_key END,
               last_key = CASE WHEN last_key = '' THEN lower(replace(last_name, ' ', '')) ELSE last_key END
        """
    )


def insert_proofs(conn: sqlite3.Connection) -> None:
    sources = [
        ("baseball", "mlbraw.mlb_teammate_game_proofs"),
        ("basketball", "nbaraw.nba_teammate_game_proofs"),
        ("hockey", "nhlraw.nhl_teammate_game_proofs"),
        ("football", "nflrt.sport_teammates"),
    ]
    for live_schema in attached_mlb_live_schemas(conn):
        if source_table_exists(conn, live_schema, "mlb_teammate_game_proofs"):
            sources.append(("baseball", f"{live_schema}.mlb_teammate_game_proofs"))
    for sport in ("basketball", "hockey", "football"):
        for live_schema in attached_sport_live_schemas(conn, sport):
            if source_table_exists(conn, live_schema, "sport_teammates"):
                sources.append((sport, f"{live_schema}.sport_teammates"))
    for scope, table in sources:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO teammate_team_seasons
                (scope, player_a_key, player_b_key, team_key)
            SELECT ?,
                   pa.player_key,
                   pb.player_key,
                   tk.team_key
              FROM {table} proof
              JOIN compact_player_keys pa
                ON pa.scope = ? AND pa.player_id = proof.player_a_id
              JOIN compact_player_keys pb
                ON pb.scope = ? AND pb.player_id = proof.player_b_id
              JOIN compact_team_keys tk
                ON tk.scope = ?
               AND tk.team_id = proof.team_id
               AND tk.season = proof.season
             WHERE proof.season >= 2000
            """,
            (scope, scope, scope, scope),
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO runtime_coverage
                (scope, season, coverage_type, strict, source)
            SELECT ?, season, 'game_boxscore', 1, 'runtime_compact'
              FROM {table}
             WHERE season >= 2000
             GROUP BY season
            """,
            (scope,),
        )


def insert_baseball_pitcher_exceptions(conn: sqlite3.Connection) -> int:
    """Treat same-team-season pitcher pairs as teammates even without shared games."""
    before = conn.execute(
        "SELECT COUNT(*) FROM teammate_team_seasons WHERE scope = 'baseball'"
    ).fetchone()[0]
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS tmp_runtime_pts_baseball_pitchers
            ON runtime_player_team_seasons(scope, team_id, season, player_id, games_pitched);
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO teammate_team_seasons
            (scope, player_a_key, player_b_key, team_key)
        SELECT 'baseball',
               CASE WHEN pa.player_key < pb.player_key THEN pa.player_key ELSE pb.player_key END,
               CASE WHEN pa.player_key < pb.player_key THEN pb.player_key ELSE pa.player_key END,
               tk.team_key
          FROM runtime_player_team_seasons a
          JOIN runtime_player_team_seasons b
            ON b.scope = a.scope
           AND b.team_id = a.team_id
           AND b.season = a.season
           AND b.player_id > a.player_id
          JOIN compact_player_keys pa
            ON pa.scope = 'baseball' AND pa.player_id = a.player_id
          JOIN compact_player_keys pb
            ON pb.scope = 'baseball' AND pb.player_id = b.player_id
          JOIN compact_team_keys tk
            ON tk.scope = 'baseball'
           AND tk.team_id = a.team_id
           AND tk.season = a.season
         WHERE a.scope = 'baseball'
           AND a.season >= 2000
           AND COALESCE(a.games_pitched, 0) > 0
           AND COALESCE(b.games_pitched, 0) > 0
        """
    )
    after = conn.execute(
        "SELECT COUNT(*) FROM teammate_team_seasons WHERE scope = 'baseball'"
    ).fetchone()[0]
    return after - before


def create_compatibility_views(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE VIEW sports AS
        SELECT scope AS sport_id,
               CASE scope WHEN 'baseball' THEN 'Baseball'
                          WHEN 'basketball' THEN 'Basketball'
                          WHEN 'hockey' THEN 'Hockey'
                          WHEN 'football' THEN 'Football'
                          ELSE scope END AS display_name,
               CASE scope WHEN 'baseball' THEN 'MLB'
                          WHEN 'basketball' THEN 'NBA'
                          WHEN 'hockey' THEN 'NHL'
                          WHEN 'football' THEN 'NFL'
                          ELSE scope END AS league_name,
               MIN(season) AS first_season,
               MAX(season) AS last_season
          FROM runtime_player_team_seasons
         GROUP BY scope;

        CREATE VIEW teams AS
        SELECT team_id, season, franchise_id, NULL AS league, name
          FROM runtime_teams WHERE scope = 'baseball';

        CREATE VIEW sport_teams AS
        SELECT scope AS sport_id, team_id, season, franchise_id, name
          FROM runtime_teams WHERE scope <> 'baseball';

        CREATE VIEW players AS
        SELECT player_id, NULL AS bbref_id, NULL AS retro_id,
               CAST(external_id AS INTEGER) AS mlbam_id, first_name AS name_first,
               last_name AS name_last, display_name AS name_given, NULL AS birth_year,
               debut_year, final_year, NULL AS bats, NULL AS throws,
               primary_pos, NULL AS name_nick
          FROM runtime_players WHERE scope = 'baseball';

        CREATE VIEW sport_players AS
        SELECT scope AS sport_id, player_id, external_id, display_name, first_name,
               last_name, NULL AS birth_year, debut_year, final_year, primary_pos
          FROM runtime_players WHERE scope <> 'baseball';

        CREATE VIEW appearances AS
        SELECT player_id, team_id, season, games_total,
               games_pitched, games_batted
          FROM runtime_player_team_seasons WHERE scope = 'baseball';

        CREATE VIEW sport_appearances AS
        SELECT scope AS sport_id, player_id, team_id, season, games_total
          FROM runtime_player_team_seasons WHERE scope <> 'baseball';

        CREATE VIEW players_searchable AS
        SELECT player_id, display_name, primary_pos || ', ' || debut_year || '-' || final_year AS disambiguation,
               search_key, last_key, career_games, teammate_count
          FROM runtime_players WHERE scope = 'baseball';

        CREATE VIEW sport_players_searchable AS
        SELECT scope AS sport_id, player_id, display_name,
               primary_pos || ', ' || debut_year || '-' || final_year AS disambiguation,
               search_key, last_key, career_games, teammate_count
          FROM runtime_players WHERE scope <> 'baseball';

        CREATE VIEW player_headshots AS
        SELECT scope AS sport_id, player_id, source_url, fallback_url, provider, status,
               NULL AS content_sha256, NULL AS perceptual_hash, NULL AS width, NULL AS height,
               NULL AS checked_at, NULL AS reviewed_at, NULL AS review_note
          FROM runtime_headshots;

        CREATE VIEW sport_player_images AS
        SELECT scope AS sport_id, player_id, source_url, NULL AS content_type
          FROM runtime_headshots WHERE scope <> 'baseball' AND source_url IS NOT NULL;

        CREATE VIEW sport_player_positions AS
        SELECT scope AS sport_id, player_id, position, games
          FROM runtime_positions WHERE scope <> 'baseball';

        CREATE VIEW sport_player_traits AS
        SELECT scope AS sport_id, player_id, career_games, career_points, career_goals,
               career_assists, career_touchdowns, passing_touchdowns,
               rushing_touchdowns, receiving_touchdowns, career_sacks,
               career_interceptions, all_star_count, mvp_count, roty_count,
               championship_count, 'runtime_compact' AS source, CURRENT_TIMESTAMP AS updated_at
          FROM runtime_player_traits;

        CREATE VIEW sport_player_season_traits AS
        SELECT scope AS sport_id, player_id, 0 AS season, 0 AS games,
               peak_points AS points, peak_goals AS goals, peak_assists AS assists,
               peak_touchdowns AS touchdowns, peak_passing_touchdowns AS passing_touchdowns,
               peak_rushing_touchdowns AS rushing_touchdowns,
               peak_receiving_touchdowns AS receiving_touchdowns,
               peak_sacks AS sacks, peak_interceptions AS interceptions,
               'runtime_compact_peaks' AS source
          FROM runtime_player_season_peaks;

        CREATE VIEW teammate_stint_coverage AS
        SELECT season, coverage_type, strict, source, CURRENT_TIMESTAMP AS updated_at
          FROM runtime_coverage WHERE scope = 'baseball';

        CREATE VIEW sport_teammate_stint_coverage AS
        SELECT scope AS sport_id, season, coverage_type, strict, source, CURRENT_TIMESTAMP AS updated_at
          FROM runtime_coverage WHERE scope <> 'baseball';

        CREATE VIEW mlb_teammate_game_proofs AS
        SELECT pa.player_id AS player_a_id, pb.player_id AS player_b_id,
               tk.team_id, tk.season, 1 AS shared_games, 0 AS first_game_pk,
               date(tk.season || '-01-01') AS first_game_date,
               'runtime_compact' AS source
          FROM teammate_team_seasons proof
          JOIN compact_player_keys pa ON pa.player_key = proof.player_a_key
          JOIN compact_player_keys pb ON pb.player_key = proof.player_b_key
          JOIN compact_team_keys tk ON tk.team_key = proof.team_key
         WHERE proof.scope = 'baseball';

        CREATE VIEW sport_teammates AS
        SELECT proof.scope AS sport_id, pa.player_id AS player_a_id,
               pb.player_id AS player_b_id, tk.team_id, tk.season
          FROM teammate_team_seasons proof
          JOIN compact_player_keys pa ON pa.player_key = proof.player_a_key
          JOIN compact_player_keys pb ON pb.player_key = proof.player_b_key
          JOIN compact_team_keys tk ON tk.team_key = proof.team_key
         WHERE proof.scope <> 'baseball';

        CREATE TABLE player_stints (
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            first_unit INTEGER NOT NULL DEFAULT 1,
            last_unit INTEGER NOT NULL DEFAULT 1,
            first_label TEXT,
            last_label TEXT,
            source TEXT,
            PRIMARY KEY (player_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE teammate_exclusions (
            player_a_id TEXT NOT NULL,
            player_b_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT,
            PRIMARY KEY (player_a_id, player_b_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE sport_teammate_exclusions (
            sport_id TEXT NOT NULL,
            player_a_id TEXT NOT NULL,
            player_b_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT,
            PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE sport_live_game_imports (
            sport_id TEXT NOT NULL,
            game_id TEXT NOT NULL,
            game_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT,
            PRIMARY KEY (sport_id, game_id)
        ) WITHOUT ROWID;

        CREATE TABLE sport_live_player_games (
            sport_id TEXT NOT NULL,
            game_id TEXT NOT NULL,
            game_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            position TEXT,
            games_total INTEGER NOT NULL DEFAULT 1,
            goals INTEGER NOT NULL DEFAULT 0,
            assists INTEGER NOT NULL DEFAULT 0,
            points INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (sport_id, game_id, player_id, team_id)
        ) WITHOUT ROWID;

        CREATE TABLE mlb_live_game_imports (
            game_pk INTEGER PRIMARY KEY,
            game_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT
        );

        CREATE TABLE mlb_live_player_games (
            game_pk INTEGER NOT NULL,
            game_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            mlbam_id INTEGER,
            games_pitched INTEGER NOT NULL DEFAULT 0,
            games_batted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (game_pk, player_id, team_id)
        ) WITHOUT ROWID;
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_runtime_players_search_key ON runtime_players(scope, search_key);
        CREATE INDEX idx_runtime_players_last_key ON runtime_players(scope, last_key);
        CREATE INDEX idx_runtime_pts_player ON runtime_player_team_seasons(scope, player_id);
        CREATE INDEX idx_runtime_pts_team ON runtime_player_team_seasons(scope, team_id, season);
        CREATE INDEX idx_compact_player_lookup ON compact_player_keys(scope, player_id, player_key);
        CREATE INDEX idx_compact_team_lookup ON compact_team_keys(scope, team_id, season, team_key);
        CREATE INDEX idx_teammate_pair ON teammate_team_seasons(scope, player_a_key, player_b_key);
        CREATE INDEX idx_teammate_reverse_pair ON teammate_team_seasons(scope, player_b_key, player_a_key);
        CREATE INDEX idx_teammate_team ON teammate_team_seasons(scope, team_key);
        """
    )


def update_teammate_counts(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TEMP TABLE teammate_counts(scope TEXT, player_id TEXT, count INTEGER)")
    conn.execute(
        """
        INSERT INTO teammate_counts
        SELECT scope, player_id, COUNT(DISTINCT teammate_id)
          FROM (
                SELECT proof.scope, pa.player_id, pb.player_id AS teammate_id
                  FROM teammate_team_seasons proof
                  JOIN compact_player_keys pa ON pa.player_key = proof.player_a_key
                  JOIN compact_player_keys pb ON pb.player_key = proof.player_b_key
                UNION ALL
                SELECT proof.scope, pb.player_id, pa.player_id AS teammate_id
                  FROM teammate_team_seasons proof
                  JOIN compact_player_keys pa ON pa.player_key = proof.player_a_key
                  JOIN compact_player_keys pb ON pb.player_key = proof.player_b_key
          )
         GROUP BY scope, player_id
        """
    )
    conn.execute(
        """
        UPDATE runtime_players
           SET teammate_count = COALESCE((
                   SELECT count FROM teammate_counts c
                    WHERE c.scope = runtime_players.scope
                      AND c.player_id = runtime_players.player_id
               ), teammate_count)
        """
    )
    conn.execute(
        """
        UPDATE runtime_players
           SET career_games = COALESCE((
                   SELECT NULLIF(t.career_games, 0)
                     FROM runtime_player_traits t
                    WHERE t.scope = runtime_players.scope
                      AND t.player_id = runtime_players.player_id
               ), career_games)
         WHERE scope <> 'baseball'
        """
    )


def verify(conn: sqlite3.Connection) -> dict[str, int]:
    checks: dict[str, int] = {}
    for label, sql in {
        "players": "SELECT COUNT(*) FROM runtime_players",
        "teams": "SELECT COUNT(*) FROM runtime_teams",
        "player_team_seasons": "SELECT COUNT(*) FROM runtime_player_team_seasons",
        "teammate_team_seasons": "SELECT COUNT(*) FROM teammate_team_seasons",
        "positions": "SELECT COUNT(*) FROM runtime_positions",
        "headshots": "SELECT COUNT(*) FROM runtime_headshots",
        "missing_proof_player_a": """
            SELECT COUNT(*) FROM teammate_team_seasons t
            LEFT JOIN compact_player_keys p ON p.player_key = t.player_a_key
            LEFT JOIN runtime_players rp ON rp.scope = p.scope AND rp.player_id = p.player_id
            WHERE rp.player_id IS NULL
        """,
        "missing_proof_player_b": """
            SELECT COUNT(*) FROM teammate_team_seasons t
            LEFT JOIN compact_player_keys p ON p.player_key = t.player_b_key
            LEFT JOIN runtime_players rp ON rp.scope = p.scope AND rp.player_id = p.player_id
            WHERE rp.player_id IS NULL
        """,
        "missing_proof_team": """
            SELECT COUNT(*) FROM teammate_team_seasons t
            LEFT JOIN compact_team_keys tk ON tk.team_key = t.team_key
            LEFT JOIN runtime_teams rt ON rt.scope = tk.scope AND rt.team_id = tk.team_id AND rt.season = tk.season
            WHERE rt.team_id IS NULL
        """,
    }.items():
        checks[label] = int(conn.execute(sql).fetchone()[0])
    return checks


def report(conn: sqlite3.Connection) -> None:
    print("scope players teams player_team_seasons teammate_team_seasons")
    for row in conn.execute(
        """
        SELECT p.scope,
               COUNT(DISTINCT p.player_id),
               (SELECT COUNT(*) FROM runtime_teams t WHERE t.scope = p.scope),
               (SELECT COUNT(*) FROM runtime_player_team_seasons pts WHERE pts.scope = p.scope),
               (SELECT COUNT(*) FROM teammate_team_seasons ts WHERE ts.scope = p.scope)
          FROM runtime_players p
         GROUP BY p.scope
         ORDER BY p.scope
        """
    ):
        print(" ".join(f"{item:,}" if isinstance(item, int) else str(item) for item in row))


def build(output: Path) -> dict[str, int]:
    require([BASEBALL_DB, SPORT_CATALOG_DB, MLB_PROOFS_DB, NBA_PROOFS_DB, NHL_PROOFS_DB, NFL_RUNTIME_DB])
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    conn = sqlite3.connect(output)
    try:
        attach(conn)
        create_schema(conn)
        copy_baseball_catalog(conn)
        copy_cross_sport_catalog(conn, "basketball", "sportcat")
        copy_cross_sport_catalog(conn, "hockey", "sportcat")
        copy_cross_sport_catalog(conn, "football", "nflrt")
        exhibition_team_rows = remove_exhibition_runtime_teams(conn)
        baseball_support = build_baseball_playoff_support(conn)
        loaded_headshots = load_headshot_registry(conn)
        backfill_raw_proof_catalog(conn)
        exhibition_team_rows += remove_exhibition_runtime_teams(conn)
        orphan_player_rows_removed = remove_orphan_runtime_players(conn)
        build_keys(conn)
        insert_proofs(conn)
        baseball_pitcher_exception_rows = insert_baseball_pitcher_exceptions(conn)
        update_teammate_counts(conn)
        create_compatibility_views(conn)
        create_indexes(conn)
        conn.execute("VACUUM")
        checks = verify(conn)
        checks["registry_rows_read"] = loaded_headshots
        checks["baseball_pitcher_exception_rows"] = baseball_pitcher_exception_rows
        checks["exhibition_team_rows_removed"] = exhibition_team_rows
        checks["orphan_player_rows_removed"] = orphan_player_rows_removed
        checks.update(baseball_support)
        report(conn)
        return checks
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    checks = build(args.output)
    print(f"output: {args.output}")
    print(f"output_size_mb: {db_size_mb(args.output):.1f}")
    for key, value in checks.items():
        print(f"{key}: {value:,}")
    bad = checks["missing_proof_player_a"] + checks["missing_proof_player_b"] + checks["missing_proof_team"]
    if bad:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
