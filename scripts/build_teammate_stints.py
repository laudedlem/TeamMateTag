#!/usr/bin/env python3
"""Build compact teammate-overlap stints for stricter cross-sport links.

The base sport_appearances table says "this player appeared for this team in
this season." For leagues with mid-season movement, that is not enough: two
players can share a team-season label without actually overlapping.

This script stores one compact row per player/team/season with first/last
appearance units. Runtime validation then accepts same-team links only when the
two stint ranges overlap for sport/seasons marked strict.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    import psycopg
except ImportError:  # local-only use still works
    psycopg = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
LOCAL_DB = ROOT / "db" / "teammatetag_local.sqlite"
NBA_STATS = ROOT / "raw" / "nba_kaggle" / "PlayerStatistics.csv"
NFL_WEEKLY = ROOT / "raw" / "nfl" / "weekly_rosters"
STRICT_SPORTS = ("basketball", "football")
NFL_EXCLUDED_STATUSES = {"CUT", "TRD", "TRT", "RET", "UFA", "RFA"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS sport_player_stints (
  sport_id TEXT NOT NULL, player_id TEXT NOT NULL, team_id TEXT NOT NULL,
  season INTEGER NOT NULL, first_unit INTEGER NOT NULL, last_unit INTEGER NOT NULL,
  first_label TEXT, last_label TEXT, source TEXT,
  PRIMARY KEY (sport_id, player_id, team_id, season)
);
CREATE INDEX IF NOT EXISTS idx_sport_stints_link
  ON sport_player_stints(sport_id, team_id, season, player_id);
CREATE TABLE IF NOT EXISTS sport_teammate_stint_coverage (
  sport_id TEXT NOT NULL, season INTEGER NOT NULL, coverage_type TEXT NOT NULL,
  strict INTEGER NOT NULL DEFAULT 1, source TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sport_id, season)
);
CREATE TABLE IF NOT EXISTS sport_teammate_exclusions (
  sport_id TEXT NOT NULL, player_a_id TEXT NOT NULL, player_b_id TEXT NOT NULL,
  team_id TEXT NOT NULL, season INTEGER NOT NULL, reason TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season)
);
"""


def field(row: dict, *names: str) -> str:
    lowered = {name.lower(): value for name, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def update_stint(stints: dict, key: tuple[str, str, str, int], unit: int, label: str, source: str) -> None:
    current = stints.get(key)
    if current is None:
        stints[key] = [unit, unit, label, label, source]
        return
    if unit < current[0]:
        current[0] = unit
        current[2] = label
    if unit > current[1]:
        current[1] = unit
        current[3] = label


def nba_season_from_game_id(game_id: str, fallback_date: str) -> int | None:
    digits = "".join(ch for ch in str(game_id or "") if ch.isdigit())
    if len(digits) >= 5:
        padded = digits.zfill(10)
        yy = int(padded[3:5])
        return 1900 + yy if yy >= 47 else 2000 + yy
    if fallback_date:
        year = int(fallback_date[:4])
        month = int(fallback_date[5:7])
        return year - 1 if month <= 6 else year
    return None


def date_unit(value: str) -> int | None:
    if not value:
        return None
    cleaned = value[:10]
    try:
        return int(datetime.strptime(cleaned, "%Y-%m-%d").strftime("%Y%m%d"))
    except ValueError:
        return None


def build_nba_stints(valid: set[tuple[str, str, str, int]]) -> tuple[list[tuple], dict[int, int]]:
    stints: dict[tuple[str, str, str, int], list] = {}
    coverage = defaultdict(int)
    if not NBA_STATS.exists():
        return [], {}
    with NBA_STATS.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            person_id = field(row, "personId", "playerId")
            team_id = field(row, "playerteamId", "teamId")
            game_id = field(row, "gameId")
            game_date = field(row, "gameDateTimeEst", "gameDate")
            if not person_id or not team_id or not game_date:
                continue
            season = nba_season_from_game_id(game_id, game_date)
            unit = date_unit(game_date)
            player_id = f"nba:{person_id}"
            key = ("basketball", player_id, team_id, season)
            if not season or not unit or key not in valid:
                continue
            update_stint(stints, key, unit, game_date[:10], "nba_kaggle_player_statistics_game_id")
            coverage[season] += 1
    return [(*key, *values) for key, values in stints.items()], dict(coverage)


def build_nfl_stints(valid: set[tuple[str, str, str, int]]) -> tuple[list[tuple], dict[int, int]]:
    stints: dict[tuple[str, str, str, int], list] = {}
    coverage = defaultdict(int)
    if not NFL_WEEKLY.exists():
        return [], {}
    for path in sorted(NFL_WEEKLY.glob("roster_weekly_*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                status = field(row, "status")
                gsis_id = field(row, "gsis_id")
                team_id = field(row, "team")
                season_text = field(row, "season")
                week_text = field(row, "week")
                if status in NFL_EXCLUDED_STATUSES or not gsis_id or not team_id or not season_text or not week_text:
                    continue
                try:
                    season = int(season_text)
                    week = int(float(week_text))
                except ValueError:
                    continue
                player_id = f"nfl:{gsis_id}"
                key = ("football", player_id, team_id, season)
                if key not in valid:
                    continue
                label = f"Week {week}"
                update_stint(stints, key, week, label, "nflverse_weekly_rosters")
                coverage[season] += 1
    return [(*key, *values) for key, values in stints.items()], dict(coverage)


def local_valid_appearances(conn: sqlite3.Connection, sport: str) -> set[tuple[str, str, str, int]]:
    return set(conn.execute(
        """SELECT sport_id, player_id, team_id, season
             FROM sport_appearances
            WHERE sport_id = ? AND season >= 2000""",
        (sport,),
    ).fetchall())


def write_sqlite(conn: sqlite3.Connection, rows: list[tuple], coverage: dict[str, dict[int, int]]) -> None:
    conn.executescript(SCHEMA)
    conn.executemany(
        """INSERT OR REPLACE INTO sport_player_stints
           (sport_id, player_id, team_id, season, first_unit, last_unit, first_label, last_label, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    for sport, seasons in coverage.items():
        conn.executemany(
            """INSERT OR REPLACE INTO sport_teammate_stint_coverage
               (sport_id, season, coverage_type, strict, source)
               VALUES (?, ?, ?, 1, ?)""",
            [(sport, season, "stint_range", f"{sport}_stint_ranges") for season in seasons],
        )
    conn.execute(
        """INSERT OR IGNORE INTO sport_teammate_exclusions
           (sport_id, player_a_id, player_b_id, team_id, season, reason)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            "basketball", "nba:202954", "nba:201952", "1610612738", 2020,
            "Brad Wanamaker left Boston before Jeff Teague joined; kept as a manual guardrail.",
        ),
    )
    conn.commit()


def write_postgres(rows: list[tuple], coverage: dict[str, dict[int, int]]) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url or psycopg is None:
        return
    with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute((ROOT / "db" / "cross_sport_schema_postgres.sql").read_text(encoding="utf-8"))
            cur.execute("DELETE FROM sport_player_stints WHERE sport_id = ANY(%s)", (list(STRICT_SPORTS),))
            cur.execute("DELETE FROM sport_teammate_stint_coverage WHERE sport_id = ANY(%s)", (list(STRICT_SPORTS),))
            with cur.copy(
                "COPY sport_player_stints "
                "(sport_id, player_id, team_id, season, first_unit, last_unit, first_label, last_label, source) FROM STDIN"
            ) as copy:
                for row in rows:
                    copy.write_row(row)
            coverage_rows = [
                (sport, season, "stint_range", 1, f"{sport}_stint_ranges")
                for sport, seasons in coverage.items()
                for season in seasons
            ]
            with cur.copy(
                "COPY sport_teammate_stint_coverage "
                "(sport_id, season, coverage_type, strict, source) FROM STDIN"
            ) as copy:
                for row in coverage_rows:
                    copy.write_row(row)
            cur.execute(
                """INSERT INTO sport_teammate_exclusions
                   (sport_id, player_a_id, player_b_id, team_id, season, reason)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (
                    "basketball", "nba:202954", "nba:201952", "1610612738", 2020,
                    "Brad Wanamaker left Boston before Jeff Teague joined; kept as a manual guardrail.",
                ),
            )
        conn.commit()


def main() -> int:
    if not LOCAL_DB.exists():
        raise SystemExit(f"Missing local database: {LOCAL_DB}")
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.executescript(SCHEMA)
        basketball_valid = local_valid_appearances(conn, "basketball")
        football_valid = local_valid_appearances(conn, "football")
        nba_rows, nba_coverage = build_nba_stints(basketball_valid)
        nfl_rows, nfl_coverage = build_nfl_stints(football_valid)
        rows = nba_rows + nfl_rows
        coverage = {"basketball": nba_coverage, "football": nfl_coverage}
        write_sqlite(conn, rows, coverage)
    write_postgres(rows, coverage)
    for sport, seasons in coverage.items():
        print(f"{sport}: {sum(1 for row in rows if row[0] == sport):,} stints, {len(seasons)} strict seasons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
