#!/usr/bin/env python3
"""Build local MLB teammate proofs from game-level regular-season boxscores.

Definition: two Baseball players are teammates only if they both appeared in at
least one regular-season game for the same team. The output is compact: one
proof row per player pair, team, and season, with the first shared proof game.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
BASEBALL_DB = ROOT / "db" / "base2nerdle.sqlite"
DEFAULT_DB = ROOT / "raw" / "mlb_game_teammates" / "mlb_game_teammates.sqlite"
MLB_CACHE = ROOT / "raw" / "mlb_statsapi"
API = "https://statsapi.mlb.com/api/v1"
SOURCE_NAME = "mlb_statsapi_game_boxscore"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TeamMateTag/0.4.16 mlb-game-proof-builder"})


@dataclass(frozen=True)
class Appearance:
    game_pk: int
    season: int
    game_date: str
    team_id: str
    player_id: str
    mlbam_id: int
    games_pitched: int
    games_batted: int


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS mlb_games (
  game_pk INTEGER PRIMARY KEY,
  season INTEGER NOT NULL,
  game_date TEXT NOT NULL,
  away_team_id TEXT NOT NULL,
  home_team_id TEXT NOT NULL,
  source TEXT NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mlb_player_game_appearances (
  game_pk INTEGER NOT NULL,
  season INTEGER NOT NULL,
  game_date TEXT NOT NULL,
  team_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  mlbam_id INTEGER NOT NULL,
  games_pitched INTEGER NOT NULL DEFAULT 0,
  games_batted INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL,
  PRIMARY KEY (game_pk, team_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_mlb_pga_player
  ON mlb_player_game_appearances(player_id, season, team_id);
CREATE INDEX IF NOT EXISTS idx_mlb_pga_game_team
  ON mlb_player_game_appearances(game_pk, team_id);
CREATE INDEX IF NOT EXISTS idx_mlb_pga_season_team
  ON mlb_player_game_appearances(season, team_id);

CREATE TABLE IF NOT EXISTS mlb_teammate_game_proofs (
  player_a_id TEXT NOT NULL,
  player_b_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  season INTEGER NOT NULL,
  shared_games INTEGER NOT NULL,
  first_game_pk INTEGER NOT NULL,
  first_game_date TEXT NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (player_a_id, player_b_id, team_id, season),
  CHECK (player_a_id < player_b_id)
);

CREATE INDEX IF NOT EXISTS idx_mlb_tgp_a_b
  ON mlb_teammate_game_proofs(player_a_id, player_b_id);
CREATE INDEX IF NOT EXISTS idx_mlb_tgp_b_a
  ON mlb_teammate_game_proofs(player_b_id, player_a_id);
CREATE INDEX IF NOT EXISTS idx_mlb_tgp_team_season
  ON mlb_teammate_game_proofs(team_id, season);
"""


def field(row: dict, *names: str) -> str:
    lowered = {name.lower(): value for name, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def norm_name(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def get_json(url: str, cache_path: Path, sleep_seconds: float = 0.03) -> dict[str, Any]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()
    cache_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    if sleep_seconds:
        time.sleep(sleep_seconds)
    return data


def final_regular_season_game(game: dict[str, Any]) -> bool:
    if game.get("gameType") != "R":
        return False
    status = game.get("status") or {}
    return (
        status.get("abstractGameState") == "Final"
        or status.get("codedGameState") in {"F", "O"}
        or status.get("statusCode") in {"F", "O"}
        or status.get("detailedState") in {"Final", "Game Over", "Completed Early"}
    )


def int_stat(stats: dict[str, Any], key: str) -> int:
    try:
        return int(stats.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def stat_group_played(stats: dict[str, Any], group: str) -> bool:
    group_stats = stats.get(group) or {}
    if not group_stats:
        return False
    if int_stat(group_stats, "gamesPlayed") > 0 or int_stat(group_stats, "gamesStarted") > 0:
        return True
    if group == "pitching":
        return any(int_stat(group_stats, key) > 0 for key in ("gamesPitched", "battersFaced", "outs"))
    if group == "batting":
        return any(int_stat(group_stats, key) > 0 for key in ("plateAppearances", "atBats", "runs"))
    if group == "fielding":
        return any(int_stat(group_stats, key) > 0 for key in ("putOuts", "assists", "errors"))
    return False


def appeared_in_game(entry: dict[str, Any]) -> tuple[int, int]:
    stats = entry.get("stats") or {}
    pitched = 1 if stat_group_played(stats, "pitching") else 0
    batted = 1 if stat_group_played(stats, "batting") else 0
    fielded = 1 if stat_group_played(stats, "fielding") else 0
    if not (pitched or batted or fielded):
        return 0, 0
    return pitched, batted


def load_chadwick_mlbam_map(valid_players: set[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in sorted((ROOT / "raw" / "chadwick").glob("people-*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                mlbam = field(row, "key_mlbam")
                bbref = field(row, "key_bbref")
                if mlbam and bbref and bbref in valid_players:
                    try:
                        mapping[int(mlbam)] = bbref
                    except ValueError:
                        continue
    return mapping


def load_mlb_team_maps(conn: sqlite3.Connection) -> tuple[dict[tuple[int, str], str], dict[tuple[int, str], str]]:
    manual_abbr = {
        "AZ": "ARI", "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
        "CHC": "CHN", "CIN": "CIN", "CLE": "CLE", "COL": "COL", "CWS": "CHA",
        "DET": "DET", "HOU": "HOU", "KC": "KCA", "KCR": "KCA", "LAA": "LAA",
        "LAD": "LAN", "MIA": "MIA", "FLA": "FLO", "MIL": "MIL", "MIN": "MIN",
        "NYM": "NYN", "NYY": "NYA", "OAK": "OAK", "ATH": "ATH", "PHI": "PHI",
        "PIT": "PIT", "SD": "SDN", "SDP": "SDN", "SEA": "SEA", "SF": "SFN",
        "SFG": "SFN", "STL": "SLN", "TB": "TBA", "TBR": "TBA", "TBD": "TBA",
        "TEX": "TEX", "TOR": "TOR", "WSH": "WAS", "WAS": "WAS", "MON": "MON",
    }
    by_name: dict[tuple[int, str], str] = {}
    by_abbr: dict[tuple[int, str], str] = {}
    for team_id, season, name in conn.execute("SELECT team_id, season, name FROM teams WHERE season >= 2000"):
        by_name[(int(season), norm_name(name))] = team_id
        for abbr, mapped in manual_abbr.items():
            if mapped == team_id:
                by_abbr[(int(season), abbr)] = team_id
    return by_name, by_abbr


def source_maps(conn: sqlite3.Connection) -> tuple[set[tuple[str, str, int]], dict[int, str], dict[tuple[int, str], str], dict[tuple[int, str], str]]:
    valid_rows = conn.execute(
        "SELECT player_id, team_id, season FROM appearances WHERE season >= 2000"
    ).fetchall()
    valid = {(player_id, team_id, int(season)) for player_id, team_id, season in valid_rows}
    valid_players = {row[0] for row in valid_rows}
    mlbam_to_player = {
        int(mlbam): player_id
        for player_id, mlbam in conn.execute("SELECT player_id, mlbam_id FROM players WHERE mlbam_id IS NOT NULL")
        if mlbam
    }
    mlbam_to_player.update(load_chadwick_mlbam_map(valid_players))
    by_name, by_abbr = load_mlb_team_maps(conn)
    return valid, mlbam_to_player, by_name, by_abbr


def collect_games(season_start: int, season_end: int) -> dict[int, tuple[int, str]]:
    games: dict[int, tuple[int, str]] = {}
    for season in range(season_start, season_end + 1):
        url = f"{API}/schedule?sportId=1&season={season}&gameTypes=R"
        schedule = get_json(url, MLB_CACHE / "schedules" / f"{season}.json")
        season_games = 0
        for day in schedule.get("dates", []):
            for game in day.get("games", []):
                if not final_regular_season_game(game):
                    continue
                game_pk = game.get("gamePk")
                game_date = (game.get("officialDate") or game.get("gameDate") or "")[:10]
                if game_pk and game_date:
                    games[int(game_pk)] = (season, game_date)
                    season_games += 1
        print(f"season {season}: discovered {season_games:,} regular-season games", flush=True)
    return games


def team_id_for_box_team(
    team: dict[str, Any],
    season: int,
    by_name: dict[tuple[int, str], str],
    by_abbr: dict[tuple[int, str], str],
) -> str | None:
    name = team.get("name") or ""
    abbr = (team.get("abbreviation") or team.get("teamCode") or "").upper()
    return by_name.get((season, norm_name(name))) or by_abbr.get((season, abbr))


def iter_appearances(
    games: dict[int, tuple[int, str]],
    valid: set[tuple[str, str, int]],
    mlbam_to_player: dict[int, str],
    by_name: dict[tuple[int, str], str],
    by_abbr: dict[tuple[int, str], str],
) -> tuple[list[Appearance], Counter, list[dict[str, Any]], list[tuple[int, int, str, str]]]:
    rows: list[Appearance] = []
    skipped: Counter = Counter()
    unmapped_players: dict[tuple[int, str], dict[str, Any]] = {}
    game_rows: list[tuple[int, int, str, str]] = []
    start = time.monotonic()
    for index, (game_pk, (season, game_date)) in enumerate(sorted(games.items()), 1):
        box_path = MLB_CACHE / "boxscores" / f"{game_pk}.json"
        try:
            box = get_json(f"{API}/game/{game_pk}/boxscore", box_path, sleep_seconds=0)
        except Exception:
            skipped["boxscore_fetch_error"] += 1
            continue
        teams = box.get("teams") or {}
        side_team_ids: dict[str, str] = {}
        for side in ("away", "home"):
            team = (teams.get(side) or {}).get("team") or {}
            tid = team_id_for_box_team(team, season, by_name, by_abbr)
            if tid:
                side_team_ids[side] = tid
            else:
                skipped["unmapped_team"] += 1
        if "away" in side_team_ids and "home" in side_team_ids:
            game_rows.append((game_pk, season, game_date, side_team_ids["away"], side_team_ids["home"]))
        for side, tid in side_team_ids.items():
            for entry in ((teams.get(side) or {}).get("players") or {}).values():
                pitched, batted = appeared_in_game(entry)
                if not (pitched or batted):
                    skipped["did_not_appear"] += 1
                    continue
                person = entry.get("person") or {}
                mlbam_id = person.get("id")
                player_id = mlbam_to_player.get(mlbam_id)
                if not player_id:
                    skipped["unmapped_player"] += 1
                    key = (int(mlbam_id or 0), str(person.get("fullName") or ""))
                    unmapped_players.setdefault(
                        key,
                        {
                            "mlbam_id": key[0],
                            "display_name": key[1],
                            "first_seen_season": season,
                            "first_seen_game_pk": game_pk,
                            "row_count": 0,
                        },
                    )["row_count"] += 1
                    continue
                if (player_id, tid, season) not in valid:
                    skipped["not_in_runtime_appearance"] += 1
                    continue
                rows.append(
                    Appearance(
                        game_pk=game_pk,
                        season=season,
                        game_date=game_date,
                        team_id=tid,
                        player_id=player_id,
                        mlbam_id=int(mlbam_id),
                        games_pitched=pitched,
                        games_batted=batted,
                    )
                )
        if index == 1 or index % 1000 == 0 or index == len(games):
            elapsed = max(time.monotonic() - start, 0.1)
            print(
                f"boxscores {index:,}/{len(games):,}; appearances {len(rows):,}; "
                f"rate {index / elapsed * 3600:,.0f}/h",
                flush=True,
            )
    return rows, skipped, list(unmapped_players.values()), game_rows


def reset_tables(conn: sqlite3.Connection, season_start: int, season_end: int) -> None:
    conn.execute("DELETE FROM mlb_teammate_game_proofs WHERE season BETWEEN ? AND ?", (season_start, season_end))
    conn.execute("DELETE FROM mlb_player_game_appearances WHERE season BETWEEN ? AND ?", (season_start, season_end))
    conn.execute("DELETE FROM mlb_games WHERE season BETWEEN ? AND ?", (season_start, season_end))
    conn.commit()


def store_rows(conn: sqlite3.Connection, appearances: list[Appearance], games: list[tuple[int, int, str, str]]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO mlb_games
           (game_pk, season, game_date, away_team_id, home_team_id, source)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [(game_pk, season, game_date, away, home, SOURCE_NAME) for game_pk, season, game_date, away, home in games],
    )
    conn.executemany(
        """INSERT OR REPLACE INTO mlb_player_game_appearances
           (game_pk, season, game_date, team_id, player_id, mlbam_id, games_pitched, games_batted, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                row.game_pk,
                row.season,
                row.game_date,
                row.team_id,
                row.player_id,
                row.mlbam_id,
                row.games_pitched,
                row.games_batted,
                SOURCE_NAME,
            )
            for row in appearances
        ],
    )
    conn.commit()


def build_proof_rows(appearances: list[Appearance]) -> list[tuple[str, str, str, int, int, int, str, str]]:
    proofs: dict[tuple[str, str, str, int], list[Any]] = {}
    sorted_rows = sorted(appearances, key=lambda row: (row.game_pk, row.team_id, row.player_id))
    current_key: tuple[int, str] | None = None
    current_rows: list[Appearance] = []
    start = time.monotonic()
    groups = 0

    def flush_group(rows: list[Appearance]) -> None:
        if len(rows) < 2:
            return
        first = rows[0]
        player_ids = sorted({row.player_id for row in rows})
        for index, player_a in enumerate(player_ids):
            for player_b in player_ids[index + 1 :]:
                key = (player_a, player_b, first.team_id, first.season)
                current = proofs.get(key)
                if current is None:
                    proofs[key] = [1, first.game_pk, first.game_date, SOURCE_NAME]
                else:
                    current[0] += 1
                    if (first.game_date, first.game_pk) < (current[2], current[1]):
                        current[1] = first.game_pk
                        current[2] = first.game_date

    for row in sorted_rows:
        key = (row.game_pk, row.team_id)
        if current_key is None:
            current_key = key
        if key != current_key:
            flush_group(current_rows)
            groups += 1
            if groups == 1 or groups % 5000 == 0:
                elapsed = max(time.monotonic() - start, 0.1)
                print(
                    f"proof groups {groups:,}; unique proofs {len(proofs):,}; "
                    f"rate {groups / elapsed * 3600:,.0f}/h",
                    flush=True,
                )
            current_key = key
            current_rows = []
        current_rows.append(row)
    flush_group(current_rows)
    groups += 1 if current_rows else 0
    print(f"proof groups {groups:,}; unique proofs {len(proofs):,}", flush=True)
    return [
        (player_a, player_b, team_id, season, values[0], values[1], values[2], values[3])
        for (player_a, player_b, team_id, season), values in proofs.items()
    ]


def store_proofs(
    conn: sqlite3.Connection,
    proof_rows: list[tuple[str, str, str, int, int, int, str, str]],
    season_start: int,
    season_end: int,
) -> int:
    conn.execute("DELETE FROM mlb_teammate_game_proofs WHERE season BETWEEN ? AND ?", (season_start, season_end))
    conn.executemany(
        """INSERT OR REPLACE INTO mlb_teammate_game_proofs
           (player_a_id, player_b_id, team_id, season, shared_games, first_game_pk, first_game_date, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        proof_rows,
    )
    conn.commit()
    return len(proof_rows)


def write_unmapped(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["mlbam_id", "display_name", "first_seen_season", "first_seen_game_pk", "row_count"],
        )
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (-int(row["row_count"]), row["display_name"])))


def print_summary(conn: sqlite3.Connection, season_start: int, season_end: int) -> None:
    games = dict(conn.execute(
        "SELECT season, COUNT(*) FROM mlb_games WHERE season BETWEEN ? AND ? GROUP BY season",
        (season_start, season_end),
    ))
    appearances = dict(conn.execute(
        "SELECT season, COUNT(*) FROM mlb_player_game_appearances WHERE season BETWEEN ? AND ? GROUP BY season",
        (season_start, season_end),
    ))
    proofs = dict(conn.execute(
        "SELECT season, COUNT(*) FROM mlb_teammate_game_proofs WHERE season BETWEEN ? AND ? GROUP BY season",
        (season_start, season_end),
    ))
    print("season games appearances proofs", flush=True)
    for season in range(season_start, season_end + 1):
        print(
            f"{season} {games.get(season, 0):,} "
            f"{appearances.get(season, 0):,} {proofs.get(season, 0):,}",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--baseball-db", type=Path, default=BASEBALL_DB)
    parser.add_argument("--season-start", type=int, default=2000)
    parser.add_argument("--season-end", type=int, default=2025)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db) as out:
        out.executescript(SCHEMA)
        if args.summary_only:
            print_summary(out, args.season_start, args.season_end)
            return 0
        if args.reset:
            reset_tables(out, args.season_start, args.season_end)
        with sqlite3.connect(args.baseball_db) as src:
            valid, mlbam_to_player, by_name, by_abbr = source_maps(src)
        print(
            f"source maps: {len(valid):,} player-team-seasons, "
            f"{len(mlbam_to_player):,} MLBAM player ids",
            flush=True,
        )
        games = collect_games(args.season_start, args.season_end)
        appearances, skipped, unmapped, game_rows = iter_appearances(games, valid, mlbam_to_player, by_name, by_abbr)
        store_rows(out, appearances, game_rows)
        proof_rows = build_proof_rows(appearances)
        proofs = store_proofs(out, proof_rows, args.season_start, args.season_end)
        unmapped_path = args.db.parent / "mlb_unmapped_players.csv"
        write_unmapped(unmapped_path, unmapped)
        print_summary(out, args.season_start, args.season_end)
        print(f"skipped: {dict(skipped)}", flush=True)
        print(f"unmapped player audit: {unmapped_path} ({len(unmapped):,} players)", flush=True)
        print(
            f"complete: {len(game_rows):,} games, {len(appearances):,} appearances, "
            f"{proofs:,} teammate/team-season proof rows",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
