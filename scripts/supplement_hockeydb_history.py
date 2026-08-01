"""Supplement the local NHL graph with HockeyDB player IDs and NHL stints.

The NHL roster API is the primary source. HockeyDB fills historical holes and
supplies stable HockeyDB player IDs used by its awards data. Existing NHL API
players are reused only for an unambiguous name-and-career match; all other
records receive an `hdb:` canonical ID so no two real players are merged by
guesswork.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import requests

from build_local_sports_dataset import DEFAULT_DB, ROOT, key
from name_normalize import normalize


MASTER_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/Master.csv"
SCORING_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/Scoring.csv"
TEAMS_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/Teams.csv"
NHL_STATS_URL = "https://api.nhle.com/stats/rest/en/{kind}/summary?isAggregate=false&isGame=false&start=0&limit=5000&cayenneExp=seasonId%3D{season_id}%20and%20gameTypeId%3D2"
CACHE = ROOT / "raw" / "hockeydb"
SOURCE = "hockeydb"
NHL_API_SOURCE = "nhl_stats_api"

TEAM_NAMES = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres", "CAR": "Carolina Hurricanes",
    "CBJ": "Columbus Blue Jackets", "CGY": "Calgary Flames", "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche",
    "DAL": "Dallas Stars", "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers", "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens", "NJD": "New Jersey Devils",
    "NSH": "Nashville Predators", "NYI": "New York Islanders", "NYR": "New York Rangers", "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers", "PIT": "Pittsburgh Penguins", "SEA": "Seattle Kraken", "SJS": "San Jose Sharks",
    "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs", "UTA": "Utah Mammoth",
    "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights", "WPG": "Winnipeg Jets", "WSH": "Washington Capitals",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sport_player_external_ids (
  sport_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  PRIMARY KEY (sport_id, source, external_id),
  UNIQUE (sport_id, player_id, source),
  FOREIGN KEY (sport_id, player_id) REFERENCES sport_players(sport_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_sport_player_external_ids_player
  ON sport_player_external_ids(sport_id, player_id);
"""


def integer(value: str | None) -> int | None:
    try:
        return int(value or "")
    except ValueError:
        return None


def fetch(filename: str, url: str) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    path.write_text(response.text, encoding="utf-8", newline="")
    return response.text


def canonical_team_id(source_team: str, known_ids: set[str]) -> str:
    # Same-code clubs retain their existing ID. Obsolete HockeyDB-only codes
    # remain distinct rather than being incorrectly merged with a modern club.
    return source_team if source_team in known_ids else f"hdb:{source_team}"


def fetch_nhl_stats(season: int, kind: str) -> list[dict]:
    path = CACHE / f"nhl_{kind}_{season}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("data", [])
    response = requests.get(NHL_STATS_URL.format(kind=kind, season_id=f"{season}{season + 1}"), timeout=120)
    response.raise_for_status()
    path.write_text(response.text, encoding="utf-8", newline="")
    return response.json().get("data", [])


def supplement_recent_nhl_stats(conn: sqlite3.Connection, seasons: tuple[int, ...] = (2024, 2025)) -> tuple[int, int]:
    """Add recent official player IDs and team-season appearances, including call-ups."""
    known_ids = {row[0] for row in conn.execute("SELECT DISTINCT team_id FROM sport_teams WHERE sport_id='hockey'")}
    players = appearances = 0
    for season in seasons:
        rows = [(row, row.get("skaterFullName") or "", row.get("positionCode") or "?") for row in fetch_nhl_stats(season, "skater")]
        rows += [(row, row.get("goalieFullName") or "", "G") for row in fetch_nhl_stats(season, "goalie")]
        for row, name, position in rows:
            numeric_id = str(row.get("playerId") or "")
            if not numeric_id or not name:
                continue
            player_id = f"nhl:{numeric_id}"
            first, _, last = name.rpartition(" ")
            conn.execute(
                """INSERT OR IGNORE INTO sport_players
                   (sport_id, player_id, external_id, display_name, first_name, last_name, debut_year, final_year, primary_pos)
                   VALUES ('hockey', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (player_id, numeric_id, name, first or None, last or name, season, season, position),
            )
            conn.execute("INSERT OR REPLACE INTO sport_player_external_ids VALUES ('hockey', ?, ?, ?)", (player_id, NHL_API_SOURCE, numeric_id))
            conn.execute(
                """INSERT INTO sport_players_searchable
                   (sport_id, player_id, display_name, disambiguation, search_key, last_key, career_games, teammate_count)
                   VALUES ('hockey', ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(sport_id, player_id) DO UPDATE SET
                     career_games=MAX(sport_players_searchable.career_games, excluded.career_games)""",
                (player_id, name, f"{position}, {season}-{season}", key(name), key(last or name), integer(str(row.get("gamesPlayed") or "0")) or 0),
            )
            players += 1
            for team_id in {part.strip() for part in (row.get("teamAbbrevs") or "").split(",") if part.strip()}:
                team_name = TEAM_NAMES.get(team_id, team_id)
                conn.execute("INSERT OR IGNORE INTO sport_franchises VALUES ('hockey', ?, ?)", (team_id, team_name))
                conn.execute("INSERT OR IGNORE INTO sport_teams VALUES ('hockey', ?, ?, ?, ?)", (team_id, season, team_id, team_name))
                conn.execute(
                    """INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
                       VALUES ('hockey', ?, ?, ?, ?)
                       ON CONFLICT(sport_id, player_id, team_id, season) DO UPDATE SET
                         games_total=MAX(sport_appearances.games_total, excluded.games_total)""",
                    (player_id, team_id, season, integer(str(row.get("gamesPlayed") or "0")) or 0),
                )
                appearances += 1
    return players, appearances


def find_existing_player(players_by_name: dict[str, list[sqlite3.Row]], master: dict[str, str]) -> str | None:
    given = " ".join(part for part in (master.get("firstName"), master.get("lastName")) if part)
    given_key = normalize(given)
    first_nhl, last_nhl = integer(master.get("firstNHL")), integer(master.get("lastNHL"))
    exact = list(players_by_name.get(given_key, []))
    if len(exact) > 1 and first_nhl:
        exact = [row for row in exact if not row["debut_year"] or not row["final_year"] or row["debut_year"] <= last_nhl + 1 and row["final_year"] >= first_nhl - 1]
    return exact[0]["player_id"] if len(exact) == 1 else None


def main() -> None:
    conn = sqlite3.connect(DEFAULT_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        master_rows = {row["playerID"]: row for row in csv.DictReader(io.StringIO(fetch("Master.csv", MASTER_URL)))}
        team_rows = {
            (row["tmID"], integer(row["year"])): row
            for row in csv.DictReader(io.StringIO(fetch("Teams.csv", TEAMS_URL)))
            if row.get("lgID") == "NHL" and integer(row.get("year"))
        }
        stints: dict[tuple[str, int, str], int] = defaultdict(int)
        for row in csv.DictReader(io.StringIO(fetch("Scoring.csv", SCORING_URL))):
            season, games = integer(row.get("year")), integer(row.get("GP"))
            if row.get("lgID") == "NHL" and season and row.get("playerID") and row.get("tmID"):
                stints[(row["playerID"], season, row["tmID"])] += games or 0

        known_ids = {row[0] for row in conn.execute("SELECT DISTINCT team_id FROM sport_teams WHERE sport_id='hockey'")}
        local_players = list(conn.execute("SELECT player_id, display_name, debut_year, final_year FROM sport_players WHERE sport_id='hockey'"))
        players_by_name: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for player in local_players:
            players_by_name[normalize(player["display_name"])].append(player)
        existing_hdb = {row[0]: row[1] for row in conn.execute("SELECT external_id, player_id FROM sport_player_external_ids WHERE sport_id='hockey' AND source=?", (SOURCE,))}
        player_ids: dict[str, str] = {}
        added = reused = 0
        for hdb_id in {pid for pid, _season, _team in stints}:
            if hdb_id in existing_hdb:
                player_ids[hdb_id] = existing_hdb[hdb_id]
                continue
            master = master_rows.get(hdb_id)
            if not master:
                continue
            player_id = find_existing_player(players_by_name, master)
            if player_id:
                reused += 1
            else:
                player_id = f"hdb:{hdb_id}"
                name = " ".join(part for part in (master.get("firstName"), master.get("lastName")) if part)
                first_nhl, last_nhl = integer(master.get("firstNHL")), integer(master.get("lastNHL"))
                conn.execute(
                    """INSERT OR IGNORE INTO sport_players
                       (sport_id, player_id, external_id, display_name, first_name, last_name, birth_year, debut_year, final_year, primary_pos)
                       VALUES ('hockey', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (player_id, hdb_id, name, master.get("firstName") or None, master.get("lastName") or None,
                     integer(master.get("birthYear")), first_nhl, last_nhl, master.get("pos") or None),
                )
                player = conn.execute("SELECT player_id, display_name, debut_year, final_year FROM sport_players WHERE sport_id='hockey' AND player_id=?", (player_id,)).fetchone()
                local_players.append(player)
                players_by_name[normalize(player["display_name"])].append(player)
                added += 1
            conn.execute(
                "INSERT OR REPLACE INTO sport_player_external_ids VALUES ('hockey', ?, ?, ?)",
                (player_id, SOURCE, hdb_id),
            )
            player_ids[hdb_id] = player_id

        team_count = appearance_count = 0
        career_games: dict[str, int] = defaultdict(int)
        franchises: set[tuple[str, str]] = set()
        teams: set[tuple[str, int, str, str]] = set()
        appearances: list[tuple[str, str, int, int]] = []
        for (hdb_id, season, source_team), games in stints.items():
            player_id = player_ids.get(hdb_id)
            if not player_id:
                continue
            team_id = canonical_team_id(source_team, known_ids)
            team = team_rows.get((source_team, season), {})
            name = team.get("name") or source_team
            franchise = team.get("franchID") or team_id
            franchises.add((franchise, name))
            teams.add((team_id, season, franchise, name))
            team_count += 1
            appearances.append((player_id, team_id, season, games))
            appearance_count += 1
            career_games[player_id] += games
        conn.executemany("INSERT OR IGNORE INTO sport_franchises VALUES ('hockey', ?, ?)", franchises)
        conn.executemany("INSERT OR IGNORE INTO sport_teams VALUES ('hockey', ?, ?, ?, ?)", teams)
        conn.executemany(
            """INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
               VALUES ('hockey', ?, ?, ?, ?)
               ON CONFLICT(sport_id, player_id, team_id, season) DO UPDATE SET
                 games_total=MAX(sport_appearances.games_total, excluded.games_total)""",
            appearances,
        )
        for player_id, games in career_games.items():
            player = conn.execute("SELECT display_name, primary_pos, debut_year, final_year, last_name FROM sport_players WHERE sport_id='hockey' AND player_id=?", (player_id,)).fetchone()
            conn.execute(
                """INSERT INTO sport_players_searchable
                   (sport_id, player_id, display_name, disambiguation, search_key, last_key, career_games, teammate_count)
                   VALUES ('hockey', ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(sport_id, player_id) DO UPDATE SET
                     career_games=MAX(sport_players_searchable.career_games, excluded.career_games)""",
                (player_id, player["display_name"], f"{player['primary_pos'] or '?'}, {player['debut_year'] or '?'}-{player['final_year'] or '?'}",
                 key(player["display_name"]), key(player["last_name"] or player["display_name"].split()[-1]), games),
            )
        recent_players, recent_appearances = supplement_recent_nhl_stats(conn)
        conn.commit()
    finally:
        conn.close()
    print(f"HockeyDB supplement: {added:,} players added, {reused:,} existing players linked, {appearance_count:,} NHL stints processed.")
    print(f"Official NHL recent stats: {recent_players:,} player rows and {recent_appearances:,} team-season appearances processed.")


if __name__ == "__main__":
    main()
