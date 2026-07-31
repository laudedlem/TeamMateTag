"""Remove cached league placeholder silhouettes that are not player photos."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"

# Known placeholders returned with HTTP 200 for otherwise valid player IDs.
PLACEHOLDER_URLS = [
    "https://a.espncdn.com/i/headshots/nfl/players/full/9643.png",
    "https://cdn.nba.com/headshots/nba/latest/1040x760/2430.png",
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    placeholders = {digest(requests.get(url, timeout=30).content) for url in PLACEHOLDER_URLS}
    conn = sqlite3.connect(DATABASE)
    rows = conn.execute("SELECT sport_id, player_id, local_path FROM local_player_images").fetchall()
    removed = []
    for sport, player_id, filename in rows:
        path = Path(filename)
        if path.exists() and digest(path.read_bytes()) in placeholders:
            path.unlink()
            removed.append((sport, player_id))
    conn.executemany("DELETE FROM local_player_images WHERE sport_id = ? AND player_id = ?", removed)
    conn.commit(); conn.close()
    print(f"Removed {len(removed):,} placeholder images.")


if __name__ == "__main__":
    main()
