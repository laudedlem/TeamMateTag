"""Apply local-only cleanup to the generated multi-sport dataset."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"

NBA_EXHIBITION_SQL = """
    lower(replace(name, '-', ' ')) LIKE '%all star%'
    OR lower(replace(name, '-', ' ')) LIKE '%rising star%'
    OR lower(replace(name, '-', ' ')) LIKE '%young star%'
    OR lower(replace(name, '-', ' ')) LIKE '%rookie challenge%'
    OR lower(name) IN ('world', 'usa', 'ogs', 'stripes')
    OR lower(name) LIKE 'team %'
"""


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def main() -> None:
    conn = sqlite3.connect(DATABASE)
    try:
        # Exhibition selections are not NBA franchise memberships.
        affected_players = [
            row[0]
            for row in conn.execute(f"""SELECT DISTINCT player_id FROM sport_appearances
                WHERE sport_id = 'basketball' AND team_id IN (
                    SELECT team_id FROM sport_teams WHERE sport_id = 'basketball' AND ({NBA_EXHIBITION_SQL})
                )""").fetchall()
        ]
        conn.execute(f"""DELETE FROM sport_appearances WHERE sport_id = 'basketball' AND team_id IN (
            SELECT team_id FROM sport_teams WHERE sport_id = 'basketball' AND ({NBA_EXHIBITION_SQL})
        )""")
        conn.execute(f"""DELETE FROM sport_player_stints WHERE sport_id = 'basketball' AND team_id IN (
            SELECT team_id FROM sport_teams WHERE sport_id = 'basketball' AND ({NBA_EXHIBITION_SQL})
        )""")
        if table_exists(conn, "sport_teammates"):
            conn.execute(f"""DELETE FROM sport_teammates WHERE sport_id = 'basketball' AND team_id IN (
                SELECT team_id FROM sport_teams WHERE sport_id = 'basketball' AND ({NBA_EXHIBITION_SQL})
            )""")
        conn.execute(f"""DELETE FROM sport_teams WHERE sport_id = 'basketball' AND ({NBA_EXHIBITION_SQL})""")
        if affected_players:
            placeholders = ",".join("?" for _ in affected_players)
            conn.execute(
                f"""DELETE FROM sport_players_searchable
                     WHERE sport_id = 'basketball'
                       AND player_id IN ({placeholders})
                       AND NOT EXISTS (
                           SELECT 1 FROM sport_appearances a
                            WHERE a.sport_id = sport_players_searchable.sport_id
                              AND a.player_id = sport_players_searchable.player_id
                       )""",
                affected_players,
            )
        conn.execute(
            """DELETE FROM sport_players_searchable
                 WHERE sport_id = 'basketball'
                   AND NOT EXISTS (
                       SELECT 1 FROM sport_appearances a
                        WHERE a.sport_id = sport_players_searchable.sport_id
                          AND a.player_id = sport_players_searchable.player_id
                   )"""
        )

        # nflverse exposes an ESPN ID that works with ESPN's player headshots.
        nfl_images = {}
        for path in (ROOT / 'raw' / 'nfl').glob('**/*.csv'):
            with path.open(encoding='utf-8', newline='') as handle:
                for row in csv.DictReader(handle):
                    gsis, espn = (row.get('gsis_id') or '').strip(), (row.get('espn_id') or '').strip()
                    image = (row.get('headshot_url') or '').strip() or espn
                    if gsis and image:
                        nfl_images[gsis] = image
        conn.executemany("UPDATE sport_players SET external_id = ? WHERE sport_id = 'football' AND player_id = ?",
                         [(image, f'nfl:{gsis}') for gsis, image in nfl_images.items()])
        with (ROOT / 'raw' / 'nba_kaggle' / 'Players.csv').open(encoding='utf-8-sig', newline='') as handle:
            rows = []
            for row in csv.DictReader(handle):
                positions = [label for key, label in [('guard', 'G'), ('forward', 'F'), ('center', 'C')]
                             if (row.get(key) or '').strip() == '1']
                if row.get('personId') and positions:
                    rows.append(('/'.join(positions), f"nba:{row['personId']}"))
            conn.executemany("UPDATE sport_players SET primary_pos = ? WHERE sport_id = 'basketball' AND player_id = ?", rows)
        conn.execute("""UPDATE sport_players SET debut_year = (SELECT MIN(season) FROM sport_appearances a WHERE a.sport_id=sport_players.sport_id AND a.player_id=sport_players.player_id),
               final_year = (SELECT MAX(season) FROM sport_appearances a WHERE a.sport_id=sport_players.sport_id AND a.player_id=sport_players.player_id)""")
        conn.commit()
    finally:
        conn.close()


if __name__ == '__main__':
    main()
