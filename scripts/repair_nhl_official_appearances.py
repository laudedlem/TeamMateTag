#!/usr/bin/env python3
"""Repair NHL team-season appearances from official NHL player landing data."""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

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
sys.path.insert(0, str(ROOT))

from web.server import NHL_TEAM_NAMES  # noqa: E402


SPORT_ID = "hockey"
SOURCE = "nhl_official_player_landing"
API = "https://api-web.nhle.com/v1/player/{}/landing"
GAME_LOG_API = "https://api-web.nhle.com/v1/player/{}/game-log/{}/2"

TEAM_ID_BY_NAME = {name.lower(): team_id for team_id, name in NHL_TEAM_NAMES.items()}
TEAM_ID_BY_NAME.update(
    {
        "arizona coyotes": "ARI",
        "atlanta flames": "AFM",
        "atlanta thrashers": "ATL",
        "california golden seals": "CLR",
        "cleveland barons": "CLE",
        "colorado rockies": "COR",
        "hartford whalers": "HFD",
        "minnesota north stars": "MNS",
        "phoenix coyotes": "PHX",
        "quebec nordiques": "QUE",
        "utah hockey club": "UTA",
        "utah mammoth": "UTA",
        "winnipeg jets": "WPG",
    }
)


def get_json(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("default") or next(iter(value.values()), "") or "")
    return str(value or "")


def season_start(raw_season: Any) -> int | None:
    try:
        return int(str(raw_season)[:4])
    except (TypeError, ValueError):
        return None


def official_rows(external_id: str) -> list[tuple[str, str, int, int]]:
    payload = get_json(API.format(external_id))
    rows: list[tuple[str, str, int, int]] = []
    for item in payload.get("seasonTotals", []) or []:
        if item.get("leagueAbbrev") != "NHL" or int(item.get("gameTypeId") or 0) != 2:
            continue
        games = int(item.get("gamesPlayed") or 0)
        season = season_start(item.get("season"))
        team_name = localized(item.get("teamName")).strip()
        team_id = TEAM_ID_BY_NAME.get(team_name.lower())
        if not team_id or not season or games <= 0:
            continue
        rows.append((team_id, team_name, season, games))
    return rows


def game_log_stints(external_id: str, seasons: set[int]) -> dict[tuple[str, int], tuple[int, int, str, str]]:
    stints: dict[tuple[str, int], tuple[int, int, str, str]] = {}
    for season in sorted(seasons):
        try:
            payload = get_json(GAME_LOG_API.format(external_id, f"{season}{season + 1}"))
        except requests.RequestException:
            continue
        dates_by_team: dict[str, list[str]] = defaultdict(list)
        for game in payload.get("gameLog", []) or []:
            team_id = str(game.get("teamAbbrev") or "").strip()
            game_date = str(game.get("gameDate") or "").strip()
            if team_id and game_date:
                dates_by_team[team_id].append(game_date)
        for team_id, dates in dates_by_team.items():
            first = min(dates)
            last = max(dates)
            first_unit = int(datetime.strptime(first, "%Y-%m-%d").strftime("%Y%m%d"))
            last_unit = int(datetime.strptime(last, "%Y-%m-%d").strftime("%Y%m%d"))
            stints[(team_id, season)] = (first_unit, last_unit, first, last)
    return stints


def repair_player(conn: "psycopg.Connection", player_id: str, external_id: str) -> tuple[int, int]:
    rows = official_rows(external_id)
    if not rows:
        return 0, 0
    seasons = {season for _, _, season, _ in rows}
    stints = game_log_stints(external_id, seasons)
    min_season = min(seasons)
    max_season = max(seasons)
    with conn.cursor() as cur:
        for team_id, team_name, season, _games in rows:
            cur.execute(
                """
                INSERT INTO sport_franchises (sport_id, franchise_id, name, active)
                VALUES (%s, %s, %s, true)
                ON CONFLICT (sport_id, franchise_id) DO UPDATE
                SET name = EXCLUDED.name
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
        cur.execute(
            """
            DELETE FROM sport_appearances
             WHERE sport_id = %s
               AND player_id = %s
               AND season = ANY(%s)
            """,
            (SPORT_ID, player_id, sorted(seasons)),
        )
        cur.executemany(
            """
            INSERT INTO sport_appearances
                (sport_id, player_id, team_id, season, games_total)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET games_total = EXCLUDED.games_total
            """,
            [(SPORT_ID, player_id, team_id, season, games) for team_id, _team_name, season, games in rows],
        )
        stint_rows = []
        for team_id, _team_name, season, _games in rows:
            stint = stints.get((team_id, season))
            if not stint:
                first = f"{season}-09-01"
                last = f"{season + 1}-06-30"
                stint = (
                    int(first.replace("-", "")),
                    int(last.replace("-", "")),
                    first,
                    last,
                )
            first_unit, last_unit, first_label, last_label = stint
            stint_rows.append((SPORT_ID, player_id, team_id, season, first_unit, last_unit, first_label, last_label, SOURCE))
        cur.executemany(
            """
            INSERT INTO sport_player_stints
                (sport_id, player_id, team_id, season, first_unit, last_unit, first_label, last_label, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET first_unit = EXCLUDED.first_unit,
                last_unit = EXCLUDED.last_unit,
                first_label = EXCLUDED.first_label,
                last_label = EXCLUDED.last_label,
                source = EXCLUDED.source
            """,
            stint_rows,
        )
        cur.executemany(
            """
            INSERT INTO sport_teammate_stint_coverage
                (sport_id, season, coverage_type, strict, source, updated_at)
            VALUES (%s, %s, 'stint_range', 1, %s, now())
            ON CONFLICT (sport_id, season) DO UPDATE
            SET strict = 1,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            [(SPORT_ID, season, SOURCE) for season in seasons],
        )
        cur.execute(
            """
            UPDATE sport_players
               SET debut_year = LEAST(COALESCE(debut_year, %s), %s),
                   final_year = GREATEST(COALESCE(final_year, %s), %s)
             WHERE sport_id = %s AND player_id = %s
            """,
            (min_season, min_season, max_season, max_season, SPORT_ID, player_id),
        )
        cur.execute(
            """
            INSERT INTO data_provenance (source, season, fetched_at, row_count)
            VALUES (%s, %s, now(), %s)
            ON CONFLICT (source, season) DO UPDATE
            SET fetched_at = EXCLUDED.fetched_at,
                row_count = COALESCE(data_provenance.row_count, 0) + EXCLUDED.row_count
            """,
            (f"{SOURCE}:{external_id}", max_season, len(rows)),
        )
    return len(rows), len(seasons)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--player-id", action="append", default=[], help="Internal player id, e.g. nhl:8479400")
    parser.add_argument("--external-id", action="append", default=[], help="NHL numeric player id, e.g. 8479400")
    parser.add_argument("--all", action="store_true", help="Repair every hockey player with an external id")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.04)
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required")
    if not (args.all or args.player_id or args.external_id):
        raise SystemExit("Use --all, --player-id, or --external-id")

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            clauses = []
            params: list[Any] = [SPORT_ID]
            if not args.all:
                if args.player_id:
                    clauses.append("player_id = ANY(%s)")
                    params.append(args.player_id)
                if args.external_id:
                    clauses.append("external_id = ANY(%s)")
                    params.append(args.external_id)
            where = " AND (" + " OR ".join(clauses) + ")" if clauses else ""
            cur.execute(
                f"""
                SELECT player_id, external_id, display_name
                  FROM sport_players
                 WHERE sport_id = %s
                   AND external_id IS NOT NULL
                   {where}
                 ORDER BY final_year DESC NULLS LAST, display_name
                """,
                params,
            )
            players = cur.fetchall()
    if args.limit:
        players = players[: args.limit]

    repaired = 0
    rows_total = 0
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None) as conn:
        for index, (player_id, external_id, name) in enumerate(players, 1):
            try:
                rows, _seasons = repair_player(conn, player_id, str(external_id))
            except Exception as exc:
                print(f"ERROR {player_id} {name}: {exc}", file=sys.stderr)
                continue
            rows_total += rows
            repaired += int(rows > 0)
            if index % 25 == 0 or rows:
                print(f"{index:,}/{len(players):,} {player_id} {name}: {rows} official rows")
            time.sleep(args.sleep)
    print(f"Repaired {repaired:,}/{len(players):,} NHL players; upserted {rows_total:,} official appearance rows.")


if __name__ == "__main__":
    main()
