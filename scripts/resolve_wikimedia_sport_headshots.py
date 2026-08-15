"""Resolve basketball/hockey headshot gaps from Wikipedia/Wikimedia.

This is intentionally conservative: exact or near-name article, sport-specific
career language, birth-year mismatch rejection when local birth year exists,
team-context match, and non-placeholder image validation when possible.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

API = "https://en.wikipedia.org/w/api.php"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = "TeamMateTag Wikimedia sport headshot resolver/0.2.10"
CONFIG = {
    "basketball": {
        "sport_terms": ("basketball", "nba", "national basketball association"),
        "role_titles": ("basketball player", "NBA player"),
        "provider": "Wikimedia Commons",
        "report": ROOT / "raw" / "basketball_wikimedia_headshots.csv",
    },
    "hockey": {
        "sport_terms": ("ice hockey", "hockey", "nhl", "national hockey league"),
        "role_titles": ("ice hockey", "ice hockey player", "hockey player"),
        "provider": "Wikimedia Commons",
        "report": ROOT / "raw" / "hockey_wikimedia_headshots.csv",
    },
}


def norm(value: str) -> str:
    value = re.sub(r"\s*\([^)]*\)\s*", " ", value)
    return re.sub(r"[^a-z0-9]", "", normalize(value).replace(" jr", "").replace(" sr", ""))


def article_birth_year(text: str) -> int | None:
    match = re.search(r"\bborn\s+(?:[A-Z][a-z]+\s+\d{1,2},\s+)?((?:19|20)\d{2})\b", text)
    return int(match.group(1)) if match else None


def unresolved_players(sport: str, force: bool) -> list[dict]:
    tried_filter = "" if force else "AND tried.player_id IS NULL"
    with server.db() as conn:
        rows = conn.execute(
            f"""SELECT p.player_id,p.display_name,p.birth_year,p.debut_year,p.final_year,p.primary_pos,
                       array_agg(DISTINCT st.name) FILTER (WHERE st.name IS NOT NULL)
                  FROM sport_players p
                  JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
                  LEFT JOIN player_headshot_source_attempts tried
                    ON tried.sport_id=p.sport_id AND tried.player_id=p.player_id
                   AND tried.provider='Wikimedia Commons'
                  LEFT JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
                  LEFT JOIN sport_teams st ON st.sport_id=a.sport_id AND st.team_id=a.team_id AND st.season=a.season
                 WHERE p.sport_id=%s AND h.status IN ('placeholder','missing') {tried_filter}
                 GROUP BY p.player_id,p.display_name,p.birth_year,p.debut_year,p.final_year,p.primary_pos
                 ORDER BY p.final_year DESC NULLS LAST,p.display_name,p.player_id""",
            (sport,),
        ).fetchall()
    return [
        {
            "player_id": player_id, "name": name, "birth_year": birth_year,
            "debut": debut, "final": final, "position": pos or "", "teams": teams or [],
        }
        for player_id, name, birth_year, debut, final, pos, teams in rows
    ]


def placeholder_fingerprints(sport: str) -> tuple[set[str], set[str]]:
    hashes, perceptual = set(), set()
    for url in KNOWN_PLACEHOLDER_URLS.get(sport, []):
        image = fetch(url)
        if image.get("status") == "ok":
            hashes.add(image["sha256"])
            perceptual.add(image["perceptual_hash"])
    return hashes, perceptual


def search_titles(session: requests.Session, sport: str, name: str) -> list[str]:
    cfg = CONFIG[sport]
    titles = [name, *(f"{name} ({role})" for role in cfg["role_titles"])]
    try:
        response = session.get(
            API,
            params={"action": "query", "list": "search", "srsearch": f'intitle:"{name}" {cfg["role_titles"][0]}', "format": "json", "srlimit": 8},
            timeout=20,
        )
        if response.status_code == 200:
            titles.extend(item.get("title") or "" for item in response.json().get("query", {}).get("search", []))
    except (requests.RequestException, ValueError):
        pass
    seen, result = set(), []
    for title in titles:
        title = title.strip()
        if title and title not in seen:
            seen.add(title)
            result.append(title)
    return result


def summary_for(session: requests.Session, title: str) -> dict | None:
    try:
        response = session.get(SUMMARY + quote(title.replace(" ", "_")), timeout=20)
        return response.json() if response.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def article_extract(session: requests.Session, title: str) -> str:
    try:
        response = session.get(
            API,
            params={"action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "format": "json", "redirects": 1},
            timeout=20,
        )
        if response.status_code != 200:
            return ""
        return "\n".join(page.get("extract") or "" for page in response.json().get("query", {}).get("pages", {}).values())
    except (requests.RequestException, ValueError):
        return ""


def team_match(text: str, teams: list[str]) -> str | None:
    lower = text.lower()
    for team in teams:
        if team.lower() in lower:
            return team
        last = team.split()[-1].lower()
        if len(last) > 3 and last in lower:
            return team
    return None


def image_candidate(page: dict) -> tuple[str | None, int | None, int | None]:
    for key in ("thumbnail", "originalimage"):
        image = page.get(key) or {}
        url = image.get("source")
        if url and "upload.wikimedia.org" in url:
            return url.split("?")[0], image.get("width"), image.get("height")
    return None, None, None


def reject_reason(image: dict, hashes: set[str], perceptual: set[str]) -> str | None:
    if image.get("status") != "ok":
        return image.get("error") or image.get("status") or "not ok"
    if image.get("sha256") in hashes:
        return "known placeholder hash"
    if any(hamming(image.get("perceptual_hash", ""), candidate) <= 4 for candidate in perceptual):
        return "known placeholder perceptual match"
    return None


def candidate(session: requests.Session, sport: str, row: dict, hashes: set[str], perceptual: set[str]) -> dict:
    cfg = CONFIG[sport]
    notes = []
    for title in search_titles(session, sport, row["name"]):
        page = summary_for(session, title)
        if not page:
            notes.append(f"{title}: no page")
            continue
        description = page.get("description") or ""
        page_title = page.get("title") or title
        if page.get("type") == "disambiguation" or "topics referred to by the same term" in description.lower():
            notes.append(f"{page_title}: disambiguation")
            continue
        title_parts = normalize(page_title).split()
        name_parts = normalize(row["name"]).split()
        redirected = bool(title_parts and name_parts and title_parts[-1] == name_parts[-1] and title_parts[0][:1] == name_parts[0][:1])
        if norm(page_title) != norm(row["name"]) and not redirected:
            notes.append(f"{page_title}: title mismatch")
            continue
        full_text = f"{page_title}\n{description}\n{page.get('extract') or ''}\n{article_extract(session, page_title)}"
        lower = full_text.lower()
        if not any(term in lower for term in cfg["sport_terms"]):
            notes.append(f"{page_title}: sport mismatch")
            continue
        born = article_birth_year(full_text)
        if born and row.get("birth_year") and born != int(row["birth_year"]):
            notes.append(f"{page_title}: birth year mismatch {born}")
            continue
        matched_team = team_match(full_text, row["teams"])
        if row["teams"] and not matched_team:
            notes.append(f"{page_title}: no team match")
            continue
        url, width, height = image_candidate(page)
        if not url:
            notes.append(f"{page_title}: no image")
            continue
        image = fetch(url)
        reason = reject_reason(image, hashes, perceptual)
        if reason and not (reason == "HTTP 429" and int(width or 0) >= 80 and int(height or 0) >= 80):
            notes.append(f"{page_title}: {reason}")
            continue
        if image.get("status") != "ok":
            image = {"sha256": None, "perceptual_hash": None, "width": width, "height": height}
        source_page = ((page.get("content_urls") or {}).get("desktop") or {}).get("page") or ""
        return {
            **row, "status": "verified", "title": page_title, "source_url": url, "source_page": source_page,
            "note": f"Wikimedia image matched by sport article and team context: {matched_team or 'none'}.", **image,
        }
    return {**row, "status": "no_match", "title": "", "source_url": "", "source_page": "", "note": " | ".join(notes[:5])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=sorted(CONFIG), required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = unresolved_players(args.sport, args.force)
    if args.limit:
        rows = rows[:args.limit]
    hashes, perceptual = placeholder_fingerprints(args.sport)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    results = []
    for index, row in enumerate(rows, 1):
        results.append(candidate(session, args.sport, row, hashes, perceptual))
        if index % 25 == 0 or index == len(rows):
            print(f"{args.sport}: checked {index}/{len(rows)}", flush=True)
        if args.delay:
            time.sleep(args.delay)
    report = CONFIG[args.sport]["report"]
    report.parent.mkdir(parents=True, exist_ok=True)
    fields = ["player_id", "name", "debut", "final", "position", "status", "title", "source_url", "source_page", "note", "width", "height"]
    with report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field, "") for field in fields})
    promoted = [row for row in results if row["status"] == "verified"]
    if not args.dry_run and promoted:
        with server.db() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
                   VALUES (%s,%s,'Wikimedia Commons','verified',%s)
                   ON CONFLICT (sport_id,player_id,provider)
                   DO UPDATE SET status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",
                [(args.sport, row["player_id"], row["source_page"] or row["source_url"]) for row in promoted],
            )
            cur.executemany(
                """UPDATE player_headshots SET source_url=%s,fallback_url=NULL,provider='Wikimedia Commons',
                      status='verified',content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
                      reviewed_at=now(),review_note=%s
                   WHERE sport_id=%s AND player_id=%s""",
                [
                    (
                        row["source_url"], row.get("sha256"), row.get("perceptual_hash"), row.get("width"), row.get("height"),
                        f"Wikimedia Commons via {row['source_page']}; {row['note']}", args.sport, row["player_id"],
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
    print(f"{args.sport}: promoted {len(promoted)} Wikimedia headshots.")
    print(f"Other results: {dict(Counter(row['status'] for row in results if row['status'] != 'verified'))}")
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
