"""Resolve remaining NFL headshot gaps from web image search.

This is a playtest-oriented fallback after nflverse/NFL.com, ESPN,
TheSportsDB, and Wikimedia have been exhausted. It targets records still
classified as placeholder/missing in player_headshots and validates the image
bytes before promotion.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

REPORT = ROOT / "raw" / "nfl_web_image_headshots.csv"
USER_AGENT = "Mozilla/5.0 TeamMateTag NFL headshot resolver/0.2.14"


def norm_compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(value))


def unresolved_players() -> list[dict]:
    with server.db() as conn:
        rows = conn.execute(
            """SELECT p.player_id,p.display_name,p.debut_year,p.final_year,p.primary_pos,h.status,
                      COALESCE(SUM(a.games_total), 0) AS career_games,
                      array_agg(DISTINCT st.name) FILTER (WHERE st.name IS NOT NULL)
                 FROM sport_players p
                 JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
                 LEFT JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
                 LEFT JOIN sport_teams st ON st.sport_id=a.sport_id AND st.team_id=a.team_id AND st.season=a.season
                WHERE p.sport_id='football' AND p.final_year>=2000
                  AND h.status IN ('placeholder','missing')
                GROUP BY p.player_id,p.display_name,p.debut_year,p.final_year,p.primary_pos,h.status
                ORDER BY career_games DESC, p.final_year DESC NULLS LAST, p.display_name,p.player_id"""
        ).fetchall()
    name_counts = Counter(name for _, name, *_ in rows)
    return [
        {
            "player_id": player_id, "name": name, "debut": debut, "final": final,
            "position": pos or "", "status": status, "career_games": int(career_games or 0),
            "teams": teams or [], "ambiguous_name": name_counts[name] > 1,
        }
        for player_id, name, debut, final, pos, status, career_games, teams in rows
    ]


def placeholder_fingerprints() -> tuple[set[str], set[str]]:
    hashes, perceptual = set(), set()
    for url in KNOWN_PLACEHOLDER_URLS["football"] + [
        "https://a.espncdn.com/i/headshots/nfl/players/full/9643.png"
    ]:
        image = fetch(url)
        if image.get("status") == "ok":
            hashes.add(image["sha256"])
            perceptual.add(image["perceptual_hash"])
    with server.db() as conn:
        for digest, phash in conn.execute(
            """SELECT content_sha256, perceptual_hash
                 FROM player_headshots
                WHERE sport_id='football' AND status='placeholder'"""
        ).fetchall():
            if digest:
                hashes.add(digest)
            if phash:
                perceptual.add(phash)
    return hashes, perceptual


def ddg_vqd(session: requests.Session, query: str) -> str | None:
    try:
        response = session.get("https://duckduckgo.com/", params={"q": query}, timeout=20)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    match = re.search(r"vqd=([\"']?)([\d-]+)\1", response.text)
    return match.group(2) if match else None


def image_results(session: requests.Session, query: str) -> list[dict]:
    vqd = ddg_vqd(session, query)
    if not vqd:
        return []
    try:
        response = session.get(
            "https://duckduckgo.com/i.js",
            params={"q": query, "o": "json", "p": "1", "s": "0", "u": "bing", "f": ",,,", "l": "us-en", "vqd": vqd},
            headers={"Referer": "https://duckduckgo.com/"},
            timeout=25,
        )
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    try:
        return response.json().get("results") or []
    except ValueError:
        return []


def team_cue(row: dict, text: str) -> str | None:
    lower = normalize(text)
    for team in row["teams"]:
        team_lower = normalize(team)
        nickname = normalize(team.split()[-1])
        if team_lower in lower or (len(nickname) > 3 and nickname in lower):
            return team
    return None


def name_evidence(row: dict, item: dict) -> bool:
    name = norm_compact(row["name"])
    haystack = norm_compact(" ".join(str(item.get(key) or "") for key in ("title", "image", "url")))
    if name in haystack:
        return True
    pieces = normalize(row["name"]).split()
    tokens = set(re.findall(r"[a-z0-9]+", normalize(" ".join(str(item.get(key) or "") for key in ("title", "image", "url")))))
    return bool(pieces) and all(piece in tokens for piece in pieces)


def result_score(row: dict, item: dict) -> int:
    text = " ".join(str(item.get(key) or "") for key in ("title", "image", "url")).lower()
    score = 0
    if "headshot" in text:
        score += 8
    if "portrait" in text or "poses" in text or "media day" in text:
        score += 5
    if "nfl.com" in text or "espn" in text or "pro-football-reference" in text or "sports-reference" in text:
        score += 5
    if "gettyimages" in text or "alamy" in text or "zimbio" in text or "usatoday" in text:
        score += 3
    if "football" in text or "nfl" in text:
        score += 4
    if team_cue(row, text):
        score += 4
    if row["ambiguous_name"] and not team_cue(row, text):
        score -= 20
    return score


def reject_reason(image: dict, hashes: set[str], perceptual: set[str]) -> str | None:
    if image.get("status") != "ok":
        return image.get("error") or image.get("status") or "not ok"
    if image.get("sha256") in hashes:
        return "known NFL placeholder hash"
    if any(hamming(image.get("perceptual_hash", ""), phash) <= 4 for phash in perceptual):
        return "known NFL placeholder perceptual match"
    if int(image.get("width") or 0) < 80 or int(image.get("height") or 0) < 80:
        return f"image too small: {image.get('width')}x{image.get('height')}"
    return None


def candidate(session: requests.Session, row: dict, hashes: set[str], perceptual: set[str]) -> dict:
    queries = [
        f'"{row["name"]}" NFL headshot',
        f'"{row["name"]}" football portrait',
    ]
    seen = set()
    candidates = []
    for query in queries:
        for item in image_results(session, query):
            key = item.get("image") or item.get("url")
            if key in seen or not name_evidence(row, item):
                continue
            seen.add(key)
            item["_query"] = query
            candidates.append(item)
        time.sleep(0.15)
    candidates.sort(key=lambda item: result_score(row, item), reverse=True)
    notes = []
    for item in candidates[:10]:
        score = result_score(row, item)
        if score < 4:
            notes.append(f"low score: {item.get('title')}")
            continue
        image_url = item.get("image") or ""
        if not image_url.startswith(("https://", "http://")):
            continue
        image = fetch(image_url)
        reason = reject_reason(image, hashes, perceptual)
        if reason:
            notes.append(f"{item.get('title')}: {reason}")
            continue
        return {
            **row, **image, "result_status": "verified", "source_url": image_url,
            "source_page": item.get("url") or "", "title": item.get("title") or "",
            "note": f"Web image search exact-name match; score {score}; query {item.get('_query')}.",
        }
    return {**row, "result_status": "needs_review", "source_url": "", "source_page": "", "title": "", "note": " | ".join(notes[:5]) or "no exact-name image result"}


def persist_promoted(rows: list[dict]) -> int:
    promoted = [row for row in rows if row["result_status"] == "verified"]
    if not promoted:
        return 0
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
               VALUES ('football',%s,'Web image search','verified',%s)
               ON CONFLICT (sport_id,player_id,provider)
               DO UPDATE SET status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",
            [(row["player_id"], row["source_page"] or row["source_url"]) for row in promoted],
        )
        cur.executemany(
            """UPDATE player_headshots SET source_url=%s,fallback_url=NULL,provider='Web image search',
                  status='verified',content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
                  reviewed_at=now(),review_note=%s
               WHERE sport_id='football' AND player_id=%s""",
            [
                (
                    row["source_url"], row["sha256"], row["perceptual_hash"], row["width"], row["height"],
                    f"{row['note']} Source page: {row['source_page']}", row["player_id"],
                )
                for row in promoted
            ],
        )
        cur.executemany(
            """INSERT INTO sport_player_images (sport_id,player_id,source_url)
               VALUES ('football',%s,%s)
               ON CONFLICT (sport_id,player_id)
               DO UPDATE SET source_url=EXCLUDED.source_url""",
            [(row["player_id"], row["source_url"]) for row in promoted],
        )
    return len(promoted)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.8)
    parser.add_argument("--flush-every", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = unresolved_players()
    if args.offset:
        rows = rows[args.offset:]
    if args.limit:
        rows = rows[:args.limit]
    hashes, perceptual = placeholder_fingerprints()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    results = []
    batch = []
    promoted_total = 0
    for index, row in enumerate(rows, 1):
        result = candidate(session, row, hashes, perceptual)
        results.append(result)
        batch.append(result)
        if not args.dry_run and args.flush_every > 0 and len(batch) >= args.flush_every:
            promoted = persist_promoted(batch)
            promoted_total += promoted
            if promoted:
                print(f"promoted {promoted_total} so far", flush=True)
            batch.clear()
        if index % 10 == 0 or index == len(rows):
            print(f"checked {index}/{len(rows)}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    if not args.dry_run and batch:
        promoted = persist_promoted(batch)
        promoted_total += promoted

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["player_id", "name", "debut", "final", "position", "status", "career_games", "result_status", "title", "source_url", "source_page", "note", "width", "height"]
    with REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field, "") for field in fields})

    promoted = [row for row in results if row["result_status"] == "verified"]
    print(f"Promoted {len(promoted) if args.dry_run else promoted_total} NFL web-image headshots.")
    print(f"Other results: {dict(Counter(row['result_status'] for row in results if row['result_status'] != 'verified'))}")
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()
