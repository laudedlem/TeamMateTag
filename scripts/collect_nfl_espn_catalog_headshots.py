"""Use the full local ESPN NFL identity catalog to repair unresolved portraits."""
from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web")); sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
CATALOG = ROOT / "raw" / "espn_nfl_athlete_pages"
OUTPUT = ROOT / "raw" / "nfl_espn_catalog_headshots.csv"


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument('--workers',type=int,default=24); parser.add_argument('--limit',type=int,default=0); args=parser.parse_args()
    identities=defaultdict(list)
    for path in CATALOG.glob('page_*.csv'):
        with path.open(encoding='utf-8',newline='') as handle:
            for row in csv.DictReader(handle):
                birth=(row.get('birth_date') or '')[:10]
                if row.get('espn_id','').isdigit() and birth:
                    identities[(normalize(row.get('display_name') or ''),birth)].append(row['espn_id'])
    identities={key:ids for key,ids in identities.items() if len(set(ids))==1}
    response=requests.get(PLAYERS_URL,timeout=90); response.raise_for_status()
    births={f"nfl:{row['gsis_id']}":row.get('birth_date','') for row in csv.DictReader(io.StringIO(response.text)) if row.get('gsis_id') and row.get('birth_date')}
    with server.db() as conn:
        rows=conn.execute("""SELECT p.player_id,p.display_name FROM sport_players p JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
          LEFT JOIN player_headshot_source_attempts tried ON tried.sport_id=p.sport_id AND tried.player_id=p.player_id AND tried.provider='ESPN catalog'
          WHERE p.sport_id='football' AND h.status IN ('placeholder','missing') AND tried.player_id IS NULL""").fetchall()
        known_hashes={value for value, in conn.execute("SELECT DISTINCT content_sha256 FROM player_headshots WHERE sport_id='football' AND status='placeholder' AND content_sha256 IS NOT NULL")}
    jobs=[]
    for player_id,name in rows:
        ids=identities.get((normalize(name),births.get(player_id,'')))
        if ids:
            jobs.append((player_id,name,ids[0],f'https://a.espncdn.com/i/headshots/nfl/players/full/{ids[0]}.png'))
    if args.limit: jobs=jobs[:args.limit]
    print(f'Matched {len(jobs):,} unresolved NFL players to ESPN catalog identities.',flush=True)
    known_phashes=set()
    for url in KNOWN_PLACEHOLDER_URLS['football']+['https://a.espncdn.com/i/headshots/nfl/players/full/9643.png']:
        image=fetch(url)
        if image['status']=='ok': known_hashes.add(image['sha256']); known_phashes.add(image['perceptual_hash'])
    results=[]
    with ThreadPoolExecutor(max_workers=max(1,args.workers)) as pool:
        futures={pool.submit(fetch,url):(pid,name,espn_id,url) for pid,name,espn_id,url in jobs}
        for count,future in enumerate(as_completed(futures),1):
            pid,name,espn_id,url=futures[future]; image=future.result(); status=image['status']
            if status=='ok' and (image['sha256'] in known_hashes or any(hamming(image['perceptual_hash'],value)<=4 for value in known_phashes)): status='placeholder'
            results.append((pid,name,espn_id,url,status,image))
            if count%500==0 or count==len(jobs): print(f'  checked {count:,}/{len(jobs):,}',flush=True)
    digests=Counter(image.get('sha256') for *_,status,image in results if status=='ok')
    attempts=[]; promoted=[]; output=[]
    for pid,name,espn_id,url,status,image in results:
        if status=='ok' and digests[image['sha256']]>1: status='shared_image'
        attempts.append((pid,'candidate' if status=='ok' else status,url))
        output.append({'player_id':pid,'display_name':name,'espn_id':espn_id,'source_url':url,'status':status,'sha256':image.get('sha256',''),'note':image.get('error','')})
        if status=='ok': promoted.append((url,image,pid))
    with OUTPUT.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['player_id','display_name','espn_id','source_url','status','sha256','note']); writer.writeheader(); writer.writerows(output)
    with server.db() as conn,conn.cursor() as cur:
        cur.executemany("""INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url) VALUES ('football',%s,'ESPN catalog',%s,%s)
          ON CONFLICT (sport_id,player_id,provider) DO UPDATE SET status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",attempts)
        cur.executemany("""UPDATE player_headshots SET source_url=%s,fallback_url=NULL,provider='ESPN catalog',status='verified',content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,review_note='Validated full ESPN NFL catalog portrait.' WHERE sport_id='football' AND player_id=%s""",[(url,img['sha256'],img['perceptual_hash'],img['width'],img['height'],pid) for url,img,pid in promoted])
        cur.executemany("INSERT INTO sport_player_images (sport_id,player_id,source_url) VALUES ('football',%s,%s) ON CONFLICT (sport_id,player_id) DO UPDATE SET source_url=EXCLUDED.source_url",[(pid,url) for url,_,pid in promoted])
    print(f'Wrote {len(output):,} catalog attempts; promoted {len(promoted)} NFL ESPN portraits.',flush=True)


if __name__=='__main__': main()
