"""Supplement NFL headshot priority CSV colleges from Wikipedia pages.

This is conservative: it only fills blank college cells when a likely Wikipedia
page has an American-football infobox and a College field.
"""
from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "raw" / "nfl_headshot_priority_list.csv"
USER_AGENT = "TeamMateTag college supplement/0.2.15"


def wiki_search(session: requests.Session, name: str) -> str | None:
    response = session.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": f'intitle:"{name}" American football',
            "format": "json",
            "srlimit": 5,
        },
        timeout=12,
    )
    response.raise_for_status()
    results = response.json().get("query", {}).get("search", [])
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    for result in results:
        title = result.get("title", "")
        compact = re.sub(r"[^a-z0-9]", "", title.lower())
        if normalized in compact and "football" in result.get("snippet", "").lower() + title.lower():
            return title
    return None


def extract_college(session: requests.Session, title: str) -> str | None:
    response = session.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "parse", "page": title, "prop": "wikitext", "format": "json"},
        timeout=12,
    )
    response.raise_for_status()
    text = response.json().get("parse", {}).get("wikitext", {}).get("*", "")
    if "Infobox NFL biography" not in text and "Infobox gridiron football person" not in text:
        return None
    match = re.search(r"(?im)^\s*\|\s*College\s*=\s*(.+?)\s*$", text)
    if not match:
        match = re.search(r"(?im)^\s*\|\s*college\s*=\s*(.+?)\s*$", text)
    if not match:
        return None
    value = match.group(1)
    value = re.sub(r"<.*?>", "", value)
    value = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\{\{.*?\}\}", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()

    path = Path(args.csv)
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    filled = 0
    checked = 0
    for row in rows:
        if row.get("college"):
            continue
        checked += 1
        if args.limit and checked > args.limit:
            break
        try:
            title = wiki_search(session, row["name"])
            if title:
                college = extract_college(session, title)
                if college:
                    row["college"] = college
                    row["college_team_search"] = "https://www.google.com/search?q=" + quote(f'{row["name"]} {college} football photo')
                    row["college_image_search"] = "https://www.google.com/search?tbm=isch&q=" + quote(f'{row["name"]} {college} football photo')
                    filled += 1
        except Exception as exc:
            row["attempted_sources"] = (row.get("attempted_sources") or "") + f"; wikipedia_college_error:{str(exc)[:80]}"
        if checked % 25 == 0:
            print(f"checked {checked}, filled {filled}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Filled {filled} college values in {path}")


if __name__ == "__main__":
    main()
