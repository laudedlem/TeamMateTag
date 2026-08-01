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
import sqlite3
import zipfile
from collections import defaultdict
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
HOCKEYDB_MASTER_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/Master.csv"
HOCKEYDB_AWARDS_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/AwardsPlayers.csv"

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


def load_nba(conn: sqlite3.Connection) -> int:
    if not NBA_STATS.exists():
        raise RuntimeError(f"Missing NBA source file: {NBA_STATS}")
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with NBA_STATS.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("gameType") or "").lower() != "regular season":
                continue
            player_id = (row.get("personId") or "").strip()
            if not player_id:
                continue
            stats = totals[f"nba:{player_id}"]
            stats["games"] += 1
            stats["points"] += integer(row.get("points"))
            stats["goals"] += integer(row.get("threePointersMade"))
            stats["assists"] += integer(row.get("assists"))
    rows = [("basketball", player_id, stat["games"], stat["points"], stat["goals"], stat["assists"], 0, 0, 0, 0, 0, 0, "kaggle_eoinamoore_nba") for player_id, stat in totals.items()]
    replace_traits(conn, "basketball", rows, "kaggle_eoinamoore_nba", "https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores", "regular-season box scores, 1947-present source archive")
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


def load_nfl(conn: sqlite3.Connection, first_season: int, last_season: int) -> tuple[int, list[int]]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
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
    rows = [("football", player_id, int(stat["games"]), 0, 0, 0, int(stat["touchdowns"]), int(stat["passing_tds"]), int(stat["rushing_tds"]), int(stat["receiving_tds"]), stat["sacks"], int(stat["interceptions"]), "nflverse_player_stats") for player_id, stat in totals.items()]
    replace_traits(conn, "football", rows, "nflverse_player_stats", "https://github.com/nflverse/nflverse-data/releases/tag/player_stats", f"weekly player stats, {first_season}-{last_season}; unavailable seasons: {','.join(map(str, skipped)) or 'none'}")
    return len(rows), skipped


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
    return len(rows), unresolved


def load_nhl_awards(conn: sqlite3.Connection) -> tuple[int, int]:
    """Load historical Hart, Calder, and All-Star counts through HockeyDB's coverage."""
    master = requests.get(HOCKEYDB_MASTER_URL, timeout=90)
    awards = requests.get(HOCKEYDB_AWARDS_URL, timeout=90)
    master.raise_for_status(); awards.raise_for_status()
    index = make_name_index(conn, "hockey")
    source_to_local: dict[str, str] = {}
    unresolved = 0
    for row in csv.DictReader(io.StringIO(master.text)):
        given = (row.get("nameGiven") or "").strip()
        last = (row.get("lastName") or "").strip()
        name = " ".join(part for part in (given, last) if part).strip()
        player_ids = index.get(normalize(name), [])
        if len(player_ids) != 1 and given and last:
            # Hockey Databank often records middle names while the local NHL
            # roster source uses the player's common first name.
            player_ids = index.get(normalize(f"{given.split()[0]} {last}"), [])
        if len(player_ids) == 1:
            source_to_local[row["playerID"]] = player_ids[0]
        elif name:
            unresolved += 1
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in csv.DictReader(io.StringIO(awards.text)):
        player_id = source_to_local.get(row.get("playerID") or "")
        if not player_id:
            continue
        award = row.get("award") or ""
        if award == "Hart": counts[player_id]["mvp"] += 1
        elif award == "Calder": counts[player_id]["roty"] += 1
        elif award in {"First Team All-Star", "Second Team All-Star"}: counts[player_id]["all_star"] += 1
    conn.executemany(
        """UPDATE sport_player_traits
              SET mvp_count=?, roty_count=?, all_star_count=?, updated_at=CURRENT_TIMESTAMP
            WHERE sport_id='hockey' AND player_id=?""",
        [(values["mvp"], values["roty"], values["all_star"], player_id) for player_id, values in counts.items()],
    )
    conn.execute("DELETE FROM sport_trait_provenance WHERE sport_id='hockey' AND source='hockey_databank_awards'")
    conn.execute(
        "INSERT INTO sport_trait_provenance (sport_id, source, source_url, coverage) VALUES (?, ?, ?, ?)",
        ("hockey", "hockey_databank_awards", HOCKEYDB_AWARDS_URL, "Hart, Calder, and First/Second Team All-Star counts through Hockey Databank coverage"),
    )
    return len(counts), unresolved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-nhl", action="store_true")
    parser.add_argument("--nfl-first", type=int, default=1999)
    parser.add_argument("--nfl-last", type=int, default=2024)
    args = parser.parse_args()
    conn = sqlite3.connect(DATABASE)
    try:
        conn.executescript(SCHEMA)
        nba = load_nba(conn)
        nba_awards, nba_award_unresolved = load_nba_awards(conn)
        nfl, skipped = load_nfl(conn, args.nfl_first, args.nfl_last)
        nhl, unresolved = load_nhl(conn, args.download_nhl)
        nhl_awards, nhl_award_unresolved = load_nhl_awards(conn)
        conn.commit()
    finally:
        conn.close()
    print(f"NBA traits: {nba}")
    print(f"NBA awards: {nba_awards}; unresolved source names: {nba_award_unresolved}")
    print(f"NFL traits: {nfl}; unavailable seasons: {skipped or 'none'}")
    print(f"NHL traits: {nhl}; unresolved source names: {unresolved}")
    print(f"NHL awards: {nhl_awards}; unresolved Hockey Databank names: {nhl_award_unresolved}")


if __name__ == "__main__":
    main()
