"""Collect NFL headshot candidates from source pages for manual review.

This is for the stubborn football gaps where ESPN/FootballDB did not provide a
usable portrait. It targets the priority CSV, searches likely college/team
source pages, extracts page images, rejects known/obvious placeholders, and
writes a browser review sheet. It does not modify the live game.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, dhash, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

PRIORITY = ROOT / "raw" / "nfl_headshot_priority_50plus.csv"
RESEARCH = ROOT / "raw" / "nfl_50plus_photo_research_top10.csv"
OUT = ROOT / "raw" / "nfl_priority_source_review"
PENDING = OUT / "pending"
META = OUT / "candidates.csv"
HTML = OUT / "review.html"
STATE = OUT / "state.json"
USER_AGENT = "Mozilla/5.0 TeamMateTag NFL source candidate collector/0.2.15"

OFFICIAL_DOMAIN_HINTS = (
    "athletics.com",
    "sports.",
    "gopack.com",
    "ukathletics.com",
    "byucougars.com",
    "uwbadgers.com",
    "floridagators.com",
    "csurams.com",
    "autigers.com",
)
BAD_IMAGE_HINTS = (
    "placeholder",
    "default",
    "blank",
    "silhouette",
    "missing",
    "no-photo",
    "nophoto",
    "logo",
    "sprite",
    "icon",
    "favicon",
    "loading",
    "transparent",
)


class ImageExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.title = ""
        self.images: list[dict[str, str]] = []
        self._in_title = False
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs_raw: list[tuple[str, str | None]]) -> None:
        attrs = {key.lower(): value or "" for key, value in attrs_raw}
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            prop = (attrs.get("property") or attrs.get("name") or "").lower()
            if prop in {"og:image", "twitter:image", "twitter:image:src"}:
                self._add(attrs.get("content", ""), prop, "")
        elif tag == "img":
            src = attrs.get("src") or attrs.get("data-src") or attrs.get("data-original") or attrs.get("data-lazy-src") or ""
            alt = attrs.get("alt") or attrs.get("title") or ""
            self._add(src, "img", alt)
            srcset = attrs.get("srcset") or attrs.get("data-srcset") or ""
            for item in srcset.split(","):
                candidate = item.strip().split(" ")[0]
                self._add(candidate, "srcset", alt)

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if not stripped:
            return
        if self._in_title:
            self.title += stripped
        if len(" ".join(self._text_parts)) < 120_000:
            self._text_parts.append(stripped)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    @property
    def page_text(self) -> str:
        return " ".join([self.title, *self._text_parts])

    def _add(self, url: str, kind: str, alt: str) -> None:
        if not url:
            return
        absolute = urljoin(self.base_url, html.unescape(url))
        if absolute.startswith(("http://", "https://")):
            self.images.append({"url": absolute, "kind": kind, "alt": alt})


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def clean_url(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    return url


def load_rows(path: Path, limit: int, offset: int) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows = rows[offset:]
    return rows[:limit] if limit else rows


def load_research(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {normalize(row["name"]): row for row in csv.DictReader(handle)}


def placeholder_fingerprints() -> tuple[set[str], set[str]]:
    hashes: set[str] = set()
    perceptual: set[str] = set()
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


def candidate_queries(row: dict[str, str], research: dict[str, dict[str, str]], max_queries: int) -> list[str]:
    name = row["name"]
    teams = [team.strip() for team in (row.get("teams") or "").split(",") if team.strip()]
    college = (research.get(normalize(name), {}).get("college") or row.get("college") or "").strip()
    queries = []
    if college:
        queries.extend(
            [
                f'"{name}" "{college}" football photo',
                f'"{name}" "{college}" football roster',
                f'"{name}" "{college}" athletics football',
            ]
        )
    if teams:
        queries.extend(
            [
                f'"{name}" "{teams[0]}" football photo',
                f'"{name}" "{teams[0]}" NFL headshot',
            ]
        )
    queries.extend([f'"{name}" NFL football player photo', f'"{name}" football roster photo'])
    deduped = []
    seen = set()
    for query in queries:
        if query not in seen:
            seen.add(query)
            deduped.append(query)
    return deduped[:max_queries]


def ddg_pages(session: requests.Session, query: str, max_pages: int) -> list[str]:
    response = session.get("https://duckduckgo.com/html/", params={"q": query}, timeout=12)
    response.raise_for_status()
    urls = []
    for match in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', response.text):
        url = clean_url(html.unescape(match.group(1)))
        if url.startswith(("http://", "https://")):
            urls.append(url)
    if not urls:
        for match in re.finditer(r'href="(https?://[^"]+)"', response.text):
            urls.append(clean_url(html.unescape(match.group(1))))
    deduped = []
    seen = set()
    for url in urls:
        host = urlparse(url).netloc.lower()
        if not host or "duckduckgo.com" in host or url in seen:
            continue
        seen.add(url)
        deduped.append(url)
        if len(deduped) >= max_pages:
            break
    return deduped


def fetch_page_images(session: requests.Session, page_url: str) -> tuple[str, str, list[dict[str, str]]]:
    response = session.get(page_url, timeout=12)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("image/"):
        return response.url, "", [{"url": response.url, "kind": "direct", "alt": ""}]
    parser = ImageExtractor(response.url)
    parser.feed(response.text[:1_500_000])
    return response.url, parser.page_text, parser.images


def image_score(row: dict[str, str], page_url: str, page_text: str, image: dict[str, str], research: dict[str, dict[str, str]]) -> int:
    name_parts = [part for part in normalize(row["name"]).split() if len(part) > 1]
    college = normalize(research.get(normalize(row["name"]), {}).get("college") or row.get("college") or "")
    teams = [normalize(team) for team in (row.get("teams") or "").split(",") if team.strip()]
    page_identity = normalize(" ".join([page_url, page_text[:8000]]))
    image_identity = normalize(" ".join([image.get("url", ""), image.get("alt", "")]))
    text = normalize(" ".join([page_url, page_text[:8000], image.get("url", ""), image.get("alt", ""), image.get("kind", "")]))
    page_has_name = bool(name_parts) and all(part in page_identity for part in name_parts)
    image_has_name = bool(name_parts) and all(part in image_identity for part in name_parts)
    if not page_has_name and not image_has_name:
        return -100
    value = 0
    if image_has_name:
        value += 20
    if page_has_name:
        value += 12
    if college and college in text:
        value += 8
    if any(team in text or team.split()[-1] in text for team in teams):
        value += 6
    if any(hint in urlparse(page_url).netloc.lower() for hint in OFFICIAL_DOMAIN_HINTS):
        value += 6
    if any(word in text for word in ("headshot", "portrait", "roster", "mug")):
        value += 5
    if image.get("kind") in {"og:image", "twitter:image", "twitter:image:src", "direct"}:
        value += 9
    if image.get("kind") in {"img", "srcset"} and not image_has_name:
        value -= 12
    if any(word in text for word in ("getty", "espn", "nfl.com", "footballdb")):
        value += 2
    if any(bad in text for bad in BAD_IMAGE_HINTS):
        value -= 40
    return value


def inspect_image(session: requests.Session, url: str, hashes: set[str], phashes: set[str]) -> tuple[bool, bytes | None, str]:
    text = normalize(url)
    if any(bad in text for bad in BAD_IMAGE_HINTS):
        return False, None, "bad image-url hint"
    response = session.get(url, timeout=12)
    response.raise_for_status()
    image = Image.open(io.BytesIO(response.content))
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    if width < 120 or height < 120:
        return False, None, f"too small {width}x{height}"
    ratio = width / max(height, 1)
    if ratio > 3.2 or ratio < 0.25:
        return False, None, f"bad aspect {width}x{height}"
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    phash, _, _ = dhash(data)
    if digest in hashes:
        return False, None, "known placeholder hash"
    if any(hamming(phash, known) <= 4 for known in phashes):
        return False, None, "known placeholder perceptual match"
    return True, data, ""


def collect_row(
    row: dict[str, str],
    research: dict[str, dict[str, str]],
    hashes: set[str],
    phashes: set[str],
    max_queries: int,
    max_pages: int,
    max_candidates: int,
) -> list[dict[str, str]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    candidates: list[dict[str, str]] = []
    manual = research.get(normalize(row["name"]), {})
    manual_urls = [manual.get("candidate", ""), manual.get("source_page", "")]
    page_urls: list[str] = []
    for url in manual_urls:
        if url and url.startswith(("http://", "https://")):
            page_urls.append(url)
    for query in candidate_queries(row, research, max_queries):
        try:
            page_urls.extend(ddg_pages(session, query, max_pages=max_pages))
        except Exception:
            continue
        time.sleep(0.08)
    seen_pages = set()
    image_items = []
    for page_url in page_urls:
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        try:
            final_page, page_text, images = fetch_page_images(session, page_url)
        except Exception:
            continue
        for image in images:
            score_value = image_score(row, final_page, page_text, image, research)
            if score_value < 0:
                continue
            image_items.append(
                {
                    "page_url": final_page,
                    "image_url": image["url"],
                    "alt": image.get("alt", ""),
                    "kind": image.get("kind", ""),
                    "score": str(score_value),
                }
            )
    image_items.sort(key=lambda item: int(item["score"]), reverse=True)
    seen_images = set()
    for item in image_items:
        if len(candidates) >= max_candidates:
            break
        if item["image_url"] in seen_images or int(item["score"]) < 0:
            continue
        seen_images.add(item["image_url"])
        try:
            ok, data, reason = inspect_image(session, item["image_url"], hashes, phashes)
        except Exception as exc:
            reason = str(exc)[:120]
            ok, data = False, None
        if not ok or data is None:
            continue
        filename = f"{safe(row['player_id'])}__{len(candidates) + 1}.jpg"
        path = PENDING / filename
        path.write_bytes(data)
        candidates.append(
            {
                "player_id": row["player_id"],
                "name": row["name"],
                "career_years": row["career_years"],
                "position": row["position"],
                "games": row["games"],
                "college": manual.get("college") or row.get("college", ""),
                "teams": row.get("teams", ""),
                "candidate_path": str(path),
                "source_url": item["image_url"],
                "source_page": item["page_url"],
                "score": item["score"],
                "alt": item["alt"],
                "note": reason,
            }
        )
    return candidates


def write_review(candidates: list[dict[str, str]], rows: list[dict[str, str]]) -> None:
    fields = [
        "player_id",
        "name",
        "career_years",
        "position",
        "games",
        "college",
        "teams",
        "candidate_path",
        "source_url",
        "source_page",
        "score",
        "alt",
        "note",
    ]
    with META.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(candidates)

    by_player: dict[str, list[dict[str, str]]] = {}
    for item in candidates:
        by_player.setdefault(item["player_id"], []).append(item)
    cards = []
    for row in rows:
        items = by_player.get(row["player_id"], [])
        if not items:
            continue
        photos = []
        for item in items:
            rel = Path(item["candidate_path"]).relative_to(OUT).as_posix()
            photos.append(
                f"""<figure>
                  <img src="{html.escape(rel)}">
                  <figcaption>score {html.escape(item['score'])}<br>
                    <a href="{html.escape(item['source_page'])}" target="_blank">page</a>
                    <a href="{html.escape(item['source_url'])}" target="_blank">image</a>
                  </figcaption>
                </figure>"""
            )
        cards.append(
            f"""<section>
              <h2>{html.escape(row['rank'])}. {html.escape(row['name'])}</h2>
              <p>{html.escape(row['career_years'])} | {html.escape(row['position'])} |
                 {html.escape(row['games'])} games | {html.escape(items[0].get('college') or row.get('college') or 'college unknown')}</p>
              <p>{html.escape(row.get('teams', ''))}</p>
              <div>{''.join(photos)}</div>
            </section>"""
        )
    HTML.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>NFL Source Candidate Review</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, Segoe UI, Arial, sans-serif; }}
    body {{ margin: 24px; background: #101317; color: #eef2f6; }}
    h1 {{ margin-bottom: 4px; }}
    p {{ color: #b9c2cc; }}
    section {{ border-top: 1px solid #2c3540; padding: 18px 0; }}
    h2 {{ font-size: 18px; margin: 0 0 4px; }}
    div {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; }}
    figure {{ margin: 0; background: #171c22; padding: 10px; border-radius: 8px; }}
    img {{ width: 100%; height: 220px; object-fit: cover; background: #27303a; }}
    figcaption {{ color: #b9c2cc; font-size: 12px; }}
    a {{ color: #7cc7ff; margin-right: 8px; }}
  </style>
</head>
<body>
  <h1>NFL Source Candidate Review</h1>
  <p>{len(candidates)} candidates for {len(by_player)} of {len(rows)} reviewed players. Delete bad images from pending before importing.</p>
  {''.join(cards)}
</body>
</html>
""",
        encoding="utf-8",
    )
    STATE.write_text(json.dumps({"players": len(rows), "candidates": len(candidates), "with_candidates": len(by_player)}, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=234)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-queries", type=int, default=4)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    if args.clear and OUT.exists():
        shutil.rmtree(OUT)
    PENDING.mkdir(parents=True, exist_ok=True)
    research = load_research(RESEARCH)
    rows = load_rows(PRIORITY, limit=args.limit, offset=args.offset)
    hashes, phashes = placeholder_fingerprints()
    results: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                collect_row,
                row,
                research,
                hashes,
                phashes,
                args.max_queries,
                args.max_pages,
                args.max_candidates,
            ): row
            for row in rows
        }
        for index, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            found = future.result()
            results.extend(found)
            players_with = len({item["player_id"] for item in results})
            print(
                f"checked {index}/{len(rows)} found_this={len(found)} players_with_candidates={players_with} total_candidates={len(results)} :: {row['name']}",
                flush=True,
            )
    order = {row["player_id"]: index for index, row in enumerate(rows)}
    results.sort(key=lambda item: (order.get(item["player_id"], 999999), -int(item["score"])))
    write_review(results, rows)
    print(f"Review page: {HTML}")
    print(f"Pending images: {PENDING}")


if __name__ == "__main__":
    main()
