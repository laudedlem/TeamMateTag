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


def main() -> None:
    conn = sqlite3.connect(DATABASE)
    nfl = football_positions()
    nhl = hockey_positions()
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
