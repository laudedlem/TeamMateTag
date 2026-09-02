"""
Flask server for Teammate Tag (codename base2nerdle).

Backed by Supabase Postgres. Warm server instances reuse a small psycopg
connection pool; Supabase's transaction pooler still handles upstream pooling.
Game state lives in Postgres as single JSONB blobs
per game (bp_games / dr_games / fr_games), so deploying to a serverless
host (Vercel) works without sticky in-memory state.

Engine code (game/engine.py) uses sqlite-style `?` placeholders; a tiny
PgEngineConn wrapper translates those to `%s` so the engine works against
psycopg without modification.

Run locally: `python web/server.py` (reads DATABASE_URL from .env).
"""
from __future__ import annotations

import json
import os
import random
import re
import secrets
import sqlite3
import sys
import uuid
import hashlib
import hmac
from urllib.parse import quote_plus, urljoin
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from pathlib import Path
from threading import Lock

import psycopg
import requests
from psycopg.types.json import Jsonb
try:
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover - local fallback when pool extra is absent
    ConnectionPool = None

# Load .env first so DATABASE_URL is available before module-level code.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import abort, Flask, jsonify, make_response, redirect, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "game"))
from engine import (  # noqa: E402
    STRIKES_TO_BURN,
    TURN_SECONDS,
    GameState,
    MoveOutcome,
    MoveResult,
    find_player_by_name,
    get_shared_seasons,
    seed_game,
    validate_and_apply_move,
)
from film_review_generator import generate as generate_local_film_review  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
from name_normalize import normalize  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL")

APP_VERSION = "0.5.35"
HEADSHOT_AUDIT_TOKEN = os.environ.get("HEADSHOT_AUDIT_TOKEN", "")
DEFAULT_SEED = "rizzoan01"
LOCAL_SPORTS_ENABLED = os.environ.get("TEAMMATETAG_LOCAL_SPORTS") == "1"
# When running the local curation build we deliberately keep using SQLite so
# data imports can be tested without touching Supabase. Every deployed sport
# page uses the compact Postgres catalog instead.
CROSS_SPORTS_ONLINE = bool(DATABASE_URL) and not LOCAL_SPORTS_ENABLED
# This remains false until the shared matchmaking, challenge, rematch, and
# Playoffs persistence adapters are ported. It prevents a partial deployment
# from exposing the two baseball-only multiplayer endpoints on another sport.
CROSS_SPORTS_FULLY_ONLINE = CROSS_SPORTS_ONLINE
LOCAL_SPORT_DATA = ROOT / "db" / "teammatetag_local.sqlite"
LOCAL_SPORT_SEEDS = {
    "football": "nfl:00-0024272",  # Devin Hester
    "basketball": "nba:201565",     # Derrick Rose
    "hockey": "nhl:8474141",        # Patrick Kane
}
LOCAL_SPORT_MODE_NAMES = {
    "football": "Manager Mode",
    "basketball": "Manager Mode",
    "hockey": "Manager Mode",
}
CURRENT_SPORT_TEAM_NAMES = {
    "basketball": [
        "Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets", "Chicago Bulls",
        "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets", "Detroit Pistons", "Golden State Warriors",
        "Houston Rockets", "Indiana Pacers", "LA Clippers", "Los Angeles Lakers", "Memphis Grizzlies",
        "Miami Heat", "Milwaukee Bucks", "Minnesota Timberwolves", "New Orleans Pelicans", "New York Knicks",
        "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers", "Phoenix Suns", "Portland Trail Blazers",
        "Sacramento Kings", "San Antonio Spurs", "Toronto Raptors", "Utah Jazz", "Washington Wizards",
    ],
    "hockey": [
        "Anaheim Ducks", "Boston Bruins", "Buffalo Sabres", "Calgary Flames", "Carolina Hurricanes",
        "Chicago Blackhawks", "Colorado Avalanche", "Columbus Blue Jackets", "Dallas Stars", "Detroit Red Wings",
        "Edmonton Oilers", "Florida Panthers", "Los Angeles Kings", "Minnesota Wild", "Montreal Canadiens",
        "Nashville Predators", "New Jersey Devils", "New York Islanders", "New York Rangers", "Ottawa Senators",
        "Philadelphia Flyers", "Pittsburgh Penguins", "San Jose Sharks", "Seattle Kraken", "St. Louis Blues",
        "Tampa Bay Lightning", "Toronto Maple Leafs", "Utah Mammoth", "Vancouver Canucks", "Vegas Golden Knights",
        "Washington Capitals", "Winnipeg Jets",
    ],
    "football": [
        "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills", "Carolina Panthers",
        "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns", "Dallas Cowboys", "Denver Broncos",
        "Detroit Lions", "Green Bay Packers", "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars",
        "Kansas City Chiefs", "Las Vegas Raiders", "Los Angeles Chargers", "Los Angeles Rams", "Miami Dolphins",
        "Minnesota Vikings", "New England Patriots", "New Orleans Saints", "New York Giants", "New York Jets",
        "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers", "Seattle Seahawks", "Tampa Bay Buccaneers",
        "Tennessee Titans", "Washington Commanders",
    ],
}
SPORT_TEAM_CANONICAL_NAMES = {
    "basketball": {
        "Baltimore Bullets": "Washington Wizards", "Capital Bullets": "Washington Wizards",
        "Washington Bullets": "Washington Wizards", "Charlotte Bobcats": "Charlotte Hornets",
        "Buffalo Braves": "LA Clippers", "San Diego Clippers": "LA Clippers",
        "Seattle SuperSonics": "Oklahoma City Thunder", "New Jersey Nets": "Brooklyn Nets",
        "New Orleans Hornets": "New Orleans Pelicans", "New Orleans/Oklahoma City Hornets": "New Orleans Pelicans",
        "Cincinnati Royals": "Sacramento Kings", "Kansas City Kings": "Sacramento Kings",
        "Kansas City-Omaha Kings": "Sacramento Kings", "Fort Wayne Pistons": "Detroit Pistons",
        "Ft. Wayne Zollner Pistons": "Detroit Pistons", "Minneapolis Lakers": "Los Angeles Lakers",
        "Philadelphia Warriors": "Golden State Warriors", "San Francisco Warriors": "Golden State Warriors",
        "Syracuse Nationals": "Philadelphia 76ers", "Vancouver Grizzlies": "Memphis Grizzlies",
    },
    "hockey": {
        "AFM": "Calgary Flames", "Atlanta Flames": "Calgary Flames",
        "ARI": "Utah Mammoth", "Arizona Coyotes": "Utah Mammoth", "Phoenix Coyotes": "Utah Mammoth",
        "ATL": "Winnipeg Jets", "Atlanta Thrashers": "Winnipeg Jets",
        "COR": "Colorado Avalanche", "Colorado Rockies": "Colorado Avalanche",
        "QUE": "Colorado Avalanche", "Quebec Nordiques": "Colorado Avalanche",
        "HFD": "Carolina Hurricanes", "Hartford Whalers": "Carolina Hurricanes",
        "MNS": "Dallas Stars", "Minnesota North Stars": "Dallas Stars",
        "CLR": "Dallas Stars", "California Golden Seals": "Dallas Stars",
        "CLE": "Dallas Stars", "Cleveland Barons": "Dallas Stars",
        "WIN": "Winnipeg Jets", "WPG": "Winnipeg Jets",
    },
    "football": {
        "ARZ": "Arizona Cardinals", "CLV": "Cleveland Browns", "HST": "Houston Texans",
        "Boston Patriots": "New England Patriots", "Houston Oilers": "Tennessee Titans",
        "Tennessee Oilers": "Tennessee Titans", "Oakland Raiders": "Las Vegas Raiders",
        "Los Angeles Raiders": "Las Vegas Raiders", "San Diego Chargers": "Los Angeles Chargers",
        "St. Louis Rams": "Los Angeles Rams", "Phoenix Cardinals": "Arizona Cardinals",
        "St. Louis Cardinals": "Arizona Cardinals", "Baltimore Colts": "Indianapolis Colts",
        "Washington Redskins": "Washington Commanders", "Washington Football Team": "Washington Commanders",
    },
}
NHL_TEAM_NAMES = {
    "ANA": "Anaheim Ducks", "ARI": "Arizona Coyotes", "ATL": "Atlanta Thrashers", "BOS": "Boston Bruins",
    "BUF": "Buffalo Sabres", "CAR": "Carolina Hurricanes", "CBJ": "Columbus Blue Jackets", "CGY": "Calgary Flames",
    "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche", "DAL": "Dallas Stars", "DET": "Detroit Red Wings",
    "EDM": "Edmonton Oilers", "FLA": "Florida Panthers", "HFD": "Hartford Whalers", "LAK": "Los Angeles Kings",
    "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens", "NJD": "New Jersey Devils", "NSH": "Nashville Predators",
    "NYI": "New York Islanders", "NYR": "New York Rangers", "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers",
    "PHX": "Phoenix Coyotes", "PIT": "Pittsburgh Penguins", "QUE": "Quebec Nordiques", "SEA": "Seattle Kraken",
    "SJS": "San Jose Sharks", "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs",
    "UTA": "Utah Mammoth", "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights", "WIN": "Winnipeg Jets",
    "WPG": "Winnipeg Jets", "WSH": "Washington Capitals",
}
LOCAL_BP_GAMES: dict[str, dict] = {}
LOCAL_BP_LOCK = Lock()
LOCAL_FR_GAMES: dict[str, dict] = {}
LOCAL_FR_LOCK = Lock()
# Local cross-sport Division Rivalry is intentionally shaped like the
# production /api/dr contract. It is a staging adapter until these leagues
# move to persistent, shared database tables.
LOCAL_DR_GAMES: dict[str, dict] = {}
LOCAL_DR_QUEUE: dict[str, list[dict]] = {sport: [] for sport in LOCAL_SPORT_SEEDS}
LOCAL_DR_MATCH_BY_PLAYER: dict[tuple[str, str], str] = {}
LOCAL_DR_REMATCH_REQUESTS: dict[str, set[str]] = {}
LOCAL_DR_REMATCH_LINKS: dict[str, str] = {}
LOCAL_DR_POSTGAME_EXITS: dict[str, set[str]] = {}
LOCAL_DR_LOCK = Lock()
LOCAL_PO_GAMES: dict[str, dict] = {}
LOCAL_PO_QUEUE: dict[str, list[dict]] = {sport: [] for sport in LOCAL_SPORT_SEEDS}
LOCAL_RANDOM_PLAYOFF_HISTORY: dict[tuple[str, str], list[str]] = {}
LOCAL_PO_MATCH_BY_PLAYER: dict[tuple[str, str], str] = {}
LOCAL_PO_REMATCH_REQUESTS: dict[str, set[str]] = {}
LOCAL_PO_REMATCH_LINKS: dict[str, str] = {}
LOCAL_PO_POSTGAME_EXITS: dict[str, set[str]] = {}
LOCAL_PO_LOCK = Lock()
HEADSHOT_URL = "https://midfield.mlbstatic.com/v1/people/{}/spots/120"
FILE_STORAGE_ROOT = ROOT / "raw" / "file_storage"
FILE_STORAGE_HEADSHOT_BUCKET = "player-headshots"
FILE_STORAGE_HEADSHOT_BASE_URL = (
    os.environ.get("TEAMMATETAG_HEADSHOT_BASE_URL")
    or (f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public" if SUPABASE_URL else "")
).rstrip("/")
FILE_STORAGE_HEADSHOT_MANIFEST_CACHE: dict[str, dict[str, str]] = {}
LOCAL_HEADSHOT_DIRS = {
    "baseball": ROOT / "raw" / "player_headshots" / "baseball",
    "basketball": ROOT / "raw" / "player_headshots" / "basketball",
    "hockey": ROOT / "raw" / "player_headshots" / "hockey",
    "football": ROOT / "raw" / "player_headshots" / "football",
}
BOT_MATCH_MIN_WAIT_SECONDS = 8
BOT_MATCH_JITTER_SECONDS = 3
BOT_WIN_CONDITION_MISS_PERCENT = 8
PLAYOFF_OPENING_LOCK_MOVES = 4
BOT_POSTGAME_REMATCH_MIN_SECONDS = 25
BOT_POSTGAME_REMATCH_JITTER_SECONDS = 11
BOT_NAMES = [
    "Guest b31d9a2c",
    "Guest c84f6e10",
    "Guest d927a4b5",
    "Guest e15c8f30",
    "Guest f06a7d91",
    "Guest a49e2c73",
]


def _official_sport_headshot_url(sport: str, external_id: str | None) -> str | None:
    """Return the league CDN candidate when the catalog has a usable ID."""
    external_id = str(external_id or "").strip()
    if sport == "basketball" and external_id:
        return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{external_id}.png"
    if sport == "hockey" and external_id.isdigit():
        return f"https://assets.nhle.com/mugs/nhl/latest/{external_id}.png"
    return None


def _file_storage_headshot_manifest(sport: str) -> dict[str, str]:
    if sport in FILE_STORAGE_HEADSHOT_MANIFEST_CACHE:
        return FILE_STORAGE_HEADSHOT_MANIFEST_CACHE[sport]
    manifest_path = FILE_STORAGE_ROOT / "manifests" / "headshots" / f"{sport}.json"
    if not manifest_path.exists():
        FILE_STORAGE_HEADSHOT_MANIFEST_CACHE[sport] = {}
        return {}
    with manifest_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = {}
    for row in payload.get("rows") or []:
        player_id = row.get("player_id")
        object_path = row.get("object_path")
        if not player_id or not object_path:
            continue
        if FILE_STORAGE_HEADSHOT_BASE_URL:
            rows[player_id] = (
                f"{FILE_STORAGE_HEADSHOT_BASE_URL}/"
                f"{quote_plus(FILE_STORAGE_HEADSHOT_BUCKET)}/{quote_plus(object_path, safe='/')}"
            )
        elif LOCAL_SPORTS_ENABLED:
            rows[player_id] = f"/file-storage/{FILE_STORAGE_HEADSHOT_BUCKET}/{object_path}"
    FILE_STORAGE_HEADSHOT_MANIFEST_CACHE[sport] = rows
    return rows


def _file_storage_headshot_urls(sport: str, player_ids: list[str]) -> dict[str, str]:
    manifest = _file_storage_headshot_manifest(sport)
    if not manifest:
        return {}
    return {player_id: manifest[player_id] for player_id in player_ids if player_id in manifest}


def _headshot_registry_urls(conn, sport: str, player_ids: list[str]) -> dict[str, str | None]:
    """Return reviewed URLs, or an explicit block for known bad sources."""
    if not player_ids:
        return {}
    urls = dict(_file_storage_headshot_urls(sport, player_ids))
    missing_player_ids = [player_id for player_id in player_ids if player_id not in urls]
    if not missing_player_ids:
        return urls
    rows = conn.execute(
        """SELECT player_id, source_url, fallback_url, status FROM player_headshots
             WHERE sport_id=%s AND player_id=ANY(%s)""",
        (sport, missing_player_ids),
    ).fetchall()
    for player_id, source_url, fallback_url, status in rows:
        if status == "verified" and source_url:
            if source_url.startswith("/local-headshots/") and not LOCAL_SPORTS_ENABLED:
                urls[player_id] = fallback_url or None
                continue
            urls[player_id] = source_url
        elif status in {"placeholder", "missing", "wrong_player", "bad_crop"}:
            urls[player_id] = fallback_url or None
    return urls
OPENING_COUNTDOWN_SECONDS = 3.0
APP_TURN_SECONDS = 20.0
MOVE_GRACE_SECONDS = 1.25
SUPPORT_EMAIL = "support@teammatetag.com"
SESSION_COOKIE = "tt_session"
DEFAULT_PLAYOFF_TURN_SECONDS = 20.0
QUICK_PITCH_TURN_SECONDS = 10.0
FILM_REVIEW_EPOCH = date(2026, 8, 1)
CENTRAL_TIME = ZoneInfo("America/Chicago")

# These are intentionally based only on fields in the local cross-sport
# dataset. Production scoring and award traits can be added without changing
# the local Playoffs API or client contract.
LOCAL_PLAYOFF_CONFIG = {
    "basketball": {
        "powerups": {
            "heat_check": {"label": "Heat Check", "description": "Name a Player from the same franchise with a 2,000+ point season. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "peak_points", "threshold": 2000},
            "sixth_man": {"label": "Sixth Man", "description": "Name a Player from the same franchise with 7,000+ career assists. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "career_assists", "threshold": 7000},
            "switch": {"label": "Switch", "description": "Name a Player from the same franchise who played the same position. +5 seconds.", "kind": "same_position", "bonus_seconds": 5},
            "mvp_badge": {"label": "MVP Badge", "description": "Name a Player from the same franchise who won an MVP Award. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "mvp_count", "threshold": 1},
            "all_star_callup": {"label": "Star Power", "description": "Name an All-Star from the same franchise. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "all_star_count", "threshold": 1},
            "timeout": {"label": "Timeout", "description": "+15 seconds.", "kind": "time", "bonus_seconds": 15},
            "full_court_press": {"label": "Full-Court Press", "description": "Your opponent only has 10 seconds on their next turn.", "kind": "pressure"},
        },
        "conditions": {
            "bucket_getter": {"label": "Bucket Getter", "description": "Name 2 players with 25,000 career points", "target": 2, "kind": "trait", "trait": "career_points", "threshold": 25000},
            "season_scorer": {"label": "Scoring Run", "description": "Name 2 players with a 2,000-point season", "target": 2, "kind": "trait", "trait": "peak_points", "threshold": 2000},
            "playmaker": {"label": "Table Setter", "description": "Name 2 players with 7,000 career assists", "target": 2, "kind": "trait", "trait": "career_assists", "threshold": 7000},
            "three_point_club": {"label": "Deep Range", "description": "Name 2 players with 2,000 career three-pointers", "target": 2, "kind": "trait", "trait": "career_goals", "threshold": 2000},
            "ironhorse": {"label": "Ironhorse", "description": "Name 2 players with 1,000 career games", "target": 2, "kind": "career_games", "threshold": 1000},
            "one_team": {"label": "Home Court", "description": "Name 2 players with 8 seasons for one franchise", "target": 2, "kind": "one_franchise", "threshold": 8},
            "journeyman": {"label": "Frequent Flyer", "description": "Name 2 players who played for 5 teams", "target": 2, "kind": "team_count", "threshold": 5},
            "mvp_circle": {"label": "MVP Circle", "description": "Name 2 MVP winners", "target": 2, "kind": "trait", "trait": "mvp_count", "threshold": 1},
            "all_star_marathon": {"label": "All-Star Marathon", "description": "Name players with 12 combined All-Star selections", "target": 12, "kind": "sum_trait", "trait": "all_star_count"},
            "ring_chaser": {"label": "Ring Chaser", "description": "Name players with 6 combined championships", "target": 6, "kind": "sum_trait", "trait": "championship_count"},
            "young_guns": {"label": "Young Guns", "description": "Name 2 Rookie of the Year winners", "target": 2, "kind": "trait", "trait": "roty_count", "threshold": 1},
        },
    },
    "football": {
        "powerups": {
            "trick_play": {"label": "Trick Play", "description": "Name a Player from the same franchise with a 20+ touchdown season (non-passing). +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "peak_touchdowns", "threshold": 20},
            "iron_man": {"label": "Iron Man", "description": "Name a Player from the same franchise with 100 career games played. +5 seconds.", "kind": "veteran", "bonus_seconds": 5, "career_games": 100},
            "package_change": {"label": "Package Change", "description": "Name a Player from the same franchise who played the same position. +5 seconds.", "kind": "same_position", "bonus_seconds": 5},
            "mvp_badge": {"label": "MVP Badge", "description": "Name a Player from the same franchise who won an MVP Award. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "mvp_count", "threshold": 1},
            "pro_bowl_callup": {"label": "Bowler", "description": "Name a Pro Bowler from the same franchise. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "all_star_count", "threshold": 1},
            "timeout": {"label": "Timeout", "description": "+15 seconds.", "kind": "time", "bonus_seconds": 15},
            "blitz": {"label": "Blitz", "description": "Your opponent only has 10 seconds on their next turn.", "kind": "pressure"},
        },
        "conditions": {
            "touchdown_club": {"label": "End Zone", "description": "Name 2 players with 100 career touchdowns", "target": 2, "kind": "trait", "trait": "career_touchdowns", "threshold": 100},
            "season_scorer": {"label": "Season Scorer", "description": "Name 2 players with a 15-touchdown season", "target": 2, "kind": "trait", "trait": "peak_touchdowns", "threshold": 15},
            "air_raid": {"label": "Air Raid", "description": "Name 2 players with 300 career passing touchdowns", "target": 2, "kind": "trait", "trait": "passing_touchdowns", "threshold": 300},
            "single_season_passer": {"label": "Sunday Slingers", "description": "Name 2 players with a 35-passing-touchdown season", "target": 2, "kind": "trait", "trait": "peak_passing_touchdowns", "threshold": 35},
            "sack_master": {"label": "Sack Master", "description": "Name 2 players with 100 career sacks", "target": 2, "kind": "trait", "trait": "career_sacks", "threshold": 100},
            "ballhawk": {"label": "Ballhawk", "description": "Name 2 players with 30 career interceptions", "target": 2, "kind": "trait", "trait": "career_interceptions", "threshold": 30},
            "one_team": {"label": "One Club", "description": "Name 2 players with 10 seasons for one franchise", "target": 2, "kind": "one_franchise", "threshold": 10},
            "journeyman": {"label": "Journeyman", "description": "Name 2 players who played for 5 teams", "target": 2, "kind": "team_count", "threshold": 5},
            "mvp_circle": {"label": "MVP Circle", "description": "Name 2 MVP winners", "target": 2, "kind": "trait", "trait": "mvp_count", "threshold": 1},
            "pro_bowl_marathon": {"label": "Pro Bowl Marathon", "description": "Name players with 12 combined Pro Bowl selections", "target": 12, "kind": "sum_trait", "trait": "all_star_count"},
            "ring_chaser": {"label": "Ring Chaser", "description": "Name players with 5 combined championships", "target": 5, "kind": "sum_trait", "trait": "championship_count"},
            "young_guns": {"label": "Fresh Faces", "description": "Name 2 Rookie of the Year winners", "target": 2, "kind": "trait", "trait": "roty_count", "threshold": 1},
        },
    },
    "hockey": {
        "powerups": {
            "breakaway": {"label": "Breakaway", "description": "Name a Player from the same franchise with a 400+ goal career. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "career_goals", "threshold": 400},
            "veteran_presence": {"label": "Veteran Presence", "description": "Name a Player from the same franchise with 800+ career points. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "career_points", "threshold": 800},
            "line_change": {"label": "Line Change", "description": "Name a Player from the same franchise who played the same position. +5 seconds.", "kind": "same_position", "bonus_seconds": 5},
            "hart_honor": {"label": "Hart Honor", "description": "Name a Hart Trophy winner from the same franchise. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "mvp_count", "threshold": 1},
            "all_star_callup": {"label": "All-Star", "description": "Name an All-Star from the same franchise. +5 seconds.", "kind": "stat", "bonus_seconds": 5, "stat": "all_star_count", "threshold": 1},
            "timeout": {"label": "Timeout", "description": "+15 seconds.", "kind": "time", "bonus_seconds": 15},
            "forecheck": {"label": "Forecheck", "description": "Your opponent only has 10 seconds on their next turn.", "kind": "pressure"},
        },
        "conditions": {
            "sniper": {"label": "Sniper", "description": "Name 2 players with 500 career goals", "target": 2, "kind": "trait", "trait": "career_goals", "threshold": 500},
            "single_season_sniper": {"label": "Rocket Season", "description": "Name 1 player with a 60-goal season", "target": 1, "kind": "trait", "trait": "peak_goals", "threshold": 60},
            "playmaker": {"label": "Playmaker", "description": "Name 2 players with 1,000 career assists", "target": 2, "kind": "trait", "trait": "career_assists", "threshold": 1000},
            "point_streak": {"label": "Point Machine", "description": "Name 1 player with a 120-point season", "target": 1, "kind": "trait", "trait": "peak_points", "threshold": 120},
            "one_team": {"label": "Lifer", "description": "Name 2 players with 10 seasons for one franchise", "target": 2, "kind": "one_franchise", "threshold": 10},
            "journeyman": {"label": "Journeyman", "description": "Name 2 players who played for 5 teams", "target": 2, "kind": "team_count", "threshold": 5},
            "mvp_circle": {"label": "Hart Club", "description": "Name 2 Hart Trophy winners", "target": 2, "kind": "trait", "trait": "mvp_count", "threshold": 1},
            "all_star_marathon": {"label": "All-Star Marathon", "description": "Name players with 12 combined All-Star selections", "target": 12, "kind": "sum_trait", "trait": "all_star_count"},
            "ironhorse": {"label": "Ironhorse", "description": "Name 2 players with 1,200 career games", "target": 2, "kind": "trait", "trait": "career_games", "threshold": 1200},
            "ring_chaser": {"label": "Cup Chasers", "description": "Name players with 7 combined Stanley Cup credits", "target": 7, "kind": "sum_trait", "trait": "championship_count"},
            "young_guns": {"label": "Fresh Ice", "description": "Name 2 Calder Trophy winners", "target": 2, "kind": "trait", "trait": "roty_count", "threshold": 1},
        },
    },
}

PLAYOFF_POWERUPS = {
    "bubblegum": {
        "label": "Bubblegum",
        "description": "Name a Player from the same franchise with a 40+ home run season. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "batter",
    },
    "pine_tar": {
        "label": "Pine Tar",
        "description": "Name a Player from the same franchise with a 200+ strikeout season. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "pitcher",
    },
    "bat_donut": {
        "label": "Bat Donut",
        "description": "Name a Silver Slugger from the same franchise. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "any",
    },
    "sunglasses": {
        "label": "Sunglasses",
        "description": "Name an All-Star from the same franchise. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "any",
    },
    "backup_mitt": {
        "label": "Backup Mitt",
        "description": "Name a Gold-Glover from the same franchise. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "any",
    },
    "abs": {
        "label": "ABS",
        "description": "+15 seconds.",
        "kind": "timer",
        "bonus_seconds": 15.0,
        "role": "any",
    },
    "quick_pitch": {
        "label": "Quick Pitch",
        "description": "Your opponent only has 10 seconds on their next turn.",
        "kind": "timer",
        "bonus_seconds": 0.0,
        "role": "any",
    },
}

PLAYOFF_WIN_CONDITIONS = {
    "sunset_kingdom": {
        "label": "Sunset Kingdom",
        "description": "Name 3 Japanese players.",
        "target": 3,
        "mode": "count",
    },
    "havana_heat": {
        "label": "Havana Heat",
        "description": "Name 3 Cuban players.",
        "target": 3,
        "mode": "count",
    },
    "maple_corridor": {
        "label": "Maple Corridor",
        "description": "Name 4 Canadian players.",
        "target": 4,
        "mode": "count",
    },
    "mvp_circle": {
        "label": "MVP Circle",
        "description": "Name 2 MVP winners.",
        "target": 2,
        "mode": "count",
    },
    "young_buck": {
        "label": "Young Buck",
        "description": "Name 2 Rookie of the Year winners.",
        "target": 2,
        "mode": "count",
    },
    "gonna_be_golden": {
        "label": "Gonna Be Golden",
        "description": "Name 2 Gold Glove winners.",
        "target": 2,
        "mode": "count",
    },
    "secretariat": {
        "label": "Secretariat",
        "description": "Name 1 Triple Crown winner.",
        "target": 1,
        "mode": "count",
    },
    "hound_dog": {
        "label": "Hound-dog",
        "description": "Name 2 players who spent at least 10 seasons with one franchise only.",
        "target": 2,
        "mode": "count",
    },
    "great_bambinos": {
        "label": "Great Bambinos",
        "description": "Name 1 player with 500 career home runs.",
        "target": 1,
        "mode": "count",
    },
    "ring_chaser": {
        "label": "Ring Chaser",
        "description": "Name players with a combined 15 World Series rings.",
        "target": 15,
        "mode": "sum",
    },
    "journeyman": {
        "label": "Journeyman",
        "description": "Name 2 players who played for at least 7 teams.",
        "target": 2,
        "mode": "count",
    },
}

# Explicit folders so Flask works regardless of CWD on serverless hosts.
app = Flask(
    __name__,
    template_folder=str(ROOT / "web" / "templates"),
    static_folder=str(ROOT / "web" / "static"),
    static_url_path="/static",
)


@app.errorhandler(Exception)
def api_exception_response(error):
    if request.path.startswith("/api/"):
        status_code = error.code if isinstance(error, HTTPException) else 500
        app.logger.exception("Unhandled API error on %s", request.path)
        return jsonify({"error": f"server error: {error.__class__.__name__}"}), status_code
    if isinstance(error, HTTPException):
        return error
    raise error


# ============================================================
# Database access
# ============================================================

@contextmanager
def db():
    """Open a Postgres connection for a single request.

    Warm server instances reuse a tiny local psycopg pool to avoid paying
    connection setup on every request. Supabase's transaction-mode pgbouncer
    still manages upstream pooling.

    Supabase's underlying Postgres sets `default_transaction_read_only=on`
    at the config-file level (visible in pg_settings). We override at the
    session level immediately after connecting so this server can write."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required. Copy .env.example to .env and set the "
            "Supabase connection URI, or export it in the environment."
        )
    pooled = _pooled_connection()
    if pooled is not None:
        with pooled as conn:
            conn.execute("SET default_transaction_read_only = off")
            yield conn
        return
    conn = psycopg.connect(DATABASE_URL, **_connection_kwargs())
    try:
        conn.execute("SET default_transaction_read_only = off")
        yield conn
    finally:
        conn.close()


class PgEngineConn:
    """Wrap a psycopg.Connection to expose the .execute(sql, params) ->
    cursor interface engine.py expects (the engine was written for
    sqlite3 and uses `?` placeholders). Cheap; not thread-safe."""
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()):
        return self._conn.execute(sql.replace("?", "%s"), params)

    def cursor(self):
        return self._conn.cursor()


# ============================================================
# Static caches populated once at startup
# ============================================================

TEAM_NAME: dict[tuple[str, int], str] = {}
TEAM_FRANCHISE: dict[tuple[str, int], str] = {}
ALL_FR_TEAM_NAMES: list[str] = []
PLAYER_CARD_CACHE: dict[str, dict] = {}
PLAYER_CARD_LOCK = Lock()
SPORT_CARD_CACHE: dict[tuple[str, str], dict] = {}
SPORT_TEAM_NAME_CACHE: dict[tuple[str, str, int], str] = {}
MANAGER_SEED_CACHE: dict[tuple[str, str], str] = {}
STATIC_CACHE_LOCK = Lock()
STATIC_CACHE_READY = False
RUNTIME_SCHEMA_LOCK = Lock()
# Runtime tables are established by the deployment/bootstrap migration. Running
# every historical `ALTER TABLE ... IF NOT EXISTS` on a serverless cold start
# adds several seconds before the first game can load. Set
# TEAMMATETAG_AUTO_MIGRATE=1 only while intentionally applying a new migration.
RUNTIME_SCHEMA_READY = os.environ.get("TEAMMATETAG_AUTO_MIGRATE", "").strip().lower() not in {
    "1", "true", "yes",
}
DB_POOL = None
DB_POOL_LOCK = Lock()


def _connection_kwargs() -> dict:
    # prepare_threshold=None disables psycopg3's auto-prepared-statement
    # cache. pgbouncer in transaction mode doesn't preserve session state
    # across transactions, so cached prepared-statement names collide
    # ("prepared statement _pg3_0 already exists").
    return {"autocommit": True, "prepare_threshold": None}


def _pooled_connection():
    global DB_POOL
    if ConnectionPool is None:
        return None
    if DB_POOL is None:
        with DB_POOL_LOCK:
            if DB_POOL is None:
                DB_POOL = ConnectionPool(
                    DATABASE_URL,
                    min_size=0,
                    max_size=int(os.environ.get("TEAMMATETAG_DB_POOL_MAX", "4")),
                    timeout=float(os.environ.get("TEAMMATETAG_DB_POOL_TIMEOUT", "5")),
                    kwargs=_connection_kwargs(),
                    open=True,
                )
    return DB_POOL.connection()


FR_CANONICAL_FRANCHISE_NAMES = {
    "ANA": "Los Angeles Angels",
    "FLA": "Miami Marlins",
    "TBD": "Tampa Bay Rays",
    "WSN": "Expos/Nationals",
    "CLE": "Cleveland Guardians",
}


def fr_display_team_name(team_id: str, season: int) -> str:
    ensure_static_caches()
    franchise_id = TEAM_FRANCHISE.get((team_id, season))
    if franchise_id in FR_CANONICAL_FRANCHISE_NAMES:
        return FR_CANONICAL_FRANCHISE_NAMES[franchise_id]
    return TEAM_NAME.get((team_id, season), team_id)


def fr_team_aliases(team_id: str, season: int) -> list[str]:
    ensure_static_caches()
    raw_name = (TEAM_NAME.get((team_id, season), team_id) or "").lower()
    franchise_id = TEAM_FRANCHISE.get((team_id, season))
    if franchise_id == "ANA":
        return ["angels", "anaheim angels", "los angeles angels",
                "los angeles angels of anaheim", "california angels"]
    if franchise_id == "FLA":
        return ["marlins", "florida marlins", "miami marlins"]
    if franchise_id == "TBD":
        return ["rays", "tampa bay rays", "tampa bay devil rays", "devil rays"]
    if franchise_id == "WSN":
        return ["expos/nationals", "expos", "nationals",
                "montreal expos", "washington nationals"]
    if franchise_id == "CLE":
        return ["guardians", "indians", "cleveland guardians", "cleveland indians"]
    return [raw_name]


def ensure_static_caches():
    """Load team-name lookups once per process. ~810 rows."""
    global TEAM_NAME, TEAM_FRANCHISE, ALL_FR_TEAM_NAMES, STATIC_CACHE_READY
    if STATIC_CACHE_READY:
        return
    with STATIC_CACHE_LOCK:
        if STATIC_CACHE_READY:
            return
        with db() as conn:
            rows = conn.execute(
                "SELECT team_id, season, franchise_id, name FROM teams"
            ).fetchall()
        TEAM_NAME = {(t, s): n for t, s, _, n in rows}
        TEAM_FRANCHISE = {(t, s): f for t, s, f, _ in rows}
        ALL_FR_TEAM_NAMES = sorted({
            fr_display_team_name_noinit(t, s) for t, s in TEAM_NAME
        })
        STATIC_CACHE_READY = True


def fr_display_team_name_noinit(team_id: str, season: int) -> str:
    """Internal helper for cache-building to avoid recursive init."""
    franchise_id = TEAM_FRANCHISE.get((team_id, season))
    if franchise_id in FR_CANONICAL_FRANCHISE_NAMES:
        return FR_CANONICAL_FRANCHISE_NAMES[franchise_id]
    return TEAM_NAME.get((team_id, season), team_id)


def ensure_runtime_schema():
    global RUNTIME_SCHEMA_READY
    if RUNTIME_SCHEMA_READY:
        return
    with RUNTIME_SCHEMA_LOCK:
        if RUNTIME_SCHEMA_READY:
            return
        with db() as conn:
            conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT"
            )
            conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT"
            )
            conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_user_id UUID"
            )
            conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT"
            )
            conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_salt TEXT"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique "
                "ON users ((lower(username))) WHERE username IS NOT NULL"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique "
                "ON users ((lower(email))) WHERE email IS NOT NULL"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_auth_user_unique "
                "ON users(auth_user_id) WHERE auth_user_id IS NOT NULL"
            )
            conn.execute(
                "ALTER TABLE guests "
                "ADD COLUMN IF NOT EXISTS elo INTEGER NOT NULL DEFAULT 1200"
            )
            conn.execute(
                "ALTER TABLE guests ADD COLUMN IF NOT EXISTS "
                "playoff_win_condition_preference TEXT NOT NULL DEFAULT 'random'"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS fr_results (
                       result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                       owner_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
                       owner_guest_id UUID REFERENCES guests(guest_id) ON DELETE SET NULL,
                       puzzle_id TEXT NOT NULL,
                       hits INTEGER NOT NULL,
                       fouls INTEGER NOT NULL,
                       strikes INTEGER NOT NULL,
                       won BOOLEAN NOT NULL DEFAULT false,
                       unit TEXT,
                       finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fr_results_owner_guest "
                "ON fr_results(owner_guest_id, finished_at DESC)"
            )
            conn.execute("ALTER TABLE fr_results ADD COLUMN IF NOT EXISTS sport_id TEXT NOT NULL DEFAULT 'baseball'")
            conn.execute("ALTER TABLE fr_results ADD COLUMN IF NOT EXISTS unit TEXT")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS film_review_daily_attempts (
                       owner_guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       sport_id TEXT NOT NULL,
                       puzzle_date DATE NOT NULL,
                       unit TEXT NOT NULL DEFAULT '',
                       game_id UUID NOT NULL,
                       status TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress','won','lost')),
                       official BOOLEAN NOT NULL DEFAULT true,
                       completed_at TIMESTAMPTZ,
                       PRIMARY KEY (owner_guest_id, sport_id, puzzle_date, unit)
                   )"""
            )
            conn.execute("ALTER TABLE film_review_daily_attempts ADD COLUMN IF NOT EXISTS unit TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE film_review_daily_attempts ADD COLUMN IF NOT EXISTS official BOOLEAN NOT NULL DEFAULT true")
            conn.execute(
                """DO $$
                   BEGIN
                     IF NOT EXISTS (
                         SELECT 1
                           FROM pg_constraint
                          WHERE conrelid = 'film_review_daily_attempts'::regclass
                            AND contype = 'p'
                     ) THEN
                       ALTER TABLE film_review_daily_attempts
                         ADD CONSTRAINT film_review_daily_attempts_pkey
                         PRIMARY KEY (owner_guest_id, sport_id, puzzle_date, unit);
                     END IF;
                   END $$"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dr_results (
                       result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                       game_id UUID,
                       owner_guest_id UUID REFERENCES guests(guest_id) ON DELETE SET NULL,
                       opponent_guest_id UUID REFERENCES guests(guest_id) ON DELETE SET NULL,
                       opponent_name TEXT,
                       chain_length INTEGER,
                       won BOOLEAN NOT NULL DEFAULT false,
                       elo_before INTEGER NOT NULL,
                       elo_after INTEGER NOT NULL,
                       finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "ALTER TABLE dr_results ADD COLUMN IF NOT EXISTS game_id UUID"
            )
            conn.execute(
                "ALTER TABLE dr_results ADD COLUMN IF NOT EXISTS chain_length INTEGER"
            )
            conn.execute(
                "ALTER TABLE dr_results "
                "ADD COLUMN IF NOT EXISTS opponent_guest_id UUID REFERENCES guests(guest_id) ON DELETE SET NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dr_results_owner_guest "
                "ON dr_results(owner_guest_id, finished_at DESC)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_dr_results_owner_game_unique "
                "ON dr_results(owner_guest_id, game_id) WHERE game_id IS NOT NULL"
            )
            conn.execute("ALTER TABLE dr_results ADD COLUMN IF NOT EXISTS sport_id TEXT NOT NULL DEFAULT 'baseball'")
            conn.execute("ALTER TABLE bp_runs ADD COLUMN IF NOT EXISTS sport_id TEXT NOT NULL DEFAULT 'baseball'")
            conn.execute("ALTER TABLE bp_runs DROP CONSTRAINT IF EXISTS bp_runs_seed_player_id_fkey")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS manager_daily_starters (
                       sport_id TEXT NOT NULL,
                       starter_date DATE NOT NULL,
                       player_id TEXT NOT NULL,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (sport_id, starter_date)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dr_queue (
                       guest_id UUID PRIMARY KEY REFERENCES guests(guest_id) ON DELETE CASCADE,
                       display_name TEXT NOT NULL,
                       enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "ALTER TABLE dr_queue ADD COLUMN IF NOT EXISTS avoid_guest_id UUID"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dr_queue_enqueued "
                "ON dr_queue(enqueued_at)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dr_invites (
                       code TEXT PRIMARY KEY,
                       host_guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       host_name TEXT NOT NULL,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 minutes'),
                       claimed_at TIMESTAMPTZ
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dr_invites_host "
                "ON dr_invites(host_guest_id, created_at DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS player_usage (
                       player_id TEXT PRIMARY KEY REFERENCES players(player_id),
                       total_count INTEGER NOT NULL DEFAULT 0,
                       bp_count INTEGER NOT NULL DEFAULT 0,
                       dr_count INTEGER NOT NULL DEFAULT 0,
                       last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS guest_team_strikeouts (
                       event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                       owner_guest_id UUID REFERENCES guests(guest_id) ON DELETE CASCADE,
                       mode TEXT NOT NULL,
                       team_name TEXT NOT NULL,
                       team_id TEXT,
                       season INTEGER,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_guest_team_strikeouts_owner "
                "ON guest_team_strikeouts(owner_guest_id, created_at DESC)"
            )
            conn.execute("ALTER TABLE guest_team_strikeouts ADD COLUMN IF NOT EXISTS sport_id TEXT NOT NULL DEFAULT 'baseball'")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS guest_sport_ratings (
                       guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       sport_id TEXT NOT NULL,
                       elo INTEGER NOT NULL DEFAULT 1200,
                       PRIMARY KEY (guest_id, sport_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS film_review_daily_puzzles (
                       sport_id TEXT NOT NULL,
                       puzzle_date DATE NOT NULL,
                       unit TEXT NOT NULL DEFAULT '',
                       puzzle JSONB NOT NULL,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (sport_id, puzzle_date, unit)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS baseball_player_positions (
                       player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
                       position TEXT NOT NULL,
                       games INTEGER NOT NULL DEFAULT 0,
                       PRIMARY KEY (player_id, position)
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_baseball_fr_positions "
                "ON baseball_player_positions(position, player_id)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_player_usage (
                       sport_id TEXT NOT NULL,
                       player_id TEXT NOT NULL,
                       total_count INTEGER NOT NULL DEFAULT 0,
                       bp_count INTEGER NOT NULL DEFAULT 0,
                       dr_count INTEGER NOT NULL DEFAULT 0,
                       last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (sport_id, player_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_online_games (
                       game_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                       sport_id TEXT NOT NULL REFERENCES sports(sport_id),
                       mode TEXT NOT NULL CHECK (mode IN ('dr', 'po')),
                       state JSONB NOT NULL,
                       finished BOOLEAN NOT NULL DEFAULT false,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sport_online_games_active ON sport_online_games(sport_id, mode, created_at DESC) WHERE NOT finished")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_online_queue (
                       sport_id TEXT NOT NULL REFERENCES sports(sport_id),
                       mode TEXT NOT NULL CHECK (mode IN ('dr', 'po')),
                       guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       display_name TEXT NOT NULL,
                       preference TEXT,
                       avoid_guest_id UUID,
                       enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (sport_id, mode, guest_id)
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sport_online_queue ON sport_online_queue(sport_id, mode, enqueued_at)")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS guest_random_playoff_conditions (
                       event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                       guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       sport_id TEXT NOT NULL REFERENCES sports(sport_id),
                       condition_key TEXT NOT NULL,
                       assigned_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_guest_random_playoff_conditions_recent "
                "ON guest_random_playoff_conditions(guest_id, sport_id, assigned_at DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS player_headshots (
                       sport_id TEXT NOT NULL,
                       player_id TEXT NOT NULL,
                       source_url TEXT,
                       fallback_url TEXT,
                       provider TEXT,
                       status TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'verified', 'placeholder', 'missing',
                                             'duplicate', 'wrong_player', 'bad_crop', 'needs_review')),
                       content_sha256 TEXT,
                       perceptual_hash TEXT,
                       width INTEGER,
                       height INTEGER,
                       checked_at TIMESTAMPTZ,
                       reviewed_at TIMESTAMPTZ,
                       review_note TEXT,
                       PRIMARY KEY (sport_id, player_id)
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_player_headshots_review "
                "ON player_headshots(status, sport_id, checked_at DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_teammate_exclusions (
                       sport_id TEXT NOT NULL,
                       player_a_id TEXT NOT NULL,
                       player_b_id TEXT NOT NULL,
                       team_id TEXT NOT NULL,
                       season INTEGER NOT NULL,
                       reason TEXT,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS player_stints (
                       player_id TEXT NOT NULL REFERENCES players(player_id),
                       team_id TEXT NOT NULL,
                       season INTEGER NOT NULL,
                       first_unit INTEGER NOT NULL,
                       last_unit INTEGER NOT NULL,
                       first_label TEXT,
                       last_label TEXT,
                       source TEXT,
                       PRIMARY KEY (player_id, team_id, season)
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_player_stints_link "
                "ON player_stints(team_id, season, player_id)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS teammate_stint_coverage (
                       season INTEGER PRIMARY KEY,
                       coverage_type TEXT NOT NULL,
                       strict INTEGER NOT NULL DEFAULT 1,
                       source TEXT,
                       updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS teammate_exclusions (
                       player_a_id TEXT NOT NULL REFERENCES players(player_id),
                       player_b_id TEXT NOT NULL REFERENCES players(player_id),
                       team_id TEXT NOT NULL,
                       season INTEGER NOT NULL,
                       reason TEXT,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (player_a_id, player_b_id, team_id, season)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS mlb_teammate_game_proofs (
                       player_a_id TEXT NOT NULL REFERENCES players(player_id),
                       player_b_id TEXT NOT NULL REFERENCES players(player_id),
                       team_id TEXT NOT NULL,
                       season INTEGER NOT NULL,
                       shared_games INTEGER NOT NULL,
                       first_game_pk INTEGER NOT NULL,
                       first_game_date DATE NOT NULL,
                       source TEXT,
                       PRIMARY KEY (player_a_id, player_b_id, team_id, season),
                       CHECK (player_a_id < player_b_id),
                       FOREIGN KEY (team_id, season) REFERENCES teams(team_id, season)
                   )"""
            )
            mlb_proof_kind = conn.execute(
                "SELECT relkind FROM pg_class WHERE oid = to_regclass('mlb_teammate_game_proofs')"
            ).fetchone()
            if mlb_proof_kind and mlb_proof_kind[0] == "r":
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mlb_tgp_pair "
                    "ON mlb_teammate_game_proofs(player_a_id, player_b_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mlb_tgp_b_a "
                    "ON mlb_teammate_game_proofs(player_b_id, player_a_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mlb_tgp_team_season "
                    "ON mlb_teammate_game_proofs(team_id, season)"
                )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_player_stints (
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
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sport_stints_link "
                "ON sport_player_stints(sport_id, team_id, season, player_id)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_teammate_stint_coverage (
                       sport_id TEXT NOT NULL,
                       season INTEGER NOT NULL,
                       coverage_type TEXT NOT NULL,
                       strict INTEGER NOT NULL DEFAULT 1,
                       source TEXT,
                       updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (sport_id, season)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_teammates (
                       sport_id TEXT NOT NULL,
                       player_a_id TEXT NOT NULL,
                       player_b_id TEXT NOT NULL,
                       team_id TEXT NOT NULL,
                       season INTEGER NOT NULL,
                       PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season),
                       CHECK (player_a_id < player_b_id)
                   )"""
            )
            sport_proof_kind = conn.execute(
                "SELECT relkind FROM pg_class WHERE oid = to_regclass('sport_teammates')"
            ).fetchone()
            if sport_proof_kind and sport_proof_kind[0] == "r":
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sport_teammates_pair "
                    "ON sport_teammates(sport_id, player_a_id, player_b_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sport_teammates_a "
                    "ON sport_teammates(sport_id, player_a_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sport_teammates_b "
                    "ON sport_teammates(sport_id, player_b_id)"
                )
            conn.execute(
                """INSERT INTO sport_teammate_exclusions
                       (sport_id, player_a_id, player_b_id, team_id, season, reason)
                   VALUES ('basketball', 'nba:202954', 'nba:201952', '1610612738', 2020,
                           'Brad Wanamaker left Boston in the 2020 offseason before Jeff Teague joined for 2020-21.')
                   ON CONFLICT DO NOTHING"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_online_rematches (
                       original_game_id UUID NOT NULL REFERENCES sport_online_games(game_id) ON DELETE CASCADE,
                       requester_guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       PRIMARY KEY (original_game_id, requester_guest_id)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_online_rematch_links (
                       original_game_id UUID PRIMARY KEY REFERENCES sport_online_games(game_id) ON DELETE CASCADE,
                       new_game_id UUID NOT NULL REFERENCES sport_online_games(game_id) ON DELETE CASCADE
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_online_postgame_exits (
                       original_game_id UUID NOT NULL REFERENCES sport_online_games(game_id) ON DELETE CASCADE,
                       guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       PRIMARY KEY (original_game_id, guest_id)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sport_online_invites (
                       code TEXT PRIMARY KEY,
                       sport_id TEXT NOT NULL REFERENCES sports(sport_id),
                       mode TEXT NOT NULL CHECK (mode IN ('dr', 'po')),
                       host_guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       host_name TEXT NOT NULL,
                       preference TEXT,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 minutes'),
                       claimed_at TIMESTAMPTZ
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sport_online_invites_host "
                "ON sport_online_invites(sport_id, mode, host_guest_id, created_at DESC)"
            )
            conn.execute(
                """INSERT INTO sports (sport_id, display_name, league_name, active)
                   VALUES ('baseball', 'Baseball', 'MLB', true)
                   ON CONFLICT (sport_id) DO UPDATE
                   SET display_name = EXCLUDED.display_name,
                       league_name = EXCLUDED.league_name,
                       active = true"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS multi_sport_queue (
                       guest_id UUID PRIMARY KEY REFERENCES guests(guest_id) ON DELETE CASCADE,
                       mode TEXT NOT NULL CHECK (mode IN ('dr', 'po')),
                       sports JSONB NOT NULL,
                       display_name TEXT NOT NULL,
                       preference JSONB NOT NULL DEFAULT '{}'::jsonb,
                       avoid_guest_id UUID,
                       enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_multi_sport_queue_mode "
                "ON multi_sport_queue(mode, enqueued_at)"
            )
            conn.execute(
                """INSERT INTO guest_sport_ratings (guest_id, sport_id, elo)
                   SELECT guest_id, 'baseball', elo FROM guests
                   ON CONFLICT (guest_id, sport_id) DO NOTHING"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dr_rematches (
                       original_game_id UUID NOT NULL,
                       requester_guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (original_game_id, requester_guest_id)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dr_rematch_links (
                       original_game_id UUID PRIMARY KEY,
                       new_game_id UUID NOT NULL,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dr_postgame_exits (
                       original_game_id UUID NOT NULL,
                       guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (original_game_id, guest_id)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS friend_requests (
                       request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                       sender_user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                       recipient_user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                       status TEXT NOT NULL DEFAULT 'pending',
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       responded_at TIMESTAMPTZ,
                       CHECK (sender_user_id <> recipient_user_id)
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_friend_requests_recipient "
                "ON friend_requests(recipient_user_id, status, created_at DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS friendships (
                       user_a_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                       user_b_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (user_a_id, user_b_id),
                       CHECK (user_a_id < user_b_id)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dr_friend_challenges (
                       challenge_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                       sender_user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                       recipient_user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                       sender_name TEXT NOT NULL,
                       recipient_name TEXT NOT NULL,
                       status TEXT NOT NULL DEFAULT 'pending',
                       game_id UUID,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       responded_at TIMESTAMPTZ,
                       CHECK (sender_user_id <> recipient_user_id)
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_friend_challenges_recipient "
                "ON dr_friend_challenges(recipient_user_id, status, created_at DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS player_powerup_qualifications (
                       player_id TEXT NOT NULL REFERENCES players(player_id),
                       powerup_key TEXT NOT NULL,
                       franchise_id TEXT NOT NULL REFERENCES franchises(franchise_id),
                       team_id TEXT NOT NULL,
                       season INTEGER NOT NULL,
                       PRIMARY KEY (player_id, powerup_key, team_id, season)
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ppq_lookup "
                "ON player_powerup_qualifications(powerup_key, franchise_id, player_id)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS player_playoff_traits (
                       player_id TEXT PRIMARY KEY REFERENCES players(player_id),
                       birth_country TEXT,
                       is_japanese BOOLEAN NOT NULL DEFAULT false,
                       is_cuban BOOLEAN NOT NULL DEFAULT false,
                       is_canadian BOOLEAN NOT NULL DEFAULT false,
                       mvp_count INTEGER NOT NULL DEFAULT 0,
                       roty_count INTEGER NOT NULL DEFAULT 0,
                       gold_glove_count INTEGER NOT NULL DEFAULT 0,
                       triple_crown_count INTEGER NOT NULL DEFAULT 0,
                       career_hr INTEGER NOT NULL DEFAULT 0,
                       world_series_rings INTEGER NOT NULL DEFAULT 0,
                       team_count INTEGER NOT NULL DEFAULT 0,
                       franchise_count INTEGER NOT NULL DEFAULT 0,
                       season_count INTEGER NOT NULL DEFAULT 0,
                       hound_dog_eligible BOOLEAN NOT NULL DEFAULT false,
                       journeyman_eligible BOOLEAN NOT NULL DEFAULT false,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS po_games (
                       game_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                       state JSONB NOT NULL,
                       finished BOOLEAN NOT NULL DEFAULT false,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_po_games_active ON po_games(created_at DESC) WHERE NOT finished"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_po_games_created ON po_games(created_at DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS po_queue (
                       guest_id UUID PRIMARY KEY REFERENCES guests(guest_id) ON DELETE CASCADE,
                       display_name TEXT NOT NULL,
                       avoid_guest_id UUID,
                       enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_po_queue_enqueued ON po_queue(enqueued_at)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS po_invites (
                       code TEXT PRIMARY KEY,
                       host_guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       host_name TEXT NOT NULL,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 minutes'),
                       claimed_at TIMESTAMPTZ
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_po_invites_host ON po_invites(host_guest_id, created_at DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS po_rematches (
                       original_game_id UUID NOT NULL,
                       requester_guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (original_game_id, requester_guest_id)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS po_rematch_links (
                       original_game_id UUID PRIMARY KEY,
                       new_game_id UUID NOT NULL,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS po_postgame_exits (
                       original_game_id UUID NOT NULL,
                       guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       PRIMARY KEY (original_game_id, guest_id)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS app_sessions (
                       session_token TEXT PRIMARY KEY,
                       guest_id UUID NOT NULL REFERENCES guests(guest_id) ON DELETE CASCADE,
                       auth_user_id UUID NOT NULL,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                       last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_sessions_guest ON app_sessions(guest_id, created_at DESC)"
            )
        RUNTIME_SCHEMA_READY = True


def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260000)
    return hashed.hex(), salt.hex()


def _verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    candidate, _ = _hash_password(password, password_salt)
    return hmac.compare_digest(candidate, password_hash)


def _supabase_ready() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY)


def _public_app_url() -> str:
    if PUBLIC_APP_URL:
        return PUBLIC_APP_URL.rstrip("/")
    return request.url_root.rstrip("/")


def _supabase_headers(use_service: bool = False, bearer: str | None = None) -> dict[str, str]:
    key = SUPABASE_SERVICE_ROLE_KEY if use_service else SUPABASE_ANON_KEY
    if not SUPABASE_URL or not key:
        raise RuntimeError("Supabase Auth env vars are not configured.")
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif use_service:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _supabase_auth_url(path: str) -> str:
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is not configured.")
    return urljoin(SUPABASE_URL.rstrip("/") + "/", f"auth/v1/{path.lstrip('/')}")


def _supabase_auth_post(path: str, payload: dict, *, use_service: bool = False,
                        bearer: str | None = None):
    return requests.post(
        _supabase_auth_url(path),
        headers=_supabase_headers(use_service=use_service, bearer=bearer),
        json=payload,
        timeout=15,
    )


def _supabase_auth_get(path: str, *, use_service: bool = False, bearer: str | None = None):
    return requests.get(
        _supabase_auth_url(path),
        headers=_supabase_headers(use_service=use_service, bearer=bearer),
        timeout=15,
    )


def _supabase_signup(email: str, password: str, username: str, display_name: str, email_redirect_to: str | None):
    payload = {
        "email": email,
        "password": password,
        "data": {
            "username": username,
            "display_name": display_name,
        },
    }
    if email_redirect_to:
        payload["email_redirect_to"] = email_redirect_to
    return _supabase_auth_post("signup", payload)


def _supabase_signin(email: str, password: str):
    return _supabase_auth_post("token?grant_type=password", {
        "email": email,
        "password": password,
    })


def _supabase_resend_signup(email: str, email_redirect_to: str | None):
    payload = {"email": email, "type": "signup"}
    if email_redirect_to:
        payload["email_redirect_to"] = email_redirect_to
    return _supabase_auth_post("resend", payload)


def _supabase_reset_password(email: str, redirect_to: str | None):
    payload = {"email": email}
    if redirect_to:
        payload["redirect_to"] = redirect_to
    return _supabase_auth_post("recover", payload)


def _supabase_admin_delete_user(auth_user_id: str):
    return requests.delete(
        _supabase_auth_url(f"admin/users/{auth_user_id}"),
        headers=_supabase_headers(use_service=True),
        timeout=15,
    )


def _supabase_admin_create_user(email: str, password: str, username: str, display_name: str):
    return requests.post(
        _supabase_auth_url("admin/users"),
        headers=_supabase_headers(use_service=True),
        json={
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {
                "username": username,
                "display_name": display_name,
            },
        },
        timeout=15,
    )


def _extract_auth_user_id(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    user = payload.get("user")
    if isinstance(user, dict) and user.get("id"):
        return str(user["id"])
    if payload.get("id"):
        return str(payload["id"])
    return None


def _find_auth_user_id_by_email(conn, email: str) -> str | None:
    row = conn.execute(
        """SELECT id::text
             FROM auth.users
            WHERE lower(email) = %s
            ORDER BY created_at DESC
            LIMIT 1""",
        (email.lower(),),
    ).fetchone()
    return row[0] if row else None


def _create_app_session(conn, guest_id: str, auth_user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO app_sessions (session_token, guest_id, auth_user_id)
           VALUES (%s, %s, %s)""",
        (token, guest_id, auth_user_id),
    )
    return token


def _session_row(conn):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = conn.execute(
        """SELECT session_token, guest_id::text, auth_user_id::text
             FROM app_sessions
            WHERE session_token = %s""",
        (token,),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE app_sessions SET last_seen_at = now() WHERE session_token = %s",
            (token,),
        )
    return row


def _session_guest_id(conn) -> str | None:
    row = _session_row(conn)
    return row[1] if row else None


def _clear_app_session(conn):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        conn.execute("DELETE FROM app_sessions WHERE session_token = %s", (token,))


def _session_response(payload: dict, session_token: str | None = None):
    resp = make_response(jsonify(payload))
    if session_token:
        resp.set_cookie(
            SESSION_COOKIE,
            session_token,
            httponly=True,
            samesite="Lax",
            secure=not app.debug,
            path="/",
        )
    return resp


def _clear_session_response(payload: dict):
    resp = make_response(jsonify(payload))
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def _guest_stats(conn, guest_id: str) -> dict:
    bp_plays, bp_best, fr_plays, fr_wins, dr_plays, dr_wins, elo = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM bp_runs WHERE owner_guest_id = %s),
            (SELECT COALESCE(MAX(chain_length), 0) FROM bp_runs WHERE owner_guest_id = %s),
            (SELECT COUNT(*) FROM fr_results WHERE owner_guest_id = %s),
            (SELECT COALESCE(SUM(CASE WHEN won THEN 1 ELSE 0 END), 0) FROM fr_results WHERE owner_guest_id = %s),
            (SELECT COUNT(*) FROM dr_results WHERE owner_guest_id = %s),
            (SELECT COALESCE(SUM(CASE WHEN won THEN 1 ELSE 0 END), 0) FROM dr_results WHERE owner_guest_id = %s),
            (SELECT elo FROM guests WHERE guest_id = %s)
        """,
        (guest_id, guest_id, guest_id, guest_id, guest_id, guest_id, guest_id),
    ).fetchone()
    top_struck = conn.execute(
        """SELECT team_name, season, COUNT(*) AS n
             FROM guest_team_strikeouts
            WHERE owner_guest_id = %s AND sport_id = 'baseball'
            GROUP BY team_name, season
            ORDER BY n DESC, season DESC, team_name ASC
            LIMIT 3""",
        (guest_id,),
    ).fetchall()
    def daily_streak(sport_id: str) -> int:
        won_days = {
            row[0] for row in conn.execute(
                """SELECT puzzle_date FROM film_review_daily_attempts
                     WHERE owner_guest_id=%s AND sport_id=%s AND status='won' AND official""",
                (guest_id, sport_id),
            ).fetchall()
        }
        cursor = datetime.now(CENTRAL_TIME).date()
        # A streak can remain alive until today's puzzle is attempted.
        if cursor not in won_days:
            cursor -= timedelta(days=1)
        streak = 0
        while cursor in won_days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    baseball_stats = {
        "bp_plays": bp_plays,
        "bp_best": bp_best,
        "fr_plays": fr_plays,
        "fr_wins": fr_wins,
        "dr_plays": dr_plays,
        "dr_wins": dr_wins,
        "dr_losses": max(0, dr_plays - dr_wins),
        "dr_elo": elo,
        "top_struck_teams": [
            {"team_name": team_name, "season": season, "season_label": str(season), "count": count}
            for team_name, season, count in top_struck
        ],
        "fr_daily_streak": daily_streak("baseball"),
    }
    # Keep the established baseball fields for the current profile UI, while
    # exposing the identical shape for every league. This lets the profile
    # grow sport tabs without another storage migration.
    sports = {"baseball": baseball_stats}
    for sport in LOCAL_SPORT_SEEDS:
        values = conn.execute(
            """SELECT
                   (SELECT COUNT(*) FROM bp_runs WHERE owner_guest_id=%s AND sport_id=%s),
                   (SELECT COALESCE(MAX(chain_length), 0) FROM bp_runs WHERE owner_guest_id=%s AND sport_id=%s),
                   (SELECT COUNT(*) FROM fr_results WHERE owner_guest_id=%s AND sport_id=%s),
                   (SELECT COALESCE(SUM(CASE WHEN won THEN 1 ELSE 0 END), 0) FROM fr_results WHERE owner_guest_id=%s AND sport_id=%s),
                   (SELECT COUNT(*) FROM fr_results WHERE owner_guest_id=%s AND sport_id=%s AND unit='offense'),
                   (SELECT COALESCE(SUM(CASE WHEN won THEN 1 ELSE 0 END), 0) FROM fr_results WHERE owner_guest_id=%s AND sport_id=%s AND unit='offense'),
                   (SELECT COUNT(*) FROM fr_results WHERE owner_guest_id=%s AND sport_id=%s AND unit='defense'),
                   (SELECT COALESCE(SUM(CASE WHEN won THEN 1 ELSE 0 END), 0) FROM fr_results WHERE owner_guest_id=%s AND sport_id=%s AND unit='defense'),
                   (SELECT COUNT(*) FROM dr_results WHERE owner_guest_id=%s AND sport_id=%s),
                   (SELECT COALESCE(SUM(CASE WHEN won THEN 1 ELSE 0 END), 0) FROM dr_results WHERE owner_guest_id=%s AND sport_id=%s),
                   (SELECT elo FROM guest_sport_ratings WHERE guest_id=%s AND sport_id=%s)""",
            (guest_id, sport, guest_id, sport, guest_id, sport, guest_id, sport,
             guest_id, sport, guest_id, sport, guest_id, sport, guest_id, sport,
             guest_id, sport, guest_id, sport, guest_id, sport),
        ).fetchone()
        (bp_plays_s, bp_best_s, fr_plays_s, fr_wins_s, fr_off_plays, fr_off_wins,
         fr_def_plays, fr_def_wins, dr_plays_s, dr_wins_s, elo_s) = values
        struck = conn.execute(
            """SELECT team_name, season, COUNT(*) FROM guest_team_strikeouts
                 WHERE owner_guest_id=%s AND sport_id=%s
                 GROUP BY team_name, season ORDER BY COUNT(*) DESC, season DESC, team_name ASC LIMIT 3""",
            (guest_id, sport),
        ).fetchall()
        sports[sport] = {
            "bp_plays": bp_plays_s, "bp_best": bp_best_s, "fr_plays": fr_plays_s,
            "fr_wins": fr_wins_s, "dr_plays": dr_plays_s, "dr_wins": dr_wins_s,
            "fr_offense_plays": fr_off_plays, "fr_offense_wins": fr_off_wins,
            "fr_defense_plays": fr_def_plays, "fr_defense_wins": fr_def_wins,
            "dr_losses": max(0, dr_plays_s - dr_wins_s), "dr_elo": elo_s or 1200,
            "top_struck_teams": [{"team_name": name, "season": season,
                                  "season_label": _sport_season_label(sport, season), "count": count}
                                  for name, season, count in struck],
            "fr_daily_streak": daily_streak(sport),
        }
    return {**baseball_stats, "sports": sports}


def _valid_uuid_text(value: str | None) -> bool:
    if not value:
        return False
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _guest_profile(conn, guest_id: str, *, authenticated: bool = False) -> dict | None:
    row = conn.execute(
        """SELECT
               g.guest_id::text,
               g.display_name,
               g.created_at,
               g.playoff_win_condition_preference,
               u.username,
               u.email,
               u.auth_user_id::text
             FROM guests g
             LEFT JOIN users u ON u.user_id = g.guest_id
            WHERE g.guest_id = %s""",
        (guest_id,),
    ).fetchone()
    if not row:
        return None
    gid, display_name, created_at, playoff_preference, username, email, auth_user_id = row
    return {
        "guest_id": gid,
        "display_name": display_name or f"Guest {gid[:8]}",
        "created_at": created_at.isoformat(),
        "account": (
            {"username": username, "email": email, "auth_user_id": auth_user_id}
            if username or auth_user_id else None
        ),
        "authenticated": bool(authenticated and auth_user_id),
        "playoff_win_condition_preference": playoff_preference or "random",
        "stats": _guest_stats(conn, gid),
    }


def _guest_label(conn, guest_id: str) -> str:
    row = conn.execute(
        """SELECT COALESCE(u.username, g.display_name, %s)
             FROM guests g
             LEFT JOIN users u ON u.user_id = g.guest_id
            WHERE g.guest_id = %s""",
        (f"Guest {guest_id[:8]}", guest_id),
    ).fetchone()
    return row[0] if row and row[0] else f"Guest {guest_id[:8]}"


def _require_user(conn, guest_id: str):
    return conn.execute(
        """SELECT user_id::text, username, email, display_name
             FROM users
            WHERE user_id = %s""",
        (guest_id,),
    ).fetchone()


def _session_account_guest_id(conn) -> str | None:
    guest_id = _session_guest_id(conn)
    if not guest_id:
        return None
    return guest_id if _require_user(conn, guest_id) else None


def _friendship_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _random_playoff_powerup() -> str:
    return secrets.choice(list(PLAYOFF_POWERUPS.keys()))


def _all_playoff_powerups() -> list[str]:
    return list(PLAYOFF_POWERUPS.keys())


def _random_playoff_win_condition() -> str:
    return secrets.choice(list(PLAYOFF_WIN_CONDITIONS.keys()))


def _random_playoff_condition_for_guest(conn, guest_id: str, sport: str, conditions: dict) -> str:
    """Choose randomly without immediately recycling a guest's recent draw."""
    rows = conn.execute(
        """SELECT condition_key FROM guest_random_playoff_conditions
             WHERE guest_id = %s AND sport_id = %s
             ORDER BY assigned_at DESC LIMIT 3""",
        (guest_id, sport),
    ).fetchall()
    recent = {row[0] for row in rows}
    options = [key for key in conditions if key not in recent] or list(conditions)
    choice = secrets.choice(options)
    conn.execute(
        """INSERT INTO guest_random_playoff_conditions (guest_id, sport_id, condition_key)
             VALUES (%s, %s, %s)""",
        (guest_id, sport, choice),
    )
    return choice


def _normalized_playoff_preference(value: str | None) -> str:
    value = (value or "random").strip()
    return value if value in PLAYOFF_WIN_CONDITIONS else "random"


def _playoff_condition_for_guest(conn, guest_id: str) -> str:
    row = conn.execute(
        "SELECT playoff_win_condition_preference FROM guests WHERE guest_id = %s",
        (guest_id,),
    ).fetchone()
    preference = _normalized_playoff_preference(row[0] if row else None)
    return (_random_playoff_condition_for_guest(conn, guest_id, "baseball", PLAYOFF_WIN_CONDITIONS)
            if preference == "random" else preference)


def _save_playoff_preference(conn, guest_id: str, value: str | None) -> str:
    preference = _normalized_playoff_preference(value)
    conn.execute(
        "UPDATE guests SET playoff_win_condition_preference = %s WHERE guest_id = %s",
        (preference, guest_id),
    )
    return preference


def _playoff_player_role(conn, player_id: str) -> str:
    row = conn.execute(
        "SELECT COALESCE(primary_pos, '') FROM players WHERE player_id = %s",
        (player_id,),
    ).fetchone()
    primary_pos = (row[0] or "").upper() if row else ""
    return "pitcher" if primary_pos == "P" else "batter"


def _playoff_powerup_state(blob: dict, viewer_guest_id: str | None) -> dict:
    def side_payload(side_key: str) -> list[dict]:
        keys = blob.get(f"{side_key}_powerup_keys")
        if not keys:
            legacy_key = blob.get(f"{side_key}_powerup_key")
            keys = [legacy_key] if legacy_key else []
        used_keys = set(blob.get(f"{side_key}_powerup_used_keys") or [])
        legacy_key = blob.get(f"{side_key}_powerup_key")
        if blob.get(f"{side_key}_powerup_used") and legacy_key:
            used_keys.add(legacy_key)
        rows = []
        for key in keys:
            meta = PLAYOFF_POWERUPS.get(key, {})
            rows.append({
                "key": key,
                "label": meta.get("label"),
                "description": meta.get("description"),
                "used": key in used_keys,
                "kind": meta.get("kind"),
                "owner": blob.get("p1") if side_key == "p1" else blob.get("p2"),
            })
        return rows

    p1_side = side_payload("p1")
    p2_side = side_payload("p2")
    your_side = None
    if viewer_guest_id == blob.get("p1_guest_id"):
        your_side = "p1"
    elif viewer_guest_id == blob.get("p2_guest_id"):
        your_side = "p2"
    your_powerup = p1_side if your_side == "p1" else p2_side if your_side == "p2" else None
    opponent_powerup = p2_side if your_side == "p1" else p1_side if your_side == "p2" else None
    active_key = blob.get("active_turn_powerup")
    return {
        "your_powerups": your_powerup or [],
        "opponent_powerups": opponent_powerup or [],
        "active_turn_powerup": {
            "key": active_key,
            "label": PLAYOFF_POWERUPS.get(active_key, {}).get("label"),
        } if active_key else None,
        "next_turn_seconds_override": blob.get("next_turn_seconds_override"),
        "turn_powerup_used": bool(blob.get("turn_powerup_used")),
        "opening_lock_moves": PLAYOFF_OPENING_LOCK_MOVES,
    }


def _playoff_condition_progress_state(blob: dict, viewer_guest_id: str | None) -> dict:
    def side_payload(side_key: str) -> dict:
        key = blob.get(f"{side_key}_win_condition_key")
        meta = PLAYOFF_WIN_CONDITIONS.get(key, {})
        return {
            "key": key,
            "label": meta.get("label"),
            "description": meta.get("description"),
            "target": meta.get("target", 0),
            "mode": meta.get("mode", "count"),
            "progress": int(blob.get(f"{side_key}_win_progress") or 0),
            "completed": bool(blob.get(f"{side_key}_win_completed")),
        }

    your_side = None
    if viewer_guest_id == blob.get("p1_guest_id"):
        your_side = "p1"
    elif viewer_guest_id == blob.get("p2_guest_id"):
        your_side = "p2"
    your_condition = side_payload("p1") if your_side == "p1" else side_payload("p2") if your_side == "p2" else None
    opponent_condition = side_payload("p2") if your_side == "p1" else side_payload("p1") if your_side == "p2" else None
    return {
        "your_condition": your_condition,
        "opponent_condition": opponent_condition,
    }


def _playoff_trait_row(conn, player_id: str):
    row = conn.execute(
        """SELECT birth_country, is_japanese, is_cuban, is_canadian, mvp_count,
                  roty_count, gold_glove_count, triple_crown_count, career_hr,
                  world_series_rings, team_count, franchise_count, season_count,
                  hound_dog_eligible, journeyman_eligible
             FROM player_playoff_traits
            WHERE player_id = %s""",
        (player_id,),
    ).fetchone()
    if not row:
        return {
            "birth_country": None,
            "is_japanese": False,
            "is_cuban": False,
            "is_canadian": False,
            "mvp_count": 0,
            "roty_count": 0,
            "gold_glove_count": 0,
            "triple_crown_count": 0,
            "career_hr": 0,
            "world_series_rings": 0,
            "team_count": 0,
            "franchise_count": 0,
            "season_count": 0,
            "hound_dog_eligible": False,
            "journeyman_eligible": False,
        }
    keys = [
        "birth_country", "is_japanese", "is_cuban", "is_canadian", "mvp_count",
        "roty_count", "gold_glove_count", "triple_crown_count", "career_hr",
        "world_series_rings", "team_count", "franchise_count", "season_count",
        "hound_dog_eligible", "journeyman_eligible",
    ]
    return dict(zip(keys, row))


def _playoff_condition_increment(condition_key: str, traits: dict) -> int:
    if condition_key == "sunset_kingdom":
        return 1 if traits["is_japanese"] else 0
    if condition_key == "havana_heat":
        return 1 if traits["is_cuban"] else 0
    if condition_key == "maple_corridor":
        return 1 if traits["is_canadian"] else 0
    if condition_key == "mvp_circle":
        return 1 if int(traits["mvp_count"]) > 0 else 0
    if condition_key == "young_buck":
        return 1 if int(traits["roty_count"]) > 0 else 0
    if condition_key == "gonna_be_golden":
        return 1 if int(traits["gold_glove_count"]) > 0 else 0
    if condition_key == "secretariat":
        return 1 if int(traits["triple_crown_count"]) > 0 else 0
    if condition_key == "hound_dog":
        return 1 if traits["hound_dog_eligible"] else 0
    if condition_key == "great_bambinos":
        return 1 if int(traits["career_hr"]) >= 500 else 0
    if condition_key == "ring_chaser":
        return int(traits["world_series_rings"] or 0)
    if condition_key == "journeyman":
        return 1 if traits["journeyman_eligible"] else 0
    if condition_key == "four_hundred_club":
        return 1 if int(traits["career_hr"]) >= 400 else 0
    return 0


def _apply_playoff_win_condition_hit(conn, blob: dict, player_id: str, mover_side: str) -> dict:
    key = blob.get(f"{mover_side}_win_condition_key")
    meta = PLAYOFF_WIN_CONDITIONS.get(key, {})
    progress = int(blob.get(f"{mover_side}_win_progress") or 0)
    traits = _playoff_trait_row(conn, player_id)
    increment = _playoff_condition_increment(key, traits)
    hits = list(blob.get("chain_win_condition_hits") or [False] * (len(blob.get("chain") or [])))
    hits.append(increment > 0)
    blob["chain_win_condition_hits"] = hits
    if increment <= 0:
        return {
            "hit": False,
            "progress": progress,
            "target": meta.get("target", 0),
            "completed": False,
            "label": meta.get("label"),
        }
    progress += increment
    target = int(meta.get("target", 0) or 0)
    completed = progress >= target if target else False
    blob[f"{mover_side}_win_progress"] = progress
    blob[f"{mover_side}_win_completed"] = completed
    return {
        "hit": True,
        "progress": progress,
        "target": target,
        "completed": completed,
        "label": meta.get("label"),
        "increment": increment,
    }


def _resolve_pick(conn, raw: str | None = None, player_id: str | None = None) -> tuple[str | None, str | None, str | None, int]:
    if (raw is None) == (player_id is None):
        raise ValueError("pass exactly one of raw or player_id")
    if raw is not None:
        matches = find_player_by_name(PgEngineConn(conn), raw)
        if not matches:
            return None, None, None, 0
        pid, display_name, disambiguation, _career_games = matches[0]
        return pid, display_name, disambiguation, len(matches)
    row = conn.execute(
        "SELECT display_name, disambiguation FROM players_searchable WHERE player_id = %s",
        (player_id,),
    ).fetchone()
    if not row:
        return None, None, None, 0
    return player_id, row[0], row[1], 1


def _playoff_qualification_rows(conn, current_player_id: str, candidate_player_id: str, powerup_key: str):
    return conn.execute(
        """SELECT q.team_id, q.season, t.name
             FROM player_powerup_qualifications q
             JOIN teams t
               ON t.team_id = q.team_id
              AND t.season = q.season
            WHERE q.player_id = %s
              AND q.powerup_key = %s
              AND q.season >= 2000
              AND q.franchise_id IN (
                    SELECT DISTINCT tm.franchise_id
                      FROM appearances a
                      JOIN teams tm
                        ON tm.team_id = a.team_id
                       AND tm.season = a.season
                     WHERE a.player_id = %s AND a.season >= 2000
              )
            ORDER BY q.season, q.team_id""",
        (candidate_player_id, powerup_key, current_player_id),
    ).fetchall()


def _apply_playoff_powerup_move(conn, state: GameState, blob: dict,
                                raw: str | None = None, player_id: str | None = None) -> dict | None:
    powerup_key = blob.get("active_turn_powerup")
    if not powerup_key or powerup_key not in PLAYOFF_POWERUPS:
        return None
    pid, display_name, disambiguation, ambiguous_count = _resolve_pick(
        conn, raw=raw if raw is not None else None, player_id=player_id,
    )
    if not pid:
        return None
    if pid in state.chain:
        return None

    powerup = PLAYOFF_POWERUPS[powerup_key]
    role_needed = powerup.get("role", "any")
    candidate_role = _playoff_player_role(conn, pid)
    if role_needed != "any" and candidate_role != role_needed:
        return {
            "outcome": "powerup_not_eligible",
            "player_id": pid,
            "display_name": display_name,
            "disambiguation": disambiguation,
            "ambiguous_count": ambiguous_count,
            "powerup_key": powerup_key,
            "powerup_label": powerup["label"],
            "reason": f"{powerup['label']} only works on {role_needed}s.",
        }

    qualifying_rows = _playoff_qualification_rows(conn, state.current_player_id, pid, powerup_key)
    if not qualifying_rows:
        return {
            "outcome": "powerup_not_eligible",
            "player_id": pid,
            "display_name": display_name,
            "disambiguation": disambiguation,
            "ambiguous_count": ambiguous_count,
            "powerup_key": powerup_key,
            "powerup_label": powerup["label"],
            "reason": f"{display_name} is not a {powerup['label']} match for {state.current_player_name}.",
        }

    available = [(t, s, name) for t, s, name in qualifying_rows if not state.is_burned((t, s))]
    if not available:
        return {
            "outcome": "blocked_by_burned",
            "player_id": pid,
            "display_name": display_name,
            "disambiguation": disambiguation,
            "ambiguous_count": ambiguous_count,
            "powerup_key": powerup_key,
            "powerup_label": powerup["label"],
            "shared_seasons": [
                {"team_id": t, "season": s, "team_name": name}
                for t, s, name in qualifying_rows
            ],
            "burned_seasons": [
                {"team_id": t, "season": s, "team_name": name}
                for t, s, name in qualifying_rows
            ],
        }

    team_id, season, team_name = available[0]
    state.strikes[(team_id, season)] = state.strikes.get((team_id, season), 0) + 1
    state.chain.append(pid)
    state.chain_names.append(display_name)
    state.chain_shared_with_prev.append([(team_id, season)])
    chain_link_meta = list(blob.get("chain_link_meta") or [None] * (len(state.chain) - 1))
    chain_link_meta.append({
        "type": "powerup",
        "powerup_key": powerup_key,
        "powerup_label": powerup["label"],
    })
    blob["chain_link_meta"] = chain_link_meta
    return {
        "outcome": "valid",
        "player_id": pid,
        "display_name": display_name,
        "disambiguation": disambiguation,
        "ambiguous_count": ambiguous_count,
        "shared_seasons": [{"team_id": team_id, "season": season, "team_name": team_name}],
        "burned_seasons": [],
        "powerup_key": powerup_key,
        "powerup_label": powerup["label"],
        "move_via_powerup": True,
    }


def _friends_payload(conn, guest_id: str) -> dict:
    user_row = _require_user(conn, guest_id)
    if not user_row:
        return {"error": "account required"}
    incoming_requests = conn.execute(
        """SELECT r.request_id::text, u.user_id::text, u.username, COALESCE(u.display_name, u.username)
             FROM friend_requests r
             JOIN users u ON u.user_id = r.sender_user_id
            WHERE r.recipient_user_id = %s AND r.status = 'pending'
            ORDER BY r.created_at DESC""",
        (guest_id,),
    ).fetchall()
    outgoing_requests = conn.execute(
        """SELECT r.request_id::text, u.user_id::text, u.username, COALESCE(u.display_name, u.username)
             FROM friend_requests r
             JOIN users u ON u.user_id = r.recipient_user_id
            WHERE r.sender_user_id = %s AND r.status = 'pending'
            ORDER BY r.created_at DESC""",
        (guest_id,),
    ).fetchall()
    friends = conn.execute(
        """SELECT friend_user_id::text, username, display_name FROM (
               SELECT u.user_id AS friend_user_id, u.username, COALESCE(u.display_name, u.username) AS display_name
                 FROM friendships f
                 JOIN users u ON u.user_id = f.user_b_id
                WHERE f.user_a_id = %s
               UNION ALL
               SELECT u.user_id AS friend_user_id, u.username, COALESCE(u.display_name, u.username) AS display_name
                 FROM friendships f
                 JOIN users u ON u.user_id = f.user_a_id
                WHERE f.user_b_id = %s
           ) q
           ORDER BY username""",
        (guest_id, guest_id),
    ).fetchall()
    incoming_challenges = conn.execute(
        """SELECT challenge_id::text, sender_user_id::text, sender_name
             FROM dr_friend_challenges
            WHERE recipient_user_id = %s AND status = 'pending'
            ORDER BY created_at DESC""",
        (guest_id,),
    ).fetchall()
    outgoing_challenges = conn.execute(
        """SELECT challenge_id::text, recipient_user_id::text, recipient_name
             FROM dr_friend_challenges
            WHERE sender_user_id = %s AND status = 'pending'
            ORDER BY created_at DESC""",
        (guest_id,),
    ).fetchall()
    challenge_history = conn.execute(
        """SELECT
               r.opponent_guest_id::text,
               COALESCE(u.username, r.opponent_name) AS opponent_label,
               r.chain_length,
               r.won,
               r.finished_at
             FROM dr_results r
             LEFT JOIN users u ON u.user_id = r.opponent_guest_id
            WHERE r.owner_guest_id = %s
              AND r.opponent_guest_id IS NOT NULL
              AND EXISTS (
                    SELECT 1
                      FROM friendships f
                     WHERE (f.user_a_id = %s AND f.user_b_id = r.opponent_guest_id)
                        OR (f.user_b_id = %s AND f.user_a_id = r.opponent_guest_id)
              )
            ORDER BY r.finished_at DESC
            LIMIT 3""",
        (guest_id, guest_id, guest_id),
    ).fetchall()
    matched = conn.execute(
        """SELECT challenge_id::text, game_id::text
             FROM dr_friend_challenges
            WHERE (sender_user_id = %s OR recipient_user_id = %s)
              AND status = 'accepted'
              AND game_id IS NOT NULL
            ORDER BY responded_at DESC NULLS LAST, created_at DESC
            LIMIT 1""",
        (guest_id, guest_id),
    ).fetchone()
    matched_game = None
    if matched:
        gid = matched[1]
        blob, state = _load_game(conn, "dr_games", gid)
        if blob and not blob.get("finished"):
            blob["viewer_guest_id"] = guest_id
            matched_game = dr_state_dict(gid, blob, state, conn=conn)
    return {
        "friends": [
            {"user_id": uid, "username": username, "display_name": display_name}
            for uid, username, display_name in friends
        ],
        "incoming_requests": [
            {"request_id": rid, "user_id": uid, "username": username, "display_name": display_name}
            for rid, uid, username, display_name in incoming_requests
        ],
        "outgoing_requests": [
            {"request_id": rid, "user_id": uid, "username": username, "display_name": display_name}
            for rid, uid, username, display_name in outgoing_requests
        ],
        "incoming_challenges": [
            {"challenge_id": cid, "user_id": uid, "name": name}
            for cid, uid, name in incoming_challenges
        ],
        "outgoing_challenges": [
            {"challenge_id": cid, "user_id": uid, "name": name}
            for cid, uid, name in outgoing_challenges
        ],
        "challenge_history": [
            {
                "opponent_guest_id": opponent_guest_id,
                "opponent_label": opponent_label,
                "chain_length": chain_length or 0,
                "won": won,
                "finished_at": finished_at.isoformat(),
            }
            for opponent_guest_id, opponent_label, chain_length, won, finished_at in challenge_history
        ],
        "matched_game": matched_game,
    }


def _create_guest(conn) -> dict:
    gid = str(uuid.uuid4())
    display_name = f"Guest {gid[:8]}"
    conn.execute(
        "INSERT INTO guests (guest_id, display_name) VALUES (%s, %s)",
        (gid, display_name),
    )
    return _guest_profile(conn, gid)


def _save_bp_run(conn, blob: dict, state: GameState):
    if blob.get("result_saved"):
        return
    guest_id = blob.get("owner_guest_id")
    if guest_id:
        conn.execute(
            """INSERT INTO bp_runs (owner_guest_id, seed_player_id, chain_length)
                 VALUES (%s, %s, %s)""",
            (guest_id, blob.get("seed_player_id", DEFAULT_SEED), len(state.chain)),
        )
        _record_struck_out_teams(conn, guest_id, "bp", state)
    blob["result_saved"] = True


def _record_player_usage(conn, player_id: str, mode: str):
    bp_inc = 1 if mode == "bp" else 0
    dr_inc = 1 if mode == "dr" else 0
    conn.execute(
        """INSERT INTO player_usage (
               player_id, total_count, bp_count, dr_count, last_used_at
           ) VALUES (%s, 1, %s, %s, now())
           ON CONFLICT (player_id) DO UPDATE
           SET total_count = player_usage.total_count + 1,
               bp_count = player_usage.bp_count + EXCLUDED.bp_count,
               dr_count = player_usage.dr_count + EXCLUDED.dr_count,
               last_used_at = now()""",
        (player_id, bp_inc, dr_inc),
    )


def _record_sport_player_usage(conn, sport: str, player_id: str, mode: str):
    bp_inc = 1 if mode == "bp" else 0
    dr_inc = 1 if mode in {"dr", "po"} else 0
    conn.execute(
        """INSERT INTO sport_player_usage (
               sport_id, player_id, total_count, bp_count, dr_count, last_used_at
           ) VALUES (%s, %s, 1, %s, %s, now())
           ON CONFLICT (sport_id, player_id) DO UPDATE
           SET total_count = sport_player_usage.total_count + 1,
               bp_count = sport_player_usage.bp_count + EXCLUDED.bp_count,
               dr_count = sport_player_usage.dr_count + EXCLUDED.dr_count,
               last_used_at = now()""",
        (sport, player_id, bp_inc, dr_inc),
    )


def _record_sport_struck_out_teams(conn, guest_id: str | None, sport: str,
                                    mode: str, state: GameState):
    if not guest_id:
        return
    for (team_id, season), count in state.strikes.items():
        if count < STRIKES_TO_BURN:
            continue
        conn.execute(
            """INSERT INTO guest_team_strikeouts (
                   owner_guest_id, sport_id, mode, team_name, team_id, season
               ) VALUES (%s, %s, %s, %s, %s, %s)""",
            (guest_id, sport, mode, _sport_team_name(conn, sport, team_id, season), team_id, season),
        )


def _record_struck_out_teams(conn, guest_id: str | None, mode: str, state: GameState):
    if not guest_id:
        return
    rows = []
    for (team_id, season), count in state.strikes.items():
        if count >= STRIKES_TO_BURN:
            rows.append((guest_id, mode, TEAM_NAME.get((team_id, season), team_id), team_id, season))
    for row in rows:
        conn.execute(
            """INSERT INTO guest_team_strikeouts (
                   owner_guest_id, mode, team_name, team_id, season
               ) VALUES (%s, %s, %s, %s, %s)""",
            row,
        )


def _save_dr_result(conn, blob: dict, game_id: str | None = None):
    if blob.get("result_saved"):
        return
    p1_guest_id = blob.get("p1_guest_id")
    p2_guest_id = blob.get("p2_guest_id")
    if not p1_guest_id or not p2_guest_id:
        blob["result_saved"] = True
        return
    p1_row = conn.execute(
        "SELECT elo FROM guests WHERE guest_id = %s",
        (p1_guest_id,),
    ).fetchone()
    p2_row = conn.execute(
        "SELECT elo FROM guests WHERE guest_id = %s",
        (p2_guest_id,),
    ).fetchone()
    p1_before = p1_row[0] if p1_row else 1200
    p2_before = p2_row[0] if p2_row else 1200
    p1_won = blob.get("winner") == blob.get("p1")
    chain_length = len(deserialize_state(blob).chain)
    p1_after = max(800, p1_before + (16 if p1_won else -16))
    p2_after = max(800, p2_before + (16 if not p1_won else -16))
    conn.execute(
        "UPDATE guests SET elo = %s WHERE guest_id = %s",
        (p1_after, p1_guest_id),
    )
    conn.execute(
        "UPDATE guests SET elo = %s WHERE guest_id = %s",
        (p2_after, p2_guest_id),
    )
    conn.execute(
        """INSERT INTO dr_results (
               game_id, owner_guest_id, opponent_guest_id, opponent_name, chain_length, won, elo_before, elo_after
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (owner_guest_id, game_id) WHERE game_id IS NOT NULL DO NOTHING""",
        (game_id, p1_guest_id, p2_guest_id, blob.get("p2"), chain_length, bool(p1_won), p1_before, p1_after),
    )
    conn.execute(
        """INSERT INTO dr_results (
               game_id, owner_guest_id, opponent_guest_id, opponent_name, chain_length, won, elo_before, elo_after
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (owner_guest_id, game_id) WHERE game_id IS NOT NULL DO NOTHING""",
        (game_id, p2_guest_id, p1_guest_id, blob.get("p1"), chain_length, bool(not p1_won), p2_before, p2_after),
    )
    state = deserialize_state(blob)
    _record_struck_out_teams(conn, p1_guest_id, "dr", state)
    _record_struck_out_teams(conn, p2_guest_id, "dr", state)
    blob["result_saved"] = True


def _save_fr_result(conn, blob: dict):
    if blob.get("result_saved"):
        return
    guest_id = blob.get("owner_guest_id")
    if guest_id:
        conn.execute(
            """INSERT INTO fr_results (
                   owner_guest_id, puzzle_id, hits, fouls, strikes, won
               ) VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                guest_id,
                blob.get("puzzle_id"),
                blob.get("hits", 0),
                blob.get("fouls", 0),
                blob.get("strikes", 0),
                bool(blob.get("won", False)),
            ),
        )
    blob["result_saved"] = True


def _hydrate_player_cards(conn, player_ids: list[str]) -> dict[str, dict]:
    """Batch-hydrate player cards into the process cache using one DB roundtrip
    for player rows and one for appearance rows."""
    wanted = []
    out: dict[str, dict] = {}
    for pid in player_ids:
        cached = PLAYER_CARD_CACHE.get(pid)
        if cached is not None:
            out[pid] = cached
        elif pid not in wanted:
            wanted.append(pid)
    if not wanted:
        return out

    with PLAYER_CARD_LOCK:
        missing = [pid for pid in wanted if PLAYER_CARD_CACHE.get(pid) is None]
        for pid in wanted:
            cached = PLAYER_CARD_CACHE.get(pid)
            if cached is not None:
                out[pid] = cached
        if not missing:
            return out

        player_rows = conn.execute(
            """SELECT player_id, mlbam_id, debut_year, final_year, name_first, name_last
                 FROM players
                WHERE player_id = ANY(%s)""",
            (missing,),
        ).fetchall()
        player_map = {
            pid: (mlbam_id, debut_year, final_year, first, last)
            for pid, mlbam_id, debut_year, final_year, first, last in player_rows
        }
        registry_urls = _headshot_registry_urls(conn, "baseball", missing)
        appearance_rows = conn.execute(
            """SELECT a.player_id, a.season, a.team_id, t.name
                 FROM appearances a
                 JOIN teams t ON t.team_id = a.team_id AND t.season = a.season
                WHERE a.player_id = ANY(%s) AND a.season >= 2000
                ORDER BY a.player_id, a.season, t.team_id""",
            (missing,),
        ).fetchall()
        appearances_by_player: dict[str, list[tuple[int, str, str]]] = {pid: [] for pid in missing}
        for pid, season, team_id, team_name in appearance_rows:
            appearances_by_player.setdefault(pid, []).append((season, team_id, team_name))

        for pid in missing:
            mlbam_id, debut_year, final_year, first, last = player_map.get(
                pid, (None, None, None, None, None)
            )
            spans: list[list] = []
            for season, team_id, team_name in appearances_by_player.get(pid, []):
                if spans and spans[-1][0] == team_id and spans[-1][3] == season - 1:
                    spans[-1][3] = season
                else:
                    spans.append([team_id, team_name, season, season])
            teams_list = [
                f"{name} {_sport_card_stint_label('baseball', start, end)}"
                for _, name, start, end in spans
            ]
            team_stints = [
                {"team_id": team_id, "team_name": name, "start": start, "end": end,
                 "seasons": end - start + 1,
                 "label": f"{name} {_sport_card_stint_label('baseball', start, end)}"}
                for team_id, name, start, end in spans
            ]
            card = {
                "mlbam_id": mlbam_id,
                "headshot_url": (registry_urls[pid] if pid in registry_urls
                                  else HEADSHOT_URL.format(mlbam_id) if mlbam_id else None),
                "debut_year": max(debut_year or 2000, 2000),
                "final_year": final_year,
                "teams": teams_list,
                "team_stints": team_stints,
                "name_first": first,
                "name_last": last,
            }
            PLAYER_CARD_CACHE[pid] = card
            out[pid] = card
    return out


def player_card(player_id: str) -> dict:
    cached = PLAYER_CARD_CACHE.get(player_id)
    if cached is not None:
        return cached
    with db() as conn:
        return _hydrate_player_cards(conn, [player_id]).get(player_id, {
            "mlbam_id": None,
            "headshot_url": None,
            "debut_year": None,
            "final_year": None,
            "teams": [],
            "name_first": None,
            "name_last": None,
        })


# ============================================================
# State serialization (GameState <-> dict for JSONB)
# ============================================================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _move_submitted_in_time(data: dict, live_elapsed: float, turn_seconds: float) -> bool:
    if live_elapsed <= turn_seconds:
        return True
    try:
        client_remaining = float(data.get("client_remaining_seconds"))
    except (TypeError, ValueError):
        client_remaining = None
    return (
        client_remaining is not None
        and client_remaining >= -0.2
        and live_elapsed <= turn_seconds + MOVE_GRACE_SECONDS
    )


def serialize_state(state: GameState) -> dict:
    """GameState -> JSON-friendly dict. Strikes' tuple keys can't survive
    JSON, so they ride as a list of objects."""
    return {
        "chain": list(state.chain),
        "chain_names": list(state.chain_names),
        "chain_shared_with_prev": [
            [[t, s] for t, s in shared]
            for shared in state.chain_shared_with_prev
        ],
        "strikes": [
            {"team_id": t, "season": s, "count": n}
            for (t, s), n in sorted(state.strikes.items())
        ],
    }


def deserialize_state(blob: dict) -> GameState:
    state = GameState()
    state.chain = list(blob.get("chain", []))
    state.chain_names = list(blob.get("chain_names", []))
    state.chain_shared_with_prev = [
        [(pair[0], pair[1]) for pair in shared]
        for shared in blob.get("chain_shared_with_prev", [])
    ]
    state.strikes = {
        (row["team_id"], row["season"]): row["count"]
        for row in blob.get("strikes", [])
    }
    return state


def chain_dict(state: GameState, cards: dict[str, dict] | None = None) -> list[dict]:
    """Hydrated chain for the client: full player cards + per-link shared
    seasons with display team-name."""
    ensure_static_caches()
    if cards is None:
        with db() as conn:
            cards = _hydrate_player_cards(conn, list(state.chain))
    out = []
    for i, (pid, name) in enumerate(zip(state.chain, state.chain_names)):
        card = cards.get(pid) or player_card(pid)
        shared = state.chain_shared_with_prev[i]
        out.append({
            "id": pid,
            "name": name,
            "mlbam_id": card["mlbam_id"],
            "headshot_url": card["headshot_url"],
            "debut_year": card["debut_year"],
            "final_year": card["final_year"],
            "teams": card["teams"],
            "team_stints": card.get("team_stints", []),
            "shared_with_prev": [
                {
                    "team_id": t,
                    "season": s,
                    "team_name": TEAM_NAME.get((t, s), t),
                }
                for t, s in shared
            ],
        })
    return out


def strikes_dict(state: GameState) -> list[dict]:
    ensure_static_caches()
    return [
        {
            "team_id": t,
            "season": s,
            "count": n,
            "team_name": TEAM_NAME.get((t, s), t),
        }
        for (t, s), n in sorted(state.strikes.items())
    ]


def result_to_dict(r: MoveResult) -> dict:
    ensure_static_caches()
    return {
        "outcome": r.outcome.value,
        "player_id": r.player_id,
        "display_name": r.display_name,
        "disambiguation": r.disambiguation,
        "shared_seasons": [
            {"team_id": t, "season": s, "team_name": TEAM_NAME.get((t, s), t)}
            for t, s in r.shared_seasons
        ],
        "burned_seasons": [
            {"team_id": t, "season": s, "team_name": TEAM_NAME.get((t, s), t)}
            for t, s in r.burned_seasons
        ],
        "ambiguous_count": r.ambiguous_count,
    }


# ============================================================
# Game-state storage helpers
# Each *_load returns (state_dict, GameState) or (None, None).
# Each *_save writes the blob back; finished column tracked separately.
# ============================================================

def _load_game(conn, table: str, game_id: str):
    cur = conn.execute(
        f"SELECT state, finished FROM {table} WHERE game_id = %s",
        (game_id,),
    )
    row = cur.fetchone()
    if not row:
        return None, None
    blob, finished = row
    blob["finished"] = finished  # source of truth for finished is the column
    return blob, deserialize_state(blob)


def _save_game(conn, table: str, game_id: str, blob: dict):
    conn.execute(
        f"UPDATE {table} SET state = %s, finished = %s WHERE game_id = %s",
        (Jsonb(blob), bool(blob.get("finished", False)), game_id),
    )


def _insert_game(conn, table: str, blob: dict) -> str:
    cur = conn.execute(
        f"INSERT INTO {table} (game_id, state, finished) "
        f"VALUES (%s, %s, %s) RETURNING game_id::text",
        (str(uuid.uuid4()), Jsonb(blob), bool(blob.get("finished", False))),
    )
    return cur.fetchone()[0]


# ============================================================
# Routes
# ============================================================

@app.route("/local-headshots/<sport>/<path:filename>")
def local_canonical_headshot(sport: str, filename: str):
    """Serve canonical local headshots only in the local sports build."""
    if not LOCAL_SPORTS_ENABLED:
        abort(404)
    directory = LOCAL_HEADSHOT_DIRS.get(sport)
    if directory is None:
        abort(404)
    return send_from_directory(directory, filename)


@app.route("/file-storage/<bucket>/<sport>/<path:filename>")
def local_file_storage_object(bucket: str, sport: str, filename: str):
    """Serve deploy-ready local file-storage artifacts during offline playtesting."""
    if not LOCAL_SPORTS_ENABLED:
        abort(404)
    if bucket != FILE_STORAGE_HEADSHOT_BUCKET:
        abort(404)
    if sport not in {"baseball", "basketball", "hockey", "football"}:
        abort(404)
    directory = FILE_STORAGE_ROOT / bucket / sport
    if not (directory / filename).exists():
        abort(404)
    return send_from_directory(directory, filename)


@app.route("/")
def index():
    return render_template(
        "index.html",
        sport=None,
        sport_ready=False,
        cross_sports_online=CROSS_SPORTS_ONLINE,
        app_version=APP_VERSION,
        launch={},
        supabase_url=SUPABASE_URL or "",
    )


MODE_HUBS = {
    "manager": {"title": "Manager Mode", "mode": "bp", "description": "Build a solo lineup. Name a teammate before the clock expires and set your longest lineup."},
    "film": {"title": "Film Review", "mode": "fr", "description": "Solve the daily lineup. Identify the team and season linking each pair before three strikes end the review."},
    "division": {"title": "Division Rivalry", "mode": "mp", "description": "Head-to-head. Take turns adding teammates to one lineup. Team strikes block links. Win on time."},
    "playoffs": {"title": "Playoffs", "mode": "po", "description": "Head-to-head with powerups and a win condition. Complete yours first, or win on time."},
}

MODE_ALIASES = {
    "manager-mode": "manager",
    "film-review": "film",
    "division-rivalry": "division",
}

LAUNCH_COOKIE_PREFIX = "tt_launch_"
LAUNCH_COOKIE_KEYS = ("mode", "date", "archive", "game_id", "source", "unit")


def _set_launch_cookies(response, launch):
    for key in LAUNCH_COOKIE_KEYS:
        value = str(launch.get(key, "") or "")
        if value:
            response.set_cookie(f"{LAUNCH_COOKIE_PREFIX}{key}", value, max_age=60, path="/", samesite="Lax")
    return response


def _clear_launch_cookies(response):
    for key in LAUNCH_COOKIE_KEYS:
        response.delete_cookie(f"{LAUNCH_COOKIE_PREFIX}{key}", path="/", samesite="Lax")
    return response


def _launch_from_request():
    return {
        key: request.args.get(key, "") or request.cookies.get(f"{LAUNCH_COOKIE_PREFIX}{key}", "")
        for key in LAUNCH_COOKIE_KEYS
    }


def _with_current_query(path: str) -> str:
    if request.query_string:
        return path + "?" + request.query_string.decode("utf-8", errors="ignore")
    return path


@app.route("/manager-mode")
@app.route("/film-review")
@app.route("/division-rivalry")
def legacy_mode_hub():
    slug = request.path.strip("/")
    return redirect(_with_current_query(f"/{MODE_ALIASES[slug]}"))


@app.route("/manager")
@app.route("/film")
@app.route("/division")
@app.route("/playoffs")
def mode_hub():
    slug = request.path.strip("/")
    return render_template(
        "mode_hub.html",
        hub=MODE_HUBS[slug],
        slug=slug,
        sports=SPORT_HUBS,
        app_version=APP_VERSION,
        supabase_url=SUPABASE_URL or "",
    )


@app.route("/manager-mode/<sport_key>")
@app.route("/film-review/<sport_key>")
def legacy_direct_mode_sport(sport_key: str):
    slug = request.path.strip("/").split("/", 1)[0]
    return redirect(_with_current_query(f"/{MODE_ALIASES[slug]}/{sport_key}"))


@app.route("/manager/<sport_key>")
@app.route("/film/<sport_key>")
def direct_mode_sport(sport_key: str):
    slug = request.path.strip("/").split("/", 1)[0]
    if sport_key not in SPORT_HUBS:
        return "Sport not found", 404
    response = redirect(f"/{sport_key}")
    launch_unit = request.args.get("unit", "")
    if slug == "film" and sport_key == "football" and not launch_unit:
        launch_unit = "offense"
    return _set_launch_cookies(response, {
        "mode": MODE_HUBS[slug]["mode"],
        "date": request.args.get("date", ""),
        "archive": request.args.get("archive", ""),
        "game_id": "",
        "source": slug,
        "unit": launch_unit,
    })


SPORT_HUBS = {
    "baseball": {"name": "Baseball", "league": "MLB", "ready": True},
    "basketball": {"name": "Basketball", "league": "NBA", "ready": LOCAL_SPORTS_ENABLED or CROSS_SPORTS_FULLY_ONLINE},
    "hockey": {"name": "Hockey", "league": "NHL", "ready": LOCAL_SPORTS_ENABLED or CROSS_SPORTS_FULLY_ONLINE},
    "football": {"name": "Football", "league": "NFL", "ready": LOCAL_SPORTS_ENABLED or CROSS_SPORTS_FULLY_ONLINE},
}


@app.route("/baseball")
@app.route("/basketball")
@app.route("/hockey")
@app.route("/football")
def sport_hub():
    sport_key = request.path.strip("/")
    sport = SPORT_HUBS[sport_key]
    response = make_response(render_template(
        "index.html",
        sport={"key": sport_key, **sport},
        sport_ready=sport["ready"],
        cross_sports_online=CROSS_SPORTS_ONLINE,
        app_version=APP_VERSION,
        launch=_launch_from_request(),
        supabase_url=SUPABASE_URL or "",
    ))
    return _clear_launch_cookies(response)


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", support_email=SUPPORT_EMAIL, app_version=APP_VERSION)


@app.route("/terms")
def terms():
    return render_template("terms.html", support_email=SUPPORT_EMAIL, app_version=APP_VERSION)


@app.route("/contact")
def contact():
    return render_template("contact.html", support_email=SUPPORT_EMAIL, app_version=APP_VERSION)


def _headshot_audit_allowed() -> bool:
    """Keep mutation tooling local unless an explicit audit token is configured."""
    if HEADSHOT_AUDIT_TOKEN:
        provided = request.args.get("token", "") or request.headers.get("X-Headshot-Audit-Token", "")
        return hmac.compare_digest(provided, HEADSHOT_AUDIT_TOKEN)
    return request.remote_addr in {"127.0.0.1", "::1"}


@app.route("/headshot-audit")
def headshot_audit_page():
    if not _headshot_audit_allowed():
        abort(404)
    return render_template("headshot_audit.html", app_version=APP_VERSION)


@app.route("/api/headshot-audit/summary")
def headshot_audit_summary():
    if not _headshot_audit_allowed():
        abort(404)
    with db() as conn:
        rows = conn.execute(
            """SELECT sport_id, status, COUNT(*) FROM player_headshots
                 GROUP BY sport_id, status ORDER BY sport_id, status"""
        ).fetchall()
    return jsonify([{"sport": sport, "status": status, "count": count} for sport, status, count in rows])


@app.route("/api/headshot-audit/items")
def headshot_audit_items():
    if not _headshot_audit_allowed():
        abort(404)
    allowed = {"pending", "verified", "placeholder", "missing", "duplicate", "wrong_player", "bad_crop", "needs_review"}
    statuses = [item for item in request.args.get("status", "placeholder,missing,duplicate,wrong_player,bad_crop,needs_review").split(",") if item in allowed]
    sport = request.args.get("sport", "all")
    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except ValueError:
        offset = 0
    if not statuses:
        return jsonify({"items": [], "next_offset": None})
    sport_clause, sport_params = ("", []) if sport == "all" else (" AND h.sport_id=%s", [sport])
    with db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM (
                    SELECT h.sport_id, h.player_id, concat_ws(' ', p.name_first, p.name_last) AS display_name,
                           p.debut_year, p.final_year, h.source_url, h.fallback_url, h.provider, h.status, h.review_note
                      FROM player_headshots h JOIN players p ON h.sport_id='baseball' AND p.player_id=h.player_id
                     WHERE h.sport_id='baseball'
                    UNION ALL
                    SELECT h.sport_id, h.player_id, p.display_name, p.debut_year, p.final_year,
                           h.source_url, h.fallback_url, h.provider, h.status, h.review_note
                      FROM player_headshots h JOIN sport_players p ON p.sport_id=h.sport_id AND p.player_id=h.player_id
                     WHERE h.sport_id <> 'baseball'
                 ) h WHERE h.status = ANY(%s){sport_clause}
                 ORDER BY h.status, h.sport_id, h.final_year DESC, h.display_name
                 LIMIT 48 OFFSET %s""",
            (statuses, *sport_params, offset),
        ).fetchall()
    items = [{"sport": row[0], "player_id": row[1], "name": row[2], "debut_year": row[3],
              "final_year": row[4], "source_url": row[5], "fallback_url": row[6], "provider": row[7],
              "status": row[8], "review_note": row[9]} for row in rows]
    return jsonify({"items": items, "next_offset": offset + len(items) if len(items) == 48 else None})


@app.route("/api/headshot-audit/review", methods=["POST"])
def headshot_audit_review():
    if not _headshot_audit_allowed():
        abort(404)
    data = request.get_json(silent=True) or {}
    sport, player_id = (data.get("sport") or "").strip(), (data.get("player_id") or "").strip()
    status = (data.get("status") or "").strip()
    if not sport or not player_id or status not in {"verified", "placeholder", "wrong_player", "bad_crop", "needs_review"}:
        return jsonify({"error": "sport, player_id, and a valid review status are required"}), 400
    replacement = (data.get("replacement_url") or "").strip() or None
    note = (data.get("review_note") or "").strip()[:1000] or None
    with db() as conn:
        conn.execute(
            """UPDATE player_headshots
                  SET status=%s, source_url=COALESCE(%s, source_url), reviewed_at=now(), review_note=%s
                WHERE sport_id=%s AND player_id=%s""",
            (status, replacement, note, sport, player_id),
        )
    return jsonify({"ok": True})


@app.route("/reset-password")
def reset_password_page():
    return render_template(
        "reset_password.html",
        support_email=SUPPORT_EMAIL,
        app_version=APP_VERSION,
        supabase_url=SUPABASE_URL or "",
        supabase_anon_key=SUPABASE_ANON_KEY or "",
    )


@app.route("/api/profile/bootstrap", methods=["POST"])
def profile_bootstrap():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    requested_guest_id = (data.get("guest_id") or "").strip() or None
    if not _valid_uuid_text(requested_guest_id):
        requested_guest_id = None
    with db() as conn:
        session_guest_id = _session_guest_id(conn)
        if session_guest_id:
            profile = _guest_profile(conn, session_guest_id, authenticated=True)
        else:
            profile = _guest_profile(conn, requested_guest_id) if requested_guest_id else None
        if profile is None:
            profile = _create_guest(conn)
    return jsonify(profile)


@app.route("/api/profile/name", methods=["POST"])
def profile_name():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    display_name = " ".join((data.get("display_name") or "").strip().split())
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    if not display_name:
        return jsonify({"error": "display_name required"}), 400
    if len(display_name) > 24:
        return jsonify({"error": "display_name too long"}), 400
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM guests WHERE guest_id = %s",
            (guest_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "unknown guest_id"}), 404
        conn.execute(
            "UPDATE guests SET display_name = %s WHERE guest_id = %s",
            (display_name, guest_id),
        )
        conn.execute(
            "UPDATE users SET display_name = %s WHERE user_id = %s",
            (display_name, guest_id),
        )
        profile = _guest_profile(conn, guest_id)
    return jsonify(profile)


@app.route("/api/account/register", methods=["POST"])
def account_register():
    ensure_runtime_schema()
    if not _supabase_ready():
        return jsonify({"error": "Supabase Auth is not configured on the server."}), 500
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip() or None
    display_name = " ".join((data.get("display_name") or "").strip().split())
    username = " ".join((data.get("username") or "").strip().split())
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not username or len(username) < 3:
        return jsonify({"error": "username must be at least 3 characters"}), 400
    if len(username) > 24:
        return jsonify({"error": "username too long"}), 400
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 characters"}), 400
    if not email:
        return jsonify({"error": "email required"}), 400
    if not display_name:
        display_name = username
    if len(display_name) > 24:
        return jsonify({"error": "display_name too long"}), 400

    with db() as conn:
        taken = conn.execute(
            """SELECT user_id::text
                 FROM users
                WHERE (lower(username) = %s OR lower(email) = %s)
                  AND (user_id <> %s)
                LIMIT 1""",
            (username.lower(), email.lower(), guest_id or ""),
        ).fetchone()
        if taken:
            return jsonify({"error": "username or email already in use"}), 409

        create_res = _supabase_admin_create_user(
            email,
            password,
            username,
            display_name,
        )
        create_json = create_res.json()
        if create_res.status_code >= 400:
            message = create_json.get("msg") or create_json.get("error_description") or create_json.get("error") or "signup failed"
            return jsonify({"error": message}), create_res.status_code

        auth_user_id = _extract_auth_user_id(create_json) or _find_auth_user_id_by_email(conn, email)
        if not auth_user_id:
            return jsonify({
                "error": "account was created, but the auth identity could not be linked yet"
            }), 502

        gid = guest_id
        guest_row = None
        if gid:
            guest_row = conn.execute(
                "SELECT 1 FROM guests WHERE guest_id = %s",
                (gid,),
            ).fetchone()
        if not gid or not guest_row:
            gid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO guests (guest_id, display_name) VALUES (%s, %s)",
                (gid, display_name),
            )
        else:
            conn.execute(
                "UPDATE guests SET display_name = %s WHERE guest_id = %s",
                (display_name, gid),
            )

        existing_profile = conn.execute(
            "SELECT auth_user_id::text FROM users WHERE user_id = %s",
            (gid,),
        ).fetchone()
        if existing_profile and existing_profile[0] and existing_profile[0] != auth_user_id:
            return jsonify({"error": "this guest profile already has a Supabase account"}), 409
        if existing_profile:
            conn.execute(
                """UPDATE users
                      SET display_name = %s,
                          username = %s,
                          email = %s,
                          auth_user_id = %s,
                          password_hash = NULL,
                          password_salt = NULL
                    WHERE user_id = %s""",
                (display_name, username, email, auth_user_id, gid),
            )
        else:
            conn.execute(
                """INSERT INTO users (
                       user_id, display_name, username, email, auth_user_id,
                       password_hash, password_salt
                   ) VALUES (%s, %s, %s, %s, %s, NULL, NULL)""",
                (gid, display_name, username, email, auth_user_id),
            )
        signin_res = _supabase_signin(email, password)
        signin_json = signin_res.json()
        if signin_res.status_code >= 400:
            message = signin_json.get("msg") or signin_json.get("error_description") or signin_json.get("error") or "login failed"
            return jsonify({"error": message}), 403 if signin_res.status_code == 400 else signin_res.status_code
        profile = _guest_profile(conn, gid, authenticated=True)
        session_token = _create_app_session(conn, gid, auth_user_id)
        return _session_response(profile, session_token)


@app.route("/api/account/login", methods=["POST"])
def account_login():
    ensure_runtime_schema()
    if not _supabase_ready():
        return jsonify({"error": "Supabase Auth is not configured on the server."}), 500
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip().lower()
    password = data.get("password") or ""
    if not identifier or not password:
        return jsonify({"error": "identifier and password required"}), 400
    with db() as conn:
        row = conn.execute(
            """SELECT user_id::text, display_name, username, email, auth_user_id::text,
                      password_hash, password_salt
                 FROM users
                WHERE lower(username) = %s OR lower(email) = %s
                LIMIT 1""",
            (identifier, identifier),
        ).fetchone()
        if not row:
            return jsonify({"error": "account not found"}), 404
        user_id, display_name, username, email, auth_user_id, password_hash, password_salt = row
        if not email:
            return jsonify({"error": "this account is missing an email address"}), 409

        if not auth_user_id:
            if not password_hash or not password_salt or not _verify_password(password, password_hash, password_salt):
                return jsonify({"error": "this account has not been migrated to Supabase Auth yet"}), 409

            existing_auth_user_id = _find_auth_user_id_by_email(conn, email)
            if existing_auth_user_id:
                signin_res = _supabase_signin(email, password)
                signin_json = signin_res.json()
                if signin_res.status_code >= 400:
                    message = signin_json.get("msg") or signin_json.get("error_description") or signin_json.get("error") or "login failed"
                    return jsonify({"error": message}), 403 if signin_res.status_code == 400 else signin_res.status_code
                auth_user_id = _extract_auth_user_id(signin_json) or existing_auth_user_id
            else:
                create_res = _supabase_admin_create_user(
                    email,
                    password,
                    username or email.split("@", 1)[0],
                    display_name or username or email.split("@", 1)[0],
                )
                create_json = create_res.json()
                if create_res.status_code >= 400:
                    message = create_json.get("msg") or create_json.get("error_description") or create_json.get("error") or "could not migrate account"
                    return jsonify({"error": message}), 502
                auth_user_id = _extract_auth_user_id(create_json) or _find_auth_user_id_by_email(conn, email)
                if not auth_user_id:
                    return jsonify({"error": "could not finish Supabase Auth migration for this account"}), 502
                signin_res = _supabase_signin(email, password)
                signin_json = signin_res.json()
                if signin_res.status_code >= 400:
                    message = signin_json.get("msg") or signin_json.get("error_description") or signin_json.get("error") or "login failed"
                    return jsonify({"error": message}), 403 if signin_res.status_code == 400 else signin_res.status_code

            conn.execute(
                """UPDATE users
                      SET auth_user_id = %s,
                          password_hash = NULL,
                          password_salt = NULL
                    WHERE user_id = %s""",
                (auth_user_id, user_id),
            )
        else:
            signin_res = _supabase_signin(email, password)
            signin_json = signin_res.json()
            if signin_res.status_code >= 400:
                message = signin_json.get("msg") or signin_json.get("error_description") or signin_json.get("error") or "login failed"
                return jsonify({"error": message}), 403 if signin_res.status_code == 400 else signin_res.status_code

        user = signin_json.get("user") or {}
        signed_in_auth_user_id = user.get("id") or auth_user_id
        if signed_in_auth_user_id != auth_user_id:
            return jsonify({"error": "account identity mismatch"}), 409
        guest_row = conn.execute(
            "SELECT 1 FROM guests WHERE guest_id = %s",
            (user_id,),
        ).fetchone()
        if not guest_row:
            conn.execute(
                "INSERT INTO guests (guest_id, display_name) VALUES (%s, %s)",
                (user_id, display_name or username),
            )
        session_token = _create_app_session(conn, user_id, auth_user_id)
        profile = _guest_profile(conn, user_id, authenticated=True)
    return _session_response(profile, session_token)


@app.route("/api/account/logout", methods=["POST"])
def account_logout():
    ensure_runtime_schema()
    with db() as conn:
        _clear_app_session(conn)
    return _clear_session_response({"status": "signed_out"})


@app.route("/api/account/reset_password", methods=["POST"])
def account_reset_password():
    ensure_runtime_schema()
    if not _supabase_ready():
        return jsonify({"error": "Supabase Auth is not configured on the server."}), 500
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip().lower()
    if not identifier:
        return jsonify({"error": "identifier required"}), 400
    with db() as conn:
        row = conn.execute(
            """SELECT email
                 FROM users
                WHERE lower(username) = %s OR lower(email) = %s
                LIMIT 1""",
            (identifier, identifier),
        ).fetchone()
    if not row or not row[0]:
        return jsonify({"error": "account not found"}), 404
    reset_res = _supabase_reset_password(
        row[0],
        _public_app_url() + "/reset-password",
    )
    if reset_res.status_code >= 400:
        payload = reset_res.json()
        message = payload.get("msg") or payload.get("error_description") or payload.get("error") or "reset request failed"
        return jsonify({"error": message}), reset_res.status_code
    return jsonify({"status": "sent"})


def _forfeit_active_dr_games(conn, guest_id: str):
    rows = conn.execute(
        """SELECT game_id::text, state, finished
             FROM dr_games
            WHERE NOT finished
              AND ((state->>'p1_guest_id') = %s OR (state->>'p2_guest_id') = %s)""",
        (guest_id, guest_id),
    ).fetchall()
    for game_id, blob, finished in rows:
        blob["finished"] = finished
        if blob.get("finished"):
            continue
        blob["finished"] = True
        blob["winner"] = (
            blob.get("p2") if guest_id == blob.get("p1_guest_id")
            else blob.get("p1")
        )
        blob["last_move"] = {"outcome": "forfeit"}
        _save_dr_result(conn, blob, game_id)
        _save_game(conn, "dr_games", game_id, blob)


@app.route("/api/account/delete", methods=["POST"])
def account_delete():
    ensure_runtime_schema()
    if not _supabase_ready():
        return jsonify({"error": "Supabase Auth is not configured on the server."}), 500
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    with db() as conn:
        guest_id = _session_guest_id(conn)
        if not guest_id or not password:
            return jsonify({"error": "signed-in account and password required"}), 400
        row = conn.execute(
            """SELECT email, auth_user_id::text
                 FROM users
                WHERE user_id = %s""",
            (guest_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "account not found"}), 404
        email, auth_user_id = row
        if not email or not auth_user_id:
            return jsonify({"error": "this account is not using Supabase Auth"}), 409
        signin_res = _supabase_signin(email, password)
        if signin_res.status_code >= 400:
            return jsonify({"error": "incorrect password"}), 403
        delete_res = _supabase_admin_delete_user(auth_user_id)
        if delete_res.status_code >= 400:
            return jsonify({"error": "failed to delete Supabase Auth user"}), 502

        _forfeit_active_dr_games(conn, guest_id)
        _forfeit_active_po_games(conn, guest_id)
        conn.execute("DELETE FROM dr_queue WHERE guest_id = %s", (guest_id,))
        conn.execute("DELETE FROM dr_invites WHERE host_guest_id = %s", (guest_id,))
        conn.execute("DELETE FROM dr_rematches WHERE requester_guest_id = %s", (guest_id,))
        conn.execute("DELETE FROM dr_postgame_exits WHERE guest_id = %s", (guest_id,))
        conn.execute("DELETE FROM po_queue WHERE guest_id = %s", (guest_id,))
        conn.execute("DELETE FROM po_invites WHERE host_guest_id = %s", (guest_id,))
        conn.execute("DELETE FROM po_rematches WHERE requester_guest_id = %s", (guest_id,))
        conn.execute("DELETE FROM po_postgame_exits WHERE guest_id = %s", (guest_id,))
        _clear_app_session(conn)
        conn.execute("DELETE FROM users WHERE user_id = %s", (guest_id,))
        conn.execute("DELETE FROM guests WHERE guest_id = %s", (guest_id,))

    return _clear_session_response({"status": "deleted"})


@app.route("/api/friends/list", methods=["POST"])
def friends_list():
    ensure_runtime_schema()
    with db() as conn:
        guest_id = _session_account_guest_id(conn)
        if not guest_id:
            return jsonify({"error": "account login required"}), 403
        payload = _friends_payload(conn, guest_id)
        if payload.get("error"):
            return jsonify(payload), 403
        return jsonify(payload)


@app.route("/api/friends/request", methods=["POST"])
def friends_request():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    target = (data.get("target") or "").strip().lower()
    if not target:
        return jsonify({"error": "target required"}), 400
    with db() as conn:
        guest_id = _session_account_guest_id(conn)
        if not guest_id:
            return jsonify({"error": "account login required"}), 403
        me = _require_user(conn, guest_id)
        if not me:
            return jsonify({"error": "account required"}), 403
        target_row = conn.execute(
            """SELECT user_id::text, username
                 FROM users
                WHERE lower(username) = %s OR lower(email) = %s
                LIMIT 1""",
            (target, target),
        ).fetchone()
        if not target_row:
            return jsonify({"error": "account not found"}), 404
        target_id, _target_username = target_row
        if target_id == guest_id:
            return jsonify({"error": "cannot add yourself"}), 400
        a, b = _friendship_pair(guest_id, target_id)
        already = conn.execute(
            "SELECT 1 FROM friendships WHERE user_a_id = %s AND user_b_id = %s",
            (a, b),
        ).fetchone()
        if already:
            return jsonify({"error": "already friends"}), 409
        pending = conn.execute(
            """SELECT 1 FROM friend_requests
                 WHERE ((sender_user_id = %s AND recipient_user_id = %s)
                     OR (sender_user_id = %s AND recipient_user_id = %s))
                   AND status = 'pending'""",
            (guest_id, target_id, target_id, guest_id),
        ).fetchone()
        if pending:
            return jsonify({"error": "friend request already pending"}), 409
        conn.execute(
            """INSERT INTO friend_requests (sender_user_id, recipient_user_id)
               VALUES (%s, %s)""",
            (guest_id, target_id),
        )
        return jsonify(_friends_payload(conn, guest_id))


@app.route("/api/friends/respond", methods=["POST"])
def friends_respond():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    request_id = (data.get("request_id") or "").strip()
    accept = bool(data.get("accept"))
    if not request_id:
        return jsonify({"error": "request_id required"}), 400
    with db() as conn:
        guest_id = _session_account_guest_id(conn)
        if not guest_id:
            return jsonify({"error": "account login required"}), 403
        me = _require_user(conn, guest_id)
        if not me:
            return jsonify({"error": "account required"}), 403
        row = conn.execute(
            """SELECT sender_user_id::text, recipient_user_id::text, status
                 FROM friend_requests
                WHERE request_id = %s""",
            (request_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "request not found"}), 404
        sender_id, recipient_id, status = row
        if recipient_id != guest_id:
            return jsonify({"error": "unauthorized"}), 403
        if status != "pending":
            return jsonify({"error": "request already handled"}), 409
        new_status = "accepted" if accept else "rejected"
        conn.execute(
            "UPDATE friend_requests SET status = %s, responded_at = now() WHERE request_id = %s",
            (new_status, request_id),
        )
        if accept:
            a, b = _friendship_pair(sender_id, recipient_id)
            conn.execute(
                """INSERT INTO friendships (user_a_id, user_b_id)
                   VALUES (%s, %s)
                   ON CONFLICT DO NOTHING""",
                (a, b),
            )
        return jsonify(_friends_payload(conn, guest_id))


@app.route("/api/friends/challenge", methods=["POST"])
def friends_challenge():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    friend_user_id = (data.get("friend_user_id") or "").strip()
    if not friend_user_id:
        return jsonify({"error": "friend_user_id required"}), 400
    with db() as conn:
        guest_id = _session_account_guest_id(conn)
        if not guest_id:
            return jsonify({"error": "account login required"}), 403
        me = _require_user(conn, guest_id)
        other = _require_user(conn, friend_user_id)
        if not me or not other:
            return jsonify({"error": "account required"}), 403
        a, b = _friendship_pair(guest_id, friend_user_id)
        friends = conn.execute(
            "SELECT 1 FROM friendships WHERE user_a_id = %s AND user_b_id = %s",
            (a, b),
        ).fetchone()
        if not friends:
            return jsonify({"error": "friendship required"}), 403
        pending = conn.execute(
            """SELECT 1 FROM dr_friend_challenges
                 WHERE ((sender_user_id = %s AND recipient_user_id = %s)
                     OR (sender_user_id = %s AND recipient_user_id = %s))
                   AND status = 'pending'""",
            (guest_id, friend_user_id, friend_user_id, guest_id),
        ).fetchone()
        if pending:
            return jsonify({"error": "challenge already pending"}), 409
        conn.execute("DELETE FROM dr_queue WHERE guest_id IN (%s, %s)", (guest_id, friend_user_id))
        conn.execute(
            """INSERT INTO dr_friend_challenges (
                   sender_user_id, recipient_user_id, sender_name, recipient_name
               ) VALUES (%s, %s, %s, %s)""",
            (guest_id, friend_user_id, _guest_label(conn, guest_id), _guest_label(conn, friend_user_id)),
        )
        return jsonify(_friends_payload(conn, guest_id))


@app.route("/api/friends/challenge_respond", methods=["POST"])
def friends_challenge_respond():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    challenge_id = (data.get("challenge_id") or "").strip()
    accept = bool(data.get("accept"))
    if not challenge_id:
        return jsonify({"error": "challenge_id required"}), 400
    with db() as conn:
        guest_id = _session_account_guest_id(conn)
        if not guest_id:
            return jsonify({"error": "account login required"}), 403
        me = _require_user(conn, guest_id)
        if not me:
            return jsonify({"error": "account required"}), 403
        row = conn.execute(
            """SELECT sender_user_id::text, recipient_user_id::text, sender_name, recipient_name, status
                 FROM dr_friend_challenges
                WHERE challenge_id = %s""",
            (challenge_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "challenge not found"}), 404
        sender_id, recipient_id, sender_name, recipient_name, status = row
        if recipient_id != guest_id:
            return jsonify({"error": "unauthorized"}), 403
        if status != "pending":
            return jsonify({"error": "challenge already handled"}), 409
        if not accept:
            conn.execute(
                "UPDATE dr_friend_challenges SET status = 'declined', responded_at = now() WHERE challenge_id = %s",
                (challenge_id,),
            )
            return jsonify(_friends_payload(conn, guest_id))
        gid, blob, state = _dr_create_online_game(conn, sender_id, sender_name, recipient_id, recipient_name)
        conn.execute(
            """UPDATE dr_friend_challenges
                  SET status = 'accepted',
                      game_id = %s,
                      responded_at = now()
                WHERE challenge_id = %s""",
            (gid, challenge_id),
        )
        blob["viewer_guest_id"] = guest_id
        return jsonify({
            "status": "matched",
            "game": dr_state_dict(gid, blob, state, conn=conn),
        })


@app.route("/api/friends/request_cancel", methods=["POST"])
def friends_request_cancel():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    request_id = (data.get("request_id") or "").strip()
    if not request_id:
        return jsonify({"error": "request_id required"}), 400
    with db() as conn:
        guest_id = _session_account_guest_id(conn)
        if not guest_id:
            return jsonify({"error": "account login required"}), 403
        me = _require_user(conn, guest_id)
        if not me:
            return jsonify({"error": "account required"}), 403
        conn.execute(
            """UPDATE friend_requests
                  SET status = 'cancelled', responded_at = now()
                WHERE request_id = %s
                  AND sender_user_id = %s
                  AND status = 'pending'""",
            (request_id, guest_id),
        )
        return jsonify(_friends_payload(conn, guest_id))


@app.route("/api/friends/challenge_cancel", methods=["POST"])
def friends_challenge_cancel():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    challenge_id = (data.get("challenge_id") or "").strip()
    if not challenge_id:
        return jsonify({"error": "challenge_id required"}), 400
    with db() as conn:
        guest_id = _session_account_guest_id(conn)
        if not guest_id:
            return jsonify({"error": "account login required"}), 403
        me = _require_user(conn, guest_id)
        if not me:
            return jsonify({"error": "account required"}), 403
        conn.execute(
            """UPDATE dr_friend_challenges
                  SET status = 'cancelled', responded_at = now()
                WHERE challenge_id = %s
                  AND sender_user_id = %s
                  AND status = 'pending'""",
            (challenge_id, guest_id),
        )
        return jsonify(_friends_payload(conn, guest_id))


@app.route("/api/bp/leaderboard")
def bp_leaderboard():
    ensure_runtime_schema()
    with db() as conn:
        return jsonify(_bp_daily_leaderboard(conn))


# ----- Local cross-sport Batting Practice -----

def _local_sport_conn() -> sqlite3.Connection:
    if not LOCAL_SPORTS_ENABLED or not LOCAL_SPORT_DATA.exists():
        raise RuntimeError("Local sport playtesting is not enabled.")
    return sqlite3.connect(LOCAL_SPORT_DATA)


def _local_sport_cards(conn: sqlite3.Connection, sport: str, player_ids: list[str]) -> dict[str, dict]:
    out = {}
    for player_id in player_ids:
        row = conn.execute(
            "SELECT external_id, debut_year, final_year, first_name, last_name, primary_pos FROM sport_players WHERE sport_id = ? AND player_id = ?",
            (sport, player_id),
        ).fetchone()
        appearances = conn.execute(
            """SELECT DISTINCT a.team_id, COALESCE(t.franchise_id, a.team_id), t.name, a.season, COALESCE(a.games_total, 1)
                 FROM sport_appearances a
                 JOIN sport_teams t ON t.sport_id = a.sport_id AND t.team_id = a.team_id AND t.season = a.season
                WHERE a.sport_id = ? AND a.player_id = ?
                ORDER BY a.season, t.name""",
            (sport, player_id),
        ).fetchall()
        stint_rows = conn.execute(
            """SELECT team_id, season, first_label, last_label
                 FROM sport_player_stints
                WHERE sport_id = ? AND player_id = ?""",
            (sport, player_id),
        ).fetchall()
        stint_labels_by_team_season = {
            (team_id, int(season)): (first_label, last_label)
            for team_id, season, first_label, last_label in stint_rows
        }
        spans_by_team = {}
        games_by_team = {}
        teams_by_season: dict[int, set[tuple]] = {}
        games_by_team_season = {}
        seen_team_seasons = set()
        for team_id, franchise_id, team, season, games_total in appearances:
            if sport == "hockey":
                team = NHL_TEAM_NAMES.get(team, team)
            key = (team_id, team, season)
            if key in seen_team_seasons:
                continue
            seen_team_seasons.add(key)
            team_key = (team_id, franchise_id or team_id, team)
            season = int(season)
            teams_by_season.setdefault(season, set()).add(team_key)
            games_by_team_season[(team_key, season)] = games_by_team_season.get((team_key, season), 0) + int(games_total or 0)
            games_by_team[team_key] = games_by_team.get(team_key, 0) + int(games_total or 0)
            spans = spans_by_team.setdefault(team_key, [])
            if spans and spans[-1][1] == season - 1:
                spans[-1][1] = season
            else:
                spans.append([season, season])
        overlap_years_by_team = _team_overlap_calendar_years(spans_by_team, teams_by_season, games_by_team_season)
        teams = []
        team_stints = []
        seen_team_labels = set()
        for (team_id, franchise_id, team), ranges in spans_by_team.items():
            team_key = (team_id, franchise_id, team)
            overlap_years = overlap_years_by_team.get(team_key, {})
            years = ", ".join(
                _sport_card_display_stint_label(
                    sport,
                    start,
                    end,
                    overlap_years,
                    stint_labels_by_team_season.get((team_id, start), (None, None))[0],
                    stint_labels_by_team_season.get((team_id, end), (None, None))[1],
                )
                for start, end in ranges
            )
            label = f"{team} {years}"
            if label in seen_team_labels:
                continue
            seen_team_labels.add(label)
            teams.append(label)
            team_stints.append({
                "team_id": team_id,
                "color_team_id": franchise_id or team_id,
                "team_name": team,
                "label": label,
                "start": ranges[0][0],
                "end": ranges[-1][1],
                "seasons": sum(end - start + 1 for start, end in ranges),
                "games": games_by_team.get((team_id, franchise_id, team), 0),
            })
        external_id = row[0] if row else None
        image_row = conn.execute("SELECT local_path FROM local_player_images WHERE sport_id = ? AND player_id = ?", (sport, player_id)).fetchone() if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='local_player_images'").fetchone() else None
        if sport == "basketball" and external_id:
            headshot = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{external_id}.png"
        elif sport == "hockey" and external_id:
            headshot = f"https://assets.nhle.com/mugs/nhl/latest/{external_id}.png"
        elif sport == "football" and image_row:
            headshot = f"/api/local/headshot/{sport}/{player_id}"
        else:
            headshot = None
        if image_row and sport != "football":
            headshot = f"/api/local/headshot/{sport}/{player_id}"
        out[player_id] = {
            "mlbam_id": None, "headshot_url": headshot,
            "debut_year": max(row[1] or 2000, 2000) if row else None, "final_year": row[2] if row else None,
            "name_first": row[3] if row else None, "name_last": row[4] if row else None,
            "display_name": _sport_display_name(sport, player_id, row[3] if row else None, row[4] if row else None),
            "primary_pos": ({"R": "RW", "L": "LW", "D": "D"}.get(row[5], row[5]) if row else None), "teams": teams,
            "team_stints": team_stints,
        }
    return out


def _is_cross_sport(sport: str) -> bool:
    return sport == "baseball" or sport in LOCAL_SPORT_SEEDS


def _engine_sport(sport: str) -> str | None:
    return None if sport == "baseball" else sport


def _sport_team_name(conn, sport: str, team_id: str, season: int) -> str:
    if sport == "baseball":
        ensure_static_caches()
        return TEAM_NAME.get((team_id, season), team_id)
    cache_key = (sport, team_id, season)
    cached = SPORT_TEAM_NAME_CACHE.get(cache_key)
    if cached is not None:
        return cached
    row = conn.execute(
        """SELECT name FROM sport_teams
             WHERE sport_id = %s AND team_id = %s AND season = %s""",
        (sport, team_id, season),
    ).fetchone()
    name = row[0] if row else team_id
    if sport == "hockey":
        name = NHL_TEAM_NAMES.get(name, NHL_TEAM_NAMES.get(team_id, name))
    name = _canonical_sport_team_name(sport, team_id, name)
    SPORT_TEAM_NAME_CACHE[cache_key] = name
    return name


def _canonical_sport_team_name(sport: str, team_id: str | None, name: str | None) -> str:
    raw_name = name or team_id or ""
    clean_team_id = str(team_id or "").split(":")[-1]
    aliases = SPORT_TEAM_CANONICAL_NAMES.get(sport, {})
    if raw_name in aliases:
        return aliases[raw_name]
    if clean_team_id in aliases:
        return aliases[clean_team_id]
    if sport == "hockey":
        nhl_name = NHL_TEAM_NAMES.get(raw_name, NHL_TEAM_NAMES.get(clean_team_id))
        if nhl_name:
            return aliases.get(nhl_name, nhl_name)
    return raw_name


def _cross_year_season_sports(sport: str) -> bool:
    return sport in {"basketball", "football", "hockey"}


def _sport_season_label(sport: str, season: int | str | None) -> str:
    if season is None:
        return ""
    try:
        year = int(season)
    except (TypeError, ValueError):
        return str(season)
    if _cross_year_season_sports(sport):
        return f"{year}-{str(year + 1)[-2:]}"
    return str(year)


SPORT_DISPLAY_NAME_OVERRIDES = {
    ("football", "nfl:00-0033077"): "Dak Prescott",
    ("football", "nfl:00-0034367"): "Nyheim Hines",
}


def _sport_display_name(sport: str, player_id: str | None, first: str | None = None,
                        last: str | None = None, fallback: str | None = None) -> str:
    if (sport, player_id or "") in SPORT_DISPLAY_NAME_OVERRIDES:
        return SPORT_DISPLAY_NAME_OVERRIDES[(sport, player_id or "")]
    name = " ".join(part for part in (first, last) if part).strip()
    # League display names preserve common names and initials: Geno Smith,
    # C.J. Mosley, A.J. Brown, and similar cases are not legal given names.
    return fallback or name or (player_id or "")


def _sport_team_span_label(sport: str, start: int, end: int) -> str:
    if start == end:
        return _sport_season_label(sport, start)
    if _cross_year_season_sports(sport):
        return f"{start}-{str(end + 1)[-2:]}"
    return f"{start}-{end}"


def _sport_card_stint_label(sport: str, start: int, end: int) -> str:
    if _cross_year_season_sports(sport):
        display_start = start + 1 if start == end else start
        display_end = end + 1
        return _format_calendar_year_span(display_start, display_end)
    if start == end:
        return str(start)
    return _sport_team_span_label(sport, start, end)


def _range_contains_season(ranges: list[list[int]], season: int) -> bool:
    return any(start <= season <= end for start, end in ranges)


def _team_overlap_calendar_years(spans_by_team: dict, teams_by_season: dict[int, set],
                                 games_by_team_season: dict[tuple, int]) -> dict[tuple, dict[int, int]]:
    years_by_team: dict[tuple, dict[int, int]] = {}
    for season, team_keys in teams_by_season.items():
        if len({team_key[-1] for team_key in team_keys}) <= 1:
            continue
        max_games = max(games_by_team_season.get((team_key, season), 0) for team_key in team_keys)
        for team_key in team_keys:
            ranges = spans_by_team.get(team_key, [])
            if _range_contains_season(ranges, season - 1):
                display_year = season
            elif _range_contains_season(ranges, season + 1):
                display_year = season + 1
            else:
                display_year = season if games_by_team_season.get((team_key, season), 0) >= max_games else season + 1
            years_by_team.setdefault(team_key, {})[season] = display_year
    return years_by_team


def _format_calendar_year_span(start: int, end: int) -> str:
    if start == end:
        return str(start)
    return f"{start}-{end}"


def _calendar_year_from_label(label: str | None) -> int | None:
    if not label:
        return None
    match = re.match(r"^\s*(\d{4})", str(label))
    return int(match.group(1)) if match else None


def _sport_card_display_stint_label(sport: str, start: int, end: int,
                                    overlap_years: dict[int, int] | None = None,
                                    first_label: str | None = None,
                                    last_label: str | None = None) -> str:
    if _cross_year_season_sports(sport):
        first_year = _calendar_year_from_label(first_label)
        last_year = _calendar_year_from_label(last_label)
        if first_year is not None or last_year is not None:
            display_start = first_year if first_year is not None else start
            display_end = last_year if last_year is not None else end + 1
            return _format_calendar_year_span(display_start, display_end)
    if not _cross_year_season_sports(sport) or not overlap_years:
        return _sport_card_stint_label(sport, start, end)
    display_start = overlap_years.get(start, start)
    display_end = overlap_years.get(end, end + 1)
    if start not in overlap_years and end not in overlap_years:
        return _sport_card_stint_label(sport, start, end)
    return _format_calendar_year_span(display_start, display_end)


def _parse_cross_year_season_guess(year_text: str) -> tuple[int | None, int | None]:
    raw = (year_text or "").strip()
    parts = re.findall(r"\d{1,4}", raw)
    if len(parts) != 2:
        return None, None
    try:
        start_raw, end_raw = int(parts[0]), int(parts[1])
    except ValueError:
        return None, None
    start = start_raw if start_raw >= 100 else (2000 + start_raw if start_raw <= 69 else 1900 + start_raw)
    end = end_raw if end_raw >= 100 else (2000 + end_raw if end_raw <= 69 else 1900 + end_raw)
    if end < start:
        end += 100
    if end != start + 1:
        return None, None
    return start, end


def _sport_fr_year_matches(sport: str, season: int, year_text: str | int | None) -> bool:
    if year_text is None:
        return False
    if _cross_year_season_sports(sport):
        start, end = _parse_cross_year_season_guess(str(year_text))
        return start == season and end == season + 1
    try:
        return int(str(year_text).strip()) == season
    except (TypeError, ValueError):
        return False


def _sport_link_allowed(conn, sport: str, first: str, second: str, team_id: str, season: int) -> bool:
    excluded = conn.execute(
        """SELECT 1 FROM sport_teammate_exclusions
             WHERE sport_id=%s AND team_id=%s AND season=%s
               AND ((player_a_id=%s AND player_b_id=%s)
                 OR (player_a_id=%s AND player_b_id=%s))""",
        (sport, team_id, season, first, second, second, first),
    ).fetchone()
    if excluded is not None:
        return False
    strict_game_coverage = conn.execute(
        """SELECT 1 FROM sport_teammate_stint_coverage
            WHERE sport_id=%s AND season=%s AND strict <> 0
              AND coverage_type='game_boxscore'""",
        (sport, season),
    ).fetchone()
    if strict_game_coverage is not None:
        player_a_id, player_b_id = sorted((first, second))
        if conn.execute(
            """SELECT 1 FROM sport_teammates
                WHERE sport_id=%s
                  AND player_a_id=%s
                  AND player_b_id=%s
                  AND team_id=%s
                  AND season=%s
                LIMIT 1""",
            (sport, player_a_id, player_b_id, team_id, season),
        ).fetchone() is not None:
            return True
        return conn.execute(
            """SELECT 1
                 FROM sport_live_player_games a
                 JOIN sport_live_player_games b
                   ON b.sport_id = a.sport_id
                  AND b.game_id = a.game_id
                  AND b.team_id = a.team_id
                WHERE a.sport_id=%s
                  AND a.player_id=%s
                  AND b.player_id=%s
                  AND a.team_id=%s
                  AND a.season=%s
                LIMIT 1""",
            (sport, first, second, team_id, season),
        ).fetchone() is not None
    strict = conn.execute(
        """SELECT 1 FROM sport_teammate_stint_coverage
            WHERE sport_id=%s AND season=%s AND strict <> 0""",
        (sport, season),
    ).fetchone()
    if strict is None:
        return True
    overlap = conn.execute(
        """SELECT 1
             FROM sport_player_stints a
             JOIN sport_player_stints b
               ON b.sport_id = a.sport_id
              AND b.team_id = a.team_id
              AND b.season = a.season
            WHERE a.sport_id=%s
              AND a.player_id=%s
              AND b.player_id=%s
              AND a.team_id=%s
              AND a.season=%s
              AND a.first_unit <= b.last_unit
              AND b.first_unit <= a.last_unit
            LIMIT 1""",
        (sport, first, second, team_id, season),
    ).fetchone()
    return overlap is not None


def _sport_cards(conn, sport: str, player_ids: list[str]) -> dict[str, dict]:
    """Hydrate cross-sport cards from the compact Postgres catalog.

    Images are source URLs only. Keeping the original image binaries out of
    Supabase is what lets the entire game catalog fit on the free database.
    """
    if not player_ids:
        return {}
    if sport == "baseball":
        return _hydrate_player_cards(conn, player_ids)
    out = {}
    missing = []
    for player_id in player_ids:
        cached = SPORT_CARD_CACHE.get((sport, player_id))
        if cached is None:
            missing.append(player_id)
        else:
            out[player_id] = cached
    if not missing:
        return out
    rows = conn.execute(
        """SELECT player_id, external_id, debut_year, final_year, first_name,
                  last_name, primary_pos, display_name
             FROM sport_players
            WHERE sport_id = %s AND player_id = ANY(%s)""",
        (sport, missing),
    ).fetchall()
    images = dict(conn.execute(
        """SELECT player_id, source_url FROM sport_player_images
             WHERE sport_id = %s AND player_id = ANY(%s)""",
        (sport, missing),
    ).fetchall())
    registry_urls = _headshot_registry_urls(conn, sport, missing)
    appearances = conn.execute(
        """SELECT DISTINCT a.player_id, a.team_id, COALESCE(t.franchise_id, a.team_id), t.name, a.season, COALESCE(a.games_total, 1)
             FROM sport_appearances a
             JOIN sport_teams t ON t.sport_id=a.sport_id
               AND t.team_id=a.team_id AND t.season=a.season
            WHERE a.sport_id=%s AND a.player_id = ANY(%s)
            ORDER BY a.player_id, a.season, t.name""",
        (sport, missing),
    ).fetchall()
    stint_rows = conn.execute(
        """SELECT player_id, team_id, season, first_label, last_label
             FROM sport_player_stints
            WHERE sport_id=%s AND player_id = ANY(%s)""",
        (sport, missing),
    ).fetchall()
    stint_labels_by_player_team_season = {
        (player_id, team_id, int(season)): (first_label, last_label)
        for player_id, team_id, season, first_label, last_label in stint_rows
    }
    teams_by_player: dict[str, dict[tuple[str, str, str], list[list[int]]]] = {}
    teams_by_player_season: dict[tuple[str, int], set[tuple]] = {}
    games_by_player_team_season = {}
    games_by_player_team: dict[tuple[str, str, str, str], int] = {}
    seen_player_team_seasons = set()
    for player_id, team_id, franchise_id, team, season, games_total in appearances:
        if sport == "hockey":
            team = NHL_TEAM_NAMES.get(team, team)
        key = (player_id, team_id, team, season)
        if key in seen_player_team_seasons:
            continue
        seen_player_team_seasons.add(key)
        team_key = (team_id, franchise_id or team_id, team)
        season = int(season)
        teams_by_player_season.setdefault((player_id, season), set()).add(team_key)
        games_by_player_team_season[(player_id, team_key, season)] = (
            games_by_player_team_season.get((player_id, team_key, season), 0) + int(games_total or 0)
        )
        games_key = (player_id, team_id, franchise_id or team_id, team)
        games_by_player_team[games_key] = games_by_player_team.get(games_key, 0) + int(games_total or 0)
        ranges = teams_by_player.setdefault(player_id, {}).setdefault(team_key, [])
        # A player returning after an injury is still one tenure line, with
        # each actual year range preserved (Chicago Bulls 2008-11, 2013-15).
        if ranges and ranges[-1][1] == season - 1:
            ranges[-1][1] = season
        else:
            ranges.append([season, season])
    for player_id, external_id, debut, final, first, last, primary_pos, canonical_name in rows:
        teams = []
        team_stints = []
        player_spans = teams_by_player.get(player_id, {})
        player_teams_by_season = {
            season: season_teams
            for (season_player_id, season), season_teams in teams_by_player_season.items()
            if season_player_id == player_id
        }
        player_games_by_team_season = {
            (team_key, season): games
            for (games_player_id, team_key, season), games in games_by_player_team_season.items()
            if games_player_id == player_id
        }
        overlap_years_by_team = _team_overlap_calendar_years(
            player_spans,
            player_teams_by_season,
            player_games_by_team_season,
        )
        for (team_id, franchise_id, team), ranges in teams_by_player.get(player_id, {}).items():
            team_key = (team_id, franchise_id, team)
            overlap_years = overlap_years_by_team.get(team_key, {})
            years = ", ".join(
                _sport_card_display_stint_label(
                    sport,
                    a,
                    b,
                    overlap_years,
                    stint_labels_by_player_team_season.get((player_id, team_id, a), (None, None))[0],
                    stint_labels_by_player_team_season.get((player_id, team_id, b), (None, None))[1],
                )
                for a, b in ranges
            )
            label = f"{team} {years}"
            if label in teams:
                continue
            teams.append(label)
            team_stints.append({
                "team_id": team_id, "team_name": team,
                "color_team_id": franchise_id or team_id,
                "label": label,
                "start": ranges[0][0], "end": ranges[-1][1],
                "seasons": sum(end - start + 1 for start, end in ranges),
                "games": games_by_player_team.get((player_id, team_id, franchise_id or team_id, team), 0),
            })
        # NBA's official image CDN covers many older players omitted by the
        # source-image catalog. Broken URLs still fall through to the UI's
        # existing placeholder without affecting gameplay.
        headshot_url = registry_urls.get(player_id) if player_id in registry_urls else images.get(player_id)
        if sport != "football" and not headshot_url and player_id not in registry_urls:
            headshot_url = _official_sport_headshot_url(sport, external_id)
        card = {
            "mlbam_id": None,
            "headshot_url": headshot_url,
            "debut_year": max(debut or 2000, 2000),
            "final_year": final,
            "name_first": first,
            "name_last": last,
            "display_name": _sport_display_name(sport, player_id, first, last, canonical_name),
            "primary_pos": primary_pos,
            "teams": teams,
            "team_stints": team_stints,
        }
        SPORT_CARD_CACHE[(sport, player_id)] = card
        out[player_id] = card
    return out


def _sport_chain_dict(conn, sport: str, state: GameState) -> list[dict]:
    if sport == "baseball":
        return chain_dict(state, cards=_hydrate_player_cards(conn, list(state.chain)))
    cards = _sport_cards(conn, sport, list(state.chain))
    chain = []
    for index, (player_id, name) in enumerate(zip(state.chain, state.chain_names)):
        card = cards.get(player_id, {})
        display_name = _sport_display_name(sport, player_id, card.get("name_first"), card.get("name_last"), name)
        chain.append({
            "id": player_id, "name": display_name, **card,
            "shared_with_prev": [
                {"team_id": team, "season": season,
                 "team_name": _sport_team_name(conn, sport, team, season),
                 "season_label": _sport_season_label(sport, season)}
                for team, season in state.chain_shared_with_prev[index]
            ],
        })
    return chain


def _sport_strikes_dict(conn, sport: str, state: GameState) -> list[dict]:
    if sport == "baseball":
        return strikes_dict(state)
    return [
        {"team_id": team, "season": season, "count": count,
         "team_name": _sport_team_name(conn, sport, team, season),
         "season_label": _sport_season_label(sport, season)}
        for (team, season), count in state.strikes.items()
    ]


@app.route("/api/local/headshot/<sport>/<path:player_id>")
def local_headshot(sport: str, player_id: str):
    from flask import send_file
    with _local_sport_conn() as conn:
        row = conn.execute("SELECT local_path FROM local_player_images WHERE sport_id = ? AND player_id = ?", (sport, player_id)).fetchone()
    if not row or not Path(row[0]).exists():
        return "", 404
    response = send_file(row[0], conditional=True)
    response.headers["Cache-Control"] = "no-store"
    return response


def _local_bp_state(game_id: str, game: dict) -> dict:
    state, sport = game["state"], game["sport"]
    with _local_sport_conn() as conn:
        cards = _local_sport_cards(conn, sport, state.chain)
        team_names = {
            (team, season): (NHL_TEAM_NAMES.get(team, team) if sport == "hockey" else name) for team, season, name in conn.execute(
                "SELECT team_id, season, name FROM sport_teams WHERE sport_id = ?", (sport,)
            )
        }
    now = now_utc()
    elapsed = (now - game["started_at"]).total_seconds()
    countdown = max(0.0, OPENING_COUNTDOWN_SECONDS - elapsed) if not game["finished"] else 0.0
    remaining = max(0.0, APP_TURN_SECONDS - max(0.0, elapsed - OPENING_COUNTDOWN_SECONDS)) if not game["finished"] else 0.0
    chain = []
    for index, (player_id, name) in enumerate(zip(state.chain, state.chain_names)):
        card = cards[player_id]
        display_name = card.get("display_name") or _sport_display_name(sport, player_id, card.get("name_first"), card.get("name_last"), name)
        chain.append({"id": player_id, "name": display_name, **card, "shared_with_prev": [
            {"team_id": team, "season": season, "team_name": team_names.get((team, season), team),
             "season_label": _sport_season_label(sport, season)}
            for team, season in state.chain_shared_with_prev[index]
        ]})
    last_move = dict(game["last_move"] or {})
    for field in ("shared_seasons", "burned_seasons"):
        for item in last_move.get(field, []):
            item["team_name"] = team_names.get((item["team_id"], item["season"]), item["team_id"])
            item["season_label"] = _sport_season_label(sport, item["season"])
    return {"game_id": game_id, "mode": "bp", "sport": sport, "mode_name": LOCAL_SPORT_MODE_NAMES[sport],
            "current_player": {"id": state.current_player_id, "name": _sport_display_name(game["sport"], state.current_player_id, fallback=state.current_player_name)},
            "chain": chain, "strikes": [{"team_id": team, "season": season, "count": count,
            "team_name": team_names.get((team, season), team),
            "season_label": _sport_season_label(sport, season)} for (team, season), count in state.strikes.items()],
            "chain_length": len(state.chain), "longest_chain": len(state.chain), "turn_seconds": APP_TURN_SECONDS,
            "countdown_seconds_remaining": countdown, "remaining_seconds": remaining,
            "finished": game["finished"], "last_move": last_move}


def _local_team_name(sport: str, team_id: str, season: int, conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT name FROM sport_teams WHERE sport_id=? AND team_id=? AND season=?",
        (sport, team_id, season),
    ).fetchone()
    name = row[0] if row else team_id
    return NHL_TEAM_NAMES.get(name, NHL_TEAM_NAMES.get(team_id, name)) if sport == "hockey" else name


def _local_fr_card(sport: str, player_id: str, card: dict) -> dict:
    return {
        "id": player_id,
        "name": card.get("display_name") or _sport_display_name(sport, player_id, card.get("name_first"), card.get("name_last")),
        "mlbam_id": None,
        "headshot_url": card.get("headshot_url"),
        "debut_year": card.get("debut_year"),
        "final_year": card.get("final_year"),
        "primary_pos": card.get("primary_pos"),
        "teams": [],
        "team_stints": card.get("team_stints", []),
    }


def _local_fr_state(game_id: str, game: dict) -> dict:
    sport, blob = game["sport"], game["blob"]
    deck, pair_index = blob["deck"], blob["pair_index"]
    with _local_sport_conn() as conn:
        cards = _local_sport_cards(conn, sport, deck)
    card_dicts = {player_id: _local_fr_card(sport, player_id, card) for player_id, card in cards.items()}
    return {
        "game_id": game_id,
        "mode": "fr",
        "sport": sport,
        "puzzle_id": blob["puzzle_id"],
        "slots": blob["slots"],
        "unit": blob.get("unit"),
        "total_cards": len(deck),
        "revealed_count": blob["revealed_count"],
        "revealed_cards": [card_dicts[player_id] for player_id in deck[:blob["revealed_count"]]],
        "pair_index": pair_index,
        "pair_names": [
            card_dicts[deck[pair_index]]["name"] if pair_index < len(deck) else None,
            card_dicts[deck[pair_index + 1]]["name"] if pair_index + 1 < len(deck) else None,
        ],
        "current_answers": [
            {"team_id": row[0], "season": row[1], "team_name": row[2],
             "season_label": row[3] if len(row) > 3 else _sport_season_label(sport, row[1])}
            for row in (blob["shared_per_pair"][pair_index] if pair_index < len(blob["shared_per_pair"]) else [])
        ],
        "solved_links": blob["solved_links"][:max(0, blob["revealed_count"] - 1)],
        "stats": {
            "hits": blob["hits"], "fouls": blob["fouls"], "strikes": blob["strikes"],
            "max_strikes": FR_MAX_STRIKES, "consec_fouls": blob["consec_fouls"],
            "total_pairs": len(deck) - 1,
        },
        "finished": blob["finished"], "won": blob["won"], "last_guess": blob["last_guess"],
    }


def _local_fr_shared(conn: sqlite3.Connection, sport: str, first: str, second: str) -> list[list]:
    rows = conn.execute("""
        SELECT a.team_id, a.season FROM sport_appearances a
        JOIN sport_appearances b
          ON b.sport_id=a.sport_id AND b.team_id=a.team_id AND b.season=a.season
        WHERE a.sport_id=? AND a.player_id=? AND b.player_id=?
          AND NOT (
              a.sport_id='football' AND a.season>=2025
              AND EXISTS (
                  SELECT 1 FROM sport_players pa
                   WHERE pa.sport_id=a.sport_id AND pa.player_id=a.player_id
                     AND pa.debut_year <= a.season - 4
              )
              AND NOT EXISTS (
                  SELECT 1 FROM sport_appearances prior_a
                   WHERE prior_a.sport_id=a.sport_id AND prior_a.player_id=a.player_id
                     AND prior_a.season BETWEEN a.season - 2 AND a.season - 1
              )
          )
          AND NOT (
              b.sport_id='football' AND b.season>=2025
              AND EXISTS (
                  SELECT 1 FROM sport_players pb
                   WHERE pb.sport_id=b.sport_id AND pb.player_id=b.player_id
                     AND pb.debut_year <= b.season - 4
              )
              AND NOT EXISTS (
                  SELECT 1 FROM sport_appearances prior_b
                   WHERE prior_b.sport_id=b.sport_id AND prior_b.player_id=b.player_id
                     AND prior_b.season BETWEEN b.season - 2 AND b.season - 1
              )
          )
        ORDER BY a.season, a.team_id
    """, (sport, first, second)).fetchall()
    out = [
        [team_id, season, _canonical_sport_team_name(sport, team_id, _local_team_name(sport, team_id, season, conn)),
         _sport_season_label(sport, season)]
        for team_id, season in rows
    ]
    seen = set()
    deduped = []
    for row in out:
        key = (normalize(row[2]), row[3])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _classify_local_fr_guess(team_text: str, year_text: str, shared: list[list], sport: str = "") -> tuple[str, list]:
    query = normalize(team_text)
    team_matches, year_match = [], False
    for team_id, season, team_name, *_rest in shared:
        aliases = {normalize(team_id), normalize(team_name)}
        team_hit = bool(query) and any(query == alias or query in alias or alias in query for alias in aliases)
        if team_hit:
            team_matches.append([team_id, season, team_name])
        if _sport_fr_year_matches(sport, season, year_text):
            year_match = True
    hits = [row for row in team_matches if _sport_fr_year_matches(sport, row[1], year_text)]
    return ("hit", hits) if hits else (("foul", []) if team_matches or year_match else ("strike", []))


@app.route("/api/local/<sport>/fr/team_autocomplete")
def local_fr_team_autocomplete(sport: str):
    query = normalize(request.args.get("q") or "")
    if sport not in LOCAL_SPORT_SEEDS or not query:
        return jsonify([])
    names = CURRENT_SPORT_TEAM_NAMES.get(sport)
    if not names:
        with _local_sport_conn() as conn:
            names = sorted({_canonical_sport_team_name(sport, team_id, _local_team_name(sport, team_id, season, conn))
                            for team_id, season in conn.execute(
                                "SELECT team_id, season FROM sport_teams WHERE sport_id=?", (sport,)
                            )})
    prefix = [name for name in names if normalize(name).startswith(query)]
    contains = [name for name in names if query in normalize(name) and not normalize(name).startswith(query)]
    return jsonify((prefix + contains)[:6])


@app.route("/api/local/<sport>/fr/new", methods=["POST"])
def local_fr_new(sport: str):
    if sport not in LOCAL_SPORT_SEEDS:
        return jsonify({"error": "unsupported local sport"}), 404
    try:
        unit = (request.get_json(silent=True) or {}).get("unit")
        unit = unit.strip().lower() if isinstance(unit, str) else None
        with _local_sport_conn() as conn:
            puzzle = generate_local_film_review(conn, sport, unit=unit)
            shared_per_pair = [_local_fr_shared(conn, sport, puzzle.deck[index], puzzle.deck[index + 1])
                               for index in range(len(puzzle.deck) - 1)]
    except (RuntimeError, ValueError) as error:
        return jsonify({"error": f"could not build today's Film Review: {error}"}), 500
    game_id = str(uuid.uuid4())
    blob = {
        "puzzle_id": f"local_{sport}_{puzzle.puzzle_date}_{puzzle.unit or 'full'}", "deck": list(puzzle.deck),
        "slots": list(puzzle.slots), "unit": puzzle.unit,
        "pair_index": 0, "revealed_count": 2, "hits": 0, "fouls": 0, "strikes": 0,
        "consec_fouls": 0, "solved_links": [None] * (len(puzzle.deck) - 1),
        "shared_per_pair": shared_per_pair, "finished": False, "won": False, "last_guess": None,
    }
    game = {"sport": sport, "blob": blob}
    with LOCAL_FR_LOCK:
        LOCAL_FR_GAMES[game_id] = game
    return jsonify(_local_fr_state(game_id, game))


@app.route("/api/local/<sport>/fr/guess", methods=["POST"])
def local_fr_guess(sport: str):
    data = request.get_json(silent=True) or {}
    game_id = data.get("game_id")
    team_text, year_text = (data.get("team") or "").strip(), (data.get("year") or "").strip()
    with LOCAL_FR_LOCK:
        game = LOCAL_FR_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"error": "unknown local game"}), 404
        blob = game["blob"]
        if blob["finished"]:
            return jsonify(_local_fr_state(game_id, game))
        if not team_text or not year_text or (_cross_year_season_sports(sport) and _parse_cross_year_season_guess(year_text) == (None, None)):
            blob["last_guess"] = {"outcome": "invalid", "team": team_text, "year": year_text}
            return jsonify(_local_fr_state(game_id, game))
        outcome, matched = _classify_local_fr_guess(team_text, year_text, blob["shared_per_pair"][blob["pair_index"]], sport)
        converted = outcome == "foul" and blob["consec_fouls"] + 1 >= 2
        if outcome == "foul":
            blob["consec_fouls"] += 1
            if converted:
                outcome = "strike"
        else:
            blob["consec_fouls"] = 0
        if outcome == "hit":
            blob["hits"] += 1
            team_id, season, team_name = matched[0][:3]
            blob["solved_links"][blob["pair_index"]] = {
                "team_id": team_id, "season": season, "team_name": team_name,
                "season_label": _sport_season_label(sport, season),
            }
            blob["pair_index"] += 1
            blob["revealed_count"] = min(blob["revealed_count"] + 1, len(blob["deck"]))
            if blob["hits"] == len(blob["deck"]) - 1:
                blob["finished"], blob["won"] = True, True
        elif outcome == "foul":
            blob["fouls"] += 1
        else:
            blob["strikes"] += 1
            if blob["strikes"] >= FR_MAX_STRIKES:
                blob["finished"] = True
        blob["last_guess"] = {
            "outcome": outcome, "team": team_text, "year": year_text,
            "converted_from_foul": converted,
            "matched": [{"team_id": item[0], "season": item[1], "team_name": item[2],
                         "season_label": _sport_season_label(sport, item[1])} for item in matched],
        }
        return jsonify(_local_fr_state(game_id, game))


@app.route("/api/local/<sport>/fr/reveal_answer", methods=["POST"])
def local_fr_reveal_answer(sport: str):
    game_id = (request.get_json(silent=True) or {}).get("game_id")
    with LOCAL_FR_LOCK:
        game = LOCAL_FR_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"error": "unknown local game"}), 404
        blob = game["blob"]
        if not blob["finished"]:
            return jsonify({"error": "game not finished"}), 400
        with _local_sport_conn() as conn:
            cards = _local_sport_cards(conn, sport, blob["deck"])
        return jsonify({
            "full_cards": [_local_fr_card(player_id, cards[player_id]) for player_id in blob["deck"]],
            "canonical_links": [
                {"team_id": pair[0][0], "season": pair[0][1], "team_name": pair[0][2],
                 "season_label": _sport_season_label(sport, pair[0][1])} if pair else None
                for pair in blob["shared_per_pair"]
            ],
        })


@app.route("/api/local/<sport>/autocomplete")
def local_sport_autocomplete(sport: str):
    q = (request.args.get("q") or "").strip()
    if sport not in LOCAL_SPORT_SEEDS or not q:
        return jsonify([])
    normalized = "".join(char for char in normalize(q) if char.isalnum())
    with _local_sport_conn() as conn:
        rows = conn.execute(
            """SELECT player_id, display_name, debut_year, final_year, career_games
                 FROM (
                   SELECT p.player_id, p.display_name, sp.debut_year, sp.final_year, p.career_games
                     FROM sport_players_searchable p
                     JOIN sport_players sp ON sp.sport_id = p.sport_id AND sp.player_id = p.player_id
                    WHERE p.sport_id = ? AND sp.final_year >= 2000
                      AND (p.search_key LIKE ? OR p.last_key LIKE ?)
                   UNION
                   SELECT p.player_id, p.display_name, sp.debut_year, sp.final_year, p.career_games
                     FROM sport_player_aliases a
                     JOIN sport_players_searchable p ON p.sport_id = a.sport_id AND p.player_id = a.player_id
                     JOIN sport_players sp ON sp.sport_id = p.sport_id AND sp.player_id = p.player_id
                    WHERE a.sport_id = ? AND sp.final_year >= 2000 AND a.alias_key LIKE ?
                 )
                ORDER BY career_games DESC LIMIT 4""",
            (sport, normalized + "%", normalized + "%", sport, normalized + "%"),
        ).fetchall()
    return jsonify([{"player_id": pid, "display_name": _sport_display_name(sport, pid, fallback=name), "debut_year": debut, "final_year": final,
                     "career_games": games} for pid, name, debut, final, games in rows])


@app.route("/api/local/<sport>/bp/new", methods=["POST"])
def local_bp_new(sport: str):
    if sport not in LOCAL_SPORT_SEEDS:
        return jsonify({"error": "unsupported local sport"}), 404
    with _local_sport_conn() as conn:
        state = seed_game(conn, LOCAL_SPORT_SEEDS[sport], sport=sport)
    game_id = str(uuid.uuid4())
    game = {"sport": sport, "state": state, "started_at": now_utc(), "finished": False, "last_move": None}
    with LOCAL_BP_LOCK:
        LOCAL_BP_GAMES[game_id] = game
    return jsonify(_local_bp_state(game_id, game))


@app.route("/api/local/<sport>/bp/move", methods=["POST"])
def local_bp_move(sport: str):
    data = request.get_json(silent=True) or {}
    game_id = data.get("game_id")
    with LOCAL_BP_LOCK:
        game = LOCAL_BP_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"error": "unknown local game"}), 404
        if (now_utc() - game["started_at"]).total_seconds() > APP_TURN_SECONDS + OPENING_COUNTDOWN_SECONDS:
            game["finished"] = True
            game["last_move"] = {"outcome": "timeout"}
        elif not game["finished"]:
            with _local_sport_conn() as conn:
                picked_id = (data.get("player_id") or "").strip() or None
                result = validate_and_apply_move(
                    game["state"], conn,
                    player_id=picked_id,
                    raw_input=None if picked_id else (data.get("raw") or "").strip(),
                    track_strikes=True, sport=sport,
                )
                game["last_move"] = {"outcome": result.outcome.value, "player_id": result.player_id,
                    "display_name": result.display_name, "disambiguation": result.disambiguation,
                    "ambiguous_count": result.ambiguous_count, "shared_seasons": [
                    {"team_id": t, "season": s, "team_name": t} for t, s in result.shared_seasons],
                    "burned_seasons": [{"team_id": t, "season": s, "team_name": t} for t, s in result.burned_seasons]}
                if result.outcome == MoveOutcome.VALID:
                    game["started_at"] = now_utc() - timedelta(seconds=OPENING_COUNTDOWN_SECONDS)
        return jsonify(_local_bp_state(game_id, game))


@app.route("/api/local/<sport>/bp/timeout", methods=["POST"])
def local_bp_timeout(sport: str):
    data = request.get_json(silent=True) or {}
    game_id = data.get("game_id")
    with LOCAL_BP_LOCK:
        game = LOCAL_BP_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"error": "unknown local game"}), 404
        if not game["finished"] and (now_utc() - game["started_at"]).total_seconds() >= APP_TURN_SECONDS + OPENING_COUNTDOWN_SECONDS - 0.25:
            game["finished"] = True
            game["last_move"] = {"outcome": "timeout"}
        return jsonify(_local_bp_state(game_id, game))


# ----- Local cross-sport Division Rivalry -----

def _local_dr_player_key(sport: str, guest_id: str) -> tuple[str, str]:
    return sport, guest_id


def _local_dr_authorized(game: dict, guest_id: str) -> bool:
    return guest_id in {game["p1_guest_id"], game["p2_guest_id"]}


def _local_dr_expire(game: dict) -> None:
    if game["finished"]:
        return
    elapsed = (now_utc() - game["turn_started_at"]).total_seconds()
    live_elapsed = max(0.0, elapsed - game["countdown_seconds"])
    if live_elapsed >= game["turn_seconds"]:
        loser = game["turn_index"]
        game["finished"] = True
        game["winner"] = game["p2"] if loser == 0 else game["p1"]
        game["last_move"] = {"outcome": "timeout"}


def _local_dr_chain(state: GameState, sport: str) -> tuple[list[dict], list[dict]]:
    with _local_sport_conn() as conn:
        cards = _local_sport_cards(conn, sport, state.chain)
        team_names = {
            (team, season): _local_team_name(sport, team, season, conn)
            for team, season in conn.execute(
                "SELECT team_id, season FROM sport_teams WHERE sport_id = ?", (sport,)
            )
        }
    chain = []
    for index, (player_id, name) in enumerate(zip(state.chain, state.chain_names)):
        card = cards[player_id]
        display_name = card.get("display_name") or _sport_display_name(sport, player_id, card.get("name_first"), card.get("name_last"), name)
        chain.append({
            "id": player_id,
            "name": display_name,
            **card,
            "shared_with_prev": [
                {"team_id": team, "season": season, "team_name": team_names.get((team, season), team),
                 "season_label": _sport_season_label(sport, season)}
                for team, season in state.chain_shared_with_prev[index]
            ],
        })
    strikes = [
        {"team_id": team, "season": season, "count": count,
         "team_name": team_names.get((team, season), team),
         "season_label": _sport_season_label(sport, season)}
        for (team, season), count in state.strikes.items()
    ]
    return chain, strikes


def _local_dr_state(game_id: str, game: dict, viewer_guest_id: str) -> dict:
    _local_dr_expire(game)
    elapsed = (now_utc() - game["turn_started_at"]).total_seconds()
    countdown_left = max(0.0, game["countdown_seconds"] - elapsed) if not game["finished"] else 0.0
    remaining = max(0.0, game["turn_seconds"] - max(0.0, elapsed - game["countdown_seconds"])) \
        if not game["finished"] else 0.0
    state = game["state"]
    chain, strikes = _local_dr_chain(state, game["sport"])
    your_side = "p1" if viewer_guest_id == game["p1_guest_id"] else "p2"
    last_move = dict(game.get("last_move") or {})
    for field in ("shared_seasons", "burned_seasons"):
        for item in last_move.get(field, []):
            # The chain has already normalized names for display; resolve move feedback too.
            with _local_sport_conn() as conn:
                item["team_name"] = _local_team_name(game["sport"], item["team_id"], item["season"], conn)
                item["season_label"] = _sport_season_label(game["sport"], item["season"])
    return {
        "game_id": game_id,
        "mode": "mp",
        "sport": game["sport"],
        "current_player": {"id": state.current_player_id, "name": _sport_display_name(game["sport"], state.current_player_id, fallback=state.current_player_name)},
        "current_label": game["p1"] if game["turn_index"] == 0 else game["p2"],
        "p1": game["p1"], "p2": game["p2"],
        "p1_guest_id": game["p1_guest_id"], "p2_guest_id": game["p2_guest_id"],
        "viewer_guest_id": viewer_guest_id,
        "your_side": your_side,
        "your_name": game[your_side],
        "opponent_name": game["p2" if your_side == "p1" else "p1"],
        "your_turn": not game["finished"] and (
            (your_side == "p1" and game["turn_index"] == 0) or
            (your_side == "p2" and game["turn_index"] == 1)
        ),
        "turn_index": game["turn_index"],
        "turn_seconds": game["turn_seconds"],
        "countdown_seconds_remaining": countdown_left,
        "remaining_seconds": remaining,
        "chain": chain,
        "strikes": strikes,
        "finished": game["finished"],
        "winner": game.get("winner"),
        "last_move": last_move,
    }


def _local_dr_create_game(sport: str, first: dict, second: dict) -> tuple[str, dict]:
    p1, p2 = (first, second) if secrets.randbelow(2) == 0 else (second, first)
    with _local_sport_conn() as conn:
        state = seed_game(conn, LOCAL_SPORT_SEEDS[sport], sport=sport)
    game_id = str(uuid.uuid4())
    game = {
        "sport": sport,
        "state": state,
        "p1": p1["name"], "p2": p2["name"],
        "p1_guest_id": p1["guest_id"], "p2_guest_id": p2["guest_id"],
        "turn_index": 0,
        "turn_seconds": APP_TURN_SECONDS,
        "turn_started_at": now_utc(),
        "countdown_seconds": OPENING_COUNTDOWN_SECONDS,
        "finished": False, "winner": None, "last_move": None,
    }
    LOCAL_DR_GAMES[game_id] = game
    LOCAL_DR_MATCH_BY_PLAYER[_local_dr_player_key(sport, p1["guest_id"])] = game_id
    LOCAL_DR_MATCH_BY_PLAYER[_local_dr_player_key(sport, p2["guest_id"])] = game_id
    return game_id, game


def _local_dr_status(sport: str, guest_id: str) -> dict:
    game_id = LOCAL_DR_MATCH_BY_PLAYER.get(_local_dr_player_key(sport, guest_id))
    game = LOCAL_DR_GAMES.get(game_id) if game_id else None
    if game and not game["finished"]:
        return {"status": "matched", "game": _local_dr_state(game_id, game, guest_id)}
    if any(row["guest_id"] == guest_id for row in LOCAL_DR_QUEUE[sport]):
        return {"status": "waiting", "guest_id": guest_id}
    return {"status": "idle"}


@app.route("/api/local/<sport>/dr/queue", methods=["POST"])
def local_dr_queue(sport: str):
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if sport not in LOCAL_SPORT_SEEDS or not guest_id:
        return jsonify({"error": "supported sport and guest_id required"}), 400
    name = (data.get("display_name") or data.get("name") or "Player").strip()[:24] or "Player"
    avoid_guest_id = (data.get("avoid_guest_id") or "").strip()
    with LOCAL_DR_LOCK:
        current = _local_dr_status(sport, guest_id)
        if current["status"] == "matched":
            return jsonify(current)
        queue = LOCAL_DR_QUEUE[sport]
        queue[:] = [row for row in queue if row["guest_id"] != guest_id]
        opponent_index = next((index for index, row in enumerate(queue)
                               if row["guest_id"] != guest_id and row["guest_id"] != avoid_guest_id), None)
        if opponent_index is None:
            queue.append({"guest_id": guest_id, "name": name, "avoid_guest_id": avoid_guest_id})
            return jsonify({"status": "waiting", "guest_id": guest_id})
        opponent = queue.pop(opponent_index)
        game_id, game = _local_dr_create_game(sport, opponent, {"guest_id": guest_id, "name": name})
        return jsonify({"status": "matched", "game": _local_dr_state(game_id, game, guest_id)})


@app.route("/api/local/<sport>/dr/status", methods=["POST"])
def local_dr_status(sport: str):
    guest_id = ((request.get_json(silent=True) or {}).get("guest_id") or "").strip()
    if sport not in LOCAL_SPORT_SEEDS or not guest_id:
        return jsonify({"error": "supported sport and guest_id required"}), 400
    with LOCAL_DR_LOCK:
        return jsonify(_local_dr_status(sport, guest_id))


@app.route("/api/local/<sport>/dr/game", methods=["POST"])
def local_dr_game(sport: str):
    data = request.get_json(silent=True) or {}
    guest_id, game_id = (data.get("guest_id") or "").strip(), (data.get("game_id") or "").strip()
    with LOCAL_DR_LOCK:
        game = LOCAL_DR_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"error": "unknown game_id"}), 404
        if not _local_dr_authorized(game, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        return jsonify(_local_dr_state(game_id, game, guest_id))


@app.route("/api/local/<sport>/dr/move", methods=["POST"])
def local_dr_move(sport: str):
    data = request.get_json(silent=True) or {}
    guest_id, game_id = (data.get("guest_id") or "").strip(), (data.get("game_id") or "").strip()
    with LOCAL_DR_LOCK:
        game = LOCAL_DR_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"error": "unknown game_id"}), 404
        if not _local_dr_authorized(game, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        _local_dr_expire(game)
        if game["finished"]:
            return jsonify(_local_dr_state(game_id, game, guest_id))
        expected = game["p1_guest_id"] if game["turn_index"] == 0 else game["p2_guest_id"]
        if guest_id != expected:
            return jsonify({"error": "not your turn", **_local_dr_state(game_id, game, guest_id)}), 409
        elapsed = (now_utc() - game["turn_started_at"]).total_seconds()
        if elapsed < game["countdown_seconds"]:
            return jsonify(_local_dr_state(game_id, game, guest_id))
        picked_id = (data.get("player_id") or "").strip() or None
        with _local_sport_conn() as conn:
            result = validate_and_apply_move(
                game["state"], conn, player_id=picked_id,
                raw_input=None if picked_id else (data.get("raw") or "").strip(),
                track_strikes=True, sport=sport,
            )
        game["last_move"] = {
            "outcome": result.outcome.value, "player_id": result.player_id,
            "display_name": result.display_name, "disambiguation": result.disambiguation,
            "ambiguous_count": result.ambiguous_count,
            "shared_seasons": [{"team_id": team, "season": season} for team, season in result.shared_seasons],
            "burned_seasons": [{"team_id": team, "season": season} for team, season in result.burned_seasons],
        }
        if result.outcome == MoveOutcome.VALID:
            game["turn_index"] = 1 - game["turn_index"]
            game["turn_started_at"] = now_utc()
            game["countdown_seconds"] = 0.0
        return jsonify(_local_dr_state(game_id, game, guest_id))


@app.route("/api/local/<sport>/dr/leave_game", methods=["POST"])
def local_dr_leave_game(sport: str):
    data = request.get_json(silent=True) or {}
    guest_id, game_id = (data.get("guest_id") or "").strip(), (data.get("game_id") or "").strip()
    with LOCAL_DR_LOCK:
        game = LOCAL_DR_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"status": "gone"})
        if not _local_dr_authorized(game, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        if not game["finished"]:
            game["finished"] = True
            game["winner"] = game["p2"] if guest_id == game["p1_guest_id"] else game["p1"]
            game["last_move"] = {"outcome": "forfeit"}
        return jsonify({"status": "gone"})


@app.route("/api/local/<sport>/dr/rematch_request", methods=["POST"])
def local_dr_rematch_request(sport: str):
    data = request.get_json(silent=True) or {}
    guest_id, game_id = (data.get("guest_id") or "").strip(), (data.get("game_id") or "").strip()
    with LOCAL_DR_LOCK:
        game = LOCAL_DR_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"error": "unknown game_id"}), 404
        if not _local_dr_authorized(game, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        if not game["finished"] or game.get("last_move", {}).get("outcome") == "forfeit":
            return jsonify({"error": "rematch unavailable"}), 400
        new_game_id = LOCAL_DR_REMATCH_LINKS.get(game_id)
        if new_game_id:
            return jsonify({"status": "matched", "game": _local_dr_state(new_game_id, LOCAL_DR_GAMES[new_game_id], guest_id)})
        requests = LOCAL_DR_REMATCH_REQUESTS.setdefault(game_id, set())
        requests.add(guest_id)
        if {game["p1_guest_id"], game["p2_guest_id"]} <= requests:
            first = {"guest_id": game["p1_guest_id"], "name": game["p1"]}
            second = {"guest_id": game["p2_guest_id"], "name": game["p2"]}
            new_game_id, new_game = _local_dr_create_game(sport, first, second)
            LOCAL_DR_REMATCH_LINKS[game_id] = new_game_id
            return jsonify({"status": "matched", "game": _local_dr_state(new_game_id, new_game, guest_id)})
        return jsonify({"status": "waiting"})


@app.route("/api/local/<sport>/dr/rematch_status", methods=["POST"])
def local_dr_rematch_status(sport: str):
    data = request.get_json(silent=True) or {}
    guest_id, game_id = (data.get("guest_id") or "").strip(), (data.get("game_id") or "").strip()
    with LOCAL_DR_LOCK:
        game = LOCAL_DR_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"error": "unknown game_id"}), 404
        new_game_id = LOCAL_DR_REMATCH_LINKS.get(game_id)
        if new_game_id:
            return jsonify({"status": "matched", "game": _local_dr_state(new_game_id, LOCAL_DR_GAMES[new_game_id], guest_id)})
        other = game["p2_guest_id"] if guest_id == game["p1_guest_id"] else game["p1_guest_id"]
        if other in LOCAL_DR_POSTGAME_EXITS.get(game_id, set()):
            return jsonify({"status": "abandoned", "opponent_present": False})
        return jsonify({"status": "waiting", "opponent_present": True})


@app.route("/api/local/<sport>/dr/postgame_leave", methods=["POST"])
def local_dr_postgame_leave(sport: str):
    data = request.get_json(silent=True) or {}
    guest_id, game_id = (data.get("guest_id") or "").strip(), (data.get("game_id") or "").strip()
    with LOCAL_DR_LOCK:
        game = LOCAL_DR_GAMES.get(game_id)
        if not game or game["sport"] != sport:
            return jsonify({"status": "gone"})
        LOCAL_DR_POSTGAME_EXITS.setdefault(game_id, set()).add(guest_id)
        LOCAL_DR_REMATCH_REQUESTS.setdefault(game_id, set()).discard(guest_id)
        other = game["p2_guest_id"] if guest_id == game["p1_guest_id"] else game["p1_guest_id"]
        if other in LOCAL_DR_REMATCH_REQUESTS.get(game_id, set()):
            LOCAL_DR_QUEUE[sport] = [row for row in LOCAL_DR_QUEUE[sport] if row["guest_id"] != other]
            LOCAL_DR_QUEUE[sport].append({"guest_id": other, "name": game["p2"] if other == game["p2_guest_id"] else game["p1"], "avoid_guest_id": guest_id})
        return jsonify({"status": "gone"})


@app.route("/api/local/<sport>/dr/cancel_queue", methods=["POST"])
def local_dr_cancel_queue(sport: str):
    guest_id = ((request.get_json(silent=True) or {}).get("guest_id") or "").strip()
    with LOCAL_DR_LOCK:
        if sport in LOCAL_DR_QUEUE:
            LOCAL_DR_QUEUE[sport][:] = [row for row in LOCAL_DR_QUEUE[sport] if row["guest_id"] != guest_id]
    return jsonify({"status": "idle"})


@app.route("/api/local/<sport>/dr/cancel_challenge", methods=["POST"])
def local_dr_cancel_challenge(sport: str):
    # Challenge codes remain a production-baseball feature until account-backed
    # cross-sport games are migrated from the local data store.
    return jsonify({"status": "idle"})


# ----- Local cross-sport Playoffs -----

def _local_position_group(sport: str, position: str | None) -> str:
    value = (position or "").upper()
    if sport == "basketball":
        return "guard" if "G" in value else "big" if "C" in value or "F" in value else "other"
    if sport == "hockey":
        return "defense" if value == "D" else "goalie" if value == "G" else "forward"
    defensive = {"CB", "S", "FS", "SS", "LB", "OLB", "ILB", "MLB", "MIKE", "DT", "DE", "EDGE", "DL", "NT"}
    special = {"K", "P", "LS"}
    return "defense" if value in defensive else "special" if value in special else "offense"


def _local_position_tokens(position: str | None) -> set[str]:
    value = (position or "").upper().replace(",", "/").replace("-", "/")
    return {part.strip() for part in value.split("/") if part.strip()}


def _local_po_traits(conn: sqlite3.Connection, sport: str, player_id: str) -> dict:
    row = conn.execute(
        """SELECT p.primary_pos, COALESCE(NULLIF(pt.career_games, 0), s.career_games),
                  COUNT(DISTINCT a.team_id), COUNT(DISTINCT t.franchise_id), COUNT(DISTINCT a.season),
                  COALESCE(pt.career_points, 0), COALESCE(pt.career_goals, 0), COALESCE(pt.career_assists, 0),
                  COALESCE(pt.career_touchdowns, 0), COALESCE(pt.passing_touchdowns, 0), COALESCE(pt.rushing_touchdowns, 0),
                  COALESCE(pt.receiving_touchdowns, 0), COALESCE(pt.career_sacks, 0), COALESCE(pt.career_interceptions, 0),
                  COALESCE(pt.mvp_count, 0), COALESCE(pt.roty_count, 0),
                  COALESCE(pt.all_star_count, 0), COALESCE(pt.championship_count, 0),
                  COALESCE(MAX(st.points), 0), COALESCE(MAX(st.goals), 0), COALESCE(MAX(st.assists), 0),
                  COALESCE(MAX(st.touchdowns), 0), COALESCE(MAX(st.passing_touchdowns), 0),
                  COALESCE(MAX(st.rushing_touchdowns), 0), COALESCE(MAX(st.receiving_touchdowns), 0),
                  COALESCE(MAX(st.sacks), 0), COALESCE(MAX(st.interceptions), 0)
             FROM sport_players p
             JOIN sport_players_searchable s ON s.sport_id=p.sport_id AND s.player_id=p.player_id
             LEFT JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
             LEFT JOIN sport_teams t ON t.sport_id=a.sport_id AND t.team_id=a.team_id AND t.season=a.season
             LEFT JOIN sport_player_traits pt ON pt.sport_id=p.sport_id AND pt.player_id=p.player_id
             LEFT JOIN sport_player_season_traits st ON st.sport_id=p.sport_id AND st.player_id=p.player_id
            WHERE p.sport_id=? AND p.player_id=?
            GROUP BY p.primary_pos, s.career_games, pt.career_games, pt.career_points,
                     pt.career_goals, pt.career_assists, pt.career_touchdowns,
                     pt.passing_touchdowns, pt.rushing_touchdowns,
                     pt.receiving_touchdowns, pt.career_sacks,
                     pt.career_interceptions, pt.mvp_count, pt.roty_count,
                     pt.all_star_count, pt.championship_count""",
        (sport, player_id),
    ).fetchone()
    if not row:
        return {"position": "", "career_games": 0, "team_count": 0, "franchise_count": 0, "season_count": 0,
                "career_points": 0, "career_goals": 0, "career_assists": 0, "career_touchdowns": 0,
                "passing_touchdowns": 0, "rushing_touchdowns": 0, "receiving_touchdowns": 0,
                "career_sacks": 0, "career_interceptions": 0,
                "mvp_count": 0, "roty_count": 0, "all_star_count": 0, "championship_count": 0,
                "peak_points": 0, "peak_goals": 0, "peak_assists": 0, "peak_touchdowns": 0,
                "peak_passing_touchdowns": 0, "peak_rushing_touchdowns": 0, "peak_receiving_touchdowns": 0,
                "peak_sacks": 0, "peak_interceptions": 0}
    return dict(zip(("position", "career_games", "team_count", "franchise_count", "season_count",
                     "career_points", "career_goals", "career_assists", "career_touchdowns",
                     "passing_touchdowns", "rushing_touchdowns", "receiving_touchdowns", "career_sacks", "career_interceptions",
                     "mvp_count", "roty_count", "all_star_count", "championship_count",
                     "peak_points", "peak_goals", "peak_assists", "peak_touchdowns",
                     "peak_passing_touchdowns", "peak_rushing_touchdowns", "peak_receiving_touchdowns",
                     "peak_sacks", "peak_interceptions"), row))


def _local_po_condition_increment(conn: sqlite3.Connection, sport: str, key: str, player_id: str) -> int:
    condition = LOCAL_PLAYOFF_CONFIG[sport]["conditions"][key]
    traits = _local_po_traits(conn, sport, player_id)
    kind, threshold = condition["kind"], int(condition.get("threshold") or 0)
    if kind == "career_games":
        return int(traits["career_games"] >= threshold)
    if kind == "team_count":
        return int(traits["team_count"] >= threshold)
    if kind == "one_franchise":
        return int(traits["franchise_count"] == 1 and traits["season_count"] >= threshold)
    if kind == "trait":
        return int(int(traits.get(condition["trait"], 0)) >= threshold)
    if kind == "sum_trait":
        return int(traits.get(condition["trait"], 0))
    if kind == "position_group":
        return int(_local_position_group(sport, traits["position"]) == condition["group"])
    return 0


def _local_po_powerup_state(game: dict, viewer_guest_id: str) -> dict:
    config = LOCAL_PLAYOFF_CONFIG[game["sport"]]["powerups"]
    def payload(side: str) -> list[dict]:
        used = set(game.get(f"{side}_powerup_used_keys") or [])
        return [{"key": key, "label": meta["label"], "description": meta["description"],
                 "kind": meta["kind"], "used": key in used,
                 "owner": game[side]}
                for key, meta in config.items()]
    your_side = "p1" if viewer_guest_id == game["p1_guest_id"] else "p2"
    other = "p2" if your_side == "p1" else "p1"
    active = game.get("active_turn_powerup")
    return {
        "your_powerups": payload(your_side), "opponent_powerups": payload(other),
        "active_turn_powerup": {"key": active, "label": config[active]["label"]} if active else None,
        "turn_powerup_used": bool(game.get("turn_powerup_used")),
        "opening_lock_moves": PLAYOFF_OPENING_LOCK_MOVES,
    }


def _local_po_state(game_id: str, game: dict, viewer_guest_id: str) -> dict:
    sport = game["sport"]
    _local_dr_expire(game)
    elapsed = (now_utc() - game["turn_started_at"]).total_seconds()
    countdown_left = max(0.0, game["countdown_seconds"] - elapsed) if not game["finished"] else 0.0
    remaining = max(0.0, game["turn_seconds"] - max(0.0, elapsed - game["countdown_seconds"])) if not game["finished"] else 0.0
    state = game["state"]
    chain, strikes = _local_dr_chain(state, sport)
    link_meta = game.get("chain_link_meta") or [None] * len(chain)
    hits = game.get("chain_win_condition_hits") or [False] * len(chain)
    for index, player in enumerate(chain):
        player["link_meta_with_prev"] = link_meta[index] if index < len(link_meta) else None
        player["win_condition_hit"] = bool(hits[index]) if index < len(hits) else False
    your_side = "p1" if viewer_guest_id == game["p1_guest_id"] else "p2"
    other_side = "p2" if your_side == "p1" else "p1"
    conditions = LOCAL_PLAYOFF_CONFIG[sport]["conditions"]
    def condition_payload(side: str) -> dict:
        key = game[f"{side}_win_condition_key"]
        condition = conditions[key]
        return {"key": key, "label": condition["label"], "description": condition["description"],
                "target": condition["target"], "progress": game.get(f"{side}_win_progress", 0),
                "completed": game.get(f"{side}_win_completed", False)}
    last_move = dict(game.get("last_move") or {})
    if last_move.get("shared_seasons") or last_move.get("burned_seasons"):
        with _local_sport_conn() as conn:
            for field in ("shared_seasons", "burned_seasons"):
                for item in last_move.get(field, []):
                    item["team_name"] = _local_team_name(sport, item["team_id"], item["season"], conn)
    return {
        "game_id": game_id, "mode": "po", "sport": sport,
        "current_player": {"id": state.current_player_id, "name": _sport_display_name(sport, state.current_player_id, fallback=state.current_player_name)},
        "current_label": game["p1"] if game["turn_index"] == 0 else game["p2"],
        "p1": game["p1"], "p2": game["p2"],
        "p1_guest_id": game["p1_guest_id"], "p2_guest_id": game["p2_guest_id"],
        "viewer_guest_id": viewer_guest_id, "your_side": your_side,
        "your_name": game[your_side], "opponent_name": game[other_side],
        "your_turn": not game["finished"] and ((your_side == "p1" and game["turn_index"] == 0) or (your_side == "p2" and game["turn_index"] == 1)),
        "turn_index": game["turn_index"], "turn_seconds": game["turn_seconds"],
        "default_turn_seconds": APP_TURN_SECONDS, "countdown_seconds_remaining": countdown_left,
        "remaining_seconds": remaining, "chain": chain, "strikes": strikes,
        "finished": game["finished"], "winner": game.get("winner"), "last_move": last_move,
        "powerups": _local_po_powerup_state(game, viewer_guest_id),
        "win_conditions": {"your_condition": condition_payload(your_side), "opponent_condition": condition_payload(other_side)},
    }


def _local_po_create_game(sport: str, first: dict, second: dict, preferences: dict[str, str] | None = None,
                          first_guest_id: str | None = None) -> tuple[str, dict]:
    if first_guest_id == first["guest_id"]:
        p1, p2 = first, second
    elif first_guest_id == second["guest_id"]:
        p1, p2 = second, first
    else:
        p1, p2 = (first, second) if secrets.randbelow(2) == 0 else (second, first)
    conditions = LOCAL_PLAYOFF_CONFIG[sport]["conditions"]
    preferences = preferences or {}
    def selected(player: dict) -> tuple[str, str]:
        preference = preferences.get(player["guest_id"], "random")
        if preference in conditions:
            return preference, preference
        history_key = (player["guest_id"], sport)
        recent = LOCAL_RANDOM_PLAYOFF_HISTORY.get(history_key, [])[-3:]
        options = [key for key in conditions if key not in recent] or list(conditions)
        choice = secrets.choice(options)
        LOCAL_RANDOM_PLAYOFF_HISTORY[history_key] = (recent + [choice])[-3:]
        return choice, "random"
    with _local_sport_conn() as conn:
        state = seed_game(conn, LOCAL_SPORT_SEEDS[sport], sport=sport)
    game_id = str(uuid.uuid4())
    p1_condition, p1_preference = selected(p1)
    p2_condition, p2_preference = selected(p2)
    game = {
        "sport": sport, "state": state, "p1": p1["name"], "p2": p2["name"],
        "p1_guest_id": p1["guest_id"], "p2_guest_id": p2["guest_id"], "turn_index": 0,
        "turn_seconds": APP_TURN_SECONDS, "turn_started_at": now_utc(), "countdown_seconds": OPENING_COUNTDOWN_SECONDS,
        "finished": False, "winner": None, "last_move": None, "active_turn_powerup": None,
        "next_turn_seconds_override": None, "turn_powerup_used": False,
        "p1_powerup_used_keys": [], "p2_powerup_used_keys": [],
        "p1_win_condition_key": p1_condition, "p2_win_condition_key": p2_condition,
        "p1_win_condition_preference": p1_preference, "p2_win_condition_preference": p2_preference,
        "p1_win_progress": 0, "p2_win_progress": 0, "p1_win_completed": False, "p2_win_completed": False,
        "chain_win_condition_hits": [False], "chain_link_meta": [None],
    }
    LOCAL_PO_GAMES[game_id] = game
    LOCAL_PO_MATCH_BY_PLAYER[_local_dr_player_key(sport, p1["guest_id"])] = game_id
    LOCAL_PO_MATCH_BY_PLAYER[_local_dr_player_key(sport, p2["guest_id"])] = game_id
    return game_id, game


def _local_po_status(sport: str, guest_id: str) -> dict:
    game_id = LOCAL_PO_MATCH_BY_PLAYER.get(_local_dr_player_key(sport, guest_id))
    game = LOCAL_PO_GAMES.get(game_id) if game_id else None
    if game and not game["finished"]:
        return {"status": "matched", "game": _local_po_state(game_id, game, guest_id)}
    if any(row["guest_id"] == guest_id for row in LOCAL_PO_QUEUE[sport]):
        return {"status": "waiting", "guest_id": guest_id}
    return {"status": "idle"}


def _local_po_pick(conn: sqlite3.Connection, sport: str, raw: str, player_id: str | None) -> tuple[str | None, str | None, str | None, int]:
    if player_id:
        row = conn.execute("""SELECT p.display_name, p.disambiguation
                              FROM sport_players_searchable p
                              JOIN sport_players sp ON sp.sport_id=p.sport_id AND sp.player_id=p.player_id
                             WHERE p.sport_id=? AND p.player_id=? AND sp.final_year >= 2000""", (sport, player_id)).fetchone()
        return (player_id, row[0], row[1], 1) if row else (None, None, None, 0)
    matches = find_player_by_name(conn, raw, sport=sport)
    return (matches[0][0], matches[0][1], matches[0][2], len(matches)) if matches else (None, None, None, 0)


def _local_po_powerup_move(conn: sqlite3.Connection, game: dict, raw: str, player_id: str | None) -> dict | None:
    key = game.get("active_turn_powerup")
    if not key:
        return None
    sport, state, meta = game["sport"], game["state"], LOCAL_PLAYOFF_CONFIG[game["sport"]]["powerups"][key]
    candidate_id, name, disambiguation, ambiguous_count = _local_po_pick(conn, sport, raw, player_id)
    if not candidate_id or candidate_id in state.chain:
        return None
    current_franchises = {row[0] for row in conn.execute(
        """SELECT DISTINCT t.franchise_id FROM sport_appearances a JOIN sport_teams t
               ON t.sport_id=a.sport_id AND t.team_id=a.team_id AND t.season=a.season
             WHERE a.sport_id=? AND a.player_id=? AND a.season >= 2000""", (sport, state.current_player_id))}
    traits = _local_po_traits(conn, sport, candidate_id)
    eligible = bool(current_franchises)
    if meta["kind"] == "veteran":
        eligible = eligible and traits["career_games"] >= meta["career_games"]
    elif meta["kind"] == "stat":
        eligible = eligible and int(traits.get(meta["stat"], 0)) >= int(meta["threshold"])
    elif meta["kind"] == "position":
        current_traits = _local_po_traits(conn, sport, state.current_player_id)
        eligible = eligible and _local_position_group(sport, traits["position"]) == _local_position_group(sport, current_traits["position"])
    elif meta["kind"] == "same_position":
        current_traits = _local_po_traits(conn, sport, state.current_player_id)
        eligible = eligible and bool(_local_position_tokens(traits["position"]) & _local_position_tokens(current_traits["position"]))
    if not eligible:
        return {"outcome": "powerup_not_eligible", "player_id": candidate_id, "display_name": name,
                "disambiguation": disambiguation, "ambiguous_count": ambiguous_count, "powerup_key": key,
                "powerup_label": meta["label"], "reason": f"{name} is not eligible for {meta['label']}."}
    rows = conn.execute(
        """SELECT a.team_id, a.season FROM sport_appearances a JOIN sport_teams t
               ON t.sport_id=a.sport_id AND t.team_id=a.team_id AND t.season=a.season
             WHERE a.sport_id=? AND a.player_id=? AND a.season >= 2000
               AND t.franchise_id IN ({}) ORDER BY a.season, a.team_id""".format(
            ",".join("?" for _ in current_franchises)), (sport, candidate_id, *sorted(current_franchises))).fetchall()
    available = [(team, season) for team, season in rows if not state.is_burned((team, season))]
    if not available:
        return {"outcome": "blocked_by_burned", "player_id": candidate_id, "display_name": name,
                "disambiguation": disambiguation, "ambiguous_count": ambiguous_count, "powerup_key": key,
                "powerup_label": meta["label"], "shared_seasons": [{"team_id": team, "season": season} for team, season in rows],
                "burned_seasons": [{"team_id": team, "season": season} for team, season in rows]}
    team, season = available[0]
    state.strikes[(team, season)] = state.strikes.get((team, season), 0) + 1
    state.chain.append(candidate_id); state.chain_names.append(name); state.chain_shared_with_prev.append([(team, season)])
    game["chain_link_meta"].append({"type": "powerup", "powerup_key": key, "powerup_label": meta["label"]})
    return {"outcome": "valid", "player_id": candidate_id, "display_name": name, "disambiguation": disambiguation,
            "ambiguous_count": ambiguous_count, "shared_seasons": [{"team_id": team, "season": season}], "burned_seasons": [],
            "powerup_key": key, "powerup_label": meta["label"], "move_via_powerup": True}


@app.route("/api/local/<sport>/po/queue", methods=["POST"])
def local_po_queue(sport: str):
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if sport not in LOCAL_PLAYOFF_CONFIG or not guest_id:
        return jsonify({"error": "supported sport and guest_id required"}), 400
    name = (data.get("display_name") or "Player").strip()[:24] or "Player"
    avoid = (data.get("avoid_guest_id") or "").strip()
    preference = (data.get("win_condition_preference") or "random").strip()
    with LOCAL_PO_LOCK:
        current = _local_po_status(sport, guest_id)
        if current["status"] == "matched": return jsonify(current)
        queue = LOCAL_PO_QUEUE[sport]; queue[:] = [row for row in queue if row["guest_id"] != guest_id]
        opponent_index = next((i for i, row in enumerate(queue) if row["guest_id"] != guest_id and row["guest_id"] != avoid), None)
        own = {"guest_id": guest_id, "name": name, "preference": preference}
        if opponent_index is None:
            queue.append(own); return jsonify({"status": "waiting", "guest_id": guest_id})
        opponent = queue.pop(opponent_index)
        preferences = {guest_id: preference, opponent["guest_id"]: opponent.get("preference", "random")}
        game_id, game = _local_po_create_game(sport, opponent, own, preferences)
        return jsonify({"status": "matched", "game": _local_po_state(game_id, game, guest_id)})


@app.route("/api/local/<sport>/po/status", methods=["POST"])
def local_po_status(sport: str):
    guest_id = ((request.get_json(silent=True) or {}).get("guest_id") or "").strip()
    if sport not in LOCAL_PLAYOFF_CONFIG or not guest_id: return jsonify({"error": "supported sport and guest_id required"}), 400
    with LOCAL_PO_LOCK: return jsonify(_local_po_status(sport, guest_id))


@app.route("/api/local/<sport>/po/game", methods=["POST"])
def local_po_game(sport: str):
    data = request.get_json(silent=True) or {}; guest_id = (data.get("guest_id") or "").strip(); game_id = (data.get("game_id") or "").strip()
    with LOCAL_PO_LOCK:
        game = LOCAL_PO_GAMES.get(game_id)
        if not game or game["sport"] != sport: return jsonify({"error": "unknown game_id"}), 404
        if not _local_dr_authorized(game, guest_id): return jsonify({"error": "unauthorized"}), 403
        return jsonify(_local_po_state(game_id, game, guest_id))


@app.route("/api/local/<sport>/po/powerup", methods=["POST"])
def local_po_powerup(sport: str):
    data = request.get_json(silent=True) or {}; guest_id = (data.get("guest_id") or "").strip(); game_id = (data.get("game_id") or "").strip(); key = (data.get("powerup_key") or "").strip()
    with LOCAL_PO_LOCK:
        game = LOCAL_PO_GAMES.get(game_id)
        if not game or game["sport"] != sport: return jsonify({"error": "unknown game_id"}), 404
        if not _local_dr_authorized(game, guest_id): return jsonify({"error": "unauthorized"}), 403
        _local_dr_expire(game)
        side = "p1" if guest_id == game["p1_guest_id"] else "p2"
        config = LOCAL_PLAYOFF_CONFIG[sport]["powerups"]
        if not _playoff_powerups_unlocked(game["state"]):
            return jsonify({"error": "Powerups unlock after each player has played twice.", **_local_po_state(game_id, game, guest_id)}), 409
        if game["finished"] or game["turn_index"] != (0 if side == "p1" else 1) or game["turn_powerup_used"] or key not in config or key in game[f"{side}_powerup_used_keys"]:
            return jsonify({"error": "powerup is not available", **_local_po_state(game_id, game, guest_id)}), 409
        meta = config[key]; game[f"{side}_powerup_used_keys"].append(key); game["turn_powerup_used"] = True
        if meta["kind"] == "time":
            game["turn_seconds"] += meta["bonus_seconds"]; game["last_move"] = {"outcome": "powerup_activated", "powerup_key": key, "powerup_label": meta["label"], "message": f"{meta['label']} activated. +15 seconds."}
        elif meta["kind"] == "pressure":
            game["next_turn_seconds_override"] = QUICK_PITCH_TURN_SECONDS; game["last_move"] = {"outcome": "powerup_activated", "powerup_key": key, "powerup_label": meta["label"], "message": f"{meta['label']} activated. Opponent gets 10 seconds next turn."}
        else:
            game["turn_seconds"] += meta["bonus_seconds"]; game["active_turn_powerup"] = key; game["last_move"] = {"outcome": "powerup_activated", "powerup_key": key, "powerup_label": meta["label"], "message": f"{meta['label']} activated. Adds 5 seconds and an expanded link this turn."}
        return jsonify(_local_po_state(game_id, game, guest_id))


@app.route("/api/local/<sport>/po/move", methods=["POST"])
def local_po_move(sport: str):
    data = request.get_json(silent=True) or {}; guest_id = (data.get("guest_id") or "").strip(); game_id = (data.get("game_id") or "").strip(); raw = (data.get("raw") or "").strip(); player_id = (data.get("player_id") or "").strip() or None
    with LOCAL_PO_LOCK:
        game = LOCAL_PO_GAMES.get(game_id)
        if not game or game["sport"] != sport: return jsonify({"error": "unknown game_id"}), 404
        if not _local_dr_authorized(game, guest_id): return jsonify({"error": "unauthorized"}), 403
        _local_dr_expire(game)
        side = "p1" if guest_id == game["p1_guest_id"] else "p2"
        if game["finished"]: return jsonify(_local_po_state(game_id, game, guest_id))
        if game["turn_index"] != (0 if side == "p1" else 1): return jsonify({"error": "not your turn", **_local_po_state(game_id, game, guest_id)}), 409
        if (now_utc() - game["turn_started_at"]).total_seconds() < game["countdown_seconds"]: return jsonify(_local_po_state(game_id, game, guest_id))
        with _local_sport_conn() as conn:
            direct = validate_and_apply_move(game["state"], conn, player_id=player_id, raw_input=None if player_id else raw, track_strikes=True, sport=sport)
            payload = {"outcome": direct.outcome.value, "player_id": direct.player_id, "display_name": direct.display_name, "disambiguation": direct.disambiguation, "ambiguous_count": direct.ambiguous_count, "shared_seasons": [{"team_id": t, "season": s} for t, s in direct.shared_seasons], "burned_seasons": [{"team_id": t, "season": s} for t, s in direct.burned_seasons]}
            if direct.outcome == MoveOutcome.VALID:
                game["chain_link_meta"].append(None)
            else:
                power_move = _local_po_powerup_move(conn, game, raw, player_id)
                if power_move: payload = power_move
            if payload["outcome"] == "valid":
                key = game[f"{side}_win_condition_key"]
                condition = LOCAL_PLAYOFF_CONFIG[sport]["conditions"][key]
                if _playoff_win_conditions_unlocked(game["state"]):
                    increment = _local_po_condition_increment(conn, sport, key, payload["player_id"])
                    game["chain_win_condition_hits"].append(increment > 0)
                    game[f"{side}_win_progress"] += increment
                    target = condition["target"]
                    completed = game[f"{side}_win_progress"] >= target
                    game[f"{side}_win_completed"] = completed
                else:
                    increment = 0
                    target = condition["target"]
                    completed = False
                    _append_no_win_condition_hit(game, game["state"])
                payload.update({"win_condition_hit": increment > 0, "win_condition_label": condition["label"], "win_condition_progress": game[f"{side}_win_progress"], "win_condition_target": target, "win_condition_completed": completed})
                if completed:
                    game["finished"] = True; game["winner"] = game[side]
                else:
                    game["turn_index"] = 1 - game["turn_index"]; game["turn_started_at"] = now_utc(); game["countdown_seconds"] = 0.0
                    game["turn_seconds"] = game.get("next_turn_seconds_override") or APP_TURN_SECONDS; game["next_turn_seconds_override"] = None; game["active_turn_powerup"] = None; game["turn_powerup_used"] = False
        game["last_move"] = payload
        return jsonify(_local_po_state(game_id, game, guest_id))


def _local_po_postgame_response(sport: str, game_id: str, guest_id: str, action: str):
    game = LOCAL_PO_GAMES.get(game_id)
    if not game or game["sport"] != sport: return {"status": "gone"}
    if action == "leave" and not game["finished"]:
        game["finished"] = True; game["winner"] = game["p2"] if guest_id == game["p1_guest_id"] else game["p1"]; game["last_move"] = {"outcome": "forfeit"}
    return {"status": "gone"}


@app.route("/api/local/<sport>/po/leave_game", methods=["POST"])
def local_po_leave_game(sport: str):
    data = request.get_json(silent=True) or {}
    with LOCAL_PO_LOCK: return jsonify(_local_po_postgame_response(sport, (data.get("game_id") or "").strip(), (data.get("guest_id") or "").strip(), "leave"))


@app.route("/api/local/<sport>/po/rematch_request", methods=["POST"])
def local_po_rematch_request(sport: str):
    data = request.get_json(silent=True) or {}; guest_id = (data.get("guest_id") or "").strip(); game_id = (data.get("game_id") or "").strip()
    with LOCAL_PO_LOCK:
        game = LOCAL_PO_GAMES.get(game_id)
        if not game or game["sport"] != sport: return jsonify({"error": "unknown game_id"}), 404
        if not _local_dr_authorized(game, guest_id): return jsonify({"error": "unauthorized"}), 403
        if not game["finished"] or game.get("last_move", {}).get("outcome") == "forfeit": return jsonify({"error": "rematch unavailable"}), 400
        linked = LOCAL_PO_REMATCH_LINKS.get(game_id)
        if linked: return jsonify({"status": "matched", "game": _local_po_state(linked, LOCAL_PO_GAMES[linked], guest_id)})
        requests = LOCAL_PO_REMATCH_REQUESTS.setdefault(game_id, set()); requests.add(guest_id)
        if {game["p1_guest_id"], game["p2_guest_id"]} <= requests:
            first = {"guest_id": game["p1_guest_id"], "name": game["p1"]}; second = {"guest_id": game["p2_guest_id"], "name": game["p2"]}
            linked, new_game = _local_po_create_game(
                sport,
                first,
                second,
                _sport_online_rematch_preferences(game),
                first_guest_id=_sport_online_rematch_first_guest_id(game),
            )
            LOCAL_PO_REMATCH_LINKS[game_id] = linked
            return jsonify({"status": "matched", "game": _local_po_state(linked, new_game, guest_id)})
        return jsonify({"status": "waiting"})


@app.route("/api/local/<sport>/po/rematch_status", methods=["POST"])
def local_po_rematch_status(sport: str):
    data = request.get_json(silent=True) or {}; guest_id = (data.get("guest_id") or "").strip(); game_id = (data.get("game_id") or "").strip()
    with LOCAL_PO_LOCK:
        game = LOCAL_PO_GAMES.get(game_id)
        if not game or game["sport"] != sport: return jsonify({"error": "unknown game_id"}), 404
        linked = LOCAL_PO_REMATCH_LINKS.get(game_id)
        if linked: return jsonify({"status": "matched", "game": _local_po_state(linked, LOCAL_PO_GAMES[linked], guest_id)})
        other = game["p2_guest_id"] if guest_id == game["p1_guest_id"] else game["p1_guest_id"]
        if other in LOCAL_PO_POSTGAME_EXITS.get(game_id, set()): return jsonify({"status": "abandoned", "opponent_present": False})
        return jsonify({"status": "waiting", "opponent_present": True})


@app.route("/api/local/<sport>/po/postgame_leave", methods=["POST"])
def local_po_postgame_leave(sport: str):
    data = request.get_json(silent=True) or {}; guest_id = (data.get("guest_id") or "").strip(); game_id = (data.get("game_id") or "").strip()
    with LOCAL_PO_LOCK:
        game = LOCAL_PO_GAMES.get(game_id)
        if not game or game["sport"] != sport: return jsonify({"status": "gone"})
        LOCAL_PO_POSTGAME_EXITS.setdefault(game_id, set()).add(guest_id)
        LOCAL_PO_REMATCH_REQUESTS.setdefault(game_id, set()).discard(guest_id)
        other = game["p2_guest_id"] if guest_id == game["p1_guest_id"] else game["p1_guest_id"]
        if other in LOCAL_PO_REMATCH_REQUESTS.get(game_id, set()):
            LOCAL_PO_QUEUE[sport] = [row for row in LOCAL_PO_QUEUE[sport] if row["guest_id"] != other]
            LOCAL_PO_QUEUE[sport].append({
                "guest_id": other,
                "name": game["p2"] if other == game["p2_guest_id"] else game["p1"],
                "preference": _sport_online_rematch_preferences(game).get(other, "random"),
            })
        return jsonify({"status": "gone"})


@app.route("/api/local/<sport>/po/cancel_queue", methods=["POST"])
def local_po_cancel_queue(sport: str):
    guest_id = ((request.get_json(silent=True) or {}).get("guest_id") or "").strip()
    with LOCAL_PO_LOCK:
        if sport in LOCAL_PO_QUEUE: LOCAL_PO_QUEUE[sport][:] = [row for row in LOCAL_PO_QUEUE[sport] if row["guest_id"] != guest_id]
    return jsonify({"status": "idle"})


@app.route("/api/local/<sport>/po/cancel_challenge", methods=["POST"])
def local_po_cancel_challenge(sport: str):
    return jsonify({"status": "idle"})


# ----- Player autocomplete (used by MP + BP) -----

@app.route("/api/autocomplete")
def autocomplete():
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify([])
    nq = normalize(q)
    if not nq:
        return jsonify([])
    with db() as conn:
        rows = conn.execute(
            """SELECT player_id, display_name, debut_year, final_year, career_games FROM (
                  SELECT ps.player_id, ps.display_name, p.debut_year, p.final_year, ps.career_games
                    FROM players_searchable ps
                    JOIN players p ON p.player_id = ps.player_id
                   WHERE ps.search_key LIKE %s || '%%' AND COALESCE(p.final_year, 9999) >= 2000
                  UNION
                  SELECT ps.player_id, ps.display_name, p.debut_year, p.final_year, ps.career_games
                    FROM players_searchable ps
                    JOIN players p ON p.player_id = ps.player_id
                   WHERE ps.last_key LIKE %s || '%%' AND COALESCE(p.final_year, 9999) >= 2000
                  UNION
                  SELECT ps.player_id, ps.display_name, p.debut_year, p.final_year, ps.career_games
                    FROM players_searchable ps
                    JOIN players p ON p.player_id = ps.player_id
                    JOIN nickname_search ns ON ns.player_id = ps.player_id
                   WHERE ns.nickname_key LIKE %s || '%%' AND COALESCE(p.final_year, 9999) >= 2000
                ) AS u
                ORDER BY career_games DESC
                LIMIT 4""",
            (nq, nq, nq),
        ).fetchall()
    return jsonify([
        {
            "player_id": pid,
            "display_name": display_name,
            "debut_year": debut_year,
            "final_year": final_year,
            "career_games": career_games,
        }
        for pid, display_name, debut_year, final_year, career_games in rows
    ])


# ----- FR team-name autocomplete -----

@app.route("/api/fr/team_autocomplete")
def fr_team_autocomplete():
    ensure_static_caches()
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify([])
    game_id = (request.args.get("game_id") or "").strip()
    if game_id:
        with db() as conn:
            return jsonify(_film_review_autocomplete_options(conn, "baseball", game_id, q))
    prefix = [n for n in ALL_FR_TEAM_NAMES if n.lower().startswith(q)]
    sub = [
        n for n in ALL_FR_TEAM_NAMES
        if q in n.lower() and not n.lower().startswith(q)
    ]
    return jsonify((prefix + sub)[:6])


# ============================================================
# Division Rivalry (multiplayer)
# ============================================================

def dr_blob_from_state(state: GameState, p1: str, p2: str, turn_index: int,
                       turn_seconds: float, turn_started_at: datetime,
                       countdown_seconds: float, finished: bool = False,
                       owner_guest_id: str | None = None,
                       p1_guest_id: str | None = None,
                       p2_guest_id: str | None = None,
                       seed_player_id: str = DEFAULT_SEED,
                       result_saved: bool = False,
                       winner: str | None = None,
                       last_move: dict | None = None) -> dict:
    return {
        **serialize_state(state),
        "p1": p1,
        "p2": p2,
        "turn_index": turn_index,
        "turn_seconds": turn_seconds,
        "turn_started_at": turn_started_at.isoformat(),
        "countdown_seconds": countdown_seconds,
        "owner_guest_id": owner_guest_id,
        "p1_guest_id": p1_guest_id,
        "p2_guest_id": p2_guest_id,
        "seed_player_id": seed_player_id,
        "result_saved": result_saved,
        "finished": finished,
        "winner": winner,
        "last_move": last_move,
    }


def dr_state_dict(gid: str, blob: dict, state: GameState, conn=None) -> dict:
    started = datetime.fromisoformat(blob["turn_started_at"])
    elapsed = (now_utc() - started).total_seconds()
    countdown_left = max(0.0, blob["countdown_seconds"] - elapsed) \
        if not blob["finished"] else 0.0
    live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
    remaining = max(0.0, blob["turn_seconds"] - live_elapsed) \
        if not blob["finished"] else 0.0
    cards = _hydrate_player_cards(conn, list(state.chain)) if conn else None
    viewer_guest_id = blob.get("viewer_guest_id")
    p1_guest_id = blob.get("p1_guest_id")
    p2_guest_id = blob.get("p2_guest_id")
    your_side = (
        "p1" if viewer_guest_id and viewer_guest_id == p1_guest_id
        else "p2" if viewer_guest_id and viewer_guest_id == p2_guest_id
        else None
    )
    return {
        "game_id": gid,
        "current_player": {
            "id": state.current_player_id,
            "name": state.current_player_name,
        },
        "current_label": [blob["p1"], blob["p2"]][blob["turn_index"]],
        "p1": blob["p1"],
        "p2": blob["p2"],
        "p1_guest_id": p1_guest_id,
        "p2_guest_id": p2_guest_id,
        "viewer_guest_id": viewer_guest_id,
        "your_side": your_side,
        "your_name": blob["p1"] if your_side == "p1" else blob["p2"] if your_side == "p2" else None,
        "opponent_name": blob["p2"] if your_side == "p1" else blob["p1"] if your_side == "p2" else None,
        "your_turn": (
            (your_side == "p1" and blob["turn_index"] == 0) or
            (your_side == "p2" and blob["turn_index"] == 1)
        ) if your_side else False,
        "turn_index": blob["turn_index"],
        "turn_seconds": blob["turn_seconds"],
        "countdown_seconds_remaining": countdown_left,
        "remaining_seconds": remaining,
        "chain": chain_dict(state, cards=cards),
        "strikes": strikes_dict(state),
        "finished": blob["finished"],
        "winner": blob.get("winner"),
        "last_move": blob.get("last_move"),
    }


def po_blob_from_state(state: GameState, p1: str, p2: str, turn_index: int,
                       turn_seconds: float, turn_started_at: datetime,
                       countdown_seconds: float, finished: bool = False,
                       owner_guest_id: str | None = None,
                       p1_guest_id: str | None = None,
                       p2_guest_id: str | None = None,
                       seed_player_id: str = DEFAULT_SEED,
                       winner: str | None = None,
                       last_move: dict | None = None,
                       p1_win_condition_key: str | None = None,
                       p2_win_condition_key: str | None = None,
                       p1_win_condition_preference: str | None = None,
                       p2_win_condition_preference: str | None = None) -> dict:
    p1_condition = p1_win_condition_key or _random_playoff_win_condition()
    p2_condition = p2_win_condition_key or _random_playoff_win_condition()
    return {
        **serialize_state(state),
        "p1": p1,
        "p2": p2,
        "turn_index": turn_index,
        "default_turn_seconds": DEFAULT_PLAYOFF_TURN_SECONDS,
        "turn_seconds": turn_seconds,
        "turn_started_at": turn_started_at.isoformat(),
        "countdown_seconds": countdown_seconds,
        "owner_guest_id": owner_guest_id,
        "p1_guest_id": p1_guest_id,
        "p2_guest_id": p2_guest_id,
        "seed_player_id": seed_player_id,
        "finished": finished,
        "winner": winner,
        "last_move": last_move,
        "p1_powerup_key": None,
        "p2_powerup_key": None,
        "p1_powerup_used": False,
        "p2_powerup_used": False,
        "p1_powerup_keys": _all_playoff_powerups(),
        "p2_powerup_keys": _all_playoff_powerups(),
        "p1_powerup_used_keys": [],
        "p2_powerup_used_keys": [],
        "active_turn_powerup": None,
        "next_turn_seconds_override": None,
        "turn_powerup_used": False,
        "p1_win_condition_key": p1_condition,
        "p2_win_condition_key": p2_condition,
        "p1_win_condition_preference": p1_win_condition_preference or p1_condition,
        "p2_win_condition_preference": p2_win_condition_preference or p2_condition,
        "p1_win_progress": 0,
        "p2_win_progress": 0,
        "p1_win_completed": False,
        "p2_win_completed": False,
        "chain_win_condition_hits": [False],
        "chain_link_meta": [None],
    }


def po_state_dict(gid: str, blob: dict, state: GameState, conn=None) -> dict:
    started = datetime.fromisoformat(blob["turn_started_at"])
    elapsed = (now_utc() - started).total_seconds()
    countdown_left = max(0.0, blob["countdown_seconds"] - elapsed) if not blob["finished"] else 0.0
    live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
    remaining = max(0.0, blob["turn_seconds"] - live_elapsed) if not blob["finished"] else 0.0
    cards = _hydrate_player_cards(conn, list(state.chain)) if conn else None
    viewer_guest_id = blob.get("viewer_guest_id")
    p1_guest_id = blob.get("p1_guest_id")
    p2_guest_id = blob.get("p2_guest_id")
    your_side = (
        "p1" if viewer_guest_id and viewer_guest_id == p1_guest_id
        else "p2" if viewer_guest_id and viewer_guest_id == p2_guest_id
        else None
    )
    chain = chain_dict(state, cards=cards)
    link_meta = blob.get("chain_link_meta") or [None] * len(chain)
    win_hits = blob.get("chain_win_condition_hits") or [False] * len(chain)
    for i, player in enumerate(chain):
        player["link_meta_with_prev"] = link_meta[i] if i < len(link_meta) else None
        player["win_condition_hit"] = bool(win_hits[i]) if i < len(win_hits) else False
    return {
        "game_id": gid,
        "mode": "po",
        "current_player": {
            "id": state.current_player_id,
            "name": state.current_player_name,
        },
        "current_label": [blob["p1"], blob["p2"]][blob["turn_index"]],
        "p1": blob["p1"],
        "p2": blob["p2"],
        "p1_guest_id": p1_guest_id,
        "p2_guest_id": p2_guest_id,
        "viewer_guest_id": viewer_guest_id,
        "your_side": your_side,
        "your_name": blob["p1"] if your_side == "p1" else blob["p2"] if your_side == "p2" else None,
        "opponent_name": blob["p2"] if your_side == "p1" else blob["p1"] if your_side == "p2" else None,
        "your_turn": (
            (your_side == "p1" and blob["turn_index"] == 0) or
            (your_side == "p2" and blob["turn_index"] == 1)
        ) if your_side else False,
        "turn_index": blob["turn_index"],
        "turn_seconds": blob["turn_seconds"],
        "default_turn_seconds": blob["default_turn_seconds"],
        "countdown_seconds_remaining": countdown_left,
        "remaining_seconds": remaining,
        "chain": chain,
        "strikes": strikes_dict(state),
        "finished": blob["finished"],
        "winner": blob.get("winner"),
        "last_move": blob.get("last_move"),
        "powerups": _playoff_powerup_state(blob, viewer_guest_id),
        "win_conditions": _playoff_condition_progress_state(blob, viewer_guest_id),
    }


def _dr_authorize(blob: dict, guest_id: str) -> bool:
    return guest_id in {blob.get("p1_guest_id"), blob.get("p2_guest_id")}


def _dr_status_payload(conn, guest_id: str):
    row = conn.execute(
        """SELECT game_id::text, state, finished
             FROM dr_games
            WHERE NOT finished
              AND ((state->>'p1_guest_id') = %s OR (state->>'p2_guest_id') = %s)
            ORDER BY created_at DESC
            LIMIT 1""",
        (guest_id, guest_id),
    ).fetchone()
    if row:
        gid, blob, finished = row
        blob["finished"] = finished
        blob["viewer_guest_id"] = guest_id
        state = deserialize_state(blob)
        return {"status": "matched", "game": dr_state_dict(gid, blob, state, conn=conn)}
    qrow = conn.execute(
        "SELECT enqueued_at FROM dr_queue WHERE guest_id = %s",
        (guest_id,),
    ).fetchone()
    if qrow:
        return {
            "status": "waiting",
            "guest_id": guest_id,
            "enqueued_at": qrow[0].isoformat(),
        }
    return {"status": "idle"}


def _dr_create_online_game(conn, guest_a_id: str, name_a: str, guest_b_id: str, name_b: str):
    first_a = bool(secrets.randbelow(2) == 0)
    p1_guest_id, p1_name, p2_guest_id, p2_name = (
        (guest_a_id, name_a, guest_b_id, name_b) if first_a
        else (guest_b_id, name_b, guest_a_id, name_a)
    )
    engine_conn = PgEngineConn(conn)
    state = seed_game(engine_conn, DEFAULT_SEED)
    _record_player_usage(conn, DEFAULT_SEED, "dr")
    blob = dr_blob_from_state(
        state,
        p1=p1_name,
        p2=p2_name,
        turn_index=0,
        turn_seconds=APP_TURN_SECONDS,
        turn_started_at=now_utc(),
        countdown_seconds=OPENING_COUNTDOWN_SECONDS,
        owner_guest_id=p1_guest_id,
        p1_guest_id=p1_guest_id,
        p2_guest_id=p2_guest_id,
        seed_player_id=DEFAULT_SEED,
    )
    gid = _insert_game(conn, "dr_games", blob)
    return gid, blob, state


def _bp_daily_leaderboard(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            COALESCE(u.username, g.display_name, 'Guest') AS display_name,
            b.chain_length
        FROM bp_runs b
        LEFT JOIN guests g ON g.guest_id = b.owner_guest_id
        LEFT JOIN users u ON u.user_id = g.guest_id
        WHERE ((b.finished_at AT TIME ZONE 'America/Chicago')::date =
               (now() AT TIME ZONE 'America/Chicago')::date)
        ORDER BY b.chain_length DESC, b.finished_at ASC
        LIMIT 9
        """
    ).fetchall()
    return [
        {"display_name": display_name, "chain_length": chain_length}
        for display_name, chain_length in rows
    ]


def _curated_player_has_verified_headshot(conn, sport: str, player_id: str) -> bool:
    if not sport or not player_id:
        return False
    row = conn.execute(
        """SELECT 1 FROM player_headshots
            WHERE sport_id=%s AND player_id=%s AND status='verified'
            LIMIT 1""",
        (sport, player_id),
    ).fetchone()
    return bool(row)


def _manager_seed_for_day(conn, sport: str, puzzle_day: date | None = None) -> str:
    puzzle_day = puzzle_day or datetime.now(CENTRAL_TIME).date()
    cache_key = (sport, puzzle_day.isoformat())
    row = conn.execute(
        "SELECT player_id FROM manager_daily_starters WHERE sport_id=%s AND starter_date=%s",
        (sport, puzzle_day),
    ).fetchone()
    if row:
        if _curated_player_has_verified_headshot(conn, sport, row[0]):
            MANAGER_SEED_CACHE[cache_key] = row[0]
            return row[0]
        conn.execute(
            "DELETE FROM manager_daily_starters WHERE sport_id=%s AND starter_date=%s",
            (sport, puzzle_day),
        )
    cached = MANAGER_SEED_CACHE.get(cache_key)
    if cached and _curated_player_has_verified_headshot(conn, sport, cached):
        conn.execute(
            """INSERT INTO manager_daily_starters (sport_id, starter_date, player_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (sport_id, starter_date) DO NOTHING""",
            (sport, puzzle_day, cached),
        )
        return cached
    recent = [r[0] for r in conn.execute(
        """SELECT player_id FROM manager_daily_starters
            WHERE sport_id=%s AND starter_date < %s
            ORDER BY starter_date DESC LIMIT 21""",
        (sport, puzzle_day),
    ).fetchall()]
    if sport == "baseball":
        rows = conn.execute(
            """SELECT ps.player_id
                 FROM players_searchable ps
                 JOIN players p ON p.player_id=ps.player_id
                 JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=ps.player_id
                WHERE ps.career_games >= 650
                  AND p.final_year >= 2018
                  AND p.mlbam_id IS NOT NULL
                  AND h.status='verified'
                  AND NOT (ps.player_id = ANY(%s))
                ORDER BY ps.career_games DESC, ps.player_id
                LIMIT 500""",
            (recent,),
        ).fetchall()
        fallback = DEFAULT_SEED
    else:
        rows = conn.execute(
            """SELECT ps.player_id
                 FROM sport_players_searchable ps
                 JOIN sport_players p ON p.sport_id=ps.sport_id AND p.player_id=ps.player_id
                 JOIN player_headshots h ON h.sport_id=ps.sport_id AND h.player_id=ps.player_id
                WHERE ps.sport_id=%s
                  AND ps.career_games >= %s
                  AND p.final_year >= 2018
                  AND h.status='verified'
                  AND NOT (ps.player_id = ANY(%s))
                ORDER BY ps.career_games DESC, ps.player_id
                LIMIT 500""",
            (sport, {"basketball": 450, "hockey": 400, "football": 80}.get(sport, 100), recent),
        ).fetchall()
        image_rows = conn.execute(
            """SELECT ps.player_id
                 FROM sport_players_searchable ps
                 JOIN sport_players p ON p.sport_id=ps.sport_id AND p.player_id=ps.player_id
                 JOIN sport_player_images i ON i.sport_id=ps.sport_id AND i.player_id=ps.player_id
                 JOIN player_headshots h ON h.sport_id=ps.sport_id AND h.player_id=ps.player_id
                WHERE ps.sport_id=%s
                  AND ps.career_games >= %s
                  AND p.final_year >= 2018
                  AND h.status='verified'
                  AND NOT (ps.player_id = ANY(%s))
                ORDER BY ps.career_games DESC, ps.player_id
                LIMIT 500""",
            (sport, {"basketball": 450, "hockey": 400, "football": 80}.get(sport, 100), recent),
        ).fetchall()
        if image_rows:
            rows = image_rows
        fallback = LOCAL_SPORT_SEEDS.get(sport)
    candidates = [row[0] for row in rows]
    if not candidates:
        seed = fallback
        conn.execute(
            """INSERT INTO manager_daily_starters (sport_id, starter_date, player_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (sport_id, starter_date) DO NOTHING""",
            (sport, puzzle_day, seed),
        )
        row = conn.execute(
            "SELECT player_id FROM manager_daily_starters WHERE sport_id=%s AND starter_date=%s",
            (sport, puzzle_day),
        ).fetchone()
        if row:
            seed = row[0]
        MANAGER_SEED_CACHE[cache_key] = seed
        return seed
    digest = hashlib.sha256(f"{sport}:{puzzle_day.isoformat()}".encode("utf-8")).hexdigest()
    seed = candidates[int(digest[:12], 16) % len(candidates)]
    conn.execute(
        """INSERT INTO manager_daily_starters (sport_id, starter_date, player_id)
           VALUES (%s, %s, %s)
           ON CONFLICT (sport_id, starter_date) DO NOTHING""",
        (sport, puzzle_day, seed),
    )
    row = conn.execute(
        "SELECT player_id FROM manager_daily_starters WHERE sport_id=%s AND starter_date=%s",
        (sport, puzzle_day),
    ).fetchone()
    if row:
        seed = row[0]
    MANAGER_SEED_CACHE[cache_key] = seed
    return seed


def _manager_player_summary(conn, sport: str, player_id: str) -> dict:
    if not player_id:
        return {}
    if sport == "baseball":
        name_row = conn.execute(
            "SELECT display_name FROM players_searchable WHERE player_id=%s",
            (player_id,),
        ).fetchone()
        card = _hydrate_player_cards(conn, [player_id]).get(player_id, {})
    else:
        name_row = conn.execute(
            "SELECT display_name FROM sport_players_searchable WHERE sport_id=%s AND player_id=%s",
            (sport, player_id),
        ).fetchone()
        card = _sport_cards(conn, sport, [player_id]).get(player_id, {})
    return {
        "player_id": player_id,
        "name": name_row[0] if name_row else player_id,
        "headshot_url": card.get("headshot_url"),
    }


def _manager_player_brief(conn, sport: str, player_id: str) -> dict:
    if not player_id:
        return {}
    if sport == "baseball":
        row = conn.execute(
            """SELECT ps.display_name, p.mlbam_id
                 FROM players_searchable ps
                 JOIN players p ON p.player_id=ps.player_id
                WHERE ps.player_id=%s""",
            (player_id,),
        ).fetchone()
        if not row:
            return {"player_id": player_id, "name": player_id, "headshot_url": None}
        name, mlbam_id = row
        return {"player_id": player_id, "name": name, "headshot_url": HEADSHOT_URL.format(mlbam_id) if mlbam_id else None}
    row = conn.execute(
        """SELECT ps.display_name, i.source_url
             FROM sport_players_searchable ps
             LEFT JOIN sport_player_images i ON i.sport_id=ps.sport_id AND i.player_id=ps.player_id
            WHERE ps.sport_id=%s AND ps.player_id=%s""",
        (sport, player_id),
    ).fetchone()
    if not row:
        return {"player_id": player_id, "name": player_id, "headshot_url": None}
    name, image = row
    if not image and sport == "basketball":
        ext = player_id.split(":", 1)[-1]
        image = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{ext}.png"
    return {"player_id": player_id, "name": name, "headshot_url": image}


def _manager_bp_top_run(conn, sport: str, *, guest_id: str | None = None, today_only: bool = False) -> dict | None:
    filters = ["b.sport_id=%s"]
    params: list = [sport]
    if guest_id:
        filters.append("b.owner_guest_id=%s")
        params.append(guest_id)
    if today_only:
        filters.append("((b.finished_at AT TIME ZONE 'America/Chicago')::date = (now() AT TIME ZONE 'America/Chicago')::date)")
    if sport == "baseball":
        name_join = "LEFT JOIN players_searchable starter ON starter.player_id=b.seed_player_id"
        name_select = "starter.display_name"
    else:
        name_join = "LEFT JOIN sport_players_searchable starter ON starter.sport_id=b.sport_id AND starter.player_id=b.seed_player_id"
        name_select = "starter.display_name"
    row = conn.execute(
        f"""SELECT COALESCE(u.username, g.display_name, 'Guest') AS display_name,
                   b.chain_length, b.seed_player_id, {name_select} AS starter_name,
                   (b.finished_at AT TIME ZONE 'America/Chicago')::date AS run_date
              FROM bp_runs b
              LEFT JOIN guests g ON g.guest_id = b.owner_guest_id
              LEFT JOIN users u ON u.user_id = g.guest_id
              {name_join}
             WHERE {' AND '.join(filters)}
             ORDER BY b.chain_length DESC, b.finished_at ASC
             LIMIT 1""",
        tuple(params),
    ).fetchone()
    if not row:
        return None
    display_name, chain_length, seed_player_id, starter_name, run_date = row
    return {
        "display_name": display_name,
        "chain_length": chain_length,
        "date": run_date.isoformat() if run_date else None,
        "starter": {"player_id": seed_player_id, "name": starter_name or seed_player_id},
    }


def _manager_bp_daily_records(conn, sport: str, limit: int = 30) -> list[dict]:
    if sport == "baseball":
        name_join = "LEFT JOIN players_searchable starter ON starter.player_id=b.seed_player_id"
        name_select = "starter.display_name"
    else:
        name_join = "LEFT JOIN sport_players_searchable starter ON starter.sport_id=b.sport_id AND starter.player_id=b.seed_player_id"
        name_select = "starter.display_name"
    rows = conn.execute(
        """SELECT DISTINCT ON ((b.finished_at AT TIME ZONE 'America/Chicago')::date)
                  (b.finished_at AT TIME ZONE 'America/Chicago')::date AS run_date,
                  COALESCE(u.username, g.display_name, 'Guest') AS display_name,
                  b.chain_length, b.seed_player_id, {name_select} AS starter_name
             FROM bp_runs b
             LEFT JOIN guests g ON g.guest_id = b.owner_guest_id
             LEFT JOIN users u ON u.user_id = g.guest_id
             {name_join}
            WHERE b.sport_id=%s
              AND (b.finished_at AT TIME ZONE 'America/Chicago')::date >= %s
            ORDER BY (b.finished_at AT TIME ZONE 'America/Chicago')::date DESC,
                     b.chain_length DESC, b.finished_at ASC
            LIMIT %s""".format(name_select=name_select, name_join=name_join),
        (sport, FILM_REVIEW_EPOCH, limit),
    ).fetchall()
    return [
        {
            "date": run_date.isoformat() if run_date else None,
            "display_name": display_name,
            "chain_length": chain_length,
            "starter": {"player_id": seed_player_id, "name": starter_name or seed_player_id},
        }
        for run_date, display_name, chain_length, seed_player_id, starter_name in rows
    ]


@app.route("/api/manager/summary", methods=["POST"])
def manager_summary():
    ensure_runtime_schema()
    guest_id = ((request.get_json(silent=True) or {}).get("guest_id") or "").strip() or None
    today = datetime.now(CENTRAL_TIME).date()
    sports = ["baseball", "basketball", "hockey", "football"]
    with db() as conn:
        if guest_id and not conn.execute("SELECT 1 FROM guests WHERE guest_id=%s", (guest_id,)).fetchone():
            guest_id = None
        payload = {}
        for sport in sports:
            seed = _manager_seed_for_day(conn, sport, today)
            payload[sport] = {
                "starter": _manager_player_brief(conn, sport, seed),
                "own_all_time": _manager_bp_top_run(conn, sport, guest_id=guest_id),
                "own_today": _manager_bp_top_run(conn, sport, guest_id=guest_id, today_only=True),
                "global_all_time": _manager_bp_top_run(conn, sport),
                "global_today": _manager_bp_top_run(conn, sport, today_only=True),
                "records": _manager_bp_daily_records(conn, sport),
            }
    return jsonify({"date": today.isoformat(), "sports": payload})


@app.route("/api/manager/tiles", methods=["POST"])
def manager_tiles():
    ensure_runtime_schema()
    guest_id = ((request.get_json(silent=True) or {}).get("guest_id") or "").strip() or None
    today = datetime.now(CENTRAL_TIME).date()
    sports = ["baseball", "basketball", "hockey", "football"]
    with db() as conn:
        if guest_id and not conn.execute("SELECT 1 FROM guests WHERE guest_id=%s", (guest_id,)).fetchone():
            guest_id = None
        payload = {}
        for sport in sports:
            seed = _manager_seed_for_day(conn, sport, today)
            best = conn.execute(
                "SELECT COALESCE(MAX(chain_length), 0) FROM bp_runs WHERE owner_guest_id=%s AND sport_id=%s",
                (guest_id, sport),
            ).fetchone()[0] if guest_id else 0
            payload[sport] = {"starter": _manager_player_brief(conn, sport, seed), "own_best": best}
    return jsonify({"date": today.isoformat(), "sports": payload})


def _film_archive_days(conn, guest_id: str, sport: str) -> list[dict]:
    today = datetime.now(CENTRAL_TIME).date()
    rows = {(row[0], row[1] or ""): (row[2], row[3]) for row in conn.execute(
        """SELECT a.puzzle_date, a.unit, a.status, a.game_id::text
             FROM film_review_daily_attempts a
            WHERE owner_guest_id=%s AND sport_id=%s""",
        (guest_id, sport),
    ).fetchall()}
    days = []
    units = ["offense", "defense"] if sport == "football" else [""]
    for offset in range(min(60, max(1, (today - FILM_REVIEW_EPOCH).days + 1))):
        puzzle_day = today - timedelta(days=offset)
        if puzzle_day < FILM_REVIEW_EPOCH:
            break
        for unit in units:
            status, game_id = rows.get((puzzle_day, unit), ("unseen", None))
            days.append({"date": puzzle_day.isoformat(), "number": _film_review_number(puzzle_day),
                         "status": status, "game_id": game_id, "is_today": puzzle_day == today,
                         "unit": unit})
    return days


def _film_archive_days_from_rows(rows: dict, sport: str, today: date) -> list[dict]:
    days = []
    units = ["offense", "defense"] if sport == "football" else [""]
    for offset in range(min(60, max(1, (today - FILM_REVIEW_EPOCH).days + 1))):
        puzzle_day = today - timedelta(days=offset)
        if puzzle_day < FILM_REVIEW_EPOCH:
            break
        for unit in units:
            status, game_id = rows.get((sport, puzzle_day, unit), ("unseen", None))
            days.append({"date": puzzle_day.isoformat(), "number": _film_review_number(puzzle_day),
                         "status": status, "game_id": game_id, "is_today": puzzle_day == today,
                         "unit": unit})
    return days


def _film_streak(conn, guest_id: str, sport: str, unit: str = "") -> int:
    won_days = {
        row[0] for row in conn.execute(
            """SELECT DISTINCT puzzle_date
                 FROM film_review_daily_attempts
                WHERE owner_guest_id=%s AND sport_id=%s AND unit=%s AND status='won' AND official""",
            (guest_id, sport, unit),
        ).fetchall()
    }
    cursor = datetime.now(CENTRAL_TIME).date()
    if cursor not in won_days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor in won_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _film_streak_from_won_days(won_days: set[date], today: date) -> int:
    cursor = today
    if cursor not in won_days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor in won_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _film_current_streak(conn, guest_id: str | None, sport: str, unit: str = "") -> int:
    if not guest_id:
        return 0
    today = datetime.now(CENTRAL_TIME).date()
    row = conn.execute(
        """SELECT status FROM film_review_daily_attempts
            WHERE owner_guest_id=%s AND sport_id=%s AND puzzle_date=%s AND unit=%s AND official""",
        (guest_id, sport, today, unit),
    ).fetchone()
    if row and row[0] == "lost":
        return 0
    return _film_streak(conn, guest_id, sport, unit)


def _film_success_rate(conn, sport: str, puzzle_day: date, unit: str = "") -> dict:
    row = conn.execute(
        """SELECT
              COALESCE(SUM(CASE WHEN status='won' THEN 1 ELSE 0 END), 0),
              COALESCE(SUM(CASE WHEN status IN ('won','lost') THEN 1 ELSE 0 END), 0)
             FROM film_review_daily_attempts
            WHERE sport_id=%s AND puzzle_date=%s AND unit=%s""",
        (sport, puzzle_day, unit),
    ).fetchone()
    wins, finished = row if row else (0, 0)
    pct = round((wins / finished) * 100) if finished else 0
    return {"wins": int(wins), "finished": int(finished), "percent": pct}


def _build_film_preview_cards(conn, sport: str, deck: list[str]) -> list[dict]:
    deck = list(deck or [])
    if len(deck) < 2:
        return []
    cards = _hydrate_player_cards(conn, deck[:2]) if sport == "baseball" else _sport_cards(conn, sport, deck[:2])
    preview = []
    for player_id in deck[:2]:
        card = cards.get(player_id, {})
        name = card.get("display_name") or _sport_display_name(sport, player_id, card.get("name_first"), card.get("name_last"))
        preview.append({"player_id": player_id, "name": name, "headshot_url": card.get("headshot_url")})
    return preview


def _film_card_map(conn, sport: str, deck: list[str]) -> dict[str, dict]:
    if sport == "baseball":
        cards = _hydrate_player_cards(conn, deck)
        return {pid: fr_card_dict_from_card(pid, cards.get(pid) or player_card(pid)) for pid in deck}
    return {pid: _sport_fr_card(sport, pid, card) for pid, card in _sport_cards(conn, sport, deck).items()}


def _film_card_map_missing_team_stints(card_map: dict | None, deck: list[str]) -> bool:
    if not isinstance(card_map, dict):
        return True
    return any(
        not isinstance(card_map.get(pid), dict) or not card_map.get(pid, {}).get("team_stints")
        for pid in deck
    )


def _film_card_map_with_team_stints(conn, sport: str, deck: list[str], card_map: dict | None) -> dict[str, dict]:
    if _film_card_map_missing_team_stints(card_map, deck):
        fresh = _film_card_map(conn, sport, deck)
        merged = dict(card_map or {})
        for pid in deck:
            fresh_card = fresh.get(pid)
            if not fresh_card:
                continue
            current = dict(merged.get(pid) or {})
            if not current:
                merged[pid] = fresh_card
                continue
            if not current.get("team_stints"):
                current["team_stints"] = fresh_card.get("team_stints", [])
            if current.get("teams") is None:
                current["teams"] = []
            merged[pid] = current
        return merged
    return card_map or {}


def _film_shared_for_deck(conn, sport: str, deck: list[str]) -> list[list]:
    if len(deck) < 2:
        return []
    if sport == "baseball":
        return [
            [[team_id, season, team_name] for team_id, season, team_name in pair]
            for pair in _fr_compute_shared(conn, deck)
        ]
    return [_sport_fr_shared(conn, sport, deck[i], deck[i + 1]) for i in range(len(deck) - 1)]


def _film_puzzle_with_cached_payload(conn, sport: str, puzzle_day: date, unit: str | None, puzzle: dict) -> dict:
    preview = puzzle.get("preview_cards")
    card_map = puzzle.get("card_map")
    shared = puzzle.get("shared_per_pair")
    deck = list(puzzle.get("deck") or [])
    if (isinstance(preview, list) and len(preview) >= 2
            and isinstance(card_map, dict)
            and not _film_card_map_missing_team_stints(card_map, deck)
            and isinstance(shared, list)
            and len(shared) == max(0, len(deck) - 1)):
        return puzzle
    if len(deck) < 2:
        return puzzle
    enriched = dict(puzzle)
    if not isinstance(preview, list) or len(preview) < 2:
        enriched["preview_cards"] = _build_film_preview_cards(conn, sport, deck)
    enriched["card_map"] = _film_card_map_with_team_stints(conn, sport, deck, card_map)
    if not isinstance(shared, list) or len(shared) != len(deck) - 1:
        enriched["shared_per_pair"] = _film_shared_for_deck(conn, sport, deck)
    conn.execute(
        """UPDATE film_review_daily_puzzles
              SET puzzle=%s
            WHERE sport_id=%s AND puzzle_date=%s AND unit=%s""",
        (Jsonb(enriched), sport, puzzle_day, unit or ""),
    )
    return enriched


def _film_puzzle_with_preview(conn, sport: str, puzzle_day: date, unit: str | None, puzzle: dict) -> dict:
    return _film_puzzle_with_cached_payload(conn, sport, puzzle_day, unit, puzzle)


def _film_preview_cards(conn, sport: str, puzzle_day: date, unit: str = "") -> list[dict]:
    row = conn.execute(
        """SELECT puzzle FROM film_review_daily_puzzles
             WHERE sport_id=%s AND puzzle_date=%s AND unit=%s""",
        (sport, puzzle_day, unit or ""),
    ).fetchone()
    if not row:
        return []
    puzzle = _film_puzzle_with_preview(conn, sport, puzzle_day, unit or None, dict(row[0]))
    return list(puzzle.get("preview_cards") or [])


def _film_preview_map_for_day(conn, puzzle_day: date) -> dict[tuple[str, str], list[dict]]:
    rows = conn.execute(
        """SELECT sport_id, unit, puzzle
             FROM film_review_daily_puzzles
            WHERE puzzle_date=%s""",
        (puzzle_day,),
    ).fetchall()
    previews: dict[tuple[str, str], list[dict]] = {}
    for sport, unit, puzzle_blob in rows:
        puzzle = dict(puzzle_blob)
        preview = puzzle.get("preview_cards")
        if not isinstance(preview, list) or len(preview) < 2:
            puzzle = _film_puzzle_with_preview(conn, sport, puzzle_day, unit or None, puzzle)
            preview = puzzle.get("preview_cards")
        for card in preview or []:
            player_id = card.get("player_id")
            if player_id:
                card["name"] = _sport_display_name(sport, player_id, fallback=card.get("name"))
        previews[(sport, unit or "")] = list(preview or [])
    return previews


def _ensure_daily_film_puzzles(conn, puzzle_day: date | None = None) -> dict[str, list[str]]:
    puzzle_day = puzzle_day or datetime.now(CENTRAL_TIME).date()
    generated: dict[str, list[str]] = {}
    for sport in ("baseball", "basketball", "hockey", "football"):
        units = ("offense", "defense") if sport == "football" else ("",)
        for unit in units:
            unit_key = unit or "default"
            try:
                if sport == "baseball":
                    _daily_film_review_puzzle(
                        conn, sport, puzzle_day, None,
                        lambda puzzle_day=puzzle_day: _build_film_review_puzzle_with_history(conn, "baseball", puzzle_day, None),
                    )
                else:
                    _daily_film_review_puzzle(
                        conn, sport, puzzle_day, unit or None,
                        lambda sport=sport, unit=unit, puzzle_day=puzzle_day:
                            _build_film_review_puzzle_with_history(conn, sport, puzzle_day, unit or None),
                    )
                generated.setdefault(sport, []).append(unit_key)
            except Exception as error:
                app.logger.warning(
                    "Could not prebuild Film Review puzzle: sport=%s date=%s unit=%s error=%s",
                    sport, puzzle_day.isoformat(), unit_key, error,
                )
    return generated


def _daily_film_puzzles_ready(conn, puzzle_day: date) -> bool:
    rows = conn.execute(
        """SELECT sport_id, unit FROM film_review_daily_puzzles
            WHERE puzzle_date=%s""",
        (puzzle_day,),
    ).fetchall()
    found = {(sport_id, unit or "") for sport_id, unit in rows}
    expected = {
        ("baseball", ""),
        ("basketball", ""),
        ("hockey", ""),
        ("football", "offense"),
        ("football", "defense"),
    }
    return expected.issubset(found)


@app.route("/api/film/archive_summary", methods=["POST"])
def film_archive_summary():
    ensure_runtime_schema()
    guest_id = ((request.get_json(silent=True) or {}).get("guest_id") or "").strip()
    if not _valid_uuid_text(guest_id):
        guest_id = None
    sports = ["baseball", "basketball", "hockey", "football"]
    today = datetime.now(CENTRAL_TIME).date()
    with db() as conn:
        if not _daily_film_puzzles_ready(conn, today):
            _ensure_daily_film_puzzles(conn, today)
        preview_map = _film_preview_map_for_day(conn, today)
        attempt_rows = {}
        if guest_id:
            attempt_rows = {
                (sport_id, puzzle_date, unit or ""): (status, game_id)
                for sport_id, puzzle_date, unit, status, game_id in conn.execute(
                    """SELECT sport_id, puzzle_date, unit, status, game_id::text
                         FROM film_review_daily_attempts
                        WHERE owner_guest_id=%s""",
                    (guest_id,),
                ).fetchall()
            }
        won_rows: dict[tuple[str, str], set[date]] = {}
        if guest_id:
            for sport_id, unit, puzzle_date in conn.execute(
                """SELECT sport_id, unit, puzzle_date
                     FROM film_review_daily_attempts
                    WHERE owner_guest_id=%s AND status='won' AND official""",
                (guest_id,),
            ).fetchall():
                won_rows.setdefault((sport_id, unit or ""), set()).add(puzzle_date)
        payload = {}
        for sport in sports:
            units = ["offense", "defense"] if sport == "football" else [""]
            unit_payload = {
                unit or "default": {
                    "preview": preview_map.get((sport, unit), []),
                    "streak": _film_streak_from_won_days(won_rows.get((sport, unit), set()), today),
                }
                for unit in units
            }
            payload[sport] = {
                "days": _film_archive_days_from_rows(attempt_rows, sport, today),
                "streak": max((entry["streak"] for entry in unit_payload.values()), default=0),
                "today": unit_payload,
            }
        return jsonify({"sports": payload})


@app.route("/api/film/previews", methods=["POST"])
def film_previews():
    ensure_runtime_schema()
    today = datetime.now(CENTRAL_TIME).date()
    with db() as conn:
        if not _daily_film_puzzles_ready(conn, today):
            _ensure_daily_film_puzzles(conn, today)
        preview_map = _film_preview_map_for_day(conn, today)
        payload = {}
        for sport in ("baseball", "basketball", "hockey", "football"):
            units = ("offense", "defense") if sport == "football" else ("",)
            payload[sport] = {unit or "default": preview_map.get((sport, unit), []) for unit in units}
    return jsonify({"previews": payload})


@app.route("/api/cron/generate-film-review", methods=["GET", "POST"])
def cron_generate_film_review():
    ensure_runtime_schema()
    expected = os.environ.get("CRON_SECRET", "")
    if expected:
        supplied = request.headers.get("Authorization", "")
        if supplied != f"Bearer {expected}" and request.args.get("token") != expected:
            return jsonify({"error": "unauthorized"}), 401
    today = datetime.now(CENTRAL_TIME).date()
    try:
        days = max(1, min(60, int(request.args.get("days") or 1)))
    except ValueError:
        days = 1
    with db() as conn:
        generated = {}
        for offset in range(days):
            puzzle_day = today - timedelta(days=offset)
            if puzzle_day < FILM_REVIEW_EPOCH:
                break
            generated[puzzle_day.isoformat()] = _ensure_daily_film_puzzles(conn, puzzle_day)
    return jsonify({"date": today.isoformat(), "days": days, "generated": generated})


@app.route("/api/dr/queue", methods=["POST"])
def dr_queue():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    avoid_guest_id = (data.get("avoid_guest_id") or "").strip() or None
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM guests WHERE guest_id = %s",
            (guest_id,),
        ).fetchone()
        if not exists:
            return jsonify({"error": "unknown guest_id"}), 404
        display_name = _guest_label(conn, guest_id)

        existing = _dr_status_payload(conn, guest_id)
        if existing["status"] == "matched":
            return jsonify(existing)

        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(4411001)")
            opp = conn.execute(
                """SELECT guest_id::text, display_name
                     FROM dr_queue
                    WHERE guest_id <> %s
                      AND (avoid_guest_id IS NULL OR avoid_guest_id <> CAST(%s AS uuid))
                      AND (CAST(%s AS uuid) IS NULL OR guest_id <> CAST(%s AS uuid))
                    ORDER BY enqueued_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED""",
                (guest_id, guest_id, avoid_guest_id, avoid_guest_id),
            ).fetchone()
            if opp:
                opp_guest_id, opp_name = opp
                conn.execute(
                    "DELETE FROM dr_queue WHERE guest_id IN (%s, %s)",
                    (guest_id, opp_guest_id),
                )
                gid, blob, state = _dr_create_online_game(
                    conn, opp_guest_id, opp_name, guest_id, display_name
                )
                blob["viewer_guest_id"] = guest_id
                return jsonify({
                    "status": "matched",
                    "game": dr_state_dict(gid, blob, state, conn=conn),
                })

            conn.execute(
                """INSERT INTO dr_queue (guest_id, display_name, avoid_guest_id, enqueued_at)
                   VALUES (%s, %s, %s, now())
                   ON CONFLICT (guest_id) DO UPDATE
                   SET display_name = EXCLUDED.display_name,
                       avoid_guest_id = EXCLUDED.avoid_guest_id,
                       enqueued_at = now()""",
                (guest_id, display_name, avoid_guest_id),
            )
        return jsonify(_dr_status_payload(conn, guest_id))


@app.route("/api/dr/status", methods=["POST"])
def dr_status():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        return jsonify(_dr_status_payload(conn, guest_id))


@app.route("/api/dr/game", methods=["POST"])
def dr_game():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    gid = (data.get("game_id") or "").strip()
    if not guest_id or not gid:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, state = _load_game(conn, "dr_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not _dr_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        blob["viewer_guest_id"] = guest_id
        return jsonify(dr_state_dict(gid, blob, state, conn=conn))


@app.route("/api/dr/rematch_request", methods=["POST"])
def dr_rematch_request():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    gid = (data.get("game_id") or "").strip()
    if not guest_id or not gid:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, state = _load_game(conn, "dr_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not _dr_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        if not blob["finished"]:
            return jsonify({"error": "game not finished"}), 400
        if blob.get("last_move", {}).get("outcome") == "forfeit":
            return jsonify({"error": "rematch unavailable after forfeit"}), 400

        link = conn.execute(
            "SELECT new_game_id::text FROM dr_rematch_links WHERE original_game_id = %s",
            (gid,),
        ).fetchone()
        if link:
            new_gid = link[0]
            new_blob, new_state = _load_game(conn, "dr_games", new_gid)
            if new_blob:
                new_blob["viewer_guest_id"] = guest_id
                return jsonify({
                    "status": "matched",
                    "game": dr_state_dict(new_gid, new_blob, new_state, conn=conn),
                })

        conn.execute(
            """INSERT INTO dr_rematches (original_game_id, requester_guest_id)
               VALUES (%s, %s)
               ON CONFLICT DO NOTHING""",
            (gid, guest_id),
        )
        requesters = conn.execute(
            "SELECT requester_guest_id::text FROM dr_rematches WHERE original_game_id = %s",
            (gid,),
        ).fetchall()
        requested_ids = {r[0] for r in requesters}
        if {blob.get("p1_guest_id"), blob.get("p2_guest_id")} <= requested_ids:
            new_gid, new_blob, new_state = _dr_create_online_game(
                conn,
                blob.get("p1_guest_id"), blob.get("p1"),
                blob.get("p2_guest_id"), blob.get("p2"),
            )
            conn.execute(
                """INSERT INTO dr_rematch_links (original_game_id, new_game_id)
                   VALUES (%s, %s)
                   ON CONFLICT (original_game_id) DO NOTHING""",
                (gid, new_gid),
            )
            new_blob["viewer_guest_id"] = guest_id
            return jsonify({
                "status": "matched",
                "game": dr_state_dict(new_gid, new_blob, new_state, conn=conn),
            })
        return jsonify({"status": "waiting"})


@app.route("/api/dr/rematch_status", methods=["POST"])
def dr_rematch_status():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    gid = (data.get("game_id") or "").strip()
    if not guest_id or not gid:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, state = _load_game(conn, "dr_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not _dr_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        link = conn.execute(
            "SELECT new_game_id::text FROM dr_rematch_links WHERE original_game_id = %s",
            (gid,),
        ).fetchone()
        if link:
            new_gid = link[0]
            new_blob, new_state = _load_game(conn, "dr_games", new_gid)
            if new_blob:
                new_blob["viewer_guest_id"] = guest_id
                return jsonify({
                    "status": "matched",
                    "game": dr_state_dict(new_gid, new_blob, new_state, conn=conn),
                })
        requesters = {
            r[0] for r in conn.execute(
                "SELECT requester_guest_id::text FROM dr_rematches WHERE original_game_id = %s",
                (gid,),
            ).fetchall()
        }
        self_in_queue = conn.execute(
            "SELECT 1 FROM dr_queue WHERE guest_id = %s",
            (guest_id,),
        ).fetchone()
        if self_in_queue:
            return jsonify({
                "status": "requeued",
                "you_requested": guest_id in requesters,
                "opponent_requested": False,
                "opponent_present": False,
                "rematch_available": False,
            })
        other_guest_id = blob.get("p2_guest_id") if guest_id == blob.get("p1_guest_id") else blob.get("p1_guest_id")
        exited = {
            r[0] for r in conn.execute(
                "SELECT guest_id::text FROM dr_postgame_exits WHERE original_game_id = %s",
                (gid,),
            ).fetchall()
        }
        if other_guest_id in exited:
            return jsonify({
                "status": "abandoned",
                "you_requested": guest_id in requesters,
                "opponent_requested": False,
                "opponent_present": False,
                "rematch_available": False,
            })
        other_in_queue = conn.execute(
            "SELECT 1 FROM dr_queue WHERE guest_id = %s",
            (other_guest_id,),
        ).fetchone()
        other_in_other_game = conn.execute(
            """SELECT 1
                 FROM dr_games
                WHERE NOT finished
                  AND game_id <> %s
                  AND ((state->>'p1_guest_id') = %s OR (state->>'p2_guest_id') = %s)
                LIMIT 1""",
            (gid, other_guest_id, other_guest_id),
        ).fetchone()
        other_hosting_invite = conn.execute(
            """SELECT 1 FROM dr_invites
                 WHERE host_guest_id = %s
                   AND claimed_at IS NULL
                   AND expires_at > now()
                 LIMIT 1""",
            (other_guest_id,),
        ).fetchone()
        if other_in_queue or other_in_other_game or other_hosting_invite:
            return jsonify({
                "status": "abandoned",
                "you_requested": guest_id in requesters,
                "opponent_requested": False,
                "opponent_present": False,
                "rematch_available": False,
            })
        return jsonify({
            "status": "waiting",
            "you_requested": guest_id in requesters,
            "opponent_requested": other_guest_id in requesters,
            "opponent_present": True,
            "rematch_available": blob.get("last_move", {}).get("outcome") != "forfeit",
        })


@app.route("/api/dr/postgame_leave", methods=["POST"])
def dr_postgame_leave():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    game_id = (data.get("game_id") or "").strip()
    if not guest_id or not game_id:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, _state = _load_game(conn, "dr_games", game_id)
        if not blob:
            return jsonify({"status": "gone"})
        if not _dr_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        conn.execute(
            """INSERT INTO dr_postgame_exits (original_game_id, guest_id)
               VALUES (%s, %s)
               ON CONFLICT DO NOTHING""",
            (game_id, guest_id),
        )
        conn.execute(
            "DELETE FROM dr_rematches WHERE original_game_id = %s AND requester_guest_id = %s",
            (game_id, guest_id),
        )
        if blob.get("finished"):
            other_guest_id = (
                blob.get("p2_guest_id") if guest_id == blob.get("p1_guest_id")
                else blob.get("p1_guest_id")
            )
            if other_guest_id:
                other_requested = conn.execute(
                    """SELECT 1
                         FROM dr_rematches
                        WHERE original_game_id = %s
                          AND requester_guest_id = %s""",
                    (game_id, other_guest_id),
                ).fetchone()
                if other_requested:
                    conn.execute(
                        """INSERT INTO dr_queue (guest_id, display_name, avoid_guest_id, enqueued_at)
                           VALUES (%s, %s, %s, now())
                           ON CONFLICT (guest_id) DO UPDATE
                           SET display_name = EXCLUDED.display_name,
                               avoid_guest_id = EXCLUDED.avoid_guest_id,
                               enqueued_at = now()""",
                        (other_guest_id, _guest_label(conn, other_guest_id), guest_id),
                    )
    return jsonify({"status": "gone"})


@app.route("/api/dr/create_challenge", methods=["POST"])
def dr_create_challenge():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM guests WHERE guest_id = %s",
            (guest_id,),
        ).fetchone()
        if not exists:
            return jsonify({"error": "unknown guest_id"}), 404
        conn.execute("DELETE FROM dr_queue WHERE guest_id = %s", (guest_id,))
        conn.execute(
            "DELETE FROM dr_invites WHERE host_guest_id = %s AND claimed_at IS NULL",
            (guest_id,),
        )
        code = secrets.token_hex(3).upper()
        conn.execute(
            """INSERT INTO dr_invites (code, host_guest_id, host_name)
               VALUES (%s, %s, %s)""",
            (code, guest_id, _guest_label(conn, guest_id)),
        )
    return jsonify({"code": code})


@app.route("/api/dr/join_challenge", methods=["POST"])
def dr_join_challenge():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    code = (data.get("code") or "").strip().upper()
    if not guest_id or not code:
        return jsonify({"error": "guest_id and code required"}), 400
    with db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM guests WHERE guest_id = %s",
            (guest_id,),
        ).fetchone()
        if not exists:
            return jsonify({"error": "unknown guest_id"}), 404
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(4411002)")
            row = conn.execute(
                """SELECT host_guest_id::text, host_name
                     FROM dr_invites
                    WHERE code = %s
                      AND claimed_at IS NULL
                      AND expires_at > now()
                    FOR UPDATE""",
                (code,),
            ).fetchone()
            if not row:
                return jsonify({"error": "challenge not found"}), 404
            host_guest_id, host_name = row
            if host_guest_id == guest_id:
                return jsonify({"error": "cannot join your own code"}), 400
            gid, blob, state = _dr_create_online_game(
                conn, host_guest_id, host_name, guest_id, _guest_label(conn, guest_id)
            )
            conn.execute(
                "UPDATE dr_invites SET claimed_at = now() WHERE code = %s",
                (code,),
            )
            blob["viewer_guest_id"] = guest_id
            return jsonify({
                "status": "matched",
                "game": dr_state_dict(gid, blob, state, conn=conn),
            })


@app.route("/api/dr/cancel_queue", methods=["POST"])
def dr_cancel_queue():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        conn.execute("DELETE FROM dr_queue WHERE guest_id = %s", (guest_id,))
    return jsonify({"status": "idle"})


@app.route("/api/dr/cancel_challenge", methods=["POST"])
def dr_cancel_challenge():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        conn.execute(
            "DELETE FROM dr_invites WHERE host_guest_id = %s AND claimed_at IS NULL",
            (guest_id,),
        )
    return jsonify({"status": "idle"})


@app.route("/api/dr/leave_game", methods=["POST"])
def dr_leave_game():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    game_id = (data.get("game_id") or "").strip()
    if not guest_id or not game_id:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, state = _load_game(conn, "dr_games", game_id)
        if not blob:
            return jsonify({"status": "gone"})
        if not _dr_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        if not blob["finished"]:
            blob["finished"] = True
            blob["winner"] = blob.get("p2") if guest_id == blob.get("p1_guest_id") else blob.get("p1")
            blob["last_move"] = {"outcome": "forfeit"}
            _save_dr_result(conn, blob, game_id)
            _save_game(conn, "dr_games", game_id, blob)
    return jsonify({"status": "gone"})


@app.route("/api/new_game", methods=["POST"])
def new_game():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    p1 = (data.get("p1") or "Player 1").strip()
    p2 = (data.get("p2") or "Player 2").strip()
    seed = data.get("seed") or DEFAULT_SEED
    guest_id = (data.get("guest_id") or "").strip() or None
    turn_seconds = float(data.get("turn_seconds") or APP_TURN_SECONDS)
    with db() as conn:
        if guest_id:
            row = conn.execute(
                "SELECT 1 FROM guests WHERE guest_id = %s",
                (guest_id,),
            ).fetchone()
            if not row:
                guest_id = None
        engine_conn = PgEngineConn(conn)
        try:
            state = seed_game(engine_conn, seed)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        _record_player_usage(conn, seed, "dr")
        blob = dr_blob_from_state(
            state, p1, p2,
            turn_index=0,
            turn_seconds=turn_seconds,
            turn_started_at=now_utc(),
            countdown_seconds=OPENING_COUNTDOWN_SECONDS,
            owner_guest_id=guest_id,
            seed_player_id=seed,
        )
        gid = _insert_game(conn, "dr_games", blob)
        return jsonify(dr_state_dict(gid, blob, state, conn=conn))


@app.route("/api/move", methods=["POST"])
def move():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    guest_id = (data.get("guest_id") or "").strip()
    raw = (data.get("raw") or "").strip()
    player_id = (data.get("player_id") or "").strip() or None

    with db() as conn:
        blob, state = _load_game(conn, "dr_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not guest_id or not _dr_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        blob["viewer_guest_id"] = guest_id
        if blob["finished"]:
            return jsonify(dr_state_dict(gid, blob, state, conn=conn))
        expected_turn_guest = [blob.get("p1_guest_id"), blob.get("p2_guest_id")][blob["turn_index"]]
        if guest_id != expected_turn_guest:
            return jsonify({"error": "not your turn", **dr_state_dict(gid, blob, state, conn=conn)}), 409

        started = datetime.fromisoformat(blob["turn_started_at"])
        elapsed = (now_utc() - started).total_seconds()
        live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
        if not _move_submitted_in_time(data, live_elapsed, blob["turn_seconds"]):
            blob["finished"] = True
            blob["winner"] = [blob["p2"], blob["p1"]][blob["turn_index"]]
            blob["last_move"] = {"outcome": "timeout"}
            _save_dr_result(conn, blob, gid)
            _save_game(conn, "dr_games", gid, blob)
            return jsonify(dr_state_dict(gid, blob, state, conn=conn))

        if not raw and not player_id:
            blob["last_move"] = None
            _save_game(conn, "dr_games", gid, blob)
            return jsonify(dr_state_dict(gid, blob, state, conn=conn))

        engine_conn = PgEngineConn(conn)
        if player_id:
            result = validate_and_apply_move(state, engine_conn, player_id=player_id)
        else:
            result = validate_and_apply_move(state, engine_conn, raw)

        blob.update(serialize_state(state))
        blob["last_move"] = result_to_dict(result)
        if result.outcome == MoveOutcome.VALID:
            if result.player_id:
                _record_player_usage(conn, result.player_id, "dr")
            blob["turn_index"] = 1 - blob["turn_index"]
            blob["turn_started_at"] = now_utc().isoformat()
            blob["countdown_seconds"] = 0.0
        _save_game(conn, "dr_games", gid, blob)
        return jsonify(dr_state_dict(gid, blob, state, conn=conn))


@app.route("/api/timeout", methods=["POST"])
def timeout():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    guest_id = (data.get("guest_id") or "").strip()
    with db() as conn:
        blob, state = _load_game(conn, "dr_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not guest_id or not _dr_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        blob["viewer_guest_id"] = guest_id
        if blob["finished"]:
            return jsonify(dr_state_dict(gid, blob, state, conn=conn))
        started = datetime.fromisoformat(blob["turn_started_at"])
        elapsed = (now_utc() - started).total_seconds()
        live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
        if live_elapsed < blob["turn_seconds"] - 0.25:
            return jsonify(dr_state_dict(gid, blob, state, conn=conn))
        blob["finished"] = True
        blob["winner"] = [blob["p2"], blob["p1"]][blob["turn_index"]]
        blob["last_move"] = {"outcome": "timeout"}
        _save_dr_result(conn, blob, gid)
        _save_game(conn, "dr_games", gid, blob)
        return jsonify(dr_state_dict(gid, blob, state, conn=conn))


# ============================================================
# Playoffs (multiplayer with powerups)
# ============================================================

def _po_authorize(blob: dict, guest_id: str) -> bool:
    return guest_id in {blob.get("p1_guest_id"), blob.get("p2_guest_id")}


def _po_status_payload(conn, guest_id: str):
    row = conn.execute(
        """SELECT game_id::text, state, finished
             FROM po_games
            WHERE NOT finished
              AND ((state->>'p1_guest_id') = %s OR (state->>'p2_guest_id') = %s)
            ORDER BY created_at DESC
            LIMIT 1""",
        (guest_id, guest_id),
    ).fetchone()
    if row:
        gid, blob, finished = row
        blob["finished"] = finished
        blob["viewer_guest_id"] = guest_id
        state = deserialize_state(blob)
        return {"status": "matched", "game": po_state_dict(gid, blob, state, conn=conn)}
    qrow = conn.execute(
        "SELECT enqueued_at FROM po_queue WHERE guest_id = %s",
        (guest_id,),
    ).fetchone()
    if qrow:
        return {
            "status": "waiting",
            "guest_id": guest_id,
            "enqueued_at": qrow[0].isoformat(),
        }
    return {"status": "idle"}


def _po_create_online_game(conn, guest_a_id: str, name_a: str, guest_b_id: str, name_b: str,
                           preferences: dict[str, str] | None = None,
                           first_guest_id: str | None = None):
    if first_guest_id == guest_a_id:
        p1_guest_id, p1_name, p2_guest_id, p2_name = guest_a_id, name_a, guest_b_id, name_b
    elif first_guest_id == guest_b_id:
        p1_guest_id, p1_name, p2_guest_id, p2_name = guest_b_id, name_b, guest_a_id, name_a
    else:
        first_a = bool(secrets.randbelow(2) == 0)
        p1_guest_id, p1_name, p2_guest_id, p2_name = (
            (guest_a_id, name_a, guest_b_id, name_b) if first_a
            else (guest_b_id, name_b, guest_a_id, name_a)
        )
    engine_conn = PgEngineConn(conn)
    state = seed_game(engine_conn, DEFAULT_SEED)
    _record_player_usage(conn, DEFAULT_SEED, "dr")
    preferences = preferences or {}
    def selected(guest_id: str) -> tuple[str, str]:
        preference = preferences.get(guest_id)
        if preference in PLAYOFF_WIN_CONDITIONS:
            return preference, preference
        if preference == "random":
            return _random_playoff_condition_for_guest(conn, guest_id, "baseball", PLAYOFF_WIN_CONDITIONS), "random"
        key = _playoff_condition_for_guest(conn, guest_id)
        row = conn.execute(
            "SELECT playoff_win_condition_preference FROM guests WHERE guest_id = %s",
            (guest_id,),
        ).fetchone()
        return key, _normalized_playoff_preference(row[0] if row else None)
    p1_condition, p1_preference = selected(p1_guest_id)
    p2_condition, p2_preference = selected(p2_guest_id)
    blob = po_blob_from_state(
        state,
        p1=p1_name,
        p2=p2_name,
        turn_index=0,
        turn_seconds=DEFAULT_PLAYOFF_TURN_SECONDS,
        turn_started_at=now_utc(),
        countdown_seconds=OPENING_COUNTDOWN_SECONDS,
        owner_guest_id=p1_guest_id,
        p1_guest_id=p1_guest_id,
        p2_guest_id=p2_guest_id,
        seed_player_id=DEFAULT_SEED,
        p1_win_condition_key=p1_condition,
        p2_win_condition_key=p2_condition,
        p1_win_condition_preference=p1_preference,
        p2_win_condition_preference=p2_preference,
    )
    gid = _insert_game(conn, "po_games", blob)
    return gid, blob, state


def _forfeit_active_po_games(conn, guest_id: str):
    rows = conn.execute(
        """SELECT game_id::text, state, finished
             FROM po_games
            WHERE NOT finished
              AND ((state->>'p1_guest_id') = %s OR (state->>'p2_guest_id') = %s)""",
        (guest_id, guest_id),
    ).fetchall()
    for game_id, blob, finished in rows:
        blob["finished"] = finished
        if blob.get("finished"):
            continue
        blob["finished"] = True
        blob["winner"] = blob.get("p2") if guest_id == blob.get("p1_guest_id") else blob.get("p1")
        blob["last_move"] = {"outcome": "forfeit"}
        _save_game(conn, "po_games", game_id, blob)


@app.route("/api/po/queue", methods=["POST"])
def po_queue():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    avoid_guest_id = (data.get("avoid_guest_id") or "").strip() or None
    requested_preference = data.get("win_condition_preference")
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM guests WHERE guest_id = %s", (guest_id,)).fetchone()
        if not exists:
            return jsonify({"error": "unknown guest_id"}), 404
        if requested_preference is not None:
            _save_playoff_preference(conn, guest_id, requested_preference)
        display_name = _guest_label(conn, guest_id)
        existing = _po_status_payload(conn, guest_id)
        if existing["status"] == "matched":
            return jsonify(existing)
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(4411002)")
            opp = conn.execute(
                """SELECT guest_id::text, display_name
                     FROM po_queue
                    WHERE guest_id <> %s
                      AND (avoid_guest_id IS NULL OR avoid_guest_id <> CAST(%s AS uuid))
                      AND (CAST(%s AS uuid) IS NULL OR guest_id <> CAST(%s AS uuid))
                    ORDER BY enqueued_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED""",
                (guest_id, guest_id, avoid_guest_id, avoid_guest_id),
            ).fetchone()
            if opp:
                opp_guest_id, opp_name = opp
                conn.execute("DELETE FROM po_queue WHERE guest_id IN (%s, %s)", (guest_id, opp_guest_id))
                gid, blob, state = _po_create_online_game(conn, opp_guest_id, opp_name, guest_id, display_name)
                blob["viewer_guest_id"] = guest_id
                return jsonify({"status": "matched", "game": po_state_dict(gid, blob, state, conn=conn)})
            conn.execute(
                """INSERT INTO po_queue (guest_id, display_name, avoid_guest_id, enqueued_at)
                   VALUES (%s, %s, %s, now())
                   ON CONFLICT (guest_id) DO UPDATE
                   SET display_name = EXCLUDED.display_name,
                       avoid_guest_id = EXCLUDED.avoid_guest_id,
                       enqueued_at = now()""",
                (guest_id, display_name, avoid_guest_id),
            )
        return jsonify(_po_status_payload(conn, guest_id))


@app.route("/api/po/status", methods=["POST"])
def po_status():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        return jsonify(_po_status_payload(conn, guest_id))


@app.route("/api/po/game", methods=["POST"])
def po_game():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    gid = (data.get("game_id") or "").strip()
    if not guest_id or not gid:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, state = _load_game(conn, "po_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not _po_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        blob["viewer_guest_id"] = guest_id
        return jsonify(po_state_dict(gid, blob, state, conn=conn))


@app.route("/api/po/powerup", methods=["POST"])
def po_powerup():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    gid = (data.get("game_id") or "").strip()
    requested_key = (data.get("powerup_key") or "").strip()
    if not guest_id or not gid:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, state = _load_game(conn, "po_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not _po_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        blob["viewer_guest_id"] = guest_id
        if blob["finished"]:
            return jsonify(po_state_dict(gid, blob, state, conn=conn))
        expected_turn_guest = [blob.get("p1_guest_id"), blob.get("p2_guest_id")][blob["turn_index"]]
        if guest_id != expected_turn_guest:
            return jsonify({"error": "not your turn", **po_state_dict(gid, blob, state, conn=conn)}), 409
        side = "p1" if guest_id == blob.get("p1_guest_id") else "p2"
        keys = blob.get(f"{side}_powerup_keys")
        if not keys:
            legacy_key = blob.get(f"{side}_powerup_key")
            keys = [legacy_key] if legacy_key else []
        used_keys = set(blob.get(f"{side}_powerup_used_keys") or [])
        legacy_key = blob.get(f"{side}_powerup_key")
        if blob.get(f"{side}_powerup_used") and legacy_key:
            used_keys.add(legacy_key)
        if not keys:
            return jsonify({"error": "no powerup assigned", **po_state_dict(gid, blob, state, conn=conn)}), 409
        if blob.get("turn_powerup_used"):
            return jsonify({"error": "you already used a powerup this turn", **po_state_dict(gid, blob, state, conn=conn)}), 409
        if not _playoff_powerups_unlocked(state):
            return jsonify({"error": "Powerups unlock after each player has played twice.", **po_state_dict(gid, blob, state, conn=conn)}), 409
        if not requested_key or requested_key not in keys:
            return jsonify({"error": "choose a valid powerup", **po_state_dict(gid, blob, state, conn=conn)}), 409
        if requested_key in used_keys:
            return jsonify({"error": "powerup already used", **po_state_dict(gid, blob, state, conn=conn)}), 409
        if blob.get("active_turn_powerup"):
            return jsonify({"error": "powerup already active this turn", **po_state_dict(gid, blob, state, conn=conn)}), 409
        key = requested_key
        powerup = PLAYOFF_POWERUPS[key]
        used_keys.add(key)
        blob[f"{side}_powerup_used_keys"] = sorted(used_keys)
        blob[f"{side}_powerup_key"] = key
        blob[f"{side}_powerup_used"] = True
        blob["turn_powerup_used"] = True
        if key == "abs":
            blob["turn_seconds"] = float(blob["turn_seconds"]) + float(powerup["bonus_seconds"])
            blob["last_move"] = {
                "outcome": "powerup_activated",
                "powerup_key": key,
                "powerup_label": powerup["label"],
                "message": f"{powerup['label']} activated. +15 seconds.",
            }
        elif key == "quick_pitch":
            blob["next_turn_seconds_override"] = QUICK_PITCH_TURN_SECONDS
            blob["last_move"] = {
                "outcome": "powerup_activated",
                "powerup_key": key,
                "powerup_label": powerup["label"],
                "message": f"{powerup['label']} activated. Opponent gets 10 seconds next turn.",
            }
        else:
            blob["turn_seconds"] = float(blob["turn_seconds"]) + float(powerup["bonus_seconds"])
            blob["active_turn_powerup"] = key
            blob["last_move"] = {
                "outcome": "powerup_activated",
                "powerup_key": key,
                "powerup_label": powerup["label"],
                "message": f"{powerup['label']} activated. Adds 5 seconds and expanded move rules this turn.",
            }
        _save_game(conn, "po_games", gid, blob)
        return jsonify(po_state_dict(gid, blob, state, conn=conn))


@app.route("/api/po/move", methods=["POST"])
def po_move():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    guest_id = (data.get("guest_id") or "").strip()
    raw = (data.get("raw") or "").strip()
    player_id = (data.get("player_id") or "").strip() or None
    with db() as conn:
        blob, state = _load_game(conn, "po_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not guest_id or not _po_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        blob["viewer_guest_id"] = guest_id
        if blob["finished"]:
            return jsonify(po_state_dict(gid, blob, state, conn=conn))
        expected_turn_guest = [blob.get("p1_guest_id"), blob.get("p2_guest_id")][blob["turn_index"]]
        if guest_id != expected_turn_guest:
            return jsonify({"error": "not your turn", **po_state_dict(gid, blob, state, conn=conn)}), 409
        started = datetime.fromisoformat(blob["turn_started_at"])
        elapsed = (now_utc() - started).total_seconds()
        live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
        if not _move_submitted_in_time(data, live_elapsed, blob["turn_seconds"]):
            blob["finished"] = True
            blob["winner"] = [blob["p2"], blob["p1"]][blob["turn_index"]]
            blob["last_move"] = {"outcome": "timeout"}
            _save_game(conn, "po_games", gid, blob)
            return jsonify(po_state_dict(gid, blob, state, conn=conn))
        if not raw and not player_id:
            blob["last_move"] = None
            _save_game(conn, "po_games", gid, blob)
            return jsonify(po_state_dict(gid, blob, state, conn=conn))

        engine_conn = PgEngineConn(conn)
        if player_id:
            result = validate_and_apply_move(state, engine_conn, player_id=player_id)
        else:
            result = validate_and_apply_move(state, engine_conn, raw)

        mover_side = "p1" if guest_id == blob.get("p1_guest_id") else "p2"
        move_payload = None
        if result.outcome == MoveOutcome.VALID:
            move_payload = result_to_dict(result)
            move_payload["move_via_powerup"] = False
            active_key = blob.get("active_turn_powerup")
            if active_key:
                move_payload["powerup_key"] = active_key
                move_payload["powerup_label"] = PLAYOFF_POWERUPS[active_key]["label"]
            chain_link_meta = list(blob.get("chain_link_meta") or [None] * (len(state.chain) - 1))
            if len(chain_link_meta) < len(state.chain):
                chain_link_meta.append(None)
            blob["chain_link_meta"] = chain_link_meta
        else:
            powerup_move = _apply_playoff_powerup_move(
                conn,
                state,
                blob,
                raw=raw if raw else None,
                player_id=player_id,
            )
            move_payload = powerup_move or result_to_dict(result)

        blob.update(serialize_state(state))
        blob["last_move"] = move_payload
        if move_payload.get("outcome") == "valid":
            if _playoff_win_conditions_unlocked(state):
                win_update = _apply_playoff_win_condition_hit(conn, blob, move_payload["player_id"], mover_side)
            else:
                meta = PLAYOFF_WIN_CONDITIONS.get(blob.get(f"{mover_side}_win_condition_key"), {})
                _append_no_win_condition_hit(blob, state)
                win_update = {
                    "hit": False,
                    "progress": int(blob.get(f"{mover_side}_win_progress") or 0),
                    "target": meta.get("target", 0),
                    "completed": False,
                    "label": meta.get("label"),
                }
            move_payload["win_condition_hit"] = win_update["hit"]
            move_payload["win_condition_label"] = win_update["label"]
            move_payload["win_condition_progress"] = win_update["progress"]
            move_payload["win_condition_target"] = win_update["target"]
            move_payload["win_condition_completed"] = win_update["completed"]
            if move_payload.get("player_id"):
                _record_player_usage(conn, move_payload["player_id"], "dr")
            if win_update["completed"]:
                blob["finished"] = True
                blob["winner"] = blob.get(mover_side)
            else:
                blob["turn_index"] = 1 - blob["turn_index"]
                blob["turn_started_at"] = now_utc().isoformat()
                blob["countdown_seconds"] = 0.0
                blob["turn_seconds"] = float(blob.get("next_turn_seconds_override") or blob.get("default_turn_seconds") or DEFAULT_PLAYOFF_TURN_SECONDS)
                blob["next_turn_seconds_override"] = None
                blob["active_turn_powerup"] = None
                blob["turn_powerup_used"] = False
        _save_game(conn, "po_games", gid, blob)
        return jsonify(po_state_dict(gid, blob, state, conn=conn))


@app.route("/api/po/timeout", methods=["POST"])
def po_timeout():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    guest_id = (data.get("guest_id") or "").strip()
    with db() as conn:
        blob, state = _load_game(conn, "po_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not guest_id or not _po_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        blob["viewer_guest_id"] = guest_id
        if blob["finished"]:
            return jsonify(po_state_dict(gid, blob, state, conn=conn))
        started = datetime.fromisoformat(blob["turn_started_at"])
        elapsed = (now_utc() - started).total_seconds()
        live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
        if live_elapsed < blob["turn_seconds"] - 0.25:
            return jsonify(po_state_dict(gid, blob, state, conn=conn))
        blob["finished"] = True
        blob["winner"] = [blob["p2"], blob["p1"]][blob["turn_index"]]
        blob["last_move"] = {"outcome": "timeout"}
        _save_game(conn, "po_games", gid, blob)
        return jsonify(po_state_dict(gid, blob, state, conn=conn))


@app.route("/api/po/rematch_request", methods=["POST"])
def po_rematch_request():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    gid = (data.get("game_id") or "").strip()
    if not guest_id or not gid:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, state = _load_game(conn, "po_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not _po_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        if not blob["finished"]:
            return jsonify({"error": "game not finished"}), 400
        if blob.get("last_move", {}).get("outcome") == "forfeit":
            return jsonify({"error": "rematch unavailable after forfeit"}), 400
        link = conn.execute("SELECT new_game_id::text FROM po_rematch_links WHERE original_game_id = %s", (gid,)).fetchone()
        if link:
            new_gid = link[0]
            new_blob, new_state = _load_game(conn, "po_games", new_gid)
            if new_blob:
                new_blob["viewer_guest_id"] = guest_id
                return jsonify({"status": "matched", "game": po_state_dict(new_gid, new_blob, new_state, conn=conn)})
        conn.execute(
            """INSERT INTO po_rematches (original_game_id, requester_guest_id)
               VALUES (%s, %s)
               ON CONFLICT DO NOTHING""",
            (gid, guest_id),
        )
        requesters = {r[0] for r in conn.execute(
            "SELECT requester_guest_id::text FROM po_rematches WHERE original_game_id = %s",
            (gid,),
        ).fetchall()}
        if {blob.get("p1_guest_id"), blob.get("p2_guest_id")} <= requesters:
            new_gid, new_blob, new_state = _po_create_online_game(
                conn,
                blob.get("p1_guest_id"),
                blob.get("p1"),
                blob.get("p2_guest_id"),
                blob.get("p2"),
                _sport_online_rematch_preferences(blob),
                first_guest_id=_sport_online_rematch_first_guest_id(blob),
            )
            conn.execute(
                """INSERT INTO po_rematch_links (original_game_id, new_game_id)
                   VALUES (%s, %s)
                   ON CONFLICT (original_game_id) DO NOTHING""",
                (gid, new_gid),
            )
            new_blob["viewer_guest_id"] = guest_id
            return jsonify({"status": "matched", "game": po_state_dict(new_gid, new_blob, new_state, conn=conn)})
        return jsonify({"status": "waiting"})


@app.route("/api/po/rematch_status", methods=["POST"])
def po_rematch_status():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    gid = (data.get("game_id") or "").strip()
    if not guest_id or not gid:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, _state = _load_game(conn, "po_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not _po_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        link = conn.execute("SELECT new_game_id::text FROM po_rematch_links WHERE original_game_id = %s", (gid,)).fetchone()
        if link:
            new_gid = link[0]
            new_blob, new_state = _load_game(conn, "po_games", new_gid)
            if new_blob:
                new_blob["viewer_guest_id"] = guest_id
                return jsonify({"status": "matched", "game": po_state_dict(new_gid, new_blob, new_state, conn=conn)})
        requesters = {r[0] for r in conn.execute(
            "SELECT requester_guest_id::text FROM po_rematches WHERE original_game_id = %s",
            (gid,),
        ).fetchall()}
        self_in_queue = conn.execute("SELECT 1 FROM po_queue WHERE guest_id = %s", (guest_id,)).fetchone()
        if self_in_queue:
            return jsonify({"status": "requeued", "you_requested": guest_id in requesters, "opponent_requested": False, "opponent_present": False, "rematch_available": False})
        other_guest_id = blob.get("p2_guest_id") if guest_id == blob.get("p1_guest_id") else blob.get("p1_guest_id")
        exited = {r[0] for r in conn.execute(
            "SELECT guest_id::text FROM po_postgame_exits WHERE original_game_id = %s",
            (gid,),
        ).fetchall()}
        if other_guest_id in exited:
            return jsonify({"status": "abandoned", "you_requested": guest_id in requesters, "opponent_requested": False, "opponent_present": False, "rematch_available": False})
        other_in_queue = conn.execute("SELECT 1 FROM po_queue WHERE guest_id = %s", (other_guest_id,)).fetchone()
        other_in_other_game = conn.execute(
            """SELECT 1
                 FROM po_games
                WHERE NOT finished
                  AND game_id <> %s
                  AND ((state->>'p1_guest_id') = %s OR (state->>'p2_guest_id') = %s)""",
            (gid, other_guest_id, other_guest_id),
        ).fetchone()
        opponent_present = not other_in_queue and not other_in_other_game and other_guest_id not in exited
        return jsonify({
            "status": "waiting",
            "you_requested": guest_id in requesters,
            "opponent_requested": other_guest_id in requesters,
            "opponent_present": opponent_present,
            "rematch_available": True,
        })


@app.route("/api/po/postgame_leave", methods=["POST"])
def po_postgame_leave():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    game_id = (data.get("game_id") or "").strip()
    if not guest_id or not game_id:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, _state = _load_game(conn, "po_games", game_id)
        if not blob:
            return jsonify({"status": "gone"})
        if not _po_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        conn.execute(
            """INSERT INTO po_postgame_exits (original_game_id, guest_id)
               VALUES (%s, %s)
               ON CONFLICT DO NOTHING""",
            (game_id, guest_id),
        )
        conn.execute("DELETE FROM po_rematches WHERE original_game_id = %s AND requester_guest_id = %s", (game_id, guest_id))
        other_guest_id = blob.get("p2_guest_id") if guest_id == blob.get("p1_guest_id") else blob.get("p1_guest_id")
        other_requested = conn.execute(
            """SELECT 1
                 FROM po_rematches
                WHERE original_game_id = %s
                  AND requester_guest_id = %s""",
            (game_id, other_guest_id),
        ).fetchone()
        if other_requested:
            conn.execute(
                """INSERT INTO po_queue (guest_id, display_name, avoid_guest_id, enqueued_at)
                   VALUES (%s, %s, %s, now())
                   ON CONFLICT (guest_id) DO UPDATE
                   SET display_name = EXCLUDED.display_name,
                       avoid_guest_id = EXCLUDED.avoid_guest_id,
                       enqueued_at = now()""",
                (other_guest_id, _guest_label(conn, other_guest_id), guest_id),
            )
        return jsonify({"status": "gone"})


@app.route("/api/po/create_challenge", methods=["POST"])
def po_create_challenge():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    requested_preference = data.get("win_condition_preference")
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        if requested_preference is not None:
            _save_playoff_preference(conn, guest_id, requested_preference)
        display_name = _guest_label(conn, guest_id)
        conn.execute("DELETE FROM po_queue WHERE guest_id = %s", (guest_id,))
        conn.execute("DELETE FROM po_invites WHERE host_guest_id = %s AND claimed_at IS NULL", (guest_id,))
        code = secrets.token_hex(3).upper()
        conn.execute(
            """INSERT INTO po_invites (code, host_guest_id, host_name)
               VALUES (%s, %s, %s)""",
            (code, guest_id, display_name),
        )
        return jsonify({"status": "waiting", "code": code})


@app.route("/api/po/join_challenge", methods=["POST"])
def po_join_challenge():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    code = (data.get("code") or "").strip().upper()
    if not guest_id or not code:
        return jsonify({"error": "guest_id and code required"}), 400
    with db() as conn:
        display_name = _guest_label(conn, guest_id)
        with conn.transaction():
            row = conn.execute(
                """SELECT host_guest_id::text, host_name
                     FROM po_invites
                    WHERE code = %s
                      AND claimed_at IS NULL
                      AND expires_at > now()
                    FOR UPDATE""",
                (code,),
            ).fetchone()
            if not row:
                return jsonify({"error": "challenge code not found"}), 404
            host_guest_id, host_name = row
            if host_guest_id == guest_id:
                return jsonify({"error": "cannot join your own challenge"}), 400
            gid, blob, state = _po_create_online_game(conn, host_guest_id, host_name, guest_id, display_name)
            conn.execute("UPDATE po_invites SET claimed_at = now() WHERE code = %s", (code,))
            blob["viewer_guest_id"] = guest_id
            return jsonify({"status": "matched", "game": po_state_dict(gid, blob, state, conn=conn)})


@app.route("/api/po/cancel_queue", methods=["POST"])
def po_cancel_queue():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        conn.execute("DELETE FROM po_queue WHERE guest_id = %s", (guest_id,))
    return jsonify({"status": "idle"})


@app.route("/api/po/cancel_challenge", methods=["POST"])
def po_cancel_challenge():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        conn.execute("DELETE FROM po_invites WHERE host_guest_id = %s AND claimed_at IS NULL", (guest_id,))
    return jsonify({"status": "idle"})


@app.route("/api/po/leave_game", methods=["POST"])
def po_leave_game():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    gid = (data.get("game_id") or "").strip()
    if not guest_id or not gid:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        blob, state = _load_game(conn, "po_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if not _po_authorize(blob, guest_id):
            return jsonify({"error": "unauthorized"}), 403
        if not blob["finished"]:
            blob["finished"] = True
            blob["winner"] = blob.get("p2") if guest_id == blob.get("p1_guest_id") else blob.get("p1")
            blob["last_move"] = {"outcome": "forfeit"}
            _save_game(conn, "po_games", gid, blob)
    return jsonify({"status": "gone"})

# ============================================================
# Batting Practice (solo, timed)
# ============================================================

def bp_blob_from_state(state: GameState, turn_seconds: float,
                       turn_started_at: datetime,
                       countdown_seconds: float, longest_chain: int = 1,
                       owner_guest_id: str | None = None,
                       seed_player_id: str = DEFAULT_SEED,
                       result_saved: bool = False,
                       finished: bool = False,
                       last_move: dict | None = None) -> dict:
    return {
        **serialize_state(state),
        "turn_seconds": turn_seconds,
        "turn_started_at": turn_started_at.isoformat(),
        "countdown_seconds": countdown_seconds,
        "longest_chain": longest_chain,
        "owner_guest_id": owner_guest_id,
        "seed_player_id": seed_player_id,
        "result_saved": result_saved,
        "finished": finished,
        "last_move": last_move,
    }


def bp_state_dict(gid: str, blob: dict, state: GameState, conn=None) -> dict:
    started = datetime.fromisoformat(blob["turn_started_at"])
    elapsed = (now_utc() - started).total_seconds()
    countdown_left = max(0.0, blob["countdown_seconds"] - elapsed) \
        if not blob["finished"] else 0.0
    live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
    remaining = max(0.0, blob["turn_seconds"] - live_elapsed) \
        if not blob["finished"] else 0.0
    cards = _hydrate_player_cards(conn, list(state.chain)) if conn else None
    return {
        "game_id": gid,
        "mode": "bp",
        "current_player": {
            "id": state.current_player_id,
            "name": state.current_player_name,
        },
        "chain": chain_dict(state, cards=cards),
        "strikes": strikes_dict(state),
        "chain_length": len(state.chain),
        "longest_chain": blob["longest_chain"],
        "turn_seconds": blob["turn_seconds"],
        "countdown_seconds_remaining": countdown_left,
        "remaining_seconds": remaining,
        "finished": blob["finished"],
        "last_move": blob.get("last_move"),
    }


@app.route("/api/bp/new", methods=["POST"])
def bp_new():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip() or None
    if not _valid_uuid_text(guest_id):
        guest_id = None
    turn_seconds = float(data.get("turn_seconds") or APP_TURN_SECONDS)
    with db() as conn:
        seed = data.get("seed") or _manager_seed_for_day(conn, "baseball")
        if guest_id:
            row = conn.execute(
                "SELECT 1 FROM guests WHERE guest_id = %s",
                (guest_id,),
            ).fetchone()
            if not row:
                guest_id = None
        engine_conn = PgEngineConn(conn)
        try:
            state = seed_game(engine_conn, seed)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        _record_player_usage(conn, seed, "bp")
        blob = bp_blob_from_state(
            state,
            turn_seconds=turn_seconds,
            turn_started_at=now_utc(),
            countdown_seconds=OPENING_COUNTDOWN_SECONDS,
            longest_chain=1,
            owner_guest_id=guest_id,
            seed_player_id=seed,
        )
        gid = _insert_game(conn, "bp_games", blob)
        return jsonify(bp_state_dict(gid, blob, state, conn=conn))


@app.route("/api/bp/move", methods=["POST"])
def bp_move():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    raw = (data.get("raw") or "").strip()
    player_id = (data.get("player_id") or "").strip() or None
    with db() as conn:
        blob, state = _load_game(conn, "bp_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if blob["finished"]:
            return jsonify(bp_state_dict(gid, blob, state, conn=conn))

        started = datetime.fromisoformat(blob["turn_started_at"])
        elapsed = (now_utc() - started).total_seconds()
        live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
        if not _move_submitted_in_time(data, live_elapsed, blob["turn_seconds"]):
            blob["finished"] = True
            blob["last_move"] = {"outcome": "timeout"}
            _save_bp_run(conn, blob, state)
            _save_game(conn, "bp_games", gid, blob)
            return jsonify(bp_state_dict(gid, blob, state, conn=conn))

        if not raw and not player_id:
            blob["last_move"] = None
            _save_game(conn, "bp_games", gid, blob)
            return jsonify(bp_state_dict(gid, blob, state, conn=conn))

        engine_conn = PgEngineConn(conn)
        if player_id:
            result = validate_and_apply_move(
                state, engine_conn, player_id=player_id, track_strikes=True,
            )
        else:
            result = validate_and_apply_move(
                state, engine_conn, raw, track_strikes=True,
            )
        blob.update(serialize_state(state))
        blob["last_move"] = result_to_dict(result)
        if result.outcome == MoveOutcome.VALID:
            if result.player_id:
                _record_player_usage(conn, result.player_id, "bp")
            blob["turn_started_at"] = now_utc().isoformat()
            blob["countdown_seconds"] = 0.0
            blob["longest_chain"] = max(blob["longest_chain"], len(state.chain))
        _save_game(conn, "bp_games", gid, blob)
        return jsonify(bp_state_dict(gid, blob, state, conn=conn))


@app.route("/api/bp/timeout", methods=["POST"])
def bp_timeout():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    with db() as conn:
        blob, state = _load_game(conn, "bp_games", gid)
        if not blob:
            return jsonify({"error": "unknown game_id"}), 404
        if blob["finished"]:
            return jsonify(bp_state_dict(gid, blob, state, conn=conn))
        started = datetime.fromisoformat(blob["turn_started_at"])
        elapsed = (now_utc() - started).total_seconds()
        live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
        if live_elapsed < blob["turn_seconds"] - 0.25:
            return jsonify(bp_state_dict(gid, blob, state, conn=conn))
        blob["finished"] = True
        blob["last_move"] = {"outcome": "timeout"}
        _save_bp_run(conn, blob, state)
        _save_game(conn, "bp_games", gid, blob)
        return jsonify(bp_state_dict(gid, blob, state, conn=conn))


def _sport_bp_state_dict(gid: str, blob: dict, state: GameState, conn) -> dict:
    started = datetime.fromisoformat(blob["turn_started_at"])
    elapsed = (now_utc() - started).total_seconds()
    countdown = max(0.0, blob["countdown_seconds"] - elapsed) if not blob["finished"] else 0.0
    remaining = max(0.0, blob["turn_seconds"] - max(0.0, elapsed - blob["countdown_seconds"])) if not blob["finished"] else 0.0
    sport = blob["sport"]
    last_move = dict(blob.get("last_move") or {})
    for field in ("shared_seasons", "burned_seasons"):
        for item in last_move.get(field, []):
            item["team_name"] = _sport_team_name(conn, sport, item["team_id"], item["season"])
            item["season_label"] = _sport_season_label(sport, item["season"])
    return {
        "game_id": gid, "mode": "bp", "sport": sport,
        "mode_name": LOCAL_SPORT_MODE_NAMES[sport],
        "current_player": {"id": state.current_player_id, "name": _sport_display_name(sport, state.current_player_id, fallback=state.current_player_name)},
        "chain": _sport_chain_dict(conn, sport, state),
        "strikes": _sport_strikes_dict(conn, sport, state),
        "chain_length": len(state.chain), "longest_chain": blob["longest_chain"],
        "turn_seconds": blob["turn_seconds"], "countdown_seconds_remaining": countdown,
        "remaining_seconds": remaining, "finished": blob["finished"], "last_move": last_move,
    }


def _save_sport_bp_run(conn, blob: dict, state: GameState):
    if blob.get("result_saved"):
        return
    guest_id = blob.get("owner_guest_id")
    sport = blob["sport"]
    if guest_id:
        conn.execute(
            """INSERT INTO bp_runs (owner_guest_id, seed_player_id, chain_length, sport_id)
               VALUES (%s, %s, %s, %s)""",
            (guest_id, blob["seed_player_id"], len(state.chain), sport),
        )
        _record_sport_struck_out_teams(conn, guest_id, sport, "bp", state)
    blob["result_saved"] = True


@app.route("/api/sports/<sport>/autocomplete")
def sport_autocomplete(sport: str):
    q = "".join(char for char in normalize(request.args.get("q") or "") if char.isalnum())
    if not _is_cross_sport(sport) or not q:
        return jsonify([])
    if sport == "baseball":
        with app.test_request_context(f"/api/autocomplete?q={quote_plus(request.args.get('q') or '')}"):
            return autocomplete()
    with db() as conn:
        rows = conn.execute(
            """SELECT player_id, display_name, debut_year, final_year, career_games
                 FROM (
                   SELECT p.player_id, p.display_name, sp.debut_year, sp.final_year, p.career_games
                     FROM sport_players_searchable p
                     JOIN sport_players sp ON sp.sport_id=p.sport_id AND sp.player_id=p.player_id
                    WHERE p.sport_id=%s AND COALESCE(sp.final_year, 9999) >= 2000
                      AND (p.search_key LIKE %s OR p.last_key LIKE %s)
                   UNION
                   SELECT p.player_id, p.display_name, sp.debut_year, sp.final_year, p.career_games
                     FROM sport_player_aliases a
                     JOIN sport_players_searchable p ON p.sport_id=a.sport_id AND p.player_id=a.player_id
                     JOIN sport_players sp ON sp.sport_id=p.sport_id AND sp.player_id=p.player_id
                    WHERE a.sport_id=%s AND COALESCE(sp.final_year, 9999) >= 2000 AND a.alias_key LIKE %s
                 ) matches
                ORDER BY career_games DESC LIMIT 4""",
            (sport, q + "%", q + "%", sport, q + "%"),
        ).fetchall()
    return jsonify([{"player_id": pid, "display_name": _sport_display_name(sport, pid, fallback=name), "debut_year": debut,
                     "final_year": final, "career_games": games}
                    for pid, name, debut, final, games in rows])


@app.route("/api/sports/<sport>/bp/new", methods=["POST"])
def sport_bp_new(sport: str):
    ensure_runtime_schema()
    if not _is_cross_sport(sport):
        return jsonify({"error": "unsupported sport"}), 404
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip() or None
    if not _valid_uuid_text(guest_id):
        guest_id = None
    with db() as conn:
        if guest_id and not conn.execute("SELECT 1 FROM guests WHERE guest_id=%s", (guest_id,)).fetchone():
            guest_id = None
        state = seed_game(PgEngineConn(conn), data.get("seed") or _manager_seed_for_day(conn, sport), sport=_engine_sport(sport))
        _record_sport_player_usage(conn, sport, state.current_player_id, "bp")
        blob = bp_blob_from_state(state, APP_TURN_SECONDS, now_utc(), OPENING_COUNTDOWN_SECONDS,
                                  owner_guest_id=guest_id, seed_player_id=state.current_player_id)
        blob["sport"] = sport
        gid = _insert_game(conn, "bp_games", blob)
        return jsonify(_sport_bp_state_dict(gid, blob, state, conn))


@app.route("/api/sports/<sport>/bp/move", methods=["POST"])
def sport_bp_move(sport: str):
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    with db() as conn:
        blob, state = _load_game(conn, "bp_games", gid)
        if not blob or blob.get("sport") != sport:
            return jsonify({"error": "unknown game_id"}), 404
        if blob["finished"]:
            return jsonify(_sport_bp_state_dict(gid, blob, state, conn))
        elapsed = (now_utc() - datetime.fromisoformat(blob["turn_started_at"])).total_seconds()
        live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
        if not _move_submitted_in_time(data, live_elapsed, blob["turn_seconds"]):
            blob.update({"finished": True, "last_move": {"outcome": "timeout"}})
            _save_sport_bp_run(conn, blob, state)
        else:
            player_id = (data.get("player_id") or "").strip() or None
            raw = (data.get("raw") or "").strip()
            if player_id or raw:
                result = validate_and_apply_move(state, PgEngineConn(conn), raw_input=None if player_id else raw,
                                                 player_id=player_id, track_strikes=True, sport=_engine_sport(sport))
                blob.update(serialize_state(state))
                blob["last_move"] = result_to_dict(result)
                if result.outcome == MoveOutcome.VALID:
                    _record_sport_player_usage(conn, sport, result.player_id, "bp")
                    blob["turn_started_at"] = now_utc().isoformat()
                    blob["countdown_seconds"] = 0.0
                    blob["longest_chain"] = max(blob["longest_chain"], len(state.chain))
        _save_game(conn, "bp_games", gid, blob)
        return jsonify(_sport_bp_state_dict(gid, blob, state, conn))


@app.route("/api/sports/<sport>/bp/timeout", methods=["POST"])
def sport_bp_timeout(sport: str):
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    with db() as conn:
        blob, state = _load_game(conn, "bp_games", gid)
        if not blob or blob.get("sport") != sport:
            return jsonify({"error": "unknown game_id"}), 404
        elapsed = (now_utc() - datetime.fromisoformat(blob["turn_started_at"])).total_seconds()
        if not blob["finished"] and max(0.0, elapsed - blob["countdown_seconds"]) >= blob["turn_seconds"] - .25:
            blob.update({"finished": True, "last_move": {"outcome": "timeout"}})
            _save_sport_bp_run(conn, blob, state)
            _save_game(conn, "bp_games", gid, blob)
        return jsonify(_sport_bp_state_dict(gid, blob, state, conn))


# ============================================================
# Film Review (daily puzzle)
# ============================================================

BASEBALL_FR_SLOTS = ("DH", "1B", "SP", "2B", "3B", "SS", "LF", "CF", "RF", "C", "RP", "CP")

FR_MAX_STRIKES = 3
FR_MAX_LINK_OPTIONS = 4

FR_STABLE_HEADSHOT_PROVIDERS = {
    "baseball": {"MLBAM", "OOTP Facepack"},
    "basketball": {"catalog", "NBA", "BBGM community map"},
    "hockey": {"catalog", "NHL", "ESPN", "FHM Historical Photos Megapack 3.5", "FHM Facepack 24-25"},
}
FR_FALLBACK_HEADSHOT_PROVIDERS = {
    "Web image search", "Wikimedia Commons", "HockeyDB", "Basketball Reference",
    "Hockey Reference", "Baseball Reference", "manual_submission", "manual_upload",
    "Community roster CSV", "TheSportsDB",
}


def _fr_choice_window(slot_index: int, total_slots: int, choices_len: int) -> int:
    if slot_index == 1:
        return min(10, choices_len)
    if slot_index <= 2:
        return min(14, choices_len)
    if slot_index <= max(4, total_slots // 2):
        return min(26, choices_len)
    if slot_index <= total_slots - 3:
        return min(44, choices_len)
    return min(70, choices_len)


def _fr_early_choice_floor(sport: str, slot_index: int) -> int:
    if slot_index > 5:
        return 0
    early = {"baseball": 3600, "basketball": 3600, "hockey": 3300, "football": 1900}
    middle = {"baseball": 2800, "basketball": 2600, "hockey": 2450, "football": 1500}
    return (early if slot_index <= 2 else middle).get(sport, 0)


def _fr_photo_score(sport: str, provider: str | None) -> int:
    if provider in FR_STABLE_HEADSHOT_PROVIDERS.get(sport, set()):
        return 600
    if provider in FR_FALLBACK_HEADSHOT_PROVIDERS:
        return -450
    return 0


def _fr_player_quality(career_games: int | None, teammate_count: int | None, final_year: int | None,
                       provider: str | None, sport: str) -> int:
    final_year = final_year or 2000
    recency = max(0, min(2026, final_year) - 2014) * 70
    return int(career_games or 0) * 5 + int(teammate_count or 0) * 3 + recency \
        + (650 if final_year >= 2024 else 0) + (350 if final_year >= 2020 else 0) \
        + _fr_photo_score(sport, provider)


def _fr_preferred_link(links: list[tuple[str, int]], used_links: set[tuple[str, int]]) -> tuple[str, int] | None:
    usable = [link for link in links if link not in used_links]
    if not usable:
        return None
    return sorted(usable, key=lambda link: (int(link[1]), link[0]), reverse=True)[0]


def _fr_compute_shared(conn, deck: list[str]) -> list[list[tuple[str, int, str]]]:
    engine_conn = PgEngineConn(conn)
    out = []
    for i in range(len(deck) - 1):
        shared = get_shared_seasons(engine_conn, deck[i], deck[i + 1])
        out.append([(t, s, fr_display_team_name(t, s)) for t, s in shared])
    return out


def _film_pair_key(first: str, second: str) -> tuple[str, str]:
    return tuple(sorted((first, second)))


def _film_history_constraints(conn, sport: str, puzzle_day: date) -> tuple[set[str], set[str], set[tuple[str, str]]]:
    """Return prior Film Review players, openings, and adjacent pairs for this sport.

    The archive is intentionally stable, so future daily puzzles should avoid
    reusing players or exact teammate pairings from earlier stored tapes.
    """
    rows = conn.execute(
        """SELECT puzzle FROM film_review_daily_puzzles
            WHERE sport_id=%s AND puzzle_date < %s""",
        (sport, puzzle_day),
    ).fetchall()
    opening_players: set[str] = set()
    all_players: set[str] = set()
    adjacent_pairs: set[tuple[str, str]] = set()
    for (puzzle,) in rows:
        deck = list((puzzle or {}).get("deck") or [])
        if len(deck) >= 2:
            opening_players.update(deck[:2])
        all_players.update(deck)
        for index in range(len(deck) - 1):
            adjacent_pairs.add(_film_pair_key(deck[index], deck[index + 1]))
    return all_players, opening_players, adjacent_pairs


def _film_puzzle_repeats_history(conn, sport: str, puzzle_day: date, puzzle: dict) -> bool:
    deck = list(puzzle.get("deck") or [])
    if len(deck) < 2:
        return True
    all_players, opening_players, adjacent_pairs = _film_history_constraints(conn, sport, puzzle_day)
    if sport == "football":
        if any(player_id in opening_players for player_id in deck[:2]):
            return True
    elif any(player_id in all_players for player_id in deck):
        return True
    return any(_film_pair_key(deck[index], deck[index + 1]) in adjacent_pairs for index in range(len(deck) - 1))


def generate_baseball_film_review(conn, puzzle_day: date, seed_suffix: str = "",
                                  banned_opening_players: set[str] | None = None,
                                  banned_players: set[str] | None = None,
                                  banned_adjacent_pairs: set[tuple[str, str]] | None = None) -> dict:
    """Build one stable, role-aware baseball lineup for a calendar day.

    The date is the sole seed, so every player sees the same puzzle without a
    cron job or a pre-generated deck file. Distinct team-seasons are enforced
    for every connection in the chain.
    """
    pools: dict[str, dict[str, int]] = {}
    for slot in BASEBALL_FR_SLOTS:
        if slot in {"RP", "CP"}:
            min_pitching_games = 45 if slot == "CP" else 25
            rows = conn.execute(
                """SELECT a.player_id, ps.career_games, ps.teammate_count, p.final_year, h.provider
                     FROM appearances a
                     JOIN players p ON p.player_id=a.player_id
                     JOIN players_searchable ps ON ps.player_id=p.player_id
                     LEFT JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=p.player_id
                    WHERE a.season>=2000 AND p.final_year>=2000
                      AND h.status='verified'
                      AND COALESCE(h.source_url, h.fallback_url, '') <> ''
                    GROUP BY a.player_id, ps.career_games, ps.teammate_count, p.final_year, h.provider
                    HAVING SUM(a.games_pitched)>=%s
                    ORDER BY a.player_id""",
                (min_pitching_games,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT bp.player_id, ps.career_games, ps.teammate_count, p.final_year, h.provider
                     FROM baseball_player_positions bp
                     JOIN players p ON p.player_id=bp.player_id
                     JOIN players_searchable ps ON ps.player_id=p.player_id
                     LEFT JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=p.player_id
                    WHERE bp.position=%s AND bp.games>=25 AND p.final_year>=2000
                      AND h.status='verified'
                      AND COALESCE(h.source_url, h.fallback_url, '') <> ''
                    ORDER BY bp.player_id""",
                (slot,),
            ).fetchall()
        pools[slot] = {
            player_id: _fr_player_quality(career_games, teammate_count, final_year, provider, "baseball")
            for player_id, career_games, teammate_count, final_year, provider in rows
        }
    missing = [slot for slot, players in pools.items() if not players]
    if missing:
        raise RuntimeError("baseball Film Review position data is unavailable for " + ", ".join(missing))
    recent_players = {
        row[0] for row in conn.execute(
            "SELECT player_id FROM players WHERE final_year>=2016"
        ).fetchall()
    }

    def candidates(player_id: str, eligible: dict[str, int], used_players: set[str],
                   used_links: set[tuple[str, int]]) -> list[tuple[str, tuple[str, int]]]:
        strict_game_coverage = conn.execute(
            """SELECT 1
                 FROM teammate_stint_coverage
                WHERE strict <> 0
                  AND coverage_type = 'game_boxscore'
                  AND season >= 2000
                LIMIT 1"""
        ).fetchone()
        if strict_game_coverage is not None:
            rows = conn.execute(
                """SELECT CASE WHEN proof.player_a_id=%s THEN proof.player_b_id ELSE proof.player_a_id END AS player_id,
                          proof.team_id,
                          proof.season
                     FROM mlb_teammate_game_proofs proof
                     JOIN players p
                       ON p.player_id = CASE WHEN proof.player_a_id=%s THEN proof.player_b_id ELSE proof.player_a_id END
                    WHERE (proof.player_a_id=%s OR proof.player_b_id=%s)
                      AND proof.season >= 2000
                      AND p.final_year >= 2000
                      AND EXISTS (
                          SELECT 1 FROM teammate_stint_coverage c
                           WHERE c.season = proof.season
                             AND c.strict <> 0
                             AND c.coverage_type = 'game_boxscore'
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM teammate_exclusions e
                           WHERE e.team_id = proof.team_id
                             AND e.season = proof.season
                             AND ((e.player_a_id = proof.player_a_id AND e.player_b_id = proof.player_b_id)
                               OR (e.player_a_id = proof.player_b_id AND e.player_b_id = proof.player_a_id))
                      )
                    ORDER BY player_id, proof.team_id, proof.season""",
                (player_id, player_id, player_id, player_id),
            ).fetchall()
            by_candidate: dict[str, list[tuple[str, int]]] = {}
            for pid, team, season in rows:
                if pid in eligible and pid not in used_players:
                    by_candidate.setdefault(pid, []).append((team, season))
            options = []
            for pid, links in by_candidate.items():
                if len(links) > FR_MAX_LINK_OPTIONS:
                    continue
                link = _fr_preferred_link(links, used_links)
                if link is not None:
                    options.append((pid, link))
            return sorted(options, key=lambda item: (eligible[item[0]], item[1][1]), reverse=True)
        rows = conn.execute(
            """SELECT DISTINCT b.player_id, a.team_id, a.season
                 FROM appearances a
                 JOIN appearances b ON b.team_id=a.team_id AND b.season=a.season
                 JOIN players p ON p.player_id=b.player_id
                WHERE a.player_id=%s AND b.player_id<>%s
                  AND a.season>=2000 AND p.final_year>=2000
                  AND (
                      NOT EXISTS (
                          SELECT 1 FROM teammate_stint_coverage c
                           WHERE c.season = a.season
                             AND c.strict <> 0
                      )
                      OR (
                          EXISTS (
                              SELECT 1 FROM teammate_stint_coverage c
                               WHERE c.season = a.season
                                 AND c.strict <> 0
                                 AND c.coverage_type = 'game_boxscore'
                          )
                          AND EXISTS (
                              SELECT 1
                                FROM mlb_teammate_game_proofs proof
                               WHERE proof.team_id = a.team_id
                                 AND proof.season = a.season
                                 AND proof.player_a_id = LEAST(a.player_id, b.player_id)
                                 AND proof.player_b_id = GREATEST(a.player_id, b.player_id)
                          )
                      )
                      OR EXISTS (
                          SELECT 1
                            FROM player_stints sa
                            JOIN player_stints sb
                              ON sb.team_id = sa.team_id
                             AND sb.season = sa.season
                           WHERE sa.player_id = a.player_id
                             AND sb.player_id = b.player_id
                             AND sa.team_id = a.team_id
                             AND sa.season = a.season
                             AND NOT EXISTS (
                                  SELECT 1 FROM teammate_stint_coverage c
                                   WHERE c.season = a.season
                                     AND c.strict <> 0
                                     AND c.coverage_type = 'game_boxscore'
                             )
                             AND sa.first_unit <= sb.last_unit
                             AND sb.first_unit <= sa.last_unit
                      )
                  )
                ORDER BY b.player_id, a.team_id, a.season""",
            (player_id, player_id),
        ).fetchall()
        by_candidate: dict[str, list[tuple[str, int]]] = {}
        for pid, team, season in rows:
            if pid in eligible and pid not in used_players:
                by_candidate.setdefault(pid, []).append((team, season))
        options = []
        for pid, links in by_candidate.items():
            if len(links) > FR_MAX_LINK_OPTIONS:
                continue
            link = _fr_preferred_link(links, used_links)
            if link is not None:
                options.append((pid, link))
        return sorted(options, key=lambda item: (eligible[item[0]], item[1][1]), reverse=True)

    banned_opening_players = banned_opening_players or set()
    banned_players = banned_players or set()
    banned_adjacent_pairs = banned_adjacent_pairs or set()
    rng = random.Random(f"baseball:{puzzle_day.isoformat()}:{seed_suffix}")
    for _ in range(500):
        first = [
            player_id
            for player_id in sorted(pools[BASEBALL_FR_SLOTS[0]], key=pools[BASEBALL_FR_SLOTS[0]].get, reverse=True)
            if player_id not in banned_players and player_id not in banned_opening_players and player_id in recent_players
        ]
        if not first:
            first = [
                player_id
                for player_id in sorted(pools[BASEBALL_FR_SLOTS[0]], key=pools[BASEBALL_FR_SLOTS[0]].get, reverse=True)
                if player_id not in banned_players and player_id not in banned_opening_players
            ]
        if not first:
            first = [
                player_id
                for player_id in sorted(pools[BASEBALL_FR_SLOTS[0]], key=pools[BASEBALL_FR_SLOTS[0]].get, reverse=True)
                if player_id not in banned_players
            ]
        deck = [rng.choice(first[:min(12, len(first))])]
        used_players, used_links = {deck[0]}, set()
        failed = False
        for slot_index, slot in enumerate(BASEBALL_FR_SLOTS[1:], 1):
            choices = [
                item for item in candidates(deck[-1], pools[slot], used_players, used_links)
                if _film_pair_key(deck[-1], item[0]) not in banned_adjacent_pairs
                   and not (slot_index == 1 and item[0] in banned_opening_players)
                   and not (slot_index == 1 and item[0] not in recent_players)
                   and item[0] not in banned_players
            ]
            preferred_floor = _fr_early_choice_floor("baseball", slot_index)
            if preferred_floor:
                preferred = [item for item in choices if pools[slot][item[0]] >= preferred_floor]
                if preferred:
                    choices = preferred
            if not choices:
                failed = True
                break
            rng.shuffle(choices)
            choices.sort(key=lambda item: pools[slot][item[0]], reverse=True)
            next_player, link = rng.choice(choices[:_fr_choice_window(slot_index, len(BASEBALL_FR_SLOTS), len(choices))])
            deck.append(next_player)
            used_players.add(next_player)
            used_links.add(link)
        if not failed:
            return {
                "id": f"baseball_{puzzle_day.isoformat()}", "title": "Starting Lineup",
                "slots": list(BASEBALL_FR_SLOTS), "deck": deck,
                "puzzle_date": puzzle_day.isoformat(), "puzzle_number": _film_review_number(puzzle_day),
            }
    raise RuntimeError("could not build a complete baseball Film Review lineup")


def fr_card_dict(player_id: str) -> dict:
    card = player_card(player_id)
    return fr_card_dict_from_card(player_id, card)


def fr_card_dict_from_card(player_id: str, card: dict) -> dict:
    name_first = card["name_first"] or ""
    name_last = card["name_last"] or ""
    return {
        "id": player_id,
        "name": f"{name_first} {name_last}".strip(),
        "mlbam_id": card["mlbam_id"],
        "headshot_url": card["headshot_url"],
        "debut_year": card["debut_year"],
        "final_year": card["final_year"],
        "teams": [],  # hidden in FR
        "team_stints": card.get("team_stints", []),
    }


def fr_state_dict(gid: str, blob: dict, conn=None) -> dict:
    deck = blob["deck"]
    pair_index = blob["pair_index"]
    cards = blob.get("card_map") if isinstance(blob.get("card_map"), dict) else None
    if not cards and conn:
        cards = _film_card_map(conn, "baseball", list(deck))
    elif not cards:
        with db() as _conn:
            cards = _film_card_map(_conn, "baseball", list(deck))
    elif _film_card_map_missing_team_stints(cards, list(deck)):
        if conn:
            cards = _film_card_map_with_team_stints(conn, "baseball", list(deck), cards)
        else:
            with db() as _conn:
                cards = _film_card_map_with_team_stints(_conn, "baseball", list(deck), cards)
    current_streak = 0
    if conn and blob.get("puzzle_date") and blob.get("finished"):
        try:
            if not blob.get("archive"):
                current_streak = _film_current_streak(conn, blob.get("owner_guest_id"), "baseball", "")
        except Exception:
            current_streak = 0
    return {
        "game_id": gid,
        "mode": "fr",
        "puzzle_id": blob["puzzle_id"],
        "puzzle_date": blob.get("puzzle_date"),
        "puzzle_number": blob.get("puzzle_number"),
        "archive": bool(blob.get("archive")),
        "slots": blob.get("slots", []),
        "unit": blob.get("unit"),
        "total_cards": len(deck),
        "revealed_count": blob["revealed_count"],
        "revealed_cards": [
            cards.get(pid) or fr_card_dict(pid)
            for pid in deck[:blob["revealed_count"]]
        ],
        "pair_index": pair_index,
        "pair_names": [
            ((cards.get(deck[pair_index]) or fr_card_dict(deck[pair_index]))["name"]
             if pair_index < len(deck) else None),
            ((cards.get(deck[pair_index + 1]) or fr_card_dict(deck[pair_index + 1]))["name"]
             if pair_index + 1 < len(deck) else None),
        ],
        "current_answers": [
            {"team_id": row[0], "season": row[1], "team_name": row[2],
             "season_label": row[3] if len(row) > 3 else _sport_season_label("baseball", row[1])}
            for row in (blob["shared_per_pair"][pair_index] if pair_index < len(blob["shared_per_pair"]) else [])
        ],
        "solved_links": blob["solved_links"][: max(0, blob["revealed_count"] - 1)],
        "stats": {
            "hits": blob["hits"],
            "fouls": blob["fouls"],
            "strikes": blob["strikes"],
            "max_strikes": FR_MAX_STRIKES,
            "consec_fouls": blob["consec_fouls"],
            "total_pairs": len(deck) - 1,
        },
        "finished": blob["finished"],
        "won": blob.get("won", False),
        "last_guess": blob.get("last_guess"),
        "current_streak": current_streak,
    }


def fr_blob_from_puzzle(
    puzzle: dict,
    shared_per_pair: list[list[tuple[str, int, str]]],
    owner_guest_id: str | None = None,
    *, archive: bool = False,
) -> dict:
    return {
        "puzzle_id": puzzle["id"],
        "puzzle_date": puzzle.get("puzzle_date"),
        "puzzle_number": puzzle.get("puzzle_number"),
        "archive": archive,
        "slots": list(puzzle.get("slots", [])),
        "unit": None,
        "deck": list(puzzle["deck"]),
        "pair_index": 0,
        "revealed_count": 2,
        "hits": 0,
        "fouls": 0,
        "strikes": 0,
        "consec_fouls": 0,
        "solved_links": [None] * (len(puzzle["deck"]) - 1),
        "shared_per_pair": [
            [list(t) for t in pair] for pair in shared_per_pair
        ],
        "owner_guest_id": owner_guest_id,
        "result_saved": False,
        "finished": False,
        "won": False,
        "last_guess": None,
    }


def _classify_fr_guess(team_text: str, year_text: str,
                       shared: list[tuple[str, int, str]]) -> tuple[str, list]:
    team_q = (team_text or "").strip().lower()
    try:
        year_q = int((year_text or "").strip())
    except (ValueError, TypeError):
        year_q = None

    team_match_rows = []
    year_match_any = False
    for team_id, season, team_name in shared:
        aliases = fr_team_aliases(team_id, season)
        team_hit = bool(team_q) and (
            team_q == team_id.lower()
            or any(team_q in alias or alias in team_q for alias in aliases)
        )
        if team_hit:
            team_match_rows.append((team_id, season, team_name))
        if year_q is not None and season == year_q:
            year_match_any = True

    full_hits = [r for r in team_match_rows if r[1] == year_q]
    if full_hits:
        return "hit", full_hits
    if team_match_rows or year_match_any:
        return "foul", []
    return "strike", []


@app.route("/api/fr/new", methods=["POST"])
def fr_new():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip() or None
    archive = bool(data.get("archive"))
    try:
        puzzle_day = _film_review_day(data.get("puzzle_date"))
    except ValueError:
        return jsonify({"error": "invalid puzzle date"}), 400
    if puzzle_day > datetime.now(CENTRAL_TIME).date():
        return jsonify({"error": "future Film Review unavailable"}), 400
    with db() as conn:
        if guest_id:
            row = conn.execute(
                "SELECT 1 FROM guests WHERE guest_id = %s",
                (guest_id,),
            ).fetchone()
            if not row:
                guest_id = None
        if guest_id and not archive:
            existing = conn.execute(
                """SELECT game_id::text FROM film_review_daily_attempts
                     WHERE owner_guest_id=%s AND sport_id='baseball' AND puzzle_date=%s AND unit=''""",
                (guest_id, puzzle_day),
            ).fetchone()
            if existing:
                prior = conn.execute("SELECT state, finished FROM fr_games WHERE game_id=%s", (existing[0],)).fetchone()
                if prior:
                    blob, finished = prior
                    if (blob.get("puzzle_date") == puzzle_day.isoformat()
                            and blob.get("puzzle_number") == _film_review_number(puzzle_day)
                            and tuple(blob.get("slots") or []) == BASEBALL_FR_SLOTS):
                        _store_daily_film_review_puzzle(conn, "baseball", puzzle_day, None, {
                            "id": blob["puzzle_id"], "puzzle_date": blob["puzzle_date"],
                            "slots": blob["slots"], "deck": blob["deck"], "unit": None,
                        })
                        blob["finished"] = finished
                        return jsonify(fr_state_dict(existing[0], blob, conn=conn))
        try:
            puz = _daily_film_review_puzzle(
                conn, "baseball", puzzle_day, None,
                lambda: _build_film_review_puzzle_with_history(conn, "baseball", puzzle_day, None),
            )
        except RuntimeError as error:
            return jsonify({"error": str(error)}), 500
        deck = list(puz["deck"])
        shared_per_pair = puz.get("shared_per_pair")
        if not isinstance(shared_per_pair, list) or len(shared_per_pair) != len(deck) - 1:
            shared_per_pair = _fr_compute_shared(conn, deck)
        bad = [i for i, lst in enumerate(shared_per_pair) if not lst]
        if bad:
            return jsonify({
                "error": f"puzzle {puz['id']!r} has unsolvable pair(s): {bad}",
            }), 500
        blob = fr_blob_from_puzzle(puz, shared_per_pair, owner_guest_id=guest_id, archive=archive)
        blob["card_map"] = puz.get("card_map") or _film_card_map(conn, "baseball", deck)
        gid = _insert_game(conn, "fr_games", blob)
        if guest_id:
            if archive:
                conn.execute("""INSERT INTO film_review_daily_attempts (owner_guest_id, sport_id, puzzle_date, unit, game_id, official)
                                VALUES (%s,'baseball',%s,'',%s,false)
                                ON CONFLICT (owner_guest_id, sport_id, puzzle_date, unit)
                                DO UPDATE SET game_id=EXCLUDED.game_id, status='in_progress', completed_at=NULL
                                WHERE film_review_daily_attempts.status='in_progress'""",
                             (guest_id, puzzle_day, gid))
            else:
                conn.execute("""INSERT INTO film_review_daily_attempts (owner_guest_id, sport_id, puzzle_date, unit, game_id, official)
                                VALUES (%s,'baseball',%s,'',%s,true)
                                ON CONFLICT (owner_guest_id, sport_id, puzzle_date, unit)
                                DO UPDATE SET game_id=EXCLUDED.game_id, status='in_progress', completed_at=NULL, official=true""",
                             (guest_id, puzzle_day, gid))
        return jsonify(fr_state_dict(gid, blob, conn=conn))


@app.route("/api/fr/archive", methods=["POST"])
def fr_archive():
    ensure_runtime_schema()
    guest_id = ((request.get_json(silent=True) or {}).get("guest_id") or "").strip()
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        return jsonify({"days": _film_archive_days(conn, guest_id, "baseball")})


@app.route("/api/fr/daily_game", methods=["POST"])
def fr_daily_game():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    game_id = (data.get("game_id") or "").strip()
    if not guest_id or not game_id:
        return jsonify({"error": "guest_id and game_id required"}), 400
    with db() as conn:
        owned = conn.execute(
            """SELECT 1 FROM film_review_daily_attempts
                 WHERE owner_guest_id=%s AND sport_id='baseball' AND game_id=%s""",
            (guest_id, game_id),
        ).fetchone()
        row = conn.execute("SELECT state, finished FROM fr_games WHERE game_id=%s", (game_id,)).fetchone()
        if not owned or not row:
            return jsonify({"error": "daily Film Review not found"}), 404
        blob, finished = row
        blob["finished"] = finished
        return jsonify(fr_state_dict(game_id, blob, conn=conn))


@app.route("/api/fr/guess", methods=["POST"])
def fr_guess():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    team_text = (data.get("team") or "").strip()
    year_text = (data.get("year") or "").strip()
    with db() as conn:
        cur = conn.execute(
            "SELECT state, finished FROM fr_games WHERE game_id = %s", (gid,)
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "unknown game_id"}), 404
        blob, finished = row
        blob["finished"] = finished
        if finished:
            return jsonify(fr_state_dict(gid, blob, conn=conn))
        if not team_text or not year_text:
            blob["last_guess"] = {
                "outcome": "invalid", "team": team_text, "year": year_text,
            }
            _save_game(conn, "fr_games", gid, blob)
            return jsonify(fr_state_dict(gid, blob, conn=conn))

        shared_raw = blob["shared_per_pair"][blob["pair_index"]]
        shared = [(r[0], r[1], r[2]) for r in shared_raw]
        outcome, matched = _classify_fr_guess(team_text, year_text, shared)

        converted_from_foul = False
        if outcome == "foul":
            blob["consec_fouls"] += 1
            if blob["consec_fouls"] >= 2:
                outcome = "strike"
                converted_from_foul = True
        elif outcome == "hit":
            blob["consec_fouls"] = 0
        elif outcome == "strike":
            blob["consec_fouls"] = 0

        if outcome == "hit":
            blob["hits"] += 1
            matched_key = (matched[0][0], matched[0][1])
            ordered = [row for row in shared if (row[0], row[1]) == matched_key]
            ordered.extend(row for row in shared if (row[0], row[1]) != matched_key)
            blob["solved_links"][blob["pair_index"]] = [
                {"team_id": t_id, "season": season, "team_name": team_name}
                for t_id, season, team_name in ordered
            ]
            blob["pair_index"] += 1
            blob["revealed_count"] = min(blob["revealed_count"] + 1, len(blob["deck"]))
            if blob["hits"] >= len(blob["deck"]) - 1:
                blob["finished"] = True
                blob["won"] = True
        elif outcome == "foul":
            blob["fouls"] += 1
        elif outcome == "strike":
            blob["strikes"] += 1
            if blob["strikes"] >= FR_MAX_STRIKES:
                blob["finished"] = True
                blob["won"] = False

        blob["last_guess"] = {
            "outcome": outcome,
            "team": team_text,
            "year": year_text,
            "converted_from_foul": converted_from_foul,
            "matched": [
                {"team_id": t, "season": s, "team_name": n} for t, s, n in matched
            ],
        }
        if blob["finished"]:
            _save_fr_result(conn, blob)
            if blob.get("owner_guest_id"):
                conn.execute("""UPDATE film_review_daily_attempts SET status=%s, completed_at=now()
                                WHERE owner_guest_id=%s AND sport_id='baseball' AND puzzle_date=%s AND unit=''""",
                             ("won" if blob.get("won") else "lost", blob["owner_guest_id"], blob.get("puzzle_date")))
        _save_game(conn, "fr_games", gid, blob)
        return jsonify(fr_state_dict(gid, blob, conn=conn))


@app.route("/api/fr/reveal_answer", methods=["POST"])
def fr_reveal_answer():
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    with db() as conn:
        cur = conn.execute(
            "SELECT state, finished FROM fr_games WHERE game_id = %s", (gid,)
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "unknown game_id"}), 404
    blob, finished = row
    if not finished:
        return jsonify({"error": "game not finished"}), 400
    with db() as conn:
        cards = _hydrate_player_cards(conn, list(blob["deck"]))
    return jsonify({
        "full_cards": [
            fr_card_dict_from_card(pid, cards.get(pid) or player_card(pid))
            for pid in blob["deck"]
        ],
        "canonical_links": [
            [{"team_id": row[0], "season": row[1], "team_name": row[2]} for row in pair]
            for pair in blob["shared_per_pair"]
        ],
        "answers": [
            [{"team_id": r[0], "season": r[1], "team_name": r[2]} for r in pair]
            for pair in blob["shared_per_pair"]
        ],
    })


def _sport_fr_card(sport: str, player_id: str, card: dict) -> dict:
    return {
        "id": player_id,
        "name": card.get("display_name") or _sport_display_name(sport, player_id, card.get("name_first"), card.get("name_last")),
        "mlbam_id": None, "headshot_url": card.get("headshot_url"),
        "debut_year": card.get("debut_year"), "final_year": card.get("final_year"),
        "primary_pos": card.get("primary_pos"), "teams": [],
        "team_stints": card.get("team_stints", []),
    }


def _sport_fr_state_dict(gid: str, blob: dict, conn) -> dict:
    sport, deck, pair_index = blob["sport"], blob["deck"], blob["pair_index"]
    cards = blob.get("card_map") if isinstance(blob.get("card_map"), dict) else None
    if not cards:
        cards = _film_card_map(conn, sport, deck)
    elif _film_card_map_missing_team_stints(cards, deck):
        cards = _film_card_map_with_team_stints(conn, sport, deck, cards)
    else:
        for pid in deck:
            if pid in cards:
                cards[pid]["name"] = _sport_display_name(sport, pid, fallback=cards[pid].get("name"))
    current_streak = 0
    if blob.get("puzzle_date") and blob.get("finished"):
        try:
            if not blob.get("archive"):
                current_streak = _film_current_streak(conn, blob.get("owner_guest_id"), sport, blob.get("unit") or "")
        except Exception:
            current_streak = 0
    return {
        "game_id": gid, "mode": "fr", "sport": sport, "puzzle_id": blob["puzzle_id"],
        "puzzle_date": blob.get("puzzle_date"), "puzzle_number": blob.get("puzzle_number"), "archive": bool(blob.get("archive")),
        "slots": blob["slots"], "unit": blob.get("unit"), "total_cards": len(deck),
        "revealed_count": blob["revealed_count"],
        "revealed_cards": [cards[pid] for pid in deck[:blob["revealed_count"]]],
        "pair_index": pair_index,
        "pair_names": [cards[deck[pair_index]]["name"] if pair_index < len(deck) else None,
                       cards[deck[pair_index + 1]]["name"] if pair_index + 1 < len(deck) else None],
        "current_answers": [
            {"team_id": row[0], "season": row[1], "team_name": row[2],
             "season_label": row[3] if len(row) > 3 else _sport_season_label(sport, row[1])}
            for row in (blob["shared_per_pair"][pair_index] if pair_index < len(blob["shared_per_pair"]) else [])
        ],
        "solved_links": blob["solved_links"][:max(0, blob["revealed_count"] - 1)],
        "stats": {"hits": blob["hits"], "fouls": blob["fouls"], "strikes": blob["strikes"],
                  "max_strikes": FR_MAX_STRIKES, "consec_fouls": blob["consec_fouls"],
                  "total_pairs": len(deck) - 1},
        "finished": blob["finished"], "won": blob["won"], "last_guess": blob.get("last_guess"),
        "current_streak": current_streak,
    }


def _sport_fr_shared(conn, sport: str, first: str, second: str) -> list[list]:
    rows = conn.execute(
        """SELECT a.team_id, a.season, t.name
             FROM sport_appearances a
             JOIN sport_appearances b ON b.sport_id=a.sport_id
               AND b.team_id=a.team_id AND b.season=a.season
            JOIN sport_teams t ON t.sport_id=a.sport_id AND t.team_id=a.team_id AND t.season=a.season
            WHERE a.sport_id=%s AND a.player_id=%s AND b.player_id=%s
              AND NOT (
                  a.sport_id='football' AND a.season>=2025
                  AND EXISTS (
                      SELECT 1 FROM sport_players pa
                       WHERE pa.sport_id=a.sport_id AND pa.player_id=a.player_id
                         AND pa.debut_year <= a.season - 4
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM sport_appearances prior_a
                       WHERE prior_a.sport_id=a.sport_id AND prior_a.player_id=a.player_id
                         AND prior_a.season BETWEEN a.season - 2 AND a.season - 1
                  )
              )
              AND NOT (
                  b.sport_id='football' AND b.season>=2025
                  AND EXISTS (
                      SELECT 1 FROM sport_players pb
                       WHERE pb.sport_id=b.sport_id AND pb.player_id=b.player_id
                         AND pb.debut_year <= b.season - 4
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM sport_appearances prior_b
                       WHERE prior_b.sport_id=b.sport_id AND prior_b.player_id=b.player_id
                         AND prior_b.season BETWEEN b.season - 2 AND b.season - 1
                  )
              )
            ORDER BY a.season, a.team_id""", (sport, first, second),
    ).fetchall()
    out = [
        [team_id, season, _canonical_sport_team_name(sport, team_id, name), _sport_season_label(sport, season)]
        for team_id, season, name in rows
        if _sport_link_allowed(conn, sport, first, second, team_id, season)
    ]
    seen = set()
    deduped = []
    for row in out:
        key = (normalize(row[2]), row[3])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _film_review_day(value: str | None = None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(CENTRAL_TIME).date()


def _film_review_number(puzzle_day: date) -> int:
    return max(1, (puzzle_day - FILM_REVIEW_EPOCH).days + 1)


def _film_deck_has_verified_headshots(conn, sport: str, deck: list[str]) -> bool:
    if not deck:
        return False
    rows = conn.execute(
        """SELECT COUNT(*) FROM player_headshots
            WHERE sport_id=%s AND status='verified' AND player_id = ANY(%s)""",
        (sport, deck),
    ).fetchone()
    return bool(rows and int(rows[0] or 0) == len(set(deck)))


def _film_puzzle_connections_are_clean(conn, sport: str, deck: list[str]) -> bool:
    """Film Review links should stay readable without forcing one exact answer."""
    if len(deck) < 2:
        return False
    all_shared = _fr_compute_shared(conn, deck) if sport == "baseball" else None
    for index in range(len(deck) - 1):
        shared = all_shared[index] if all_shared is not None else _sport_fr_shared(
            conn, sport, deck[index], deck[index + 1]
        )
        if not shared or len(shared) > FR_MAX_LINK_OPTIONS:
            return False
    return True


def _film_review_autocomplete_options(conn, sport: str, game_id: str, query: str) -> list[dict]:
    """Return team-season guesses inside the current pair's career overlap.

    Film Review lets a player explore an educated wrong answer. The picker is
    therefore broader than the exact solution: it offers every league team for
    seasons both visible players could plausibly have shared, while the guess
    endpoint remains authoritative about whether they actually did.
    """
    row = conn.execute("SELECT state FROM fr_games WHERE game_id=%s", (game_id,)).fetchone()
    if not row:
        return []
    blob = row[0]
    if blob.get("sport", sport) != sport:
        return []
    index, deck = int(blob.get("pair_index", 0)), list(blob.get("deck") or [])
    if index < 0 or index + 1 >= len(deck):
        return []
    first, second = deck[index], deck[index + 1]
    if sport == "baseball":
        years = conn.execute(
            "SELECT player_id, debut_year, final_year FROM players WHERE player_id = ANY(%s)",
            ([first, second],),
        ).fetchall()
        teams = conn.execute(
            "SELECT DISTINCT team_id, name FROM teams WHERE season >= 2000 ORDER BY name", ()
        ).fetchall()
    else:
        years = conn.execute(
            """SELECT player_id, debut_year, final_year FROM sport_players
                 WHERE sport_id=%s AND player_id = ANY(%s)""",
            (sport, [first, second]),
        ).fetchall()
        teams = conn.execute(
            """SELECT DISTINCT team_id, name FROM sport_teams
                 WHERE sport_id=%s AND season >= 2000 ORDER BY name""",
            (sport,),
        ).fetchall()
    spans = {player_id: (max(2000, int(debut or 2000)), min(2026, int(final or 2026)))
             for player_id, debut, final in years}
    if first not in spans or second not in spans:
        return []
    start = max(spans[first][0], spans[second][0])
    end = min(spans[first][1], spans[second][1])
    if start > end:
        return []
    # Let the team part and season part narrow independently. For a cross-year
    # league, typing 2011 can reasonably mean either 2010-11 or 2011-12.
    year_match = re.search(r"(?:^|\s)(\d{1,4})(?:\s*-\s*\d{2,4})?\s*$", query)
    requested_year_text = year_match.group(1) if year_match else ""
    requested_year = int(requested_year_text) if len(requested_year_text) == 4 else None
    team_query = query[:year_match.start()].strip() if year_match else query
    needle = re.sub(r"(.)\1+", r"\1", normalize(team_query))
    names = {}
    for team_id, name in teams:
        label = fr_display_team_name(team_id, start) if sport == "baseball" else _canonical_sport_team_name(sport, team_id, name)
        label_key = re.sub(r"(.)\1+", r"\1", normalize(label))
        if needle in label_key:
            names[normalize(label)] = label
    options = []
    for team_name in sorted(names.values()):
        for season in range(start, end + 1):
            if requested_year_text and len(requested_year_text) < 4:
                if not str(season).startswith(requested_year_text):
                    continue
            elif requested_year is not None:
                year_matches = season == requested_year
                if sport != "baseball":
                    year_matches = year_matches or season + 1 == requested_year
                if not year_matches:
                    continue
            season_label = _sport_season_label(sport, season)
            options.append({"team_name": team_name, "season": season, "season_label": season_label,
                            "label": f"{team_name} {season_label}"})
            if len(options) >= 40:
                return options
    return options


def _valid_daily_film_puzzle(conn, puzzle: dict, sport: str, puzzle_day: date, unit: str | None) -> bool:
    deck = puzzle.get("deck")
    slots = puzzle.get("slots")
    if not isinstance(deck, list) or not isinstance(slots, list):
        return False
    if len(deck) < 2 or len(deck) != len(slots):
        return False
    if sport == "baseball" and tuple(slots) != BASEBALL_FR_SLOTS:
        return False
    if len(set(deck)) != len(deck):
        return False
    if puzzle.get("puzzle_date") != puzzle_day.isoformat():
        return False
    expected_unit = unit or None
    if (puzzle.get("unit") or None) != expected_unit:
        return False
    if sport == "football" and expected_unit not in {"offense", "defense"}:
        return False
    if not _film_deck_has_verified_headshots(conn, sport, deck):
        return False
    if _film_puzzle_repeats_history(conn, sport, puzzle_day, puzzle):
        return False
    return True


def _static_film_puzzle_payload_ready(puzzle: dict, sport: str, puzzle_day: date, unit: str | None) -> bool:
    deck = puzzle.get("deck")
    slots = puzzle.get("slots")
    if not isinstance(deck, list) or not isinstance(slots, list):
        return False
    if len(deck) < 2 or len(deck) != len(slots) or len(set(deck)) != len(deck):
        return False
    if sport == "baseball" and tuple(slots) != BASEBALL_FR_SLOTS:
        return False
    if puzzle.get("puzzle_date") != puzzle_day.isoformat():
        return False
    expected_unit = unit or None
    if (puzzle.get("unit") or None) != expected_unit:
        return False
    if sport == "football" and expected_unit not in {"offense", "defense"}:
        return False
    preview = puzzle.get("preview_cards")
    if not isinstance(preview, list) or len(preview) < 2:
        return False
    card_map = puzzle.get("card_map")
    if _film_card_map_missing_team_stints(card_map, deck):
        return False
    for player_id in deck:
        card = card_map.get(player_id) if isinstance(card_map, dict) else None
        if not isinstance(card, dict) or not card.get("headshot_url"):
            return False
    shared = puzzle.get("shared_per_pair")
    if not isinstance(shared, list) or len(shared) != len(deck) - 1:
        return False
    for pair in shared:
        if not isinstance(pair, list) or not pair or len(pair) > FR_MAX_LINK_OPTIONS:
            return False
        for row in pair:
            if not isinstance(row, list) or len(row) < 3:
                return False
            team_id, season = row[0], row[1]
            if not team_id:
                return False
            try:
                int(season)
            except (TypeError, ValueError):
                return False
    return True


def _build_film_review_puzzle_with_history(conn, sport: str, puzzle_day: date, unit: str | None) -> dict:
    banned_players, banned_opening_players, banned_adjacent_pairs = _film_history_constraints(conn, sport, puzzle_day)
    generator_banned_players = set() if sport == "football" else banned_players
    last_error: Exception | None = None
    for salt in range(80):
        seed_suffix = "" if salt == 0 else f"alt{salt}"
        try:
            if sport == "baseball":
                puzzle = generate_baseball_film_review(
                    conn,
                    puzzle_day,
                    seed_suffix=seed_suffix,
                    banned_opening_players=banned_opening_players,
                    banned_players=generator_banned_players,
                    banned_adjacent_pairs=banned_adjacent_pairs,
                )
            else:
                generated = generate_local_film_review(
                    PgEngineConn(conn),
                    sport,
                    unit=unit,
                    puzzle_day=puzzle_day,
                    seed_suffix=seed_suffix,
                    banned_opening_players=banned_opening_players,
                    banned_players=generator_banned_players,
                    banned_adjacent_pairs=banned_adjacent_pairs,
                )
                puzzle = {
                    "id": f"{sport}_{generated.puzzle_date}_{generated.unit or 'full'}",
                    "puzzle_date": generated.puzzle_date,
                    "slots": list(generated.slots),
                    "deck": list(generated.deck),
                    "unit": generated.unit,
                }
            if (
                not _film_puzzle_repeats_history(conn, sport, puzzle_day, puzzle)
                and _film_puzzle_connections_are_clean(conn, sport, list(puzzle["deck"]))
            ):
                return puzzle
        except Exception as error:
            last_error = error
    if last_error:
        raise RuntimeError(f"could not build a non-repeating {sport} Film Review puzzle: {last_error}")
    raise RuntimeError(f"could not build a non-repeating {sport} Film Review puzzle")


def _daily_film_review_puzzle(conn, sport: str, puzzle_day: date, unit: str | None, builder) -> dict:
    """Return the immutable daily deck, generating it only once if needed."""
    unit_key = unit or ""
    row = conn.execute(
        """SELECT puzzle FROM film_review_daily_puzzles
             WHERE sport_id=%s AND puzzle_date=%s AND unit=%s""",
        (sport, puzzle_day, unit_key),
    ).fetchone()
    if row:
        puzzle = dict(row[0])
        if _static_film_puzzle_payload_ready(puzzle, sport, puzzle_day, unit):
            return puzzle
        if _valid_daily_film_puzzle(conn, puzzle, sport, puzzle_day, unit):
            puzzle = _film_puzzle_with_preview(conn, sport, puzzle_day, unit, puzzle)
            deck = list(puzzle.get("deck") or [])
            shared = puzzle.get("shared_per_pair")
            if isinstance(shared, list) and len(shared) == len(deck) - 1:
                still_resolves = all(bool(pair) for pair in shared)
            elif sport == "baseball":
                still_resolves = all(_fr_compute_shared(conn, deck)[i] for i in range(len(deck) - 1))
            else:
                still_resolves = all(_sport_fr_shared(conn, sport, deck[i], deck[i + 1]) for i in range(len(deck) - 1))
            if still_resolves:
                return puzzle
        app.logger.warning("Replacing invalid Film Review puzzle row: sport=%s date=%s unit=%s",
                           sport, puzzle_day.isoformat(), unit_key)
        conn.execute(
            "DELETE FROM film_review_daily_puzzles WHERE sport_id=%s AND puzzle_date=%s AND unit=%s",
            (sport, puzzle_day, unit_key),
        )
    puzzle = builder()
    puzzle = _film_puzzle_with_preview(conn, sport, puzzle_day, unit, dict(puzzle))
    conn.execute(
        """INSERT INTO film_review_daily_puzzles (sport_id, puzzle_date, unit, puzzle)
             VALUES (%s,%s,%s,%s) ON CONFLICT (sport_id, puzzle_date, unit) DO NOTHING""",
        (sport, puzzle_day, unit_key, Jsonb(puzzle)),
    )
    row = conn.execute(
        """SELECT puzzle FROM film_review_daily_puzzles
             WHERE sport_id=%s AND puzzle_date=%s AND unit=%s""",
        (sport, puzzle_day, unit_key),
    ).fetchone()
    return dict(row[0])


def _store_daily_film_review_puzzle(conn, sport: str, puzzle_day: date, unit: str | None, puzzle: dict) -> None:
    puzzle = _film_puzzle_with_preview(conn, sport, puzzle_day, unit, dict(puzzle))
    conn.execute(
        """INSERT INTO film_review_daily_puzzles (sport_id, puzzle_date, unit, puzzle)
             VALUES (%s,%s,%s,%s) ON CONFLICT (sport_id, puzzle_date, unit) DO NOTHING""",
        (sport, puzzle_day, unit or "", Jsonb(puzzle)),
    )


def _local_film_review_puzzle_dict(conn, sport: str, unit: str | None, puzzle_day: date) -> dict:
    puzzle = generate_local_film_review(PgEngineConn(conn), sport, unit=unit, puzzle_day=puzzle_day)
    return {
        "id": f"{sport}_{puzzle.puzzle_date}_{puzzle.unit or 'full'}",
        "puzzle_date": puzzle.puzzle_date,
        "slots": list(puzzle.slots),
        "deck": list(puzzle.deck),
        "unit": puzzle.unit,
    }


@app.route("/api/sports/<sport>/fr/team_autocomplete")
def sport_fr_team_autocomplete(sport: str):
    q = normalize(request.args.get("q") or "")
    if not _is_cross_sport(sport) or not q:
        return jsonify([])
    game_id = (request.args.get("game_id") or "").strip()
    if game_id:
        with db() as conn:
            return jsonify(_film_review_autocomplete_options(conn, sport, game_id, q))
    names = CURRENT_SPORT_TEAM_NAMES.get(sport)
    if not names:
        with db() as conn:
            names = sorted({_canonical_sport_team_name(sport, team_id, name)
                            for team_id, name in conn.execute(
                                "SELECT DISTINCT team_id, name FROM sport_teams WHERE sport_id=%s", (sport,)
                            ).fetchall()})
    prefix = [name for name in names if normalize(name).startswith(q)]
    contains = [name for name in names if q in normalize(name) and not normalize(name).startswith(q)]
    return jsonify((sorted(prefix) + sorted(contains))[:6])


@app.route("/api/sports/<sport>/fr/archive", methods=["POST"])
def sport_fr_archive(sport: str):
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if not _is_cross_sport(sport) or not guest_id:
        return jsonify({"error": "sport and guest_id required"}), 400
    with db() as conn:
        return jsonify({"days": _film_archive_days(conn, guest_id, sport)})


@app.route("/api/sports/<sport>/fr/new", methods=["POST"])
def sport_fr_new(sport: str):
    ensure_runtime_schema()
    if not _is_cross_sport(sport):
        return jsonify({"error": "unsupported sport"}), 404
    data = request.get_json(silent=True) or {}
    unit = data.get("unit")
    unit = unit.strip().lower() if isinstance(unit, str) else None
    if sport == "football" and unit not in {"offense", "defense"}:
        unit = "offense"
    unit_key = unit or ""
    guest_id = (data.get("guest_id") or "").strip() or None
    archive = bool(data.get("archive"))
    try:
        puzzle_day = _film_review_day(data.get("puzzle_date"))
    except ValueError:
        return jsonify({"error": "invalid puzzle date"}), 400
    if puzzle_day > datetime.now(CENTRAL_TIME).date():
        return jsonify({"error": "future Film Review unavailable"}), 400
    with db() as conn:
        if guest_id and not conn.execute("SELECT 1 FROM guests WHERE guest_id=%s", (guest_id,)).fetchone():
            guest_id = None
        if guest_id and not archive:
            existing = conn.execute(
                "SELECT game_id::text FROM film_review_daily_attempts WHERE owner_guest_id=%s AND sport_id=%s AND puzzle_date=%s AND unit=%s",
                (guest_id, sport, puzzle_day, unit_key),
            ).fetchone()
            if existing:
                row = conn.execute("SELECT state, finished FROM fr_games WHERE game_id=%s", (existing[0],)).fetchone()
                if row:
                    blob, finished = row; blob["finished"] = finished
                    if (blob.get("puzzle_date") == puzzle_day.isoformat()
                            and blob.get("puzzle_number") == _film_review_number(puzzle_day)):
                        _store_daily_film_review_puzzle(conn, sport, puzzle_day, unit, {
                            "id": blob["puzzle_id"], "puzzle_date": blob["puzzle_date"],
                            "slots": blob["slots"], "deck": blob["deck"], "unit": blob.get("unit"),
                        })
                        return jsonify(_sport_fr_state_dict(existing[0], blob, conn))
        try:
            saved_puzzle = _daily_film_review_puzzle(
                conn, sport, puzzle_day, unit,
                lambda: _build_film_review_puzzle_with_history(conn, sport, puzzle_day, unit),
            )
        except (RuntimeError, ValueError) as error:
            return jsonify({"error": f"could not build Film Review: {error}"}), 500
        deck = saved_puzzle["deck"]
        shared = saved_puzzle.get("shared_per_pair")
        if not isinstance(shared, list) or len(shared) != len(deck) - 1:
            shared = [_sport_fr_shared(conn, sport, deck[i], deck[i + 1]) for i in range(len(deck) - 1)]
        if any(not pair for pair in shared):
            return jsonify({"error": "generated puzzle has an unresolved connection"}), 500
        blob = {
            "sport": sport, "puzzle_id": saved_puzzle["id"],
            "puzzle_date": saved_puzzle["puzzle_date"], "puzzle_number": _film_review_number(puzzle_day), "archive": archive,
            "deck": deck, "slots": saved_puzzle["slots"], "unit": saved_puzzle.get("unit"),
            "pair_index": 0, "revealed_count": 2, "hits": 0, "fouls": 0, "strikes": 0,
            "consec_fouls": 0, "solved_links": [None] * (len(deck) - 1),
            "shared_per_pair": shared, "owner_guest_id": guest_id, "result_saved": False,
            "finished": False, "won": False, "last_guess": None,
            "card_map": saved_puzzle.get("card_map") or _film_card_map(conn, sport, deck),
        }
        gid = _insert_game(conn, "fr_games", blob)
        if guest_id:
            if archive:
                conn.execute("""INSERT INTO film_review_daily_attempts (owner_guest_id, sport_id, puzzle_date, unit, game_id, official)
                                VALUES (%s,%s,%s,%s,%s,false)
                                ON CONFLICT (owner_guest_id, sport_id, puzzle_date, unit)
                                DO UPDATE SET game_id=EXCLUDED.game_id, status='in_progress', completed_at=NULL
                                WHERE film_review_daily_attempts.status='in_progress'""",
                             (guest_id, sport, puzzle_day, unit_key, gid))
            else:
                conn.execute("""INSERT INTO film_review_daily_attempts (owner_guest_id, sport_id, puzzle_date, unit, game_id, official)
                                VALUES (%s,%s,%s,%s,%s,true)
                                ON CONFLICT (owner_guest_id, sport_id, puzzle_date, unit)
                                DO UPDATE SET game_id=EXCLUDED.game_id, status='in_progress', completed_at=NULL, official=true""",
                             (guest_id, sport, puzzle_day, unit_key, gid))
        return jsonify(_sport_fr_state_dict(gid, blob, conn))


@app.route("/api/sports/<sport>/fr/guess", methods=["POST"])
def sport_fr_guess(sport: str):
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    gid = data.get("game_id")
    team, year = (data.get("team") or "").strip(), (data.get("year") or "").strip()
    with db() as conn:
        row = conn.execute("SELECT state, finished FROM fr_games WHERE game_id=%s", (gid,)).fetchone()
        if not row or row[0].get("sport") != sport:
            return jsonify({"error": "unknown game_id"}), 404
        blob, finished = row
        blob["finished"] = finished
        if not finished:
            if not team or not year or (_cross_year_season_sports(sport) and _parse_cross_year_season_guess(year) == (None, None)):
                blob["last_guess"] = {"outcome": "invalid", "team": team, "year": year}
            else:
                outcome, matched = _classify_local_fr_guess(team, year, blob["shared_per_pair"][blob["pair_index"]], sport)
                converted = outcome == "foul" and blob["consec_fouls"] + 1 >= 2
                if outcome == "foul":
                    blob["consec_fouls"] += 1
                    blob["fouls"] += 1
                    if converted:
                        outcome = "strike"
                        blob["strikes"] += 1
                        blob["consec_fouls"] = 0
                elif outcome == "hit":
                    blob["consec_fouls"] = 0
                    blob["hits"] += 1
                    match = matched[0]
                    shared = blob["shared_per_pair"][blob["pair_index"]]
                    ordered = [row for row in shared if (row[0], row[1]) == (match[0], match[1])]
                    ordered.extend(row for row in shared if (row[0], row[1]) != (match[0], match[1]))
                    blob["solved_links"][blob["pair_index"]] = [
                        {"team_id": row[0], "season": row[1], "team_name": row[2],
                         "season_label": row[3] if len(row) > 3 else _sport_season_label(sport, row[1])}
                        for row in ordered
                    ]
                    blob["pair_index"] += 1
                    blob["revealed_count"] = min(blob["revealed_count"] + 1, len(blob["deck"]))
                    if blob["hits"] >= len(blob["deck"]) - 1:
                        blob.update({"finished": True, "won": True})
                else:
                    blob["consec_fouls"] = 0
                    blob["strikes"] += 1
                if blob["strikes"] >= FR_MAX_STRIKES:
                    blob.update({"finished": True, "won": False})
                blob["last_guess"] = {"outcome": outcome, "team": team, "year": year,
                                      "converted_from_foul": converted,
                                      "matched": [{"team_id": row[0], "season": row[1], "team_name": row[2],
                                                   "season_label": row[3] if len(row) > 3 else _sport_season_label(sport, row[1])}
                                                  for row in matched]}
            if blob["finished"] and not blob.get("result_saved") and blob.get("owner_guest_id"):
                conn.execute("""INSERT INTO fr_results (owner_guest_id, sport_id, puzzle_id, hits, fouls, strikes, won, unit)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                             (blob["owner_guest_id"], sport, blob["puzzle_id"], blob["hits"], blob["fouls"], blob["strikes"], blob["won"], blob.get("unit")))
                blob["result_saved"] = True
            if blob["finished"] and blob.get("owner_guest_id"):
                conn.execute("""UPDATE film_review_daily_attempts SET status=%s, completed_at=now()
                                WHERE owner_guest_id=%s AND sport_id=%s AND puzzle_date=%s AND unit=%s""",
                             ("won" if blob["won"] else "lost", blob["owner_guest_id"], sport, blob.get("puzzle_date"), blob.get("unit") or ""))
            _save_game(conn, "fr_games", gid, blob)
        return jsonify(_sport_fr_state_dict(gid, blob, conn))


@app.route("/api/sports/<sport>/fr/daily_game", methods=["POST"])
def sport_fr_daily_game(sport: str):
    """Load a completed official daily game for archive review only."""
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    game_id = (data.get("game_id") or "").strip()
    if not _is_cross_sport(sport) or not guest_id or not game_id:
        return jsonify({"error": "sport, guest_id, and game_id required"}), 400
    with db() as conn:
        owned = conn.execute(
            """SELECT 1 FROM film_review_daily_attempts
                 WHERE owner_guest_id=%s AND sport_id=%s AND game_id=%s""",
            (guest_id, sport, game_id),
        ).fetchone()
        row = conn.execute("SELECT state, finished FROM fr_games WHERE game_id=%s", (game_id,)).fetchone()
        if not owned or not row:
            return jsonify({"error": "daily Film Review not found"}), 404
        blob, finished = row
        blob["finished"] = finished
        return jsonify(_sport_fr_state_dict(game_id, blob, conn))


@app.route("/api/sports/<sport>/fr/reveal_answer", methods=["POST"])
def sport_fr_reveal_answer(sport: str):
    gid = (request.get_json(silent=True) or {}).get("game_id")
    with db() as conn:
        row = conn.execute("SELECT state, finished FROM fr_games WHERE game_id=%s", (gid,)).fetchone()
        if not row or row[0].get("sport") != sport:
            return jsonify({"error": "unknown game_id"}), 404
        blob, finished = row
        if not finished:
            return jsonify({"error": "game not finished"}), 400
        cards = _sport_cards(conn, sport, blob["deck"])
        return jsonify({"full_cards": [_sport_fr_card(sport, pid, cards[pid]) for pid in blob["deck"]],
                        "canonical_links": [[{"team_id": row[0], "season": row[1], "team_name": row[2],
                                              "season_label": row[3] if len(row) > 3 else _sport_season_label(sport, row[1])}
                                             for row in pair]
                                            for pair in blob["shared_per_pair"]]})


# ============================================================
# Persistent cross-sport multiplayer (Division Rivalry / Playoffs)
# ============================================================

def _sport_online_insert(conn, sport: str, mode: str, blob: dict) -> str:
    return conn.execute(
        """INSERT INTO sport_online_games (game_id, sport_id, mode, state, finished)
           VALUES (%s, %s, %s, %s, %s) RETURNING game_id::text""",
        (str(uuid.uuid4()), sport, mode, Jsonb(blob), bool(blob.get("finished"))),
    ).fetchone()[0]


def _sport_online_load(conn, sport: str, mode: str, game_id: str):
    row = conn.execute(
        """SELECT state, finished FROM sport_online_games
             WHERE game_id=%s AND sport_id=%s AND mode=%s""", (game_id, sport, mode),
    ).fetchone()
    if not row:
        return None, None
    blob, finished = row
    blob["finished"] = finished
    return blob, deserialize_state(blob)


def _sport_online_save(conn, game_id: str, blob: dict):
    _stamp_sport_online_finished(blob, game_id)
    conn.execute("UPDATE sport_online_games SET state=%s, finished=%s WHERE game_id=%s",
                 (Jsonb(blob), bool(blob.get("finished")), game_id))


def _bot_guest_ids(blob: dict) -> set[str]:
    return {str(item) for item in (blob.get("bot_guest_ids") or []) if item}


def _is_bot_guest(blob: dict, guest_id: str | None) -> bool:
    return bool(guest_id) and guest_id in _bot_guest_ids(blob)


def _bot_postgame_window_seconds(game_id: str) -> float:
    jitter = int(hashlib.sha256(str(game_id).encode("utf-8")).hexdigest()[:4], 16) % BOT_POSTGAME_REMATCH_JITTER_SECONDS
    return float(BOT_POSTGAME_REMATCH_MIN_SECONDS + jitter)


def _stamp_sport_online_finished(blob: dict, game_id: str | None = None) -> None:
    if not blob.get("finished"):
        return
    finished_at = blob.get("finished_at")
    if not finished_at:
        finished_at = now_utc().isoformat()
        blob["finished_at"] = finished_at
    if _bot_guest_ids(blob) and game_id and not blob.get("bot_rematch_expires_at"):
        expires = datetime.fromisoformat(finished_at) + timedelta(seconds=_bot_postgame_window_seconds(game_id))
        blob["bot_rematch_expires_at"] = expires.isoformat()


def _bot_rematch_window_open(blob: dict) -> bool:
    expires_at = blob.get("bot_rematch_expires_at")
    if not expires_at:
        return True
    return now_utc() < datetime.fromisoformat(expires_at)


def _current_turn_guest_id(blob: dict) -> str:
    return blob["p1_guest_id"] if blob["turn_index"] == 0 else blob["p2_guest_id"]


def _bot_queue_wait_seconds(guest_id: str, mode: str, enqueued_at) -> float:
    raw = f"{guest_id}:{mode}:{enqueued_at}".encode("utf-8")
    jitter = int(hashlib.sha256(raw).hexdigest()[:4], 16) % BOT_MATCH_JITTER_SECONDS
    return float(BOT_MATCH_MIN_WAIT_SECONDS + jitter)


def _bot_match_due(guest_id: str, mode: str, enqueued_at) -> bool:
    if not enqueued_at:
        return False
    return (now_utc() - enqueued_at).total_seconds() >= _bot_queue_wait_seconds(guest_id, mode, enqueued_at)


def _playoff_powerups_unlocked(state: GameState) -> bool:
    return max(0, len(state.chain) - 1) >= PLAYOFF_OPENING_LOCK_MOVES


def _playoff_win_conditions_unlocked(state: GameState) -> bool:
    return max(0, len(state.chain) - 1) > PLAYOFF_OPENING_LOCK_MOVES


def _append_no_win_condition_hit(blob: dict, state: GameState) -> None:
    hits = list(blob.get("chain_win_condition_hits") or [])
    while len(hits) < len(state.chain) - 1:
        hits.append(False)
    if len(hits) < len(state.chain):
        hits.append(False)
    blob["chain_win_condition_hits"] = hits


def _bot_next_move_at(blob: dict | None = None) -> str:
    ready_at = now_utc()
    chain_length = 1
    low_clock = False
    if blob and blob.get("turn_started_at"):
        turn_start = datetime.fromisoformat(blob["turn_started_at"])
        ready_at = max(ready_at, turn_start + timedelta(seconds=float(blob.get("countdown_seconds") or 0)))
        chain_length = len(blob.get("chain") or []) or 1
        low_clock = float(blob.get("turn_seconds") or APP_TURN_SECONDS) <= 12
    if low_clock:
        floor, spread = 1.1, 3.2
    elif chain_length < 6:
        floor, spread = 1.6, 4.4
    elif chain_length < 16:
        floor, spread = 2.4, 6.8
    elif chain_length < 32:
        floor, spread = 3.2, 8.6
    else:
        floor, spread = 4.0, 10.5
    think_seconds = floor + (secrets.randbelow(1000) / 1000 * spread)
    return (ready_at + timedelta(seconds=think_seconds)).isoformat()


def _schedule_bot_turn_if_needed(blob: dict) -> None:
    if blob.get("finished"):
        blob.pop("bot_next_move_at", None)
        blob.pop("bot_timeout_at", None)
        return
    if _is_bot_guest(blob, _current_turn_guest_id(blob)):
        blob.setdefault("bot_next_move_at", _bot_next_move_at(blob))
    else:
        blob.pop("bot_next_move_at", None)
        blob.pop("bot_timeout_at", None)


def _bot_turn_timeout_at(blob: dict) -> str:
    turn_start = datetime.fromisoformat(blob["turn_started_at"])
    timeout_at = turn_start + timedelta(
        seconds=float(blob.get("countdown_seconds") or 0) + float(blob.get("turn_seconds") or APP_TURN_SECONDS) + 0.35
    )
    return timeout_at.isoformat()


def _bot_turn_deadline(blob: dict) -> datetime:
    turn_start = datetime.fromisoformat(blob["turn_started_at"])
    return turn_start + timedelta(
        seconds=float(blob.get("countdown_seconds") or 0) + float(blob.get("turn_seconds") or APP_TURN_SECONDS) + 0.35
    )


def _finish_bot_timeout_if_due(blob: dict, bot_side: str) -> bool:
    timeout_at = blob.get("bot_timeout_at")
    if not timeout_at:
        return False
    if now_utc() < datetime.fromisoformat(timeout_at):
        return True
    blob["finished"] = True
    blob["winner"] = blob["p2"] if bot_side == "p1" else blob["p1"]
    blob["last_move"] = {"outcome": "timeout"}
    blob.pop("bot_timeout_at", None)
    return True


def _schedule_bot_timeout_loss(blob: dict) -> None:
    blob["bot_timeout_at"] = _bot_turn_timeout_at(blob)


def _create_transient_bot_guest(conn, mode: str) -> tuple[str, str]:
    guest_id = str(uuid.uuid4())
    label = BOT_NAMES[secrets.randbelow(len(BOT_NAMES))]
    conn.execute(
        "INSERT INTO guests (guest_id, display_name) VALUES (%s, %s)",
        (guest_id, label),
    )
    return guest_id, label


def _ensure_transient_bot_guest(conn, guest_id: str, label: str) -> tuple[str, str]:
    conn.execute(
        """INSERT INTO guests (guest_id, display_name) VALUES (%s, %s)
           ON CONFLICT (guest_id) DO UPDATE SET display_name=EXCLUDED.display_name""",
        (guest_id, label),
    )
    return guest_id, label


def _create_bot_sport_online_match(conn, sport: str, mode: str, guest_id: str,
                                   display_name: str, preference: str | None = None,
                                   bot: tuple[str, str] | None = None,
                                   first_guest_id: str | None = None):
    bot_id, bot_name = _ensure_transient_bot_guest(conn, bot[0], bot[1]) if bot else _create_transient_bot_guest(conn, mode)
    preferences = {guest_id: preference} if preference else {}
    return _sport_online_create(
        conn,
        sport,
        mode,
        (guest_id, display_name),
        (bot_id, bot_name),
        preferences,
        bot_guest_ids={bot_id},
        first_guest_id=first_guest_id,
    )


def _delete_transient_bot_guests(conn, blob: dict, current_game_id: str | None = None) -> None:
    for bot_id in _bot_guest_ids(blob):
        if current_game_id:
            active_ref = conn.execute(
                """SELECT 1
                     FROM sport_online_games
                    WHERE game_id <> %s
                      AND NOT finished
                      AND state->'bot_guest_ids' ? %s
                    LIMIT 1""",
                (current_game_id, bot_id),
            ).fetchone()
        else:
            active_ref = conn.execute(
                """SELECT 1
                     FROM sport_online_games
                    WHERE NOT finished
                      AND state->'bot_guest_ids' ? %s
                    LIMIT 1""",
                (bot_id,),
            ).fetchone()
        if not active_ref:
            conn.execute("DELETE FROM guests WHERE guest_id=%s", (bot_id,))


def _sport_online_expire(blob: dict):
    if blob.get("finished"):
        return
    elapsed = (now_utc() - datetime.fromisoformat(blob["turn_started_at"])).total_seconds()
    if max(0.0, elapsed - blob["countdown_seconds"]) >= blob["turn_seconds"]:
        blob["finished"] = True
        blob["winner"] = blob["p2"] if blob["turn_index"] == 0 else blob["p1"]
        blob["last_move"] = {"outcome": "timeout"}
        blob.pop("bot_timeout_at", None)


def _reap_expired_sport_games(conn, sport: str, mode: str):
    """Finish abandoned serverless games before matchmaking reads active rows.

    A Vercel function cannot rely on a browser poll to enforce a 20-second
    clock. Without this sweep, closing a tab left an unfinished match that
    permanently blocked both players from the queue.
    """
    rows = conn.execute(
        """SELECT game_id::text, state, finished FROM sport_online_games
             WHERE sport_id=%s AND mode=%s AND NOT finished""", (sport, mode),
    ).fetchall()
    for game_id, blob, finished in rows:
        blob["finished"] = finished
        _sport_online_expire(blob)
        if blob["finished"]:
            state = deserialize_state(blob)
            _save_sport_online_result(conn, game_id, blob, state)
            _sport_online_save(conn, game_id, blob)


def _save_sport_online_result(conn, game_id: str, blob: dict, state: GameState):
    if blob.get("result_saved") or not blob.get("finished"):
        return
    sport = blob["sport"]
    p1_id, p2_id = blob["p1_guest_id"], blob["p2_guest_id"]
    p1_won = blob.get("winner") == blob.get("p1")
    for owner, opponent, opponent_name, won in ((p1_id,p2_id,blob["p2"],p1_won),(p2_id,p1_id,blob["p1"],not p1_won)):
        if _is_bot_guest(blob, owner):
            continue
        opponent_for_result = None if _is_bot_guest(blob, opponent) else opponent
        before_row = conn.execute("SELECT elo FROM guest_sport_ratings WHERE guest_id=%s AND sport_id=%s",(owner,sport)).fetchone()
        before = before_row[0] if before_row else 1200; after = max(800,before+(16 if won else -16))
        conn.execute("""INSERT INTO guest_sport_ratings (guest_id,sport_id,elo) VALUES (%s,%s,%s)
                        ON CONFLICT (guest_id,sport_id) DO UPDATE SET elo=EXCLUDED.elo""",(owner,sport,after))
        conn.execute("""INSERT INTO dr_results (game_id,owner_guest_id,opponent_guest_id,opponent_name,chain_length,won,elo_before,elo_after,sport_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (owner_guest_id,game_id) WHERE game_id IS NOT NULL DO NOTHING""",
                     (game_id,owner,opponent_for_result,opponent_name,len(state.chain),won,before,after,sport))
        _record_sport_struck_out_teams(conn,owner,sport,blob["mode"],state)
    blob["result_saved"] = True


def _sport_online_state(conn, game_id: str, blob: dict, state: GameState, viewer: str) -> dict:
    if not _is_bot_guest(blob, _current_turn_guest_id(blob)):
        _sport_online_expire(blob)
    sport = blob["sport"]
    elapsed = (now_utc() - datetime.fromisoformat(blob["turn_started_at"])).total_seconds()
    countdown = max(0.0, blob["countdown_seconds"] - elapsed) if not blob["finished"] else 0.0
    remaining = max(0.0, blob["turn_seconds"] - max(0.0, elapsed - blob["countdown_seconds"])) if not blob["finished"] else 0.0
    side = "p1" if viewer == blob["p1_guest_id"] else "p2"
    other = "p2" if side == "p1" else "p1"
    chain = _sport_chain_dict(conn, sport, state)
    last_move = dict(blob.get("last_move") or {})
    for field in ("shared_seasons", "burned_seasons"):
        for item in last_move.get(field, []):
            item["team_name"] = _sport_team_name(conn, sport, item["team_id"], item["season"])
            item["season_label"] = _sport_season_label(sport, item["season"])
    output = {
        # The browser uses `mp` for Division Rivalry. `dr` is only the
        # database route/table mode; leaking it here disabled the shared
        # multiplayer timer and polling client logic.
        "game_id": game_id, "mode": "mp" if blob["mode"] == "dr" else "po", "sport": sport,
        "current_player": {"id": state.current_player_id, "name": _sport_display_name(sport, state.current_player_id, fallback=state.current_player_name)},
        "current_label": blob["p1"] if blob["turn_index"] == 0 else blob["p2"],
        "p1": blob["p1"], "p2": blob["p2"], "p1_guest_id": blob["p1_guest_id"], "p2_guest_id": blob["p2_guest_id"],
        "viewer_guest_id": viewer, "your_side": side, "your_name": blob[side], "opponent_name": blob[other],
        "your_turn": not blob["finished"] and ((side == "p1" and blob["turn_index"] == 0) or (side == "p2" and blob["turn_index"] == 1)),
        "turn_index": blob["turn_index"], "turn_seconds": blob["turn_seconds"],
        "countdown_seconds_remaining": countdown, "remaining_seconds": remaining,
        "chain": chain, "strikes": _sport_strikes_dict(conn, sport, state),
        "finished": blob["finished"], "winner": blob.get("winner"), "last_move": last_move,
    }
    if blob["mode"] == "po":
        links, hits = blob.get("chain_link_meta", []), blob.get("chain_win_condition_hits", [])
        for i, player in enumerate(chain):
            player["link_meta_with_prev"] = links[i] if i < len(links) else None
            player["win_condition_hit"] = bool(hits[i]) if i < len(hits) else False
        if sport == "baseball":
            conditions, powers = PLAYOFF_WIN_CONDITIONS, PLAYOFF_POWERUPS
        else:
            conditions, powers = LOCAL_PLAYOFF_CONFIG[sport]["conditions"], LOCAL_PLAYOFF_CONFIG[sport]["powerups"]
        def cp(key):
            condition = conditions[blob[f"{key}_win_condition_key"]]
            return {"key": blob[f"{key}_win_condition_key"], "label": condition["label"], "description": condition["description"],
                    "target": condition["target"], "progress": blob.get(f"{key}_win_progress", 0), "completed": blob.get(f"{key}_win_completed", False)}
        def pp(key):
            used = set(blob.get(f"{key}_powerup_used_keys", []))
            return [{"key": k, "label": v["label"], "description": v["description"], "kind": v["kind"], "used": k in used, "owner": blob[key]} for k, v in powers.items()]
        output["default_turn_seconds"] = APP_TURN_SECONDS
        output["powerups"] = {"your_powerups": pp(side), "opponent_powerups": pp(other),
            "active_turn_powerup": ({"key": blob["active_turn_powerup"], "label": powers[blob["active_turn_powerup"]]["label"]} if blob.get("active_turn_powerup") else None),
            "turn_powerup_used": bool(blob.get("turn_powerup_used")),
            "opening_lock_moves": PLAYOFF_OPENING_LOCK_MOVES}
        output["win_conditions"] = {"your_condition": cp(side), "opponent_condition": cp(other)}
    return output


def _bot_loss_chance(chain_length: int) -> float:
    if chain_length < 12:
        return 0.0
    if chain_length < 18:
        return 0.05
    if chain_length < 28:
        return 0.11
    if chain_length < 40:
        return 0.22
    if chain_length < 52:
        return 0.36
    return 0.52


def _bot_unused_powerup_count(sport: str, blob: dict, side: str) -> int:
    if blob.get("mode") != "po":
        return 0
    powers = PLAYOFF_POWERUPS if sport == "baseball" else LOCAL_PLAYOFF_CONFIG[sport]["powerups"]
    used = set(blob.get(f"{side}_powerup_used_keys") or [])
    return max(0, len(powers) - len(used))


def _bot_total_powerup_count(sport: str, blob: dict) -> int:
    if blob.get("mode") != "po":
        return 0
    powers = PLAYOFF_POWERUPS if sport == "baseball" else LOCAL_PLAYOFF_CONFIG[sport]["powerups"]
    return len(powers)


def _bot_planned_loss_chance(sport: str, mode: str, blob: dict, state: GameState, side: str) -> float:
    if mode != "po":
        return _bot_loss_chance(len(state.chain))

    if not _playoff_powerups_unlocked(state):
        return 0.0

    chance = _bot_loss_chance(len(state.chain))
    unused = _bot_unused_powerup_count(sport, blob, side)
    if unused >= _bot_total_powerup_count(sport, blob):
        return 0.0
    if unused >= 5:
        return chance * 0.12
    if unused >= 3:
        return chance * 0.22
    if unused >= 1:
        return chance * 0.45
    return chance


def _bot_should_schedule_timeout_loss(sport: str, mode: str, blob: dict, state: GameState, side: str) -> bool:
    chance = _bot_planned_loss_chance(sport, mode, blob, state, side)
    if chance <= 0:
        return False
    return secrets.randbelow(10000) < int(chance * 10000)


def _bot_should_try_powerup(sport: str, blob: dict, state: GameState, side: str) -> bool:
    if blob.get("mode") != "po" or not _playoff_powerups_unlocked(state):
        return False
    # The first unlocked turn was causing the bot to leave the stable normal
    # chain path. Let the game breathe before powerups enter the simulation.
    if len(state.chain) < 12:
        return False
    total = _bot_total_powerup_count(sport, blob)
    unused = _bot_unused_powerup_count(sport, blob, side)
    if total <= 0 or unused <= 0:
        return False
    spent = total - unused
    chain_length = len(state.chain)
    if spent == 0:
        chance = 45 if chain_length < 18 else 70
    elif chain_length < 22:
        chance = 34
    elif chain_length < 36:
        chance = 48
    else:
        chance = 64
    return secrets.randbelow(100) < chance


def _bot_candidate_rows(conn, sport: str, current_player_id: str, used: list[str]) -> list[tuple[str, str, int]]:
    used = used or [current_player_id]
    if sport == "baseball":
        return conn.execute(
            """
            WITH current_key AS (
                SELECT player_key FROM compact_player_keys
                 WHERE scope='baseball' AND player_id=%s
            ),
            candidate_keys AS (
                SELECT CASE
                         WHEN proof.player_a_key = current_key.player_key THEN proof.player_b_key
                         ELSE proof.player_a_key
                       END AS player_key
                  FROM compact_mlb_teammate_game_proofs proof
                  JOIN current_key
                    ON proof.player_a_key = current_key.player_key
                    OR proof.player_b_key = current_key.player_key
                 GROUP BY 1
            )
            SELECT ps.player_id, ps.display_name, ps.career_games
              FROM candidate_keys ck
              JOIN compact_player_keys pk
                ON pk.scope='baseball' AND pk.player_key=ck.player_key
              JOIN players_searchable ps ON ps.player_id=pk.player_id
             WHERE NOT (ps.player_id = ANY(%s))
             ORDER BY ps.career_games DESC, ps.player_id
             LIMIT 1000
            """,
            (current_player_id, used),
        ).fetchall()
    return conn.execute(
        """
        WITH current_key AS (
            SELECT player_key FROM compact_player_keys
             WHERE scope=%s AND player_id=%s
        ),
        candidate_keys AS (
            SELECT CASE
                     WHEN proof.player_a_key = current_key.player_key THEN proof.player_b_key
                     ELSE proof.player_a_key
                   END AS player_key
              FROM compact_sport_teammates proof
              JOIN current_key
                ON proof.sport_id = %s
               AND (proof.player_a_key = current_key.player_key
                    OR proof.player_b_key = current_key.player_key)
             GROUP BY 1
        )
        SELECT ps.player_id, ps.display_name, ps.career_games
          FROM candidate_keys ck
          JOIN compact_player_keys pk
            ON pk.scope=%s AND pk.player_key=ck.player_key
          JOIN sport_players_searchable ps
            ON ps.sport_id=%s AND ps.player_id=pk.player_id
         WHERE NOT (ps.player_id = ANY(%s))
         ORDER BY ps.career_games DESC, ps.player_id
         LIMIT 1000
        """,
        (sport, current_player_id, sport, sport, sport, used),
    ).fetchall()


def _bot_ordered_candidates(rows: list[tuple[str, str, int]], chain_length: int) -> list[str]:
    if not rows:
        return []
    top = rows[:24]
    middle = rows[24:120]
    deep = rows[120:]
    obscure_pull = secrets.randbelow(100) < min(24, 4 + max(0, chain_length - 10))
    if obscure_pull and (middle or deep):
        primary = (deep[:120] if deep and secrets.randbelow(100) < 45 else middle) or top
    elif chain_length >= 28 and middle and secrets.randbelow(100) < 35:
        primary = middle
    else:
        primary = top or middle or deep
    picked = [row[0] for row in primary]
    random.shuffle(picked)
    fallback = [row[0] for row in rows if row[0] not in set(picked)]
    random.shuffle(fallback)
    return picked + fallback


def _bot_condition_increment(conn, sport: str, condition_key: str | None, player_id: str) -> int:
    if not condition_key:
        return 0
    if sport == "baseball":
        return _playoff_condition_increment(condition_key, _playoff_trait_row(conn, player_id))
    if sport in LOCAL_PLAYOFF_CONFIG and condition_key in LOCAL_PLAYOFF_CONFIG[sport]["conditions"]:
        return _local_po_condition_increment(PgEngineConn(conn), sport, condition_key, player_id)
    return 0


def _bot_prioritized_candidates(conn, sport: str, mode: str, blob: dict, side: str,
                                rows: list[tuple[str, str, int]], chain_length: int) -> list[str]:
    ordered = _bot_ordered_candidates(rows, chain_length)
    if mode != "po" or not ordered:
        return ordered
    condition_key = blob.get(f"{side}_win_condition_key")
    condition = (PLAYOFF_WIN_CONDITIONS if sport == "baseball" else LOCAL_PLAYOFF_CONFIG[sport]["conditions"]).get(condition_key, {})
    target = int(condition.get("target") or 0)
    progress = int(blob.get(f"{side}_win_progress") or 0)
    hits: list[tuple[int, str]] = []
    rest: list[str] = []
    for candidate_id in ordered[:120]:
        inc = _bot_condition_increment(conn, sport, condition_key, candidate_id)
        if inc > 0:
            priority = 0 if target and progress + inc >= target else 1
            hits.append((priority, candidate_id))
        else:
            rest.append(candidate_id)
    tail = ordered[120:] + [candidate_id for candidate_id in ordered[:120] if candidate_id not in rest and all(candidate_id != hit[1] for hit in hits)]
    if hits and hits[0][0] == 0 and secrets.randbelow(100) < BOT_WIN_CONDITION_MISS_PERCENT:
        return rest + [candidate_id for _priority, candidate_id in hits] + tail
    random.shuffle(hits)
    return [candidate_id for _priority, candidate_id in sorted(hits, key=lambda item: item[0])] + rest + tail


def _bot_powerup_candidates(conn, sport: str, state: GameState, powerup_key: str) -> list[str]:
    rows = _bot_candidate_rows(conn, sport, state.current_player_id, state.chain)
    limit = 350 if len(state.chain) < 12 else 150
    return _bot_ordered_candidates(rows, len(state.chain))[:limit]


def _bot_try_powerup_move(conn, sport: str, blob: dict, state: GameState, powerup_key: str) -> dict | None:
    original_key = blob.get("active_turn_powerup")
    blob["active_turn_powerup"] = powerup_key
    moved = False
    try:
        for candidate_id in _bot_powerup_candidates(conn, sport, state, powerup_key):
            if sport == "baseball":
                payload = _apply_playoff_powerup_move(conn, state, blob, player_id=candidate_id)
            else:
                payload = _local_po_powerup_move(PgEngineConn(conn), blob, raw="", player_id=candidate_id)
            if payload and payload.get("outcome") == "valid":
                moved = True
                return payload
        return None
    finally:
        if not moved:
            blob["active_turn_powerup"] = original_key


def _bot_activate_powerup(conn, sport: str, blob: dict, state: GameState, side: str) -> dict | None:
    if not _playoff_powerups_unlocked(state):
        return None
    if not _bot_should_try_powerup(sport, blob, state, side):
        return None
    if blob.get("turn_powerup_used") or blob.get("active_turn_powerup"):
        return None
    powers = PLAYOFF_POWERUPS if sport == "baseball" else LOCAL_PLAYOFF_CONFIG[sport]["powerups"]
    used = set(blob.get(f"{side}_powerup_used_keys") or [])
    available = [key for key in powers if key not in used]
    if not available:
        return None
    low_clock = float(blob.get("turn_seconds") or APP_TURN_SECONDS) <= 12
    def rank(key: str) -> tuple[int, int]:
        kind = powers[key].get("kind")
        if kind in {"time", "timer"} and (low_clock or len(state.chain) >= 10):
            return (0, secrets.randbelow(100))
        if kind in {"skill", "stat", "same_position", "position", "veteran"}:
            return (1, secrets.randbelow(100))
        if kind == "pressure" or key == "quick_pitch":
            return (2 if len(state.chain) >= 8 or secrets.randbelow(100) < 30 else 3, secrets.randbelow(100))
        return (3, secrets.randbelow(100))
    for key in sorted(available, key=rank):
        meta = powers[key]
        blob[f"{side}_powerup_used_keys"].append(key)
        blob["turn_powerup_used"] = True
        kind = meta.get("kind")
        if kind in {"time", "timer"} and key != "quick_pitch":
            blob["turn_seconds"] += float(meta.get("bonus_seconds") or 0)
            return {"outcome": "powerup_activated", "powerup_key": key, "powerup_label": meta["label"]}
        if kind == "pressure" or key == "quick_pitch":
            blob["next_turn_seconds_override"] = QUICK_PITCH_TURN_SECONDS
            return {"outcome": "powerup_activated", "powerup_key": key, "powerup_label": meta["label"]}
        blob["turn_seconds"] += float(meta.get("bonus_seconds") or 0)
        blob["active_turn_powerup"] = key
        payload = _bot_try_powerup_move(conn, sport, blob, state, key)
        if payload:
            return payload
        blob["active_turn_powerup"] = None
        blob["turn_powerup_used"] = False
        blob[f"{side}_powerup_used_keys"].remove(key)
        blob["turn_seconds"] -= float(meta.get("bonus_seconds") or 0)
    return None


def _bot_choose_move(conn, sport: str, mode: str, blob: dict, side: str, state: GameState) -> MoveResult | None:
    rows = _bot_candidate_rows(conn, sport, state.current_player_id, state.chain)
    candidates = _bot_prioritized_candidates(conn, sport, mode, blob, side, rows, len(state.chain))
    if mode == "po" and len(state.chain) < 12:
        limit = len(candidates)
    else:
        limit = 500 if len(state.chain) < 12 else 220
    for candidate_id in candidates[:limit]:
        trial = validate_and_apply_move(
            state,
            PgEngineConn(conn),
            player_id=candidate_id,
            track_strikes=True,
            sport=_engine_sport(sport),
        )
        if trial.outcome == MoveOutcome.VALID:
            return trial
    return None


def _sport_online_apply_valid_payload(conn, sport: str, mode: str, blob: dict, state: GameState,
                                      payload: dict, mover: str, record_usage: bool) -> None:
    if mode == "po":
        if sport == "baseball":
            if _playoff_win_conditions_unlocked(state):
                update = _apply_playoff_win_condition_hit(conn, blob, payload["player_id"], mover)
            else:
                condition = PLAYOFF_WIN_CONDITIONS.get(blob.get(f"{mover}_win_condition_key"), {})
                _append_no_win_condition_hit(blob, state)
                update = {"hit": False, "label": condition.get("label"),
                          "progress": int(blob.get(f"{mover}_win_progress") or 0),
                          "target": condition.get("target", 0), "completed": False}
            payload["win_condition_hit"] = update["hit"]
            payload["win_condition_label"] = update["label"]
            payload["win_condition_progress"] = update["progress"]
            payload["win_condition_target"] = update["target"]
            payload["win_condition_completed"] = update["completed"]
            if len(blob.get("chain_link_meta", [])) < len(state.chain):
                blob.setdefault("chain_link_meta", []).append(None)
            if update["completed"]:
                blob["finished"] = True
                blob["winner"] = blob[mover]
        else:
            condition_key = blob[f"{mover}_win_condition_key"]
            condition = LOCAL_PLAYOFF_CONFIG[sport]["conditions"][condition_key]
            if _playoff_win_conditions_unlocked(state):
                inc = _local_po_condition_increment(PgEngineConn(conn), sport, condition_key, payload["player_id"])
                blob[f"{mover}_win_progress"] += inc
                blob["chain_win_condition_hits"].append(bool(inc))
            else:
                inc = 0
                _append_no_win_condition_hit(blob, state)
            if len(blob.get("chain_link_meta", [])) < len(state.chain):
                blob.setdefault("chain_link_meta", []).append(None)
            completed = blob[f"{mover}_win_progress"] >= condition["target"]
            payload["win_condition_hit"] = bool(inc)
            payload["win_condition_label"] = condition["label"]
            payload["win_condition_progress"] = blob[f"{mover}_win_progress"]
            payload["win_condition_target"] = condition["target"]
            payload["win_condition_completed"] = completed
            if completed:
                blob[f"{mover}_win_completed"] = True
                blob["finished"] = True
                blob["winner"] = blob[mover]
    if record_usage:
        if sport == "baseball":
            _record_player_usage(conn, payload["player_id"], mode)
        _record_sport_player_usage(conn, sport, payload["player_id"], mode)
    if not blob["finished"]:
        blob["turn_index"] = 1 - blob["turn_index"]
        blob["turn_started_at"] = now_utc().isoformat()
        blob["countdown_seconds"] = 0.0
        if mode == "po":
            blob["turn_seconds"] = float(blob.get("next_turn_seconds_override") or APP_TURN_SECONDS)
            blob["next_turn_seconds_override"] = None
            blob["active_turn_powerup"] = None
            blob["turn_powerup_used"] = False
    _schedule_bot_turn_if_needed(blob)


def _sport_online_maybe_advance_bot(conn, game_id: str, blob: dict, state: GameState) -> None:
    if not _is_bot_guest(blob, _current_turn_guest_id(blob)):
        _sport_online_expire(blob)
        if blob.get("finished"):
            _save_sport_online_result(conn, game_id, blob, state)
            return
        before = blob.get("bot_next_move_at")
        _schedule_bot_turn_if_needed(blob)
        if before != blob.get("bot_next_move_at"):
            _sport_online_save(conn, game_id, blob)
        return
    next_move = blob.get("bot_next_move_at")
    if not next_move:
        _schedule_bot_turn_if_needed(blob)
        _sport_online_save(conn, game_id, blob)
        return
    sport, mode = blob["sport"], blob["mode"]
    bot_side = "p1" if blob["turn_index"] == 0 else "p2"
    if _finish_bot_timeout_if_due(blob, bot_side):
        if blob.get("finished"):
            blob.pop("bot_next_move_at", None)
            _save_sport_online_result(conn, game_id, blob, state)
        _sport_online_save(conn, game_id, blob)
        return
    next_move_at = datetime.fromisoformat(next_move)
    now = now_utc()
    deadline = _bot_turn_deadline(blob)
    if now < next_move_at and now < deadline:
        return
    if next_move_at > deadline and now >= deadline:
        blob["finished"] = True
        blob["winner"] = blob["p2"] if bot_side == "p1" else blob["p1"]
        blob["last_move"] = {"outcome": "timeout"}
        blob.pop("bot_next_move_at", None)
        blob.pop("bot_timeout_at", None)
        _save_sport_online_result(conn, game_id, blob, state)
        _sport_online_save(conn, game_id, blob)
        return
    if _bot_should_schedule_timeout_loss(sport, mode, blob, state, bot_side):
        _schedule_bot_timeout_loss(blob)
    else:
        payload = _bot_activate_powerup(conn, sport, blob, state, bot_side) if mode == "po" else None
        if not payload or payload.get("outcome") != "valid":
            result = _bot_choose_move(conn, sport, mode, blob, bot_side, state)
            payload = result_to_dict(result) if result else None
            if mode == "po" and result and result.outcome != MoveOutcome.VALID:
                if sport == "baseball":
                    alternate = _apply_playoff_powerup_move(conn, state, blob, player_id=result.player_id)
                else:
                    alternate = _local_po_powerup_move(PgEngineConn(conn), blob, raw="", player_id=result.player_id)
                payload = alternate or payload
        if not payload or payload.get("outcome") != "valid":
            _schedule_bot_timeout_loss(blob)
        else:
            blob.pop("bot_timeout_at", None)
            blob.update(serialize_state(state))
            blob["last_move"] = payload
            _sport_online_apply_valid_payload(conn, sport, mode, blob, state, payload, bot_side, record_usage=False)
    if blob.get("finished"):
        blob.pop("bot_next_move_at", None)
        _save_sport_online_result(conn, game_id, blob, state)
    _sport_online_save(conn, game_id, blob)


def _sport_online_create(conn, sport: str, mode: str, a: tuple[str, str], b: tuple[str, str],
                         preferences=None, bot_guest_ids: set[str] | None = None,
                         first_guest_id: str | None = None):
    if first_guest_id == a[0]:
        (p1_id, p1), (p2_id, p2) = a, b
    elif first_guest_id == b[0]:
        (p1_id, p1), (p2_id, p2) = b, a
    else:
        (p1_id, p1), (p2_id, p2) = (a, b) if secrets.randbelow(2) else (b, a)
    seed_player_id = DEFAULT_SEED if sport == "baseball" else LOCAL_SPORT_SEEDS[sport]
    state = seed_game(PgEngineConn(conn), seed_player_id, sport=_engine_sport(sport))
    bot_guest_ids = set(bot_guest_ids or set())
    if not bot_guest_ids:
        if sport == "baseball":
            _record_player_usage(conn, state.current_player_id, mode)
        _record_sport_player_usage(conn, sport, state.current_player_id, mode)
    blob = dr_blob_from_state(state, p1, p2, 0, APP_TURN_SECONDS, now_utc(), OPENING_COUNTDOWN_SECONDS,
                              p1_guest_id=p1_id, p2_guest_id=p2_id, seed_player_id=state.current_player_id)
    blob.update({"sport": sport, "mode": mode})
    if bot_guest_ids:
        blob["bot_guest_ids"] = sorted(bot_guest_ids)
    if mode == "po":
        conditions = PLAYOFF_WIN_CONDITIONS if sport == "baseball" else LOCAL_PLAYOFF_CONFIG[sport]["conditions"]
        preferences = preferences or {}
        def choose(guest_id: str) -> str:
            preferred = preferences.get(guest_id)
            if preferred in conditions:
                return preferred
            return _random_playoff_condition_for_guest(conn, guest_id, sport, conditions)
        def preference_marker(guest_id: str, chosen: str) -> str:
            preferred = preferences.get(guest_id)
            return "random" if preferred in {None, "", "random"} or preferred not in conditions else chosen
        p1_condition = choose(p1_id)
        p2_condition = choose(p2_id)
        blob.update({"active_turn_powerup": None, "next_turn_seconds_override": None, "turn_powerup_used": False,
                     "p1_powerup_used_keys": [], "p2_powerup_used_keys": [],
                     "p1_win_condition_key": p1_condition, "p2_win_condition_key": p2_condition,
                     "p1_win_condition_preference": preference_marker(p1_id, p1_condition),
                     "p2_win_condition_preference": preference_marker(p2_id, p2_condition),
                     "p1_win_progress": 0, "p2_win_progress": 0, "p1_win_completed": False, "p2_win_completed": False,
                     "chain_win_condition_hits": [False], "chain_link_meta": [None]})
    _schedule_bot_turn_if_needed(blob)
    return _sport_online_insert(conn, sport, mode, blob), blob, state


def _sport_online_rematch_first_guest_id(blob: dict) -> str | None:
    return blob.get("p2_guest_id") if blob.get("p1_guest_id") else None


def _sport_online_rematch_preferences(blob: dict) -> dict[str, str]:
    if not blob.get("p1_win_condition_key") and not blob.get("p2_win_condition_key"):
        return {}
    preferences: dict[str, str] = {}
    for side in ("p1", "p2"):
        guest_id = blob.get(f"{side}_guest_id")
        if not guest_id:
            continue
        marker = blob.get(f"{side}_win_condition_preference")
        key = blob.get(f"{side}_win_condition_key")
        if marker == "random":
            preferences[guest_id] = "random"
        elif key:
            preferences[guest_id] = key
    return preferences


def _sport_online_status(conn, sport: str, mode: str, guest_id: str):
    _reap_expired_sport_games(conn, sport, mode)
    row = conn.execute("""SELECT game_id::text, state, finished FROM sport_online_games
                         WHERE sport_id=%s AND mode=%s AND NOT finished
                           AND (state->>'p1_guest_id'=%s OR state->>'p2_guest_id'=%s)
                         ORDER BY created_at DESC LIMIT 1""", (sport, mode, guest_id, guest_id)).fetchone()
    if row:
        gid, blob, finished = row; blob["finished"] = finished
        state = deserialize_state(blob)
        _sport_online_maybe_advance_bot(conn, gid, blob, state)
        return {"status": "matched", "game": _sport_online_state(conn, gid, blob, state, guest_id)}
    waiting = conn.execute(
        """SELECT display_name, preference, enqueued_at
             FROM sport_online_queue
            WHERE sport_id=%s AND mode=%s AND guest_id=%s""",
        (sport, mode, guest_id),
    ).fetchone()
    if waiting and _bot_match_due(guest_id, mode, waiting[2]):
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(4412000 + %s)", (0 if mode == "dr" else 1,))
            locked = conn.execute(
                """SELECT display_name, preference, enqueued_at
                     FROM sport_online_queue
                    WHERE sport_id=%s AND mode=%s AND guest_id=%s
                    FOR UPDATE""",
                (sport, mode, guest_id),
            ).fetchone()
            if locked and _bot_match_due(guest_id, mode, locked[2]):
                conn.execute(
                    "DELETE FROM sport_online_queue WHERE sport_id=%s AND mode=%s AND guest_id=%s",
                    (sport, mode, guest_id),
                )
                conn.execute("DELETE FROM multi_sport_queue WHERE guest_id=%s", (guest_id,))
                gid, blob, state = _create_bot_sport_online_match(conn, sport, mode, guest_id, locked[0], locked[1])
                return {"status": "matched", "game": _sport_online_state(conn, gid, blob, state, guest_id)}
    return {"status": "waiting", "guest_id": guest_id} if waiting else {"status": "idle"}


def _sport_online_requeue(conn, sport: str, mode: str, guest_id: str, avoid_guest_id: str | None,
                          preference: str | None = None):
    conn.execute(
        """INSERT INTO sport_online_queue (sport_id, mode, guest_id, display_name, preference, avoid_guest_id)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (sport_id, mode, guest_id) DO UPDATE
             SET display_name=EXCLUDED.display_name, preference=EXCLUDED.preference,
                 avoid_guest_id=EXCLUDED.avoid_guest_id,
                 enqueued_at=now()""",
        (sport, mode, guest_id, _guest_label(conn, guest_id), preference, avoid_guest_id),
    )


MULTI_QUEUE_SPORTS = ("baseball", "basketball", "hockey", "football")


def _multi_mode_from_slug(mode: str) -> str | None:
    return {"division": "dr", "division-rivalry": "dr", "playoffs": "po", "dr": "dr", "po": "po"}.get(mode)


def _multi_client_mode(mode: str) -> str:
    return "po" if mode == "po" else "mp"


def _multi_redirect(sport: str, mode: str, game_id: str) -> str:
    source = "playoffs" if mode == "po" else "division"
    return f"/{sport}?mode={_multi_client_mode(mode)}&game_id={game_id}&source={source}"


def _normalize_multi_sports(raw_sports) -> list[str]:
    sports = raw_sports if isinstance(raw_sports, list) else []
    cleaned: list[str] = []
    for sport in sports:
        sport = str(sport).strip().lower()
        if sport in MULTI_QUEUE_SPORTS and sport not in cleaned:
            cleaned.append(sport)
    return cleaned


def _load_active_multisport_game(conn, guest_id: str, mode: str, sports: list[str]):
    sport_list = [sport for sport in sports if sport in MULTI_QUEUE_SPORTS]
    if sport_list:
        for sport in sport_list:
            _reap_expired_sport_games(conn, sport, mode)
        row = conn.execute(
            """SELECT sport_id, game_id::text
                 FROM sport_online_games
                WHERE sport_id = ANY(%s)
                  AND mode = %s
                  AND NOT finished
                  AND ((state->>'p1_guest_id') = %s OR (state->>'p2_guest_id') = %s)
                ORDER BY created_at DESC
                LIMIT 1""",
            (sport_list, mode, guest_id, guest_id),
        ).fetchone()
        if row:
            return row[0], row[1]
    return None


def _create_multisport_match(conn, sport: str, mode: str, a: tuple[str, str], b: tuple[str, str], preferences: dict | None):
    game_id, _blob, _state = _sport_online_create(conn, sport, mode, a, b, preferences or {})
    return game_id


@app.route("/api/modes/<mode>/queue", methods=["POST"])
def multi_sport_queue(mode: str):
    ensure_runtime_schema()
    queue_mode = _multi_mode_from_slug(mode)
    if not queue_mode:
        return jsonify({"error": "unsupported mode"}), 404
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    sports = _normalize_multi_sports(data.get("sports"))
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    if not sports:
        return jsonify({"error": "choose at least one sport"}), 400
    with db() as conn:
        if not conn.execute("SELECT 1 FROM guests WHERE guest_id = %s", (guest_id,)).fetchone():
            return jsonify({"error": "unknown guest_id"}), 404
        active = _load_active_multisport_game(conn, guest_id, queue_mode, sports)
        if active:
            sport, game_id = active
            return jsonify({
                "status": "matched",
                "sport": sport,
                "game_id": game_id,
                "redirect": _multi_redirect(sport, queue_mode, game_id),
            })

        display_name = _guest_label(conn, guest_id)
        avoid_guest_id = (data.get("avoid_guest_id") or "").strip() or None
        preferences = data.get("preferences") if isinstance(data.get("preferences"), dict) else {}
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(4412000 + %s)", (0 if queue_mode == "dr" else 1,))
            direct = conn.execute(
                """SELECT guest_id::text, display_name, preference, sport_id
                     FROM sport_online_queue
                    WHERE mode=%s AND sport_id = ANY(%s) AND guest_id<>%s
                    ORDER BY enqueued_at LIMIT 1 FOR UPDATE SKIP LOCKED""",
                (queue_mode, sports, guest_id),
            ).fetchone()
            if direct:
                opponent_id, opponent_name, opponent_preference, sport = direct
                conn.execute("DELETE FROM sport_online_queue WHERE mode=%s AND guest_id=%s", (queue_mode, opponent_id))
                game_id = _create_multisport_match(conn, sport, queue_mode, (opponent_id, opponent_name),
                    (guest_id, display_name), {opponent_id: opponent_preference, guest_id: preferences.get(sport)})
                return jsonify({"status":"matched","sport":sport,"game_id":game_id,"redirect":_multi_redirect(sport,queue_mode,game_id)})
            rows = conn.execute(
                """SELECT guest_id::text, display_name, sports, preference
                     FROM multi_sport_queue
                    WHERE mode = %s
                      AND guest_id <> %s
                      AND (avoid_guest_id IS NULL OR avoid_guest_id <> CAST(%s AS uuid))
                      AND (CAST(%s AS uuid) IS NULL OR guest_id <> CAST(%s AS uuid))
                    ORDER BY enqueued_at
                    LIMIT 25
                    FOR UPDATE SKIP LOCKED""",
                (queue_mode, guest_id, guest_id, avoid_guest_id, avoid_guest_id),
            ).fetchall()
            for opponent_id, opponent_name, opponent_sports, opponent_preferences in rows:
                overlap = [sport for sport in sports if sport in (opponent_sports or [])]
                if not overlap:
                    continue
                sport = overlap[secrets.randbelow(len(overlap))]
                conn.execute(
                    "DELETE FROM multi_sport_queue WHERE guest_id IN (%s, %s)",
                    (guest_id, opponent_id),
                )
                game_id = _create_multisport_match(
                    conn,
                    sport,
                    queue_mode,
                    (opponent_id, opponent_name),
                    (guest_id, display_name),
                    {opponent_id: (opponent_preferences or {}).get(sport), guest_id: preferences.get(sport)},
                )
                return jsonify({
                    "status": "matched",
                    "sport": sport,
                    "game_id": game_id,
                    "redirect": _multi_redirect(sport, queue_mode, game_id),
                })
            conn.execute(
                """INSERT INTO multi_sport_queue (guest_id, mode, sports, display_name, preference, avoid_guest_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (guest_id) DO UPDATE
                     SET mode = EXCLUDED.mode,
                         sports = EXCLUDED.sports,
                         display_name = EXCLUDED.display_name,
                         preference = EXCLUDED.preference,
                         avoid_guest_id = EXCLUDED.avoid_guest_id,
                         enqueued_at = now()""",
                (guest_id, queue_mode, Jsonb(sports), display_name, Jsonb(preferences), avoid_guest_id),
            )
    return jsonify({"status": "waiting", "guest_id": guest_id})


@app.route("/api/modes/<mode>/status", methods=["POST"])
def multi_sport_status(mode: str):
    ensure_runtime_schema()
    queue_mode = _multi_mode_from_slug(mode)
    if not queue_mode:
        return jsonify({"error": "unsupported mode"}), 404
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    sports = _normalize_multi_sports(data.get("sports")) or list(MULTI_QUEUE_SPORTS)
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        active = _load_active_multisport_game(conn, guest_id, queue_mode, sports)
        if active:
            sport, game_id = active
            conn.execute("DELETE FROM multi_sport_queue WHERE guest_id = %s", (guest_id,))
            return jsonify({
                "status": "matched",
                "sport": sport,
                "game_id": game_id,
                "redirect": _multi_redirect(sport, queue_mode, game_id),
            })
        waiting = conn.execute(
            "SELECT sports, display_name, preference, enqueued_at FROM multi_sport_queue WHERE guest_id = %s AND mode = %s",
            (guest_id, queue_mode),
        ).fetchone()
        if waiting and _bot_match_due(guest_id, queue_mode, waiting[3]):
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(4412000 + %s)", (0 if queue_mode == "dr" else 1,))
                locked = conn.execute(
                    """SELECT sports, display_name, preference, enqueued_at
                         FROM multi_sport_queue
                        WHERE guest_id = %s AND mode = %s
                        FOR UPDATE""",
                    (guest_id, queue_mode),
                ).fetchone()
                if locked and _bot_match_due(guest_id, queue_mode, locked[3]):
                    queued_sports = _normalize_multi_sports(locked[0])
                    overlap = [sport for sport in sports if sport in queued_sports]
                    if overlap:
                        sport = overlap[secrets.randbelow(len(overlap))]
                        preference = (locked[2] or {}).get(sport) if isinstance(locked[2], dict) else None
                        conn.execute("DELETE FROM multi_sport_queue WHERE guest_id = %s", (guest_id,))
                        conn.execute(
                            "DELETE FROM sport_online_queue WHERE mode=%s AND guest_id=%s",
                            (queue_mode, guest_id),
                        )
                        game_id, _blob, _state = _create_bot_sport_online_match(
                            conn,
                            sport,
                            queue_mode,
                            guest_id,
                            locked[1],
                            preference,
                        )
                        return jsonify({
                            "status": "matched",
                            "sport": sport,
                            "game_id": game_id,
                            "redirect": _multi_redirect(sport, queue_mode, game_id),
                        })
    return jsonify({"status": "waiting", "guest_id": guest_id} if waiting else {"status": "idle", "guest_id": guest_id})


@app.route("/api/modes/<mode>/cancel_queue", methods=["POST"])
def multi_sport_cancel(mode: str):
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    with db() as conn:
        conn.execute("DELETE FROM multi_sport_queue WHERE guest_id = %s", (guest_id,))
    return jsonify({"status": "idle"})


@app.route("/api/sports/<sport>/<mode>/queue", methods=["POST"])
def sport_online_queue(sport: str, mode: str):
    if not _is_cross_sport(sport) or mode not in {"dr", "po"}: return jsonify({"error": "unsupported mode"}), 404
    data = request.get_json(silent=True) or {}; guest = (data.get("guest_id") or "").strip()
    if not guest: return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        if not conn.execute("SELECT 1 FROM guests WHERE guest_id=%s", (guest,)).fetchone(): return jsonify({"error": "unknown guest_id"}), 404
        existing = _sport_online_status(conn, sport, mode, guest)
        if existing["status"] == "matched": return jsonify(existing)
        name, avoid, preference = _guest_label(conn, guest), (data.get("avoid_guest_id") or "").strip() or None, data.get("win_condition_preference")
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(4412000 + %s)", (0 if mode == "dr" else 1,))
            multi = conn.execute(
                """SELECT guest_id::text, display_name, preference FROM multi_sport_queue
                     WHERE mode=%s AND guest_id<>%s AND sports @> %s
                     ORDER BY enqueued_at LIMIT 1 FOR UPDATE SKIP LOCKED""",
                (mode, guest, Jsonb([sport])),
            ).fetchone()
            if multi:
                oid, oname, oprefs = multi
                conn.execute("DELETE FROM multi_sport_queue WHERE guest_id=%s", (oid,))
                gid, blob, state = _sport_online_create(conn, sport, mode, (oid, oname), (guest, name),
                    {oid: (oprefs or {}).get(sport), guest: preference})
                return jsonify({"status":"matched", "game":_sport_online_state(conn,gid,blob,state,guest)})
            opp = conn.execute("""SELECT guest_id::text, display_name, preference FROM sport_online_queue
                                  WHERE sport_id=%s AND mode=%s AND guest_id<>%s
                                    AND (avoid_guest_id IS NULL OR avoid_guest_id<>CAST(%s AS uuid))
                                    AND (CAST(%s AS uuid) IS NULL OR guest_id<>CAST(%s AS uuid))
                                  ORDER BY enqueued_at LIMIT 1 FOR UPDATE SKIP LOCKED""",
                               (sport, mode, guest, guest, avoid, avoid)).fetchone()
            if opp:
                oid, oname, opref = opp
                conn.execute("DELETE FROM sport_online_queue WHERE sport_id=%s AND mode=%s AND guest_id IN (%s, %s)", (sport, mode, guest, oid))
                gid, blob, state = _sport_online_create(conn, sport, mode, (oid, oname), (guest, name), {oid: opref, guest: preference})
                return jsonify({"status": "matched", "game": _sport_online_state(conn, gid, blob, state, guest)})
            conn.execute("""INSERT INTO sport_online_queue (sport_id, mode, guest_id, display_name, preference, avoid_guest_id)
                            VALUES (%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (sport_id,mode,guest_id) DO UPDATE SET display_name=EXCLUDED.display_name,
                              preference=EXCLUDED.preference, avoid_guest_id=EXCLUDED.avoid_guest_id, enqueued_at=now()""",
                         (sport, mode, guest, name, preference, avoid))
        return jsonify(_sport_online_status(conn, sport, mode, guest))


@app.route("/api/sports/<sport>/<mode>/status", methods=["POST"])
def sport_online_status(sport: str, mode: str):
    guest = ((request.get_json(silent=True) or {}).get("guest_id") or "").strip()
    with db() as conn: return jsonify(_sport_online_status(conn, sport, mode, guest))


@app.route("/api/sports/<sport>/<mode>/game", methods=["POST"])
def sport_online_game(sport: str, mode: str):
    data = request.get_json(silent=True) or {}; guest, gid = (data.get("guest_id") or "").strip(), data.get("game_id")
    with db() as conn:
        _reap_expired_sport_games(conn, sport, mode)
        blob, state = _sport_online_load(conn, sport, mode, gid)
        if not blob: return jsonify({"error": "unknown game_id"}), 404
        if guest not in {blob["p1_guest_id"], blob["p2_guest_id"]}: return jsonify({"error": "unauthorized"}), 403
        _sport_online_maybe_advance_bot(conn, gid, blob, state)
        _sport_online_save(conn, gid, blob)
        return jsonify(_sport_online_state(conn, gid, blob, state, guest))


@app.route("/api/sports/<sport>/<mode>/move", methods=["POST"])
def sport_online_move(sport: str, mode: str):
    data = request.get_json(silent=True) or {}; guest, gid = (data.get("guest_id") or "").strip(), data.get("game_id")
    with db() as conn:
        blob, state = _sport_online_load(conn, sport, mode, gid)
        if not blob: return jsonify({"error": "unknown game_id"}), 404
        if guest not in {blob["p1_guest_id"], blob["p2_guest_id"]}: return jsonify({"error": "unauthorized"}), 403
        if blob["finished"]: _save_sport_online_result(conn,gid,blob,state); _sport_online_save(conn, gid, blob); return jsonify(_sport_online_state(conn, gid, blob, state, guest))
        if guest != (blob["p1_guest_id"] if blob["turn_index"] == 0 else blob["p2_guest_id"]): return jsonify({"error": "not your turn", **_sport_online_state(conn,gid,blob,state,guest)}), 409
        elapsed = (now_utc() - datetime.fromisoformat(blob["turn_started_at"])).total_seconds()
        live_elapsed = max(0.0, elapsed - blob["countdown_seconds"])
        if not _move_submitted_in_time(data, live_elapsed, blob["turn_seconds"]):
            blob["finished"] = True
            blob["winner"] = blob["p2"] if blob["turn_index"] == 0 else blob["p1"]
            blob["last_move"] = {"outcome": "timeout"}
            _save_sport_online_result(conn,gid,blob,state)
            _sport_online_save(conn, gid, blob)
            return jsonify(_sport_online_state(conn, gid, blob, state, guest))
        player_id, raw = (data.get("player_id") or "").strip() or None, (data.get("raw") or "").strip()
        result = validate_and_apply_move(
            state,
            PgEngineConn(conn),
            raw_input=None if player_id else raw,
            player_id=player_id,
            track_strikes=True,
            sport=_engine_sport(sport),
        )
        payload = result_to_dict(result)
        if mode == "po" and result.outcome != MoveOutcome.VALID:
            if sport == "baseball":
                alternate = _apply_playoff_powerup_move(conn, state, blob, raw=raw if raw else None, player_id=player_id)
            else:
                alternate = _local_po_powerup_move(PgEngineConn(conn), blob, raw, player_id)
            payload = alternate or payload
        blob.update(serialize_state(state)); blob["last_move"] = payload
        if payload.get("outcome") == "valid":
            mover = "p1" if guest == blob["p1_guest_id"] else "p2"
            _sport_online_apply_valid_payload(conn, sport, mode, blob, state, payload, mover, record_usage=True)
        _save_sport_online_result(conn,gid,blob,state)
        _sport_online_save(conn, gid, blob)
        return jsonify(_sport_online_state(conn, gid, blob, state, guest))


@app.route("/api/sports/<sport>/po/powerup", methods=["POST"])
def sport_online_powerup(sport: str):
    data = request.get_json(silent=True) or {}; guest, gid, key = (data.get("guest_id") or "").strip(), data.get("game_id"), (data.get("powerup_key") or "").strip()
    with db() as conn:
        blob, state = _sport_online_load(conn, sport, "po", gid)
        if not blob: return jsonify({"error":"unknown game_id"}), 404
        side = "p1" if guest == blob["p1_guest_id"] else "p2" if guest == blob["p2_guest_id"] else None
        if not side: return jsonify({"error":"unauthorized"}), 403
        if blob["finished"] or blob["turn_index"] != (0 if side == "p1" else 1): return jsonify({"error":"not your turn"}), 409
        powers = PLAYOFF_POWERUPS if sport == "baseball" else LOCAL_PLAYOFF_CONFIG[sport]["powerups"]
        if not _playoff_powerups_unlocked(state):
            return jsonify({"error":"Powerups unlock after each player has played twice.", **_sport_online_state(conn,gid,blob,state,guest)}), 409
        if key not in powers or key in blob.get(f"{side}_powerup_used_keys", []) or blob.get("turn_powerup_used"): return jsonify({"error":"powerup unavailable"}), 409
        blob[f"{side}_powerup_used_keys"].append(key); blob["turn_powerup_used"] = True
        meta = powers[key]
        if meta["kind"] in {"time", "timer"} and key != "quick_pitch":
            blob["turn_seconds"] += meta["bonus_seconds"]
        elif meta["kind"] == "pressure" or key == "quick_pitch":
            blob["next_turn_seconds_override"] = 10
        else: blob["turn_seconds"] += meta["bonus_seconds"]; blob["active_turn_powerup"] = key
        blob["last_move"] = {"outcome":"powerup_activated", "powerup_key":key, "powerup_label":meta["label"], "message":f"{meta['label']} activated."}
        _sport_online_save(conn,gid,blob); return jsonify(_sport_online_state(conn,gid,blob,state,guest))


@app.route("/api/sports/<sport>/<mode>/timeout", methods=["POST"])
def sport_online_timeout(sport: str, mode: str):
    data=request.get_json(silent=True) or {}; guest,gid=(data.get("guest_id") or "").strip(),data.get("game_id")
    with db() as conn:
        _reap_expired_sport_games(conn, sport, mode)
        blob,state=_sport_online_load(conn,sport,mode,gid)
        if not blob: return jsonify({"error":"unknown game_id"}),404
        if guest not in {blob["p1_guest_id"],blob["p2_guest_id"]}: return jsonify({"error":"unauthorized"}),403
        _sport_online_expire(blob); _save_sport_online_result(conn,gid,blob,state); _sport_online_save(conn,gid,blob)
        return jsonify(_sport_online_state(conn,gid,blob,state,guest))


@app.route("/api/sports/<sport>/<mode>/leave_game", methods=["POST"])
def sport_online_leave(sport: str, mode: str):
    data=request.get_json(silent=True) or {}; guest,gid=(data.get("guest_id") or "").strip(),data.get("game_id")
    with db() as conn:
        blob,state=_sport_online_load(conn,sport,mode,gid)
        if not blob: return jsonify({"status":"gone"})
        if guest not in {blob["p1_guest_id"],blob["p2_guest_id"]}: return jsonify({"error":"unauthorized"}),403
        if not blob["finished"]:
            blob["finished"]=True; blob["winner"]=blob["p2"] if guest==blob["p1_guest_id"] else blob["p1"]; blob["last_move"]={"outcome":"forfeit"}; _save_sport_online_result(conn,gid,blob,state); _sport_online_save(conn,gid,blob)
        _delete_transient_bot_guests(conn, blob, gid)
        return jsonify({"status":"gone"})


@app.route("/api/sports/<sport>/<mode>/cancel_queue", methods=["POST"])
@app.route("/api/sports/<sport>/<mode>/cancel_challenge", methods=["POST"])
def sport_online_cancel(sport: str, mode: str):
    guest=((request.get_json(silent=True) or {}).get("guest_id") or "").strip()
    with db() as conn:
        conn.execute("DELETE FROM sport_online_queue WHERE sport_id=%s AND mode=%s AND guest_id=%s",(sport,mode,guest))
        if request.path.endswith("cancel_challenge"):
            conn.execute(
                """DELETE FROM sport_online_invites
                    WHERE sport_id=%s AND mode=%s AND host_guest_id=%s AND claimed_at IS NULL""",
                (sport, mode, guest),
            )
    return jsonify({"status":"idle"})


@app.route("/api/sports/<sport>/<mode>/create_challenge", methods=["POST"])
def sport_online_create_challenge(sport: str, mode: str):
    if not _is_cross_sport(sport) or mode not in {"dr", "po"}:
        return jsonify({"error": "unsupported mode"}), 404
    data = request.get_json(silent=True) or {}
    guest = (data.get("guest_id") or "").strip()
    preference = data.get("win_condition_preference") if mode == "po" else None
    if not guest:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        if not conn.execute("SELECT 1 FROM guests WHERE guest_id=%s", (guest,)).fetchone():
            return jsonify({"error": "unknown guest_id"}), 404
        conn.execute("DELETE FROM sport_online_queue WHERE sport_id=%s AND mode=%s AND guest_id=%s", (sport, mode, guest))
        conn.execute(
            """DELETE FROM sport_online_invites
                WHERE sport_id=%s AND mode=%s AND host_guest_id=%s AND claimed_at IS NULL""",
            (sport, mode, guest),
        )
        code = secrets.token_hex(3).upper()
        conn.execute(
            """INSERT INTO sport_online_invites (code, sport_id, mode, host_guest_id, host_name, preference)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (code, sport, mode, guest, _guest_label(conn, guest), preference),
        )
    return jsonify({"status": "waiting", "code": code})


@app.route("/api/sports/<sport>/<mode>/join_challenge", methods=["POST"])
def sport_online_join_challenge(sport: str, mode: str):
    if not _is_cross_sport(sport) or mode not in {"dr", "po"}:
        return jsonify({"error": "unsupported mode"}), 404
    data = request.get_json(silent=True) or {}
    guest = (data.get("guest_id") or "").strip()
    code = (data.get("code") or "").strip().upper()
    preference = data.get("win_condition_preference") if mode == "po" else None
    if not guest or not code:
        return jsonify({"error": "guest_id and code required"}), 400
    with db() as conn:
        if not conn.execute("SELECT 1 FROM guests WHERE guest_id=%s", (guest,)).fetchone():
            return jsonify({"error": "unknown guest_id"}), 404
        name = _guest_label(conn, guest)
        with conn.transaction():
            row = conn.execute(
                """SELECT host_guest_id::text, host_name, preference
                     FROM sport_online_invites
                    WHERE code=%s AND sport_id=%s AND mode=%s
                      AND claimed_at IS NULL AND expires_at > now()
                    FOR UPDATE""",
                (code, sport, mode),
            ).fetchone()
            if not row:
                return jsonify({"error": "challenge code not found"}), 404
            host_guest_id, host_name, host_preference = row
            if host_guest_id == guest:
                return jsonify({"error": "cannot join your own challenge"}), 400
            conn.execute(
                "DELETE FROM sport_online_queue WHERE sport_id=%s AND mode=%s AND guest_id IN (%s, %s)",
                (sport, mode, guest, host_guest_id),
            )
            game_id, blob, state = _sport_online_create(
                conn,
                sport,
                mode,
                (host_guest_id, host_name),
                (guest, name),
                {host_guest_id: host_preference, guest: preference},
            )
            conn.execute("UPDATE sport_online_invites SET claimed_at=now() WHERE code=%s", (code,))
            return jsonify({"status": "matched", "game": _sport_online_state(conn, game_id, blob, state, guest)})


@app.route("/api/sports/<sport>/<mode>/rematch_request", methods=["POST"])
@app.route("/api/sports/<sport>/<mode>/rematch_status", methods=["POST"])
def sport_online_rematch(sport: str, mode: str):
    data=request.get_json(silent=True) or {}; guest,gid=(data.get("guest_id") or "").strip(),data.get("game_id")
    with db() as conn:
        blob,state=_sport_online_load(conn,sport,mode,gid)
        if not blob: return jsonify({"error":"unknown game_id"}),404
        if guest not in {blob["p1_guest_id"],blob["p2_guest_id"]}: return jsonify({"error":"unauthorized"}),403
        if blob.get("finished"):
            before_expires = blob.get("bot_rematch_expires_at")
            _stamp_sport_online_finished(blob, gid)
            if blob.get("bot_rematch_expires_at") != before_expires:
                _sport_online_save(conn, gid, blob)
        link=conn.execute("SELECT new_game_id::text FROM sport_online_rematch_links WHERE original_game_id=%s",(gid,)).fetchone()
        if link:
            new_blob,new_state=_sport_online_load(conn,sport,mode,link[0]); return jsonify({"status":"matched","game":_sport_online_state(conn,link[0],new_blob,new_state,guest)})
        other = blob["p2_guest_id"] if guest == blob["p1_guest_id"] else blob["p1_guest_id"]
        bot_rematch = _is_bot_guest(blob, other)
        requesters = {row[0] for row in conn.execute(
            "SELECT requester_guest_id::text FROM sport_online_rematches WHERE original_game_id=%s", (gid,)
        ).fetchall()}
        exited = {row[0] for row in conn.execute(
            "SELECT guest_id::text FROM sport_online_postgame_exits WHERE original_game_id=%s", (gid,)
        ).fetchall()}
        if request.path.endswith("rematch_status"):
            if bot_rematch:
                if not _bot_rematch_window_open(blob):
                    _delete_transient_bot_guests(conn, blob, gid)
                    return jsonify({"status":"abandoned","you_requested":guest in requesters,"opponent_requested":False,"opponent_present":False,"rematch_available":False})
                return jsonify({"status":"waiting","you_requested":guest in requesters,"opponent_requested":True,"opponent_present":True,"rematch_available":True})
            if other in exited:
                if guest in requesters:
                    _sport_online_requeue(
                        conn,
                        sport,
                        mode,
                        guest,
                        other,
                        _sport_online_rematch_preferences(blob).get(guest) if mode == "po" else None,
                    )
                    return jsonify({"status":"requeued","you_requested":True,"opponent_requested":False,"opponent_present":False,"rematch_available":False})
                return jsonify({"status":"abandoned","you_requested":False,"opponent_requested":False,"opponent_present":False,"rematch_available":False})
            return jsonify({"status":"waiting","you_requested":guest in requesters,"opponent_requested":other in requesters,"opponent_present":True,"rematch_available":True})
        if not blob["finished"] or (blob.get("last_move") or {}).get("outcome")=="forfeit": return jsonify({"error":"rematch unavailable"}),400
        if bot_rematch:
            if not _bot_rematch_window_open(blob):
                _delete_transient_bot_guests(conn, blob, gid)
                return jsonify({"error":"rematch unavailable"}),400
            human_name = blob["p1"] if guest == blob["p1_guest_id"] else blob["p2"]
            bot_name = blob["p1"] if other == blob["p1_guest_id"] else blob["p2"]
            preferences = _sport_online_rematch_preferences(blob)
            preference = preferences.get(guest) if mode == "po" else None
            new_gid, new_blob, new_state = _create_bot_sport_online_match(
                conn,
                sport,
                mode,
                guest,
                human_name,
                preference,
                bot=(other, bot_name),
                first_guest_id=_sport_online_rematch_first_guest_id(blob),
            )
            conn.execute("INSERT INTO sport_online_rematch_links (original_game_id,new_game_id) VALUES (%s,%s)",(gid,new_gid))
            return jsonify({"status":"matched","game":_sport_online_state(conn,new_gid,new_blob,new_state,guest)})
        if other in exited:
            _sport_online_requeue(
                conn,
                sport,
                mode,
                guest,
                other,
                _sport_online_rematch_preferences(blob).get(guest) if mode == "po" else None,
            )
            return jsonify({"status":"requeued","you_requested":True,"opponent_requested":False,"opponent_present":False,"rematch_available":False})
        conn.execute("INSERT INTO sport_online_rematches (original_game_id,requester_guest_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",(gid,guest))
        asked={r[0] for r in conn.execute("SELECT requester_guest_id::text FROM sport_online_rematches WHERE original_game_id=%s",(gid,)).fetchall()}
        if {blob["p1_guest_id"],blob["p2_guest_id"]} <= asked:
            new_gid,new_blob,new_state=_sport_online_create(
                conn,
                sport,
                mode,
                (blob["p1_guest_id"],blob["p1"]),
                (blob["p2_guest_id"],blob["p2"]),
                _sport_online_rematch_preferences(blob),
                first_guest_id=_sport_online_rematch_first_guest_id(blob),
            )
            conn.execute("INSERT INTO sport_online_rematch_links (original_game_id,new_game_id) VALUES (%s,%s)",(gid,new_gid))
            return jsonify({"status":"matched","game":_sport_online_state(conn,new_gid,new_blob,new_state,guest)})
        return jsonify({"status":"waiting"})


@app.route("/api/sports/<sport>/<mode>/postgame_leave", methods=["POST"])
def sport_online_postgame_leave(sport: str, mode: str):
    data=request.get_json(silent=True) or {}; guest,gid=(data.get("guest_id") or "").strip(),data.get("game_id")
    with db() as conn:
        conn.execute("INSERT INTO sport_online_postgame_exits (original_game_id,guest_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",(gid,guest))
        blob, _ = _sport_online_load(conn, sport, mode, gid)
        if blob:
            other = blob["p2_guest_id"] if guest == blob["p1_guest_id"] else blob["p1_guest_id"]
            if _is_bot_guest(blob, other):
                _delete_transient_bot_guests(conn, blob, gid)
            else:
                asked = conn.execute("SELECT 1 FROM sport_online_rematches WHERE original_game_id=%s AND requester_guest_id=%s", (gid, other)).fetchone()
                if asked:
                    _sport_online_requeue(
                        conn,
                        sport,
                        mode,
                        other,
                        guest,
                        _sport_online_rematch_preferences(blob).get(other) if mode == "po" else None,
                    )
    return jsonify({"status":"gone"})


if __name__ == "__main__":
    ensure_runtime_schema()
    ensure_static_caches()
    app.run(host="127.0.0.1", port=5000, debug=True)
