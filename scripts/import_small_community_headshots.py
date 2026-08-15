"""Import the limited NHL/MLB matches from community roster photo CSVs."""
from __future__ import annotations

import argparse
import csv
import io
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, 'web'); sys.path.insert(0, 'scripts')
import server  # noqa: E402
from audit_runtime_headshots import fetch  # noqa: E402
from name_normalize import normalize  # noqa: E402

SOURCES={
 'baseball':('https://raw.githubusercontent.com/IvoVillanueva/dataMLB/main/dataMLB.csv','espn_nombres','cabezas'),
 'hockey':('https://raw.githubusercontent.com/IvoVillanueva/NHL/main/dataNHL.csv','espn_player_name','cabezas'),
}

def main() -> None:
 parser=argparse.ArgumentParser(); parser.add_argument('--sport',required=True,choices=tuple(SOURCES)); parser.add_argument('--workers',type=int,default=12); args=parser.parse_args()
 url,name_col,image_col=SOURCES[args.sport]
 source=list(csv.DictReader(io.StringIO(requests.get(url,timeout=60).text)))
 urls={normalize(row.get(name_col) or ''):row.get(image_col) for row in source if row.get(image_col)}
 with server.db() as conn:
  if args.sport=='baseball':
   rows=conn.execute("SELECT p.player_id,concat_ws(' ',p.name_first,p.name_last) FROM players p JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=p.player_id WHERE h.status IN ('placeholder','missing')").fetchall()
  else:
   rows=conn.execute("SELECT p.player_id,p.display_name FROM sport_players p JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id WHERE p.sport_id=%s AND h.status IN ('placeholder','missing')",(args.sport,)).fetchall()
 jobs=[(player_id,name,urls[normalize(name)]) for player_id,name in rows if normalize(name) in urls]
 print(f'Matched {len(jobs)} {args.sport} gaps to community roster URLs.',flush=True)
 results=[]
 with ThreadPoolExecutor(max_workers=max(1,args.workers)) as pool:
  futures={pool.submit(fetch,url):(player_id,name,url) for player_id,name,url in jobs}
  for future in as_completed(futures):
   player_id,name,url=futures[future]; image=future.result(); results.append((player_id,name,url,image))
 promoted=[row for row in results if row[3]['status']=='ok']
 with server.db() as conn,conn.cursor() as cur:
  cur.executemany("""INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url) VALUES (%s,%s,'Community roster CSV',%s,%s)
   ON CONFLICT (sport_id,player_id,provider) DO UPDATE SET status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",[(args.sport,pid,'candidate' if image['status']=='ok' else image['status'],url) for pid,_,url,image in results])
  cur.executemany("""UPDATE player_headshots SET source_url=%s,provider='Community roster CSV',status='verified',content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
   review_note='Playtest-only community mapping; license and source review required.' WHERE sport_id=%s AND player_id=%s""",[(url,img['sha256'],img['perceptual_hash'],img['width'],img['height'],args.sport,pid) for pid,_,url,img in promoted])
  if args.sport!='baseball': cur.executemany("INSERT INTO sport_player_images (sport_id,player_id,source_url) VALUES (%s,%s,%s) ON CONFLICT (sport_id,player_id) DO UPDATE SET source_url=EXCLUDED.source_url",[(args.sport,pid,url) for pid,_,url,_ in promoted])
 print(f'Promoted {len(promoted)} community roster photos.')

if __name__=='__main__': main()
