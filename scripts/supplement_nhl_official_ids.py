"""Resolve NHL career records through official NHL player IDs and game logs."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import requests

from build_local_sports_dataset import DEFAULT_DB, ROOT, key
from name_normalize import normalize


CACHE = ROOT / "raw" / "nhl_identity"
SEARCH_URL = "https://search.d3.nhle.com/api/v1/search/player"
GAME_LOG_URL = "https://api-web.nhle.com/v1/player/{player_id}/game-log/{season_id}/2"
SOURCE = "kaggle_nhl_stat_audit"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sport_player_external_ids (
  sport_id TEXT NOT NULL, player_id TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL,
  PRIMARY KEY (sport_id, source, external_id), UNIQUE (sport_id, player_id, source)
);
"""


def cached_json(path: Path, url: str, params: dict | None = None) -> object:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    path.write_text(response.text, encoding="utf-8")
    return response.json()


def search_candidates(name: str) -> list[dict]:
    filename = re.sub(r"[^a-z0-9]+", "_", normalize(name)).strip("_") + ".json"
    rows = cached_json(CACHE / "search" / filename, SEARCH_URL, {"culture": "en-us", "limit": 20, "q": name})
    return [row for row in rows if normalize(row.get("name") or "") == normalize(name)]


def game_log(player_id: int, season: int) -> list[dict]:
    payload = cached_json(CACHE / "games" / f"{player_id}_{season}.json", GAME_LOG_URL.format(player_id=player_id, season_id=f"{season}{season + 1}"))
    return payload.get("gameLog", [])


def main() -> None:
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        conn.executescript(SCHEMA)
        refs = conn.execute(
            """SELECT r.source, r.reference_key, r.source_name, r.season
                FROM source_player_references r
                WHERE r.sport_id='hockey' AND r.source=?
                  AND NOT EXISTS (SELECT 1 FROM player_identity_claims c WHERE c.sport_id=r.sport_id AND c.source=r.source AND c.reference_key=r.reference_key AND c.status='accepted')
                  AND NOT EXISTS (SELECT 1 FROM source_reference_dispositions d WHERE d.sport_id=r.sport_id AND d.source=r.source AND d.reference_key=r.reference_key)""",
            (SOURCE,),
        ).fetchall()
        resolved = 0
        for source, reference_key, name, season in refs:
            matches = []
            for candidate in search_candidates(name):
                player_numeric_id = candidate.get("playerId")
                if not player_numeric_id:
                    continue
                logs = game_log(int(player_numeric_id), season)
                if logs:
                    matches.append((candidate, logs))
            if len(matches) != 1:
                continue
            candidate, logs = matches[0]
            numeric_id = str(candidate["playerId"])
            player_id = f"nhl:{numeric_id}"
            position = candidate.get("positionCode") or "?"
            first, _, last = name.rpartition(" ")
            conn.execute(
                """INSERT OR IGNORE INTO sport_players
                   (sport_id, player_id, external_id, display_name, first_name, last_name, debut_year, final_year, primary_pos)
                   VALUES ('hockey', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (player_id, numeric_id, name, first or None, last or name, season, season, position),
            )
            conn.execute("INSERT OR REPLACE INTO sport_player_external_ids VALUES ('hockey', ?, 'nhl_official', ?)", (player_id, numeric_id))
            teams = {game.get("teamAbbrev") for game in logs if game.get("teamAbbrev")}
            for team_id in teams:
                team_name = conn.execute(
                    "SELECT name FROM sport_teams WHERE sport_id='hockey' AND team_id=? ORDER BY season DESC LIMIT 1", (team_id,)
                ).fetchone()
                team_name = team_name[0] if team_name else team_id
                conn.execute("INSERT OR IGNORE INTO sport_franchises VALUES ('hockey', ?, ?)", (team_id, team_name))
                conn.execute("INSERT OR IGNORE INTO sport_teams VALUES ('hockey', ?, ?, ?, ?)", (team_id, season, team_id, team_name))
                conn.execute("INSERT INTO sport_appearances VALUES ('hockey', ?, ?, ?, ?) ON CONFLICT(sport_id,player_id,team_id,season) DO UPDATE SET games_total=MAX(games_total,excluded.games_total)", (player_id, team_id, season, len([game for game in logs if game.get('teamAbbrev') == team_id])))
            conn.execute("INSERT INTO sport_players_searchable VALUES ('hockey', ?, ?, ?, ?, ?, ?, 0) ON CONFLICT(sport_id,player_id) DO UPDATE SET career_games=MAX(career_games,excluded.career_games)", (player_id, name, f"{position}, {season}-{season}", key(name), key(last or name), len(logs)))
            conn.execute("INSERT OR REPLACE INTO player_identity_claims (sport_id,source,reference_key,player_id,status,method,confidence,evidence,reviewed_by) VALUES ('hockey', ?, ?, ?, 'accepted', 'nhl_official_search_game_log', 100, ?, 'source_identifier')", (source, reference_key, player_id, f"Official NHL ID {numeric_id}; {len(logs)} game(s) in {season}-{season + 1}."))
            for category, fact_season, source_url in conn.execute("SELECT fact_type,season,source_url FROM source_fact_observations WHERE sport_id='hockey' AND source=? AND reference_key=?", (source, reference_key)):
                conn.execute("INSERT OR REPLACE INTO sport_honors VALUES ('hockey', ?, ?, ?, ?, ?, ?)", (player_id, category, fact_season, name, source_url, source))
                conn.execute("DELETE FROM sport_honor_unresolved WHERE sport_id='hockey' AND category=? AND season=? AND source_name=? AND source=?", (category, fact_season, name, source))
            resolved += 1
        conn.commit()
    finally:
        conn.close()
    print(f"Resolved {resolved:,} NHL career records through official player IDs and game logs.")


if __name__ == "__main__":
    main()
