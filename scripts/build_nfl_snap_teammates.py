#!/usr/bin/env python3
"""Build strict NFL teammate proof input from nflverse snap counts.

A Football teammate link is valid for covered seasons only when both players
recorded at least one offensive, defensive, or special-teams snap for the same
NFL team in the same regular-season game.
"""
from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "raw" / "nfl_game_teammates" / "nfl_snap_teammates.sqlite"
SNAP_CACHE = ROOT / "raw" / "nfl" / "snap_counts"
PLAYERS_CACHE = ROOT / "raw" / "nfl" / "players.csv"
MANUAL_OVERRIDES = ROOT / "scripts" / "data" / "nfl_pfr_manual_id_overrides.csv"
PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
SNAP_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
SOURCE_NAME = "nflverse_snap_counts"


SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS nfl_games (
    game_id TEXT PRIMARY KEY,
    pfr_game_id TEXT,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    game_type TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nfl_player_game_snap_appearances (
    game_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    pfr_player_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    position TEXT,
    offense_snaps INTEGER NOT NULL DEFAULT 0,
    defense_snaps INTEGER NOT NULL DEFAULT 0,
    special_teams_snaps INTEGER NOT NULL DEFAULT 0,
    total_snaps INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, player_id, team_id)
);
CREATE INDEX IF NOT EXISTS idx_nfl_snap_player
    ON nfl_player_game_snap_appearances(player_id, season, team_id);
CREATE INDEX IF NOT EXISTS idx_nfl_snap_game_team
    ON nfl_player_game_snap_appearances(game_id, team_id, player_id);
CREATE TABLE IF NOT EXISTS nfl_snap_players (
    player_id TEXT PRIMARY KEY,
    pfr_player_id TEXT NOT NULL,
    gsis_id TEXT,
    display_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    birth_year INTEGER,
    primary_pos TEXT,
    headshot_url TEXT
);
CREATE TABLE IF NOT EXISTS nfl_snap_unmapped_players (
    pfr_player_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    seasons TEXT NOT NULL,
    teams TEXT NOT NULL,
    positions TEXT NOT NULL,
    row_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS nfl_snap_build_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    with urlopen(url, timeout=120) as response:
        destination.write_bytes(response.read())


def parse_int(value: str | None) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def load_manual_overrides() -> dict[str, str]:
    if not MANUAL_OVERRIDES.exists():
        return {}
    with MANUAL_OVERRIDES.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["pfr_player_id"].strip(): row["player_id"].strip()
            for row in csv.DictReader(handle)
            if row.get("pfr_player_id") and row.get("player_id")
        }


def split_name(name: str) -> tuple[str | None, str | None]:
    parts = [part for part in name.split() if part]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[-1]


def load_player_registry() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    download(PLAYERS_URL, PLAYERS_CACHE)
    pfr_to_player_id: dict[str, str] = {}
    registry: dict[str, dict[str, str]] = {}
    with PLAYERS_CACHE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            pfr = (row.get("pfr_id") or "").strip()
            gsis = (row.get("gsis_id") or "").strip()
            if pfr and gsis:
                pfr_to_player_id[pfr] = f"nfl:{gsis}"
                registry[pfr] = row
    pfr_to_player_id.update(load_manual_overrides())
    return pfr_to_player_id, registry


def build(args: argparse.Namespace) -> None:
    pfr_to_player_id, registry = load_player_registry()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.output)
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM nfl_games")
    conn.execute("DELETE FROM nfl_player_game_snap_appearances")
    conn.execute("DELETE FROM nfl_snap_players")
    conn.execute("DELETE FROM nfl_snap_unmapped_players")
    conn.execute("DELETE FROM nfl_snap_build_meta")
    unmapped: dict[str, dict[str, object]] = {}
    game_ids: set[str] = set()
    player_meta: dict[str, dict[str, str]] = {}
    total_rows = 0
    stored_rows = 0
    for season in range(args.season_start, args.season_end + 1):
        path = SNAP_CACHE / f"snap_counts_{season}.csv"
        download(SNAP_URL.format(season=season), path)
        season_rows = 0
        season_games = set()
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                total_rows += 1
                if row.get("game_type") != "REG":
                    continue
                offense = parse_int(row.get("offense_snaps"))
                defense = parse_int(row.get("defense_snaps"))
                special = parse_int(row.get("st_snaps"))
                total = offense + defense + special
                if total <= 0:
                    continue
                pfr = (row.get("pfr_player_id") or "").strip()
                if not pfr:
                    continue
                game_id = (row.get("game_id") or "").strip()
                team = (row.get("team") or "").strip().upper()
                if not game_id or not team:
                    continue
                player_id = pfr_to_player_id.get(pfr)
                display_name = (row.get("player") or "").strip()
                position = (row.get("position") or "").strip() or None
                if player_id is None:
                    player_id = f"nfl_pfr:{pfr}"
                    entry = unmapped.setdefault(
                        pfr,
                        {"display_name": display_name, "seasons": set(), "teams": set(), "positions": set(), "rows": 0},
                    )
                    entry["seasons"].add(str(season))
                    entry["teams"].add(team)
                    if position:
                        entry["positions"].add(position)
                    entry["rows"] = int(entry["rows"]) + 1
                meta = registry.get(pfr, {})
                player_meta.setdefault(
                    player_id,
                    {
                        "pfr_player_id": pfr,
                        "gsis_id": player_id.removeprefix("nfl:") if player_id.startswith("nfl:") else "",
                        "display_name": meta.get("display_name") or display_name,
                        "first_name": meta.get("first_name") or split_name(display_name)[0] or "",
                        "last_name": meta.get("last_name") or split_name(display_name)[1] or "",
                        "birth_year": (meta.get("birth_date") or "")[:4],
                        "primary_pos": meta.get("position") or position or "",
                        "headshot_url": meta.get("headshot") or "",
                    },
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO nfl_player_game_snap_appearances
                        (game_id, season, week, team_id, player_id, pfr_player_id,
                         display_name, position, offense_snaps, defense_snaps,
                         special_teams_snaps, total_snaps)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        game_id,
                        season,
                        parse_int(row.get("week")),
                        team,
                        player_id,
                        pfr,
                        display_name,
                        position,
                        offense,
                        defense,
                        special,
                        total,
                    ),
                )
                game_ids.add(game_id)
                season_games.add(game_id)
                season_rows += 1
                stored_rows += 1
        for game_id in season_games:
            parts = game_id.split("_")
            week = parse_int(parts[1] if len(parts) > 1 else "0")
            conn.execute(
                """
                INSERT OR REPLACE INTO nfl_games
                    (game_id, pfr_game_id, season, week, game_type, source)
                VALUES (?, ?, ?, ?, 'REG', ?)
                """,
                (game_id, None, season, week, SOURCE_NAME),
            )
        print(f"season {season}: games {len(season_games):,}; snap appearances {season_rows:,}", flush=True)
    conn.executemany(
        """
        INSERT OR REPLACE INTO nfl_snap_players
            (player_id, pfr_player_id, gsis_id, display_name, first_name,
             last_name, birth_year, primary_pos, headshot_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                player_id,
                meta["pfr_player_id"],
                meta["gsis_id"] or None,
                meta["display_name"],
                meta["first_name"] or None,
                meta["last_name"] or None,
                int(meta["birth_year"]) if str(meta["birth_year"]).isdigit() else None,
                meta["primary_pos"] or None,
                meta["headshot_url"] or None,
            )
            for player_id, meta in sorted(player_meta.items())
        ],
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO nfl_snap_unmapped_players
            (pfr_player_id, display_name, seasons, teams, positions, row_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                pfr,
                str(entry["display_name"]),
                ",".join(sorted(entry["seasons"])),
                ",".join(sorted(entry["teams"])),
                ",".join(sorted(entry["positions"])),
                int(entry["rows"]),
            )
            for pfr, entry in sorted(unmapped.items())
        ],
    )
    for key, value in {
        "source": SOURCE_NAME,
        "season_start": str(args.season_start),
        "season_end": str(args.season_end),
        "games": str(len(game_ids)),
        "snap_appearances": str(stored_rows),
        "players": str(len(player_meta)),
        "unmapped_pfr_players": str(len(unmapped)),
        "total_source_rows": str(total_rows),
    }.items():
        conn.execute("INSERT OR REPLACE INTO nfl_snap_build_meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    print(
        f"built {args.output}: games {len(game_ids):,}; snap appearances {stored_rows:,}; "
        f"players {len(player_meta):,}; unresolved pfr ids {len(unmapped):,}",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season-start", type=int, default=2013)
    parser.add_argument("--season-end", type=int, default=2025)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
