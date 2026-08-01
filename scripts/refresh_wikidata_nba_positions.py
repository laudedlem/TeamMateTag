"""Fetch exact NBA position labels keyed by NBA.com player ID from Wikidata.

The result is an ignored local cache. Wikidata is CC0, but the source is used
only to supplement the local game-by-game roster dataset, never to infer
teammate links.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "raw" / "nba_kaggle" / "wikidata_nba_positions.json"
QUERY = """SELECT ?nba_id ?positionLabel WHERE {
  ?player wdt:P3647 ?nba_id; wdt:P413 ?position.
  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\". }
}"""


def main() -> None:
    url = "https://query.wikidata.org/sparql?format=json&query=" + quote(QUERY)
    request = Request(url, headers={
        "User-Agent": "TeamMateTag-data-research/0.1 (contact@teammatetag.com)",
    })
    with urlopen(request, timeout=120) as response:
        payload = json.load(response)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload), encoding="utf-8")
    print(f"Wrote {len(payload['results']['bindings']):,} NBA position records to {OUTPUT}")


if __name__ == "__main__":
    main()
