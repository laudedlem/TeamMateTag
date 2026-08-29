"""Small ESPN NBA client used by local-first compact updaters."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_nba_espn_game_teammates import ESPN_TO_NBA_TEAM, load_crosswalk  # noqa: E402


SPORT_ID = "basketball"
SOURCE = "espn_nba_scoreboard_boxscore"
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
