"""Build the complete local multi-sport TeamMateTag dataset.

The output is db/teammatetag_local.sqlite. It is not deployed and is ignored
by Git. Data is stored as indexed player-team-season appearances so game logic
can resolve teammates on demand without materializing every player pair.

Scopes:
  baseball:   1871-2025, Lahman CSVs already in raw/
  football:   1966-2025, nflverse roster cache in raw/nfl/
  basketball: 2002-2025, SportsDataverse ESPN NBA player box scores
  hockey:     NHL API roster history, 1917-2025 where exposed by the API

Run: python scripts/build_local_sports_dataset.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "db" / "teammatetag_local.sqlite"
NBA_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_player_boxscores/player_box_{season}.csv"
NHL_API = "https://api-web.nhle.com/v1"
NHL_CODES = [
    "ANA", "ARI", "ATL", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "CLE",
    "COL", "DAL", "DET", "EDM", "FLA", "HFD", "KCS", "LAK", "MDA", "MIN",
    "MNS", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT", "PHI", "PHX", "PIT",
    "QUE", "SJS", "SEA", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WIN", "WPG", "WSH",
]


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS dataset_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sports (
  sport_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, league_name TEXT NOT NULL,
  first_season INTEGER, last_season INTEGER
);
CREATE TABLE IF NOT EXISTS sport_franchises (
  sport_id TEXT NOT NULL, franchise_id TEXT NOT NULL, name TEXT NOT NULL,
  PRIMARY KEY (sport_id, franchise_id)
);
CREATE TABLE IF NOT EXISTS sport_teams (
  sport_id TEXT NOT NULL, team_id TEXT NOT NULL, season INTEGER NOT NULL,
  franchise_id TEXT NOT NULL, name TEXT NOT NULL,
  PRIMARY KEY (sport_id, team_id, season)
);
CREATE TABLE IF NOT EXISTS sport_players (
  sport_id TEXT NOT NULL, player_id TEXT NOT NULL, external_id TEXT,
  display_name TEXT NOT NULL, first_name TEXT, last_name TEXT, birth_year INTEGER,
  debut_year INTEGER, final_year INTEGER, primary_pos TEXT,
  PRIMARY KEY (sport_id, player_id)
);
CREATE TABLE IF NOT EXISTS sport_appearances (
  sport_id TEXT NOT NULL, player_id TEXT NOT NULL, team_id TEXT NOT NULL,
  season INTEGER NOT NULL, games_total INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (sport_id, player_id, team_id, season)
);
CREATE TABLE IF NOT EXISTS sport_player_positions (
  sport_id TEXT NOT NULL, player_id TEXT NOT NULL, position TEXT NOT NULL,
  games INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (sport_id, player_id, position)
);
CREATE INDEX IF NOT EXISTS idx_local_player_positions
  ON sport_player_positions(sport_id, position, player_id);
CREATE INDEX IF NOT EXISTS idx_local_appearances_team
  ON sport_appearances(sport_id, team_id, season, player_id);
CREATE INDEX IF NOT EXISTS idx_local_appearances_player
  ON sport_appearances(sport_id, player_id, season);
CREATE TABLE IF NOT EXISTS sport_players_searchable (
  sport_id TEXT NOT NULL, player_id TEXT NOT NULL, display_name TEXT NOT NULL,
  disambiguation TEXT NOT NULL, search_key TEXT NOT NULL, last_key TEXT NOT NULL,
  career_games INTEGER NOT NULL, teammate_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (sport_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_local_search_name
  ON sport_players_searchable(sport_id, search_key);
CREATE INDEX IF NOT EXISTS idx_local_search_last
  ON sport_players_searchable(sport_id, last_key);
CREATE TABLE IF NOT EXISTS sport_data_provenance (
  sport_id TEXT NOT NULL, source TEXT NOT NULL, season INTEGER, source_url TEXT,
  row_count INTEGER NOT NULL, PRIMARY KEY (sport_id, source, season)
);
"""


def key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", value.lower())


def clear_sport(conn: sqlite3.Connection, sport: str) -> None:
    for table in ("sport_data_provenance", "sport_players_searchable", "sport_player_positions", "sport_appearances",
                  "sport_players", "sport_teams", "sport_franchises"):
        conn.execute(f"DELETE FROM {table} WHERE sport_id = ?", (sport,))


def write_sport(conn: sqlite3.Connection, sport: str, label: str, league: str,
                players: dict, teams: dict, appearances: dict, provenance: list[tuple]) -> None:
    clear_sport(conn, sport)
    seasons = [season for _, season in teams]
    conn.execute(
        "INSERT OR REPLACE INTO sports VALUES (?, ?, ?, ?, ?)",
        (sport, label, league, min(seasons), max(seasons)),
    )
    franchises = {(franchise, name) for franchise, name in teams.values()}
    conn.executemany(
        "INSERT OR REPLACE INTO sport_franchises VALUES (?, ?, ?)",
        [(sport, franchise, name) for franchise, name in franchises],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO sport_teams VALUES (?, ?, ?, ?, ?)",
        [(sport, team, season, franchise, name)
         for (team, season), (franchise, name) in teams.items()],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO sport_players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(sport, pid, *values) for pid, values in players.items()],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO sport_appearances VALUES (?, ?, ?, ?, ?)",
        [(sport, pid, team, season, games)
         for (pid, team, season), games in appearances.items()],
    )
    career = defaultdict(int)
    for (pid, _team, _season), games in appearances.items():
        career[pid] += games
    search_rows = []
    for pid, (_, display, first, last, _birth, debut, final, pos) in players.items():
        search_rows.append((
            sport, pid, display, f"{pos or '?'}, {debut or '?'}-{final or '?'}",
            key(display), key(last or display.split()[-1]), career[pid], 0,
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO sport_players_searchable VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        search_rows,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO sport_data_provenance VALUES (?, ?, ?, ?, ?)",
        [(sport, *row) for row in provenance],
    )
    conn.commit()
    print(f"{sport}: {len(players):,} players, {len(teams):,} team-seasons, "
          f"{len(appearances):,} player-team-seasons")


def load_baseball(conn: sqlite3.Connection, raw: Path) -> None:
    players = {}
    for row in csv.DictReader((raw / "People.csv").open(encoding="utf-8-sig", newline="")):
        debut = (row.get("debut") or "")[:4]
        final = (row.get("finalGame") or "")[:4]
        if not debut.isdigit():
            continue
        players[row["playerID"]] = (
            row.get("bbrefID") or None,
            f"{row.get('nameFirst') or ''} {row.get('nameLast') or ''}".strip(),
            row.get("nameFirst") or None, row.get("nameLast") or None,
            int(row["birthYear"]) if (row.get("birthYear") or "").isdigit() else None,
            int(debut), int(final) if final.isdigit() else None, None,
        )
    teams = {}
    for row in csv.DictReader((raw / "Teams.csv").open(encoding="utf-8-sig", newline="")):
        season = int(row["yearID"])
        teams[(row["teamID"], season)] = (row["franchID"], row.get("name") or row["teamID"])
    appearances = {}
    for row in csv.DictReader((raw / "Appearances.csv").open(encoding="utf-8-sig", newline="")):
        games = int(row.get("G_all") or 0)
        season = int(row["yearID"])
        pid, team = row["playerID"], row["teamID"]
        if games and pid in players and (team, season) in teams:
            appearances[(pid, team, season)] = games
    used = {pid for pid, _, _ in appearances}
    players = {pid: value for pid, value in players.items() if pid in used}
    write_sport(conn, "baseball", "Baseball", "MLB", players, teams, appearances,
                [("Lahman", None, "local raw/Lahman CSV", len(appearances))])


def nfl_team(code: str, season: int) -> tuple[str, str]:
    code = code.upper()
    if code == "BOS": return "NE", "Boston Patriots"
    if code == "BAL": return ("IND", "Baltimore Colts") if season <= 1983 else ("BAL", "Baltimore Ravens")
    if code == "HOU": return ("TEN", "Houston Oilers") if season <= 1996 else ("HOU", "Houston Texans")
    if code in {"LA", "RAM"}: return "LAR", "Los Angeles Rams"
    if code == "STL": return ("ARI", "St. Louis Cardinals") if season <= 1987 else ("LAR", "St. Louis Rams")
    if code in {"OAK", "RAI"}: return "LV", "Oakland Raiders"
    if code == "SD": return "LAC", "San Diego Chargers"
    if code == "PHO": return "ARI", "Phoenix Cardinals"
    names = {"ARI":"Arizona Cardinals", "ATL":"Atlanta Falcons", "BUF":"Buffalo Bills",
             "CAR":"Carolina Panthers", "CHI":"Chicago Bears", "CIN":"Cincinnati Bengals",
             "CLE":"Cleveland Browns", "DAL":"Dallas Cowboys", "DEN":"Denver Broncos",
             "DET":"Detroit Lions", "GB":"Green Bay Packers", "HOU":"Houston Texans",
             "IND":"Indianapolis Colts", "JAC":"Jacksonville Jaguars", "JAX":"Jacksonville Jaguars",
             "KC":"Kansas City Chiefs", "LAC":"Los Angeles Chargers", "LAR":"Los Angeles Rams",
             "LV":"Las Vegas Raiders", "MIA":"Miami Dolphins", "MIN":"Minnesota Vikings",
             "NE":"New England Patriots", "NO":"New Orleans Saints", "NYG":"New York Giants",
             "NYJ":"New York Jets", "PHI":"Philadelphia Eagles", "PIT":"Pittsburgh Steelers",
             "SEA":"Seattle Seahawks", "SF":"San Francisco 49ers", "TB":"Tampa Bay Buccaneers",
             "TEN":"Tennessee Titans", "WAS":"Washington Commanders"}
    return {"JAC":"JAX"}.get(code, code), names.get(code, code)


def load_nfl(conn: sqlite3.Connection, raw: Path) -> None:
    players, teams, appearances, provenance = {}, {}, defaultdict(int), []
    for season in range(1966, 2026):
        directory = "weekly_rosters" if season >= 2002 else "rosters"
        filename = f"roster_weekly_{season}.csv" if season >= 2002 else f"roster_{season}.csv"
        path = raw / "nfl" / directory / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing NFL cache: {path}")
        rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
        for row in rows:
            raw_team = (row.get("team") or "").upper().strip()
            name = (row.get("full_name") or "").strip()
            source_id = (row.get("gsis_id") or row.get("pfr_id") or row.get("espn_id") or "").strip()
            if not raw_team or not name: continue
            pid = f"nfl:{source_id or key(name) + ':' + (row.get('birth_date') or '')}"
            franchise, team_name = nfl_team(raw_team, season)
            if team_name == raw_team: continue
            teams[(raw_team, season)] = (franchise, team_name)
            appearances[(pid, raw_team, season)] += 1
            birth = (row.get("birth_date") or "")[:4]
            players[pid] = (pid, name, row.get("first_name") or None, row.get("last_name") or None,
                            int(birth) if birth.isdigit() else None, season, season,
                            row.get("position") or None)
        provenance.append((directory, season, str(path), len(rows)))
    write_sport(conn, "football", "Football", "NFL", players, teams, appearances, provenance)


def load_nba(conn: sqlite3.Connection, raw: Path) -> None:
    cache = raw / "nba"; cache.mkdir(parents=True, exist_ok=True)
    players, teams, appearances, provenance = {}, {}, defaultdict(int), []
    for season in range(2002, 2026):
        path = cache / f"player_box_{season}.csv"
        if not path.exists():
            response = requests.get(NBA_URL.format(season=season), timeout=120)
            response.raise_for_status()
            path.write_bytes(response.content)
        played = set()
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            row_count = 0
            for row in rows:
                row_count += 1
                minutes = (row.get("minutes") or "").strip()
                if not minutes or minutes in {"0", "0:00"}: continue
                athlete_id, team_id = (row.get("athlete_id") or "").strip(), (row.get("team_id") or "").strip()
                name = (row.get("athlete_display_name") or "").strip()
                if not athlete_id or not team_id or not name: continue
                pid = f"nba:{athlete_id}"
                played.add((pid, team_id))
                team_name = (row.get("team_display_name") or row.get("team_name") or team_id).strip()
                teams[(team_id, season)] = (team_id, team_name)
                bits = name.rsplit(" ", 1)
                players[pid] = (athlete_id, name, bits[0] if len(bits) > 1 else None,
                                bits[-1], None, season, season,
                                row.get("athlete_position_abbreviation") or None)
        for pid, team_id in played:
            appearances[(pid, team_id, season)] = 1
        provenance.append(("sportsdataverse_espn_nba_player_boxscores", season, NBA_URL.format(season=season), row_count))
        print(f"nba source {season}: {len(played):,} player-team-seasons")
    write_sport(conn, "basketball", "Basketball", "NBA", players, teams, appearances, provenance)


def get_with_retries(url: str, timeout: int = 45, attempts: int = 5) -> requests.Response | None:
    """Return a successful NHL response without treating throttling as missing data."""
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException:
            response = None
        if response is not None and response.status_code == 200:
            return response
        if response is not None and response.status_code not in {408, 429, 500, 502, 503, 504}:
            return response
        time.sleep(0.75 * (2 ** attempt))
    return response


def nhl_roster_seasons(code: str) -> tuple[str, list[str], str | None]:
    response = get_with_retries(f"{NHL_API}/roster-season/{code}", timeout=30)
    if response is None:
        return code, [], "request failed after retries"
    if response.status_code == 404:
        return code, [], None
    if response.status_code != 200:
        return code, [], f"HTTP {response.status_code}"
    return code, [str(value) for value in response.json()], None


def load_nhl(conn: sqlite3.Connection, raw: Path) -> None:
    cache = raw / "nhl"; cache.mkdir(parents=True, exist_ok=True)
    players, teams, appearances, provenance = {}, {}, defaultdict(int), []
    season_errors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        season_results = list(pool.map(nhl_roster_seasons, NHL_CODES))
    available = {code: seasons for code, seasons, _error in season_results}
    season_errors = [f"{code}: {error}" for code, _seasons, error in season_results if error]
    if season_errors:
        raise RuntimeError("NHL roster-season lookup failed: " + "; ".join(season_errors))
    targets = [(code, season) for code, seasons in available.items() for season in seasons if int(season[:4]) <= 2025]
    print(f"nhl: fetching {len(targets):,} team-season rosters")

    def fetch(target: tuple[str, str]):
        code, season_key = target
        path = cache / code / f"{season_key}.json"; path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return code, season_key, path, path.read_text(encoding="utf-8"), None
        response = get_with_retries(f"{NHL_API}/roster/{code}/{season_key}")
        if response is None or response.status_code != 200:
            status = "request failed" if response is None else f"HTTP {response.status_code}"
            return code, season_key, path, "", status
        path.write_text(response.text, encoding="utf-8")
        return code, season_key, path, response.text, None

    failures = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fetch, target) for target in targets]
        for index, future in enumerate(as_completed(futures), 1):
            code, season_key, path, text, error = future.result()
            if error:
                failures.append(f"{code} {season_key}: {error}")
                continue
            season = int(season_key[:4])
            payload = json.loads(text)
            people = payload.get("forwards", []) + payload.get("defensemen", []) + payload.get("goalies", [])
            teams[(code, season)] = (code, code)
            for person in people:
                external_id = str(person.get("id") or "")
                first = (person.get("firstName") or {}).get("default", "")
                last = (person.get("lastName") or {}).get("default", "")
                name = f"{first} {last}".strip()
                if not external_id or not name: continue
                pid = f"nhl:{external_id}"
                birth = str(person.get("birthDate") or "")[:4]
                players[pid] = (external_id, name, first or None, last or None,
                                int(birth) if birth.isdigit() else None, season, season,
                                person.get("positionCode") or None)
                appearances[(pid, code, season)] += 1
            provenance.append(("nhl_api_roster", season, str(path), len(people)))
            if index % 100 == 0: print(f"nhl: {index:,}/{len(targets):,} rosters")
    if failures:
        raise RuntimeError("NHL roster download incomplete: " + "; ".join(failures[:12]))
    write_sport(conn, "hockey", "Hockey", "NHL", players, teams, appearances, provenance)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--sports", nargs="+", default=["baseball", "football", "basketball", "hockey"],
                        choices=["baseball", "football", "basketball", "hockey"])
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)
    raw = ROOT / "raw"
    loaders = {"baseball": load_baseball, "football": load_nfl, "basketball": load_nba, "hockey": load_nhl}
    for sport in args.sports:
        print(f"Building {sport}...")
        loaders[sport](conn, raw)
    conn.execute("INSERT OR REPLACE INTO dataset_meta VALUES ('built_at', datetime('now'))")
    conn.commit(); conn.close()
    print(f"Local dataset ready: {args.db}")


if __name__ == "__main__":
    main()
