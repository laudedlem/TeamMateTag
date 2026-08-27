#!/usr/bin/env python3
"""Build local NBA teammate proof from game-level regular-season appearances.

Rule proven by this dataset:
  two players are teammates only if both appeared in at least one regular-season
  NBA game for the same team.

The historical source is the local NBA Stats-shaped PlayerStatistics.csv. It
uses NBA person IDs, which match TeamMateTag's existing basketball player ids.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "raw" / "nba_kaggle" / "PlayerStatistics.csv"
DEFAULT_DB = ROOT / "raw" / "nba_game_teammates" / "nba_game_teammates.sqlite"
LOCAL_SPORT_DB = ROOT / "db" / "teammatetag_local.sqlite"
SOURCE_NAME = "nba_player_statistics_game_boxscore"

OFFICIAL_NBA_TEAM_IDS = {
    "1610612737", "1610612738", "1610612739", "1610612740", "1610612741",
    "1610612742", "1610612743", "1610612744", "1610612745", "1610612746",
    "1610612747", "1610612748", "1610612749", "1610612750", "1610612751",
    "1610612752", "1610612753", "1610612754", "1610612755", "1610612756",
    "1610612757", "1610612758", "1610612759", "1610612760", "1610612761",
    "1610612762", "1610612763", "1610612764", "1610612765", "1610612766",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS nba_games (
  game_id TEXT PRIMARY KEY,
  season INTEGER NOT NULL,
  game_date TEXT NOT NULL,
  source TEXT NOT NULL,
  source_rows INTEGER NOT NULL DEFAULT 0,
  appearance_rows INTEGER NOT NULL DEFAULT 0,
  teams_seen INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_nba_games_season ON nba_games(season);

CREATE TABLE IF NOT EXISTS nba_player_game_appearances (
  game_id TEXT NOT NULL,
  season INTEGER NOT NULL,
  game_date TEXT NOT NULL,
  team_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  minutes REAL NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (game_id, team_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_nba_appearances_player
  ON nba_player_game_appearances(player_id, season, team_id);
CREATE INDEX IF NOT EXISTS idx_nba_appearances_game_team
  ON nba_player_game_appearances(game_id, team_id);

CREATE TABLE IF NOT EXISTS nba_teammate_game_proofs (
  player_a_id TEXT NOT NULL,
  player_b_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  season INTEGER NOT NULL,
  shared_games INTEGER NOT NULL,
  first_game_id TEXT NOT NULL,
  first_game_date TEXT NOT NULL,
  last_game_id TEXT NOT NULL,
  last_game_date TEXT NOT NULL,
  PRIMARY KEY (player_a_id, player_b_id, team_id, season)
);
CREATE INDEX IF NOT EXISTS idx_nba_teammate_proofs_a
  ON nba_teammate_game_proofs(player_a_id);
CREATE INDEX IF NOT EXISTS idx_nba_teammate_proofs_b
  ON nba_teammate_game_proofs(player_b_id);
CREATE INDEX IF NOT EXISTS idx_nba_teammate_proofs_team
  ON nba_teammate_game_proofs(team_id, season);

CREATE TABLE IF NOT EXISTS nba_build_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Appearance:
    game_id: str
    season: int
    game_date: str
    team_id: str
    player_id: str
    external_id: str
    display_name: str
    minutes: float


def normalize_team_name(value: str) -> str:
    return " ".join((value or "").lower().replace(".", "").split())


def season_from_game_id(game_id: str, fallback_date: str) -> int | None:
    digits = "".join(ch for ch in str(game_id or "") if ch.isdigit())
    if len(digits) >= 5:
        padded = digits.zfill(10)
        if not padded.startswith("002"):
            return None
        yy = int(padded[3:5])
        return 1900 + yy if yy >= 47 else 2000 + yy
    if fallback_date:
        try:
            dt = datetime.strptime(fallback_date[:10], "%Y-%m-%d")
        except ValueError:
            return None
        return dt.year - 1 if dt.month <= 6 else dt.year
    return None


def clean_date(value: str) -> str:
    return (value or "")[:10]


def field(row: dict[str, str], *names: str) -> str:
    lowered = {name.lower(): value for name, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def parse_minutes(value: str) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    if ":" in text:
        parts = text.split(":")
        try:
            return float(parts[0]) + (float(parts[1]) / 60.0 if len(parts) > 1 else 0.0)
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def load_team_name_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    if LOCAL_SPORT_DB.exists():
        conn = sqlite3.connect(LOCAL_SPORT_DB)
        try:
            for team_id, name in conn.execute(
                "SELECT DISTINCT team_id, name FROM sport_teams WHERE sport_id='basketball'"
            ):
                if str(team_id) in OFFICIAL_NBA_TEAM_IDS:
                    mapping[normalize_team_name(str(name))] = str(team_id)
        finally:
            conn.close()

    # Names/abbreviations that appear in the NBA Stats-shaped file.
    mapping.update({
        "atlanta hawks": "1610612737",
        "boston celtics": "1610612738",
        "cleveland cavaliers": "1610612739",
        "new orleans hornets": "1610612740",
        "new orleans pelicans": "1610612740",
        "oklahoma city hornets": "1610612740",
        "chicago bulls": "1610612741",
        "dallas mavericks": "1610612742",
        "denver nuggets": "1610612743",
        "golden state warriors": "1610612744",
        "houston rockets": "1610612745",
        "la clippers": "1610612746",
        "los angeles clippers": "1610612746",
        "los angeles lakers": "1610612747",
        "miami heat": "1610612748",
        "milwaukee bucks": "1610612749",
        "minnesota timberwolves": "1610612750",
        "new jersey nets": "1610612751",
        "brooklyn nets": "1610612751",
        "new york knicks": "1610612752",
        "orlando magic": "1610612753",
        "indiana pacers": "1610612754",
        "philadelphia 76ers": "1610612755",
        "phoenix suns": "1610612756",
        "portland trail blazers": "1610612757",
        "sacramento kings": "1610612758",
        "san antonio spurs": "1610612759",
        "seattle supersonics": "1610612760",
        "oklahoma city thunder": "1610612760",
        "toronto raptors": "1610612761",
        "utah jazz": "1610612762",
        "vancouver grizzlies": "1610612763",
        "memphis grizzlies": "1610612763",
        "washington wizards": "1610612764",
        "detroit pistons": "1610612765",
        "charlotte bobcats": "1610612766",
        "charlotte hornets": "1610612766",
    })
    return mapping


def infer_team_id(row: dict[str, str], team_name_map: dict[str, str]) -> str:
    raw_id = field(row, "playerteamId", "teamId")
    if raw_id in OFFICIAL_NBA_TEAM_IDS:
        return raw_id
    city = field(row, "playerteamCity", "team_location")
    name = field(row, "playerteamName", "team_name")
    display = field(row, "team_display_name")
    for candidate in (f"{city} {name}", display, name):
        team_id = team_name_map.get(normalize_team_name(candidate))
        if team_id:
            return team_id
    return ""


def iter_appearances(
    source_path: Path,
    season_start: int,
    season_end: int,
) -> tuple[list[Appearance], dict[str, dict[str, object]], dict[str, int]]:
    team_name_map = load_team_name_map()
    appearances: list[Appearance] = []
    games: dict[str, dict[str, object]] = {}
    stats = defaultdict(int)

    with source_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            stats["source_rows"] += 1
            if field(row, "gameType", "season_type") not in {"Regular Season", "2"}:
                stats["skipped_non_regular"] += 1
                continue
            game_id = field(row, "gameId", "game_id")
            game_date = clean_date(field(row, "gameDate", "gameDateTimeEst", "game_date"))
            season = season_from_game_id(game_id, game_date)
            if season is None or season < season_start or season > season_end:
                stats["skipped_out_of_range"] += 1
                continue
            team_id = infer_team_id(row, team_name_map)
            if not team_id:
                stats["skipped_unmapped_team"] += 1
                continue
            external_id = field(row, "personId", "playerId", "athlete_id")
            first_name = field(row, "firstName")
            last_name = field(row, "lastName")
            display_name = field(row, "athlete_display_name") or f"{first_name} {last_name}".strip()
            if not game_id or not game_date or not external_id or not display_name:
                stats["skipped_missing_identity"] += 1
                continue
            minutes = parse_minutes(field(row, "numMinutes", "minutes"))
            if minutes <= 0:
                stats["skipped_no_minutes"] += 1
                continue

            player_id = f"nba:{external_id}"
            appearances.append(
                Appearance(
                    game_id=game_id,
                    season=season,
                    game_date=game_date,
                    team_id=team_id,
                    player_id=player_id,
                    external_id=external_id,
                    display_name=display_name,
                    minutes=minutes,
                )
            )
            game = games.setdefault(
                game_id,
                {
                    "season": season,
                    "game_date": game_date,
                    "source_rows": 0,
                    "appearance_rows": 0,
                    "teams": set(),
                },
            )
            game["source_rows"] = int(game["source_rows"]) + 1
            game["appearance_rows"] = int(game["appearance_rows"]) + 1
            game["teams"].add(team_id)

    stats["appearances"] = len(appearances)
    stats["games"] = len(games)
    return appearances, games, dict(stats)


def init_db(conn: sqlite3.Connection, rebuild: bool) -> None:
    if rebuild:
        conn.executescript("""
        DROP TABLE IF EXISTS nba_teammate_game_proofs;
        DROP TABLE IF EXISTS nba_player_game_appearances;
        DROP TABLE IF EXISTS nba_games;
        DROP TABLE IF EXISTS nba_build_meta;
        """)
    conn.executescript(SCHEMA)


def store_appearances(
    conn: sqlite3.Connection,
    appearances: list[Appearance],
    games: dict[str, dict[str, object]],
) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO nba_games
          (game_id, season, game_date, source, source_rows, appearance_rows, teams_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                game_id,
                int(game["season"]),
                str(game["game_date"]),
                SOURCE_NAME,
                int(game["source_rows"]),
                int(game["appearance_rows"]),
                len(game["teams"]),
            )
            for game_id, game in games.items()
        ],
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO nba_player_game_appearances
          (game_id, season, game_date, team_id, player_id, external_id, display_name, minutes, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                app.game_id,
                app.season,
                app.game_date,
                app.team_id,
                app.player_id,
                app.external_id,
                app.display_name,
                app.minutes,
                SOURCE_NAME,
            )
            for app in appearances
        ],
    )


def rebuild_proofs(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM nba_teammate_game_proofs")
    conn.execute(
        """
        INSERT INTO nba_teammate_game_proofs
          (player_a_id, player_b_id, team_id, season, shared_games,
           first_game_id, first_game_date, last_game_id, last_game_date)
        SELECT
          CASE WHEN a.player_id < b.player_id THEN a.player_id ELSE b.player_id END AS player_a_id,
          CASE WHEN a.player_id < b.player_id THEN b.player_id ELSE a.player_id END AS player_b_id,
          a.team_id,
          a.season,
          COUNT(*) AS shared_games,
          SUBSTR(MIN(a.game_date || '|' || a.game_id), 12) AS first_game_id,
          SUBSTR(MIN(a.game_date || '|' || a.game_id), 1, 10) AS first_game_date,
          SUBSTR(MAX(a.game_date || '|' || a.game_id), 12) AS last_game_id,
          SUBSTR(MAX(a.game_date || '|' || a.game_id), 1, 10) AS last_game_date
        FROM nba_player_game_appearances a
        JOIN nba_player_game_appearances b
          ON b.game_id = a.game_id
         AND b.team_id = a.team_id
         AND b.player_id > a.player_id
        GROUP BY
          CASE WHEN a.player_id < b.player_id THEN a.player_id ELSE b.player_id END,
          CASE WHEN a.player_id < b.player_id THEN b.player_id ELSE a.player_id END,
          a.team_id,
          a.season
        """
    )
    return int(conn.execute("SELECT COUNT(*) FROM nba_teammate_game_proofs").fetchone()[0])


def summarize(conn: sqlite3.Connection) -> None:
    games = {
        season: count
        for season, count in conn.execute(
            "SELECT season, COUNT(*) FROM nba_games GROUP BY season"
        )
    }
    appearances = {
        season: count
        for season, count in conn.execute(
            "SELECT season, COUNT(*) FROM nba_player_game_appearances GROUP BY season"
        )
    }
    proofs = {
        season: count
        for season, count in conn.execute(
            "SELECT season, COUNT(*) FROM nba_teammate_game_proofs GROUP BY season"
        )
    }
    print("season games appearances proofs", flush=True)
    for season in sorted(set(games) | set(appearances) | set(proofs)):
        print(
            f"{season} {games.get(season, 0):,} "
            f"{appearances.get(season, 0):,} {proofs.get(season, 0):,}",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--season-start", type=int, default=2000)
    parser.add_argument("--season-end", type=int, default=2025)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"NBA source file not found: {args.source}")
    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    try:
        if args.summary_only:
            summarize(conn)
            return 0
        init_db(conn, args.rebuild)
        print(
            f"building NBA game proofs from {args.source} for seasons "
            f"{args.season_start}-{args.season_end}",
            flush=True,
        )
        appearances, games, stats = iter_appearances(args.source, args.season_start, args.season_end)
        print(
            "parsed "
            f"{stats.get('source_rows', 0):,} source rows; "
            f"{len(games):,} regular-season games; "
            f"{len(appearances):,} positive-minute appearances",
            flush=True,
        )
        for key in sorted(k for k in stats if k.startswith("skipped_")):
            print(f"{key}: {stats[key]:,}", flush=True)
        with conn:
            store_appearances(conn, appearances, games)
            proofs = rebuild_proofs(conn)
            conn.execute(
                "INSERT OR REPLACE INTO nba_build_meta(key, value) VALUES (?, ?)",
                ("last_build_utc", datetime.now(UTC).isoformat(timespec="seconds")),
            )
            conn.execute(
                "INSERT OR REPLACE INTO nba_build_meta(key, value) VALUES (?, ?)",
                ("source", str(args.source)),
            )
        print(f"proof build complete: {proofs:,} teammate/team-season proof rows", flush=True)
        summarize(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
