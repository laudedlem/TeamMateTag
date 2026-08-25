#!/usr/bin/env python3
"""
Import completed NHL games from the free public NHL web API into production.

The updater mirrors the MLB live-data pattern: store one row per player/game,
then roll those rows into the compact runtime tables. Daily reruns are safe and
midseason team stints are based on actual game dates.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

try:
    import psycopg
except ImportError:  # pragma: no cover
    print("ERROR: install psycopg first: pip install 'psycopg[binary]'", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from name_normalize import normalize  # noqa: E402


SPORT_ID = "hockey"
SOURCE = "nhl_web_api_game_log"
API = "https://api-web.nhle.com/v1"
EASTERN = ZoneInfo("America/New_York")
FINAL_STATES = {"OFF"}


@dataclass(frozen=True)
class RawAppearance:
    player_id: str
    external_id: str
    team_id: str
    team_name: str
    position: str
    name: str
    games_total: int
    goals: int
    assists: int
    points: int


@dataclass(frozen=True)
class GameRows:
    game_id: str
    game_date: date
    season: int
    status: str
    rows: list[RawAppearance]


def get_json(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("default") or next(iter(value.values()), "") or "")
    return str(value or "")


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def current_nhl_season(today: date | None = None) -> int:
    today = today or datetime.now(EASTERN).date()
    return today.year if today.month >= 9 else today.year - 1


def nhl_season_start(season: int) -> date:
    return date(season, 9, 1)


def default_window(backfill_days: int) -> tuple[date, date]:
    today = datetime.now(EASTERN).date()
    return today - timedelta(days=max(backfill_days - 1, 0)), today


def season_start_year(raw_season: int | str) -> int:
    return int(str(raw_season)[:4])


def toi_seconds(value: Any) -> int:
    if not value or not isinstance(value, str) or ":" not in value:
        return 0
    minutes, seconds = value.split(":", 1)
    try:
        return int(minutes) * 60 + int(seconds)
    except ValueError:
        return 0


def game_is_final(game: dict[str, Any]) -> bool:
    return game.get("gameState") in FINAL_STATES and int(game.get("gameType") or 0) == 2


def scheduled_games(start: date, end: date, season: int) -> list[dict[str, Any]]:
    games_by_id: dict[int, dict[str, Any]] = {}
    cursor = start
    while cursor <= end:
        data = get_json(f"{API}/schedule/{cursor.isoformat()}")
        for week in data.get("gameWeek", []):
            for game in week.get("games", []):
                if not game_is_final(game):
                    continue
                if season_start_year(game.get("season", season)) != season:
                    continue
                game_date = parse_date(week.get("date") or game.get("gameDate") or game["startTimeUTC"][:10])
                if start <= game_date <= end:
                    game = dict(game)
                    game["gameDate"] = game_date.isoformat()
                    games_by_id[int(game["id"])] = game
        cursor += timedelta(days=7)
        time.sleep(0.05)
    return [games_by_id[key] for key in sorted(games_by_id)]


def full_team_name(team: dict[str, Any]) -> str:
    place = localized(team.get("placeName"))
    common = localized(team.get("commonName"))
    return " ".join(part for part in (place, common) if part).strip() or team.get("abbrev") or "NHL"


def short_name_to_full(initial_name: str, landing: dict[str, Any] | None) -> tuple[str, str | None, str | None]:
    if landing:
        first = localized(landing.get("firstName")) or None
        last = localized(landing.get("lastName")) or None
        if first or last:
            return " ".join(part for part in (first, last) if part), first, last
    parts = initial_name.replace(".", ". ").split()
    if len(parts) >= 2:
        return initial_name, parts[0], " ".join(parts[1:])
    return initial_name, None, initial_name or None


def fetch_player_landing(external_id: str) -> dict[str, Any] | None:
    try:
        return get_json(f"{API}/player/{external_id}/landing")
    except requests.RequestException:
        return None


def fetch_game_rows(game: dict[str, Any]) -> GameRows:
    game_id = str(game["id"])
    box = get_json(f"{API}/gamecenter/{game_id}/boxscore")
    game_date = parse_date(box.get("gameDate") or game.get("gameDate"))
    season = season_start_year(box.get("season") or game.get("season"))
    status = box.get("gameState") or game.get("gameState") or "OFF"
    rows: list[RawAppearance] = []

    team_meta = {
        "awayTeam": box.get("awayTeam", {}),
        "homeTeam": box.get("homeTeam", {}),
    }
    stats = box.get("playerByGameStats") or {}
    for side in ("awayTeam", "homeTeam"):
        team = team_meta[side]
        tid = team.get("abbrev")
        team_name = full_team_name(team)
        side_stats = stats.get(side) or {}
        for group in ("forwards", "defense", "goalies"):
            for player in side_stats.get(group, []) or []:
                if toi_seconds(player.get("toi")) <= 0:
                    continue
                external_id = str(player["playerId"])
                position = player.get("position") or ("D" if group == "defense" else "G" if group == "goalies" else "")
                goals = int(player.get("goals") or 0)
                assists = int(player.get("assists") or 0)
                points = int(player.get("points") or goals + assists)
                rows.append(
                    RawAppearance(
                        player_id=f"nhl:{external_id}",
                        external_id=external_id,
                        team_id=tid,
                        team_name=team_name,
                        position=position,
                        name=localized(player.get("name")),
                        games_total=1,
                        goals=goals,
                        assists=assists,
                        points=points,
                    )
                )
    return GameRows(game_id=game_id, game_date=game_date, season=season, status=status, rows=rows)


def ensure_live_schema(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sport_live_game_imports (
                sport_id TEXT NOT NULL REFERENCES sports(sport_id),
                game_id TEXT NOT NULL,
                game_date DATE NOT NULL,
                season INTEGER NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (sport_id, game_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sport_live_player_games (
                sport_id TEXT NOT NULL,
                game_id TEXT NOT NULL,
                game_date DATE NOT NULL,
                season INTEGER NOT NULL,
                player_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                position TEXT,
                games_total INTEGER NOT NULL DEFAULT 1,
                goals INTEGER NOT NULL DEFAULT 0,
                assists INTEGER NOT NULL DEFAULT 0,
                points INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (sport_id, game_id, player_id, team_id),
                FOREIGN KEY (sport_id, game_id)
                    REFERENCES sport_live_game_imports(sport_id, game_id) ON DELETE CASCADE,
                FOREIGN KEY (sport_id, player_id)
                    REFERENCES sport_players(sport_id, player_id),
                FOREIGN KEY (sport_id, team_id, season)
                    REFERENCES sport_teams(sport_id, team_id, season)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_live_player_games_rollup "
            "ON sport_live_player_games(sport_id, season, player_id, team_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_live_player_games_date "
            "ON sport_live_player_games(sport_id, game_date DESC)"
        )
    conn.commit()


def load_existing_players(conn: "psycopg.Connection") -> dict[str, str]:
    with conn.cursor() as cur:
        return {
            str(row[0]): row[1]
            for row in cur.execute(
                "SELECT external_id, player_id FROM sport_players WHERE sport_id = %s AND external_id IS NOT NULL",
                (SPORT_ID,),
            ).fetchall()
        }


def ensure_team(conn: "psycopg.Connection", team_id: str, team_name: str, season: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sport_franchises (sport_id, franchise_id, name, active)
            VALUES (%s, %s, %s, true)
            ON CONFLICT (sport_id, franchise_id) DO UPDATE
            SET name = EXCLUDED.name,
                active = true
            """,
            (SPORT_ID, team_id, team_name),
        )
        cur.execute(
            """
            INSERT INTO sport_teams (sport_id, team_id, season, franchise_id, name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sport_id, team_id, season) DO UPDATE
            SET franchise_id = EXCLUDED.franchise_id,
                name = EXCLUDED.name
            """,
            (SPORT_ID, team_id, season, team_id, team_name),
        )


def ensure_player(
    conn: "psycopg.Connection",
    row: RawAppearance,
    season: int,
    player_cache: dict[str, str],
) -> str:
    cached = player_cache.get(row.external_id)
    if cached:
        return cached

    landing = fetch_player_landing(row.external_id)
    name, first, last = short_name_to_full(row.name, landing)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sport_players
                (sport_id, player_id, external_id, display_name, first_name, last_name,
                 debut_year, final_year, primary_pos)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sport_id, player_id) DO UPDATE
            SET external_id = COALESCE(sport_players.external_id, EXCLUDED.external_id),
                display_name = COALESCE(NULLIF(sport_players.display_name, ''), EXCLUDED.display_name),
                first_name = COALESCE(sport_players.first_name, EXCLUDED.first_name),
                last_name = COALESCE(sport_players.last_name, EXCLUDED.last_name),
                debut_year = COALESCE(sport_players.debut_year, EXCLUDED.debut_year),
                final_year = GREATEST(COALESCE(sport_players.final_year, EXCLUDED.final_year), EXCLUDED.final_year),
                primary_pos = COALESCE(sport_players.primary_pos, EXCLUDED.primary_pos)
            """,
            (SPORT_ID, row.player_id, row.external_id, name, first, last, season, season, row.position),
        )
    player_cache[row.external_id] = row.player_id
    return row.player_id


def upsert_game(conn: "psycopg.Connection", game: GameRows, player_cache: dict[str, str]) -> None:
    teams = {(row.team_id, row.team_name) for row in game.rows}
    for tid, name in teams:
        ensure_team(conn, tid, name, game.season)
    rows = [
        (
            game.game_id,
            game.game_date,
            game.season,
            ensure_player(conn, row, game.season, player_cache),
            row.team_id,
            row.position,
            row.games_total,
            row.goals,
            row.assists,
            row.points,
        )
        for row in game.rows
    ]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sport_live_game_imports
                (sport_id, game_id, game_date, season, status, source, row_count, imported_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (sport_id, game_id) DO UPDATE
            SET game_date = EXCLUDED.game_date,
                season = EXCLUDED.season,
                status = EXCLUDED.status,
                source = EXCLUDED.source,
                row_count = EXCLUDED.row_count,
                imported_at = now()
            """,
            (SPORT_ID, game.game_id, game.game_date, game.season, game.status, SOURCE, len(rows)),
        )
        cur.execute(
            "DELETE FROM sport_live_player_games WHERE sport_id = %s AND game_id = %s",
            (SPORT_ID, game.game_id),
        )
        cur.executemany(
            """
            INSERT INTO sport_live_player_games
                (sport_id, game_id, game_date, season, player_id, team_id, position,
                 games_total, goals, assists, points)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(SPORT_ID, *row) for row in rows],
        )


def refresh_rollups(conn: "psycopg.Connection", season: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sport_players
            SET final_year = GREATEST(COALESCE(final_year, %s), %s)
            WHERE sport_id = %s
              AND player_id IN (
                  SELECT DISTINCT player_id FROM sport_live_player_games
                  WHERE sport_id = %s AND season = %s
              )
            """,
            (season, season, SPORT_ID, SPORT_ID, season),
        )
        cur.execute(
            """
            WITH live AS (
                SELECT sport_id, player_id, team_id, season,
                       SUM(games_total)::integer AS games_total
                FROM sport_live_player_games
                WHERE sport_id = %s AND season = %s
                GROUP BY sport_id, player_id, team_id, season
            )
            INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
            SELECT sport_id, player_id, team_id, season, games_total
            FROM live
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET games_total = EXCLUDED.games_total
            """,
            (SPORT_ID, season),
        )
        cur.execute(
            """
            WITH live AS (
                SELECT sport_id, player_id, team_id, season,
                       MIN(game_date) AS first_date,
                       MAX(game_date) AS last_date
                FROM sport_live_player_games
                WHERE sport_id = %s AND season = %s
                GROUP BY sport_id, player_id, team_id, season
            )
            INSERT INTO sport_player_stints
                (sport_id, player_id, team_id, season, first_unit, last_unit,
                 first_label, last_label, source)
            SELECT sport_id, player_id, team_id, season,
                   to_char(first_date, 'YYYYMMDD')::integer,
                   to_char(last_date, 'YYYYMMDD')::integer,
                   to_char(first_date, 'YYYY-MM-DD'),
                   to_char(last_date, 'YYYY-MM-DD'),
                   %s
            FROM live
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET first_unit = EXCLUDED.first_unit,
                last_unit = EXCLUDED.last_unit,
                first_label = EXCLUDED.first_label,
                last_label = EXCLUDED.last_label,
                source = EXCLUDED.source
            """,
            (SPORT_ID, season, SOURCE),
        )
        cur.execute(
            """
            WITH live AS (
                SELECT sport_id, player_id, season,
                       SUM(games_total)::integer AS games,
                       SUM(points)::integer AS points,
                       SUM(goals)::integer AS goals,
                       SUM(assists)::integer AS assists
                FROM sport_live_player_games
                WHERE sport_id = %s AND season = %s
                GROUP BY sport_id, player_id, season
            )
            INSERT INTO sport_player_season_traits
                (sport_id, player_id, season, games, points, goals, assists, source)
            SELECT sport_id, player_id, season, games, points, goals, assists, %s
            FROM live
            ON CONFLICT (sport_id, player_id, season) DO UPDATE
            SET games = EXCLUDED.games,
                points = EXCLUDED.points,
                goals = EXCLUDED.goals,
                assists = EXCLUDED.assists,
                source = EXCLUDED.source
            """,
            (SPORT_ID, season, SOURCE),
        )
        cur.execute(
            """
            WITH live AS (
                SELECT sport_id, player_id, position, SUM(games_total)::integer AS games
                FROM sport_live_player_games
                WHERE sport_id = %s AND season = %s AND COALESCE(position, '') <> ''
                GROUP BY sport_id, player_id, position
            )
            INSERT INTO sport_player_positions (sport_id, player_id, position, games)
            SELECT sport_id, player_id, position, games
            FROM live
            ON CONFLICT (sport_id, player_id, position) DO UPDATE
            SET games = GREATEST(sport_player_positions.games, EXCLUDED.games)
            """,
            (SPORT_ID, season),
        )
        cur.execute(
            """
            INSERT INTO sport_teammate_stint_coverage
                (sport_id, season, coverage_type, strict, source, updated_at)
            VALUES (%s, %s, 'game-log', 1, %s, now())
            ON CONFLICT (sport_id, season) DO UPDATE
            SET coverage_type = EXCLUDED.coverage_type,
                strict = EXCLUDED.strict,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            (SPORT_ID, season, SOURCE),
        )
        cur.execute(
            """
            INSERT INTO sport_data_provenance
                (sport_id, source, season, source_url, license_note, fetched_at, row_count)
            SELECT %s, %s, %s, %s, %s, now(), COUNT(*)
            FROM sport_live_player_games
            WHERE sport_id = %s AND season = %s
            ON CONFLICT (sport_id, source, season) DO UPDATE
            SET source_url = EXCLUDED.source_url,
                license_note = EXCLUDED.license_note,
                fetched_at = now(),
                row_count = EXCLUDED.row_count
            """,
            (
                SPORT_ID,
                SOURCE,
                season,
                API,
                "Free public NHL web API endpoint; no API key used.",
                SPORT_ID,
                season,
            ),
        )
        cur.execute(
            """
            UPDATE sports
            SET active = true,
                last_season = GREATEST(COALESCE(last_season, %s), %s)
            WHERE sport_id = %s
            """,
            (season, season, SPORT_ID),
        )
    refresh_search_index(conn, season)


def refresh_search_index(conn: "psycopg.Connection", season: int) -> None:
    with conn.cursor() as cur:
        rows = cur.execute(
            """
            WITH live_players AS (
                SELECT DISTINCT player_id
                FROM sport_live_player_games
                WHERE sport_id = %s AND season = %s
            ),
            careers AS (
                SELECT player_id, SUM(games_total)::integer AS career_games
                FROM sport_appearances
                WHERE sport_id = %s AND player_id IN (SELECT player_id FROM live_players)
                GROUP BY player_id
            ),
            teammate_counts AS (
                SELECT a.player_id, COUNT(DISTINCT b.player_id)::integer AS teammate_count
                FROM sport_appearances a
                JOIN sport_appearances b
                  ON b.sport_id = a.sport_id
                 AND b.team_id = a.team_id
                 AND b.season = a.season
                 AND b.player_id <> a.player_id
                WHERE a.sport_id = %s
                  AND a.player_id IN (SELECT player_id FROM live_players)
                GROUP BY a.player_id
            )
            SELECT p.player_id, p.display_name, p.last_name, p.primary_pos,
                   p.debut_year, p.final_year,
                   COALESCE(c.career_games, 0) AS career_games,
                   COALESCE(t.teammate_count, 0) AS teammate_count
            FROM sport_players p
            JOIN live_players lp ON lp.player_id = p.player_id
            LEFT JOIN careers c ON c.player_id = p.player_id
            LEFT JOIN teammate_counts t ON t.player_id = p.player_id
            WHERE p.sport_id = %s
            """,
            (SPORT_ID, season, SPORT_ID, SPORT_ID, SPORT_ID),
        ).fetchall()
        payload = []
        for row in rows:
            name = row[1]
            years = f"{row[4] or '?'}-{row[5] or '?'}"
            pos = row[3] or "NHL"
            payload.append((SPORT_ID, row[0], name, f"{pos}, {years}", normalize(name), normalize(row[2] or name), row[6], row[7]))
        cur.executemany(
            """
            INSERT INTO sport_players_searchable
                (sport_id, player_id, display_name, disambiguation, search_key,
                 last_key, career_games, teammate_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sport_id, player_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                disambiguation = EXCLUDED.disambiguation,
                search_key = EXCLUDED.search_key,
                last_key = EXCLUDED.last_key,
                career_games = EXCLUDED.career_games,
                teammate_count = EXCLUDED.teammate_count
            """,
            payload,
        )


def should_backfill_season(conn: "psycopg.Connection", season: int) -> bool:
    with conn.cursor() as cur:
        row = cur.execute(
            "SELECT COUNT(*) FROM sport_live_game_imports WHERE sport_id = %s AND season = %s",
            (SPORT_ID, season),
        ).fetchone()
    return not row or int(row[0]) == 0


def import_window(
    conn: "psycopg.Connection | None",
    start: date,
    end: date,
    season: int,
    dry_run: bool,
    workers: int,
) -> tuple[int, int]:
    games = scheduled_games(start, end, season)
    rows_seen = 0
    player_cache = load_existing_players(conn) if conn and not dry_run else {}
    chunk_size = max(workers * 6, 24)
    for chunk_start in range(0, len(games), chunk_size):
        chunk = games[chunk_start : chunk_start + chunk_size]
        fetched: dict[int, GameRows] = {}
        with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
            futures = {pool.submit(fetch_game_rows, game): i for i, game in enumerate(chunk)}
            for future in as_completed(futures):
                fetched[futures[future]] = future.result()
        for offset, _game in enumerate(chunk):
            index = chunk_start + offset + 1
            game_rows = fetched[offset]
            rows_seen += len(game_rows.rows)
            if dry_run:
                print(f"  dry-run game {game_rows.game_id}: {len(game_rows.rows)} player appearances")
            else:
                assert conn is not None
                upsert_game(conn, game_rows, player_cache)
                if index % 50 == 0:
                    conn.commit()
                if index == 1 or index == len(games) or index % 50 == 0:
                    print(
                        f"  imported {index:>4}/{len(games)} games "
                        f"({rows_seen:,} appearances so far)",
                        flush=True,
                    )
        time.sleep(0.08)
    if conn and not dry_run:
        conn.commit()
    return len(games), rows_seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=current_nhl_season())
    parser.add_argument("--start-date", type=parse_date)
    parser.add_argument("--end-date", type=parse_date)
    parser.add_argument("--backfill-days", type=int, default=3)
    parser.add_argument("--season-to-date", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.season_to_date:
        start = nhl_season_start(args.season)
        end = args.end_date or datetime.now(EASTERN).date()
    elif args.start_date or args.end_date:
        start = args.start_date or args.end_date
        end = args.end_date or args.start_date
    else:
        start, end = default_window(args.backfill_days)
    if start is None or end is None:
        raise ValueError("Could not determine import window")
    if start > end:
        start, end = end, start

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url and not args.dry_run:
        print("ERROR: set DATABASE_URL or run with --dry-run", file=sys.stderr)
        return 1

    print(f"NHL live update {args.season}: {start} through {end}")
    if args.dry_run:
        games, rows = import_window(None, start, end, args.season, True, args.workers)
        print(f"dry-run complete: {games} final games, {rows} player appearances")
        return 0

    assert pg_url
    safe_url = pg_url.split("@", 1)[-1] if "@" in pg_url else pg_url
    print(f"target: {safe_url}")
    with psycopg.connect(pg_url, autocommit=False, prepare_threshold=None) as conn:
        ensure_live_schema(conn)
        if not args.season_to_date and not (args.start_date or args.end_date) and should_backfill_season(conn, args.season):
            start = nhl_season_start(args.season)
            end = datetime.now(EASTERN).date()
            print(f"no live imports found for {args.season}; expanding first run to {start} through {end}")
        games, rows = import_window(conn, start, end, args.season, False, args.workers)
        refresh_rollups(conn, args.season)
        conn.commit()

    print(f"complete: {games} final games, {rows} player appearances imported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
