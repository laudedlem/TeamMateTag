"""Verify every active player headshot and seed the Headshot Audit registry.

The script reads live Supabase data, downloads the actual candidate image, and
classifies HTTP failures, known placeholder graphics, and byte-identical image
collisions. It stores URLs and fingerprints only; image binaries are never put
in Supabase. Run locally before opening /headshot-audit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
import server  # noqa: E402

REPORT = ROOT / "raw" / "headshot_audit_report.csv"
USER_AGENT = "TeamMateTag headshot audit/1.0 (local verification)"
KNOWN_PLACEHOLDER_URLS = {
    "baseball": [server.HEADSHOT_URL.format(150411)],  # Brian Schneider silhouette
    "basketball": ["https://cdn.nba.com/headshots/nba/latest/1040x760/2557.png"],  # Luke Ridnour silhouette
    "football": ["https://static.www.nfl.com/image/private/f_auto,q_auto/league/kzskiya49cfacm4bv5nr"],  # Trent Cole generic
    "hockey": ["https://assets.nhle.com/mugs/nhl/latest/8467857.png"],  # Jason Krog missing graphic
}


def dhash(content: bytes) -> tuple[str, int, int]:
    image = Image.open(io.BytesIO(content)).convert("L")
    width, height = image.size
    image = image.resize((9, 8))
    pixels = list(image.getdata())
    bits = "".join("1" if pixels[row * 9 + col] > pixels[row * 9 + col + 1] else "0"
                   for row in range(8) for col in range(8))
    return f"{int(bits, 2):016x}", width, height


def hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def fetch(url: str | None) -> dict:
    if not url:
        return {"status": "missing", "error": "no candidate URL"}
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=8)
        content = response.content
        if response.status_code != 200:
            return {"status": "missing", "error": f"HTTP {response.status_code}"}
        if not content:
            return {"status": "missing", "error": "empty response"}
        try:
            perceptual_hash, width, height = dhash(content)
        except Exception as error:
            return {"status": "missing", "error": f"not a decodable image: {error}"}
        if width < 80 or height < 80:
            return {"status": "missing", "error": f"image too small: {width}x{height}"}
        return {"status": "ok", "sha256": hashlib.sha256(content).hexdigest(),
                "perceptual_hash": perceptual_hash, "width": width, "height": height}
    except requests.RequestException as error:
        return {"status": "missing", "error": str(error)[:300]}


def candidates(conn) -> list[dict]:
    rows = []
    for player_id, name, mlbam_id, debut, final in conn.execute(
        """SELECT p.player_id, concat_ws(' ', p.name_first, p.name_last), p.mlbam_id, p.debut_year, p.final_year
             FROM players p WHERE EXISTS (SELECT 1 FROM appearances a WHERE a.player_id=p.player_id AND a.season>=2000)"""
    ).fetchall():
        rows.append({"sport": "baseball", "player_id": player_id, "name": name, "debut": debut, "final": final,
                     "url": server.HEADSHOT_URL.format(mlbam_id) if mlbam_id else None, "provider": "MLBAM"})
    for sport, player_id, name, external_id, debut, final, source_url in conn.execute(
        """SELECT p.sport_id, p.player_id, p.display_name, p.external_id, p.debut_year, p.final_year, i.source_url
             FROM sport_players p LEFT JOIN sport_player_images i ON i.sport_id=p.sport_id AND i.player_id=p.player_id
             ORDER BY p.sport_id, p.player_id"""
    ).fetchall():
        url = source_url or server._official_sport_headshot_url(sport, external_id)
        provider = "catalog" if source_url else {"basketball": "NBA", "football": "NFL/ESPN", "hockey": "NHL"}.get(sport, "unknown")
        rows.append({"sport": sport, "player_id": player_id, "name": name, "debut": debut, "final": final,
                     "url": url, "provider": provider})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0, help="Audit only this many players (for a quick smoke test).")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many eligible rows before applying --limit.")
    parser.add_argument("--sport", choices=("baseball", "basketball", "football", "hockey"))
    parser.add_argument("--force", action="store_true", help="Recheck manually reviewed records too.")
    args = parser.parse_args()
    server.ensure_runtime_schema()
    with server.db() as conn:
        rows = candidates(conn)
        reviewed = {(sport, player_id): status for sport, player_id, status in conn.execute(
            "SELECT sport_id, player_id, status FROM player_headshots WHERE status IN ('verified', 'wrong_player', 'bad_crop')"
        ).fetchall()}
    if not args.force:
        rows = [row for row in rows if (row["sport"], row["player_id"]) not in reviewed]
    if args.sport:
        rows = [row for row in rows if row["sport"] == args.sport]
    if args.offset:
        rows = rows[args.offset:]
    if args.limit:
        rows = rows[:args.limit]
    print(f"Auditing {len(rows):,} headshot candidates with {args.workers} workers.", flush=True)

    placeholder_hashes: dict[str, set[str]] = defaultdict(set)
    placeholder_perceptual: dict[str, set[str]] = defaultdict(set)
    for sport, urls in KNOWN_PLACEHOLDER_URLS.items():
        for url in urls:
            result = fetch(url)
            if result["status"] == "ok":
                placeholder_hashes[sport].add(result["sha256"])
                placeholder_perceptual[sport].add(result["perceptual_hash"])

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {executor.submit(fetch, row["url"]): (row["sport"], row["player_id"]) for row in rows}
        for index, future in enumerate(as_completed(future_map), 1):
            results[future_map[future]] = future.result()
            if index % 500 == 0 or index == len(rows):
                print(f"  checked {index:,}/{len(rows):,}", flush=True)

    duplicates = Counter((row["sport"], result.get("sha256")) for row in rows
                         if (result := results[(row["sport"], row["player_id"])]).get("status") == "ok")
    output = []
    for row in rows:
        result = results[(row["sport"], row["player_id"])]
        status = result["status"]
        if status == "ok":
            is_placeholder = result["sha256"] in placeholder_hashes[row["sport"]] or any(
                hamming(result["perceptual_hash"], known) <= 4 for known in placeholder_perceptual[row["sport"]]
            )
            if is_placeholder:
                status = "placeholder"
            elif duplicates[(row["sport"], result["sha256"])] > 1:
                status = "duplicate"
            else:
                status = "verified"
        row.update(result)
        row["status"] = status
        output.append(row)

    now = datetime.now(timezone.utc)
    with server.db() as conn:
        for row in output:
            conn.execute(
                """INSERT INTO player_headshots
                       (sport_id, player_id, source_url, provider, status, content_sha256, perceptual_hash, width, height, checked_at, review_note)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                     ON CONFLICT (sport_id, player_id) DO UPDATE SET
                       source_url=EXCLUDED.source_url, provider=EXCLUDED.provider, status=EXCLUDED.status,
                       content_sha256=EXCLUDED.content_sha256, perceptual_hash=EXCLUDED.perceptual_hash,
                       width=EXCLUDED.width, height=EXCLUDED.height, checked_at=EXCLUDED.checked_at,
                       review_note=EXCLUDED.review_note""",
                (row["sport"], row["player_id"], row["url"], row["provider"], row["status"],
                 row.get("sha256"), row.get("perceptual_hash"), row.get("width"), row.get("height"), now,
                 row.get("error")),
            )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    flagged = [row for row in output if row["status"] != "verified"]
    with REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sport", "player_id", "name", "debut", "final", "status", "url", "provider", "error"])
        writer.writeheader(); writer.writerows({key: row.get(key, "") for key in writer.fieldnames} for row in flagged)
    print("Results:", dict(Counter(row["status"] for row in output)))
    print(f"Wrote {len(flagged):,} flagged rows to {REPORT}")


if __name__ == "__main__":
    main()
