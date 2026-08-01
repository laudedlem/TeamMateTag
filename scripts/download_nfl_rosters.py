"""Cache nflverse season rosters missing from the historical local archive."""
from __future__ import annotations

from pathlib import Path

import requests

from build_local_sports_dataset import ROOT


ROSTER_DIR = ROOT / "raw" / "nfl" / "rosters"
URL = "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{season}.csv"


def main() -> None:
    ROSTER_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for season in range(2002, 2026):
        path = ROSTER_DIR / f"roster_{season}.csv"
        if path.exists() and path.stat().st_size > 1000:
            continue
        response = requests.get(URL.format(season=season), timeout=120)
        response.raise_for_status()
        path.write_bytes(response.content)
        downloaded += 1
    print(f"Cached {downloaded} nflverse season roster files in {ROSTER_DIR}.")


if __name__ == "__main__":
    main()
