"""Apply local-only cleanup to the generated multi-sport dataset."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"


def main() -> None:
    conn = sqlite3.connect(DATABASE)
    try:
        # Exhibition selections are not NBA franchise memberships.
        conn.execute("""DELETE FROM sport_appearances WHERE sport_id = 'basketball' AND team_id IN (
            SELECT team_id FROM sport_teams WHERE sport_id = 'basketball' AND (
              lower(name) LIKE '%all star%' OR lower(name) LIKE '%all-stars%' OR lower(name) LIKE '%rising stars%' OR lower(name) = 'world'
            ))""")
        conn.execute("""DELETE FROM sport_teams WHERE sport_id = 'basketball' AND (
            lower(name) LIKE '%all star%' OR lower(name) LIKE '%all-stars%' OR lower(name) LIKE '%rising stars%' OR lower(name) = 'world'
        )""")

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
