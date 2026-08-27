#!/usr/bin/env python3
"""Build NBA teammate proofs from SportsDataverse/ESPN player boxscores.

This is the preferred Basketball historical source because the local ESPN CSVs
have complete recent regular-season game counts. ESPN athlete ids are mapped to
TeamMateTag's current NBA person ids via raw/nba_identity/espn_to_nba_crosswalk_auto.csv.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = ROOT / "raw" / "nba"
DEFAULT_CROSSWALK = ROOT / "raw" / "nba_identity" / "espn_to_nba_crosswalk_auto.csv"
DEFAULT_DB = ROOT / "raw" / "nba_game_teammates" / "nba_espn_game_teammates.sqlite"
SOURCE_NAME = "sportsdataverse_espn_nba_player_boxscores"

ESPN_TO_NBA_TEAM = {
    "1": "1610612737", "2": "1610612738", "3": "1610612740",
    "4": "1610612741", "5": "1610612739", "6": "1610612742",
    "7": "1610612743", "8": "1610612765", "9": "1610612744",
    "10": "1610612745", "11": "1610612754", "12": "1610612746",
    "13": "1610612747", "14": "1610612748", "15": "1610612749",
    "16": "1610612750", "17": "1610612751", "18": "1610612752",
    "19": "1610612753", "20": "1610612755", "21": "1610612756",
    "22": "1610612757", "23": "1610612758", "24": "1610612759",
    "25": "1610612760", "26": "1610612762", "27": "1610612764",
    "28": "1610612761", "29": "1610612763", "30": "1610612766",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS nba_games (
  game_id TEXT PRIMARY KEY,
  season INTEGER NOT NULL,
  game_date TEXT NOT NULL,
  source TEXT NOT NULL,
  teams_seen INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_nba_espn_games_season ON nba_games(season);

CREATE TABLE IF NOT EXISTS nba_player_game_appearances (
  game_id TEXT NOT NULL,
  season INTEGER NOT NULL,
  game_date TEXT NOT NULL,
  team_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  espn_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  minutes REAL NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (game_id, team_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_nba_espn_appearances_player
  ON nba_player_game_appearances(player_id, season, team_id);
CREATE INDEX IF NOT EXISTS idx_nba_espn_appearances_game_team
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
CREATE INDEX IF NOT EXISTS idx_nba_espn_teammate_proofs_a
  ON nba_teammate_game_proofs(player_a_id);
CREATE INDEX IF NOT EXISTS idx_nba_espn_teammate_proofs_b
  ON nba_teammate_game_proofs(player_b_id);
CREATE INDEX IF NOT EXISTS idx_nba_espn_teammate_proofs_team
  ON nba_teammate_game_proofs(team_id, season);

CREATE TABLE IF NOT EXISTS nba_build_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def parse_minutes(value: str) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def load_crosswalk(path: Path) -> dict[str, tuple[str, str]]:
    mapping: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            espn_id = (row.get("espn_id") or "").strip()
            nba_person_id = (row.get("nba_person_id") or "").strip()
            name = (row.get("production_name") or row.get("espn_name") or "").strip()
            if espn_id and nba_person_id:
                mapping[espn_id] = (f"nba:{nba_person_id}", nba_person_id)
    return mapping


def init_db(conn: sqlite3.Connection, rebuild: bool) -> None:
    if rebuild:
        conn.executescript("""
        DROP TABLE IF EXISTS nba_teammate_game_proofs;
        DROP TABLE IF EXISTS nba_player_game_appearances;
        DROP TABLE IF EXISTS nba_games;
        DROP TABLE IF EXISTS nba_build_meta;
        """)
    conn.executescript(SCHEMA)


def import_rows(
    conn: sqlite3.Connection,
    source_dir: Path,
    crosswalk: dict[str, tuple[str, str]],
    season_start: int,
    season_end: int,
) -> Counter:
    stats: Counter = Counter()
    game_teams: dict[str, set[str]] = defaultdict(set)
    game_info: dict[str, tuple[int, str]] = {}
    appearance_rows = []
    skipped_unmapped: Counter[str] = Counter()

    for path in sorted(source_dir.glob("player_box_*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                stats["source_rows"] += 1
                if row.get("season_type") != "2":
                    stats["skipped_non_regular"] += 1
                    continue
                try:
                    season = int(row.get("season") or "")
                except ValueError:
                    stats["skipped_bad_season"] += 1
                    continue
                if season < season_start or season > season_end:
                    stats["skipped_out_of_range"] += 1
                    continue
                team_id = ESPN_TO_NBA_TEAM.get((row.get("team_id") or "").strip())
                if not team_id:
                    stats["skipped_unmapped_team"] += 1
                    continue
                minutes = parse_minutes(row.get("minutes", ""))
                if minutes <= 0 or row.get("did_not_play") == "true":
                    stats["skipped_no_minutes"] += 1
                    continue
                espn_id = (row.get("athlete_id") or "").strip()
                mapped = crosswalk.get(espn_id)
                if not mapped:
                    stats["skipped_unmapped_player"] += 1
                    if len(skipped_unmapped) < 5000:
                        skipped_unmapped[f"{espn_id}|{row.get('athlete_display_name') or ''}"] += 1
                    continue
                player_id, external_id = mapped
                game_id = (row.get("game_id") or "").strip()
                game_date = (row.get("game_date") or row.get("game_date_time") or "")[:10]
                display_name = (row.get("athlete_display_name") or "").strip()
                if not game_id or not game_date or not display_name:
                    stats["skipped_missing_required"] += 1
                    continue
                game_info[game_id] = (season, game_date)
                game_teams[game_id].add(team_id)
                appearance_rows.append((
                    game_id, season, game_date, team_id, player_id, external_id,
                    espn_id, display_name, minutes, SOURCE_NAME,
                ))

    conn.executemany(
        """
        INSERT OR REPLACE INTO nba_games(game_id, season, game_date, source, teams_seen)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (game_id, season, game_date, SOURCE_NAME, len(game_teams[game_id]))
            for game_id, (season, game_date) in game_info.items()
        ],
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO nba_player_game_appearances
          (game_id, season, game_date, team_id, player_id, external_id, espn_id,
           display_name, minutes, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        appearance_rows,
    )
    stats["games"] = len(game_info)
    stats["appearances"] = len(appearance_rows)

    skipped_path = DEFAULT_DB.parent / "nba_espn_unmapped_players.csv"
    with skipped_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["espn_id", "display_name", "played_rows"])
        writer.writeheader()
        for key, count in skipped_unmapped.most_common():
            espn_id, name = key.split("|", 1)
            writer.writerow({"espn_id": espn_id, "display_name": name, "played_rows": count})
    stats["unmapped_player_file"] = str(skipped_path)
    return stats


def rebuild_proofs(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM nba_teammate_game_proofs")
    conn.execute(
        """
        INSERT INTO nba_teammate_game_proofs
          (player_a_id, player_b_id, team_id, season, shared_games,
           first_game_id, first_game_date, last_game_id, last_game_date)
        SELECT
          a.player_id,
          b.player_id,
          a.team_id,
          a.season,
          COUNT(*),
          SUBSTR(MIN(a.game_date || '|' || a.game_id), 12),
          SUBSTR(MIN(a.game_date || '|' || a.game_id), 1, 10),
          SUBSTR(MAX(a.game_date || '|' || a.game_id), 12),
          SUBSTR(MAX(a.game_date || '|' || a.game_id), 1, 10)
        FROM nba_player_game_appearances a
        JOIN nba_player_game_appearances b
          ON b.game_id = a.game_id
         AND b.team_id = a.team_id
         AND b.player_id > a.player_id
        GROUP BY a.player_id, b.player_id, a.team_id, a.season
        """
    )
    return int(conn.execute("SELECT COUNT(*) FROM nba_teammate_game_proofs").fetchone()[0])


def summarize(conn: sqlite3.Connection) -> None:
    games = dict(conn.execute("SELECT season, COUNT(*) FROM nba_games GROUP BY season"))
    apps = dict(conn.execute("SELECT season, COUNT(*) FROM nba_player_game_appearances GROUP BY season"))
    proofs = dict(conn.execute("SELECT season, COUNT(*) FROM nba_teammate_game_proofs GROUP BY season"))
    print("season games appearances proofs", flush=True)
    for season in sorted(set(games) | set(apps) | set(proofs)):
        print(
            f"{season} {games.get(season, 0):,} {apps.get(season, 0):,} {proofs.get(season, 0):,}",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--crosswalk", type=Path, default=DEFAULT_CROSSWALK)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--season-start", type=int, default=2002)
    parser.add_argument("--season-end", type=int, default=2025)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    try:
        if args.summary_only:
            summarize(conn)
            return 0
        if not args.crosswalk.exists():
            raise SystemExit(f"Crosswalk not found: {args.crosswalk}")
        crosswalk = load_crosswalk(args.crosswalk)
        init_db(conn, args.rebuild)
        print(f"loaded {len(crosswalk):,} ESPN->NBA identity mappings", flush=True)
        print(f"building Basketball proofs from {args.source_dir}", flush=True)
        with conn:
            stats = import_rows(conn, args.source_dir, crosswalk, args.season_start, args.season_end)
            proofs = rebuild_proofs(conn)
            conn.execute(
                "INSERT OR REPLACE INTO nba_build_meta(key, value) VALUES (?, ?)",
                ("last_build_utc", datetime.now(UTC).isoformat(timespec="seconds")),
            )
            conn.execute(
                "INSERT OR REPLACE INTO nba_build_meta(key, value) VALUES (?, ?)",
                ("source", SOURCE_NAME),
            )
        for key in sorted(stats):
            print(f"{key}: {stats[key]}", flush=True)
        print(f"proof build complete: {proofs:,} teammate/team-season proof rows", flush=True)
        summarize(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
