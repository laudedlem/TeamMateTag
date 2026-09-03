"""Load verified player statistics into the local cross-sport traits table.

Sources:
- NBA: the downloaded CC0 eoinamoore Kaggle box-score archive already under
  raw/nba_kaggle/.
- NFL: nflverse player-stat release files (CC BY 4.0), 1999 onward.
- NHL: flynn28/nhl-player-database on Kaggle (CC BY 4.0), career totals.

The table is additive and is intentionally separate from the teammate graph.
Run: python scripts/load_local_sport_traits.py --download-nhl
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from build_local_sports_dataset import ROOT
from name_normalize import normalize


DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
NBA_STATS = ROOT / "raw" / "nba_kaggle" / "PlayerStatistics.csv"
NHL_URL = "https://www.kaggle.com/api/v1/datasets/download/flynn28/nhl-player-database"
NBA_AWARDS_URL = "https://www.kaggle.com/api/v1/datasets/download/sumitrodatta/nba-aba-baa-stats"
NFL_URL = "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_week_{season}.csv"
NFL_SEASON_URL = "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_reg_{season}.csv"
NFL_SCHEDULES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
HOCKEYDB_MASTER_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/Master.csv"
HOCKEYDB_AWARDS_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/AwardsPlayers.csv"
NHL_SCHEDULE_URL = "https://api-web.nhle.com/v1/club-schedule-season/{team}/{season}"
NHL_CHAMPION_FALLBACK_URL = "https://gist.githubusercontent.com/cperera1997/5f22ac67099c16937e54e96b28a9b037/raw/52c84459973b018a42c39aa6405161518b649cbb/stanley%20cup%20champions%20playoff%20data.csv"
MODERN_NHL_HART_WINNERS = [
    "Taylor Hall",
    "Nikita Kucherov",
    "Leon Draisaitl",
    "Connor McDavid",
    "Auston Matthews",
    "Connor McDavid",
    "Nathan MacKinnon",
    "Connor Hellebuyck",
    "Nikita Kucherov",
]
MODERN_NHL_CALDER_WINNERS = [
    "Elias Pettersson",
    "Cale Makar",
    "Kirill Kaprizov",
    "Moritz Seider",
    "Matty Beniers",
    "Connor Bedard",
    "Lane Hutson",
    "Matthew Schaefer",
]
MODERN_NHL_PLAYER_OVERRIDES = {
    "Taylor Hall": "nhl:8475791",
    "Elias Pettersson": "nhl:8480012",
}
NHL_TEAM_CODES = (
    "ANA", "ARI", "ATL", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "CLE", "COL", "DAL",
    "DET", "EDM", "FLA", "HFD", "KCS", "LAK", "MDA", "MIN", "MNS", "MTL", "NJD", "NSH",
    "NYI", "NYR", "OTT", "PHI", "PHX", "PIT", "QUE", "SEA", "SJS", "STL", "TBL", "TOR",
    "UTA", "VAN", "VGK", "WIN", "WPG", "WSH",
)
NHL_CHAMPION_TEAM_CODES = {
    "anaheim ducks": "ANA", "boston bruins": "BOS", "calgary flames": "CGY",
    "carolina hurricanes": "CAR", "chicago blackhawks": "CHI", "colorado avalanche": "COL",
    "dallas stars": "DAL", "detroit red wings": "DET", "edmonton oilers": "EDM",
    "los angeles kings": "LAK", "montreal canadiens": "MTL", "new jersey devils": "NJD",
    "new york rangers": "NYR", "pittsburgh penguins": "PIT", "st louis blues": "STL",
    "tampa bay lightning": "TBL", "washington capitals": "WSH",
}
# Hockey Databank's team ids are the source ids used by the historic NHL
# appearance import. Modern clubs use the short ids directly, while a small
# number of defunct clubs retain their namespaced source id.
HOCKEYDB_CHAMPION_TEAM_IDS = {
    "AND": "ANA", "BOS": "BOS", "CAL": "CGY", "CAR": "CAR", "CHI": "CHI",
    "COL": "COL", "DAL": "DAL", "DET": "DET", "EDM": "EDM", "LAK": "LAK",
    "MTL": "MTL", "MTM": "hdb:MTM", "NJD": "NJD", "NYI": "NYI", "NYR": "NYR",
    "OTS": "hdb:OTS", "PHI": "PHI", "PIT": "PIT", "TBL": "TBL", "TOA": "hdb:TOA",
    "TOR": "TOR", "TRS": "hdb:TRS", "WAS": "WSH",
}
# Season-start year: champion. Verified against NHL Records because the club
# schedule feed omits several recent final series.
NHL_RECENT_CHAMPIONS = {2020: "TBL", 2021: "COL", 2022: "VGK", 2023: "FLA", 2024: "FLA", 2025: "CAR"}
# 1924-25 Montreal won the Cup, but Hockey Databank labels the club "F" rather
# than "SC" for that inter-league final.
NHL_HISTORIC_CHAMPION_OVERRIDES = {1924: ("MTL", "MTL")}
HOCKEYDB_SCORING = ROOT / "raw" / "hockeydb" / "Scoring.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sport_player_traits (
  sport_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  career_games INTEGER NOT NULL DEFAULT 0,
  career_points INTEGER NOT NULL DEFAULT 0,
  career_goals INTEGER NOT NULL DEFAULT 0,
  career_assists INTEGER NOT NULL DEFAULT 0,
  career_touchdowns INTEGER NOT NULL DEFAULT 0,
  passing_touchdowns INTEGER NOT NULL DEFAULT 0,
  rushing_touchdowns INTEGER NOT NULL DEFAULT 0,
  receiving_touchdowns INTEGER NOT NULL DEFAULT 0,
  career_sacks REAL NOT NULL DEFAULT 0,
  career_interceptions INTEGER NOT NULL DEFAULT 0,
  all_star_count INTEGER NOT NULL DEFAULT 0,
  mvp_count INTEGER NOT NULL DEFAULT 0,
  roty_count INTEGER NOT NULL DEFAULT 0,
  championship_count INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sport_id, player_id)
);
CREATE TABLE IF NOT EXISTS sport_trait_provenance (
  sport_id TEXT NOT NULL,
  source TEXT NOT NULL,
  source_url TEXT NOT NULL,
  coverage TEXT NOT NULL,
  loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sport_id, source)
);
CREATE TABLE IF NOT EXISTS sport_player_season_traits (
  sport_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  season INTEGER NOT NULL,
  games INTEGER NOT NULL DEFAULT 0,
  points INTEGER NOT NULL DEFAULT 0,
  goals INTEGER NOT NULL DEFAULT 0,
  assists INTEGER NOT NULL DEFAULT 0,
  touchdowns INTEGER NOT NULL DEFAULT 0,
  passing_touchdowns INTEGER NOT NULL DEFAULT 0,
  rushing_touchdowns INTEGER NOT NULL DEFAULT 0,
  receiving_touchdowns INTEGER NOT NULL DEFAULT 0,
  sacks REAL NOT NULL DEFAULT 0,
  interceptions INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL,
  PRIMARY KEY (sport_id, player_id, season)
);
CREATE INDEX IF NOT EXISTS idx_sport_player_season_trait_lookup
  ON sport_player_season_traits (sport_id, player_id);
"""


def integer(value: str | int | float | None) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def number(value: str | int | float | None) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def make_name_index(conn: sqlite3.Connection, sport: str) -> dict[str, list[str]]:
    rows = conn.execute("SELECT player_id, display_name FROM sport_players WHERE sport_id=?", (sport,)).fetchall()
    index: dict[str, list[str]] = defaultdict(list)
    for player_id, name in rows:
        index[normalize(name)].append(player_id)
    return index


def replace_traits(conn: sqlite3.Connection, sport: str, rows: list[tuple], source: str, url: str, coverage: str) -> None:
    conn.execute("DELETE FROM sport_player_traits WHERE sport_id=?", (sport,))
    conn.executemany(
        """INSERT INTO sport_player_traits (
              sport_id, player_id, career_games, career_points, career_goals,
              career_assists, career_touchdowns, passing_touchdowns,
              rushing_touchdowns, receiving_touchdowns, career_sacks,
              career_interceptions, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.execute("DELETE FROM sport_trait_provenance WHERE sport_id=? AND source=?", (sport, source))
    conn.execute(
        "INSERT INTO sport_trait_provenance (sport_id, source, source_url, coverage) VALUES (?, ?, ?, ?)",
        (sport, source, url, coverage),
    )


def replace_season_traits(conn: sqlite3.Connection, sport: str, rows: list[tuple]) -> None:
    """Replace indexed per-season achievement totals for condition evaluation."""
    conn.execute("DELETE FROM sport_player_season_traits WHERE sport_id=?", (sport,))
    conn.executemany(
        """INSERT INTO sport_player_season_traits (
              sport_id, player_id, season, games, points, goals, assists,
              touchdowns, passing_touchdowns, rushing_touchdowns,
              receiving_touchdowns, sacks, interceptions, source
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def update_championship_counts(
    conn: sqlite3.Connection, sport: str, champions: dict[int, str], source: str, url: str, coverage: str
) -> tuple[int, int]:
    """Credit rostered players for a champion's season.

    The teammate graph stores season-level appearances, so this deliberately
    measures championship-roster membership rather than attempting to infer
    who received an official ring from every club's front office.
    """
    counts: dict[str, int] = defaultdict(int)
    matched_seasons = 0
    for season, team_id in champions.items():
        rows = conn.execute(
            """SELECT DISTINCT player_id FROM sport_appearances
                 WHERE sport_id=? AND season=? AND team_id=?""",
            (sport, season, team_id),
        ).fetchall()
        if rows:
            matched_seasons += 1
        for (player_id,) in rows:
            counts[player_id] += 1
    conn.execute("UPDATE sport_player_traits SET championship_count=0 WHERE sport_id=?", (sport,))
    conn.executemany(
        """UPDATE sport_player_traits
              SET championship_count=?, updated_at=CURRENT_TIMESTAMP
            WHERE sport_id=? AND player_id=?""",
        [(count, sport, player_id) for player_id, count in counts.items()],
    )
    conn.execute("DELETE FROM sport_trait_provenance WHERE sport_id=? AND source=?", (sport, source))
    conn.execute(
        "INSERT INTO sport_trait_provenance (sport_id, source, source_url, coverage) VALUES (?, ?, ?, ?)",
        (sport, source, url, coverage),
    )
    return len(counts), matched_seasons


def update_nhl_championship_counts(
    conn: sqlite3.Connection,
    champions: dict[int, str],
    hockeydb_champions: dict[int, str],
) -> tuple[int, int]:
    """Credit Cup winners from a playoff roster where HockeyDB provides one.

    HockeyDB has individual playoff-game participation through 2017. That is a
    materially better historical definition than every regular-season appearance.
    The modern tail falls back to the game's season roster until a comparable
    playoff roster source is added.
    """
    if not HOCKEYDB_SCORING.exists():
        raise RuntimeError(f"Missing Hockey Databank scoring source: {HOCKEYDB_SCORING}")
    external_ids = {
        external_id: player_id
        for external_id, player_id in conn.execute(
            """SELECT external_id, player_id FROM sport_player_external_ids
                 WHERE sport_id='hockey' AND source='hockeydb'"""
        )
    }
    playoff_rosters: dict[tuple[int, str], set[str]] = defaultdict(set)
    with HOCKEYDB_SCORING.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("lgID") != "NHL" or integer(row.get("PostGP")) <= 0:
                continue
            player_id = external_ids.get((row.get("playerID") or "").strip())
            if player_id:
                playoff_rosters[(integer(row.get("year")), (row.get("tmID") or "").strip())].add(player_id)

    counts: dict[str, int] = defaultdict(int)
    matched_seasons = 0
    for season, team_id in champions.items():
        players = playoff_rosters.get((season, hockeydb_champions[season])) if season in hockeydb_champions else None
        if players is None:
            players = {
                player_id for (player_id,) in conn.execute(
                    """SELECT DISTINCT player_id FROM sport_appearances
                         WHERE sport_id='hockey' AND season=? AND team_id=?""",
                    (season, team_id),
                )
            }
        if players:
            matched_seasons += 1
            for player_id in players:
                counts[player_id] += 1
    conn.execute("UPDATE sport_player_traits SET championship_count=0 WHERE sport_id='hockey'")
    conn.executemany(
        """UPDATE sport_player_traits SET championship_count=?, updated_at=CURRENT_TIMESTAMP
             WHERE sport_id='hockey' AND player_id=?""",
        [(count, player_id) for player_id, count in counts.items()],
    )
    return len(counts), matched_seasons


def load_nba(conn: sqlite3.Connection) -> int:
    if not NBA_STATS.exists():
        raise RuntimeError(f"Missing NBA source file: {NBA_STATS}")
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seasons: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with NBA_STATS.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("gameType") or "").lower() != "regular season":
                continue
            player_id = (row.get("personId") or "").strip()
            if not player_id:
                continue
            played_at = (row.get("gameDate") or row.get("gameDateTimeEst") or "")[:4]
            calendar_year = integer(played_at)
            # NBA seasons cross calendar years. Games in Oct-Dec begin the
            # season; games in Jan-Jun belong to the previous start year.
            month = integer((row.get("gameDate") or row.get("gameDateTimeEst") or "")[5:7])
            season = calendar_year if month >= 8 else calendar_year - 1
            if not season:
                continue
            stats = totals[f"nba:{player_id}"]
            stats["games"] += 1
            stats["points"] += integer(row.get("points"))
            stats["goals"] += integer(row.get("threePointersMade"))
            stats["assists"] += integer(row.get("assists"))
            season_stats = seasons[(f"nba:{player_id}", season)]
            season_stats["games"] += 1
            season_stats["points"] += integer(row.get("points"))
            season_stats["goals"] += integer(row.get("threePointersMade"))
            season_stats["assists"] += integer(row.get("assists"))
    rows = [("basketball", player_id, stat["games"], stat["points"], stat["goals"], stat["assists"], 0, 0, 0, 0, 0, 0, "kaggle_eoinamoore_nba") for player_id, stat in totals.items()]
    replace_traits(conn, "basketball", rows, "kaggle_eoinamoore_nba", "https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores", "regular-season box scores, 1947-present source archive")
    replace_season_traits(conn, "basketball", [
        ("basketball", player_id, season, stat["games"], stat["points"], stat["goals"], stat["assists"], 0, 0, 0, 0, 0, 0, "kaggle_eoinamoore_nba")
        for (player_id, season), stat in seasons.items()
    ])
    return len(rows)


def load_nba_awards(conn: sqlite3.Connection) -> tuple[int, int]:
    """Load MVP, Rookie of the Year, and All-Star counts from a public archive."""
    response = requests.get(NBA_AWARDS_URL, timeout=120)
    response.raise_for_status()
    index = make_name_index(conn, "basketball")
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unresolved = 0
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        with archive.open("Player Award Shares.csv") as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")):
                if (row.get("winner") or "").upper() != "TRUE":
                    continue
                player_ids = index.get(normalize(row.get("player") or ""), [])
                if len(player_ids) != 1:
                    unresolved += 1
                    continue
                award = (row.get("award") or "").lower()
                if award in {"nba mvp", "aba mvp"}: counts[player_ids[0]]["mvp"] += 1
                elif award in {"nba roy", "aba roy", "baa roy"}: counts[player_ids[0]]["roty"] += 1
        with archive.open("All-Star Selections.csv") as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")):
                player_ids = index.get(normalize(row.get("player") or ""), [])
                if len(player_ids) == 1:
                    counts[player_ids[0]]["all_star"] += 1
                else:
                    unresolved += 1
    conn.executemany(
        """UPDATE sport_player_traits
              SET mvp_count=?, roty_count=?, all_star_count=?, updated_at=CURRENT_TIMESTAMP
            WHERE sport_id='basketball' AND player_id=?""",
        [(values["mvp"], values["roty"], values["all_star"], player_id) for player_id, values in counts.items()],
    )
    conn.execute("DELETE FROM sport_trait_provenance WHERE sport_id='basketball' AND source='kaggle_sumitrodatta_nba_awards'")
    conn.execute(
        "INSERT INTO sport_trait_provenance (sport_id, source, source_url, coverage) VALUES (?, ?, ?, ?)",
        ("basketball", "kaggle_sumitrodatta_nba_awards", "https://www.kaggle.com/datasets/sumitrodatta/nba-aba-baa-stats", "MVP, Rookie of the Year, and All-Star selections, 1947-present source archive"),
    )
    return len(counts), unresolved


def load_nba_championships(conn: sqlite3.Connection) -> tuple[int, int]:
    """Derive NBA champions from the decisive NBA Finals game in the local archive."""
    finalists: dict[int, tuple[str, int, str]] = {}
    with NBA_STATS.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("gameType") != "Playoffs" or row.get("gameLabel") != "NBA Finals":
                continue
            if integer(row.get("win")) != 1:
                continue
            date = (row.get("gameDate") or row.get("gameDateTimeEst") or "")[:10]
            if len(date) < 4:
                continue
            game_id = (row.get("gameId") or "").strip()
            # NBA playoff game IDs encode the season start year as 4YYxxxxx.
            # This handles normal June Finals and delayed bubble seasons.
            season = 2000 + integer(game_id[1:3]) if len(game_id) >= 3 and game_id.startswith("4") else 0
            if not season:
                year = integer(date[:4])
                season = year - 1 if len(date) >= 7 and integer(date[5:7]) <= 7 else year
            candidate = (date, integer(row.get("seriesGameNumber")), row.get("playerteamId") or "")
            if candidate[2] and (season not in finalists or candidate[:2] > finalists[season][:2]):
                finalists[season] = candidate
    champions = {season: value[2] for season, value in finalists.items()}
    return update_championship_counts(
        conn, "basketball", champions, "nba_finals_boxscores",
        "https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores",
        "NBA Finals decisive-game winners derived from local player box scores",
    )


def load_nfl(conn: sqlite3.Connection, first_season: int, last_season: int) -> tuple[int, list[int]]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    seasons: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    skipped = []
    for season in range(first_season, last_season + 1):
        response = requests.get(NFL_URL.format(season=season), timeout=90)
        is_season_total = False
        if response.status_code == 404:
            response = requests.get(NFL_SEASON_URL.format(season=season), timeout=90)
            is_season_total = True
        if response.status_code == 404:
            skipped.append(season)
            continue
        response.raise_for_status()
        for row in csv.DictReader(io.StringIO(response.text)):
            player_id = (row.get("player_id") or "").strip()
            if not player_id:
                continue
            stats = totals[f"nfl:{player_id}"]
            stats["games"] += integer(row.get("games")) if is_season_total else 1
            passing = integer(row.get("passing_tds")); rushing = integer(row.get("rushing_tds")); receiving = integer(row.get("receiving_tds"))
            stats["passing_tds"] += passing; stats["rushing_tds"] += rushing; stats["receiving_tds"] += receiving
            stats["touchdowns"] += passing + rushing + receiving + integer(row.get("special_teams_tds")) + integer(row.get("def_tds")) + integer(row.get("fumble_recovery_tds"))
            stats["sacks"] += number(row.get("def_sacks")); stats["interceptions"] += integer(row.get("def_interceptions"))
            season_stats = seasons[(f"nfl:{player_id}", season)]
            season_stats["games"] += integer(row.get("games")) if is_season_total else 1
            season_stats["passing_tds"] += passing; season_stats["rushing_tds"] += rushing; season_stats["receiving_tds"] += receiving
            season_stats["touchdowns"] += passing + rushing + receiving + integer(row.get("special_teams_tds")) + integer(row.get("def_tds")) + integer(row.get("fumble_recovery_tds"))
            season_stats["sacks"] += number(row.get("def_sacks")); season_stats["interceptions"] += integer(row.get("def_interceptions"))
    rows = [("football", player_id, int(stat["games"]), 0, 0, 0, int(stat["touchdowns"]), int(stat["passing_tds"]), int(stat["rushing_tds"]), int(stat["receiving_tds"]), stat["sacks"], int(stat["interceptions"]), "nflverse_player_stats") for player_id, stat in totals.items()]
    replace_traits(conn, "football", rows, "nflverse_player_stats", "https://github.com/nflverse/nflverse-data/releases/tag/player_stats", f"weekly player stats, {first_season}-{last_season}; unavailable seasons: {','.join(map(str, skipped)) or 'none'}")
    replace_season_traits(conn, "football", [
        ("football", player_id, season, int(stat["games"]), 0, 0, 0, int(stat["touchdowns"]), int(stat["passing_tds"]), int(stat["rushing_tds"]), int(stat["receiving_tds"]), stat["sacks"], int(stat["interceptions"]), "nflverse_player_stats")
        for (player_id, season), stat in seasons.items()
    ])
    return len(rows), skipped


def load_nfl_championships(conn: sqlite3.Connection) -> tuple[int, int]:
    """Derive Super Bowl champions from nflverse's game schedule results."""
    response = requests.get(NFL_SCHEDULES_URL, timeout=120)
    response.raise_for_status()
    champions: dict[int, str] = {}
    for row in csv.DictReader(io.StringIO(response.text)):
        if row.get("game_type") != "SB":
            continue
        season = integer(row.get("season"))
        away_score = integer(row.get("away_score")); home_score = integer(row.get("home_score"))
        if not season or away_score == home_score:
            continue
        champions[season] = row.get("away_team") if away_score > home_score else row.get("home_team")
    return update_championship_counts(
        conn, "football", champions, "nflverse_schedules", NFL_SCHEDULES_URL,
        "Super Bowl winners from nflverse schedules; roster-season membership, 1999-present",
    )


def load_nhl(conn: sqlite3.Connection, download: bool) -> tuple[int, int]:
    cache = ROOT / "raw" / "nhl_player_database.zip"
    if download or not cache.exists():
        response = requests.get(NHL_URL, timeout=90)
        response.raise_for_status()
        cache.write_bytes(response.content)
    index = make_name_index(conn, "hockey")
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unresolved = 0
    with zipfile.ZipFile(cache) as archive:
        files = [name for name in archive.namelist() if name.lower().endswith('.csv')]
        for filename in files:
            with archive.open(filename) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")):
                    name = (row.get("name") or row.get("player") or "").strip()
                    player_ids = index.get(normalize(name), [])
                    if len(player_ids) != 1:
                        unresolved += bool(name)
                        continue
                    stat = totals[player_ids[0]]
                    stat["games"] = max(stat["games"], integer(row.get("games")))
                    stat["goals"] = max(stat["goals"], integer(row.get("goals")))
                    stat["assists"] = max(stat["assists"], integer(row.get("assists")))
    rows = [("hockey", player_id, stat["games"], stat["goals"] + stat["assists"], stat["goals"], stat["assists"], 0, 0, 0, 0, 0, 0, "kaggle_flynn28_nhl") for player_id, stat in totals.items()]
    replace_traits(conn, "hockey", rows, "kaggle_flynn28_nhl", "https://www.kaggle.com/datasets/flynn28/nhl-player-database", "career skater and goalie totals, 1918-present")
    if not HOCKEYDB_SCORING.exists():
        raise RuntimeError(f"Missing Hockey Databank scoring source: {HOCKEYDB_SCORING}")
    hockeydb_ids = {
        external_id: player_id
        for external_id, player_id in conn.execute(
            """SELECT external_id, player_id FROM sport_player_external_ids
                 WHERE sport_id='hockey' AND source='hockeydb'"""
        )
    }
    seasons: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with HOCKEYDB_SCORING.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("lgID") != "NHL":
                continue
            player_id = hockeydb_ids.get((row.get("playerID") or "").strip())
            season = integer(row.get("year"))
            if not player_id or not season:
                continue
            stat = seasons[(player_id, season)]
            stat["games"] += integer(row.get("GP"))
            stat["goals"] += integer(row.get("G"))
            stat["assists"] += integer(row.get("A"))
    replace_season_traits(conn, "hockey", [
        ("hockey", player_id, season, stat["games"], stat["goals"] + stat["assists"], stat["goals"], stat["assists"], 0, 0, 0, 0, 0, 0, "hockey_databank_scoring")
        for (player_id, season), stat in seasons.items()
    ])
    return len(rows), unresolved


def load_nhl_awards(conn: sqlite3.Connection) -> tuple[int, int]:
    """Load historical Hart, Calder, and All-Star counts through HockeyDB's coverage."""
    master = requests.get(HOCKEYDB_MASTER_URL, timeout=90)
    awards = requests.get(HOCKEYDB_AWARDS_URL, timeout=90)
    master.raise_for_status(); awards.raise_for_status()
    index = make_name_index(conn, "hockey")
    award_rows = list(csv.DictReader(io.StringIO(awards.text)))
    award_source_ids = {row.get("playerID") for row in award_rows if row.get("playerID")}
    local_rows = conn.execute(
        """SELECT player_id, display_name, first_name, last_name, debut_year, final_year
             FROM sport_players WHERE sport_id='hockey'"""
    ).fetchall()
    by_last: dict[str, list[tuple]] = defaultdict(list)
    for local in local_rows:
        by_last[normalize(local[3] or local[1].rsplit(" ", 1)[-1])].append(local)
    source_to_local: dict[str, str] = {
        external_id: player_id for external_id, player_id in conn.execute(
            """SELECT external_id, player_id
                 FROM sport_player_external_ids
                WHERE sport_id='hockey' AND source='hockeydb'"""
        )
    }
    unresolved_ids: set[str] = set()
    for row in csv.DictReader(io.StringIO(master.text)):
        source_id = row.get("playerID") or ""
        if source_id not in award_source_ids:
            continue
        if source_id in source_to_local:
            continue
        given = (row.get("nameGiven") or row.get("firstName") or "").strip()
        last = (row.get("lastName") or "").strip()
        name = " ".join(part for part in (given, last) if part).strip()
        player_ids = index.get(normalize(name), [])
        if len(player_ids) != 1 and given and last:
            # Hockey Databank often records middle names while the local NHL
            # roster source uses the player's common first name.
            player_ids = index.get(normalize(f"{given.split()[0]} {last}"), [])
        if len(player_ids) == 1:
            source_to_local[source_id] = player_ids[0]
            continue
        candidates = by_last.get(normalize(last), [])
        first_initial = normalize(given)[:1]
        source_first = integer(row.get("firstNHL")); source_last = integer(row.get("lastNHL"))
        scored: list[tuple[int, str]] = []
        for candidate in candidates:
            player_id, display_name, first_name, _last_name, debut, final = candidate
            candidate_initial = normalize(first_name or display_name)[:1]
            if not first_initial or first_initial != candidate_initial:
                continue
            score = 10
            if normalize(display_name) == normalize(name):
                score += 100
            if source_first and source_last and debut and final and not (final < source_first - 1 or debut > source_last + 1):
                score += 20
            scored.append((score, player_id))
        scored.sort(reverse=True)
        if len(scored) == 1 or (len(scored) > 1 and scored[0][0] > scored[1][0]):
            source_to_local[source_id] = scored[0][1]
        else:
            unresolved_ids.add(source_id)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in award_rows:
        player_id = source_to_local.get(row.get("playerID") or "")
        if not player_id:
            continue
        award = row.get("award") or ""
        if award == "Hart": counts[player_id]["mvp"] += 1
        elif award == "Calder": counts[player_id]["roty"] += 1
        elif award in {"First Team All-Star", "Second Team All-Star"}: counts[player_id]["all_star"] += 1
    def resolve_modern_award_name(name: str) -> str | None:
        override = MODERN_NHL_PLAYER_OVERRIDES.get(name)
        if override:
            exists = conn.execute(
                "SELECT 1 FROM sport_players_searchable WHERE sport_id='hockey' AND player_id=?",
                (override,),
            ).fetchone()
            if exists:
                return override
        player_ids = index.get(normalize(name), [])
        if len(player_ids) == 1:
            return player_ids[0]
        return None

    for name in MODERN_NHL_HART_WINNERS:
        player_id = resolve_modern_award_name(name)
        if player_id:
            counts[player_id]["mvp"] += 1
        else:
            unresolved_ids.add(f"modern_hart:{name}")
    for name in MODERN_NHL_CALDER_WINNERS:
        player_id = resolve_modern_award_name(name)
        if player_id:
            counts[player_id]["roty"] += 1
        else:
            unresolved_ids.add(f"modern_calder:{name}")
    conn.execute(
        """INSERT OR IGNORE INTO sport_player_traits (
              sport_id, player_id, career_games, career_points, career_goals,
              career_assists, career_touchdowns, passing_touchdowns,
              rushing_touchdowns, receiving_touchdowns, career_sacks,
              career_interceptions, source)
           SELECT ps.sport_id, ps.player_id, ps.career_games, 0, 0,
                  0, 0, 0, 0, 0, 0, 0, 'hockey_awards_trait_seed'
             FROM sport_players_searchable ps
            WHERE ps.sport_id='hockey'"""
    )
    conn.execute(
        "UPDATE sport_player_traits SET mvp_count=0, roty_count=0, all_star_count=0 WHERE sport_id='hockey'"
    )
    conn.executemany(
        """UPDATE sport_player_traits
              SET mvp_count=?, roty_count=?, all_star_count=?, updated_at=CURRENT_TIMESTAMP
            WHERE sport_id='hockey' AND player_id=?""",
        [(values["mvp"], values["roty"], values["all_star"], player_id) for player_id, values in counts.items()],
    )
    conn.execute("DELETE FROM sport_trait_provenance WHERE sport_id='hockey' AND source='hockey_databank_awards'")
    conn.execute(
        "INSERT INTO sport_trait_provenance (sport_id, source, source_url, coverage) VALUES (?, ?, ?, ?)",
        ("hockey", "hockey_databank_awards", HOCKEYDB_AWARDS_URL, "Hart, Calder, and First/Second Team All-Star counts through Hockey Databank coverage plus NHL.com modern Hart/Calder supplements"),
    )
    return len(counts), len(unresolved_ids)


def load_nhl_championships(conn: sqlite3.Connection, first_season: int, last_season: int) -> tuple[int, int, list[int]]:
    """Load Stanley Cup champion seasons and credit rostered players once.

    Hockey Databank is the authoritative local historical backbone through
    2017. The NHL schedule/fallback sources fill the modern tail. This function
    always resets hockey counts, so running any loader repeatedly cannot inflate
    a player's total.
    """
    cache_root = ROOT / "raw" / "nhl_champion_schedules"
    champions: dict[int, str] = {}
    hockeydb_champions: dict[int, str] = {}
    hockeydb_path = ROOT / "raw" / "hockeydb" / "Teams.csv"
    if not hockeydb_path.exists():
        raise RuntimeError(f"Missing Hockey Databank team source: {hockeydb_path}")
    with hockeydb_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            season = integer(row.get("year"))
            source_team = (row.get("tmID") or "").strip()
            if (
                row.get("lgID") == "NHL"
                and row.get("playoff") == "SC"
                and first_season <= season <= min(last_season, 2017)
                and source_team in HOCKEYDB_CHAMPION_TEAM_IDS
            ):
                hockeydb_champions[season] = source_team
                champions[season] = HOCKEYDB_CHAMPION_TEAM_IDS[source_team]
    for season, (source_team, team_id) in NHL_HISTORIC_CHAMPION_OVERRIDES.items():
        if first_season <= season <= min(last_season, 2017):
            hockeydb_champions[season] = source_team
            champions[season] = team_id

    fallback = requests.get(NHL_CHAMPION_FALLBACK_URL, timeout=60)
    fallback.raise_for_status()
    fallback_seasons = 0
    for row in csv.DictReader(io.StringIO(fallback.text)):
        # The source's 2020 means the 2019-20 champion, while this database
        # stores season start years.
        season = integer(row.get("Season")) - 1
        code = NHL_CHAMPION_TEAM_CODES.get(normalize(row.get("Team") or ""))
        if 2018 <= season <= last_season and code:
            champions[season] = code
            fallback_seasons += 1

    # The public endpoint is complete enough for modern seasons but leaves
    # most pre-1986 playoff rounds absent. The explicit fallback above covers
    # 1986-2019, so only use the slow official calls for recent seasons.
    schedule_first = max(first_season, 2020)
    for season in range(schedule_first, last_season + 1):
        season_key = f"{season}{season + 1}"
        winner_codes: set[str] = set()
        def fetch_schedule(team: str) -> dict | None:
            path = cache_root / team / f"{season_key}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            try:
                response = requests.get(NHL_SCHEDULE_URL.format(team=team, season=season_key), timeout=45)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                payload = response.json()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(response.text, encoding="utf-8")
                return payload
            except requests.RequestException:
                return None

        # Several historical franchises return 404. Parallel requests keep a
        # full season refresh practical and cached responses make later runs fast.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(fetch_schedule, team) for team in NHL_TEAM_CODES]
            payloads = [future.result() for future in as_completed(futures)]
        for payload in payloads:
            if not payload:
                continue
            for game in payload.get("games", []):
                if integer(game.get("gameType")) != 3:
                    continue
                status = game.get("seriesStatus") or {}
                round_number = integer(status.get("round"))
                game_id = str(game.get("id") or "")
                is_final = round_number == 4 or (len(game_id) >= 10 and game_id[8:10] == "04")
                needed = integer(status.get("neededToWin"))
                clinched = needed and (integer(status.get("topSeedWins")) >= needed or integer(status.get("bottomSeedWins")) >= needed)
                if not is_final or not clinched:
                    continue
                away = game.get("awayTeam") or {}; home = game.get("homeTeam") or {}
                away_score = integer(away.get("score")); home_score = integer(home.get("score"))
                if away_score == home_score:
                    continue
                winner_codes.add(str(away.get("abbrev") if away_score > home_score else home.get("abbrev") or ""))
        if len(winner_codes) == 1:
            champions[season] = winner_codes.pop()
        if season % 10 == 0:
            print(f"NHL champion source through {season}: {len(champions)} resolved")
    for season, team_id in NHL_RECENT_CHAMPIONS.items():
        if first_season <= season <= last_season:
            champions[season] = team_id
    # The 2004-05 NHL season was cancelled during the lockout, so no Stanley
    # Cup champion can be credited even if a third-party source includes one.
    champions.pop(2004, None)
    # No Cup was awarded in 1918-19 or the cancelled 2004-05 season.
    no_champion_seasons = {1918, 2004}
    missing = [
        season for season in range(first_season, last_season + 1)
        if season not in champions and season not in no_champion_seasons
    ]
    players, matched = update_nhl_championship_counts(conn, champions, hockeydb_champions)
    conn.execute("DELETE FROM sport_trait_provenance WHERE sport_id='hockey' AND source='nhl_championship_results'")
    conn.execute(
        "INSERT INTO sport_trait_provenance (sport_id, source, source_url, coverage) VALUES (?, ?, ?, ?)",
        ("hockey", "nhl_championship_results", NHL_CHAMPION_FALLBACK_URL, "Hockey Databank playoff participants through 2017 plus season-roster credits for the modern tail"),
    )
    conn.execute("DELETE FROM sport_trait_provenance WHERE sport_id='hockey' AND source='nhl_official_club_schedules'")
    # Replace the generic provenance coverage with the actual resolved scope.
    conn.execute(
        "UPDATE sport_trait_provenance SET coverage=? WHERE sport_id='hockey' AND source='nhl_championship_results'",
        (f"{len(champions)} Stanley Cup champion seasons: Hockey Databank through 2017, {fallback_seasons} fallback seasons, and NHL modern results; no Cup: 1918,2004; unresolved: {','.join(map(str, missing)) or 'none'}",),
    )
    return players, matched, missing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-nhl", action="store_true")
    parser.add_argument("--nfl-first", type=int, default=1999)
    parser.add_argument("--nfl-last", type=int, default=2024)
    parser.add_argument("--nhl-champion-first", type=int, default=1917)
    parser.add_argument("--nhl-champion-last", type=int, default=2025)
    args = parser.parse_args()
    conn = sqlite3.connect(DATABASE)
    try:
        conn.executescript(SCHEMA)
        nba = load_nba(conn)
        nba_awards, nba_award_unresolved = load_nba_awards(conn)
        nba_champions, nba_champion_seasons = load_nba_championships(conn)
        nfl, skipped = load_nfl(conn, args.nfl_first, args.nfl_last)
        nfl_champions, nfl_champion_seasons = load_nfl_championships(conn)
        nhl, unresolved = load_nhl(conn, args.download_nhl)
        nhl_awards, nhl_award_unresolved = load_nhl_awards(conn)
        nhl_champions, nhl_champion_seasons, nhl_champion_missing = load_nhl_championships(
            conn, args.nhl_champion_first, args.nhl_champion_last
        )
        conn.commit()
    finally:
        conn.close()
    print(f"NBA traits: {nba}")
    print(f"NBA awards: {nba_awards}; unresolved source names: {nba_award_unresolved}")
    print(f"NBA championship roster counts: {nba_champions}; champion seasons: {nba_champion_seasons}")
    print(f"NFL traits: {nfl}; unavailable seasons: {skipped or 'none'}")
    print(f"NFL championship roster counts: {nfl_champions}; champion seasons: {nfl_champion_seasons}")
    print(f"NHL traits: {nhl}; unresolved source names: {unresolved}")
    print(f"NHL awards: {nhl_awards}; unresolved Hockey Databank names: {nhl_award_unresolved}")
    print(f"NHL championship roster counts: {nhl_champions}; champion seasons: {nhl_champion_seasons}; missing: {nhl_champion_missing or 'none'}")


if __name__ == "__main__":
    main()
