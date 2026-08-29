"""Small NHL web API client used by local-first compact updaters."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests


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


def fetch_game_rows(game: dict[str, Any]) -> GameRows:
    game_id = str(game["id"])
    box = get_json(f"{API}/gamecenter/{game_id}/boxscore")
    game_date = parse_date(box.get("gameDate") or game.get("gameDate"))
    season = season_start_year(box.get("season") or game.get("season"))
    status = box.get("gameState") or game.get("gameState") or "OFF"
    rows: list[RawAppearance] = []
    team_meta = {"awayTeam": box.get("awayTeam", {}), "homeTeam": box.get("homeTeam", {})}
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
