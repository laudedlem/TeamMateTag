"""Download the OOTP historical MLB Facepack locally for playtest matching."""
from __future__ import annotations

from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
URL = "https://www.dropbox.com/scl/fi/hxdghi1x6j770m2vg9u3c/COFacepackV18.zip?rlkey=azmmtw5qxw7uffdisgkshpsuc&st=gjsl6b5a&dl=1"
OUTPUT = ROOT / "raw" / "ootp" / "COFacepackV18.zip"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".zip.part")
    with requests.get(URL, stream=True, timeout=120) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        written = 0
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk); written += len(chunk)
                    if written % (25 * 1024 * 1024) < len(chunk):
                        print(f"Downloaded {written / 1024 / 1024:.0f} / {total / 1024 / 1024:.0f} MB", flush=True)
    temporary.replace(OUTPUT)
    print(f"Downloaded {OUTPUT} ({written / 1024 / 1024:.0f} MB)", flush=True)


if __name__ == "__main__":
    main()
