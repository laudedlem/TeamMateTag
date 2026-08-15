"""Resolve active MLB headshot gaps from Baseball Reference player pages.

Baseball Reference exposes player-page photos in the structured
``HeaderPersonSchema`` block as ``image.contentUrl``. This script only promotes
images that decode successfully and do not match the known MLBAM placeholder.
It updates Supabase directly, so the live site can use the new URLs without a
Vercel code deploy.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests
try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - optional local scraping dependency
    curl_requests = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "web"))

from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
import server  # noqa: E402


REPORT = ROOT / "raw" / "mlb_bref_headshot_resolution.csv"
USER_AGENT = "TeamMateTag MLB headshot resolver/1.0 (local verification)"


def bref_player_url(bbref_id: str) -> str:
    return f"https://www.baseball-reference.com/players/{bbref_id[0]}/{quote(bbref_id)}.shtml"


def placeholder_fingerprints() -> tuple[set[str], set[str]]:
    hashes: set[str] = set()
    perceptual: set[str] = set()
    for url in KNOWN_PLACEHOLDER_URLS["baseball"]:
        result = fetch(url)
        if result.get("status") == "ok":
            hashes.add(result["sha256"])
            perceptual.add(result["perceptual_hash"])
    return hashes, perceptual


def unresolved_players(conn) -> list[tuple[str, str, int | None, int | None, str | None]]:
    return conn.execute(
        """SELECT p.player_id, COALESCE(NULLIF(p.bbref_id, ''), p.player_id), concat_ws(' ', p.name_first, p.name_last),
                  p.debut_year, p.final_year, p.primary_pos
             FROM players p
             JOIN player_headshots h
               ON h.sport_id='baseball' AND h.player_id=p.player_id
            WHERE p.final_year >= 2000
              AND h.status IN ('placeholder','missing')
            ORDER BY p.final_year DESC NULLS LAST, p.name_last, p.name_first, p.player_id"""
    ).fetchall()


def extract_structured_image(page: str) -> str | None:
    for match in re.finditer(r'<script type="application/ld\\+json">\\s*(.*?)\\s*</script>', page, flags=re.S):
        raw = match.group(1).strip()
        if '"@type": "Person"' not in raw and '"@type":"Person"' not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        image = data.get("image")
        if isinstance(image, dict) and isinstance(image.get("contentUrl"), str):
            return image["contentUrl"]
        if isinstance(image, str):
            return image
    return None


def extract_photo_tag(page: str) -> str | None:
    match = re.search(r'<img[^>]+src="([^"]+)"[^>]+alt="Photo of ', page)
    if match:
        return match.group(1)
    match = re.search(r'<img[^>]+alt="Photo of [^"]+"[^>]+src="([^"]+)"', page)
    return match.group(1) if match else None


def fetch_player_page(url: str, session: requests.Session) -> tuple[int, str, str]:
    try:
        response = session.get(url, timeout=30)
    except requests.RequestException as error:
        return 0, "", f"page request failed: {error}"
    if response.status_code == 200 and "images/headshots" in response.text:
        return response.status_code, response.text, ""
    if curl_requests is not None:
        try:
            fallback = curl_requests.get(url, impersonate="safari184", timeout=30)
        except Exception as error:
            return response.status_code, response.text, f"curl fallback failed: {error}"
        return fallback.status_code, fallback.text, ""
    return response.status_code, response.text, ""


def candidate_url(player_id: str, session: requests.Session) -> tuple[str | None, str]:
    url = bref_player_url(player_id)
    status_code, page, fetch_error = fetch_player_page(url, session)
    if fetch_error:
        return None, fetch_error
    if status_code in (403, 429):
        return None, f"rate_limited HTTP {status_code}"
    if status_code != 200:
        return None, f"page HTTP {status_code}"
    image_url = extract_structured_image(page) or extract_photo_tag(page)
    if not image_url:
        return None, "no Baseball Reference photo URL"
    return image_url, ""


def is_bad_image(image: dict, hashes: set[str], perceptual: set[str]) -> str | None:
    if image.get("status") != "ok":
        return image.get("status") or "not ok"
    if image.get("sha256") in hashes:
        return "known placeholder hash"
    if any(hamming(image.get("perceptual_hash", ""), candidate) <= 4 for candidate in perceptual):
        return "known placeholder perceptual match"
    if int(image.get("width") or 0) < 80 or int(image.get("height") or 0) < 80:
        return f"image too small: {image.get('width')}x{image.get('height')}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()

    hashes, perceptual = placeholder_fingerprints()
    with server.db() as conn:
        players = unresolved_players(conn)
    if args.limit:
        players = players[:args.limit]

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    rows: list[dict] = []
    verified: list[tuple] = []
    for index, (player_id, bbref_id, name, debut, final, position) in enumerate(players, 1):
        image_url, page_error = candidate_url(bbref_id, session)
        if not image_url:
            status = "rate_limited" if page_error.startswith("rate_limited") else "missing"
            rows.append({
                "player_id": player_id, "display_name": name, "debut_year": debut,
                "final_year": final, "position": position or "", "status": status,
                "image_url": "", "note": page_error,
            })
        else:
            image = fetch(image_url)
            reason = is_bad_image(image, hashes, perceptual)
            if reason:
                rows.append({
                    "player_id": player_id, "display_name": name, "debut_year": debut,
                    "final_year": final, "position": position or "", "status": "rejected",
                    "image_url": image_url, "note": reason,
                })
            else:
                note = "Baseball Reference player-page headshot; validated by TeamMateTag resolver."
                rows.append({
                    "player_id": player_id, "display_name": name, "debut_year": debut,
                    "final_year": final, "position": position or "", "status": "verified",
                    "image_url": image_url, "note": note,
                })
                verified.append((
                    image_url, image["sha256"], image["perceptual_hash"], image["width"],
                    image["height"], note, player_id,
                ))
        if index % 10 == 0 or index == len(players):
            print(f"checked {index}/{len(players)}", flush=True)
        if args.sleep:
            time.sleep(args.sleep)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "player_id", "display_name", "debut_year", "final_year", "position",
            "status", "image_url", "note",
        ])
        writer.writeheader()
        writer.writerows(rows)

    if not args.dry_run and verified:
        with server.db() as conn, conn.cursor() as cur:
            cur.executemany(
                """UPDATE player_headshots
                      SET source_url=%s, fallback_url=NULL, provider='Baseball Reference',
                          status='verified', content_sha256=%s, perceptual_hash=%s,
                          width=%s, height=%s, reviewed_at=now(), review_note=%s
                    WHERE sport_id='baseball' AND player_id=%s""",
                verified,
            )
            cur.executemany(
                """INSERT INTO player_headshot_source_attempts
                     (sport_id, player_id, provider, status, source_url)
                   VALUES ('baseball', %s, 'Baseball Reference', 'verified', %s)
                   ON CONFLICT (sport_id, player_id, provider)
                   DO UPDATE SET status=EXCLUDED.status, source_url=EXCLUDED.source_url, checked_at=now()""",
                [(player_id, image_url) for image_url, *_rest, player_id in verified],
            )
    print(f"Verified {len(verified):,} Baseball Reference MLB headshots.")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
