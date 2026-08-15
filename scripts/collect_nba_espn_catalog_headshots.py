"""Download and audit ESPN portraits for the completed current-NBA catalog."""
from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

IDENTITIES = ROOT / "raw" / "nba_headshot_identity_matches.csv"
CATALOG = ROOT / "raw" / "espn_nba_athlete_pages" / "page_01.csv"
OUTPUT = ROOT / "raw" / "nba_espn_headshot_catalog.csv"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if not IDENTITIES.exists() or not CATALOG.exists():
        raise RuntimeError("Run the NBA identity map and ESPN index first.")
    local: dict[tuple[str, str], list[str]] = {}
    with IDENTITIES.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            local.setdefault((normalize(row["display_name"]), row["birth_date"]), []).append(row["player_id"])
    jobs = []
    with CATALOG.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            birth = (row.get("birth_date") or "")[:10]
            player_ids = local.get((normalize(row.get("display_name") or ""), birth), [])
            if len(player_ids) == 1 and row.get("espn_id", "").isdigit():
                jobs.append((player_ids[0], row["display_name"], row["espn_id"],
                             f"https://a.espncdn.com/i/headshots/nba/players/full/{row['espn_id']}.png"))
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"Matched {len(jobs):,} local NBA players to ESPN catalog identities.", flush=True)
    placeholder_hashes=set(); placeholder_phashes=set()
    for url in KNOWN_PLACEHOLDER_URLS["basketball"]:
        image=fetch(url)
        if image["status"] == "ok":
            placeholder_hashes.add(image["sha256"]); placeholder_phashes.add(image["perceptual_hash"])
    results=[]
    with ThreadPoolExecutor(max_workers=max(1,args.workers)) as pool:
        futures={pool.submit(fetch,url):(player_id,name,espn_id,url) for player_id,name,espn_id,url in jobs}
        for count,future in enumerate(as_completed(futures),1):
            player_id,name,espn_id,url=futures[future]; image=future.result(); status=image["status"]
            if status == "ok" and (image["sha256"] in placeholder_hashes or any(hamming(image["perceptual_hash"], known)<=4 for known in placeholder_phashes)):
                status="placeholder"
            results.append({"player_id":player_id,"display_name":name,"espn_id":espn_id,"source_url":url,
                            "status":status,"sha256":image.get("sha256", ""),"perceptual_hash":image.get("perceptual_hash", ""),
                            "width":image.get("width", ""),"height":image.get("height", ""),"note":image.get("error", "")})
            if count % 100 == 0 or count == len(jobs): print(f"  checked {count:,}/{len(jobs):,}",flush=True)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    with OUTPUT.open("w",encoding="utf-8",newline="") as handle:
        fields=["player_id","display_name","espn_id","source_url","status","sha256","perceptual_hash","width","height","note"]
        writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows(sorted(results,key=lambda row:row["player_id"]))
    with server.db() as conn, conn.cursor() as cur:
        flagged={player_id for player_id, in cur.execute("SELECT player_id FROM player_headshots WHERE sport_id='basketball' AND status IN ('placeholder','missing')")}
        promoted=[row for row in results if row["status"] == "ok" and row["player_id"] in flagged]
        cur.executemany("""INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
          VALUES ('basketball',%s,'ESPN catalog',%s,%s) ON CONFLICT (sport_id,player_id,provider) DO UPDATE SET
          status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",
          [(row["player_id"], "candidate" if row["status"] == "ok" else row["status"], row["source_url"]) for row in results])
        cur.executemany("""UPDATE player_headshots SET source_url=%s,fallback_url=NULL,provider='ESPN catalog',status='verified',
          content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,review_note='Validated ESPN current-NBA catalog portrait.'
          WHERE sport_id='basketball' AND player_id=%s""",
          [(row["source_url"],row["sha256"],row["perceptual_hash"],row["width"],row["height"],row["player_id"]) for row in promoted])
        cur.executemany("""INSERT INTO sport_player_images (sport_id,player_id,source_url) VALUES ('basketball',%s,%s)
          ON CONFLICT (sport_id,player_id) DO UPDATE SET source_url=EXCLUDED.source_url""",
          [(row["player_id"],row["source_url"]) for row in promoted])
    print(f"Wrote {len(results):,} local ESPN portrait records to {OUTPUT}; promoted {len(promoted)} flagged players.")


if __name__ == '__main__':
    main()
