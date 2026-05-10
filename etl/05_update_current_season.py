#!/usr/bin/env python3
"""
05_update_current_season.py — pull current-season appearances from statsapi.mlb.com.

Lahman updates once a year (after the season ends). During the season,
trades happen, players get called up, etc. This script keeps the current
season fresh by hitting MLB's own JSON API. Run it daily during the season
(crontab, GitHub Actions, Supabase scheduled function — all fine).

We use mlbam_id as the bridge: statsapi gives us mlbam_ids, we look up
our player_id via the mlbam_id column in the players table. New players
that don't exist in Lahman yet (because they debuted this season) are
created on the fly.

After updating appearances, we re-run 03_build_teammates.py with --season
to incrementally rebuild only the current season's teammate edges.
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

import requests

API = "https://statsapi.mlb.com/api/v1"


def get_mlb_teams(season: int) -> list[dict]:
    """Fetch all 30 MLB teams active in the given season."""
    r = requests.get(f"{API}/teams", params={"sportId": 1, "season": season}, timeout=30)
    r.raise_for_status()
    return [t for t in r.json()["teams"] if t.get("active")]


def get_team_roster(team_id: int, season: int) -> list[dict]:
    """Fetch every player who appeared on the team's 40-man roster this season."""
    r = requests.get(
        f"{API}/teams/{team_id}/roster",
        params={"rosterType": "fullSeason", "season": season},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("roster", [])


def get_player_stats(mlbam_id: int, season: int) -> dict:
    """Get the player's regular-season game count for this season."""
    r = requests.get(
        f"{API}/people/{mlbam_id}/stats",
        params={"stats": "season", "group": "hitting,pitching", "season": season},
        timeout=30,
    )
    if not r.ok:
        return {"games_total": 0, "games_pitched": 0, "games_batted": 0}
    data = r.json()
    g_p = g_b = 0
    for split_group in data.get("stats", []):
        group = split_group.get("group", {}).get("displayName", "").lower()
        for split in split_group.get("splits", []):
            stat = split.get("stat", {})
            games = int(stat.get("gamesPlayed") or 0)
            if group == "pitching":
                g_p = max(g_p, games)
            else:
                g_b = max(g_b, games)
    # games_total is roughly max(g_b, g_p) — a player can be both
    # but each game only counts once in real life.
    return {
        "games_total": max(g_b, g_p),
        "games_pitched": g_p,
        "games_batted": g_b,
    }


# Map statsapi's 3-letter team abbreviations to Lahman's teamID where they differ.
# Most match (NYY, BOS, etc.), but a few don't:
LAHMAN_TEAM_OVERRIDES = {
    "NYY": "NYA",   # Lahman uses NYA for Yankees
    "NYM": "NYN",   # NYN for Mets
    "LAD": "LAN",   # LAN for Dodgers
    "LAA": "ANA",   # ANA for Angels (post-2005 Lahman uses LAA actually; verify)
    "CWS": "CHA",   # CHA for White Sox
    "CHC": "CHN",   # CHN for Cubs
    "STL": "SLN",   # SLN for Cardinals
    "SD":  "SDN",
    "SF":  "SFN",
    "WSH": "WAS",
    "TB":  "TBA",
    "KC":  "KCA",
}


def find_or_create_player(conn: sqlite3.Connection, p: dict) -> str:
    """Look up player_id by mlbam_id; create a stub if new."""
    mlbam_id = p["id"]
    row = conn.execute(
        "SELECT player_id FROM players WHERE mlbam_id = ?", (mlbam_id,)
    ).fetchone()
    if row:
        return row[0]

    # Brand-new player. Generate a temp player_id we'll reconcile with
    # Lahman's playerID format on the next annual rebuild. Format mimics
    # Lahman: lastname[:5] + firstname[:2] + 01.
    last = (p.get("lastName") or "unk").lower()
    first = (p.get("firstName") or "")[:2].lower()
    stub_id = f"{last[:5]}{first}99_mlbam{mlbam_id}"

    conn.execute(
        """INSERT INTO players
           (player_id, mlbam_id, name_first, name_last, debut_year)
           VALUES (?, ?, ?, ?, ?)""",
        (stub_id, mlbam_id, p.get("firstName"), p.get("lastName"), p.get("mlbDebutDate", "")[:4] or None),
    )
    return stub_id


def update_season(conn: sqlite3.Connection, season: int):
    teams = get_mlb_teams(season)
    print(f"  found {len(teams)} active teams for {season}")

    seen = set()
    for team in teams:
        team_abbr = team.get("abbreviation") or team.get("teamCode", "").upper()
        lahman_team_id = LAHMAN_TEAM_OVERRIDES.get(team_abbr, team_abbr)
        team_name = team.get("name")
        franchise = team.get("franchiseName") or team_name

        # Ensure the team-season row exists.
        conn.execute(
            "INSERT OR IGNORE INTO franchises (franchise_id, name) VALUES (?, ?)",
            (lahman_team_id, franchise),
        )
        conn.execute(
            """INSERT OR REPLACE INTO teams
               (team_id, season, franchise_id, league, name)
               VALUES (?, ?, ?, ?, ?)""",
            (
                lahman_team_id,
                season,
                lahman_team_id,
                team.get("league", {}).get("abbreviation"),
                team_name,
            ),
        )

        roster = get_team_roster(team["id"], season)
        print(f"    {team_abbr}: {len(roster)} players")
        for entry in roster:
            person = entry["person"]
            player_id = find_or_create_player(conn, person)
            stats = get_player_stats(person["id"], season)
            if stats["games_total"] == 0:
                continue  # haven't appeared yet — exclude per teammate rule
            conn.execute(
                """INSERT OR REPLACE INTO appearances
                   (player_id, team_id, season, games_total, games_pitched, games_batted)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (player_id, lahman_team_id, season,
                 stats["games_total"], stats["games_pitched"], stats["games_batted"]),
            )
            seen.add((player_id, lahman_team_id, season))

        # Be polite to MLB's API.
        time.sleep(0.2)

    # Anyone we previously had on a roster for this season but didn't see now
    # was waived/released. We keep their existing row (they DID play earlier);
    # statsapi still reports their stats, so the row stays accurate.

    conn.execute(
        "INSERT OR REPLACE INTO data_provenance (source, season, row_count) VALUES (?, ?, ?)",
        ("statsapi", season, len(seen)),
    )
    conn.commit()
    print(f"  updated {len(seen):,} appearance rows for {season}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db/base2nerdle.sqlite")
    ap.add_argument("--season", type=int, required=True)
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}. Run 02_load_lahman.py first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    update_season(conn, args.season)
    conn.close()

    print(f"\nDone. Now rebuild teammates for {args.season}:")
    print(f"  python3 etl/03_build_teammates.py --season {args.season}")


if __name__ == "__main__":
    sys.exit(main() or 0)
