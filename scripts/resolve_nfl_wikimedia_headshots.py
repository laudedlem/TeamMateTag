"""Promote strict, identity-matched Wikimedia NFL headshot candidates.

This is deliberately conservative. A Commons image is used only when its
Wikipedia article has the same player name, identifies an American-football
career, and mentions at least one team in TeamMateTag's career data. Every
promotion retains the article and license in ``review_note``.
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests

sys.path.insert(0, "web")
import server  # noqa: E402
from audit_runtime_headshots import fetch

USER_AGENT = "TeamMateTag headshot resolver/0.2.10 (contact: teammatetag.com)"
_local = threading.local()


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower().replace(" jr", "").replace(" sr", ""))


def session() -> requests.Session:
    value = getattr(_local, "session", None)
    if value is None:
        value = requests.Session()
        value.headers.update({"User-Agent": USER_AGENT})
        _local.session = value
    return value


def candidate(row: dict) -> dict:
    name, teams = row["name"], row["teams"]
    try:
        response = session().get(
            "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(name.replace(" ", "_")), timeout=20
        )
        if response.status_code != 200:
            return {"player_id": row["player_id"], "status": "no_article"}
        page = response.json()
        title, extract = page.get("title") or "", page.get("extract") or ""
        image = (page.get("thumbnail") or {}).get("source")
        name_parts, title_parts = name.lower().split(), title.lower().split()
        redirected_name = (bool(name_parts and title_parts) and name_parts[-1] == title_parts[-1]
                           and name_parts[0][0] == title_parts[0][0])
        if (norm(title) != norm(name) and not redirected_name) or not image or "upload.wikimedia.org" not in image:
            return {"player_id": row["player_id"], "status": "no_match"}
        lower = extract.lower()
        if "football" not in lower or not any(word in lower for word in ("nfl", "national football league", "professional football")):
            return {"player_id": row["player_id"], "status": "not_nfl"}
        # Team names can change while the franchise does not (for example,
        # San Diego/Los Angeles Chargers). The distinctive final team token is
        # sufficient only after the exact-name and football-career checks.
        team_match = next((team for team in teams if team.lower() in lower or
                           (len(team.split()[-1]) > 3 and team.split()[-1].lower() in lower)), None)
        if not team_match:
            return {"player_id": row["player_id"], "status": "no_team_match"}
        source_page = ((page.get("content_urls") or {}).get("desktop") or {}).get("page")
        # Commons rate-limits per-file metadata lookups much more aggressively
        # than article summaries. The source page is retained for the separate
        # attribution pass; Commons hosts freely licensed media only.
        license_name = "Wikimedia Commons media; attribution metadata pending"
        inspected = fetch(image.split("?")[0])
        if inspected["status"] != "ok" and inspected.get("error") != "HTTP 429":
            return {"player_id": row["player_id"], "status": "unavailable"}
        return {"player_id": row["player_id"], "status": "candidate", "url": image.split("?")[0],
                "source_page": source_page, "license": license_name, "team": team_match,
                "image": inspected if inspected["status"] == "ok" else None}
    except requests.RequestException:
        return {"player_id": row["player_id"], "status": "request_failed"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0, help="Skip this many currently unresolved players.")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    server.ensure_runtime_schema()
    with server.db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS player_headshot_source_attempts (
            sport_id TEXT NOT NULL, player_id TEXT NOT NULL, provider TEXT NOT NULL,
            status TEXT NOT NULL, source_url TEXT, checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (sport_id, player_id, provider))""")
        rows = conn.execute("""
            SELECT p.player_id, p.display_name,
                   array_agg(DISTINCT t.name) FILTER (WHERE t.name IS NOT NULL) AS teams
              FROM sport_players p
              JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
              LEFT JOIN player_headshot_source_attempts tried
                ON tried.sport_id=p.sport_id AND tried.player_id=p.player_id AND tried.provider='Wikimedia Commons'
              LEFT JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
              LEFT JOIN sport_teams t ON t.sport_id=a.sport_id AND t.team_id=a.team_id AND t.season=a.season
             WHERE p.sport_id='football' AND h.status IN ('placeholder', 'missing') AND tried.player_id IS NULL
             GROUP BY p.player_id, p.display_name
             ORDER BY COALESCE(SUM(a.games_total), 0) DESC, p.player_id
        """).fetchall()
    jobs = [{"player_id": player_id, "name": name, "teams": teams or []} for player_id, name, teams in rows]
    if args.offset:
        jobs = jobs[args.offset:]
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"Searching strict Wikimedia candidates for {len(jobs):,} NFL gaps.", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(candidate, row) for row in jobs]
        for index, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if index % 250 == 0 or index == len(jobs):
                print(f"  checked {index:,}/{len(jobs):,}", flush=True)
    promoted = [result for result in results if result["status"] == "candidate"]
    with server.db() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO player_headshot_source_attempts (sport_id, player_id, provider, status, source_url)
                VALUES ('football', %s, 'Wikimedia Commons', %s, %s)
                ON CONFLICT (sport_id, player_id, provider) DO UPDATE SET
                  status=EXCLUDED.status, source_url=EXCLUDED.source_url, checked_at=now()
            """, [(result["player_id"], result["status"], result.get("source_page")) for result in results])
            cur.executemany("""
                UPDATE player_headshots SET source_url=%s, fallback_url=NULL, provider='Wikimedia Commons',
                    status='verified', content_sha256=%s, perceptual_hash=%s, width=%s, height=%s,
                    review_note=%s
                WHERE sport_id='football' AND player_id=%s
            """, [
                (result["url"], (result["image"] or {}).get("sha256"), (result["image"] or {}).get("perceptual_hash"),
                 (result["image"] or {}).get("width"), (result["image"] or {}).get("height"),
                 f"{result['license']}; {result['source_page']}; matched team: {result['team']}.", result["player_id"])
                for result in promoted
            ])
            cur.executemany("""
                INSERT INTO sport_player_images (sport_id, player_id, source_url) VALUES ('football', %s, %s)
                ON CONFLICT (sport_id, player_id) DO UPDATE SET source_url=EXCLUDED.source_url
            """, [(result["player_id"], result["url"]) for result in promoted])
    print(f"Promoted {len(promoted):,} Wikimedia candidates. Other results: {dict(Counter(r['status'] for r in results if r['status'] != 'candidate'))}")


if __name__ == "__main__":
    main()
