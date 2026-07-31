"""Backfill NHL season memberships from official player landing records.

Club roster snapshots can omit inactive, late, or historical entries. The NHL
player landing endpoint exposes season totals with the actual club name.
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
API = "https://api-web.nhle.com/v1/player/{}/landing"


def season_start(value: int) -> int:
    return int(str(value)[:4])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    conn = sqlite3.connect(DATABASE)
    players = conn.execute("SELECT player_id, external_id FROM sport_players WHERE sport_id = 'hockey'").fetchall()
    if args.limit: players = players[:args.limit]
    team_rows = conn.execute("SELECT team_id, season, name FROM sport_teams WHERE sport_id = 'hockey'").fetchall()
    name_to_team = {(name.lower(), season): team_id for team_id, season, name in team_rows}
    # Current local names are code-based. Keep the verified aliases needed by player landing data.
    aliases = {('winnipeg jets', 2025): 'WPG'}
    added = 0
    for index, (player_id, external_id) in enumerate(players, 1):
        try:
            payload = requests.get(API.format(external_id), timeout=20).json()
        except requests.RequestException:
            continue
        for row in payload.get('seasonTotals', []):
            if row.get('leagueAbbrev') != 'NHL' or row.get('gameTypeId') != 2 or season_start(row.get('season', 0)) != args.season:
                continue
            name = (row.get('teamName') or {}).get('default', '').lower()
            team_id = name_to_team.get((name, args.season)) or aliases.get((name, args.season))
            if team_id:
                before = conn.total_changes
                conn.execute("INSERT OR IGNORE INTO sport_appearances VALUES ('hockey', ?, ?, ?, ?)",
                             (player_id, team_id, args.season, row.get('gamesPlayed') or 1))
                added += int(conn.total_changes > before)
        if index % 100 == 0:
            conn.commit(); print(f"checked {index:,}/{len(players):,}; additions {added:,}")
        time.sleep(0.03)
    conn.commit(); conn.close()
    print(f"NHL player-season refresh completed; additions {added:,}")


if __name__ == '__main__':
    main()
