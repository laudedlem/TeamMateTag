"""Apply reviewed image overrides with their source and license notes."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
OVERRIDES = {
    ("football", "nfl:00-0024272"): (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f4/Devin_Hester_Chicago_Bears_Salute_to_Service_Day_%28cropped%29.jpg/330px-Devin_Hester_Chicago_Bears_Salute_to_Service_Day_%28cropped%29.jpg",
        "Wikimedia Commons, public domain",
    ),
    ("football", "nfl:00-0022128"): (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/5/56/Lance_Briggs.JPG/330px-Lance_Briggs.JPG",
        "Wikimedia Commons, CC BY-SA 3.0",
    ),
}


def main() -> None:
    conn = sqlite3.connect(DATABASE)
    for (sport, player_id), (url, license_note) in OVERRIDES.items():
        response = requests.get(url, headers={"User-Agent": "TeamMateTag local data audit/1.0"}, timeout=30)
        response.raise_for_status()
        path = ROOT / "raw" / "player_headshots" / sport / (player_id.replace(":", "_") + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        conn.execute("""UPDATE local_player_images SET source_url = ?, local_path = ?, content_type = ?
                         WHERE sport_id = ? AND player_id = ?""",
                     (url + "# " + license_note, str(path), response.headers.get("content-type"), sport, player_id))
    conn.commit(); conn.close()


if __name__ == "__main__":
    main()
