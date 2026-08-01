"""Resolve verified NBA historical identities absent or ambiguous in the base archive.

The award audit intentionally keeps ambiguous source names out of the canonical
honors table.  This small, evidence-backed supplement records the handful of
pre-merger identities that cannot be reconstructed from the player archive.
"""
from __future__ import annotations

import sqlite3

from build_local_sports_dataset import DEFAULT_DB, key
from name_normalize import normalize
from reconcile_local_identities import reference_key


SCHEMA = """
CREATE TABLE IF NOT EXISTS sport_player_external_ids (
  sport_id TEXT NOT NULL, player_id TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL,
  PRIMARY KEY (sport_id, source, external_id), UNIQUE (sport_id, player_id, source)
);
"""


# Each rule has a source-specific identity and a page that documents the
# player, club, and relevant era.  Do not add name-only guesses here.
RULES = (
    {
        "name": "Eddie Johnson",
        "seasons": (1980, 1981),
        "player_id": "nba:77144",
        "method": "nba_official_historical_identity",
        "evidence": "NBA.com 1981 All-Star recap and NBA obituary identify the 1980-81 Hawks guard as two-time All-Star Eddie Johnson.",
        "url": "https://www.nba.com/news/history-all-star-recap-1981",
    },
    {
        "name": "Alex Groza",
        "seasons": (1950, 1951),
        "player_id": "nba:76897",
        "method": "nba_official_historical_identity",
        "evidence": "NBA.com player record and Basketball-Reference document Groza's Indianapolis Olympians career in 1949-50 and 1950-51.",
        "url": "https://www.nba.com/player/76897/alex-groza",
    },
)


def ensure_alex_groza(conn: sqlite3.Connection) -> None:
    """Add the two BAA/NBA-era Olympians seasons missing from the base archive."""
    player_id = "nba:76897"
    conn.execute(
        """INSERT OR IGNORE INTO sport_players
           (sport_id,player_id,external_id,display_name,first_name,last_name,debut_year,final_year,primary_pos)
           VALUES ('basketball', ?, '76897', 'Alex Groza', 'Alex', 'Groza', 1949, 1950, 'C')""",
        (player_id,),
    )
    conn.execute("INSERT OR REPLACE INTO sport_player_external_ids VALUES ('basketball', ?, 'nba_official', '76897')", (player_id,))
    conn.execute("INSERT OR IGNORE INTO sport_franchises VALUES ('basketball', 'INO', 'Indianapolis Olympians')")
    for season, games in ((1949, 68), (1950, 66)):
        conn.execute(
            "INSERT OR IGNORE INTO sport_teams VALUES ('basketball', 'INO', ?, 'INO', 'Indianapolis Olympians')",
            (season,),
        )
        conn.execute(
            "INSERT INTO sport_appearances VALUES ('basketball', ?, 'INO', ?, ?) "
            "ON CONFLICT(sport_id,player_id,team_id,season) DO UPDATE SET games_total=MAX(games_total,excluded.games_total)",
            (player_id, season, games),
        )
    conn.execute(
        """INSERT INTO sport_players_searchable VALUES ('basketball', ?, 'Alex Groza', 'C, 1949-1950', ?, ?, 134, 0)
           ON CONFLICT(sport_id,player_id) DO UPDATE SET display_name=excluded.display_name,
             disambiguation=excluded.disambiguation,career_games=MAX(career_games,excluded.career_games)""",
        (player_id, key("Alex Groza"), key("Groza")),
    )


def promote(conn: sqlite3.Connection, rule: dict) -> int:
    """Claim matching audit references and copy their facts into canonical honors."""
    resolved = 0
    for season in rule["seasons"]:
        ref = reference_key(rule["name"], season)
        exists = conn.execute(
            "SELECT 1 FROM source_player_references WHERE sport_id='basketball' AND source='nba_award_audit' AND reference_key=?",
            (ref,),
        ).fetchone()
        if not exists:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO player_identity_claims
               (sport_id,source,reference_key,player_id,status,method,confidence,evidence,reviewed_by)
               VALUES ('basketball','nba_award_audit',? ,?,'accepted',?,100,?,'historical_source_review')""",
            (ref, rule["player_id"], rule["method"], f"{rule['evidence']} {rule['url']}"),
        )
        facts = conn.execute(
            """SELECT fact_type,season,source_url FROM source_fact_observations
               WHERE sport_id='basketball' AND source='nba_award_audit' AND reference_key=?""",
            (ref,),
        ).fetchall()
        for honor, fact_season, source_url in facts:
            conn.execute(
                "INSERT OR REPLACE INTO sport_honors VALUES ('basketball', ?, ?, ?, ?, ?, 'nba_award_audit')",
                (rule["player_id"], honor, fact_season, rule["name"], source_url),
            )
            conn.execute(
                """DELETE FROM sport_honor_unresolved WHERE sport_id='basketball' AND source='nba_award_audit'
                   AND category=? AND season=? AND source_name=?""",
                (honor, fact_season, rule["name"]),
            )
        resolved += 1
    return resolved


def main() -> None:
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        conn.executescript(SCHEMA)
        ensure_alex_groza(conn)
        resolved = sum(promote(conn, rule) for rule in RULES)
        conn.commit()
    finally:
        conn.close()
    print(f"Resolved {resolved} NBA historical award references.")


if __name__ == "__main__":
    main()
