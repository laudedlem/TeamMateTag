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
import os
import sqlite3
import sys
import unicodedata
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
BASEBALL_DB = ROOT / "db" / "base2nerdle.sqlite"
SPORT_CATALOG_DB = ROOT / "db" / "teammatetag_local.sqlite"
MLB_PROOFS_DB = ROOT / "raw" / "mlb_game_teammates" / "mlb_game_teammates_v2.sqlite"
MLB_LIVE_RUNTIME_DIR = ROOT / "raw" / "mlb_live_runtime"
NBA_PROOFS_DB = ROOT / "raw" / "nba_game_teammates" / "nba_espn_game_teammates.sqlite"
NHL_PROOFS_DB = ROOT / "raw" / "nhl_game_teammates" / "nhl_game_teammates.sqlite"
NFL_RUNTIME_DB = ROOT / "raw" / "nfl_game_teammates" / "nfl_compact_runtime_int.sqlite"
HEADSHOT_REGISTRY = ROOT / "raw" / "headshot_registry_2026-08-15.csv"


def normalize(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", value.lower())


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
    if source_table_exists(conn, "baseball", "player_playoff_traits"):
        conn.execute("INSERT INTO player_playoff_traits SELECT * FROM baseball.player_playoff_traits")
    if source_table_exists(conn, "baseball", "player_powerup_qualifications"):
        conn.execute("INSERT INTO player_powerup_qualifications SELECT * FROM baseball.player_powerup_qualifications")
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


def load_headshot_registry(conn: sqlite3.Connection) -> int:
    if not HEADSHOT_REGISTRY.exists():
        return 0
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
    conn.executemany(
        """
        INSERT OR REPLACE INTO runtime_headshots
            (scope, player_id, source_url, fallback_url, provider, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


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


def backfill_raw_proof_catalog(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        INSERT OR IGNORE INTO runtime_teams (scope, team_id, season, franchise_id, name)
        SELECT DISTINCT 'basketball', proof.team_id, proof.season, proof.team_id,
               COALESCE((
                   SELECT MAX(t.name) FROM sportcat.sport_teams t
                    WHERE t.sport_id = 'basketball'
                      AND t.team_id = proof.team_id
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

        INSERT OR IGNORE INTO runtime_player_team_seasons
            (scope, player_id, team_id, season, games_total, games_pitched, games_batted)
        SELECT 'basketball', player_id, team_id, season, COUNT(DISTINCT game_id)
               , 0, 0
          FROM nbaraw.nba_player_game_appearances
         WHERE season >= 2000
         GROUP BY player_id, team_id, season;

        INSERT OR IGNORE INTO runtime_player_team_seasons
            (scope, player_id, team_id, season, games_total, games_pitched, games_batted)
        SELECT 'hockey', player_id, team_id, season, COUNT(DISTINCT game_id)
               , 0, 0
          FROM nhlraw.nhl_player_game_appearances
         WHERE season >= 2000
         GROUP BY player_id, team_id, season;

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
        SELECT scope AS sport_id, player_id, NULL AS season, 0 AS games,
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
        loaded_headshots = load_headshot_registry(conn)
        backfill_raw_proof_catalog(conn)
        build_keys(conn)
        insert_proofs(conn)
        update_teammate_counts(conn)
        create_compatibility_views(conn)
        create_indexes(conn)
        conn.execute("VACUUM")
        checks = verify(conn)
        checks["registry_rows_read"] = loaded_headshots
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
