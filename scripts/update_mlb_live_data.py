#!/usr/bin/env python3
"""
Import completed MLB games from the free public MLB Stats API into production.

The script stores one row per player/game appearance, then rolls those rows up
into the runtime `appearances` table. That makes daily reruns idempotent and
keeps midseason trades accurate for teammate-stint logic.

Examples:
    python scripts/update_mlb_live_data.py --dry-run --backfill-days 1
    python scripts/update_mlb_live_data.py --season 2026 --season-to-date
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
except ImportError:  # pragma: no cover - exercised by operator setup
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


API = "https://statsapi.mlb.com/api/v1"
SOURCE = "mlb_statsapi_game_log"
EASTERN = ZoneInfo("America/New_York")

TEAM_ID_OVERRIDES = {
    "NYY": "NYA",
    "NYM": "NYN",
    "LAD": "LAN",
    "CWS": "CHA",
    "CHC": "CHN",
    "STL": "SLN",
    "SD": "SDN",
    "SF": "SFN",
    "WSH": "WAS",
    "TB": "TBA",
    "KC": "KCA",
}

FINAL_STATUS_CODES = {"F", "O"}
FINAL_STATES = {"Final", "Game Over", "Completed Early"}


@dataclass(frozen=True)
class GameAppearance:
    game_pk: int
    game_date: date
    season: int
    player_id: str
    mlbam_id: int
    team_id: str
    games_total: int
    games_pitched: int
    games_batted: int


@dataclass(frozen=True)
class RawAppearance:
    person: dict[str, Any]
    team: dict[str, Any]
    position: str | None
    games_total: int
    games_pitched: int
    games_batted: int


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(f"{API}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def default_window(backfill_days: int) -> tuple[date, date]:
    today = datetime.now(EASTERN).date()
    return today - timedelta(days=max(backfill_days - 1, 0)), today


def season_start(season: int) -> date:
    return date(season, 3, 1)


def is_final_game(game: dict[str, Any]) -> bool:
    status = game.get("status", {})
    return (
        status.get("codedGameState") in FINAL_STATUS_CODES
        or status.get("statusCode") in FINAL_STATUS_CODES
        or status.get("detailedState") in FINAL_STATES
        or status.get("abstractGameState") == "Final"
    )


def scheduled_games(start: date, end: date) -> list[dict[str, Any]]:
    data = get_json(
        "/schedule",
        {
            "sportId": 1,
            "gameTypes": "R",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": "team",
        },
    )
    games: list[dict[str, Any]] = []
    for day in data.get("dates", []):
        games.extend(day.get("games", []))
    return [game for game in games if game.get("gameType") == "R" and is_final_game(game)]


def team_id(team: dict[str, Any]) -> str:
    abbr = (team.get("abbreviation") or team.get("teamCode") or "").upper()
    return TEAM_ID_OVERRIDES.get(abbr, abbr)


def split_name(person: dict[str, Any]) -> tuple[str | None, str | None]:
    full = person.get("fullName") or ""
    parts = full.strip().split()
    if not parts:
        return None, None
    if len(parts) >= 3 and parts[-1].rstrip(".").lower() in {"jr", "sr", "ii", "iii", "iv", "v"}:
        return " ".join(parts[:-2]) or parts[0], " ".join(parts[-2:])
    return " ".join(parts[:-1]) or parts[0], parts[-1]


def int_stat(stats: dict[str, Any], key: str) -> int:
    value = stats.get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def stat_group_played(stats: dict[str, Any], group: str) -> bool:
    group_stats = stats.get(group) or {}
    if not group_stats:
        return False
    if int_stat(group_stats, "gamesPlayed") > 0:
        return True
    if int_stat(group_stats, "gamesStarted") > 0:
        return True
    if group == "pitching" and (
        int_stat(group_stats, "gamesPitched") > 0
        or int_stat(group_stats, "battersFaced") > 0
        or int_stat(group_stats, "outs") > 0
    ):
        return True
    if group == "batting" and (
        int_stat(group_stats, "plateAppearances") > 0
        or int_stat(group_stats, "atBats") > 0
        or int_stat(group_stats, "runs") > 0
    ):
        return True
    if group == "fielding" and (
        int_stat(group_stats, "putOuts") > 0
        or int_stat(group_stats, "assists") > 0
        or int_stat(group_stats, "errors") > 0
    ):
        return True
    return False


def appeared_in_game(entry: dict[str, Any]) -> tuple[int, int, int]:
    stats = entry.get("stats") or {}
    pitched = 1 if stat_group_played(stats, "pitching") else 0
    batted = 1 if stat_group_played(stats, "batting") else 0
    fielded = 1 if stat_group_played(stats, "fielding") else 0
    total = 1 if pitched or batted or fielded else 0
    return total, pitched, batted


def ensure_live_schema(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mlb_live_game_imports (
                game_pk INTEGER PRIMARY KEY,
                game_date DATE NOT NULL,
                season INTEGER NOT NULL,
                status TEXT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mlb_live_player_games (
                game_pk INTEGER NOT NULL REFERENCES mlb_live_game_imports(game_pk)
                    ON DELETE CASCADE,
                game_date DATE NOT NULL,
                season INTEGER NOT NULL,
                player_id TEXT NOT NULL REFERENCES players(player_id),
                team_id TEXT NOT NULL,
                games_total INTEGER NOT NULL DEFAULT 1,
                games_pitched INTEGER NOT NULL DEFAULT 0,
                games_batted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (game_pk, player_id, team_id),
                FOREIGN KEY (team_id, season) REFERENCES teams(team_id, season)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_mlb_live_player_games_rollup "
            "ON mlb_live_player_games(season, player_id, team_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_mlb_live_player_games_date "
            "ON mlb_live_player_games(game_date DESC)"
        )
    conn.commit()


def ensure_team(conn: "psycopg.Connection", team: dict[str, Any], season: int) -> str:
    tid = team_id(team)
    if not tid:
        raise ValueError(f"Could not map MLB team: {team}")
    franchise = team.get("franchiseName") or team.get("locationName") or team.get("name") or tid
    name = team.get("name") or team.get("clubName") or tid
    league = team.get("league", {}).get("abbreviation") or team.get("league", {}).get("name")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO franchises (franchise_id, name, active)
            VALUES (%s, %s, true)
            ON CONFLICT (franchise_id) DO UPDATE
            SET name = COALESCE(EXCLUDED.name, franchises.name),
                active = true
            """,
            (tid, franchise),
        )
        cur.execute(
            """
            INSERT INTO teams (team_id, season, franchise_id, league, name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (team_id, season) DO UPDATE
            SET franchise_id = EXCLUDED.franchise_id,
                league = EXCLUDED.league,
                name = EXCLUDED.name
            """,
            (tid, season, tid, league, name),
        )
    return tid


def load_caches(conn: "psycopg.Connection", season: int) -> tuple[dict[int, str], set[tuple[int, str]]]:
    with conn.cursor() as cur:
        players = {
            int(row[0]): row[1]
            for row in cur.execute(
                "SELECT mlbam_id, player_id FROM players WHERE mlbam_id IS NOT NULL"
            ).fetchall()
        }
        teams = {
            (int(row[1]), row[0])
            for row in cur.execute(
                "SELECT team_id, season FROM teams WHERE season = %s",
                (season,),
            ).fetchall()
        }
    return players, teams


def find_or_create_player(
    conn: "psycopg.Connection",
    person: dict[str, Any],
    season: int,
    position: str | None,
) -> str:
    mlbam_id = int(person["id"])
    with conn.cursor() as cur:
        row = cur.execute(
            "SELECT player_id FROM players WHERE mlbam_id = %s",
            (mlbam_id,),
        ).fetchone()
        if row:
            player_id = row[0]
            cur.execute(
                """
                UPDATE players
                SET final_year = GREATEST(COALESCE(final_year, %s), %s),
                    primary_pos = COALESCE(primary_pos, %s)
                WHERE player_id = %s
                """,
                (season, season, position, player_id),
            )
            return player_id

        first, last = split_name(person)
        player_id = f"mlbam_{mlbam_id}"
        cur.execute(
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
            (player_id, mlbam_id, first, last, season, season, position),
        )
        return player_id


def fetch_boxscore_rows(game: dict[str, Any]) -> list[RawAppearance]:
    game_pk = int(game["gamePk"])
    boxscore = get_json(f"/game/{game_pk}/boxscore")
    rows: list[RawAppearance] = []

    for side in ("away", "home"):
        side_box = boxscore.get("teams", {}).get(side, {})
        team = side_box.get("team", {})
        for player_entry in (side_box.get("players") or {}).values():
            total, pitched, batted = appeared_in_game(player_entry)
            if not total:
                continue
            rows.append(
                RawAppearance(
                    person=player_entry["person"],
                    team=team,
                    position=(player_entry.get("position") or {}).get("abbreviation"),
                    games_total=total,
                    games_pitched=pitched,
                    games_batted=batted,
                )
            )
    return rows


def materialize_appearances(
    conn: "psycopg.Connection | None",
    game: dict[str, Any],
    rows: list[RawAppearance],
    dry_run: bool,
    player_cache: dict[int, str] | None,
    team_cache: set[tuple[int, str]] | None,
) -> list[GameAppearance]:
    game_pk = int(game["gamePk"])
    season = int(game.get("season") or game.get("seasonDisplay"))
    game_day = parse_date(game.get("officialDate") or game["gameDate"][:10])
    appearances: list[GameAppearance] = []

    for row in rows:
        tid = team_id(row.team)
        if conn and not dry_run:
            if (season, tid) not in (team_cache or set()):
                tid = ensure_team(conn, row.team, season)
                if team_cache is not None:
                    team_cache.add((season, tid))

        if conn and not dry_run:
            mlbam_id = int(row.person["id"])
            if player_cache is not None and mlbam_id in player_cache:
                pid = player_cache[mlbam_id]
            else:
                pid = find_or_create_player(conn, row.person, season, row.position)
                if player_cache is not None:
                    player_cache[mlbam_id] = pid
        else:
            pid = f"mlbam_{row.person['id']}"
        appearances.append(
            GameAppearance(
                game_pk=game_pk,
                game_date=game_day,
                season=season,
                player_id=pid,
                mlbam_id=int(row.person["id"]),
                team_id=tid,
                games_total=row.games_total,
                games_pitched=row.games_pitched,
                games_batted=row.games_batted,
            )
        )
    return appearances


def upsert_game(
    conn: "psycopg.Connection",
    game: dict[str, Any],
    appearances: list[GameAppearance],
) -> None:
    game_pk = int(game["gamePk"])
    season = int(game.get("season") or game.get("seasonDisplay"))
    game_day = parse_date(game.get("officialDate") or game["gameDate"][:10])
    status = (game.get("status") or {}).get("detailedState") or "Final"

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mlb_live_game_imports (game_pk, game_date, season, status, row_count, imported_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (game_pk) DO UPDATE
            SET game_date = EXCLUDED.game_date,
                season = EXCLUDED.season,
                status = EXCLUDED.status,
                row_count = EXCLUDED.row_count,
                imported_at = now()
            """,
            (game_pk, game_day, season, status, len(appearances)),
        )
        cur.execute("DELETE FROM mlb_live_player_games WHERE game_pk = %s", (game_pk,))
        cur.executemany(
            """
            INSERT INTO mlb_live_player_games
                (game_pk, game_date, season, player_id, team_id, games_total, games_pitched, games_batted)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    row.game_pk,
                    row.game_date,
                    row.season,
                    row.player_id,
                    row.team_id,
                    row.games_total,
                    row.games_pitched,
                    row.games_batted,
                )
                for row in appearances
            ],
        )


def refresh_runtime_tables(conn: "psycopg.Connection", season: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE players
            SET final_year = GREATEST(COALESCE(final_year, %s), %s)
            WHERE player_id IN (
                SELECT DISTINCT player_id
                FROM mlb_live_player_games
                WHERE season = %s
            )
            """,
            (season, season, season),
        )
        cur.execute(
            """
            WITH live AS (
                SELECT player_id, team_id, season,
                       SUM(games_total)::integer AS games_total,
                       SUM(games_pitched)::integer AS games_pitched,
                       SUM(games_batted)::integer AS games_batted
                FROM mlb_live_player_games
                WHERE season = %s
                GROUP BY player_id, team_id, season
            )
            INSERT INTO appearances
                (player_id, team_id, season, games_total, games_pitched, games_batted)
            SELECT player_id, team_id, season, games_total, games_pitched, games_batted
            FROM live
            ON CONFLICT (player_id, team_id, season) DO UPDATE
            SET games_total = EXCLUDED.games_total,
                games_pitched = EXCLUDED.games_pitched,
                games_batted = EXCLUDED.games_batted
            """,
            (season,),
        )
        cur.execute(
            """
            WITH live AS (
                SELECT player_id, team_id, season,
                       MIN(game_date) AS first_date,
                       MAX(game_date) AS last_date
                FROM mlb_live_player_games
                WHERE season = %s
                GROUP BY player_id, team_id, season
            )
            INSERT INTO player_stints
                (player_id, team_id, season, first_unit, last_unit, first_label, last_label, source)
            SELECT player_id, team_id, season,
                   to_char(first_date, 'YYYYMMDD')::integer,
                   to_char(last_date, 'YYYYMMDD')::integer,
                   to_char(first_date, 'YYYY-MM-DD'),
                   to_char(last_date, 'YYYY-MM-DD'),
                   %s
            FROM live
            ON CONFLICT (player_id, team_id, season) DO UPDATE
            SET first_unit = EXCLUDED.first_unit,
                last_unit = EXCLUDED.last_unit,
                first_label = EXCLUDED.first_label,
                last_label = EXCLUDED.last_label,
                source = EXCLUDED.source
            """,
            (season, SOURCE),
        )
        cur.execute(
            """
            INSERT INTO teammate_stint_coverage (season, coverage_type, strict, source, updated_at)
            VALUES (%s, 'game-log', 1, %s, now())
            ON CONFLICT (season) DO UPDATE
            SET coverage_type = EXCLUDED.coverage_type,
                strict = EXCLUDED.strict,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            (season, SOURCE),
        )
        cur.execute(
            """
            INSERT INTO data_provenance (source, season, row_count, fetched_at)
            SELECT %s, %s, COUNT(*), now()
            FROM mlb_live_player_games
            WHERE season = %s
            ON CONFLICT (source, season) DO UPDATE
            SET row_count = EXCLUDED.row_count,
                fetched_at = now()
            """,
            (SOURCE, season, season),
        )
    refresh_search_index(conn, season)


def display_name(first: str | None, last: str | None) -> str:
    return " ".join(part for part in (first, last) if part).strip()


def refresh_search_index(conn: "psycopg.Connection", season: int) -> None:
    with conn.cursor() as cur:
        rows = cur.execute(
            """
            WITH live_players AS (
                SELECT DISTINCT player_id
                FROM mlb_live_player_games
                WHERE season = %s
            ),
            careers AS (
                SELECT player_id, SUM(games_total)::integer AS career_games
                FROM appearances
                WHERE player_id IN (SELECT player_id FROM live_players)
                GROUP BY player_id
            ),
            teammate_counts AS (
                SELECT a.player_id, COUNT(DISTINCT b.player_id)::integer AS teammate_count
                FROM appearances a
                JOIN appearances b
                  ON b.team_id = a.team_id
                 AND b.season = a.season
                 AND b.player_id <> a.player_id
                WHERE a.player_id IN (SELECT player_id FROM live_players)
                GROUP BY a.player_id
            )
            SELECT p.player_id, p.name_first, p.name_last, p.primary_pos,
                   p.debut_year, p.final_year,
                   COALESCE(c.career_games, 0) AS career_games,
                   COALESCE(t.teammate_count, 0) AS teammate_count
            FROM players p
            JOIN live_players lp ON lp.player_id = p.player_id
            LEFT JOIN careers c ON c.player_id = p.player_id
            LEFT JOIN teammate_counts t ON t.player_id = p.player_id
            """,
            (season,),
        ).fetchall()
        payload = []
        for row in rows:
            name = display_name(row[1], row[2])
            key = normalize(name)
            last_key = normalize(row[2] or name)
            years = f"{row[4] or '?'}-{row[5] or '?'}"
            pos = row[3] or "MLB"
            payload.append((row[0], name, f"{pos}, {years}", key, last_key, row[6], row[7]))
        cur.executemany(
            """
            INSERT INTO players_searchable
                (player_id, display_name, disambiguation, search_key, last_key, career_games, teammate_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id) DO UPDATE
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
            "SELECT COUNT(*) FROM mlb_live_game_imports WHERE season = %s",
            (season,),
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
    games = [game for game in scheduled_games(start, end) if int(game.get("season") or season) == season]
    appearance_rows = 0
    player_cache: dict[int, str] | None = None
    team_cache: set[tuple[int, str]] | None = None
    if conn and not dry_run:
        player_cache, team_cache = load_caches(conn, season)

    chunk_size = max(workers * 6, 25)
    processed = 0
    for chunk_start in range(0, len(games), chunk_size):
        chunk = games[chunk_start : chunk_start + chunk_size]
        fetched: dict[int, list[RawAppearance]] = {}
        with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
            futures = {pool.submit(fetch_boxscore_rows, game): i for i, game in enumerate(chunk)}
            for future in as_completed(futures):
                fetched[futures[future]] = future.result()

        for offset, game in enumerate(chunk):
            index = chunk_start + offset + 1
            rows = fetched[offset]
            appearances = materialize_appearances(
                conn,
                game,
                rows,
                dry_run,
                player_cache,
                team_cache,
            )
            appearance_rows += len(appearances)
            processed += 1
            if dry_run:
                print(f"  dry-run game {game['gamePk']}: {len(appearances)} player appearances")
            else:
                assert conn is not None
                upsert_game(conn, game, appearances)
                if processed % 50 == 0:
                    conn.commit()
                if index == 1 or index == len(games) or index % 50 == 0:
                    print(
                        f"  imported {index:>4}/{len(games)} games "
                        f"({appearance_rows:,} appearances so far)",
                        flush=True,
                    )
    if conn and not dry_run:
        conn.commit()
    return len(games), appearance_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=datetime.now(EASTERN).year)
    parser.add_argument("--start-date", type=parse_date)
    parser.add_argument("--end-date", type=parse_date)
    parser.add_argument("--backfill-days", type=int, default=3)
    parser.add_argument("--season-to-date", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.season_to_date:
        start = season_start(args.season)
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

    print(f"MLB live update {args.season}: {start} through {end}")

    if args.dry_run:
        games, appearances = import_window(None, start, end, args.season, True, args.workers)
        print(f"dry-run complete: {games} final games, {appearances} player appearances")
        return 0

    assert pg_url
    safe_url = pg_url.split("@", 1)[-1] if "@" in pg_url else pg_url
    print(f"target: {safe_url}")
    with psycopg.connect(pg_url, autocommit=False, prepare_threshold=None) as conn:
        ensure_live_schema(conn)
        if not args.season_to_date and not (args.start_date or args.end_date) and should_backfill_season(conn, args.season):
            start = season_start(args.season)
            end = datetime.now(EASTERN).date()
            print(f"no live imports found for {args.season}; expanding first run to {start} through {end}")
        games, appearances = import_window(conn, start, end, args.season, False, args.workers)
        refresh_runtime_tables(conn, args.season)
        conn.commit()

    print(f"complete: {games} final games, {appearances} player appearances imported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
