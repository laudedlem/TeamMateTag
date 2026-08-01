"""Fill NBA exact-position gaps from Basketball-Reference player pages.

Only players absent from the Wikidata NBA.com-ID cache are requested. Results
are cached locally and the script deliberately sleeps between requests.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
WIKIDATA = ROOT / "raw" / "nba_kaggle" / "wikidata_nba_positions.json"
OUTPUT = ROOT / "raw" / "nba_kaggle" / "bref_nba_position_gaps.json"
HEADERS = {"User-Agent": "TeamMateTag data research contact@teammatetag.com"}
POSITION_MAP = {
    "Point Guard": "PG", "Shooting Guard": "SG", "Small Forward": "SF",
    "Power Forward": "PF", "Center": "C",
}


def key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in value.lower() if ch.isalnum())


def wikidata_ids() -> set[str]:
    payload = json.loads(WIKIDATA.read_text(encoding="utf-8"))
    return {f"nba:{row['nba_id']['value']}" for row in payload["results"]["bindings"]
            if row.get("nba_id", {}).get("value")}


def player_pages() -> dict[str, list[tuple[int, int, str]]]:
    result: dict[str, list[tuple[int, int, str]]] = {}
    for letter in "abcdefghijklmnopqrstuvwxyz":
        response = requests.get(f"https://www.basketball-reference.com/players/{letter}/",
                                headers=HEADERS, timeout=45)
        response.raise_for_status()
        for url, name, debut, final in re.findall(
                r'<th[^>]*data-stat="player"[^>]*>\s*<a href="([^"]+)">([^<]+)</a>.*?'
                r'<td[^>]*data-stat="year_min"[^>]*>(\d+)</td>.*?'
                r'<td[^>]*data-stat="year_max"[^>]*>(\d+)</td>', response.text, re.S):
            result.setdefault(key(name), []).append((int(debut), int(final), url))
        time.sleep(4)
    return result


def main() -> None:
    known = wikidata_ids()
    conn = sqlite3.connect(DATABASE)
    missing = conn.execute("""SELECT player_id, display_name, debut_year, final_year
                              FROM sport_players WHERE sport_id='basketball'""").fetchall()
    conn.close()
    missing = [row for row in missing if row[0] not in known]
    cache = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    pages = player_pages()
    for index, (player_id, name, debut, final) in enumerate(missing, 1):
        if cache.get(player_id, {}).get("status") == "ok":
            continue
        candidates = pages.get(key(name), [])
        if not candidates:
            cache[player_id] = {"name": name, "positions": [], "status": "no_name_match"}
            continue
        chosen = min(candidates, key=lambda row: abs(row[0] - (debut or row[0])) + abs(row[1] - (final or row[1])))
        response = requests.get("https://www.basketball-reference.com" + chosen[2], headers=HEADERS, timeout=45)
        if response.status_code == 429:
            time.sleep(90)
            response = requests.get("https://www.basketball-reference.com" + chosen[2], headers=HEADERS, timeout=45)
        if response.status_code != 200:
            cache[player_id] = {"name": name, "positions": [], "status": f"http_{response.status_code}"}
            continue
        match = re.search(r"Position:\s*</strong>\s*(.*?)\s*(?:\s*\u25aa|</p>)", response.text, re.S)
        text = re.sub(r"<[^>]+>", "", match.group(1)).strip() if match else ""
        positions = [code for label, code in POSITION_MAP.items() if label in text]
        cache[player_id] = {"name": name, "positions": positions, "source_url": chosen[2], "status": "ok"}
        if index % 25 == 0:
            OUTPUT.write_text(json.dumps(cache), encoding="utf-8")
            print(f"Processed {index}/{len(missing)} missing players")
        time.sleep(4)
    OUTPUT.write_text(json.dumps(cache), encoding="utf-8")
    resolved = sum(1 for item in cache.values() if item.get("positions"))
    print(f"Cached {resolved}/{len(missing)} resolved Basketball-Reference position gaps")


if __name__ == "__main__":
    main()
