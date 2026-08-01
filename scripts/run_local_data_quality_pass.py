"""Recompute local metadata from source appearances and write a quality report."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
REPORT = ROOT / "raw" / "data_quality_report.json"


def top_positions(counts: Counter, aliases: dict[str, str] | None = None) -> str | None:
    aliases = aliases or {}
    values = []
    for position, _ in counts.most_common():
        position = aliases.get(position, position)
        if position not in values:
            values.append(position)
        if len(values) == 2:
            break
    return "/".join(values) or None


def football_positions() -> dict[str, Counter]:
    counts = defaultdict(Counter)
    for path in (ROOT / "raw" / "nfl").glob("**/*.csv"):
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                source = (row.get("gsis_id") or row.get("pfr_id") or row.get("espn_id") or "").strip()
                position = (row.get("position") or "").strip().upper()
                if source and position:
                    counts[f"nfl:{source}"][position] += 1
    return counts


def hockey_positions() -> dict[str, Counter]:
    counts = defaultdict(Counter)
    for path in (ROOT / "raw" / "nhl").glob("**/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for group in ("forwards", "defensemen", "goalies"):
            for player in payload.get(group, []):
                pid, pos = player.get("id"), player.get("positionCode")
                if pid and pos:
                    counts[f"nhl:{pid}"][str(pos).upper()] += 1
    return counts


def baseball_positions() -> dict[str, Counter]:
    """Read Lahman's appearance counts so Film Review can require real roles."""
    counts = defaultdict(Counter)
    columns = {
        "G_c": "C", "G_1b": "1B", "G_2b": "2B", "G_3b": "3B", "G_ss": "SS",
        "G_lf": "LF", "G_cf": "CF", "G_rf": "RF", "G_dh": "DH", "G_p": "SP",
    }
    with (ROOT / "raw" / "Appearances.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            player_id = row.get("playerID")
            if not player_id:
                continue
            for column, position in columns.items():
                games = int(row.get(column) or 0)
                if games:
                    counts[player_id][position] += games
    return counts


def write_position_rows(conn: sqlite3.Connection, sport: str, positions: dict[str, Counter],
                        aliases: dict[str, str] | None = None) -> None:
    aliases = aliases or {}
    conn.execute("DELETE FROM sport_player_positions WHERE sport_id=?", (sport,))
    rows = []
    for player_id, counts in positions.items():
        for position, games in counts.items():
            normalized = aliases.get(position, position)
            rows.append((sport, player_id, normalized, games))
    conn.executemany("""INSERT INTO sport_player_positions(sport_id, player_id, position, games)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(sport_id, player_id, position) DO UPDATE SET games=games + excluded.games""", rows)


def main() -> None:
    conn = sqlite3.connect(DATABASE)
    nfl = football_positions()
    nhl = hockey_positions()
    mlb = baseball_positions()
    conn.execute("""CREATE TABLE IF NOT EXISTS sport_player_positions (
        sport_id TEXT NOT NULL, player_id TEXT NOT NULL, position TEXT NOT NULL,
        games INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (sport_id, player_id, position))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_local_player_positions
                    ON sport_player_positions(sport_id, position, player_id)""")
    write_position_rows(conn, "baseball", mlb)
    write_position_rows(conn, "football", nfl)
    write_position_rows(conn, "hockey", nhl, {"R": "RW", "L": "LW"})
    basketball = defaultdict(Counter)
    for player_id, position in conn.execute("SELECT player_id, primary_pos FROM sport_players WHERE sport_id='basketball'"):
        if position:
            for item in position.split("/"):
                basketball[player_id][item] += 1
    write_position_rows(conn, "basketball", basketball)
    conn.executemany("UPDATE sport_players SET primary_pos=? WHERE sport_id='football' AND player_id=?",
                     [(top_positions(counts), pid) for pid, counts in nfl.items()])
    conn.executemany("UPDATE sport_players SET primary_pos=? WHERE sport_id='hockey' AND player_id=?",
                     [(top_positions(counts, {"R": "RW", "L": "LW", "D": "D"}), pid) for pid, counts in nhl.items()])
    conn.execute("""UPDATE sport_players SET debut_year=(SELECT MIN(season) FROM sport_appearances a WHERE a.sport_id=sport_players.sport_id AND a.player_id=sport_players.player_id),
                   final_year=(SELECT MAX(season) FROM sport_appearances a WHERE a.sport_id=sport_players.sport_id AND a.player_id=sport_players.player_id)""")
    conn.commit()
    report = {"sports": {}, "checks": {}}
    for sport in ("baseball", "football", "basketball", "hockey"):
        appearances, players, teams, first, last = conn.execute("""
            SELECT COUNT(*), COUNT(DISTINCT player_id), COUNT(DISTINCT team_id || ':' || season), MIN(season), MAX(season)
            FROM sport_appearances WHERE sport_id=?""", (sport,)).fetchone()
        orphans = conn.execute("""SELECT COUNT(*) FROM sport_appearances a
          LEFT JOIN sport_players p ON p.sport_id=a.sport_id AND p.player_id=a.player_id
          LEFT JOIN sport_teams t ON t.sport_id=a.sport_id AND t.team_id=a.team_id AND t.season=a.season
          WHERE a.sport_id=? AND (p.player_id IS NULL OR t.team_id IS NULL)""", (sport,)).fetchone()[0]
        report["sports"][sport] = {"appearances": appearances, "players": players, "team_seasons": teams,
                                    "first_season": first, "last_season": last, "orphan_appearances": orphans}
    report["checks"]["nba_exhibition_appearances"] = conn.execute("""
      SELECT COUNT(*) FROM sport_appearances a JOIN sport_teams t ON t.sport_id=a.sport_id AND t.team_id=a.team_id AND t.season=a.season
      WHERE a.sport_id='basketball' AND (lower(t.name) LIKE '%all star%' OR lower(t.name) LIKE '%rising stars%' OR lower(t.name)='world')""").fetchone()[0]
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    conn.close()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
