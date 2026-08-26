#!/usr/bin/env python3
"""Build local NHL teammate proof from official game-level appearances.

Rule proven by this dataset:
  two players are teammates only if both appeared in at least one regular-season
  NHL game for the same team.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "raw" / "nhl_game_teammates" / "nhl_game_teammates.sqlite"
DEFAULT_CACHE = ROOT / "raw" / "nhl_game_teammates" / "cache"
LOCAL_SPORT_DB = ROOT / "db" / "teammatetag_local.sqlite"
API = "https://api-web.nhle.com/v1"
FINAL_STATES = {"OFF", "FINAL"}
SOURCE = "nhl_api_web_gamecenter"
REQUEST_SLEEP_SECONDS = 0.0
MAX_HTTP_RETRIES = 6

# Current and recent/historical official NHL abbreviations needed since 2000.
FALLBACK_TEAM_CODES = [
    "ANA", "ARI", "ATL", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL",
    "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI",
    "NYR", "OTT", "PHI", "PHX", "PIT", "SEA", "SJS", "STL", "TBL", "TOR",
    "UTA", "VAN", "VGK", "WPG", "WSH",
]


@dataclass(frozen=True)
class GameSummary:
    game_id: str
    season: int
    game_date: str
    away_team: str
    home_team: str


@dataclass(frozen=True)
class PlayerAppearance:
    game_id: str
    season: int
    game_date: str
    team_id: str
    opponent_team_id: str
    player_id: str
    external_id: str
    display_name: str
    position: str
    toi_seconds: int


def acquire_lock(lock_path: Path, disabled: bool = False) -> None:
    if disabled:
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(
            f"Another NHL game-level build appears to be running: {lock_path}\n"
            "Stop that process or delete the lock only after confirming no build is active."
        )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))

    def cleanup() -> None:
        try:
            if lock_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                lock_path.unlink()
        except OSError:
            pass

    atexit.register(cleanup)


def cache_json_path(cache_dir: Path, kind: str, key: str) -> Path:
    return cache_dir / kind / f"{key}.json"


def get_json(url: str, cache_path: Path) -> dict[str, Any]:
    global REQUEST_SLEEP_SECONDS
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    if REQUEST_SLEEP_SECONDS > 0:
        time.sleep(REQUEST_SLEEP_SECONDS)
    for attempt in range(MAX_HTTP_RETRIES + 1):
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            payload: dict[str, Any] = {"_missing": True, "_url": url}
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
            return payload
        if response.status_code == 429 and attempt < MAX_HTTP_RETRIES:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 10.0 * (attempt + 1)
            except ValueError:
                delay = 10.0 * (attempt + 1)
            delay = min(90.0, max(5.0, delay))
            print(f"RATE_LIMIT sleeping {delay:.1f}s for {url}", flush=True)
            time.sleep(delay)
            continue
        if 500 <= response.status_code < 600 and attempt < MAX_HTTP_RETRIES:
            delay = min(60.0, 5.0 * (attempt + 1))
            print(f"SERVER_RETRY {response.status_code} sleeping {delay:.1f}s for {url}", flush=True)
            time.sleep(delay)
            continue
        response.raise_for_status()
        payload = response.json()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_name(f"{cache_path.stem}.{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        tmp_path.replace(cache_path)
        return payload
    raise RuntimeError(f"request failed after retries: {url}")


def localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("default") or next(iter(value.values()), "") or "")
    return str(value or "")


def season_id(season: int) -> str:
    return f"{season}{season + 1}"


def season_start(raw_season: int | str) -> int:
    return int(str(raw_season)[:4])


def full_team_name(team: dict[str, Any]) -> str:
    place = localized(team.get("placeName"))
    common = localized(team.get("commonName"))
    return " ".join(part for part in (place, common) if part).strip() or str(team.get("abbrev") or "")


def toi_seconds(value: Any) -> int:
    if not isinstance(value, str) or ":" not in value:
        return 0
    minutes, seconds = value.split(":", 1)
    try:
        return int(minutes) * 60 + int(seconds)
    except ValueError:
        return 0


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        CREATE TABLE IF NOT EXISTS nhl_games (
          game_id TEXT PRIMARY KEY,
          season INTEGER NOT NULL,
          game_date TEXT NOT NULL,
          game_type INTEGER NOT NULL,
          game_state TEXT,
          away_team_id TEXT NOT NULL,
          home_team_id TEXT NOT NULL,
          away_team_name TEXT,
          home_team_name TEXT,
          source TEXT NOT NULL,
          fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nhl_player_game_appearances (
          game_id TEXT NOT NULL,
          season INTEGER NOT NULL,
          game_date TEXT NOT NULL,
          team_id TEXT NOT NULL,
          opponent_team_id TEXT NOT NULL,
          player_id TEXT NOT NULL,
          external_id TEXT NOT NULL,
          display_name TEXT NOT NULL,
          position TEXT,
          toi_seconds INTEGER NOT NULL DEFAULT 0,
          source TEXT NOT NULL,
          PRIMARY KEY (game_id, team_id, player_id)
        );

        CREATE INDEX IF NOT EXISTS idx_nhl_pga_player
          ON nhl_player_game_appearances(player_id, season, team_id);
        CREATE INDEX IF NOT EXISTS idx_nhl_pga_team_game
          ON nhl_player_game_appearances(team_id, game_id);
        CREATE INDEX IF NOT EXISTS idx_nhl_pga_season_team
          ON nhl_player_game_appearances(season, team_id);

        CREATE TABLE IF NOT EXISTS nhl_teammate_game_proofs (
          player_a_id TEXT NOT NULL,
          player_b_id TEXT NOT NULL,
          team_id TEXT NOT NULL,
          season INTEGER NOT NULL,
          shared_games INTEGER NOT NULL,
          first_game_id TEXT NOT NULL,
          first_game_date TEXT NOT NULL,
          PRIMARY KEY (player_a_id, player_b_id, team_id, season),
          CHECK (player_a_id < player_b_id)
        );

        CREATE INDEX IF NOT EXISTS idx_nhl_tgp_a_b
          ON nhl_teammate_game_proofs(player_a_id, player_b_id);
        CREATE INDEX IF NOT EXISTS idx_nhl_tgp_b_a
          ON nhl_teammate_game_proofs(player_b_id, player_a_id);
        CREATE INDEX IF NOT EXISTS idx_nhl_tgp_team_season
          ON nhl_teammate_game_proofs(team_id, season);
        """
    )
    conn.commit()


def local_team_codes(season: int) -> list[str]:
    if not LOCAL_SPORT_DB.exists():
        return FALLBACK_TEAM_CODES
    conn = sqlite3.connect(LOCAL_SPORT_DB)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT team_id
              FROM sport_teams
             WHERE sport_id = 'hockey'
               AND season = ?
               AND length(team_id) = 3
               AND team_id NOT LIKE 'hdb:%'
             ORDER BY team_id
            """,
            (season,),
        ).fetchall()
        codes = [row[0] for row in rows if row[0].isupper()]
        return codes or FALLBACK_TEAM_CODES
    finally:
        conn.close()


def fetch_club_schedule(cache_dir: Path, team_id: str, season: int) -> list[GameSummary]:
    sid = season_id(season)
    payload = get_json(
        f"{API}/club-schedule-season/{team_id}/{sid}",
        cache_json_path(cache_dir, "schedule", f"{team_id}_{sid}"),
    )
    if payload.get("_missing"):
        return []
    games: list[GameSummary] = []
    for game in payload.get("games", []) or []:
        if int(game.get("gameType") or 0) != 2:
            continue
        if str(game.get("gameState") or "") not in FINAL_STATES:
            continue
        if season_start(game.get("season") or sid) != season:
            continue
        away = str((game.get("awayTeam") or {}).get("abbrev") or "")
        home = str((game.get("homeTeam") or {}).get("abbrev") or "")
        if not away or not home:
            continue
        games.append(
            GameSummary(
                game_id=str(game["id"]),
                season=season,
                game_date=str(game.get("gameDate") or ""),
                away_team=away,
                home_team=home,
            )
        )
    return games


def discover_games(cache_dir: Path, season: int, request_sleep: float) -> list[GameSummary]:
    global REQUEST_SLEEP_SECONDS
    REQUEST_SLEEP_SECONDS = request_sleep
    by_id: dict[str, GameSummary] = {}
    for team_id in local_team_codes(season):
        for game in fetch_club_schedule(cache_dir, team_id, season):
            by_id[game.game_id] = game
    return [by_id[key] for key in sorted(by_id)]


def parse_boxscore(game_id: str, cache_dir: Path) -> tuple[dict[str, Any], list[PlayerAppearance]]:
    payload = get_json(
        f"{API}/gamecenter/{game_id}/boxscore",
        cache_json_path(cache_dir, "boxscore", game_id),
    )
    if payload.get("_missing"):
        return payload, []
    season = season_start(payload.get("season"))
    game_date = str(payload.get("gameDate") or "")
    team_meta = {
        "awayTeam": payload.get("awayTeam") or {},
        "homeTeam": payload.get("homeTeam") or {},
    }
    team_ids = {
        "awayTeam": str(team_meta["awayTeam"].get("abbrev") or ""),
        "homeTeam": str(team_meta["homeTeam"].get("abbrev") or ""),
    }
    opponent = {"awayTeam": team_ids["homeTeam"], "homeTeam": team_ids["awayTeam"]}
    stats = payload.get("playerByGameStats") or {}
    rows: list[PlayerAppearance] = []
    for side in ("awayTeam", "homeTeam"):
        team_id = team_ids[side]
        if not team_id:
            continue
        for group in ("forwards", "defense", "goalies"):
            for player in (stats.get(side) or {}).get(group, []) or []:
                external_id = str(player.get("playerId") or "")
                seconds = toi_seconds(player.get("toi"))
                if not external_id or seconds <= 0:
                    continue
                rows.append(
                    PlayerAppearance(
                        game_id=str(payload["id"]),
                        season=season,
                        game_date=game_date,
                        team_id=team_id,
                        opponent_team_id=opponent[side],
                        player_id=f"nhl:{external_id}",
                        external_id=external_id,
                        display_name=localized(player.get("name")),
                        position=str(player.get("position") or ""),
                        toi_seconds=seconds,
                    )
                )
    return payload, rows


def upsert_game(conn: sqlite3.Connection, payload: dict[str, Any], rows: list[PlayerAppearance]) -> None:
    if payload.get("_missing") or not rows:
        return
    away = payload.get("awayTeam") or {}
    home = payload.get("homeTeam") or {}
    conn.execute(
        """
        INSERT INTO nhl_games
          (game_id, season, game_date, game_type, game_state, away_team_id, home_team_id,
           away_team_name, home_team_name, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(game_id) DO UPDATE SET
          season=excluded.season,
          game_date=excluded.game_date,
          game_type=excluded.game_type,
          game_state=excluded.game_state,
          away_team_id=excluded.away_team_id,
          home_team_id=excluded.home_team_id,
          away_team_name=excluded.away_team_name,
          home_team_name=excluded.home_team_name,
          source=excluded.source,
          fetched_at=CURRENT_TIMESTAMP
        """,
        (
            str(payload["id"]),
            season_start(payload["season"]),
            str(payload.get("gameDate") or ""),
            int(payload.get("gameType") or 0),
            str(payload.get("gameState") or ""),
            str(away.get("abbrev") or ""),
            str(home.get("abbrev") or ""),
            full_team_name(away),
            full_team_name(home),
            SOURCE,
        ),
    )
    conn.executemany(
        """
        INSERT INTO nhl_player_game_appearances
          (game_id, season, game_date, team_id, opponent_team_id, player_id, external_id,
           display_name, position, toi_seconds, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id, team_id, player_id) DO UPDATE SET
          season=excluded.season,
          game_date=excluded.game_date,
          opponent_team_id=excluded.opponent_team_id,
          external_id=excluded.external_id,
          display_name=excluded.display_name,
          position=excluded.position,
          toi_seconds=excluded.toi_seconds,
          source=excluded.source
        """,
        [
            (
                row.game_id,
                row.season,
                row.game_date,
                row.team_id,
                row.opponent_team_id,
                row.player_id,
                row.external_id,
                row.display_name,
                row.position,
                row.toi_seconds,
                SOURCE,
            )
            for row in rows
        ],
    )


def fetch_and_store_games(
    db_path: Path,
    cache_dir: Path,
    games: list[GameSummary],
    workers: int,
    request_sleep: float,
    progress_every: int,
    limit_games: int,
) -> None:
    global REQUEST_SLEEP_SECONDS
    REQUEST_SLEEP_SECONDS = request_sleep
    if limit_games:
        games = games[:limit_games]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_db(conn)
    target_ids = {game.game_id for game in games}
    seasons = sorted({game.season for game in games if game.season})
    existing: set[str] = set()
    if target_ids:
        if seasons:
            placeholders = ",".join("?" for _ in seasons)
            rows = conn.execute(
                f"SELECT game_id FROM nhl_games WHERE season IN ({placeholders})",
                seasons,
            ).fetchall()
        else:
            rows = conn.execute("SELECT game_id FROM nhl_games").fetchall()
        existing = {row[0] for row in rows if row[0] in target_ids}
    pending = [game for game in games if game.game_id not in existing]
    print(f"games discovered {len(games):,}; already stored {len(existing):,}; pending {len(pending):,}", flush=True)
    stored = 0
    appearances = 0
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(parse_boxscore, game.game_id, cache_dir) for game in pending]
            for index, future in enumerate(as_completed(futures), 1):
                payload, rows = future.result()
                upsert_game(conn, payload, rows)
                stored += int(bool(rows))
                appearances += len(rows)
                if progress_every and index % progress_every == 0:
                    conn.commit()
                    print(
                        f"boxscores {index:,}/{len(pending):,}; stored games {stored:,}; "
                        f"appearances {appearances:,}",
                        flush=True,
                    )
        conn.commit()
    finally:
        conn.close()
    print(f"boxscore import complete: stored {stored:,} games; appearances {appearances:,}", flush=True)


def rebuild_teammate_proofs(db_path: Path, season_start: int, season_end: int) -> None:
    conn = sqlite3.connect(db_path)
    init_db(conn)
    try:
        conn.execute(
            "DELETE FROM nhl_teammate_game_proofs WHERE season BETWEEN ? AND ?",
            (season_start, season_end),
        )
        conn.execute(
            """
            INSERT INTO nhl_teammate_game_proofs
              (player_a_id, player_b_id, team_id, season, shared_games, first_game_id, first_game_date)
            WITH grouped AS (
              SELECT
                a.player_id AS player_a_id,
                b.player_id AS player_b_id,
                a.team_id AS team_id,
                a.season AS season,
                COUNT(*) AS shared_games,
                MIN(a.game_date || '|' || a.game_id) AS first_key
              FROM nhl_player_game_appearances a
              JOIN nhl_player_game_appearances b
                ON b.game_id = a.game_id
               AND b.team_id = a.team_id
               AND b.player_id > a.player_id
             WHERE a.season BETWEEN ? AND ?
             GROUP BY a.player_id, b.player_id, a.team_id, a.season
            )
            SELECT
              player_a_id,
              player_b_id,
              team_id,
              season,
              shared_games,
              substr(first_key, instr(first_key, '|') + 1) AS first_game_id,
              substr(first_key, 1, instr(first_key, '|') - 1) AS first_game_date
            FROM grouped
            """,
            (season_start, season_end),
        )
        conn.commit()
        proof_count = conn.execute(
            """
            SELECT COUNT(*)
              FROM nhl_teammate_game_proofs
             WHERE season BETWEEN ? AND ?
            """,
            (season_start, season_end),
        ).fetchone()[0]
        print(f"proof rebuild complete: {proof_count:,} player-pair team-season proofs", flush=True)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--season-start", type=int, default=2000)
    parser.add_argument("--season-end", type=int, default=2025)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--request-sleep", type=float, default=1.5)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--limit-games", type=int, default=0)
    parser.add_argument(
        "--game-id",
        action="append",
        default=[],
        help="Import one specific NHL gamecenter id; may be passed more than once.",
    )
    parser.add_argument("--schedule-only", action="store_true")
    parser.add_argument("--rebuild-proofs-only", action="store_true")
    parser.add_argument("--no-lock", action="store_true")
    args = parser.parse_args()

    acquire_lock(args.cache_dir / "build.lock", disabled=args.no_lock or args.rebuild_proofs_only)

    if args.rebuild_proofs_only:
        rebuild_teammate_proofs(args.db, args.season_start, args.season_end)
        return

    if args.game_id:
        games = [
            GameSummary(game_id=str(game_id), season=args.season_start, game_date="", away_team="", home_team="")
            for game_id in args.game_id
        ]
        print(f"direct game import: {len(games):,} game(s)", flush=True)
    else:
        all_games: dict[str, GameSummary] = {}
        for season in range(args.season_start, args.season_end + 1):
            games_for_season = discover_games(args.cache_dir, season, args.request_sleep)
            all_games.update((game.game_id, game) for game in games_for_season)
            print(f"season {season}: discovered {len(games_for_season):,} regular-season games", flush=True)
        games = [all_games[key] for key in sorted(all_games)]
        print(f"total discovered games: {len(games):,}", flush=True)
    if args.schedule_only:
        return

    fetch_and_store_games(
        args.db,
        args.cache_dir,
        games,
        args.workers,
        args.request_sleep,
        args.progress_every,
        args.limit_games,
    )
    rebuild_teammate_proofs(args.db, args.season_start, args.season_end)


if __name__ == "__main__":
    main()
