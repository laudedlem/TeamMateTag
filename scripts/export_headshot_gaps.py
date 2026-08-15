"""Export every active playable player still lacking a verified headshot."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
import server  # noqa: E402

OUTPUT = ROOT / "raw" / "active_headshot_gaps.csv"


def main() -> None:
    rows=[]
    with server.db() as conn:
        for player_id,name,debut,final,status,note in conn.execute("""
            SELECT p.player_id,concat_ws(' ',p.name_first,p.name_last),p.debut_year,p.final_year,h.status,h.review_note
            FROM players p JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=p.player_id
            WHERE h.status IN ('placeholder','missing')
            ORDER BY p.debut_year DESC NULLS LAST,p.name_last,p.name_first"""):
            rows.append(('baseball',player_id,name,debut,final,status,note))
        for player_id,name,debut,final,status,note,sport in conn.execute("""
            SELECT p.player_id,p.display_name,p.debut_year,p.final_year,h.status,h.review_note,p.sport_id
            FROM sport_players p JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
            WHERE h.status IN ('placeholder','missing')
            ORDER BY p.sport_id,p.debut_year DESC NULLS LAST,p.display_name"""):
            rows.append((sport,player_id,name,debut,final,status,note))
    with OUTPUT.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.writer(handle); writer.writerow(['sport','player_id','display_name','debut_year','final_year','status','review_note']); writer.writerows(rows)
    print(f'Wrote {len(rows):,} active headshot gaps to {OUTPUT}')


if __name__ == '__main__':
    main()
