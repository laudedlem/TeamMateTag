"""
Flask server for Teammate Tag (codename base2nerdle).

Backed by Supabase Postgres. Per-request connections; Supabase's transaction
pooler handles pooling. Game state lives in Postgres as single JSONB blobs
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
import secrets
import sqlite3
import sys
import uuid
import hashlib
import hmac
from urllib.parse import urljoin
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

import psycopg
import requests
from psycopg.types.json import Jsonb

# Load .env first so DATABASE_URL is available before module-level code.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, jsonify, make_response, render_template, request

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
sys.path.insert(0, str(ROOT / "scripts"))
from name_normalize import normalize  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL")

DEFAULT_SEED = "rizzoan01"
LOCAL_SPORTS_ENABLED = os.environ.get("TEAMMATETAG_LOCAL_SPORTS") == "1"
LOCAL_SPORT_DATA = ROOT / "db" / "teammatetag_local.sqlite"
LOCAL_SPORT_SEEDS = {
    "football": "nfl:00-0024272",  # Devin Hester
    "basketball": "nba:201565",     # Derrick Rose
    "hockey": "nhl:8474141",        # Patrick Kane
}
LOCAL_SPORT_MODE_NAMES = {
    "football": "Gridiron Reps",
    "basketball": "Shooting Practice",
    "hockey": "Skating Sets",
}
LOCAL_BP_GAMES: dict[str, dict] = {}
LOCAL_BP_LOCK = Lock()
HEADSHOT_URL = "https://midfield.mlbstatic.com/v1/people/{}/spots/120"
OPENING_COUNTDOWN_SECONDS = 3.0
APP_TURN_SECONDS = 20.0
SUPPORT_EMAIL = "support@teammatetag.com"
SESSION_COOKIE = "tt_session"
DEFAULT_PLAYOFF_TURN_SECONDS = 20.0
QUICK_PITCH_TURN_SECONDS = 10.0

PLAYOFF_POWERUPS = {
    "bubblegum": {
        "label": "Bubblegum",
        "description": "Any batter from the same franchise with a 40+ home run season. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "batter",
    },
    "pine_tar": {
        "label": "Pine Tar",
        "description": "Any pitcher from the same franchise with a 200+ strikeout season. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "pitcher",
    },
    "bat_donut": {
        "label": "Bat Donut",
        "description": "Any player from the same franchise with a Silver Slugger. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "any",
    },
    "sunglasses": {
        "label": "Sunglasses",
        "description": "Any player from the same franchise with an All-Star selection. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "any",
    },
    "backup_mitt": {
        "label": "Backup Mitt",
        "description": "Any player from the same franchise with a Gold Glove. +5 seconds.",
        "kind": "skill",
        "bonus_seconds": 5.0,
        "role": "any",
    },
    "abs": {
        "label": "ABS",
        "description": "Add 15 seconds to your turn.",
        "kind": "timer",
        "bonus_seconds": 15.0,
        "role": "any",
    },
    "quick_pitch": {
        "label": "Quick Pitch",
        "description": "Your opponent gets 10 seconds on their next turn.",
        "kind": "timer",
        "bonus_seconds": 0.0,
        "role": "any",
    },
}

PLAYOFF_WIN_CONDITIONS = {
    "sunset_kingdom": {
        "label": "Sunset Kingdom",
        "description": "Name 5 Japanese players.",
        "target": 5,
        "mode": "count",
    },
    "havana_heat": {
        "label": "Havana Heat",
        "description": "Name 5 Cuban players.",
        "target": 5,
        "mode": "count",
    },
    "maple_corridor": {
        "label": "Maple Corridor",
        "description": "Name 5 Canadian players.",
        "target": 5,
        "mode": "count",
    },
    "mvp_circle": {
        "label": "MVP Circle",
        "description": "Name 3 MVP winners.",
        "target": 3,
        "mode": "count",
    },
    "young_buck": {
        "label": "Young Buck",
        "description": "Name 3 Rookie of the Year winners.",
        "target": 3,
        "mode": "count",
    },
    "gonna_be_golden": {
        "label": "Gonna Be Golden",
        "description": "Name 3 Gold Glove winners.",
        "target": 3,
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
        "description": "Name players with a combined 30 World Series rings.",
        "target": 30,
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


# ============================================================
# Database access
# ============================================================

@contextmanager
def db():
    """Open a Postgres connection for a single request. Supabase's
    transaction-mode pgbouncer manages pooling on the server side, so
    we open and close per request without leaking.

    Supabase's underlying Postgres sets `default_transaction_read_only=on`
    at the config-file level (visible in pg_settings). We override at the
    session level immediately after connecting so this server can write."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required. Copy .env.example to .env and set the "
            "Supabase connection URI, or export it in the environment."
        )
    # prepare_threshold=None disables psycopg3's auto-prepared-statement
    # cache. pgbouncer in transaction mode doesn't preserve session state
    # across transactions, so cached prepared-statement names collide
    # ("prepared statement _pg3_0 already exists").
    conn = psycopg.connect(
        DATABASE_URL, autocommit=True, prepare_threshold=None,
    )
    conn.execute("SET default_transaction_read_only = off")
    try:
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
STATIC_CACHE_LOCK = Lock()
STATIC_CACHE_READY = False
RUNTIME_SCHEMA_LOCK = Lock()
RUNTIME_SCHEMA_READY = False


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
                       finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fr_results_owner_guest "
                "ON fr_results(owner_guest_id, finished_at DESC)"
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
            WHERE owner_guest_id = %s
            GROUP BY team_name, season
            ORDER BY n DESC, season DESC, team_name ASC
            LIMIT 3""",
        (guest_id,),
    ).fetchall()
    return {
        "bp_plays": bp_plays,
        "bp_best": bp_best,
        "fr_plays": fr_plays,
        "fr_wins": fr_wins,
        "dr_plays": dr_plays,
        "dr_wins": dr_wins,
        "dr_losses": max(0, dr_plays - dr_wins),
        "dr_elo": elo,
        "top_struck_teams": [
            {"team_name": team_name, "season": season, "count": count}
            for team_name, season, count in top_struck
        ],
    }


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


def _normalized_playoff_preference(value: str | None) -> str:
    value = (value or "random").strip()
    return value if value in PLAYOFF_WIN_CONDITIONS else "random"


def _playoff_condition_for_guest(conn, guest_id: str) -> str:
    row = conn.execute(
        "SELECT playoff_win_condition_preference FROM guests WHERE guest_id = %s",
        (guest_id,),
    ).fetchone()
    preference = _normalized_playoff_preference(row[0] if row else None)
    return _random_playoff_win_condition() if preference == "random" else preference


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
              AND q.franchise_id IN (
                    SELECT DISTINCT tm.franchise_id
                      FROM appearances a
                      JOIN teams tm
                        ON tm.team_id = a.team_id
                       AND tm.season = a.season
                     WHERE a.player_id = %s
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
            (guest_id, blob.get("seed_player_id", DEFAULT_SEED), max(0, len(state.chain) - 1)),
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
        appearance_rows = conn.execute(
            """SELECT a.player_id, a.season, t.name
                 FROM appearances a
                 JOIN teams t ON t.team_id = a.team_id AND t.season = a.season
                WHERE a.player_id = ANY(%s)
                ORDER BY a.player_id, a.season, t.team_id""",
            (missing,),
        ).fetchall()
        appearances_by_player: dict[str, list[tuple[int, str]]] = {pid: [] for pid in missing}
        for pid, season, team_name in appearance_rows:
            appearances_by_player.setdefault(pid, []).append((season, team_name))

        for pid in missing:
            mlbam_id, debut_year, final_year, first, last = player_map.get(
                pid, (None, None, None, None, None)
            )
            spans: list[list] = []
            for season, team_name in appearances_by_player.get(pid, []):
                if spans and spans[-1][0] == team_name and spans[-1][2] == season - 1:
                    spans[-1][2] = season
                else:
                    spans.append([team_name, season, season])
            teams_list = [
                f"{name} {start}" if start == end else f"{name} {start}-{end}"
                for name, start, end in spans
            ]
            card = {
                "mlbam_id": mlbam_id,
                "headshot_url": HEADSHOT_URL.format(mlbam_id) if mlbam_id else None,
                "debut_year": debut_year,
                "final_year": final_year,
                "teams": teams_list,
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

@app.route("/")
def index():
    return render_template("index.html", sport=None, sport_ready=False)


SPORT_HUBS = {
    "baseball": {"name": "Baseball", "league": "MLB", "ready": True},
    "basketball": {"name": "Basketball", "league": "NBA", "ready": LOCAL_SPORTS_ENABLED},
    "hockey": {"name": "Hockey", "league": "NHL", "ready": LOCAL_SPORTS_ENABLED},
    "football": {"name": "Football", "league": "NFL", "ready": LOCAL_SPORTS_ENABLED},
}


@app.route("/baseball")
@app.route("/basketball")
@app.route("/hockey")
@app.route("/football")
def sport_hub():
    sport_key = request.path.strip("/")
    sport = SPORT_HUBS[sport_key]
    return render_template(
        "index.html",
        sport={"key": sport_key, **sport},
        sport_ready=sport["ready"],
    )


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", support_email=SUPPORT_EMAIL)


@app.route("/terms")
def terms():
    return render_template("terms.html", support_email=SUPPORT_EMAIL)


@app.route("/contact")
def contact():
    return render_template("contact.html", support_email=SUPPORT_EMAIL)


@app.route("/reset-password")
def reset_password_page():
    return render_template(
        "reset_password.html",
        support_email=SUPPORT_EMAIL,
        supabase_url=SUPABASE_URL or "",
        supabase_anon_key=SUPABASE_ANON_KEY or "",
    )


@app.route("/api/profile/bootstrap", methods=["POST"])
def profile_bootstrap():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    requested_guest_id = (data.get("guest_id") or "").strip() or None
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
            """SELECT t.name, a.season FROM sport_appearances a
                 JOIN sport_teams t ON t.sport_id = a.sport_id AND t.team_id = a.team_id AND t.season = a.season
                WHERE a.sport_id = ? AND a.player_id = ? ORDER BY a.season, t.name""",
            (sport, player_id),
        ).fetchall()
        spans = []
        for team, season in appearances:
            if spans and spans[-1][0] == team and spans[-1][2] == season - 1:
                spans[-1][2] = season
            else:
                spans.append([team, season, season])
        teams = [f"{team} {start}" if start == end else f"{team} {start}-{end}" for team, start, end in spans]
        external_id = row[0] if row else None
        if sport == "basketball" and external_id:
            headshot = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{external_id}.png"
        elif sport == "hockey" and external_id:
            headshot = f"https://assets.nhle.com/mugs/nhl/latest/{external_id}.png"
        elif sport == "football" and external_id:
            headshot = f"https://a.espncdn.com/i/headshots/nfl/players/full/{external_id}.png"
        else:
            headshot = None
        out[player_id] = {
            "mlbam_id": None, "headshot_url": headshot,
            "debut_year": row[1] if row else None, "final_year": row[2] if row else None,
            "name_first": row[3] if row else None, "name_last": row[4] if row else None,
            "primary_pos": row[5] if row else None, "teams": teams,
        }
    return out


def _local_bp_state(game_id: str, game: dict) -> dict:
    state, sport = game["state"], game["sport"]
    with _local_sport_conn() as conn:
        cards = _local_sport_cards(conn, sport, state.chain)
        team_names = {
            (team, season): name for team, season, name in conn.execute(
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
        chain.append({"id": player_id, "name": name, **card, "shared_with_prev": [
            {"team_id": team, "season": season, "team_name": team_names.get((team, season), team)}
            for team, season in state.chain_shared_with_prev[index]
        ]})
    last_move = dict(game["last_move"] or {})
    for field in ("shared_seasons", "burned_seasons"):
        for item in last_move.get(field, []):
            item["team_name"] = team_names.get((item["team_id"], item["season"]), item["team_id"])
    return {"game_id": game_id, "mode": "bp", "sport": sport, "mode_name": LOCAL_SPORT_MODE_NAMES[sport],
            "current_player": {"id": state.current_player_id, "name": state.current_player_name},
            "chain": chain, "strikes": [{"team_id": team, "season": season, "count": count,
            "team_name": team_names.get((team, season), team)} for (team, season), count in state.strikes.items()],
            "chain_length": len(state.chain), "longest_chain": len(state.chain), "turn_seconds": APP_TURN_SECONDS,
            "countdown_seconds_remaining": countdown, "remaining_seconds": remaining,
            "finished": game["finished"], "last_move": last_move}


@app.route("/api/local/<sport>/autocomplete")
def local_sport_autocomplete(sport: str):
    q = (request.args.get("q") or "").strip()
    if sport not in LOCAL_SPORT_SEEDS or not q:
        return jsonify([])
    normalized = "".join(char for char in normalize(q) if char.isalnum())
    with _local_sport_conn() as conn:
        rows = conn.execute(
            """SELECT p.player_id, p.display_name, sp.debut_year, sp.final_year, p.career_games
                 FROM sport_players_searchable p JOIN sport_players sp ON sp.sport_id = p.sport_id AND sp.player_id = p.player_id
                WHERE p.sport_id = ? AND (p.search_key LIKE ? OR p.last_key LIKE ?)
                ORDER BY p.career_games DESC LIMIT 4""",
            (sport, normalized + "%", normalized + "%"),
        ).fetchall()
    return jsonify([{"player_id": pid, "display_name": name, "debut_year": debut, "final_year": final,
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
                   WHERE ps.search_key LIKE %s || '%%'
                  UNION
                  SELECT ps.player_id, ps.display_name, p.debut_year, p.final_year, ps.career_games
                    FROM players_searchable ps
                    JOIN players p ON p.player_id = ps.player_id
                   WHERE ps.last_key LIKE %s || '%%'
                  UNION
                  SELECT ps.player_id, ps.display_name, p.debut_year, p.final_year, ps.career_games
                    FROM players_searchable ps
                    JOIN players p ON p.player_id = ps.player_id
                    JOIN nickname_search ns ON ns.player_id = ps.player_id
                   WHERE ns.nickname_key LIKE %s || '%%'
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
                       p2_win_condition_key: str | None = None) -> dict:
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
        "p1_win_condition_key": p1_win_condition_key or _random_playoff_win_condition(),
        "p2_win_condition_key": p2_win_condition_key or _random_playoff_win_condition(),
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
        if live_elapsed > blob["turn_seconds"]:
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


def _po_create_online_game(conn, guest_a_id: str, name_a: str, guest_b_id: str, name_b: str):
    first_a = bool(secrets.randbelow(2) == 0)
    p1_guest_id, p1_name, p2_guest_id, p2_name = (
        (guest_a_id, name_a, guest_b_id, name_b) if first_a
        else (guest_b_id, name_b, guest_a_id, name_a)
    )
    engine_conn = PgEngineConn(conn)
    state = seed_game(engine_conn, DEFAULT_SEED)
    _record_player_usage(conn, DEFAULT_SEED, "dr")
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
        p1_win_condition_key=_playoff_condition_for_guest(conn, p1_guest_id),
        p2_win_condition_key=_playoff_condition_for_guest(conn, p2_guest_id),
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
                "message": f"{powerup['label']} activated. +5 seconds and expanded move rules this turn.",
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
        if live_elapsed > blob["turn_seconds"]:
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
            win_update = _apply_playoff_win_condition_hit(conn, blob, move_payload["player_id"], mover_side)
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
                conn, blob.get("p1_guest_id"), blob.get("p1"),
                blob.get("p2_guest_id"), blob.get("p2"),
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
        if live_elapsed > blob["turn_seconds"]:
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


# ============================================================
# Film Review (daily puzzle)
# ============================================================

FR_PUZZLES: list[dict] = [
    {
        "id": "fr_001",
        "title": "Full Lineup",
        "deck": [
            "pujolal01", "hunteto01", "cabremi01", "pierrju01",
            "rolliji01", "gonzaad01", "ortizda01", "pierzaj01", "beltrad01",
        ],
    },
]

FR_MAX_STRIKES = 3


def _fr_compute_shared(conn, deck: list[str]) -> list[list[tuple[str, int, str]]]:
    engine_conn = PgEngineConn(conn)
    out = []
    for i in range(len(deck) - 1):
        shared = get_shared_seasons(engine_conn, deck[i], deck[i + 1])
        out.append([(t, s, fr_display_team_name(t, s)) for t, s in shared])
    return out


def fr_today_puzzle() -> dict:
    import time as _time
    idx = int(_time.time() // 86400) % len(FR_PUZZLES)
    return FR_PUZZLES[idx]


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
    }


def fr_state_dict(gid: str, blob: dict, conn=None) -> dict:
    deck = blob["deck"]
    pair_index = blob["pair_index"]
    if conn:
        cards = _hydrate_player_cards(conn, list(deck))
    else:
        with db() as _conn:
            cards = _hydrate_player_cards(_conn, list(deck))
    return {
        "game_id": gid,
        "mode": "fr",
        "puzzle_id": blob["puzzle_id"],
        "total_cards": len(deck),
        "revealed_count": blob["revealed_count"],
        "revealed_cards": [
            fr_card_dict_from_card(pid, cards.get(pid) or player_card(pid))
            for pid in deck[:blob["revealed_count"]]
        ],
        "pair_index": pair_index,
        "pair_names": [
            (fr_card_dict_from_card(
                deck[pair_index], cards.get(deck[pair_index]) or player_card(deck[pair_index])
            )["name"]
             if pair_index < len(deck) else None),
            (fr_card_dict_from_card(
                deck[pair_index + 1],
                cards.get(deck[pair_index + 1]) or player_card(deck[pair_index + 1]),
            )["name"]
             if pair_index + 1 < len(deck) else None),
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
    }


def fr_blob_from_puzzle(
    puzzle: dict,
    shared_per_pair: list[list[tuple[str, int, str]]],
    owner_guest_id: str | None = None,
) -> dict:
    return {
        "puzzle_id": puzzle["id"],
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
    puz = fr_today_puzzle()
    deck = list(puz["deck"])
    guest_id = (data.get("guest_id") or "").strip() or None
    with db() as conn:
        if guest_id:
            row = conn.execute(
                "SELECT 1 FROM guests WHERE guest_id = %s",
                (guest_id,),
            ).fetchone()
            if not row:
                guest_id = None
        shared_per_pair = _fr_compute_shared(conn, deck)
        bad = [i for i, lst in enumerate(shared_per_pair) if not lst]
        if bad:
            return jsonify({
                "error": f"puzzle {puz['id']!r} has unsolvable pair(s): {bad}",
            }), 500
        blob = fr_blob_from_puzzle(puz, shared_per_pair, owner_guest_id=guest_id)
        gid = _insert_game(conn, "fr_games", blob)
        return jsonify(fr_state_dict(gid, blob, conn=conn))


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
            t_id, season, team_name = matched[0]
            blob["solved_links"][blob["pair_index"]] = {
                "team_id": t_id, "season": season, "team_name": team_name,
            }
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
            (
                {"team_id": pair[0][0], "season": pair[0][1], "team_name": pair[0][2]}
                if pair else None
            )
            for pair in blob["shared_per_pair"]
        ],
        "answers": [
            [{"team_id": r[0], "season": r[1], "team_name": r[2]} for r in pair]
            for pair in blob["shared_per_pair"]
        ],
    })


if __name__ == "__main__":
    ensure_runtime_schema()
    ensure_static_caches()
    app.run(host="127.0.0.1", port=5000, debug=True)
