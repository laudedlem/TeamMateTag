"""Resolve flagged Hockey headshots from TheSportsDB using ESPN identity data."""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
from audit_runtime_headshots import fetch  # noqa: E402
from name_normalize import normalize  # noqa: E402

API = "https://www.thesportsdb.com/api/v1/json/123/searchplayers.php"
HEADERS = {"User-Agent": "TeamMateTag headshot resolver/0.2.10 (contact: teammatetag.com)"}
CONFIG = {
    "hockey": ("nhl", "Ice Hockey"),
}


def local_birth_dates(sport: str) -> dict[str, str]:
    return {}


def espn_identities(sport: str, league: str) -> dict[tuple[str, int], list[str]]:
    """Return unambiguous ESPN birth dates keyed by normalized name and birth year."""
    dates: dict[tuple[str, int], list[str]] = {}
    directory = ROOT / "raw" / f"espn_{league}_athlete_pages"
    for path in directory.glob("page_*.csv"):
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                birth = row.get("birth_date") or ""
                if len(birth) >= 4:
                    dates.setdefault((normalize(row.get("display_name") or ""), int(birth[:4])), []).append(birth)
    return {key: values for key, values in dates.items() if len(set(values)) == 1}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=tuple(CONFIG))
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--delay", type=float, default=2.5)
    args = parser.parse_args()
    league, expected_sport = CONFIG[args.sport]
    identities = espn_identities(args.sport, league)
    local_births = local_birth_dates(args.sport)
    if not identities:
        raise RuntimeError(f"No completed ESPN {league.upper()} identity index found.")
    server.ensure_runtime_schema()
    with server.db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS player_headshot_source_attempts (
            sport_id TEXT NOT NULL, player_id TEXT NOT NULL, provider TEXT NOT NULL,
            status TEXT NOT NULL, source_url TEXT, checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (sport_id, player_id, provider))""")
        if args.sport == "baseball":
            raw = conn.execute("""
                SELECT p.player_id, concat_ws(' ', p.name_first, p.name_last), p.birth_year
                FROM players p JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=p.player_id
                LEFT JOIN player_headshot_source_attempts tried ON tried.sport_id='baseball'
                  AND tried.player_id=p.player_id AND tried.provider='TheSportsDB'
                WHERE h.status IN ('placeholder','missing') AND tried.player_id IS NULL
                ORDER BY p.debut_year DESC NULLS LAST LIMIT %s
            """, (args.limit * 4,)).fetchall()
        else:
            raw = conn.execute("""
                SELECT p.player_id, p.display_name, p.birth_year
                FROM sport_players p JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
                LEFT JOIN player_headshot_source_attempts tried ON tried.sport_id=p.sport_id
                  AND tried.player_id=p.player_id AND tried.provider='TheSportsDB'
                WHERE p.sport_id=%s AND h.status IN ('placeholder','missing') AND tried.player_id IS NULL
                ORDER BY p.debut_year DESC NULLS LAST LIMIT %s
            """, (args.sport, args.limit * 4)).fetchall()
    rows = []
    for player_id, name, birth_year in raw:
        births = identities.get((normalize(name), birth_year)) if birth_year else None
        births = births or ([local_births[player_id]] if player_id in local_births else None)
        if births:
            rows.append((player_id, name, births))
    rows = rows[:args.limit]
    if not rows:
        print("No flagged players with an unambiguous completed ESPN identity match.")
        return
    results = []
    for index, (player_id, name, births) in enumerate(rows, 1):
        try:
            response = requests.get(API, params={"p": name}, headers=HEADERS, timeout=30)
            candidates = response.json().get("player") or [] if response.status_code == 200 else []
        except (requests.RequestException, ValueError):
            results.append((player_id, "transient_error", None, None)); continue
        match = next((item for item in candidates
                      if item.get("strSport") == expected_sport
                      and normalize(item.get("strPlayer") or "") == normalize(name)
                      and item.get("dateBorn") in births and item.get("strThumb")), None)
        if not match:
            results.append((player_id, "no_match", None, None))
        else:
            image = fetch(match["strThumb"])
            results.append((player_id, "candidate" if image["status"] == "ok" else "unavailable",
                            match["strThumb"] if image["status"] == "ok" else None, image))
        print(f"  checked {index}/{len(rows)}", flush=True)
        if index < len(rows): time.sleep(args.delay)
    promoted = [row for row in results if row[1] == "candidate"]
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany("""INSERT INTO player_headshot_source_attempts (sport_id, player_id, provider, status, source_url)
            VALUES (%s,%s,'TheSportsDB',%s,%s) ON CONFLICT (sport_id,player_id,provider) DO UPDATE SET
            status=EXCLUDED.status, source_url=EXCLUDED.source_url, checked_at=now()""",
            [(args.sport, player_id, status, url) for player_id, status, url, _ in results if status != "transient_error"])
        cur.executemany("""UPDATE player_headshots SET source_url=%s, fallback_url=NULL, provider='TheSportsDB', status='verified',
            content_sha256=%s, perceptual_hash=%s, width=%s, height=%s, review_note='ESPN-identity-matched TheSportsDB portrait.'
            WHERE sport_id=%s AND player_id=%s""",
            [(url, image['sha256'], image['perceptual_hash'], image['width'], image['height'], args.sport, player_id)
             for player_id, _, url, image in promoted])
        cur.executemany("""INSERT INTO sport_player_images (sport_id, player_id, source_url) VALUES (%s,%s,%s)
            ON CONFLICT (sport_id,player_id) DO UPDATE SET source_url=EXCLUDED.source_url""",
            [(args.sport, player_id, url) for player_id, _, url, _ in promoted if args.sport != 'baseball'])
    print(f"Promoted {len(promoted)} TheSportsDB portraits from {len(results)} attempts.")


if __name__ == "__main__":
    main()
