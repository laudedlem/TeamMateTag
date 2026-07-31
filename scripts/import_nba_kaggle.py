"""Import the CC0 Kaggle NBA history dataset into the local TeamMateTag DB.

Source: eoinamoore/historical-nba-data-and-player-box-scores
Place the downloaded Kaggle archive in raw/nba_kaggle/ and extract it there,
then run this script. The source is deliberately kept out of Git.

This replaces the 2002-forward SportsDataverse NBA rows with player-team-season
appearances built from PlayerStatistics.csv, covering 1947 onward.
"""
from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

from build_local_sports_dataset import ROOT, SCHEMA, key, write_sport


DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
SOURCE_DIR = ROOT / "raw" / "nba_kaggle"
SOURCE_URL = "https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores"


def field(row: dict, *names: str) -> str:
    lowered = {name.lower(): value for name, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def season_year(value: str) -> int | None:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) >= 4:
        return int(digits[:4])
    return None


def nba_season(row: dict) -> int | None:
    """Use the season start year; the Kaggle player file records game dates."""
    date = field(row, "gameDate", "gameDateTimeEst")
    year = season_year(date)
    if not year:
        return None
    month = int(date[5:7]) if len(date) >= 7 and date[5:7].isdigit() else 10
    return year - 1 if month <= 6 else year


def main() -> None:
    stats_path = SOURCE_DIR / "PlayerStatistics.csv"
    players_path = SOURCE_DIR / "Players.csv"
    if not stats_path.exists():
        raise SystemExit(
            f"Missing {stats_path}. Download and extract the Kaggle dataset first."
        )

    bio = {}
    if players_path.exists():
        with players_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                source_id = field(row, "playerid", "player_id", "id")
                name = " ".join(part for part in (field(row, "firstName"), field(row, "lastName")) if part)
                name = name or field(row, "player", "playername", "name", "display_name")
                if source_id and name:
                    bio[source_id] = (name, field(row, "position", "pos"))

    players, teams, appearances, positions = {}, {}, defaultdict(int), defaultdict(lambda: defaultdict(int))
    with stats_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            season = nba_season(row) or season_year(field(row, "season", "seasonyear", "season_year", "year"))
            team = field(row, "playerteamId", "team", "teamabbreviation", "team_abbreviation", "teamid", "team_id")
            source_id = field(row, "playerid", "player_id", "personid", "person_id")
            name = " ".join(part for part in (field(row, "firstName"), field(row, "lastName")) if part)
            name = name or field(row, "player", "playername", "player_name", "name")
            if not season or not team or not (source_id or name):
                continue
            source_id = source_id or key(name)
            name, position = bio.get(source_id, (name, field(row, "position", "pos")))
            if not name:
                continue
            pid = f"nba:{source_id}"
            first, _, last = name.rpartition(" ")
            previous = players.get(pid)
            debut = min(previous[5], season) if previous else season
            final = max(previous[6], season) if previous else season
            players[pid] = (source_id, name, first or None, last or name, None, debut, final, position or None)
            position = field(row, "startingPosition", "position", "pos")
            if position:
                positions[pid][position.upper()] += 1
            team_name = " ".join(part for part in (field(row, "playerteamCity"), field(row, "playerteamName")) if part)
            teams[(team, season)] = (team, team_name or team)
            appearances[(pid, team, season)] = 1

    if not appearances:
        raise SystemExit("No NBA player-team-season rows were recognized. Check the Kaggle CSV column names.")
    for pid, values in list(players.items()):
        top_positions = sorted(positions[pid], key=positions[pid].get, reverse=True)[:2]
        players[pid] = (*values[:-1], "/".join(top_positions) or values[-1])
    conn = sqlite3.connect(DATABASE)
    try:
        conn.executescript(SCHEMA)
        write_sport(
            conn, "basketball", "Basketball", "NBA", players, teams, appearances,
            [("kaggle_eoinamoore_nba", None, SOURCE_URL, len(appearances))],
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
