"""Resolve NFL headshots from FootballDB profile pages.

FootballDB is reachable from the local environment even when PFR blocks
scripted access. The resolver indexes paginated last-name pages, matches
remaining football headshot gaps by exact normalized name, uses team-name
overlap to break duplicate-name ties, validates image bytes against known
placeholder fingerprints, and promotes accepted URLs into the live display
table.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

BASE = "https://www.footballdb.com"
INDEX = ROOT / "raw" / "footballdb_nfl_player_index.csv"
REPORT = ROOT / "raw" / "nfl_footballdb_headshots.csv"
USER_AGENT = "Mozilla/5.0 TeamMateTag FootballDB headshot resolver/0.2.14"


def compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(value))


def ascii_letters(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z]", "", value.lower())


def slug_base(name: str) -> str:
    parts = [part for part in re.split(r"\s+", normalize(name)) if part]
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}
    while parts and ascii_letters(parts[-1]) in suffixes:
        parts.pop()
    if len(parts) < 2:
        return ""
    first = ascii_letters(parts[0])
    last = ascii_letters(parts[-1])
    if not first or not last:
        return ""
    return f"{last[:5]}{first[:2]}"


def display_from_index(text: str) -> str:
    text = unescape(re.sub(r"<.*?>", "", text)).strip()
    if "," in text:
        last, first = [piece.strip() for piece in text.split(",", 1)]
        return f"{first} {last}".strip()
    return text


def get(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=25)
    response.raise_for_status()
    return response.text


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def index_pages(session: requests.Session, delay: float) -> list[dict]:
    cached: list[dict] = []
    if INDEX.exists():
        with INDEX.open(encoding="utf-8", newline="") as handle:
            cached = list(csv.DictReader(handle))
        if cached:
            return cached

    rows: list[dict] = []
    seen: set[str] = set()
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        page = 1
        while True:
            url = f"{BASE}/players/index.html?letter={letter}&page={page}"
            html = get(session, url)
            links = re.findall(r'href="(/players/[^"#?]+)"[^>]*>([^<]+)</a>', html, flags=re.I)
            page_rows = []
            for href, label in links:
                if href == "/players/index.html":
                    continue
                if not unescape(label).strip().upper().startswith(letter):
                    continue
                name = display_from_index(label)
                if not name or " " not in name:
                    continue
                # Sidebar/fantasy links leak into every index page. Their hrefs
                # usually repeat across unrelated letters, so de-dupe globally.
                if href in seen:
                    continue
                seen.add(href)
                page_rows.append({"name": name, "norm_name": compact(name), "profile_url": BASE + href})
            rows.extend(page_rows)
            if not page_rows:
                break

            page_links = {
                int(match)
                for match in re.findall(
                    rf'/players/index\.html\?letter={letter}&amp;page=(\d+)', html, flags=re.I
                )
            }
            if not page_links or page >= max(page_links):
                break
            page += 1
            if delay:
                time.sleep(delay)
        print(f"indexed {letter}: {len([r for r in rows if compact(r['name']).startswith(compact(letter))])} cumulative={len(rows)}", flush=True)

    INDEX.parent.mkdir(parents=True, exist_ok=True)
    with INDEX.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "norm_name", "profile_url"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


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
                  AND h.status IN ('placeholder','missing','wrong_player','bad_crop')
                GROUP BY p.player_id,p.display_name,p.debut_year,p.final_year,p.primary_pos,h.status
                ORDER BY career_games DESC, p.final_year DESC NULLS LAST, p.display_name,p.player_id"""
        ).fetchall()
    return [
        {
            "player_id": player_id,
            "name": name,
            "norm_name": compact(name),
            "debut": debut,
            "final": final,
            "position": pos or "",
            "status": status,
            "career_games": int(career_games or 0),
            "teams": teams or [],
        }
        for player_id, name, debut, final, pos, status, career_games, teams in rows
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
            """SELECT content_sha256, perceptual_hash
                 FROM player_headshots
                WHERE sport_id='football' AND status='placeholder'"""
        ).fetchall():
            if digest:
                hashes.add(digest)
            if phash:
                perceptual.add(phash)
    return hashes, perceptual


def profile_candidate(session: requests.Session, profile_url: str) -> dict:
    html = get(session, profile_url)
    image_match = re.search(r'<img src="(https://cdn\.footballdb\.com/headshots/[^"]+)" alt="([^"]+)"', html)
    description_match = re.search(r'<meta name="description" content="([^"]+)"', html, flags=re.I)
    title_match = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
    pro_years: set[int] = set()
    pro_teams: set[str] = set()
    for tr in re.findall(r'(<tr[^>]*class="[^"]*row_pro[^"]*"[^>]*>.*?</tr>)', html, flags=re.S | re.I):
        if "TOT ALS" in tr or "row_total" in tr:
            continue
        year_match = re.search(r"row_[A-Z]+_reg_(\d{4})_NFL", tr)
        if year_match:
            pro_years.add(int(year_match.group(1)))
        for title in re.findall(r'title="([^"]+) \(NFL\)"', tr):
            if title and not title.startswith(("TOT", "2 Teams", "3 Teams", "4 Teams")):
                pro_teams.add(unescape(title))
    return {
        "profile_url": profile_url,
        "source_url": unescape(image_match.group(1)) if image_match else "",
        "image_alt": unescape(image_match.group(2)) if image_match else "",
        "description": unescape(description_match.group(1)) if description_match else "",
        "title": unescape(re.sub(r"\s+", " ", title_match.group(1))).strip() if title_match else "",
        "pro_first": min(pro_years) if pro_years else None,
        "pro_final": max(pro_years) if pro_years else None,
        "pro_teams": sorted(pro_teams),
    }


def score_profile(row: dict, profile: dict) -> int:
    score = 0
    if compact(profile.get("image_alt", "")) == row["norm_name"]:
        score += 30
    if row["norm_name"] in compact(profile.get("title", "")):
        score += 10
    text = normalize(" ".join([profile.get("description", ""), profile.get("title", "")]))
    for team in row["teams"]:
        team_norm = normalize(team)
        nickname = normalize(team.split()[-1])
        if team_norm and team_norm in text:
            score += 8
        elif len(nickname) > 3 and nickname in text:
            score += 3
    if str(row.get("position") or "").split("/")[0].lower() in text:
        score += 2
    return score


def team_overlap_count(row: dict, profile: dict) -> int:
    text = normalize(" ".join([profile.get("description", ""), profile.get("title", ""), " ".join(profile.get("pro_teams") or [])]))
    count = 0
    for team in row["teams"]:
        team_norm = normalize(team)
        nickname = normalize(team.split()[-1])
        if team_norm and team_norm in text:
            count += 1
        elif len(nickname) > 3 and nickname in text:
            count += 1
    return count


def career_year_match(row: dict, profile: dict) -> bool:
    first, final = profile.get("pro_first"), profile.get("pro_final")
    if not first or not final:
        return False
    debut, row_final = row.get("debut"), row.get("final")
    if not debut or not row_final:
        return False
    # Rows are already 2000-present playable rows. Older players may have true
    # FootballDB careers starting before 2000, so compare against the playable
    # window by overlap and require the same final year when possible.
    if final < debut or first > row_final:
        return False
    if row_final != final and abs(int(row_final) - int(final)) > 1:
        return False
    return True


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


def resolve(row: dict, matches: list[dict], session: requests.Session, hashes: set[str], perceptual: set[str], delay: float) -> dict:
    if not matches:
        return {**row, "result_status": "no_footballdb_profile", "source_url": "", "profile_url": "", "note": ""}
    scored = []
    for match in matches:
        try:
            profile = profile_candidate(session, match["profile_url"])
        except Exception as exc:
            scored.append((0, {**match, "source_url": "", "note": f"profile fetch failed: {exc}"}))
            continue
        scored.append((score_profile(row, profile), profile))
        if delay:
            time.sleep(delay)
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    overlap = team_overlap_count(row, best)
    year_match = career_year_match(row, best)
    if not year_match:
        return {**row, "result_status": "needs_review", "source_url": best.get("source_url", ""), "profile_url": best.get("profile_url", ""), "note": f"FootballDB career years do not match; profile {best.get('pro_first')}-{best.get('pro_final')}; score {best_score}"}
    if overlap == 0:
        return {**row, "result_status": "needs_review", "source_url": best.get("source_url", ""), "profile_url": best.get("profile_url", ""), "note": f"FootballDB profile lacks team evidence; profile {best.get('pro_first')}-{best.get('pro_final')}; score {best_score}"}
    if best_score < 38:
        return {**row, "result_status": "needs_review", "source_url": best.get("source_url", ""), "profile_url": best.get("profile_url", ""), "note": f"low FootballDB match score {best_score}"}
    if not best.get("source_url"):
        return {**row, "result_status": "no_headshot_on_profile", "source_url": "", "profile_url": best.get("profile_url", ""), "note": f"score {best_score}"}
    image = fetch(best["source_url"])
    reason = reject_reason(image, hashes, perceptual)
    if reason:
        return {**row, "result_status": "rejected", "source_url": best["source_url"], "profile_url": best["profile_url"], "note": reason}
    return {
        **row,
        **image,
        "result_status": "verified",
        "source_url": best["source_url"],
        "profile_url": best["profile_url"],
        "note": f"FootballDB exact-name profile match; score {best_score}",
    }


def persist(rows: list[dict]) -> int:
    promoted = [row for row in rows if row.get("result_status") == "verified"]
    if not promoted:
        return 0
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
               VALUES ('football',%s,'FootballDB','verified',%s)
               ON CONFLICT (sport_id,player_id,provider)
               DO UPDATE SET status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",
            [(row["player_id"], row["profile_url"]) for row in promoted],
        )
        cur.executemany(
            """UPDATE player_headshots SET source_url=%s,fallback_url=NULL,provider='FootballDB',
                  status='verified',content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
                  reviewed_at=now(),review_note=%s
               WHERE sport_id='football' AND player_id=%s""",
            [
                (
                    row["source_url"],
                    row["sha256"],
                    row["perceptual_hash"],
                    row["width"],
                    row["height"],
                    f"{row['note']} Profile: {row['profile_url']}",
                    row["player_id"],
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
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--flush-every", type=int, default=25)
    parser.add_argument("--slug-sequences", type=int, default=20)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.reindex and INDEX.exists():
        INDEX.unlink()

    session = new_session()
    indexed = index_pages(session, args.delay)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in indexed:
        by_name[row["norm_name"]].append(row)

    all_rows = unresolved_players()
    rows = []
    for row in all_rows:
        matches = list(by_name.get(row["norm_name"], []))
        base = slug_base(row["name"])
        if base:
            existing = {match["profile_url"] for match in matches}
            for number in range(1, args.slug_sequences + 1):
                url = f"{BASE}/players/{normalize(row['name']).replace(' ', '-')}-{base}{number:02d}"
                if url not in existing:
                    matches.append({"name": row["name"], "norm_name": row["norm_name"], "profile_url": url})
        if matches:
            row["_matches"] = matches
            rows.append(row)
    if args.limit:
        rows = rows[: args.limit]
    hashes, perceptual = placeholder_fingerprints()
    results: list[dict] = []
    batch: list[dict] = []
    promoted_total = 0
    if args.workers > 1:
        def run_row(row: dict) -> dict:
            return resolve(row, row["_matches"], new_session(), hashes, perceptual, args.delay)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run_row, row): row for row in rows}
            for index, future in enumerate(as_completed(futures), 1):
                result = future.result()
                results.append(result)
                batch.append(result)
                if not args.dry_run and args.flush_every > 0 and len(batch) >= args.flush_every:
                    promoted_total += persist(batch)
                    batch.clear()
                if index % 10 == 0 or index == len(rows):
                    print(f"checked {index}/{len(rows)} promoted={promoted_total}", flush=True)
    else:
        for index, row in enumerate(rows, 1):
            result = resolve(row, row["_matches"], session, hashes, perceptual, args.delay)
            results.append(result)
            batch.append(result)
            if not args.dry_run and args.flush_every > 0 and len(batch) >= args.flush_every:
                promoted_total += persist(batch)
                batch.clear()
            if index % 10 == 0 or index == len(rows):
                print(f"checked {index}/{len(rows)} promoted={promoted_total}", flush=True)
    if not args.dry_run and batch:
        promoted_total += persist(batch)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["player_id", "name", "debut", "final", "position", "career_games", "result_status", "source_url", "profile_url", "note", "width", "height"]
    with REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field, "") for field in fields})
    counts = Counter(row["result_status"] for row in results)
    print(f"FootballDB index rows: {len(indexed)}")
    print(f"Matched unresolved rows: {len(rows)}")
    print(f"Promoted: {promoted_total if not args.dry_run else counts.get('verified', 0)}")
    print(f"Results: {dict(counts)}")
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()
