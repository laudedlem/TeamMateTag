#!/usr/bin/env python3
"""Build and publish compact MLB live-season runtime data.

The safe path is:
1. Fetch completed regular-season games from the free MLB Stats API.
2. Store raw-ish player-game rows in a local SQLite file under raw/.
3. Derive compact player/team-season rows and teammate team-season proofs.
4. Optionally upload only those compact runtime rows to Supabase.

No raw boxscore/player-game rows are uploaded to Supabase by this script.
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
from zoneinfo import ZoneInfo

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
from live_mlb_client import (  # noqa: E402
    EASTERN,
    SOURCE,
    RawAppearance,
    appeared_in_game,
    get_json,
    parse_date,
    scheduled_games,
    season_start,
    split_name,
    team_id,
)


DEFAULT_OUTPUT_DIR = ROOT / "raw" / "mlb_live_runtime"
BASEBALL_DB = ROOT / "db" / "base2nerdle.sqlite"


@dataclass(frozen=True)
class CompactAppearance:
    raw: RawAppearance
    home_runs: int = 0
    strikeouts_pitched: int = 0


def default_window(backfill_days: int) -> tuple[date, date]:
    today = datetime.now(EASTERN).date()
    return today - timedelta(days=max(backfill_days - 1, 0)), today


def db_size_mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024 if path.exists() else 0.0


def create_local_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        CREATE TABLE IF NOT EXISTS teams (
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            franchise_id TEXT NOT NULL,
            league TEXT,
            name TEXT,
            PRIMARY KEY (team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS players (
            player_id TEXT PRIMARY KEY,
            mlbam_id INTEGER UNIQUE,
            name_first TEXT,
            name_last TEXT,
            debut_year INTEGER,
            final_year INTEGER,
            primary_pos TEXT
        );

        CREATE TABLE IF NOT EXISTS mlb_live_game_imports (
            game_pk INTEGER PRIMARY KEY,
            game_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            status TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS mlb_live_player_games (
            game_pk INTEGER NOT NULL,
            game_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            mlbam_id INTEGER,
            games_total INTEGER NOT NULL DEFAULT 1,
            games_pitched INTEGER NOT NULL DEFAULT 0,
            games_batted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (game_pk, player_id, team_id)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS mlb_live_player_game_stats (
            game_pk INTEGER NOT NULL,
            game_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            home_runs INTEGER NOT NULL DEFAULT 0,
            strikeouts_pitched INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (game_pk, player_id, team_id)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS mlb_live_player_season_stats (
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            home_runs INTEGER NOT NULL DEFAULT 0,
            strikeouts_pitched INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (player_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS appearances (
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            games_total INTEGER NOT NULL DEFAULT 0,
            games_pitched INTEGER NOT NULL DEFAULT 0,
            games_batted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (player_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS player_stints (
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            first_unit INTEGER NOT NULL,
            last_unit INTEGER NOT NULL,
            first_label TEXT,
            last_label TEXT,
            source TEXT,
            PRIMARY KEY (player_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS mlb_teammate_game_proofs (
            player_a_id TEXT NOT NULL,
            player_b_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            shared_games INTEGER NOT NULL,
            first_game_pk INTEGER NOT NULL,
            first_game_date TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (player_a_id, player_b_id, team_id, season)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS players_searchable (
            player_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            disambiguation TEXT NOT NULL,
            search_key TEXT NOT NULL,
            last_key TEXT NOT NULL,
            career_games INTEGER NOT NULL DEFAULT 0,
            teammate_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS player_playoff_traits (
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
        );

        CREATE TABLE IF NOT EXISTS player_powerup_qualifications (
            player_id TEXT NOT NULL,
            powerup_key TEXT NOT NULL,
            franchise_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            PRIMARY KEY (player_id, powerup_key, team_id, season)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_mlb_live_games_rollup
            ON mlb_live_player_games(season, player_id, team_id);
        CREATE INDEX IF NOT EXISTS idx_mlb_live_games_pair
            ON mlb_live_player_games(game_pk, team_id, player_id);
        CREATE INDEX IF NOT EXISTS idx_mlb_live_stats_rollup
            ON mlb_live_player_game_stats(season, player_id, team_id);
        CREATE INDEX IF NOT EXISTS idx_mlb_proofs_pair
            ON mlb_teammate_game_proofs(player_a_id, player_b_id);
        """
    )


def load_catalog(conn: sqlite3.Connection) -> dict[int, str]:
    if not BASEBALL_DB.exists():
        return {}
    conn.execute("ATTACH DATABASE ? AS catalog", (str(BASEBALL_DB),))
    conn.executescript(
        """
        INSERT OR IGNORE INTO teams
        SELECT team_id, season, franchise_id, league, name
          FROM catalog.teams
         WHERE season >= 2000;

        INSERT OR IGNORE INTO players
            (player_id, mlbam_id, name_first, name_last, debut_year, final_year, primary_pos)
        SELECT p.player_id, p.mlbam_id, p.name_first, p.name_last,
               p.debut_year, p.final_year, p.primary_pos
          FROM catalog.players p
         WHERE mlbam_id IS NOT NULL
            OR EXISTS (
                SELECT 1 FROM catalog.appearances a
                 WHERE a.player_id = p.player_id AND a.season >= 2000
            );
        """
    )
    return {
        int(mlbam_id): player_id
        for mlbam_id, player_id in conn.execute(
            "SELECT mlbam_id, player_id FROM players WHERE mlbam_id IS NOT NULL"
        )
    }


def ensure_local_team(conn: sqlite3.Connection, team: dict[str, Any], season: int) -> str:
    tid = team_id(team)
    if not tid:
        raise ValueError(f"Could not map MLB team: {team}")
    name = team.get("name") or team.get("clubName") or tid
    league = (team.get("league") or {}).get("abbreviation") or (team.get("league") or {}).get("name")
    conn.execute(
        """
        INSERT INTO teams (team_id, season, franchise_id, league, name)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(team_id, season) DO UPDATE SET
            franchise_id = excluded.franchise_id,
            league = excluded.league,
            name = excluded.name
        """,
        (tid, season, tid, league, name),
    )
    return tid


def ensure_local_player(
    conn: sqlite3.Connection,
    mlbam_to_player: dict[int, str],
    person: dict[str, Any],
    season: int,
    position: str | None,
) -> str:
    mlbam_id = int(person["id"])
    if mlbam_id in mlbam_to_player:
        player_id = mlbam_to_player[mlbam_id]
        conn.execute(
            """
            UPDATE players
               SET final_year = CASE
                       WHEN final_year IS NULL OR final_year < ? THEN ?
                       ELSE final_year
                   END,
                   primary_pos = COALESCE(primary_pos, ?)
             WHERE player_id = ?
            """,
            (season, season, position, player_id),
        )
        return player_id
    first, last = split_name(person)
    player_id = f"mlbam_{mlbam_id}"
    conn.execute(
        """
        INSERT INTO players
            (player_id, mlbam_id, name_first, name_last, debut_year, final_year, primary_pos)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            mlbam_id = COALESCE(players.mlbam_id, excluded.mlbam_id),
            name_first = COALESCE(players.name_first, excluded.name_first),
            name_last = COALESCE(players.name_last, excluded.name_last),
            debut_year = COALESCE(players.debut_year, excluded.debut_year),
            final_year = MAX(COALESCE(players.final_year, excluded.final_year), excluded.final_year),
            primary_pos = COALESCE(players.primary_pos, excluded.primary_pos)
        """,
        (player_id, mlbam_id, first, last, season, season, position),
    )
    mlbam_to_player[mlbam_id] = player_id
    return player_id


def fetch_compact_boxscore_rows(game: dict[str, Any]) -> list[CompactAppearance]:
    game_pk = int(game["gamePk"])
    boxscore = get_json(f"/game/{game_pk}/boxscore")
    rows: list[CompactAppearance] = []

    for side in ("away", "home"):
        side_box = boxscore.get("teams", {}).get(side, {})
        team = side_box.get("team", {})
        for player_entry in (side_box.get("players") or {}).values():
            total, pitched, batted = appeared_in_game(player_entry)
            if not total:
                continue
            stats = player_entry.get("stats") or {}
            batting = stats.get("batting") or {}
            pitching = stats.get("pitching") or {}
            rows.append(
                CompactAppearance(
                    raw=RawAppearance(
                        person=player_entry["person"],
                        team=team,
                        position=(player_entry.get("position") or {}).get("abbreviation"),
                        games_total=total,
                        games_pitched=pitched,
                        games_batted=batted,
                    ),
                    home_runs=int(batting.get("homeRuns") or 0),
                    strikeouts_pitched=int(pitching.get("strikeOuts") or 0),
                )
            )
    return rows


def materialize_local_game(
    conn: sqlite3.Connection,
    game: dict[str, Any],
    rows: list[CompactAppearance],
    mlbam_to_player: dict[int, str],
) -> int:
    game_pk = int(game["gamePk"])
    season = int(game.get("season") or game.get("seasonDisplay"))
    game_day = parse_date(game.get("officialDate") or game["gameDate"][:10])
    status = (game.get("status") or {}).get("detailedState") or "Final"
    payload = []
    stat_payload = []
    for item in rows:
        row = item.raw
        tid = ensure_local_team(conn, row.team, season)
        player_id = ensure_local_player(conn, mlbam_to_player, row.person, season, row.position)
        payload.append(
            (
                game_pk,
                game_day.isoformat(),
                season,
                player_id,
                tid,
                int(row.person["id"]),
                row.games_total,
                row.games_pitched,
                row.games_batted,
            )
        )
        stat_payload.append(
            (
                game_pk,
                game_day.isoformat(),
                season,
                player_id,
                tid,
                item.home_runs,
                item.strikeouts_pitched,
            )
        )
    if not payload:
        conn.execute("DELETE FROM mlb_live_player_game_stats WHERE game_pk = ?", (game_pk,))
        conn.execute("DELETE FROM mlb_live_player_games WHERE game_pk = ?", (game_pk,))
        conn.execute("DELETE FROM mlb_live_game_imports WHERE game_pk = ?", (game_pk,))
        return 0
    conn.execute(
        """
        INSERT INTO mlb_live_game_imports
            (game_pk, game_date, season, status, row_count, imported_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(game_pk) DO UPDATE SET
            game_date = excluded.game_date,
            season = excluded.season,
            status = excluded.status,
            row_count = excluded.row_count,
            imported_at = CURRENT_TIMESTAMP
        """,
        (game_pk, game_day.isoformat(), season, status, len(payload)),
    )
    conn.execute("DELETE FROM mlb_live_player_game_stats WHERE game_pk = ?", (game_pk,))
    conn.execute("DELETE FROM mlb_live_player_games WHERE game_pk = ?", (game_pk,))
    conn.executemany(
        """
        INSERT INTO mlb_live_player_game_stats
            (game_pk, game_date, season, player_id, team_id, home_runs, strikeouts_pitched)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        stat_payload,
    )
    conn.executemany(
        """
        INSERT INTO mlb_live_player_games
            (game_pk, game_date, season, player_id, team_id, mlbam_id,
             games_total, games_pitched, games_batted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def collect_local(
    output: Path,
    season: int,
    start: date,
    end: date,
    workers: int,
    reset_season: bool,
) -> tuple[int, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output)
    try:
        create_local_schema(conn)
        mlbam_to_player = load_catalog(conn)
        if reset_season:
            for table in (
                "mlb_live_player_game_stats",
                "mlb_live_player_games",
                "mlb_live_game_imports",
                "mlb_live_player_season_stats",
                "appearances",
                "player_stints",
                "mlb_teammate_game_proofs",
                "player_powerup_qualifications",
            ):
                conn.execute(f"DELETE FROM {table} WHERE season = ?", (season,))
        games = [game for game in scheduled_games(start, end) if int(game.get("season") or season) == season]
        imported_games = 0
        imported_appearances = 0
        from concurrent.futures import ThreadPoolExecutor, as_completed

        chunk_size = max(workers * 6, 25)
        for chunk_start in range(0, len(games), chunk_size):
            chunk = games[chunk_start : chunk_start + chunk_size]
            fetched: dict[int, list[CompactAppearance]] = {}
            with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
                futures = {pool.submit(fetch_compact_boxscore_rows, game): i for i, game in enumerate(chunk)}
                for future in as_completed(futures):
                    fetched[futures[future]] = future.result()
            for offset, game in enumerate(chunk):
                count = materialize_local_game(conn, game, fetched[offset], mlbam_to_player)
                imported_appearances += count
                if count:
                    imported_games += 1
                if imported_games == 1 or imported_games == len(games) or imported_games % 50 == 0:
                    print(
                        f"  local MLB {season}: {imported_games:>4}/{len(games)} games; "
                        f"{imported_appearances:,} appearances",
                        flush=True,
                    )
            conn.commit()
        rebuild_local_runtime(conn, season)
        conn.commit()
        conn.execute("VACUUM")
        return imported_games, imported_appearances
    finally:
        conn.close()


def rebuild_local_runtime(conn: sqlite3.Connection, season: int) -> None:
    conn.execute("DELETE FROM mlb_live_player_season_stats WHERE season = ?", (season,))
    conn.execute(
        """
        INSERT INTO mlb_live_player_season_stats
            (player_id, team_id, season, home_runs, strikeouts_pitched)
        SELECT player_id, team_id, season,
               SUM(home_runs), SUM(strikeouts_pitched)
          FROM mlb_live_player_game_stats
         WHERE season = ?
         GROUP BY player_id, team_id, season
        """,
        (season,),
    )
    conn.execute("DELETE FROM appearances WHERE season = ?", (season,))
    conn.execute(
        """
        INSERT INTO appearances
            (player_id, team_id, season, games_total, games_pitched, games_batted)
        SELECT player_id, team_id, season,
               SUM(games_total), SUM(games_pitched), SUM(games_batted)
          FROM mlb_live_player_games
         WHERE season = ?
         GROUP BY player_id, team_id, season
        """,
        (season,),
    )
    conn.execute("DELETE FROM player_stints WHERE season = ?", (season,))
    conn.execute(
        """
        INSERT INTO player_stints
            (player_id, team_id, season, first_unit, last_unit, first_label, last_label, source)
        SELECT player_id, team_id, season,
               CAST(REPLACE(MIN(game_date), '-', '') AS INTEGER),
               CAST(REPLACE(MAX(game_date), '-', '') AS INTEGER),
               MIN(game_date), MAX(game_date), ?
          FROM mlb_live_player_games
         WHERE season = ?
         GROUP BY player_id, team_id, season
        """,
        (SOURCE, season),
    )
    conn.execute("DELETE FROM mlb_teammate_game_proofs WHERE season = ?", (season,))
    conn.execute(
        """
        INSERT INTO mlb_teammate_game_proofs
            (player_a_id, player_b_id, team_id, season, shared_games,
             first_game_pk, first_game_date, source)
        WITH paired AS (
            SELECT a.player_id AS player_a_id,
                   b.player_id AS player_b_id,
                   a.team_id,
                   a.season,
                   COUNT(*) AS shared_games,
                   MIN(a.game_date || '|' || a.game_pk) AS first_key
              FROM mlb_live_player_games a
              JOIN mlb_live_player_games b
                ON b.game_pk = a.game_pk
               AND b.team_id = a.team_id
               AND b.player_id > a.player_id
             WHERE a.season = ?
             GROUP BY a.player_id, b.player_id, a.team_id, a.season
        )
        SELECT player_a_id, player_b_id, team_id, season, shared_games,
               CAST(substr(first_key, instr(first_key, '|') + 1) AS INTEGER),
               substr(first_key, 1, instr(first_key, '|') - 1),
               ?
          FROM paired
        """,
        (season, SOURCE),
    )
    refresh_local_search(conn, season)
    refresh_local_playoff_support(conn, season)


def refresh_local_playoff_support(conn: sqlite3.Connection, season: int) -> None:
    conn.execute("DELETE FROM player_powerup_qualifications WHERE season = ?", (season,))
    conn.execute(
        """
        INSERT OR IGNORE INTO player_powerup_qualifications
            (player_id, powerup_key, franchise_id, team_id, season)
        SELECT s.player_id, 'bubblegum', t.franchise_id, s.team_id, s.season
          FROM mlb_live_player_season_stats s
          JOIN teams t ON t.team_id = s.team_id AND t.season = s.season
         WHERE s.season = ?
           AND s.home_runs >= 40
        UNION
        SELECT s.player_id, 'pine_tar', t.franchise_id, s.team_id, s.season
          FROM mlb_live_player_season_stats s
          JOIN teams t ON t.team_id = s.team_id AND t.season = s.season
         WHERE s.season = ?
           AND s.strikeouts_pitched >= 200
        """,
        (season, season),
    )
    conn.execute(
        """
        INSERT INTO player_playoff_traits (
            player_id, birth_country, is_japanese, is_cuban, is_canadian,
            mvp_count, roty_count, gold_glove_count, triple_crown_count,
            career_hr, world_series_rings, team_count, franchise_count,
            season_count, hound_dog_eligible, journeyman_eligible
        )
        SELECT p.player_id, NULL, 0, 0, 0, 0, 0, 0, 0,
               COALESCE(SUM(s.home_runs), 0), 0,
               COUNT(DISTINCT a.team_id),
               COUNT(DISTINCT t.franchise_id),
               COUNT(DISTINCT a.season),
               CASE WHEN COUNT(DISTINCT t.franchise_id) = 1
                         AND COUNT(DISTINCT a.season) >= 10 THEN 1 ELSE 0 END,
               CASE WHEN COUNT(DISTINCT a.team_id) >= 7 THEN 1 ELSE 0 END
          FROM players p
          JOIN appearances a ON a.player_id = p.player_id
          LEFT JOIN teams t ON t.team_id = a.team_id AND t.season = a.season
          LEFT JOIN mlb_live_player_season_stats s
            ON s.player_id = p.player_id
         WHERE p.player_id IN (
               SELECT DISTINCT player_id FROM appearances WHERE season = ?
         )
         GROUP BY p.player_id
        ON CONFLICT(player_id) DO UPDATE SET
            career_hr = MAX(player_playoff_traits.career_hr, excluded.career_hr),
            team_count = excluded.team_count,
            franchise_count = excluded.franchise_count,
            season_count = excluded.season_count,
            hound_dog_eligible = excluded.hound_dog_eligible,
            journeyman_eligible = excluded.journeyman_eligible
        """,
        (season,),
    )


def refresh_local_search(conn: sqlite3.Connection, season: int) -> None:
    teammate_counts: dict[str, int] = defaultdict(int)
    seen = set()
    for a, b in conn.execute(
        "SELECT player_a_id, player_b_id FROM mlb_teammate_game_proofs"
    ):
        if (a, b) in seen:
            continue
        seen.add((a, b))
        teammate_counts[a] += 1
        teammate_counts[b] += 1
    rows = []
    for player_id, first, last, pos, debut, final, career_games in conn.execute(
        """
        SELECT p.player_id, p.name_first, p.name_last, p.primary_pos,
               p.debut_year, p.final_year, COALESCE(SUM(a.games_total), 0)
          FROM players p
          JOIN appearances a ON a.player_id = p.player_id
         WHERE p.player_id IN (
               SELECT DISTINCT player_id FROM appearances WHERE season = ?
         )
         GROUP BY p.player_id, p.name_first, p.name_last, p.primary_pos,
                  p.debut_year, p.final_year
        """,
        (season,),
    ):
        name = " ".join(part for part in (first, last) if part).strip() or player_id
        years = f"{debut or '?'}-{final or '?'}"
        rows.append(
            (
                player_id,
                name,
                f"{pos or 'MLB'}, {years}",
                normalize(name),
                normalize(last or name),
                int(career_games or 0),
                int(teammate_counts.get(player_id, 0)),
            )
        )
    conn.executemany(
        """
        INSERT INTO players_searchable
            (player_id, display_name, disambiguation, search_key, last_key,
             career_games, teammate_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            display_name = excluded.display_name,
            disambiguation = excluded.disambiguation,
            search_key = excluded.search_key,
            last_key = excluded.last_key,
            career_games = excluded.career_games,
            teammate_count = excluded.teammate_count
        """,
        rows,
    )


def table_count(conn: sqlite3.Connection, table: str, season: int | None = None) -> int:
    if season is None:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE season = ?", (season,)).fetchone()[0])


def local_summary(path: Path, season: int) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        create_local_schema(conn)
        return {
            "games": table_count(conn, "mlb_live_game_imports", season),
            "player_games": table_count(conn, "mlb_live_player_games", season),
            "player_team_seasons": table_count(conn, "appearances", season),
            "stints": table_count(conn, "player_stints", season),
            "proofs": table_count(conn, "mlb_teammate_game_proofs", season),
            "season_stats": table_count(conn, "mlb_live_player_season_stats", season),
            "powerup_qualifications": table_count(conn, "player_powerup_qualifications", season),
            "players": table_count(conn, "players"),
        }
    finally:
        conn.close()


def upload_compact(path: Path, season: int, database_url: str, prune_live_staging: bool) -> dict[str, int | str]:
    if psycopg is None:
        raise SystemExit("ERROR: install psycopg first: pip install 'psycopg[binary]'")
    src = sqlite3.connect(path)
    try:
        players = src.execute(
            """
            SELECT player_id, mlbam_id, name_first, name_last, debut_year, final_year, primary_pos
              FROM players
             WHERE player_id IN (SELECT DISTINCT player_id FROM appearances WHERE season = ?)
            """,
            (season,),
        ).fetchall()
        teams = src.execute(
            "SELECT team_id, season, franchise_id, league, name FROM teams WHERE season = ?",
            (season,),
        ).fetchall()
        appearances = src.execute(
            """
            SELECT player_id, team_id, season, games_total, games_pitched, games_batted
              FROM appearances WHERE season = ?
            """,
            (season,),
        ).fetchall()
        stints = src.execute(
            """
            SELECT player_id, team_id, season, first_unit, last_unit, first_label, last_label, source
              FROM player_stints WHERE season = ?
            """,
            (season,),
        ).fetchall()
        searchable = src.execute(
            """
            SELECT player_id, display_name, disambiguation, search_key, last_key,
                   career_games, teammate_count
              FROM players_searchable
             WHERE player_id IN (SELECT DISTINCT player_id FROM appearances WHERE season = ?)
            """,
            (season,),
        ).fetchall()
        proofs = src.execute(
            """
            SELECT player_a_id, player_b_id, team_id, season, shared_games,
                   first_game_pk, first_game_date
              FROM mlb_teammate_game_proofs
             WHERE season = ?
            """,
            (season,),
        ).fetchall()
        team_season_stats = src.execute(
            """
            SELECT player_id, team_id, season, home_runs, strikeouts_pitched
              FROM mlb_live_player_season_stats
             WHERE season = ?
            """,
            (season,),
        ).fetchall()
        player_season_stats = src.execute(
            """
            SELECT player_id, season, SUM(home_runs) AS home_runs,
                   SUM(strikeouts_pitched) AS strikeouts_pitched
              FROM mlb_live_player_season_stats
             WHERE season = ?
             GROUP BY player_id, season
            """,
            (season,),
        ).fetchall()
        powerups = src.execute(
            """
            SELECT player_id, powerup_key, franchise_id, team_id, season
              FROM player_powerup_qualifications
             WHERE season = ?
            """,
            (season,),
        ).fetchall()
    finally:
        src.close()

    with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '20min'")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mlb_player_season_stat_rollups (
                    player_id TEXT NOT NULL REFERENCES players(player_id),
                    team_id TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    home_runs INTEGER NOT NULL DEFAULT 0,
                    strikeouts_pitched INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (player_id, team_id, season)
                )
                """
            )
            cur.executemany(
                """
                INSERT INTO franchises (franchise_id, name, active)
                VALUES (%s, %s, true)
                ON CONFLICT (franchise_id) DO UPDATE
                SET name = COALESCE(EXCLUDED.name, franchises.name),
                    active = true
                """,
                [(team_id, name or team_id) for team_id, _season, _franchise, _league, name in teams],
            )
            cur.executemany(
                """
                INSERT INTO teams (team_id, season, franchise_id, league, name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (team_id, season) DO UPDATE
                SET franchise_id = EXCLUDED.franchise_id,
                    league = EXCLUDED.league,
                    name = EXCLUDED.name
                """,
                teams,
            )
            cur.executemany(
                """
                INSERT INTO players
                    (player_id, mlbam_id, name_first, name_last, debut_year, final_year, primary_pos)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id) DO UPDATE
                SET mlbam_id = COALESCE(players.mlbam_id, EXCLUDED.mlbam_id),
                    name_first = COALESCE(players.name_first, EXCLUDED.name_first),
                    name_last = COALESCE(players.name_last, EXCLUDED.name_last),
                    debut_year = COALESCE(players.debut_year, EXCLUDED.debut_year),
                    final_year = GREATEST(COALESCE(players.final_year, EXCLUDED.final_year), EXCLUDED.final_year),
                    primary_pos = COALESCE(players.primary_pos, EXCLUDED.primary_pos)
                """,
                players,
            )
            cur.execute("DELETE FROM appearances WHERE season = %s", (season,))
            cur.executemany(
                """
                INSERT INTO appearances
                    (player_id, team_id, season, games_total, games_pitched, games_batted)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                appearances,
            )
            cur.execute("DELETE FROM player_stints WHERE season = %s", (season,))
            cur.executemany(
                """
                INSERT INTO player_stints
                    (player_id, team_id, season, first_unit, last_unit, first_label, last_label, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                stints,
            )
            cur.executemany(
                """
                INSERT INTO players_searchable
                    (player_id, display_name, disambiguation, search_key, last_key,
                     career_games, teammate_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    disambiguation = EXCLUDED.disambiguation,
                    search_key = EXCLUDED.search_key,
                    last_key = EXCLUDED.last_key,
                    career_games = EXCLUDED.career_games,
                    teammate_count = EXCLUDED.teammate_count
                """,
                searchable,
            )
            cur.execute(
                """
                DELETE FROM player_powerup_qualifications
                 WHERE season = %s
                   AND powerup_key IN ('bubblegum', 'pine_tar')
                """,
                (season,),
            )
            cur.executemany(
                """
                INSERT INTO player_powerup_qualifications
                    (player_id, powerup_key, franchise_id, team_id, season)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (player_id, powerup_key, team_id, season) DO UPDATE
                SET franchise_id = EXCLUDED.franchise_id
                """,
                powerups,
            )
            old_player_home_runs: dict[str, int] = {}
            for player_id, home_runs in cur.execute(
                """
                SELECT player_id, COALESCE(SUM(home_runs), 0)
                  FROM mlb_player_season_stat_rollups
                 WHERE season = %s
                 GROUP BY player_id
                """,
                (season,),
            ).fetchall():
                old_player_home_runs[player_id] = int(home_runs or 0)
            cur.execute("DELETE FROM mlb_player_season_stat_rollups WHERE season = %s", (season,))
            cur.executemany(
                """
                INSERT INTO mlb_player_season_stat_rollups
                    (player_id, team_id, season, home_runs, strikeouts_pitched, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (player_id, team_id, season) DO UPDATE
                SET home_runs = EXCLUDED.home_runs,
                    strikeouts_pitched = EXCLUDED.strikeouts_pitched,
                    updated_at = now()
                """,
                team_season_stats,
            )
            new_player_home_runs = {player_id: int(home_runs or 0) for player_id, _season, home_runs, _so in player_season_stats}
            cur.executemany(
                """
                INSERT INTO player_playoff_traits (
                    player_id, birth_country, is_japanese, is_cuban, is_canadian,
                    mvp_count, roty_count, gold_glove_count, triple_crown_count,
                    career_hr, world_series_rings, team_count, franchise_count,
                    season_count, hound_dog_eligible, journeyman_eligible
                )
                SELECT p.player_id,
                       COALESCE(t.birth_country, NULL),
                       COALESCE(t.is_japanese, false),
                       COALESCE(t.is_cuban, false),
                       COALESCE(t.is_canadian, false),
                       COALESCE(t.mvp_count, 0),
                       COALESCE(t.roty_count, 0),
                       COALESCE(t.gold_glove_count, 0),
                       COALESCE(t.triple_crown_count, 0),
                       GREATEST(COALESCE(t.career_hr, 0) + %s, 0),
                       COALESCE(t.world_series_rings, 0),
                       stats.team_count,
                       stats.franchise_count,
                       stats.season_count,
                       (stats.franchise_count = 1 AND stats.season_count >= 10),
                       (stats.team_count >= 7)
                  FROM players p
                  LEFT JOIN player_playoff_traits t ON t.player_id = p.player_id
                  JOIN (
                        SELECT a.player_id,
                               COUNT(DISTINCT a.team_id) AS team_count,
                               COUNT(DISTINCT tm.franchise_id) AS franchise_count,
                               COUNT(DISTINCT a.season) AS season_count
                          FROM appearances a
                          LEFT JOIN teams tm
                            ON tm.team_id = a.team_id AND tm.season = a.season
                         WHERE a.player_id = %s
                         GROUP BY a.player_id
                  ) stats ON stats.player_id = p.player_id
                 WHERE p.player_id = %s
                ON CONFLICT (player_id) DO UPDATE
                SET career_hr = EXCLUDED.career_hr,
                    team_count = EXCLUDED.team_count,
                    franchise_count = EXCLUDED.franchise_count,
                    season_count = EXCLUDED.season_count,
                    hound_dog_eligible = EXCLUDED.hound_dog_eligible,
                    journeyman_eligible = EXCLUDED.journeyman_eligible
                """,
                [
                    (new_hr - old_player_home_runs.get(player_id, 0), player_id, player_id)
                    for player_id, new_hr in new_player_home_runs.items()
                ],
            )
            cur.execute(
                """
                INSERT INTO compact_player_keys (scope, player_id)
                SELECT DISTINCT 'baseball', player_id
                  FROM appearances
                 WHERE season = %s
                ON CONFLICT (scope, player_id) DO NOTHING
                """,
                (season,),
            )
            cur.execute(
                """
                INSERT INTO compact_team_keys (scope, team_id, season)
                SELECT DISTINCT 'baseball', team_id, season::smallint
                  FROM appearances
                 WHERE season = %s
                ON CONFLICT (scope, team_id, season) DO NOTHING
                """,
                (season,),
            )
            cur.execute(
                """
                DELETE FROM compact_mlb_teammate_game_proofs proof
                 USING compact_team_keys team
                 WHERE proof.team_key = team.team_key
                   AND team.scope = 'baseball'
                   AND team.season = %s
                """,
                (season,),
            )
            cur.executemany(
                """
                INSERT INTO compact_mlb_teammate_game_proofs
                    (player_a_key, player_b_key, team_key, season, shared_games,
                     first_game_pk, first_game_date)
                SELECT pa.player_key, pb.player_key, tk.team_key, %s::smallint,
                       LEAST(%s, 32767)::smallint, %s, %s::date
                  FROM compact_player_keys pa
                  JOIN compact_player_keys pb
                    ON pb.scope = 'baseball' AND pb.player_id = %s
                  JOIN compact_team_keys tk
                    ON tk.scope = 'baseball' AND tk.team_id = %s AND tk.season = %s
                 WHERE pa.scope = 'baseball' AND pa.player_id = %s
                ON CONFLICT (player_a_key, player_b_key, team_key, season) DO UPDATE
                SET shared_games = EXCLUDED.shared_games,
                    first_game_pk = EXCLUDED.first_game_pk,
                    first_game_date = EXCLUDED.first_game_date
                """,
                [
                    (proof_season, shared_games, first_game_pk, first_game_date, player_b, team_id, proof_season, player_a)
                    for player_a, player_b, team_id, proof_season, shared_games, first_game_pk, first_game_date in proofs
                ],
            )
            cur.execute(
                """
                INSERT INTO teammate_stint_coverage (season, coverage_type, strict, source, updated_at)
                VALUES (%s, 'game_boxscore', 1, %s, now())
                ON CONFLICT (season) DO UPDATE
                SET coverage_type = EXCLUDED.coverage_type,
                    strict = EXCLUDED.strict,
                    source = EXCLUDED.source,
                    updated_at = now()
                """,
                (season, SOURCE),
            )
            if prune_live_staging:
                cur.execute("DELETE FROM mlb_live_player_games WHERE season = %s", (season,))
                removed_player_games = int(cur.rowcount)
                cur.execute(
                    """
                    DELETE FROM mlb_live_game_imports game
                     WHERE game.season = %s
                       AND NOT EXISTS (
                           SELECT 1 FROM mlb_live_player_games live
                            WHERE live.game_pk = game.game_pk
                       )
                    """,
                    (season,),
                )
                removed_games = int(cur.rowcount)
            else:
                removed_player_games = 0
                removed_games = 0
            cur.execute("ANALYZE appearances")
            cur.execute("ANALYZE player_stints")
            cur.execute("ANALYZE player_powerup_qualifications")
            cur.execute("ANALYZE player_playoff_traits")
            cur.execute("ANALYZE mlb_player_season_stat_rollups")
            cur.execute("ANALYZE compact_mlb_teammate_game_proofs")
            db_size = cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
        conn.commit()
    return {
        "players": len(players),
        "teams": len(teams),
        "appearances": len(appearances),
        "stints": len(stints),
        "proofs": len(proofs),
        "season_stats": len(team_season_stats),
        "powerup_qualifications": len(powerups),
        "removed_live_player_games": removed_player_games,
        "removed_live_games": removed_games,
        "database_size": str(db_size),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=datetime.now(EASTERN).year)
    parser.add_argument("--start-date", type=parse_date)
    parser.add_argument("--end-date", type=parse_date)
    parser.add_argument("--backfill-days", type=int, default=3)
    parser.add_argument("--season-to-date", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reset-season", action="store_true")
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="Reuse the local SQLite file and only print/upload its compact rows.",
    )
    parser.add_argument("--upload", action="store_true")
    parser.add_argument(
        "--prune-live-staging",
        action="store_true",
        help="After compact upload, delete same-season mlb_live_* staging rows from Supabase.",
    )
    args = parser.parse_args()
    output = args.output or DEFAULT_OUTPUT_DIR / f"mlb_live_{args.season}.sqlite"

    if args.season_to_date:
        start = season_start(args.season)
        end = args.end_date or datetime.now(EASTERN).date()
        reset = True
    elif args.start_date or args.end_date:
        start = args.start_date or args.end_date
        end = args.end_date or args.start_date
        reset = args.reset_season
    else:
        start, end = default_window(args.backfill_days)
        reset = args.reset_season
    if start is None or end is None:
        raise ValueError("Could not determine import window")
    if start > end:
        start, end = end, start

    if args.skip_collect:
        if not output.exists():
            raise SystemExit(f"ERROR: --skip-collect requested but local file does not exist: {output}")
        print(f"MLB compact live local reuse {args.season}: {output}")
        games = 0
        player_games = 0
    else:
        print(f"MLB compact live local build {args.season}: {start} through {end}")
        games, player_games = collect_local(output, args.season, start, end, args.workers, reset)
    summary = local_summary(output, args.season)
    print(f"local output: {output}")
    print(f"local size: {db_size_mb(output):.1f} MB")
    for key, value in summary.items():
        print(f"{key}: {value:,}")
    print(f"window_games_imported: {games:,}")
    print(f"window_player_games_imported: {player_games:,}")

    if args.upload:
        database_url = os.environ.get("DATABASE_URL") or os.environ.get("DIRECT_URL")
        if not database_url:
            print("ERROR: DATABASE_URL or DIRECT_URL is required for --upload", file=sys.stderr)
            return 1
        upload_summary = upload_compact(output, args.season, database_url, args.prune_live_staging)
        print("compact upload complete")
        for key, value in upload_summary.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
