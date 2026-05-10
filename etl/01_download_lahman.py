#!/usr/bin/env python3
"""
01_download_lahman.py — fetch the Lahman dataset.

Run this FIRST. As of late 2025, fully automated download is no longer
reliable, so the recommended path is a one-time manual download per
release:

  1. Visit https://sabr.org/lahman-database/
  2. Click the "Comma-delimited version" link → Box.com folder.
  3. Download the zip; place it (or its three needed CSVs:
     People.csv, Teams.csv, Appearances.csv) into ./raw/.
  4. Run etl/02_load_lahman.py.

Why automation is hard now:
  - chadwickbureau/baseballdatabank GitHub repo was retired (404).
  - SABR's official release is on a Box.com shared folder with no
    stable, scriptable direct-download URL.
  - The community cbwinslow fork still exists but is FROZEN at 2021;
    using it silently yields a 4+ year stale dataset.

If you run this script without --url, it falls back to the cbwinslow
mirror and prints a big stale-data warning. Useful for testing the
pipeline; do NOT use it for production data.

For mid-season updates, use 05_update_current_season.py — it hits
statsapi.mlb.com directly and is the right tool for "the trade deadline
just happened, refresh the rosters."
"""
import argparse
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import requests

# Auto-download sources. See module docstring — there is no reliable
# scriptable source for the *current* Lahman release. The entry below is
# explicitly stale and emits a warning when used.
STALE_CBWINSLOW = "https://github.com/cbwinslow/baseballdatabank/archive/refs/heads/master.zip"
SOURCES = [STALE_CBWINSLOW]

# Files we actually use. The full Lahman set has 25+ tables; we only need three.
NEEDED = {"People.csv", "Teams.csv", "Appearances.csv"}


def download(url: str, out_dir: Path) -> bool:
    print(f"  fetching {url}")
    try:
        r = requests.get(url, timeout=120, stream=True)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  failed: {e}")
        return False

    print(f"  unzipping ({len(r.content) / 1e6:.1f} MB)")
    with zipfile.ZipFile(BytesIO(r.content)) as zf:
        # Lahman zips contain a 'core/' dir with the CSVs we want.
        for member in zf.namelist():
            name = Path(member).name
            if name in NEEDED:
                target = out_dir / name
                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                print(f"  wrote {target}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="raw", help="output directory")
    ap.add_argument("--url", help="override source URL (e.g., a SABR direct link)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = [args.url] if args.url else SOURCES
    for url in sources:
        if url == STALE_CBWINSLOW:
            print("=" * 70)
            print("WARNING: falling back to STALE cbwinslow mirror (frozen at 2021).")
            print("For current data, do the manual SABR procedure — see module docstring.")
            print("=" * 70)
        if download(url, out_dir):
            missing = NEEDED - {p.name for p in out_dir.iterdir()}
            if not missing:
                print(f"\nSUCCESS — all {len(NEEDED)} files in {out_dir}/")
                return 0
            print(f"  missing after extract: {missing}")

    print("\nFAILED — could not download Lahman from any source.", file=sys.stderr)
    print("Manual fallback: visit https://sabr.org/lahman-database, download the", file=sys.stderr)
    print(f"CSV release, and place People.csv, Teams.csv, Appearances.csv in {out_dir}/", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
