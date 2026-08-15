"""Create a durable local NBA player-to-birth-date map for image providers."""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
from name_normalize import normalize  # noqa: E402

SOURCE = ROOT / "raw" / "nba_kaggle" / "positions_v2" / "NBA_PLAYERS.csv"
OUTPUT = ROOT / "raw" / "nba_headshot_identity_matches.csv"


def parse_year(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def main() -> None:
    sources: dict[str, list[dict]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Birthday"):
                sources[normalize(row["Name"])].append(row)
    with server.db() as conn:
        players = conn.execute("""SELECT player_id, display_name, debut_year, final_year
            FROM sport_players WHERE sport_id='basketball'""").fetchall()
    output = []
    for player_id, name, debut, final in players:
        candidates = sources.get(normalize(name), [])
        if not candidates:
            continue
        # A one-year offset is expected because the local game feed labels the
        # season start while the source labels the calendar debut year.
        ranked = sorted(candidates, key=lambda row: (
            abs((parse_year(row.get("Debut")) or debut or 0) - (debut or 0)) +
            abs((parse_year(row.get("Final")) or final or 0) - (final or 0)),
            row.get("Birthday") or "",
        ))
        best = ranked[0]
        tied = [row for row in ranked if row.get("Birthday") == best.get("Birthday")]
        if len({row.get("Birthday") for row in ranked[:2]}) > 1 and len(candidates) > 1:
            # Multiple same-name careers need manual identity evidence.
            continue
        birth = datetime.strptime(best["Birthday"], "%B %d, %Y").date().isoformat()
        output.append({"player_id": player_id, "display_name": name, "birth_date": birth,
                       "source_debut": best.get("Debut", ""), "source_final": best.get("Final", ""),
                       "source": str(SOURCE.relative_to(ROOT))})
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["player_id", "display_name", "birth_date", "source_debut", "source_final", "source"])
        writer.writeheader(); writer.writerows(output)
    print(f"Wrote {len(output):,} NBA identity matches to {OUTPUT}")


if __name__ == "__main__":
    main()
