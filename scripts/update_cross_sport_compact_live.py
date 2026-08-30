#!/usr/bin/env python3
"""Build and publish compact NBA/NHL live-season runtime data.

Raw-ish player-game rows stay in local SQLite under raw/. Supabase receives
only compact runtime rows: catalog updates, appearance/stint rollups, season
traits, headshot URLs, coverage flags, and compact same-game teammate proofs.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError:
    psycopg = None

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from name_normalize import normalize  # noqa: E402

import live_nba_client as nba_live  # noqa: E402
import live_nhl_client as nhl_live  # noqa: E402


SPORTS = {
    "basketball": {
        "label": "Basketball",
        "league": "NBA",
        "source": nba_live.SOURCE,
        "default_season": lambda: nba_live.default_season(datetime.now(nba_live.EASTERN).date()),
        "season_start": nba_live.season_start,
        "default_window": nba_live.default_window,
    },
    "hockey": {
        "label": "Hockey",
        "league": "NHL",
        "source": nhl_live.SOURCE,
        "default_season": nhl_live.current_nhl_season,
        "season_start": nhl_live.nhl_season_start,
        "default_window": nhl_live.default_window,
    },
}


@dataclass(frozen=True)
class LocalAppearance:
    game_id: str
    game_date: date
    season: int
    player_id: str
    external_id: str | None
    display_name: str
    first_name: str | None
    last_name: str | None
    team_id: str
    team_name: str
    position: str | None
    games_total: int
    goals: int
    assists: int
    points: int
    headshot_url: str | None = None


def split_name(display_name: str) -> tuple[str | None, str | None]:
    parts = [part for part in display_name.replace(".", " ").split() if part]
    if len(parts) > 1 and parts[-1].lower() in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts = parts[:-1]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[-1]


def is_exhibition_team(sport: str, team_id: str | None, team_name: str | None) -> bool:
    normalized = (team_name or "").replace("-", " ").lower()
    raw = (team_name or "").lower()
    if "all star" in normalized or "rising star" in normalized:
        return True
    if "young star" in normalized or "rookie challenge" in normalized:
        return True
    if raw in {"world", "usa"}:
        return True
    if sport == "basketball":
        if raw in {"ogs", "stripes"}:
            return True
        if raw.startswith("team "):
            return True
    return False


def local_db_path(sport: str, season: int) -> Path:
    return ROOT / "raw" / f"{sport}_live_runtime" / f"{sport}_live_{season}.sqlite"


def db_size_mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024 if path.exists() else 0.0


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        CREATE TABLE IF NOT EXISTS sport_teams (
            sport_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            franchise_id TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (sport_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_players (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            external_id TEXT,
            display_name TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            debut_year INTEGER,
            final_year INTEGER,
            primary_pos TEXT,
            PRIMARY KEY (sport_id, player_id)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_live_game_imports (
            sport_id TEXT NOT NULL,
            game_id TEXT NOT NULL,
            game_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (sport_id, game_id)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_live_player_games (
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

        CREATE TABLE IF NOT EXISTS sport_appearances (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            games_total INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (sport_id, player_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_player_stints (
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
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_player_positions (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            position TEXT NOT NULL,
            games INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (sport_id, player_id, position)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_player_season_traits (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            games INTEGER NOT NULL DEFAULT 0,
            points INTEGER NOT NULL DEFAULT 0,
            goals INTEGER NOT NULL DEFAULT 0,
            assists INTEGER NOT NULL DEFAULT 0,
            source TEXT,
            PRIMARY KEY (sport_id, player_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_teammates (
            sport_id TEXT NOT NULL,
            player_a_id TEXT NOT NULL,
            player_b_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_teammate_stint_coverage (
            sport_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            coverage_type TEXT NOT NULL,
            strict INTEGER NOT NULL DEFAULT 1,
            source TEXT,
            PRIMARY KEY (sport_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_players_searchable (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            disambiguation TEXT NOT NULL,
            search_key TEXT NOT NULL,
            last_key TEXT NOT NULL,
            career_games INTEGER NOT NULL DEFAULT 0,
            teammate_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (sport_id, player_id)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS sport_player_images (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            content_type TEXT,
            PRIMARY KEY (sport_id, player_id)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_live_sport_rollup
            ON sport_live_player_games(sport_id, season, player_id, team_id);
        CREATE INDEX IF NOT EXISTS idx_live_sport_pair
            ON sport_live_player_games(sport_id, game_id, team_id, player_id);
        CREATE INDEX IF NOT EXISTS idx_live_sport_teammates_pair
            ON sport_teammates(sport_id, player_a_id, player_b_id);
        """
    )


def attach_catalog(conn: sqlite3.Connection) -> None:
    catalog = ROOT / "db" / "teammatetag_local.sqlite"
    if not catalog.exists():
        return
    conn.execute("ATTACH DATABASE ? AS catalog", (str(catalog),))


def table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    try:
        return bool(conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE name=?", (table,)).fetchone())
    except sqlite3.OperationalError:
        return False


def load_catalog(conn: sqlite3.Connection, sport: str, season: int) -> dict[str, tuple[str, str | None]]:
    attach_catalog(conn)
    if not table_exists(conn, "catalog", "sport_players"):
        return {}
    conn.execute(
        """
        INSERT OR IGNORE INTO sport_teams
        SELECT sport_id, team_id, season, franchise_id, name
          FROM catalog.sport_teams
         WHERE sport_id = ?
        """,
        (sport,),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO sport_players
        SELECT sport_id, player_id, external_id, display_name, first_name,
               last_name, debut_year, final_year, primary_pos
          FROM (
                SELECT sport_id, player_id, external_id, display_name, first_name,
                       last_name, debut_year, final_year, primary_pos
                  FROM catalog.sport_players
                 WHERE sport_id = ?
          )
        """,
        (sport,),
    )
    if table_exists(conn, "catalog", "local_player_images"):
        conn.execute(
            """
            INSERT OR IGNORE INTO sport_player_images
            SELECT sport_id, player_id, source_url, content_type
              FROM catalog.local_player_images
             WHERE sport_id = ? AND COALESCE(source_url, '') <> ''
            """,
            (sport,),
        )
    mapping = {
        str(external_id): (str(player_id), str(external_id) if external_id else None)
        for external_id, player_id in conn.execute(
            "SELECT external_id, player_id FROM sport_players WHERE sport_id=? AND external_id IS NOT NULL",
            (sport,),
        )
    }
    if sport == "basketball":
        mapping.update(
            {
                espn_id: (player_id, external_id)
                for espn_id, (player_id, external_id) in nba_live.load_crosswalk(nba_live.DEFAULT_CROSSWALK).items()
            }
        )
        mapping.update(
            {
                player_id.replace("nba_espn:", ""): (player_id, external_id)
                for player_id, external_id in conn.execute(
                    """
                    SELECT player_id, external_id
                      FROM sport_players
                     WHERE sport_id=? AND player_id LIKE 'nba_espn:%'
                    """,
                    (sport,),
                )
            }
        )
    return mapping


def unique_name_map(conn: sqlite3.Connection, sport: str) -> dict[str, tuple[str, str | None]]:
    return {
        name.lower(): (player_id, external_id)
        for name, _count, player_id, external_id in conn.execute(
            """
            SELECT LOWER(display_name), COUNT(*), MIN(player_id), MIN(external_id)
              FROM sport_players
             WHERE sport_id=?
             GROUP BY LOWER(display_name)
            HAVING COUNT(*)=1
            """,
            (sport,),
        )
    }


def nba_appearances(event: dict[str, Any], player_map: dict[str, tuple[str, str | None]], names: dict[str, tuple[str, str | None]]) -> tuple[list[LocalAppearance], dict[str, str], str]:
    rows, teams = nba_live.fetch_game_appearances(event, player_map, names)
    out: list[LocalAppearance] = []
    for row in rows:
        first, last = split_name(row.display_name)
        out.append(
            LocalAppearance(
                game_id=row.game_id,
                game_date=row.game_date,
                season=row.season,
                player_id=row.player_id,
                external_id=row.external_id,
                display_name=row.display_name,
                first_name=first,
                last_name=last,
                team_id=row.team_id,
                team_name=row.team_name,
                position=row.position,
                games_total=1,
                goals=0,
                assists=0,
                points=row.points,
                headshot_url=row.headshot_url,
            )
        )
    status = (((event.get("competitions") or [{}])[0].get("status") or {}).get("type") or {}).get("description") or "Final"
    return out, teams, status


def nhl_appearances(game: dict[str, Any], _player_map: dict[str, tuple[str, str | None]], _names: dict[str, tuple[str, str | None]]) -> tuple[list[LocalAppearance], dict[str, str], str]:
    game_rows = nhl_live.fetch_game_rows(game)
    teams: dict[str, str] = {}
    out: list[LocalAppearance] = []
    for row in game_rows.rows:
        teams[row.team_id] = row.team_name
        first, last = split_name(row.name)
        out.append(
            LocalAppearance(
                game_id=game_rows.game_id,
                game_date=game_rows.game_date,
                season=game_rows.season,
                player_id=row.player_id,
                external_id=row.external_id,
                display_name=row.name,
                first_name=first,
                last_name=last,
                team_id=row.team_id,
                team_name=row.team_name,
                position=row.position,
                games_total=1,
                goals=row.goals,
                assists=row.assists,
                points=row.points,
                headshot_url=f"https://assets.nhle.com/mugs/nhl/latest/{row.external_id}.png",
            )
        )
    return out, teams, game_rows.status


def scheduled(sport: str, start: date, end: date, season: int) -> list[dict[str, Any]]:
    if sport == "basketball":
        return nba_live.scheduled_games(start, end, season)
    return nhl_live.scheduled_games(start, end, season)


def upsert_local_game(conn: sqlite3.Connection, sport: str, source: str, game: dict[str, Any], rows: list[LocalAppearance], teams: dict[str, str], status: str) -> int:
    teams = {
        team_id: team_name
        for team_id, team_name in teams.items()
        if not is_exhibition_team(sport, team_id, team_name)
    }
    rows = [
        row for row in rows
        if row.team_id in teams and not is_exhibition_team(sport, row.team_id, row.team_name)
    ]
    if not rows:
        return 0
    game_id = rows[0].game_id
    game_day = rows[0].game_date.isoformat()
    season = rows[0].season
    conn.executemany(
        """
        INSERT INTO sport_teams (sport_id, team_id, season, franchise_id, name)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(sport_id, team_id, season) DO UPDATE SET
            franchise_id=excluded.franchise_id,
            name=excluded.name
        """,
        [(sport, team_id, season, team_id, name or team_id) for team_id, name in teams.items()],
    )
    conn.executemany(
        """
        INSERT INTO sport_players
            (sport_id, player_id, external_id, display_name, first_name, last_name,
             debut_year, final_year, primary_pos)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sport_id, player_id) DO UPDATE SET
            external_id=COALESCE(sport_players.external_id, excluded.external_id),
            display_name=excluded.display_name,
            first_name=COALESCE(sport_players.first_name, excluded.first_name),
            last_name=COALESCE(sport_players.last_name, excluded.last_name),
            debut_year=MIN(COALESCE(sport_players.debut_year, excluded.debut_year), excluded.debut_year),
            final_year=MAX(COALESCE(sport_players.final_year, excluded.final_year), excluded.final_year),
            primary_pos=COALESCE(sport_players.primary_pos, excluded.primary_pos)
        """,
        [
            (sport, r.player_id, r.external_id, r.display_name, r.first_name, r.last_name, season, season, r.position)
            for r in rows
        ],
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO sport_player_images (sport_id, player_id, source_url, content_type)
        VALUES (?, ?, ?, NULL)
        """,
        [(sport, r.player_id, r.headshot_url) for r in rows if r.headshot_url],
    )
    conn.execute(
        """
        INSERT INTO sport_live_game_imports
            (sport_id, game_id, game_date, season, status, source, row_count, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(sport_id, game_id) DO UPDATE SET
            game_date=excluded.game_date,
            season=excluded.season,
            status=excluded.status,
            source=excluded.source,
            row_count=excluded.row_count,
            imported_at=CURRENT_TIMESTAMP
        """,
        (sport, game_id, game_day, season, status, source, len(rows)),
    )
    conn.execute("DELETE FROM sport_live_player_games WHERE sport_id=? AND game_id=?", (sport, game_id))
    conn.executemany(
        """
        INSERT INTO sport_live_player_games
            (sport_id, game_id, game_date, season, player_id, team_id, position,
             games_total, goals, assists, points)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (sport, r.game_id, r.game_date.isoformat(), r.season, r.player_id, r.team_id,
             r.position, r.games_total, r.goals, r.assists, r.points)
            for r in rows
        ],
    )
    return len(rows)


def rebuild_local(conn: sqlite3.Connection, sport: str, season: int, source: str) -> None:
    for table in (
        "sport_appearances",
        "sport_player_stints",
        "sport_player_season_traits",
        "sport_teammates",
        "sport_teammate_stint_coverage",
    ):
        conn.execute(f"DELETE FROM {table} WHERE sport_id=? AND season=?", (sport, season))
    conn.execute(
        """
        INSERT INTO sport_appearances
        SELECT sport_id, player_id, team_id, season, SUM(games_total)
          FROM sport_live_player_games
         WHERE sport_id=? AND season=?
         GROUP BY sport_id, player_id, team_id, season
        """,
        (sport, season),
    )
    conn.execute(
        """
        INSERT INTO sport_player_stints
        SELECT sport_id, player_id, team_id, season,
               CAST(REPLACE(MIN(game_date), '-', '') AS INTEGER),
               CAST(REPLACE(MAX(game_date), '-', '') AS INTEGER),
               MIN(game_date), MAX(game_date), ?
          FROM sport_live_player_games
         WHERE sport_id=? AND season=?
         GROUP BY sport_id, player_id, team_id, season
        """,
        (source, sport, season),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO sport_player_positions
        SELECT sport_id, player_id, COALESCE(NULLIF(position, ''), 'UNK'), SUM(games_total)
          FROM sport_live_player_games
         WHERE sport_id=? AND season=?
         GROUP BY sport_id, player_id, COALESCE(NULLIF(position, ''), 'UNK')
        """,
        (sport, season),
    )
    conn.execute(
        """
        INSERT INTO sport_player_season_traits
        SELECT sport_id, player_id, season, SUM(games_total),
               SUM(points), SUM(goals), SUM(assists), ?
          FROM sport_live_player_games
         WHERE sport_id=? AND season=?
         GROUP BY sport_id, player_id, season
        """,
        (source, sport, season),
    )
    conn.execute(
        """
        INSERT INTO sport_teammates
        SELECT a.sport_id, a.player_id, b.player_id, a.team_id, a.season
          FROM sport_live_player_games a
          JOIN sport_live_player_games b
            ON b.sport_id=a.sport_id
           AND b.game_id=a.game_id
           AND b.team_id=a.team_id
           AND b.player_id>a.player_id
         WHERE a.sport_id=? AND a.season=?
         GROUP BY a.sport_id, a.player_id, b.player_id, a.team_id, a.season
        """,
        (sport, season),
    )
    conn.execute(
        """
        INSERT INTO sport_teammate_stint_coverage
        VALUES (?, ?, 'game_boxscore', 1, ?)
        """,
        (sport, season, source),
    )
    refresh_local_search(conn, sport)


def refresh_local_search(conn: sqlite3.Connection, sport: str) -> None:
    teammate_counts: dict[str, int] = defaultdict(int)
    seen = set()
    for a, b in conn.execute("SELECT player_a_id, player_b_id FROM sport_teammates WHERE sport_id=?", (sport,)):
        if (a, b) in seen:
            continue
        seen.add((a, b))
        teammate_counts[a] += 1
        teammate_counts[b] += 1
    rows = []
    for player_id, name, last, pos, debut, final, games in conn.execute(
        """
        SELECT p.player_id, p.display_name, p.last_name, p.primary_pos,
               p.debut_year, p.final_year, COALESCE(SUM(a.games_total), 0)
          FROM sport_players p
          JOIN sport_appearances a
            ON a.sport_id=p.sport_id AND a.player_id=p.player_id
         WHERE p.sport_id=?
         GROUP BY p.player_id, p.display_name, p.last_name, p.primary_pos,
                  p.debut_year, p.final_year
        """,
        (sport,),
    ):
        rows.append(
            (
                sport,
                player_id,
                name,
                f"{pos or sport.upper()}, {debut or '?'}-{final or '?'}",
                normalize(name),
                normalize(last or name),
                int(games or 0),
                int(teammate_counts.get(player_id, 0)),
            )
        )
    conn.executemany(
        """
        INSERT INTO sport_players_searchable
            (sport_id, player_id, display_name, disambiguation, search_key,
             last_key, career_games, teammate_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sport_id, player_id) DO UPDATE SET
            display_name=excluded.display_name,
            disambiguation=excluded.disambiguation,
            search_key=excluded.search_key,
            last_key=excluded.last_key,
            career_games=excluded.career_games,
            teammate_count=excluded.teammate_count
        """,
        rows,
    )


def collect_local(path: Path, sport: str, season: int, start: date, end: date, reset_season: bool) -> tuple[int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = SPORTS[sport]
    conn = sqlite3.connect(path)
    try:
        create_schema(conn)
        player_map = load_catalog(conn, sport, season)
        name_map = unique_name_map(conn, sport)
        if reset_season:
            for table in (
                "sport_live_player_games",
                "sport_live_game_imports",
                "sport_appearances",
                "sport_player_stints",
                "sport_player_season_traits",
                "sport_teammates",
                "sport_teammate_stint_coverage",
            ):
                conn.execute(f"DELETE FROM {table} WHERE sport_id=? AND season=?", (sport, season))
        games = scheduled(sport, start, end, season)
        imported_games = 0
        imported_rows = 0
        for index, game in enumerate(games, 1):
            if sport == "basketball":
                rows, teams, status = nba_appearances(game, player_map, name_map)
            else:
                rows, teams, status = nhl_appearances(game, player_map, name_map)
            count = upsert_local_game(conn, sport, config["source"], game, rows, teams, status)
            if count:
                imported_games += 1
                imported_rows += count
            if index == 1 or index == len(games) or index % 25 == 0:
                print(f"  local {sport} {season}: {index:>4}/{len(games)} schedule games; {imported_rows:,} appearances", flush=True)
            if index % 25 == 0:
                conn.commit()
        if imported_games:
            rebuild_local(conn, sport, season, config["source"])
        conn.commit()
        conn.execute("VACUUM")
        return imported_games, imported_rows
    finally:
        conn.close()


def local_summary(path: Path, sport: str, season: int) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        create_schema(conn)
        return {
            "games": int(conn.execute("SELECT COUNT(*) FROM sport_live_game_imports WHERE sport_id=? AND season=?", (sport, season)).fetchone()[0]),
            "player_games": int(conn.execute("SELECT COUNT(*) FROM sport_live_player_games WHERE sport_id=? AND season=?", (sport, season)).fetchone()[0]),
            "appearances": int(conn.execute("SELECT COUNT(*) FROM sport_appearances WHERE sport_id=? AND season=?", (sport, season)).fetchone()[0]),
            "stints": int(conn.execute("SELECT COUNT(*) FROM sport_player_stints WHERE sport_id=? AND season=?", (sport, season)).fetchone()[0]),
            "season_traits": int(conn.execute("SELECT COUNT(*) FROM sport_player_season_traits WHERE sport_id=? AND season=?", (sport, season)).fetchone()[0]),
            "proofs": int(conn.execute("SELECT COUNT(*) FROM sport_teammates WHERE sport_id=? AND season=?", (sport, season)).fetchone()[0]),
        }
    finally:
        conn.close()


def pg_execute_values(cur: "psycopg.Cursor", sql: str, rows: list[tuple], page_size: int = 5000) -> None:
    for start in range(0, len(rows), page_size):
        cur.executemany(sql, rows[start:start + page_size])


def upload_compact(path: Path, sport: str, season: int, prune_live_staging: bool) -> dict[str, int | str]:
    if psycopg is None:
        raise SystemExit("ERROR: install psycopg first: pip install 'psycopg[binary]'")
    database_url = os.environ.get("DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("ERROR: DIRECT_URL or DATABASE_URL is required for --upload")
    src = sqlite3.connect(path)
    try:
        teams = src.execute("SELECT sport_id, team_id, season, franchise_id, name FROM sport_teams WHERE sport_id=? AND season=?", (sport, season)).fetchall()
        players = src.execute(
            """
            SELECT sport_id, player_id, external_id, display_name, first_name, last_name,
                   debut_year, final_year, primary_pos
              FROM sport_players
             WHERE sport_id=? AND player_id IN (
                   SELECT DISTINCT player_id FROM sport_appearances WHERE sport_id=? AND season=?
             )
            """,
            (sport, sport, season),
        ).fetchall()
        appearances = src.execute("SELECT sport_id, player_id, team_id, season, games_total FROM sport_appearances WHERE sport_id=? AND season=?", (sport, season)).fetchall()
        stints = src.execute("SELECT sport_id, player_id, team_id, season, first_unit, last_unit, first_label, last_label, source FROM sport_player_stints WHERE sport_id=? AND season=?", (sport, season)).fetchall()
        positions = src.execute("SELECT sport_id, player_id, position, games FROM sport_player_positions WHERE sport_id=?", (sport,)).fetchall()
        season_traits = src.execute("SELECT sport_id, player_id, season, games, points, goals, assists, source FROM sport_player_season_traits WHERE sport_id=? AND season=?", (sport, season)).fetchall()
        searchable = src.execute("SELECT sport_id, player_id, display_name, disambiguation, search_key, last_key, career_games, teammate_count FROM sport_players_searchable WHERE sport_id=?", (sport,)).fetchall()
        images = src.execute("SELECT sport_id, player_id, source_url, content_type FROM sport_player_images WHERE sport_id=?", (sport,)).fetchall()
        proofs = src.execute("SELECT player_a_id, player_b_id, team_id, season FROM sport_teammates WHERE sport_id=? AND season=?", (sport, season)).fetchall()
    finally:
        src.close()

    config = SPORTS[sport]
    with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '20min'")
            cur.execute(
                """
                INSERT INTO sports (sport_id, display_name, league_name, active, first_season, last_season)
                VALUES (%s, %s, %s, true, %s, %s)
                ON CONFLICT (sport_id) DO UPDATE
                SET active=true,
                    first_season=LEAST(COALESCE(sports.first_season, EXCLUDED.first_season), EXCLUDED.first_season),
                    last_season=GREATEST(COALESCE(sports.last_season, EXCLUDED.last_season), EXCLUDED.last_season)
                """,
                (sport, config["label"], config["league"], season, season),
            )
            pg_execute_values(
                cur,
                """
                INSERT INTO sport_franchises (sport_id, franchise_id, name, active)
                VALUES (%s, %s, %s, true)
                ON CONFLICT (sport_id, franchise_id) DO UPDATE SET name=EXCLUDED.name, active=true
                """,
                [(sport, franchise_id, name) for _sport, _team_id, _season, franchise_id, name in teams],
            )
            pg_execute_values(cur, """
                INSERT INTO sport_teams (sport_id, team_id, season, franchise_id, name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (sport_id, team_id, season) DO UPDATE
                SET franchise_id=EXCLUDED.franchise_id, name=EXCLUDED.name
                """, teams)
            pg_execute_values(cur, """
                INSERT INTO sport_players
                    (sport_id, player_id, external_id, display_name, first_name, last_name,
                     debut_year, final_year, primary_pos)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sport_id, player_id) DO UPDATE
                SET external_id=COALESCE(sport_players.external_id, EXCLUDED.external_id),
                    display_name=EXCLUDED.display_name,
                    first_name=COALESCE(sport_players.first_name, EXCLUDED.first_name),
                    last_name=COALESCE(sport_players.last_name, EXCLUDED.last_name),
                    debut_year=LEAST(COALESCE(sport_players.debut_year, EXCLUDED.debut_year), EXCLUDED.debut_year),
                    final_year=GREATEST(COALESCE(sport_players.final_year, EXCLUDED.final_year), EXCLUDED.final_year),
                    primary_pos=COALESCE(sport_players.primary_pos, EXCLUDED.primary_pos)
                """, players)
            cur.execute("DELETE FROM sport_appearances WHERE sport_id=%s AND season=%s", (sport, season))
            pg_execute_values(cur, "INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total) VALUES (%s, %s, %s, %s, %s)", appearances)
            cur.execute("DELETE FROM sport_player_stints WHERE sport_id=%s AND season=%s", (sport, season))
            pg_execute_values(cur, """
                INSERT INTO sport_player_stints
                    (sport_id, player_id, team_id, season, first_unit, last_unit,
                     first_label, last_label, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, stints)
            pg_execute_values(cur, """
                INSERT INTO sport_player_positions (sport_id, player_id, position, games)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sport_id, player_id, position) DO UPDATE
                SET games=GREATEST(sport_player_positions.games, EXCLUDED.games)
                """, positions)
            cur.execute("DELETE FROM sport_player_season_traits WHERE sport_id=%s AND season=%s", (sport, season))
            pg_execute_values(cur, """
                INSERT INTO sport_player_season_traits
                    (sport_id, player_id, season, games, points, goals, assists, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, season_traits)
            pg_execute_values(cur, """
                INSERT INTO sport_players_searchable
                    (sport_id, player_id, display_name, disambiguation, search_key,
                     last_key, career_games, teammate_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sport_id, player_id) DO UPDATE
                SET display_name=EXCLUDED.display_name,
                    disambiguation=EXCLUDED.disambiguation,
                    search_key=EXCLUDED.search_key,
                    last_key=EXCLUDED.last_key,
                    career_games=EXCLUDED.career_games,
                    teammate_count=EXCLUDED.teammate_count
                """, searchable)
            pg_execute_values(cur, """
                INSERT INTO sport_player_images (sport_id, player_id, source_url, content_type)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sport_id, player_id) DO UPDATE
                SET source_url=COALESCE(sport_player_images.source_url, EXCLUDED.source_url),
                    content_type=COALESCE(sport_player_images.content_type, EXCLUDED.content_type)
                """, images)
            cur.execute(
                """
                INSERT INTO compact_player_keys (scope, player_id)
                SELECT DISTINCT %s, player_id
                  FROM sport_appearances
                 WHERE sport_id=%s AND season=%s
                ON CONFLICT (scope, player_id) DO NOTHING
                """,
                (sport, sport, season),
            )
            cur.execute(
                """
                INSERT INTO compact_team_keys (scope, team_id, season)
                SELECT DISTINCT %s, team_id, season::smallint
                  FROM sport_appearances
                 WHERE sport_id=%s AND season=%s
                ON CONFLICT (scope, team_id, season) DO NOTHING
                """,
                (sport, sport, season),
            )
            cur.execute(
                """
                DELETE FROM compact_sport_teammates c
                 USING compact_team_keys tk
                 WHERE c.team_key=tk.team_key
                   AND c.sport_id=%s
                   AND tk.scope=%s
                   AND tk.season=%s
                """,
                (sport, sport, season),
            )
            pg_execute_values(cur, """
                INSERT INTO compact_sport_teammates
                    (sport_id, player_a_key, player_b_key, team_key, season)
                SELECT %s, pa.player_key, pb.player_key, tk.team_key, %s::smallint
                  FROM compact_player_keys pa
                  JOIN compact_player_keys pb ON pb.scope=%s AND pb.player_id=%s
                  JOIN compact_team_keys tk ON tk.scope=%s AND tk.team_id=%s AND tk.season=%s
                 WHERE pa.scope=%s AND pa.player_id=%s
                ON CONFLICT DO NOTHING
                """,
                [(sport, proof_season, sport, b, sport, team_id, proof_season, sport, a) for a, b, team_id, proof_season in proofs],
            )
            cur.execute(
                """
                INSERT INTO sport_teammate_stint_coverage
                    (sport_id, season, coverage_type, strict, source, updated_at)
                VALUES (%s, %s, 'game_boxscore', 1, %s, now())
                ON CONFLICT (sport_id, season) DO UPDATE
                SET coverage_type=EXCLUDED.coverage_type,
                    strict=EXCLUDED.strict,
                    source=EXCLUDED.source,
                    updated_at=now()
                """,
                (sport, season, config["source"]),
            )
            cur.execute(
                """
                WITH career AS (
                    SELECT st.sport_id, st.player_id,
                           SUM(st.games)::integer AS career_games,
                           SUM(st.points)::integer AS career_points,
                           SUM(st.goals)::integer AS career_goals,
                           SUM(st.assists)::integer AS career_assists
                      FROM sport_player_season_traits st
                     WHERE st.sport_id=%s
                     GROUP BY st.sport_id, st.player_id
                )
                INSERT INTO sport_player_traits
                    (sport_id, player_id, career_games, career_points, career_goals, career_assists, source, updated_at)
                SELECT sport_id, player_id, career_games, career_points, career_goals, career_assists, %s, now()
                  FROM career
                ON CONFLICT (sport_id, player_id) DO UPDATE
                SET career_games=EXCLUDED.career_games,
                    career_points=EXCLUDED.career_points,
                    career_goals=EXCLUDED.career_goals,
                    career_assists=EXCLUDED.career_assists,
                    source=EXCLUDED.source,
                    updated_at=now()
                """,
                (sport, config["source"]),
            )
            if prune_live_staging:
                cur.execute("DELETE FROM sport_live_player_games WHERE sport_id=%s AND season=%s", (sport, season))
                removed_player_games = int(cur.rowcount)
                cur.execute(
                    """
                    DELETE FROM sport_live_game_imports game
                     WHERE game.sport_id=%s AND game.season=%s
                       AND NOT EXISTS (
                           SELECT 1 FROM sport_live_player_games live
                            WHERE live.sport_id=game.sport_id AND live.game_id=game.game_id
                       )
                    """,
                    (sport, season),
                )
                removed_games = int(cur.rowcount)
            else:
                removed_player_games = 0
                removed_games = 0
            cur.execute("ANALYZE sport_appearances")
            cur.execute("ANALYZE sport_player_stints")
            cur.execute("ANALYZE compact_sport_teammates")
            db_size = cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
        conn.commit()
    return {
        "teams": len(teams),
        "players": len(players),
        "appearances": len(appearances),
        "stints": len(stints),
        "season_traits": len(season_traits),
        "proofs": len(proofs),
        "removed_live_player_games": removed_player_games,
        "removed_live_games": removed_games,
        "database_size": str(db_size),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sport", choices=sorted(SPORTS))
    parser.add_argument("--season", type=int)
    parser.add_argument("--start-date", type=lambda value: datetime.strptime(value, "%Y-%m-%d").date())
    parser.add_argument("--end-date", type=lambda value: datetime.strptime(value, "%Y-%m-%d").date())
    parser.add_argument("--backfill-days", type=int, default=4)
    parser.add_argument("--season-to-date", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reset-season", action="store_true")
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--prune-live-staging", action="store_true")
    args = parser.parse_args()

    config = SPORTS[args.sport]
    season = args.season or config["default_season"]()
    output = args.output or local_db_path(args.sport, season)
    if args.season_to_date:
        start = config["season_start"](season)
        end = args.end_date or datetime.now(nba_live.EASTERN).date()
        reset = True
    elif args.start_date or args.end_date:
        start = args.start_date or args.end_date
        end = args.end_date or args.start_date
        reset = args.reset_season
    else:
        start, end = config["default_window"](args.backfill_days)
        reset = args.reset_season
    if start > end:
        start, end = end, start

    if args.skip_collect:
        if not output.exists():
            raise SystemExit(f"ERROR: --skip-collect requested but local file does not exist: {output}")
        print(f"{args.sport} compact live local reuse {season}: {output}")
    else:
        print(f"{args.sport} compact live local build {season}: {start} through {end}")
        collect_local(output, args.sport, season, start, end, reset)
    summary = local_summary(output, args.sport, season)
    print(f"local output: {output}")
    print(f"local size: {db_size_mb(output):.1f} MB")
    for key, value in summary.items():
        print(f"{key}: {value:,}")

    if args.upload:
        upload_summary = upload_compact(output, args.sport, season, args.prune_live_staging)
        print("compact upload complete")
        for key, value in upload_summary.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
