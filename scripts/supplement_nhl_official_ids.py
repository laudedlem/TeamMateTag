"""Resolve NHL career records through official NHL player IDs and game logs."""
from __future__ import annotations

import json
import re
import sqlite3
import csv
import io
import zipfile
from pathlib import Path

import requests

from build_local_sports_dataset import DEFAULT_DB, ROOT, key
from name_normalize import normalize


CACHE = ROOT / "raw" / "nhl_identity"
SEARCH_URL = "https://search.d3.nhle.com/api/v1/search/player"
GAME_LOG_URL = "https://api-web.nhle.com/v1/player/{player_id}/game-log/{season_id}/2"
LANDING_URL = "https://api-web.nhle.com/v1/player/{player_id}/landing"
SOURCE = "kaggle_nhl_stat_audit"
SOURCE_ARCHIVE = ROOT / "raw" / "nhl_player_database.zip"

# The Kaggle career source preserves native spellings while the official NHL
# search index frequently uses an English transliteration or punctuation-free
# form. These aliases are reviewed name variants, never player-ID guesses.
OFFICIAL_SEARCH_ALIASES = {
    "Vasiliy Ponomarev": "Vasily Ponomarev",
    "Nikita Okhotyuk": "Nikita Okhotiuk",
    "Jonas Røndbjerg": "Jonas Rondbjerg",
    "Mads Søgaard": "Mads Sogaard",
    "Frédéric St. Denis": "Frederic St-Denis",
    "Jonas Holøs": "Jonas Holos",
    "Mikkel Bødker": "Mikkel Boedker",
    "Kaspars Astašenko": "Kaspars Astashenko",
    "Viktors Ignatjevs": "Viktor Ignatyev",
    "Sandis Ozoliņš": "Sandis Ozolinsh",
}

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
    search_name = OFFICIAL_SEARCH_ALIASES.get(name, name)
    filename = re.sub(r"[^a-z0-9]+", "_", normalize(search_name)).strip("_") + ".json"
    rows = cached_json(CACHE / "search" / filename, SEARCH_URL, {"culture": "en-us", "limit": 20, "q": search_name})
    return [row for row in rows if normalize(row.get("name") or "") == normalize(search_name)]


def game_log(player_id: int, season: int) -> list[dict]:
    payload = cached_json(CACHE / "games" / f"{player_id}_{season}.json", GAME_LOG_URL.format(player_id=player_id, season_id=f"{season}{season + 1}"))
    return payload.get("gameLog", [])


def source_profiles() -> dict[tuple[str, int], dict[str, set[int] | set[str]]]:
    """Keep source position, first NHL year, and career games as evidence."""
    profiles: dict[tuple[str, int], dict[str, set[int] | set[str]]] = {}
    if not SOURCE_ARCHIVE.exists():
        return profiles
    with zipfile.ZipFile(SOURCE_ARCHIVE) as archive:
        for filename in archive.namelist():
            if not filename.lower().endswith(".csv"):
                continue
            with archive.open(filename) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")):
                    name = (row.get("name") or row.get("player") or "").strip()
                    try:
                        season = int(row.get("first") or "") - 1
                    except ValueError:
                        continue
                    position = (row.get("position") or "G").upper()
                    try:
                        games = int(row.get("games") or "")
                    except ValueError:
                        games = 0
                    profile = profiles.setdefault((normalize(name), season), {"positions": set(), "games": set()})
                    profile["positions"].add(position)
                    if games:
                        profile["games"].add(games)
    return profiles


def position_matches(source_positions: set[str], candidate_position: str) -> bool:
    candidate_position = (candidate_position or "").upper()
    if not source_positions:
        return True
    return any(
        source == candidate_position
        or source == "F" and candidate_position in {"C", "L", "R"}
        for source in source_positions
    )


def career_games(player_id: int) -> int:
    """Return official regular-season games; cached for deterministic reruns."""
    payload = cached_json(CACHE / "landing" / f"{player_id}.json", LANDING_URL.format(player_id=player_id))
    return int(payload.get("careerTotals", {}).get("regularSeason", {}).get("gamesPlayed") or 0)


def select_candidate(matches: list[tuple[dict, list[dict]]], source_games: set[int]) -> tuple[dict, list[dict]] | None:
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if not source_games:
        return None
    expected = max(source_games)
    ranked = sorted((abs(career_games(int(candidate["playerId"])) - expected), candidate, logs) for candidate, logs in matches)
    # The source is a completed snapshot, so tolerate a small current-season
    # difference but require a meaningful gap from the next same-name player.
    if ranked[0][0] <= 12 and (len(ranked) == 1 or ranked[1][0] - ranked[0][0] >= 10):
        _, candidate, logs = ranked[0]
        return candidate, logs
    return None


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
        profiles = source_profiles()
        resolved = 0
        for source, reference_key, name, season in refs:
            profile = profiles.get((normalize(name), season), {"positions": set(), "games": set()})
            matches = []
            for candidate in search_candidates(name):
                if not position_matches(profile["positions"], candidate.get("positionCode") or ""):
                    continue
                player_numeric_id = candidate.get("playerId")
                if not player_numeric_id:
                    continue
                logs = game_log(int(player_numeric_id), season)
                if logs:
                    matches.append((candidate, logs))
            selected = select_candidate(matches, profile["games"])
            if not selected:
                continue
            candidate, logs = selected
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
