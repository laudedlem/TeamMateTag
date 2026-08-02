"""Build query-oriented views and audit tables over the canonical sport data."""
from __future__ import annotations

import sqlite3

from build_local_sports_dataset import DEFAULT_DB


SQL = """
DROP VIEW IF EXISTS game_player_catalog;
CREATE VIEW game_player_catalog AS
SELECT p.sport_id,p.player_id,p.display_name,p.first_name,p.last_name,
       p.debut_year,p.final_year,p.primary_pos,s.career_games,s.teammate_count,
       t.mvp_count,t.roty_count,t.all_star_count,t.championship_count,
       t.career_home_runs,t.career_strikeouts,t.career_points,t.career_goals,t.career_touchdowns
  FROM sport_players p
  LEFT JOIN sport_players_searchable s ON s.sport_id=p.sport_id AND s.player_id=p.player_id
  LEFT JOIN sport_player_traits t ON t.sport_id=p.sport_id AND t.player_id=p.player_id;

DROP VIEW IF EXISTS game_team_season_catalog;
CREATE VIEW game_team_season_catalog AS
SELECT a.sport_id,a.player_id,a.team_id,a.season,a.games_total,
       t.franchise_id,t.name AS team_name
  FROM sport_appearances a
  JOIN sport_teams t ON t.sport_id=a.sport_id AND t.team_id=a.team_id AND t.season=a.season
  JOIN sports s ON s.sport_id=a.sport_id AND a.season>=s.first_season;

DROP VIEW IF EXISTS game_teammate_links;
CREATE VIEW game_teammate_links AS
SELECT a.sport_id,a.player_id AS player_a_id,b.player_id AS player_b_id,
       a.team_id,a.season,t.name AS team_name
  FROM sport_appearances a
  JOIN sport_appearances b ON b.sport_id=a.sport_id AND b.team_id=a.team_id
       AND b.season=a.season AND b.player_id<>a.player_id
  JOIN sport_teams t ON t.sport_id=a.sport_id AND t.team_id=a.team_id AND t.season=a.season
  JOIN sports s ON s.sport_id=a.sport_id AND a.season>=s.first_season;

CREATE TABLE IF NOT EXISTS game_data_audit (
  audit_key TEXT PRIMARY KEY, sport_id TEXT, status TEXT NOT NULL, detail TEXT NOT NULL,
  source TEXT, checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def main() -> None:
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        # Keep pre-1903 baseball rows locally for a future expansion, but do
        # not expose them to present game modes.
        conn.execute("UPDATE sports SET first_season=1903 WHERE sport_id='baseball'")
        conn.executescript(SQL)
        conn.execute("DELETE FROM game_data_audit")
        for sport, players, teams, appearances in conn.execute(
            """SELECT s.sport_id,
                 (SELECT COUNT(*) FROM sport_players p WHERE p.sport_id=s.sport_id),
                 (SELECT COUNT(*) FROM sport_teams t WHERE t.sport_id=s.sport_id),
                 (SELECT COUNT(*) FROM sport_appearances a WHERE a.sport_id=s.sport_id)
               FROM sports s"""
        ):
            conn.execute("INSERT INTO game_data_audit (audit_key,sport_id,status,detail,source) VALUES (?,?, 'ok', ?, 'local_canonical')", (f'{sport}:coverage',sport,f'{players} players, {teams} team-seasons, {appearances} appearances'))
        # A resolved source reference is traceable to the Sports Reference or
        # league-source identifier stored on its accepted claim.
        unresolved = conn.execute("""SELECT COUNT(*) FROM source_player_references r WHERE NOT EXISTS
          (SELECT 1 FROM player_identity_claims c WHERE c.sport_id=r.sport_id AND c.source=r.source AND c.reference_key=r.reference_key AND c.status='accepted')
          AND NOT EXISTS (SELECT 1 FROM source_reference_dispositions d WHERE d.sport_id=r.sport_id AND d.source=r.source AND d.reference_key=r.reference_key)""").fetchone()[0]
        conn.execute("INSERT INTO game_data_audit (audit_key,status,detail,source) VALUES ('identity_queue','ok',?,'reconciliation')", (f'{unresolved} active unresolved source references',))
        conn.commit()
    finally:
        conn.close()
    print('Built game catalog views and data audit.')


if __name__ == '__main__':
    main()
