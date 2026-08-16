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
import argparse
import json
import os
import sqlite3
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

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
BASEBALL_DB = ROOT / "db" / "base2nerdle.sqlite"
NBA_STATS = ROOT / "raw" / "nba_kaggle" / "PlayerStatistics.csv"
NFL_WEEKLY = ROOT / "raw" / "nfl" / "weekly_rosters"
MLB_CACHE = ROOT / "raw" / "mlb_statsapi"
NHL_CACHE = ROOT / "raw" / "nhl_gamecenter"
STRICT_SPORTS = ("basketball", "football", "hockey")
NFL_EXCLUDED_STATUSES = {"CUT", "TRD", "TRT", "RET", "UFA", "RFA"}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TeamMateTag/0.2.14 teammate-stint-builder"})
MAX_WORKERS = int(os.environ.get("TEAMMATETAG_STINT_WORKERS", "16"))


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

BASEBALL_SCHEMA = """
CREATE TABLE IF NOT EXISTS player_stints (
  player_id TEXT NOT NULL, team_id TEXT NOT NULL, season INTEGER NOT NULL,
  first_unit INTEGER NOT NULL, last_unit INTEGER NOT NULL,
  first_label TEXT, last_label TEXT, source TEXT,
  PRIMARY KEY (player_id, team_id, season)
);
CREATE INDEX IF NOT EXISTS idx_player_stints_link
  ON player_stints(team_id, season, player_id);
CREATE TABLE IF NOT EXISTS teammate_stint_coverage (
  season INTEGER PRIMARY KEY, coverage_type TEXT NOT NULL,
  strict INTEGER NOT NULL DEFAULT 1, source TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS teammate_exclusions (
  player_a_id TEXT NOT NULL, player_b_id TEXT NOT NULL,
  team_id TEXT NOT NULL, season INTEGER NOT NULL, reason TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (player_a_id, player_b_id, team_id, season)
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


def get_json(url: str, cache_path: Path, sleep_seconds: float = 0.03) -> dict:
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


def build_nhl_stints(valid: set[tuple[str, str, str, int]]) -> tuple[list[tuple], dict[int, int]]:
    stints: dict[tuple[str, str, str, int], list] = {}
    coverage = defaultdict(int)
    by_season: dict[int, set[str]] = defaultdict(set)
    for sport, _player, team, season in valid:
        if sport == "hockey" and 2000 <= season <= 2025:
            by_season[season].add(team)
    games: dict[int, tuple[int, str, int]] = {}
    for season, teams in sorted(by_season.items()):
        season_id = f"{season}{season + 1}"
        for team in sorted(teams):
            schedule_url = f"https://api-web.nhle.com/v1/club-schedule-season/{team}/{season_id}"
            schedule_path = NHL_CACHE / "schedules" / f"{team}_{season_id}.json"
            try:
                schedule = get_json(schedule_url, schedule_path)
            except Exception:
                continue
            for game in schedule.get("games", []):
                if game.get("gameType") != 2 or game.get("gameState") not in {"FINAL", "OFF"}:
                    continue
                game_id = game.get("id")
                game_date = game.get("gameDate")
                unit = date_unit(game_date or "")
                if not game_id or not unit:
                    continue
                games[int(game_id)] = (season, game_date, unit)
    def fetch_box(game_id: int) -> tuple[int, dict | None]:
        box_url = f"https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"
        box_path = NHL_CACHE / "boxscores" / f"{game_id}.json"
        try:
            return game_id, get_json(box_url, box_path, sleep_seconds=0)
        except Exception:
            return game_id, None
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for future in as_completed([pool.submit(fetch_box, game_id) for game_id in games]):
            game_id, box = future.result()
            if not box:
                continue
            season, game_date, unit = games[game_id]
            stats = box.get("playerByGameStats") or {}
            for side in ("awayTeam", "homeTeam"):
                team_id = (box.get(side) or {}).get("abbrev")
                if not team_id:
                    continue
                for group in ("forwards", "defense", "defensemen", "goalies"):
                    for player in (stats.get(side) or {}).get(group, []):
                        nhl_id = player.get("playerId")
                        if not nhl_id:
                            continue
                        key = ("hockey", f"nhl:{nhl_id}", team_id, season)
                        if key not in valid:
                            continue
                        update_stint(stints, key, unit, game_date, "nhl_gamecenter_boxscore")
                        coverage[season] += 1
    return [(*key, *values) for key, values in stints.items()], dict(coverage)


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
                        pass
    return mapping


def norm_name(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def load_mlb_team_maps(conn: sqlite3.Connection) -> tuple[dict[tuple[int, str], str], dict[tuple[int, str], str]]:
    by_name: dict[tuple[int, str], str] = {}
    by_abbr: dict[tuple[int, str], str] = {}
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
    rows = conn.execute("SELECT team_id, season, name FROM teams WHERE season >= 2000").fetchall()
    for team_id, season, name in rows:
        by_name[(season, norm_name(name))] = team_id
        for abbr, mapped in manual_abbr.items():
            if mapped == team_id:
                by_abbr[(season, abbr)] = team_id
    return by_name, by_abbr


def mlb_player_appeared(player: dict) -> bool:
    stats = player.get("stats") or {}
    if any(stats.get(group) for group in ("batting", "pitching", "fielding")):
        return True
    return bool(player.get("allPositions")) and player.get("gameStatus") is not None


def build_mlb_stints(conn: sqlite3.Connection) -> tuple[list[tuple], dict[int, int]]:
    valid_rows = conn.execute(
        "SELECT player_id, team_id, season FROM appearances WHERE season >= 2000"
    ).fetchall()
    valid = set(valid_rows)
    valid_players = {row[0] for row in valid_rows}
    mlbam_to_player = {
        int(mlbam): player_id
        for player_id, mlbam in conn.execute("SELECT player_id, mlbam_id FROM players WHERE mlbam_id IS NOT NULL")
        if mlbam
    }
    mlbam_to_player.update(load_chadwick_mlbam_map(valid_players))
    by_name, by_abbr = load_mlb_team_maps(conn)
    stints: dict[tuple[str, str, int], list] = {}
    coverage = defaultdict(int)
    games: dict[int, tuple[int, str]] = {}
    for season in range(2000, 2026):
        schedule_url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&season={season}&gameTypes=R"
        schedule_path = MLB_CACHE / "schedules" / f"{season}.json"
        schedule = get_json(schedule_url, schedule_path)
        for day in schedule.get("dates", []):
            for game in day.get("games", []):
                if game.get("gameType") != "R" or (game.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                game_pk = game.get("gamePk")
                game_date = game.get("officialDate") or game.get("gameDate")
                unit = date_unit(game_date or "")
                if not game_pk or not unit:
                    continue
                games[int(game_pk)] = (season, game_date[:10])
    def fetch_box(game_pk: int) -> tuple[int, dict | None]:
        box_url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
        box_path = MLB_CACHE / "boxscores" / f"{game_pk}.json"
        try:
            return game_pk, get_json(box_url, box_path, sleep_seconds=0)
        except Exception:
            return game_pk, None
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for future in as_completed([pool.submit(fetch_box, game_pk) for game_pk in games]):
            game_pk, box = future.result()
            if not box:
                continue
            season, game_date = games[game_pk]
            unit = date_unit(game_date)
            if not unit:
                continue
            for side in ("away", "home"):
                team = ((box.get("teams") or {}).get(side) or {}).get("team") or {}
                team_name = team.get("name", "")
                abbr = team.get("abbreviation", "")
                team_id = by_name.get((season, norm_name(team_name))) or by_abbr.get((season, abbr))
                if not team_id:
                    continue
                for player in (((box.get("teams") or {}).get(side) or {}).get("players") or {}).values():
                    person = player.get("person") or {}
                    player_id = mlbam_to_player.get(person.get("id"))
                    if not player_id or not mlb_player_appeared(player):
                        continue
                    key = (player_id, team_id, season)
                    if key not in valid:
                        continue
                    update_stint(stints, key, unit, game_date, "mlb_statsapi_boxscore")
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


def write_baseball_sqlite(conn: sqlite3.Connection, rows: list[tuple], coverage: dict[int, int]) -> None:
    conn.executescript(BASEBALL_SCHEMA)
    conn.executemany(
        """INSERT OR REPLACE INTO player_stints
           (player_id, team_id, season, first_unit, last_unit, first_label, last_label, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.executemany(
        """INSERT OR REPLACE INTO teammate_stint_coverage
           (season, coverage_type, strict, source)
           VALUES (?, 'stint_range', 1, 'baseball_stint_ranges')""",
        [(season,) for season in coverage],
    )
    conn.commit()


def write_postgres(
    rows: list[tuple],
    coverage: dict[str, dict[int, int]],
    baseball_rows: list[tuple],
    baseball_coverage: dict[int, int],
    selected_cross_sports: set[str],
    include_baseball: bool,
) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url or psycopg is None:
        return
    with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute((ROOT / "db" / "cross_sport_schema_postgres.sql").read_text(encoding="utf-8"))
            cur.execute((ROOT / "db" / "schema_postgres.sql").read_text(encoding="utf-8"))
            if include_baseball:
                cur.execute("DELETE FROM player_stints")
                cur.execute("DELETE FROM teammate_stint_coverage")
                with cur.copy(
                    "COPY player_stints "
                    "(player_id, team_id, season, first_unit, last_unit, first_label, last_label, source) FROM STDIN"
                ) as copy:
                    for row in baseball_rows:
                        copy.write_row(row)
                with cur.copy(
                    "COPY teammate_stint_coverage "
                    "(season, coverage_type, strict, source) FROM STDIN"
                ) as copy:
                    for season in baseball_coverage:
                        copy.write_row((season, "stint_range", 1, "baseball_stint_ranges"))
            if selected_cross_sports:
                cur.execute("DELETE FROM sport_player_stints WHERE sport_id = ANY(%s)", (list(selected_cross_sports),))
                cur.execute("DELETE FROM sport_teammate_stint_coverage WHERE sport_id = ANY(%s)", (list(selected_cross_sports),))
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
            if coverage_rows:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sports",
        nargs="+",
        choices=("baseball", "basketball", "football", "hockey", "all"),
        default=["all"],
        help="Sports to rebuild. Default: all.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = set(args.sports)
    if "all" in selected:
        selected = {"baseball", "basketball", "football", "hockey"}
    selected_cross = selected & set(STRICT_SPORTS)
    if not LOCAL_DB.exists():
        raise SystemExit(f"Missing local database: {LOCAL_DB}")
    rows: list[tuple] = []
    coverage: dict[str, dict[int, int]] = {}
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.executescript(SCHEMA)
        if "basketball" in selected_cross:
            nba_rows, nba_coverage = build_nba_stints(local_valid_appearances(conn, "basketball"))
            rows.extend(nba_rows)
            coverage["basketball"] = nba_coverage
        if "football" in selected_cross:
            nfl_rows, nfl_coverage = build_nfl_stints(local_valid_appearances(conn, "football"))
            rows.extend(nfl_rows)
            coverage["football"] = nfl_coverage
        if "hockey" in selected_cross:
            nhl_rows, nhl_coverage = build_nhl_stints(local_valid_appearances(conn, "hockey"))
            rows.extend(nhl_rows)
            coverage["hockey"] = nhl_coverage
        if selected_cross:
            write_sqlite(conn, rows, coverage)
    baseball_rows: list[tuple] = []
    baseball_coverage: dict[int, int] = {}
    if "baseball" in selected and BASEBALL_DB.exists():
        with sqlite3.connect(BASEBALL_DB) as conn:
            baseball_rows, baseball_coverage = build_mlb_stints(conn)
            write_baseball_sqlite(conn, baseball_rows, baseball_coverage)
    write_postgres(rows, coverage, baseball_rows, baseball_coverage, selected_cross, "baseball" in selected)
    if "baseball" in selected:
        print(f"baseball: {len(baseball_rows):,} stints, {len(baseball_coverage)} strict seasons")
    for sport, seasons in coverage.items():
        print(f"{sport}: {sum(1 for row in rows if row[0] == sport):,} stints, {len(seasons)} strict seasons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
