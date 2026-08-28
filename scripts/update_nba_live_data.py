#!/usr/bin/env python3
"""Import completed NBA regular-season games from ESPN into production.

The historical Basketball proof graph uses SportsDataverse/ESPN player
boxscores. This live updater follows the same rule: a player counts only when
the ESPN boxscore shows positive minutes in a completed regular-season game.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

try:
    import psycopg
except ImportError:
    print("ERROR: install psycopg first: pip install 'psycopg[binary]'", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_nba_espn_game_teammates import ESPN_TO_NBA_TEAM, load_crosswalk  # noqa: E402
from name_normalize import normalize  # noqa: E402

SPORT_ID = "basketball"
SOURCE = "espn_nba_scoreboard_boxscore"
SCHEMA = ROOT / "db" / "cross_sport_schema_postgres.sql"
DEFAULT_CROSSWALK = ROOT / "raw" / "nba_identity" / "espn_to_nba_crosswalk_auto.csv"
EASTERN = ZoneInfo("America/New_York")
API_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"


@dataclass(frozen=True)
class LiveAppearance:
    game_id: str
    game_date: date
    season: int
    player_id: str
    external_id: str | None
    espn_id: str
    display_name: str
    team_id: str
    team_name: str
    position: str | None
    minutes: float
    points: int
    headshot_url: str | None


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def default_season(today: date) -> int:
    return today.year if today.month >= 9 else today.year - 1


def default_window(backfill_days: int) -> tuple[date, date]:
    today = datetime.now(EASTERN).date()
    return today - timedelta(days=max(backfill_days - 1, 0)), today


def season_start(season: int) -> date:
    return date(season, 10, 1)


def get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    response = requests.get(f"{API_BASE}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def final_regular_season_event(event: dict[str, Any]) -> bool:
    season = event.get("season") or {}
    if int(season.get("type") or 0) != 2:
        return False
    competitions = event.get("competitions") or []
    if not competitions:
        return False
    status_type = ((competitions[0].get("status") or {}).get("type") or {})
    return bool(status_type.get("completed")) and status_type.get("state") == "post"


def scheduled_games(start: date, end: date, season: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    games: list[dict[str, Any]] = []
    for day in date_range(start, end):
        data = get_json("/scoreboard", {"dates": day.strftime("%Y%m%d"), "limit": 100})
        for event in data.get("events") or []:
            if not final_regular_season_event(event):
                continue
            season_year = int((event.get("season") or {}).get("year") or 0)
            season_key = season_year - 1
            if season_key != season:
                continue
            game_id = str(event.get("id") or "")
            if game_id and game_id not in seen:
                seen.add(game_id)
                games.append(event)
    return games


def parse_minutes(value: Any) -> float:
    text = str(value or "").strip()
    if not text or text in {"--", "DNP"}:
        return 0.0
    if ":" in text:
        minutes, seconds = text.split(":", 1)
        try:
            return float(minutes) + float(seconds) / 60.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace("+", "")))
    except ValueError:
        return 0


def last_name(display_name: str) -> str:
    parts = [part for part in display_name.replace(".", " ").split() if part]
    if len(parts) > 1 and parts[-1].lower() in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts = parts[:-1]
    return parts[-1] if parts else display_name


def first_name(display_name: str) -> str:
    parts = [part for part in display_name.split() if part]
    return parts[0] if parts else display_name


def load_player_map(conn: "psycopg.Connection", crosswalk_path: Path) -> dict[str, tuple[str, str | None]]:
    mapping = {espn_id: (player_id, external_id) for espn_id, (player_id, external_id) in load_crosswalk(crosswalk_path).items()}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT player_id, REPLACE(player_id, 'nba_espn:', '') AS espn_id, external_id
              FROM sport_players
             WHERE sport_id = %s
               AND player_id LIKE 'nba_espn:%%'
            """,
            (SPORT_ID,),
        )
        for player_id, espn_id, external_id in cur.fetchall():
            mapping[str(espn_id)] = (str(player_id), str(external_id) if external_id else None)
    return mapping


def unique_existing_name_map(conn: "psycopg.Connection") -> dict[str, tuple[str, str | None]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT LOWER(display_name), COUNT(*), MIN(player_id), MIN(external_id)
              FROM sport_players
             WHERE sport_id = %s
             GROUP BY LOWER(display_name)
            HAVING COUNT(*) = 1
            """,
            (SPORT_ID,),
        )
        return {name: (player_id, external_id) for name, _count, player_id, external_id in cur.fetchall()}


def resolve_player(
    espn_id: str,
    display_name: str,
    player_map: dict[str, tuple[str, str | None]],
    name_map: dict[str, tuple[str, str | None]],
) -> tuple[str, str | None, bool]:
    mapped = player_map.get(espn_id)
    if mapped:
        return mapped[0], mapped[1], False
    name_match = name_map.get(display_name.lower())
    if name_match:
        player_map[espn_id] = name_match
        return name_match[0], name_match[1], False
    player_id = f"nba_espn:{espn_id}"
    player_map[espn_id] = (player_id, None)
    return player_id, None, True


def fetch_game_appearances(
    event: dict[str, Any],
    player_map: dict[str, tuple[str, str | None]],
    name_map: dict[str, tuple[str, str | None]],
) -> tuple[list[LiveAppearance], dict[str, str]]:
    game_id = str(event["id"])
    game_day = parse_date((event.get("date") or "")[:10])
    season = int((event.get("season") or {}).get("year") or 0) - 1
    summary = get_json("/summary", {"event": game_id})
    appearances: list[LiveAppearance] = []
    teams: dict[str, str] = {}
    boxscore = summary.get("boxscore") or {}
    for team_box in boxscore.get("players") or []:
        espn_team = team_box.get("team") or {}
        team_id = ESPN_TO_NBA_TEAM.get(str(espn_team.get("id") or "").strip())
        team_name = espn_team.get("displayName") or espn_team.get("name") or team_id or ""
        if not team_id:
            continue
        teams[team_id] = team_name
        for stat_group in team_box.get("statistics") or []:
            keys = stat_group.get("keys") or []
            try:
                minutes_index = keys.index("minutes")
            except ValueError:
                minutes_index = 0
            points_index = keys.index("points") if "points" in keys else None
            for athlete_row in stat_group.get("athletes") or []:
                if athlete_row.get("didNotPlay"):
                    continue
                athlete = athlete_row.get("athlete") or {}
                espn_id = str(athlete.get("id") or "").strip()
                display_name = str(athlete.get("displayName") or "").strip()
                stats = athlete_row.get("stats") or []
                minutes = parse_minutes(stats[minutes_index] if minutes_index < len(stats) else "")
                if not espn_id or not display_name or minutes <= 0:
                    continue
                player_id, external_id, _temporary = resolve_player(espn_id, display_name, player_map, name_map)
                position = ((athlete.get("position") or {}).get("abbreviation") or None)
                headshot = ((athlete.get("headshot") or {}).get("href") or None)
                points = parse_int(stats[points_index]) if points_index is not None and points_index < len(stats) else 0
                appearances.append(
                    LiveAppearance(
                        game_id=game_id,
                        game_date=game_day,
                        season=season,
                        player_id=player_id,
                        external_id=external_id,
                        espn_id=espn_id,
                        display_name=display_name,
                        team_id=team_id,
                        team_name=team_name,
                        position=position,
                        minutes=minutes,
                        points=points,
                        headshot_url=headshot,
                    )
                )
    return appearances, teams


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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sport_teammates_pair ON sport_teammates(sport_id, player_a_id, player_b_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sport_teammates_a ON sport_teammates(sport_id, player_a_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sport_teammates_b ON sport_teammates(sport_id, player_b_id)")
    conn.commit()


def ensure_team_rows(conn: "psycopg.Connection", season: int, teams: dict[str, str]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sport_franchises (sport_id, franchise_id, name, active)
            VALUES (%s, %s, %s, true)
            ON CONFLICT (sport_id, franchise_id) DO UPDATE
            SET name = EXCLUDED.name,
                active = true
            """,
            [(SPORT_ID, team_id, name) for team_id, name in teams.items()],
        )
        cur.executemany(
            """
            INSERT INTO sport_teams (sport_id, team_id, season, franchise_id, name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sport_id, team_id, season) DO UPDATE
            SET franchise_id = EXCLUDED.franchise_id,
                name = EXCLUDED.name
            """,
            [(SPORT_ID, team_id, season, team_id, name) for team_id, name in teams.items()],
        )


def ensure_players(conn: "psycopg.Connection", rows: list[LiveAppearance]) -> int:
    inserted = 0
    seen: dict[str, LiveAppearance] = {}
    for row in rows:
        seen.setdefault(row.player_id, row)
    with conn.cursor() as cur:
        for row in seen.values():
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
                    debut_year = LEAST(COALESCE(sport_players.debut_year, EXCLUDED.debut_year), EXCLUDED.debut_year),
                    final_year = GREATEST(COALESCE(sport_players.final_year, EXCLUDED.final_year), EXCLUDED.final_year),
                    primary_pos = COALESCE(sport_players.primary_pos, EXCLUDED.primary_pos)
                """,
                (
                    SPORT_ID,
                    row.player_id,
                    row.external_id,
                    row.display_name,
                    first_name(row.display_name),
                    last_name(row.display_name),
                    row.season,
                    row.season,
                    row.position,
                ),
            )
            inserted += 1 if cur.rowcount == 1 else 0
            cur.execute(
                """
                INSERT INTO sport_players_searchable
                    (sport_id, player_id, display_name, disambiguation, search_key,
                     last_key, career_games, teammate_count)
                VALUES (%s, %s, %s, %s, %s, %s, 0, 0)
                ON CONFLICT (sport_id, player_id) DO NOTHING
                """,
                (
                    SPORT_ID,
                    row.player_id,
                    row.display_name,
                    f"{row.position or 'NBA'}, {row.season}-{row.season}",
                    normalize(row.display_name),
                    normalize(last_name(row.display_name)),
                ),
            )
            if row.headshot_url:
                cur.execute(
                    """
                    INSERT INTO sport_player_images (sport_id, player_id, source_url)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (sport_id, player_id) DO NOTHING
                    """,
                    (SPORT_ID, row.player_id, row.headshot_url),
                )
    return inserted


def upsert_live_rows(conn: "psycopg.Connection", event: dict[str, Any], rows: list[LiveAppearance]) -> None:
    if not rows:
        return
    game_id = str(event["id"])
    game_day = rows[0].game_date
    season = rows[0].season
    status = (((event.get("competitions") or [{}])[0].get("status") or {}).get("type") or {}).get("description") or "Final"
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
            (SPORT_ID, game_id, game_day, season, status, SOURCE, len(rows)),
        )
        cur.execute("DELETE FROM sport_live_player_games WHERE sport_id = %s AND game_id = %s", (SPORT_ID, game_id))
        cur.executemany(
            """
            INSERT INTO sport_live_player_games
                (sport_id, game_id, game_date, season, player_id, team_id, position,
                 games_total, goals, assists, points)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 0, 0, %s)
            """,
            [
                (SPORT_ID, row.game_id, row.game_date, row.season, row.player_id, row.team_id, row.position, row.points)
                for row in rows
            ],
        )


def refresh_runtime_tables(conn: "psycopg.Connection", season: int) -> None:
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15min'")
        cur.execute(
            """
            WITH live AS (
                SELECT player_id, team_id, season,
                       SUM(games_total)::integer AS games_total,
                       MIN(game_date) AS first_date,
                       MAX(game_date) AS last_date
                  FROM sport_live_player_games
                 WHERE sport_id = %s AND season = %s
                 GROUP BY player_id, team_id, season
            )
            INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
            SELECT %s, player_id, team_id, season, games_total
              FROM live
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET games_total = EXCLUDED.games_total
            """,
            (SPORT_ID, season, SPORT_ID),
        )
        cur.execute(
            """
            WITH live AS (
                SELECT player_id, team_id, season,
                       MIN(game_date) AS first_date,
                       MAX(game_date) AS last_date
                  FROM sport_live_player_games
                 WHERE sport_id = %s AND season = %s
                 GROUP BY player_id, team_id, season
            )
            INSERT INTO sport_player_stints
                (sport_id, player_id, team_id, season, first_unit, last_unit,
                 first_label, last_label, source)
            SELECT %s, player_id, team_id, season,
                   to_char(first_date, 'YYYYMMDD')::integer,
                   to_char(last_date, 'YYYYMMDD')::integer,
                   first_date::text,
                   last_date::text,
                   %s
              FROM live
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET first_unit = EXCLUDED.first_unit,
                last_unit = EXCLUDED.last_unit,
                first_label = EXCLUDED.first_label,
                last_label = EXCLUDED.last_label,
                source = EXCLUDED.source
            """,
            (SPORT_ID, season, SPORT_ID, SOURCE),
        )
        cur.execute(
            """
            UPDATE sport_players p
               SET final_year = GREATEST(COALESCE(p.final_year, %s), %s)
             WHERE p.sport_id = %s
               AND EXISTS (
                   SELECT 1 FROM sport_live_player_games live
                    WHERE live.sport_id = p.sport_id
                      AND live.player_id = p.player_id
                      AND live.season = %s
               )
            """,
            (season, season, SPORT_ID, season),
        )
        cur.execute("DELETE FROM sport_teammates WHERE sport_id = %s AND season = %s", (SPORT_ID, season))
        cur.execute(
            """
            INSERT INTO sport_teammates (sport_id, player_a_id, player_b_id, team_id, season)
            SELECT DISTINCT %s, a.player_id, b.player_id, a.team_id, a.season
              FROM sport_live_player_games a
              JOIN sport_live_player_games b
                ON b.sport_id = a.sport_id
               AND b.game_id = a.game_id
               AND b.team_id = a.team_id
               AND b.player_id > a.player_id
             WHERE a.sport_id = %s
               AND a.season = %s
            ON CONFLICT DO NOTHING
            """,
            (SPORT_ID, SPORT_ID, season),
        )
        cur.execute(
            """
            INSERT INTO sport_teammate_stint_coverage
                (sport_id, season, coverage_type, strict, source, updated_at)
            VALUES (%s, %s, 'game_boxscore', 1, %s, now())
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
            WITH careers AS (
                SELECT player_id, SUM(games_total)::integer AS career_games
                  FROM sport_appearances
                 WHERE sport_id = %s
                 GROUP BY player_id
            ),
            teammate_counts AS (
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
            UPDATE sport_players_searchable s
               SET career_games = COALESCE(c.career_games, s.career_games),
                   teammate_count = COALESCE(tc.teammate_count, 0),
                   disambiguation = COALESCE(NULLIF(p.primary_pos, ''), 'NBA') || ', '
                                    || COALESCE(p.debut_year::text, '?') || '-'
                                    || COALESCE(p.final_year::text, '?')
              FROM sport_players p
              LEFT JOIN careers c ON c.player_id = p.player_id
              LEFT JOIN teammate_counts tc ON tc.player_id = p.player_id
             WHERE s.sport_id = %s
               AND p.sport_id = s.sport_id
               AND p.player_id = s.player_id
            """,
            (SPORT_ID, SPORT_ID, SPORT_ID, SPORT_ID),
        )


def import_window(conn: "psycopg.Connection", start: date, end: date, season: int, dry_run: bool) -> tuple[int, int, int]:
    player_map = load_player_map(conn, DEFAULT_CROSSWALK)
    name_map = unique_existing_name_map(conn)
    games = scheduled_games(start, end, season)
    print(f"discovered {len(games):,} completed regular-season NBA games for {season}", flush=True)
    imported_games = 0
    imported_rows = 0
    temporary_players: dict[str, str] = {}
    teams: dict[str, str] = {}
    for index, game in enumerate(games, 1):
        rows, game_teams = fetch_game_appearances(game, player_map, name_map)
        teams.update(game_teams)
        for row in rows:
            if row.player_id.startswith("nba_espn:"):
                temporary_players[row.player_id] = row.display_name
        imported_rows += len(rows)
        if dry_run:
            print(f"  dry-run game {game['id']}: {len(rows)} player appearances")
        else:
            ensure_team_rows(conn, season, game_teams)
            ensure_players(conn, rows)
            upsert_live_rows(conn, game, rows)
            imported_games += 1
            if index == 1 or index == len(games) or index % 10 == 0:
                print(f"  imported {index:>3}/{len(games)} games ({imported_rows:,} appearances)", flush=True)
    if not dry_run and imported_games:
        conn.commit()
        refresh_runtime_tables(conn, season)
        conn.commit()
    if temporary_players:
        sample = ", ".join(
            f"{name} ({player_id})"
            for player_id, name in sorted(temporary_players.items())[:12]
        )
        print(f"temporary ESPN-backed Basketball players: {sample}", flush=True)
    return imported_games if not dry_run else len(games), imported_rows, len(temporary_players)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int)
    parser.add_argument("--start-date", type=parse_date)
    parser.add_argument("--end-date", type=parse_date)
    parser.add_argument("--backfill-days", type=int, default=4)
    parser.add_argument("--season-to-date", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    today = datetime.now(EASTERN).date()
    season = args.season or default_season(today)
    if args.season_to_date:
        start = season_start(season)
        end = args.end_date or today
    elif args.start_date or args.end_date:
        start = args.start_date or args.end_date
        end = args.end_date or args.start_date
    else:
        start, end = default_window(args.backfill_days)
    if start is None or end is None:
        raise ValueError("Could not determine import window")
    if start > end:
        start, end = end, start

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required in .env.", file=sys.stderr)
        return 1
    safe_url = database_url.split("@", 1)[-1] if "@" in database_url else database_url
    print(f"NBA live update {season}: {start} through {end}")
    print(f"target: {safe_url}")

    started = time.monotonic()
    with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as conn:
        ensure_tables(conn)
        games, rows, temp_players = import_window(conn, start, end, season, args.dry_run)
    print(
        f"complete: {games:,} games, {rows:,} player appearances, "
        f"{temp_players:,} temporary ESPN-backed players, {time.monotonic() - started:.1f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
