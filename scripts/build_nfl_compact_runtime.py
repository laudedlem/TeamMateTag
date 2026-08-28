#!/usr/bin/env python3
"""Build a local compact Football runtime package from strict game proofs.

This script does not connect to Supabase. It combines the 2000-2012 official
Game Book participant source and the 2013-2025 nflverse snap-count source into
one small SQLite runtime database containing only catalog rows, rollups, and
same-game teammate proof rows. Full player-game rows remain in the source
SQLite files under raw/.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "db" / "teammatetag_local.sqlite"
GAMEBOOK = ROOT / "raw" / "nfl_game_teammates" / "nfl_gamebook_teammates.sqlite"
SNAP = ROOT / "raw" / "nfl_game_teammates" / "nfl_snap_teammates.sqlite"
OUTPUT = ROOT / "raw" / "nfl_game_teammates" / "nfl_compact_runtime.sqlite"
SPORT_ID = "football"


def nfl_team(code: str, season: int) -> tuple[str, str]:
    code = code.upper()
    if code == "BAL":
        return "BAL", "Baltimore Ravens"
    if code == "HOU":
        return "HOU", "Houston Texans"
    if code in {"LA", "LAR", "STL"}:
        if code == "STL" or season <= 2015:
            return "LAR", "St. Louis Rams"
        return "LAR", "Los Angeles Rams"
    if code == "OAK":
        return "LV", "Oakland Raiders"
    if code == "SD":
        return "LAC", "San Diego Chargers"
    names = {
        "ARI": "Arizona Cardinals",
        "ATL": "Atlanta Falcons",
        "BUF": "Buffalo Bills",
        "CAR": "Carolina Panthers",
        "CHI": "Chicago Bears",
        "CIN": "Cincinnati Bengals",
        "CLE": "Cleveland Browns",
        "DAL": "Dallas Cowboys",
        "DEN": "Denver Broncos",
        "DET": "Detroit Lions",
        "GB": "Green Bay Packers",
        "IND": "Indianapolis Colts",
        "JAX": "Jacksonville Jaguars",
        "KC": "Kansas City Chiefs",
        "LAC": "Los Angeles Chargers",
        "LV": "Las Vegas Raiders",
        "MIA": "Miami Dolphins",
        "MIN": "Minnesota Vikings",
        "NE": "New England Patriots",
        "NO": "New Orleans Saints",
        "NYG": "New York Giants",
        "NYJ": "New York Jets",
        "PHI": "Philadelphia Eagles",
        "PIT": "Pittsburgh Steelers",
        "SEA": "Seattle Seahawks",
        "SF": "San Francisco 49ers",
        "TB": "Tampa Bay Buccaneers",
        "TEN": "Tennessee Titans",
        "WAS": "Washington Commanders",
    }
    return code, names.get(code, code)


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", value.lower())


def first_name(display_name: str) -> str | None:
    parts = [part for part in display_name.split() if part]
    return parts[0] if parts else None


def last_name(display_name: str) -> str | None:
    parts = [part for part in display_name.replace(".", " ").split() if part]
    if len(parts) > 1 and parts[-1].lower() in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts = parts[:-1]
    return parts[-1] if parts else None


def attach_sources(conn: sqlite3.Connection) -> None:
    conn.execute("ATTACH DATABASE ? AS catalog", (str(CATALOG),))
    conn.execute("ATTACH DATABASE ? AS gamebook", (str(GAMEBOOK),))
    conn.execute("ATTACH DATABASE ? AS snap", (str(SNAP),))


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE sport_franchises (
            sport_id TEXT NOT NULL,
            franchise_id TEXT NOT NULL,
            name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (sport_id, franchise_id)
        );
        CREATE TABLE sport_teams (
            sport_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            franchise_id TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (sport_id, team_id, season)
        );
        CREATE TABLE sport_players (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            external_id TEXT,
            display_name TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            birth_year INTEGER,
            debut_year INTEGER,
            final_year INTEGER,
            primary_pos TEXT,
            PRIMARY KEY (sport_id, player_id)
        );
        CREATE TABLE sport_appearances (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            games_total INTEGER NOT NULL,
            PRIMARY KEY (sport_id, player_id, team_id, season)
        );
        CREATE TABLE sport_player_stints (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            first_unit INTEGER NOT NULL,
            last_unit INTEGER NOT NULL,
            first_label TEXT,
            last_label TEXT,
            source TEXT,
            PRIMARY KEY (sport_id, player_id, team_id, season)
        );
        CREATE TABLE sport_player_positions (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            position TEXT NOT NULL,
            games INTEGER NOT NULL,
            PRIMARY KEY (sport_id, player_id, position)
        );
        CREATE TABLE sport_teammate_stint_coverage (
            sport_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            coverage_type TEXT NOT NULL,
            strict INTEGER NOT NULL DEFAULT 1,
            source TEXT,
            PRIMARY KEY (sport_id, season)
        );
        CREATE TABLE sport_teammates (
            sport_id TEXT NOT NULL,
            player_a_id TEXT NOT NULL,
            player_b_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season)
        );
        CREATE TABLE sport_players_searchable (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            disambiguation TEXT NOT NULL,
            search_key TEXT NOT NULL,
            last_key TEXT NOT NULL,
            career_games INTEGER NOT NULL DEFAULT 0,
            teammate_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (sport_id, player_id)
        );
        CREATE TABLE sport_player_images (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            content_type TEXT,
            PRIMARY KEY (sport_id, player_id)
        );
        """
    )


def copy_catalog(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        INSERT OR IGNORE INTO sport_franchises
        SELECT sport_id, franchise_id, name, 1
          FROM catalog.sport_franchises
         WHERE sport_id = 'football';

        INSERT OR IGNORE INTO sport_teams
        SELECT sport_id, team_id, season, franchise_id, name
          FROM catalog.sport_teams
         WHERE sport_id = 'football';

        INSERT OR IGNORE INTO sport_players
        SELECT sport_id, player_id, external_id, display_name, first_name,
               last_name, birth_year, debut_year, final_year, primary_pos
          FROM catalog.sport_players
         WHERE sport_id = 'football';

        INSERT OR IGNORE INTO sport_player_images
        SELECT sport_id, player_id, source_url, content_type
          FROM catalog.local_player_images
         WHERE sport_id = 'football' AND source_url <> '';
        """
    )


def source_names(conn: sqlite3.Connection) -> list[str]:
    return ["gamebook", "snap"]


def fill_source_catalog(conn: sqlite3.Connection) -> None:
    for source in source_names(conn):
        rows = conn.execute(
            f"""
            SELECT p.player_id, p.pfr_player_id, p.display_name, p.first_name,
                   p.last_name, p.birth_year, p.primary_pos, p.headshot_url,
                   MIN(a.season), MAX(a.season)
              FROM {source}.nfl_snap_players p
              LEFT JOIN {source}.nfl_player_game_snap_appearances a
                ON a.player_id = p.player_id
             GROUP BY p.player_id, p.pfr_player_id, p.display_name, p.first_name,
                      p.last_name, p.birth_year, p.primary_pos, p.headshot_url
            """
        ).fetchall()
        conn.executemany(
            """
            INSERT INTO sport_players
                (sport_id, player_id, external_id, display_name, first_name,
                 last_name, birth_year, debut_year, final_year, primary_pos)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sport_id, player_id) DO UPDATE SET
                external_id = COALESCE(sport_players.external_id, excluded.external_id),
                display_name = COALESCE(NULLIF(sport_players.display_name, ''), excluded.display_name),
                first_name = COALESCE(sport_players.first_name, excluded.first_name),
                last_name = COALESCE(sport_players.last_name, excluded.last_name),
                birth_year = COALESCE(sport_players.birth_year, excluded.birth_year),
                debut_year = MIN(COALESCE(sport_players.debut_year, excluded.debut_year), excluded.debut_year),
                final_year = MAX(COALESCE(sport_players.final_year, excluded.final_year), excluded.final_year),
                primary_pos = COALESCE(sport_players.primary_pos, excluded.primary_pos)
            """,
            [
                (
                    SPORT_ID,
                    player_id,
                    pfr_id or None,
                    name,
                    first or first_name(name),
                    last or last_name(name),
                    birth_year,
                    debut,
                    final,
                    pos,
                )
                for player_id, pfr_id, name, first, last, birth_year, pos, _headshot, debut, final in rows
            ],
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO sport_player_images (sport_id, player_id, source_url, content_type)
            VALUES (?, ?, ?, NULL)
            """,
            [
                (SPORT_ID, player_id, headshot)
                for player_id, _pfr_id, _name, _first, _last, _birth_year, _pos, headshot, _debut, _final in rows
                if headshot
            ],
        )


def fill_teams(conn: sqlite3.Connection) -> None:
    team_rows: set[tuple[str, int, str, str]] = set()
    for source in source_names(conn):
        for team_id, season in conn.execute(
            f"SELECT DISTINCT team_id, season FROM {source}.nfl_player_game_snap_appearances"
        ):
            franchise_id, name = nfl_team(team_id, int(season))
            team_rows.add((team_id, int(season), franchise_id, name))
    franchises = sorted({(franchise_id, name) for _team, _season, franchise_id, name in team_rows})
    conn.executemany(
        "INSERT OR IGNORE INTO sport_franchises VALUES (?, ?, ?, 1)",
        [(SPORT_ID, franchise_id, name) for franchise_id, name in franchises],
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO sport_teams (sport_id, team_id, season, franchise_id, name)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(SPORT_ID, team_id, season, franchise_id, name) for team_id, season, franchise_id, name in sorted(team_rows)],
    )


def fill_rollups(conn: sqlite3.Connection) -> None:
    for source, source_name in [("gamebook", "nfl_gamebook_participants"), ("snap", "nflverse_snap_counts")]:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO sport_appearances
                (sport_id, player_id, team_id, season, games_total)
            SELECT '{SPORT_ID}', player_id, team_id, season, COUNT(DISTINCT game_id)
              FROM {source}.nfl_player_game_snap_appearances
             GROUP BY player_id, team_id, season
            """
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO sport_player_stints
                (sport_id, player_id, team_id, season, first_unit, last_unit,
                 first_label, last_label, source)
            SELECT '{SPORT_ID}', player_id, team_id, season, MIN(week), MAX(week),
                   'Week ' || MIN(week), 'Week ' || MAX(week), '{source_name}'
              FROM {source}.nfl_player_game_snap_appearances
             GROUP BY player_id, team_id, season
            """
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO sport_player_positions
                (sport_id, player_id, position, games)
            SELECT '{SPORT_ID}', player_id, COALESCE(NULLIF(position, ''), 'NFL'),
                   COUNT(DISTINCT game_id)
              FROM {source}.nfl_player_game_snap_appearances
             GROUP BY player_id, COALESCE(NULLIF(position, ''), 'NFL')
            """
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO sport_teammate_stint_coverage
                (sport_id, season, coverage_type, strict, source)
            SELECT '{SPORT_ID}', season, 'game_boxscore', 1, '{source_name}'
              FROM {source}.nfl_player_game_snap_appearances
             GROUP BY season
            """
        )


def fill_teammates(conn: sqlite3.Connection) -> None:
    for source in source_names(conn):
        conn.execute(
            f"""
            INSERT OR IGNORE INTO sport_teammates
                (sport_id, player_a_id, player_b_id, team_id, season)
            SELECT '{SPORT_ID}',
                   MIN(a.player_id, b.player_id),
                   MAX(a.player_id, b.player_id),
                   a.team_id,
                   a.season
              FROM {source}.nfl_player_game_snap_appearances a
              JOIN {source}.nfl_player_game_snap_appearances b
                ON b.game_id = a.game_id
               AND b.team_id = a.team_id
               AND b.player_id > a.player_id
             GROUP BY a.season, a.team_id,
                      MIN(a.player_id, b.player_id),
                      MAX(a.player_id, b.player_id)
            """
        )


def fill_search(conn: sqlite3.Connection) -> None:
    teammate_counts = {
        player_id: count
        for player_id, count in conn.execute(
            """
            SELECT player_id, COUNT(DISTINCT teammate_id)
              FROM (
                    SELECT player_a_id AS player_id, player_b_id AS teammate_id FROM sport_teammates
                    UNION ALL
                    SELECT player_b_id AS player_id, player_a_id AS teammate_id FROM sport_teammates
              )
             GROUP BY player_id
            """
        )
    }
    rows = []
    for player_id, name, last, pos, games, debut, final in conn.execute(
        """
        SELECT p.player_id, p.display_name, p.last_name, p.primary_pos,
               COALESCE(SUM(a.games_total), 0), MIN(a.season), MAX(a.season)
          FROM sport_players p
          LEFT JOIN sport_appearances a
            ON a.sport_id = p.sport_id AND a.player_id = p.player_id
         WHERE p.sport_id = 'football'
         GROUP BY p.player_id, p.display_name, p.last_name, p.primary_pos
        """
    ):
        label = f"{pos or 'NFL'}, {debut or '?'}-{final or '?'}"
        rows.append(
            (
                SPORT_ID,
                player_id,
                name,
                label,
                normalize(name),
                normalize(last or last_name(name) or name),
                int(games or 0),
                int(teammate_counts.get(player_id, 0)),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO sport_players_searchable
            (sport_id, player_id, display_name, disambiguation, search_key,
             last_key, career_games, teammate_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_nfl_runtime_teammates_pair
            ON sport_teammates(sport_id, player_a_id, player_b_id);
        CREATE INDEX idx_nfl_runtime_teammates_b
            ON sport_teammates(sport_id, player_b_id, player_a_id);
        CREATE INDEX idx_nfl_runtime_appearances_player
            ON sport_appearances(sport_id, player_id);
        CREATE INDEX idx_nfl_runtime_appearances_team
            ON sport_appearances(sport_id, team_id, season);
        CREATE INDEX idx_nfl_runtime_search_key
            ON sport_players_searchable(sport_id, search_key);
        """
    )


def verify(conn: sqlite3.Connection) -> dict[str, int]:
    checks: dict[str, int] = {}
    for label, query in {
        "players": "SELECT COUNT(*) FROM sport_players",
        "teams": "SELECT COUNT(*) FROM sport_teams",
        "appearances": "SELECT COUNT(*) FROM sport_appearances",
        "stints": "SELECT COUNT(*) FROM sport_player_stints",
        "positions": "SELECT COUNT(*) FROM sport_player_positions",
        "proofs": "SELECT COUNT(*) FROM sport_teammates",
        "coverage": "SELECT COUNT(*) FROM sport_teammate_stint_coverage",
        "missing_player_a": """
            SELECT COUNT(*) FROM sport_teammates t
            LEFT JOIN sport_players p ON p.sport_id=t.sport_id AND p.player_id=t.player_a_id
            WHERE p.player_id IS NULL
        """,
        "missing_player_b": """
            SELECT COUNT(*) FROM sport_teammates t
            LEFT JOIN sport_players p ON p.sport_id=t.sport_id AND p.player_id=t.player_b_id
            WHERE p.player_id IS NULL
        """,
        "missing_team": """
            SELECT COUNT(*) FROM sport_teammates t
            LEFT JOIN sport_teams team
              ON team.sport_id=t.sport_id AND team.team_id=t.team_id AND team.season=t.season
            WHERE team.team_id IS NULL
        """,
    }.items():
        checks[label] = int(conn.execute(query).fetchone()[0])
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    for path in (CATALOG, GAMEBOOK, SNAP):
        if not path.exists():
            raise SystemExit(f"Missing required source: {path}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()

    with sqlite3.connect(args.output) as conn:
        attach_sources(conn)
        create_schema(conn)
        copy_catalog(conn)
        fill_source_catalog(conn)
        fill_teams(conn)
        fill_rollups(conn)
        fill_teammates(conn)
        fill_search(conn)
        create_indexes(conn)
        conn.commit()
        checks = verify(conn)

    print(f"output: {args.output}")
    print(f"size_mb: {args.output.stat().st_size / 1024 / 1024:.1f}")
    for key, value in checks.items():
        print(f"{key}: {value:,}")
    if checks["missing_player_a"] or checks["missing_player_b"] or checks["missing_team"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
