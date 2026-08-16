"""Build a browser-friendly NFL headshot priority review sheet.

Input comes from raw/nfl_headshot_priority_50plus.csv. The output is a static
HTML file with one row per unresolved player, sorted by corrected games played,
plus direct search links and any researched candidate/source URLs.
"""
from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "raw" / "nfl_headshot_priority_50plus.csv"
DEFAULT_RESEARCH = ROOT / "raw" / "nfl_50plus_photo_research_top10.csv"
DEFAULT_OUTPUT = ROOT / "raw" / "nfl_headshot_priority_50plus_review.html"


def load_research(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["name"].strip().lower(): row for row in csv.DictReader(handle)}


def link(url: str, label: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--research", default=str(DEFAULT_RESEARCH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    input_path = Path(args.input)
    research = load_research(Path(args.research))

    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    body_rows: list[str] = []
    for row in rows:
        name_key = row["name"].strip().lower()
        item = research.get(name_key, {})
        college = item.get("college") or row.get("college") or ""
        candidate = link(item.get("candidate", ""), "candidate")
        source = link(item.get("source_page", ""), "source")
        links = " | ".join(
            value
            for value in [
                candidate,
                source,
                link(row.get("google_image_search", ""), "images"),
                link(row.get("college_image_search", ""), "college images"),
                link(row.get("google_search", ""), "web"),
            ]
            if value
        )
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(row['rank'])}</td>"
            f"<td><strong>{html.escape(row['name'])}</strong><br><span>{html.escape(row['player_id'])}</span></td>"
            f"<td>{html.escape(row['career_years'])}</td>"
            f"<td>{html.escape(row['position'])}</td>"
            f"<td>{html.escape(row['games'])}</td>"
            f"<td>{html.escape(college)}</td>"
            f"<td>{html.escape(row['teams'])}</td>"
            f"<td>{links}</td>"
            f"<td>{html.escape(item.get('notes', ''))}</td>"
            "</tr>"
        )

    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>NFL Headshot Priority Review</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, Segoe UI, Arial, sans-serif; }}
    body {{ margin: 24px; background: #101317; color: #eef2f6; }}
    h1 {{ margin-bottom: 4px; }}
    p {{ color: #b9c2cc; margin-top: 0; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #29313a; padding: 9px 10px; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: #171c22; text-align: left; z-index: 1; }}
    tr:hover {{ background: #171c22; }}
    a {{ color: #7cc7ff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    span {{ color: #8d98a5; font-size: 12px; }}
  </style>
</head>
<body>
  <h1>NFL Headshot Priority Review</h1>
  <p>{len(rows)} unresolved football players with at least 50 corrected games played, sorted by games played.</p>
  <table>
    <thead>
      <tr>
        <th>Rank</th><th>Player</th><th>Years</th><th>Pos</th><th>Games</th>
        <th>College</th><th>Teams</th><th>Links</th><th>Notes</th>
      </tr>
    </thead>
    <tbody>
      {''.join(body_rows)}
    </tbody>
  </table>
</body>
</html>
"""
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Wrote {len(rows):,} rows to {output}")


if __name__ == "__main__":
    main()
