"""Bulk-import Basketball GM's community NBA photo map for local playtesting.

Every promoted record is explicitly marked for later license/source review.
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

PHOTO_MAP_URL = "https://raw.githubusercontent.com/alexnoob/BasketBall-GM-Rosters/master/player-photos.json"
INDEX_URL = "https://www.basketball-reference.com/players/{letter}/"
HEADERS = {"User-Agent": "TeamMateTag playtest headshot importer/0.2.10"}
OUTPUT = ROOT / "raw" / "nba_bbgm_playtest_headshots.csv"


def player_index() -> dict[str, tuple[str, int | None, int | None]]:
    result = {}
    for letter in "abcdefghijklmnopqrstuvwxyz":
        response = requests.get(INDEX_URL.format(letter=letter), headers=HEADERS, timeout=30)
        response.raise_for_status()
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", response.text, flags=re.S):
            match = re.search(r'href="/players/[a-z]/([a-z0-9]+)\.html"[^>]*>(.*?)</a>', row, flags=re.S)
            if not match:
                continue
            years = re.findall(r'data-stat="year_(?:min|max)"[^>]*>(\d{4})', row)
            debut = int(years[0]) if years else None
            final = int(years[1]) if len(years) > 1 else debut
            name = html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
            result[match.group(1)] = (name, debut, final)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    photos = requests.get(PHOTO_MAP_URL, headers=HEADERS, timeout=60).json()
    index = player_index()
    by_name: dict[str, list[tuple[str, int | None, int | None]]] = defaultdict(list)
    for player_id, (name, debut, final) in index.items():
        if player_id in photos:
            by_name[normalize(name)].append((player_id, debut, final))
    with server.db() as conn:
        rows = conn.execute("""SELECT p.player_id,p.display_name,p.debut_year,p.final_year
            FROM sport_players p JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
            WHERE p.sport_id='basketball' AND h.status IN ('placeholder','missing')""").fetchall()
    jobs=[]
    for local_id, name, debut, final in rows:
        candidates=by_name.get(normalize(name), [])
        if not candidates:
            continue
        ranked=sorted(candidates, key=lambda item: abs((item[1] or debut or 0)-(debut or 0))+abs((item[2] or final or 0)-(final or 0)))
        if len(ranked)>1:
            first_score=abs((ranked[0][1] or debut or 0)-(debut or 0))+abs((ranked[0][2] or final or 0)-(final or 0))
            second_score=abs((ranked[1][1] or debut or 0)-(debut or 0))+abs((ranked[1][2] or final or 0)-(final or 0))
            if first_score == second_score:
                continue
        source_id,_,_=ranked[0]
        jobs.append((local_id,name,source_id,photos[source_id]))
    if args.limit: jobs=jobs[:args.limit]
    print(f"Matched {len(jobs):,} flagged NBA players to Basketball GM photo URLs.",flush=True)
    known_hashes=set(); known_phashes=set()
    for url in KNOWN_PLACEHOLDER_URLS['basketball']:
        image=fetch(url)
        if image['status']=='ok': known_hashes.add(image['sha256']); known_phashes.add(image['perceptual_hash'])
    results=[]
    with ThreadPoolExecutor(max_workers=max(1,args.workers)) as pool:
        futures={pool.submit(fetch,url):(local_id,name,source_id,url) for local_id,name,source_id,url in jobs}
        for number,future in enumerate(as_completed(futures),1):
            local_id,name,source_id,url=futures[future]; image=future.result(); status=image['status']
            if status=='ok' and (image['sha256'] in known_hashes or any(hamming(image['perceptual_hash'],value)<=4 for value in known_phashes)):
                status='placeholder'
            results.append({'player_id':local_id,'display_name':name,'bbref_id':source_id,'source_url':url,'status':status,
                            'sha256':image.get('sha256',''),'perceptual_hash':image.get('perceptual_hash',''),
                            'width':image.get('width',''),'height':image.get('height',''),'note':image.get('error','')})
            if number % 100 == 0 or number == len(jobs): print(f"  checked {number:,}/{len(jobs):,}",flush=True)
    with OUTPUT.open('w',encoding='utf-8',newline='') as handle:
        fields=['player_id','display_name','bbref_id','source_url','status','sha256','perceptual_hash','width','height','note']
        writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows(sorted(results,key=lambda row:row['player_id']))
    promoted=[row for row in results if row['status']=='ok']
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany("""INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
          VALUES ('basketball',%s,'BBGM community map',%s,%s) ON CONFLICT (sport_id,player_id,provider) DO UPDATE SET
          status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",
          [(row['player_id'],'candidate' if row['status']=='ok' else row['status'],row['source_url']) for row in results])
        cur.executemany("""UPDATE player_headshots SET source_url=%s,fallback_url=NULL,provider='BBGM community map',status='verified',
          content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
          review_note='Playtest-only community mapping; license and source review required.'
          WHERE sport_id='basketball' AND player_id=%s""",
          [(row['source_url'],row['sha256'],row['perceptual_hash'],row['width'],row['height'],row['player_id']) for row in promoted])
        cur.executemany("""INSERT INTO sport_player_images (sport_id,player_id,source_url) VALUES ('basketball',%s,%s)
          ON CONFLICT (sport_id,player_id) DO UPDATE SET source_url=EXCLUDED.source_url""",
          [(row['player_id'],row['source_url']) for row in promoted])
    print(f"Wrote {len(results):,} mappings to {OUTPUT}; promoted {len(promoted)} playtest images.")


if __name__ == '__main__':
    main()
