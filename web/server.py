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
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import psycopg
from psycopg.types.json import Jsonb

# Load .env first so DATABASE_URL is available before module-level code.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "game"))
from engine import (  # noqa: E402
    STRIKES_TO_BURN,
    TURN_SECONDS,
    GameState,
    MoveOutcome,
    MoveResult,
    get_shared_seasons,
    seed_game,
    validate_and_apply_move,
)
sys.path.insert(0, str(ROOT / "scripts"))
from name_normalize import normalize  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")

DEFAULT_SEED = "rizzoan01"
HEADSHOT_URL = "https://midfield.mlbstatic.com/v1/people/{}/spots/120"
OPENING_COUNTDOWN_SECONDS = 3.0

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
                "ALTER TABLE guests "
                "ADD COLUMN IF NOT EXISTS elo INTEGER NOT NULL DEFAULT 1200"
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
                       owner_guest_id UUID REFERENCES guests(guest_id) ON DELETE SET NULL,
                       opponent_guest_id UUID REFERENCES guests(guest_id) ON DELETE SET NULL,
                       opponent_name TEXT,
                       won BOOLEAN NOT NULL DEFAULT false,
                       elo_before INTEGER NOT NULL,
                       elo_after INTEGER NOT NULL,
                       finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dr_results_owner_guest "
                "ON dr_results(owner_guest_id, finished_at DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dr_queue (
                       guest_id UUID PRIMARY KEY REFERENCES guests(guest_id) ON DELETE CASCADE,
                       display_name TEXT NOT NULL,
                       enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dr_queue_enqueued "
                "ON dr_queue(enqueued_at)"
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
        RUNTIME_SCHEMA_READY = True


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
    return {
        "bp_plays": bp_plays,
        "bp_best": bp_best,
        "fr_plays": fr_plays,
        "fr_wins": fr_wins,
        "dr_plays": dr_plays,
        "dr_wins": dr_wins,
        "dr_losses": max(0, dr_plays - dr_wins),
        "dr_elo": elo,
    }


def _guest_profile(conn, guest_id: str) -> dict | None:
    row = conn.execute(
        """SELECT guest_id::text, display_name, created_at
             FROM guests
            WHERE guest_id = %s""",
        (guest_id,),
    ).fetchone()
    if not row:
        return None
    gid, display_name, created_at = row
    return {
        "guest_id": gid,
        "display_name": display_name or f"Guest {gid[:8]}",
        "created_at": created_at.isoformat(),
        "stats": _guest_stats(conn, gid),
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


def _save_dr_result(conn, blob: dict):
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
               owner_guest_id, opponent_guest_id, opponent_name, won, elo_before, elo_after
           ) VALUES (%s, %s, %s, %s, %s, %s)""",
        (p1_guest_id, p2_guest_id, blob.get("p2"), bool(p1_won), p1_before, p1_after),
    )
    conn.execute(
        """INSERT INTO dr_results (
               owner_guest_id, opponent_guest_id, opponent_name, won, elo_before, elo_after
           ) VALUES (%s, %s, %s, %s, %s, %s)""",
        (p2_guest_id, p1_guest_id, blob.get("p1"), bool(not p1_won), p2_before, p2_after),
    )
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
    return render_template("index.html")


@app.route("/api/profile/bootstrap", methods=["POST"])
def profile_bootstrap():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    requested_guest_id = (data.get("guest_id") or "").strip() or None
    with db() as conn:
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
        profile = _guest_profile(conn, guest_id)
    return jsonify(profile)


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


@app.route("/api/dr/queue", methods=["POST"])
def dr_queue():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    guest_id = (data.get("guest_id") or "").strip()
    if not guest_id:
        return jsonify({"error": "guest_id required"}), 400
    with db() as conn:
        guest = conn.execute(
            "SELECT display_name FROM guests WHERE guest_id = %s",
            (guest_id,),
        ).fetchone()
        if not guest:
            return jsonify({"error": "unknown guest_id"}), 404
        display_name = guest[0] or f"Guest {guest_id[:8]}"

        existing = _dr_status_payload(conn, guest_id)
        if existing["status"] in {"matched", "waiting"}:
            return jsonify(existing)

        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(4411001)")
            opp = conn.execute(
                """SELECT guest_id::text, display_name
                     FROM dr_queue
                    WHERE guest_id <> %s
                    ORDER BY enqueued_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED""",
                (guest_id,),
            ).fetchone()
            if opp:
                opp_guest_id, opp_name = opp
                conn.execute(
                    "DELETE FROM dr_queue WHERE guest_id IN (%s, %s)",
                    (guest_id, opp_guest_id),
                )
                engine_conn = PgEngineConn(conn)
                state = seed_game(engine_conn, DEFAULT_SEED)
                _record_player_usage(conn, DEFAULT_SEED, "dr")
                blob = dr_blob_from_state(
                    state,
                    p1=opp_name,
                    p2=display_name,
                    turn_index=0,
                    turn_seconds=TURN_SECONDS,
                    turn_started_at=now_utc(),
                    countdown_seconds=OPENING_COUNTDOWN_SECONDS,
                    owner_guest_id=opp_guest_id,
                    p1_guest_id=opp_guest_id,
                    p2_guest_id=guest_id,
                    seed_player_id=DEFAULT_SEED,
                )
                gid = _insert_game(conn, "dr_games", blob)
                blob["viewer_guest_id"] = guest_id
                return jsonify({
                    "status": "matched",
                    "game": dr_state_dict(gid, blob, state, conn=conn),
                })

            conn.execute(
                """INSERT INTO dr_queue (guest_id, display_name, enqueued_at)
                   VALUES (%s, %s, now())
                   ON CONFLICT (guest_id) DO UPDATE
                   SET display_name = EXCLUDED.display_name,
                       enqueued_at = now()""",
                (guest_id, display_name),
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


@app.route("/api/new_game", methods=["POST"])
def new_game():
    ensure_runtime_schema()
    data = request.get_json(silent=True) or {}
    p1 = (data.get("p1") or "Player 1").strip()
    p2 = (data.get("p2") or "Player 2").strip()
    seed = data.get("seed") or DEFAULT_SEED
    guest_id = (data.get("guest_id") or "").strip() or None
    turn_seconds = float(data.get("turn_seconds") or TURN_SECONDS)
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
            _save_dr_result(conn, blob)
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
        _save_dr_result(conn, blob)
        _save_game(conn, "dr_games", gid, blob)
        return jsonify(dr_state_dict(gid, blob, state, conn=conn))


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
    turn_seconds = float(data.get("turn_seconds") or TURN_SECONDS)
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
