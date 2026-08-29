"""Small MLB Stats API client used by local-first compact updaters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests


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
    if int_stat(group_stats, "gamesPlayed") > 0 or int_stat(group_stats, "gamesStarted") > 0:
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
