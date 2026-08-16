"""Collect loose NFL headshot candidates for human review.

This does not promote images into the game. It downloads likely candidates into
``raw/nfl_headshot_review/pending`` and writes an HTML review sheet. Delete the
bad images from that folder, then run ``import_nfl_reviewed_headshots.py`` to
crop/upload the remaining files.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

OUT = ROOT / "raw" / "nfl_headshot_review"
PENDING = OUT / "pending"
REJECTED = OUT / "rejected"
META = OUT / "candidates.csv"
HTML = OUT / "review.html"
USER_AGENT = "Mozilla/5.0 TeamMateTag NFL candidate collector/0.2.15"


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def unresolved_players(include_attempted: bool = False) -> list[dict]:
    attempted_filter = ""
    if not include_attempted:
        attempted_filter = """
          AND NOT EXISTS (
                SELECT 1 FROM player_headshot_source_attempts x
                 WHERE x.sport_id=p.sport_id AND x.player_id=p.player_id
                   AND x.provider='Human reviewed web candidate'
                   AND x.status IN ('verified','rejected','needs_review')
          )
        """
    with server.db() as conn:
        rows = conn.execute(
            f"""SELECT p.player_id,p.display_name,p.debut_year,p.final_year,p.primary_pos,h.status,
                      COALESCE(SUM(a.games_total), 0) AS career_games,
                      array_agg(DISTINCT st.name) FILTER (WHERE st.name IS NOT NULL)
                 FROM sport_players p
                 JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
                 LEFT JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
                 LEFT JOIN sport_teams st ON st.sport_id=a.sport_id AND st.team_id=a.team_id AND st.season=a.season
                WHERE p.sport_id='football' AND p.final_year>=2000
                  AND h.status IN ('placeholder','missing','wrong_player','bad_crop')
                  {attempted_filter}
                GROUP BY p.player_id,p.display_name,p.debut_year,p.final_year,p.primary_pos,h.status
                ORDER BY career_games DESC, p.final_year DESC NULLS LAST, p.display_name,p.player_id"""
        ).fetchall()
    return [
        {
            "player_id": pid,
            "name": name,
            "debut": debut,
            "final": final,
            "position": pos or "",
            "status": status,
            "career_games": int(games or 0),
            "teams": teams or [],
        }
        for pid, name, debut, final, pos, status, games, teams in rows
    ]


def placeholder_fingerprints() -> tuple[set[str], set[str]]:
    hashes, perceptual = set(), set()
    for url in KNOWN_PLACEHOLDER_URLS["football"]:
        image = fetch(url)
        if image.get("status") == "ok":
            hashes.add(image["sha256"])
            perceptual.add(image["perceptual_hash"])
    with server.db() as conn:
        for digest, phash in conn.execute(
            "SELECT content_sha256, perceptual_hash FROM player_headshots WHERE sport_id='football' AND status='placeholder'"
        ):
            if digest:
                hashes.add(digest)
            if phash:
                perceptual.add(phash)
    return hashes, perceptual


def ddg_vqd(session: requests.Session, query: str) -> str | None:
    try:
        response = session.get("https://duckduckgo.com/", params={"q": query}, timeout=12)
    except requests.RequestException:
        return None
    match = re.search(r"vqd=([\"']?)([\d-]+)\1", response.text)
    return match.group(2) if response.status_code == 200 and match else None


def image_results(session: requests.Session, query: str) -> list[dict]:
    vqd = ddg_vqd(session, query)
    if not vqd:
        return []
    try:
        response = session.get(
            "https://duckduckgo.com/i.js",
            params={"q": query, "o": "json", "p": "1", "s": "0", "u": "bing", "f": ",,,", "l": "us-en", "vqd": vqd},
            headers={"Referer": "https://duckduckgo.com/"},
            timeout=15,
        )
        return response.json().get("results") if response.status_code == 200 else []
    except Exception:
        return []


def has_name_evidence(row: dict, item: dict) -> bool:
    haystack = normalize(" ".join(str(item.get(key) or "") for key in ("title", "image", "url")))
    parts = [part for part in normalize(row["name"]).split() if len(part) > 1]
    return bool(parts) and all(part in haystack for part in parts)


def score(row: dict, item: dict) -> int:
    text = normalize(" ".join(str(item.get(key) or "") for key in ("title", "image", "url")))
    value = 0
    if "headshot" in text or "portrait" in text:
        value += 8
    if "nfl" in text or "football" in text:
        value += 5
    if any(normalize(team) in text or normalize(team.split()[-1]) in text for team in row["teams"]):
        value += 7
    if any(source in text for source in ("getty", "alamy", "zimbio", "usatoday", "espn", "nfl.com", "footballdb")):
        value += 3
    return value


def valid_image(url: str, hashes: set[str], perceptual: set[str]) -> tuple[bool, bytes | None, str]:
    image = fetch(url)
    if image.get("status") != "ok":
        return False, None, image.get("error") or image.get("status") or "not ok"
    if image.get("sha256") in hashes:
        return False, None, "placeholder hash"
    if any(hamming(image.get("perceptual_hash", ""), phash) <= 4 for phash in perceptual):
        return False, None, "placeholder perceptual match"
    if int(image.get("width") or 0) < 120 or int(image.get("height") or 0) < 120:
        return False, None, f"too small {image.get('width')}x{image.get('height')}"
    try:
        content = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20).content
        opened = Image.open(io.BytesIO(content))
        opened.verify()
        return True, content, ""
    except Exception as exc:
        return False, None, str(exc)[:160]


def collect_one(row: dict, hashes: set[str], perceptual: set[str], max_results: int) -> dict:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    queries = [f'"{row["name"]}" football headshot']
    if row["teams"]:
        queries.insert(0, f'"{row["name"]}" "{row["teams"][0]}" football')
    seen = set()
    items = []
    for query in queries:
        for item in image_results(session, query):
            image_url = item.get("image") or ""
            if not image_url or image_url in seen or not image_url.startswith(("http://", "https://")):
                continue
            if not has_name_evidence(row, item):
                continue
            seen.add(image_url)
            item["_query"] = query
            items.append(item)
        time.sleep(0.05)
    items.sort(key=lambda item: score(row, item), reverse=True)
    notes = []
    for item in items[:max_results]:
        ok, content, reason = valid_image(item.get("image") or "", hashes, perceptual)
        if not ok:
            notes.append(reason)
            continue
        ext = ".jpg"
        try:
            image = Image.open(io.BytesIO(content))
            image = ImageOps.exif_transpose(image).convert("RGB")
            filename = f"{safe(row['player_id'])}.jpg"
            path = PENDING / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, format="JPEG", quality=90)
        except Exception as exc:
            return {**row, "result_status": "failed", "note": str(exc)[:160]}
        return {
            **row,
            "result_status": "candidate",
            "candidate_path": str(path),
            "source_url": item.get("image") or "",
            "source_page": item.get("url") or "",
            "title": item.get("title") or "",
            "query": item.get("_query") or "",
            "note": f"score {score(row, item)}",
        }
    return {**row, "result_status": "none", "note": " | ".join(notes[:4]) or "no candidate"}


def write_outputs(rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fields = [
        "player_id", "name", "debut", "final", "position", "career_games", "teams",
        "result_status", "candidate_path", "source_url", "source_page", "title", "query", "note",
    ]
    with META.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            value = dict(row)
            value["teams"] = " | ".join(row.get("teams") or [])
            writer.writerow({field: value.get(field, "") for field in fields})
    cards = []
    for row in rows:
        if row.get("result_status") != "candidate":
            continue
        rel = Path(row["candidate_path"]).relative_to(OUT).as_posix()
        cards.append(
            f"""<article><img src="{rel}"><h3>{row['name']}</h3>
            <p>{row['debut']}-{row['final']} | {row.get('position','')} | {row.get('career_games',0)} games</p>
            <p>{', '.join((row.get('teams') or [])[:4])}</p>
            <p><a href="{row['source_url']}">image</a> | <a href="{row['source_page']}">page</a></p></article>"""
        )
    HTML.write_text(
        """<!doctype html><meta charset="utf-8"><title>NFL Headshot Review</title>
        <style>body{font-family:Arial;background:#111;color:#eee}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}article{background:#1d1d1d;padding:12px;border-radius:8px}img{width:100%;height:260px;object-fit:cover;background:#333}a{color:#8cc8ff}</style>
        <h1>NFL Headshot Review</h1><p>Delete bad images from pending, then run import_nfl_reviewed_headshots.py.</p><main>"""
        + "\n".join(cards)
        + "</main>",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-results", type=int, default=8)
    parser.add_argument("--include-attempted", action="store_true")
    args = parser.parse_args()

    PENDING.mkdir(parents=True, exist_ok=True)
    REJECTED.mkdir(parents=True, exist_ok=True)
    rows = unresolved_players(include_attempted=args.include_attempted)
    if args.offset:
        rows = rows[args.offset:]
    if args.limit:
        rows = rows[:args.limit]
    hashes, perceptual = placeholder_fingerprints()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(collect_one, row, hashes, perceptual, args.max_results): row for row in rows}
        for index, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if index % 10 == 0 or index == len(rows):
                found = sum(row.get("result_status") == "candidate" for row in results)
                print(f"checked {index}/{len(rows)} candidates={found}", flush=True)
    write_outputs(results)
    print(f"Candidates: {sum(row.get('result_status') == 'candidate' for row in results)}")
    print(f"Review folder: {PENDING}")
    print(f"Review page: {HTML}")


if __name__ == "__main__":
    main()
