"""Back up the production headshot registry locally as a reviewable CSV."""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "web")
import server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    server.ensure_runtime_schema()
    output = ROOT / "raw" / f"headshot_registry_{date.today().isoformat()}.csv"
    with server.db() as conn:
        rows = conn.execute("""
            SELECT h.sport_id, h.player_id,
                   CASE WHEN h.sport_id='baseball' THEN concat_ws(' ', b.name_first, b.name_last) ELSE s.display_name END,
                   h.status, h.provider, h.source_url, h.fallback_url, h.content_sha256,
                   h.width, h.height, h.checked_at, h.review_note
              FROM player_headshots h
              LEFT JOIN players b ON h.sport_id='baseball' AND b.player_id=h.player_id
              LEFT JOIN sport_players s ON h.sport_id<>'baseball' AND s.sport_id=h.sport_id AND s.player_id=h.player_id
             ORDER BY h.sport_id, h.status, 3
        """).fetchall()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sport", "player_id", "player_name", "status", "provider", "source_url", "fallback_url", "sha256", "width", "height", "checked_at", "review_note"])
        writer.writerows(rows)
    print(f"Wrote {len(rows):,} headshot records to {output}")


if __name__ == "__main__":
    main()
