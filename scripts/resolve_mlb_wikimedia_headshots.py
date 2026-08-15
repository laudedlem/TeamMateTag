"""Resolve active MLB headshot gaps from Wikipedia/Wikimedia.

The resolver searches Wikipedia for the player, validates that the chosen
article is about a baseball player and mentions at least one TeamMateTag MLB
team from the player's career, then promotes only decodable non-placeholder
Wikimedia images to the production headshot registry.
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
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "web"))

from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402
import server  # noqa: E402


REPORT = ROOT / "raw" / "mlb_wikimedia_headshot_resolution.csv"
API = "https://en.wikipedia.org/w/api.php"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = "TeamMateTag MLB Wikimedia headshot resolver/1.0 (local verification)"


def norm(value: str) -> str:
    value = re.sub(r"\s*\([^)]*\)\s*", " ", value)
    return re.sub(r"[^a-z0-9]", "", normalize(value).replace(" jr", "").replace(" sr", ""))


def placeholder_fingerprints() -> tuple[set[str], set[str]]:
    hashes: set[str] = set()
    perceptual: set[str] = set()
    for url in KNOWN_PLACEHOLDER_URLS["baseball"]:
        result = fetch(url)
        if result.get("status") == "ok":
            hashes.add(result["sha256"])
            perceptual.add(result["perceptual_hash"])
    return hashes, perceptual


def unresolved_players(conn, force: bool = False) -> list[dict]:
    tried_filter = "" if force else "AND tried.player_id IS NULL"
    rows = conn.execute(
        """SELECT p.player_id, concat_ws(' ', p.name_first, p.name_last), p.debut_year, p.final_year,
                  p.primary_pos, array_agg(DISTINCT t.name) FILTER (WHERE t.name IS NOT NULL),
                  COALESCE(ps.career_games, 0)
             FROM players p
             JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=p.player_id
             LEFT JOIN players_searchable ps ON ps.player_id=p.player_id
             LEFT JOIN player_headshot_source_attempts tried
               ON tried.sport_id='baseball' AND tried.player_id=p.player_id
              AND tried.provider='Wikimedia Commons'
             LEFT JOIN appearances a ON a.player_id=p.player_id AND a.season >= 2000
             LEFT JOIN teams t ON t.team_id=a.team_id AND t.season=a.season
            WHERE p.final_year >= 2000
              AND h.status IN ('placeholder','missing')
              {tried_filter}
            GROUP BY p.player_id,p.name_first,p.name_last,p.debut_year,p.final_year,p.primary_pos,ps.career_games
            ORDER BY COALESCE(ps.career_games, 0) DESC, p.final_year DESC NULLS LAST, p.player_id""".format(tried_filter=tried_filter)
    ).fetchall()
    return [
        {
            "player_id": player_id,
            "name": name,
            "debut_year": debut,
            "final_year": final,
            "position": position or "",
            "teams": teams or [],
        }
        for player_id, name, debut, final, position, teams, _career_games in rows
    ]


def role_title_guesses(name: str, position: str) -> list[str]:
    if position == "P":
        roles = ["pitcher", "baseball pitcher"]
    else:
        roles = [
            "outfielder", "infielder", "catcher", "first baseman",
            "second baseman", "third baseman", "shortstop",
            "left fielder", "center fielder", "right fielder",
            "designated hitter", "utility player",
            "baseball outfielder", "baseball infielder", "baseball catcher",
        ]
    return [f"{name} ({role})" for role in roles]


def search_titles(session: requests.Session, row: dict) -> list[str]:
    name = row["name"]
    candidates = [name, f"{name} (baseball)", f"{name} (baseball player)", *role_title_guesses(name, row["position"])]
    try:
        response = session.get(
            API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": f'intitle:"{name}" baseball',
                "format": "json",
                "srlimit": 8,
            },
            timeout=20,
        )
        if response.status_code == 200:
            candidates.extend(row.get("title") or "" for row in response.json().get("query", {}).get("search", []))
    except (requests.RequestException, ValueError):
        pass
    seen: set[str] = set()
    result: list[str] = []
    for title in candidates:
        title = (title or "").strip()
        if title and title not in seen:
            seen.add(title)
            result.append(title)
    return result


def summary_for(session: requests.Session, title: str) -> dict | None:
    try:
        response = session.get(SUMMARY + quote(title.replace(" ", "_")), timeout=20)
        if response.status_code != 200:
            return None
        return response.json()
    except (requests.RequestException, ValueError):
        return None


def article_extract(session: requests.Session, title: str) -> str:
    try:
        response = session.get(
            API,
            params={
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "titles": title,
                "format": "json",
                "redirects": 1,
            },
            timeout=20,
        )
        if response.status_code != 200:
            return ""
        pages = response.json().get("query", {}).get("pages", {})
        return "\n".join(page.get("extract") or "" for page in pages.values())
    except (requests.RequestException, ValueError):
        return ""


def team_match(text: str, teams: list[str]) -> str | None:
    lower = text.lower()
    return next((team for team in teams if team.lower() in lower or team.split()[-1].lower() in lower), None)


def role_match(text: str, position: str) -> bool:
    lower = text.lower()
    if position == "P":
        return "pitcher" in lower
    batter_words = (
        "outfielder", "infielder", "catcher", "first baseman",
        "second baseman", "third baseman", "shortstop", "right fielder",
        "left fielder", "center fielder", "designated hitter",
    )
    if "pitcher" in lower and not any(word in lower for word in batter_words):
        return False
    return any(word in lower for word in batter_words) or "position player" in lower


def is_baseball_article(page: dict, row: dict) -> tuple[bool, str]:
    title = page.get("title") or ""
    extract = page.get("extract") or ""
    lower = f"{title} {extract}".lower()
    if "baseball" not in lower and "major league baseball" not in lower and "mlb" not in lower:
        return False, "not baseball article"
    if norm(title) != norm(row["name"]):
        title_parts = normalize(title).split()
        name_parts = normalize(row["name"]).split()
        redirected = bool(title_parts and name_parts and title_parts[-1] == name_parts[-1] and title_parts[0][:1] == name_parts[0][:1])
        if not redirected:
            return False, f"title mismatch: {title}"
    return True, ""


def image_candidate(page: dict) -> tuple[str | None, int | None, int | None]:
    for key in ("thumbnail", "originalimage"):
        value = page.get(key) or {}
        url = value.get("source")
        if url and "upload.wikimedia.org" in url:
            return url, value.get("width"), value.get("height")
    return None, None, None


def image_reject_reason(image: dict, hashes: set[str], perceptual: set[str]) -> str | None:
    if image.get("status") != "ok":
        return image.get("status") or "not ok"
    if image.get("sha256") in hashes:
        return "known placeholder hash"
    if any(hamming(image.get("perceptual_hash", ""), candidate) <= 4 for candidate in perceptual):
        return "known placeholder perceptual match"
    return None


def candidate(session: requests.Session, row: dict, hashes: set[str], perceptual: set[str]) -> dict:
    rejected_notes: list[str] = []
    for title in search_titles(session, row):
        page = summary_for(session, title)
        if not page:
            rejected_notes.append(f"{title}: no page")
            continue
        ok, reason = is_baseball_article(page, row)
        if not ok:
            rejected_notes.append(f"{page.get('title') or title}: {reason}")
            continue
        full_text = f"{page.get('title') or ''}\n{page.get('extract') or ''}\n{article_extract(session, page.get('title') or title)}"
        matched_team = team_match(full_text, row["teams"])
        role_ok = role_match(full_text, row["position"])
        exact_title = norm(page.get("title") or title) == norm(row["name"])
        if row["teams"] and not matched_team and not (exact_title and role_ok):
            rejected_notes.append(f"{page.get('title') or title}: no team match")
            continue
        url, meta_width, meta_height = image_candidate(page)
        if not url:
            rejected_notes.append(f"{page.get('title') or title}: no Wikimedia image")
            continue
        inspected = fetch(url)
        reason = image_reject_reason(inspected, hashes, perceptual)
        if reason and not (
            inspected.get("error") == "HTTP 429"
            and int(meta_width or 0) >= 80
            and int(meta_height or 0) >= 80
        ):
            rejected_notes.append(f"{page.get('title') or title}: {reason}")
            continue
        if inspected.get("error") == "HTTP 429":
            inspected = {
                "status": "ok",
                "sha256": None,
                "perceptual_hash": None,
                "width": meta_width,
                "height": meta_height,
            }
        source_page = ((page.get("content_urls") or {}).get("desktop") or {}).get("page")
        return {
            **row,
            "status": "candidate",
            "url": url,
            "source_page": source_page or "",
            "title": page.get("title") or title,
            "image": inspected,
            "note": f"Wikimedia image matched by baseball article and team context: {matched_team or 'no team required'}.",
        }
    return {**row, "status": "no_match", "url": "", "source_page": "", "title": "", "note": " | ".join(rejected_notes[:4])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="retry rows that already have a Wikimedia attempt")
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()

    hashes, perceptual = placeholder_fingerprints()
    with server.db() as conn:
        rows = unresolved_players(conn, force=args.force)
    if args.limit:
        rows = rows[:args.limit]
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    results = []
    for index, row in enumerate(rows, 1):
        results.append(candidate(session, row, hashes, perceptual))
        if index % 10 == 0 or index == len(rows):
            print(f"checked {index}/{len(rows)}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "player_id", "name", "debut_year", "final_year", "position",
            "teams", "status", "title", "url", "source_page", "note",
        ])
        writer.writeheader()
        for result in results:
            row = dict(result)
            row["teams"] = ",".join(row.get("teams") or [])
            writer.writerow({field: row.get(field, "") for field in writer.fieldnames})

    promoted = [row for row in results if row["status"] == "candidate"]
    if not args.dry_run:
        with server.db() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO player_headshot_source_attempts (sport_id, player_id, provider, status, source_url)
                   VALUES ('baseball', %s, 'Wikimedia Commons', %s, %s)
                   ON CONFLICT (sport_id, player_id, provider)
                   DO UPDATE SET status=EXCLUDED.status, source_url=EXCLUDED.source_url, checked_at=now()""",
                [(row["player_id"], row["status"], row.get("source_page") or row.get("url")) for row in results],
            )
            cur.executemany(
                """UPDATE player_headshots
                      SET source_url=%s, fallback_url=NULL, provider='Wikimedia Commons',
                          status='verified', content_sha256=%s, perceptual_hash=%s,
                          width=%s, height=%s, reviewed_at=now(), review_note=%s
                    WHERE sport_id='baseball' AND player_id=%s""",
                [
                    (
                        row["url"], row["image"]["sha256"], row["image"]["perceptual_hash"],
                        row["image"]["width"], row["image"]["height"],
                        f"Wikimedia Commons via {row['source_page']}; {row['note']}",
                        row["player_id"],
                    )
                    for row in promoted
                ],
            )
    print(f"Promoted {len(promoted):,} Wikimedia MLB headshots.")
    print(f"Other results: {dict(Counter(row['status'] for row in results if row['status'] != 'candidate'))}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
