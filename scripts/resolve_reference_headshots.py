"""Resolve Basketball/Hockey Reference player-page headshots.

Sports Reference pages expose player portraits in page markup, but plain
requests can be blocked or served incomplete pages. This script uses curl_cffi
browser impersonation when available, matches unresolved TeamMateTag players to
Reference index slugs by normalized name plus career-year proximity, validates
the discovered image URL, and updates Supabase.
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover
    curl_requests = None
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

CONFIG = {
    "basketball": {
        "base": "https://www.basketball-reference.com",
        "index": "https://www.basketball-reference.com/players/{letter}/",
        "provider": "Basketball Reference",
        "report": ROOT / "raw" / "basketball_reference_headshots.csv",
        "row_pattern": re.compile(
            r'<tr[^>]*>.*?<a href="/players/(?P<letter>[a-z])/(?P<slug>[a-z0-9]+)\.html">(?P<name>.*?)</a>.*?'
            r'data-stat="year_min"[^>]*>(?P<debut>\d{4})</td>.*?'
            r'data-stat="year_max"[^>]*>(?P<final>\d{4})</td>',
            re.S,
        ),
    },
    "hockey": {
        "base": "https://www.hockey-reference.com",
        "index": "https://www.hockey-reference.com/players/{letter}/",
        "provider": "Hockey Reference",
        "report": ROOT / "raw" / "hockey_reference_headshots.csv",
        "row_pattern": re.compile(
            r'<p class="nhl">.*?<a href="/players/(?P<letter>[a-z])/(?P<slug>[a-z0-9]+)\.html">(?P<name>.*?)</a>'
            r'.*?\((?P<debut>\d{4})-(?P<final>\d{4}),',
            re.S,
        ),
    },
}


def page_get(url: str, retries: int = 2) -> tuple[int, str]:
    last_status, last_text = 0, ""
    if curl_requests is not None:
        for attempt in range(retries + 1):
            response = curl_requests.get(url, impersonate="safari184", timeout=30)
            last_status, last_text = response.status_code, response.text
            if response.status_code != 429:
                return last_status, last_text
            time.sleep(1.5 + attempt)
        return last_status, last_text
    for attempt in range(retries + 1):
        response = requests.get(url, headers={"User-Agent": "TeamMateTag Reference headshot resolver"}, timeout=30)
        last_status, last_text = response.status_code, response.text
        if response.status_code != 429:
            return last_status, last_text
        time.sleep(1.5 + attempt)
    return last_status, last_text


def clean_name(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def reference_index(sport: str) -> dict[str, list[dict]]:
    cfg = CONFIG[sport]
    by_name: dict[str, list[dict]] = defaultdict(list)
    for letter in "abcdefghijklmnopqrstuvwxyz":
        status, text = page_get(cfg["index"].format(letter=letter))
        if status != 200:
            print(f"{sport} index {letter}: HTTP {status}", flush=True)
            continue
        for match in cfg["row_pattern"].finditer(text):
            name = clean_name(match.group("name"))
            slug = match.group("slug")
            by_name[normalize(name)].append({
                "slug": slug,
                "letter": match.group("letter"),
                "name": name,
                "debut": int(match.group("debut")),
                "final": int(match.group("final")),
            })
        time.sleep(0.05)
    return by_name


def unresolved_players(sport: str) -> list[dict]:
    with server.db() as conn:
        rows = conn.execute(
            """SELECT p.player_id,p.display_name,p.debut_year,p.final_year,p.primary_pos,
                      array_agg(DISTINCT st.name) FILTER (WHERE st.name IS NOT NULL)
                 FROM sport_players p
                 JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
                 LEFT JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
                 LEFT JOIN sport_teams st ON st.sport_id=a.sport_id AND st.team_id=a.team_id AND st.season=a.season
                WHERE p.sport_id=%s AND h.status IN ('placeholder','missing')
                GROUP BY p.player_id,p.display_name,p.debut_year,p.final_year,p.primary_pos
                ORDER BY p.final_year DESC NULLS LAST,p.display_name,p.player_id""",
            (sport,),
        ).fetchall()
    return [
        {"player_id": player_id, "name": name, "debut": debut, "final": final, "position": pos or "", "teams": teams or []}
        for player_id, name, debut, final, pos, teams in rows
    ]


def score_candidate(row: dict, candidate: dict) -> int:
    debut = row.get("debut") or candidate["debut"]
    final = row.get("final") or candidate["final"]
    return abs(candidate["debut"] - int(debut)) + abs(candidate["final"] - int(final))


def choose_candidate(row: dict, candidates: list[dict]) -> tuple[dict | None, str]:
    if not candidates:
        return None, "no Reference index name match"
    ranked = sorted(candidates, key=lambda item: score_candidate(row, item))
    best_score = score_candidate(row, ranked[0])
    tied = [item for item in ranked if score_candidate(row, item) == best_score]
    if len(tied) > 1:
        return None, "ambiguous Reference index match"
    if best_score > 4:
        return None, f"career years too far from Reference match ({best_score})"
    return ranked[0], ""


def extract_headshot_url(sport: str, slug: str, letter: str) -> tuple[str | None, str]:
    cfg = CONFIG[sport]
    url = f"{cfg['base']}/players/{letter}/{slug}.html"
    status, text = page_get(url)
    if status != 200:
        return None, f"page HTTP {status}"
    matches = re.findall(r'https://[^"\']+/req/[^"\']+/images/headshots/[^"\']+\.(?:jpg|png)', text)
    matches += [f"{cfg['base']}{path}" for path in re.findall(r'(/req/[^"\']+/images/headshots/[^"\']+\.(?:jpg|png))', text)]
    for image_url in matches:
        if slug in image_url:
            return image_url, ""
    return (matches[0], "") if matches else (None, "no Reference photo URL")


def placeholder_fingerprints(sport: str) -> tuple[set[str], set[str]]:
    hashes, perceptual = set(), set()
    for url in KNOWN_PLACEHOLDER_URLS.get(sport, []):
        image = fetch(url)
        if image.get("status") == "ok":
            hashes.add(image["sha256"])
            perceptual.add(image["perceptual_hash"])
    return hashes, perceptual


def reject_reason(image: dict, hashes: set[str], perceptual: set[str]) -> str | None:
    if image.get("status") != "ok":
        return image.get("error") or image.get("status") or "not ok"
    if image.get("sha256") in hashes:
        return "known placeholder hash"
    if any(hamming(image.get("perceptual_hash", ""), candidate) <= 4 for candidate in perceptual):
        return "known placeholder perceptual match"
    if int(image.get("width") or 0) < 80 or int(image.get("height") or 0) < 80:
        return f"image too small: {image.get('width')}x{image.get('height')}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=sorted(CONFIG), required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()

    cfg = CONFIG[args.sport]
    index = reference_index(args.sport)
    rows = unresolved_players(args.sport)
    if args.limit:
        rows = rows[:args.limit]
    hashes, perceptual = placeholder_fingerprints(args.sport)
    results = []
    promoted = []
    for number, row in enumerate(rows, 1):
        candidate, note = choose_candidate(row, index.get(normalize(row["name"]), []))
        if not candidate:
            results.append({**row, "status": "unmatched", "reference_id": "", "source_url": "", "note": note})
        else:
            image_url, image_note = extract_headshot_url(args.sport, candidate["slug"], candidate["letter"])
            if not image_url:
                results.append({**row, "status": "missing", "reference_id": candidate["slug"], "source_url": "", "note": image_note})
            else:
                image = fetch(image_url)
                reason = reject_reason(image, hashes, perceptual)
                if reason:
                    results.append({**row, "status": "rejected", "reference_id": candidate["slug"], "source_url": image_url, "note": reason})
                else:
                    note = f"{cfg['provider']} player-page headshot; matched by name and career years ({candidate['debut']}-{candidate['final']})."
                    result = {**row, **image, "status": "verified", "reference_id": candidate["slug"], "source_url": image_url, "note": note}
                    results.append(result)
                    promoted.append(result)
        if number % 25 == 0 or number == len(rows):
            print(f"{args.sport}: checked {number}/{len(rows)}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    cfg["report"].parent.mkdir(parents=True, exist_ok=True)
    fields = ["player_id", "name", "debut", "final", "position", "status", "reference_id", "source_url", "note", "width", "height", "sha256", "perceptual_hash"]
    with cfg["report"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field, "") for field in fields})

    if not args.dry_run and promoted:
        with server.db() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
                   VALUES (%s,%s,%s,'verified',%s)
                   ON CONFLICT (sport_id,player_id,provider)
                   DO UPDATE SET status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",
                [(args.sport, row["player_id"], cfg["provider"], row["source_url"]) for row in promoted],
            )
            cur.executemany(
                """UPDATE player_headshots
                      SET source_url=%s,fallback_url=NULL,provider=%s,status='verified',
                          content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
                          reviewed_at=now(),review_note=%s
                    WHERE sport_id=%s AND player_id=%s""",
                [
                    (
                        row["source_url"], cfg["provider"], row["sha256"], row["perceptual_hash"],
                        row["width"], row["height"], row["note"], args.sport, row["player_id"],
                    )
                    for row in promoted
                ],
            )
            cur.executemany(
                """INSERT INTO sport_player_images (sport_id,player_id,source_url)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (sport_id,player_id)
                   DO UPDATE SET source_url=EXCLUDED.source_url""",
                [(args.sport, row["player_id"], row["source_url"]) for row in promoted],
            )
    print(f"{args.sport}: promoted {len(promoted)} Reference headshots.")
    print(f"Report: {cfg['report']}")


if __name__ == "__main__":
    main()
