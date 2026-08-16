"""Import user-approved NFL headshot candidates.

Delete bad images from ``raw/nfl_headshot_review/pending`` first. This script
crops/uploads the remaining files to Supabase Storage and updates the live game
tables.
"""
from __future__ import annotations

import csv
import hashlib
import io
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

import server  # noqa: E402
from audit_runtime_headshots import dhash  # noqa: E402
from crop_recent_headshots import TARGET_SIZE, choose_crop_box, upload_image  # noqa: E402
from publish_ootp_mlb_headshots import ensure_public_bucket, storage_config  # noqa: E402

OUT = ROOT / "raw" / "nfl_headshot_review"
PENDING = OUT / "pending"
META = OUT / "candidates.csv"


def crop_bytes(path: Path) -> tuple[bytes, str, int, int]:
    image = Image.open(path)
    image = ImageOps.exif_transpose(image).convert("RGB")
    box = choose_crop_box(image)
    cropped = image.crop(box).resize(TARGET_SIZE, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    cropped.save(buffer, format="JPEG", quality=88, optimize=True, progressive=True)
    data = buffer.getvalue()
    phash, width, height = dhash(data)
    return data, phash, width, height


def main() -> None:
    rows = {}
    with META.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows[row["player_id"]] = row
    base_url, key = storage_config()
    ensure_public_bucket(base_url, key)
    updates = []
    for path in sorted(PENDING.glob("*.jpg")):
        player_id = path.stem.replace("_", ":")
        row = rows.get(player_id)
        if not row:
            print(f"skip unknown file {path.name}")
            continue
        data, phash, width, height = crop_bytes(path)
        cropped_path = OUT / "approved_crops" / path.name
        cropped_path.parent.mkdir(parents=True, exist_ok=True)
        cropped_path.write_bytes(data)
        object_path = f"football/reviewed/{path.name}"
        public = upload_image(base_url, key, object_path, cropped_path)
        updates.append((row, public, hashlib.sha256(data).hexdigest(), phash, width, height))
        print(f"approved {row['name']} -> {public}")
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany(
            """UPDATE player_headshots
                  SET source_url=%s,fallback_url=%s,provider='Human reviewed web candidate',
                      status='verified',content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
                      reviewed_at=now(),review_note=%s
                WHERE sport_id='football' AND player_id=%s""",
            [
                (
                    public,
                    row["source_url"],
                    sha,
                    phash,
                    width,
                    height,
                    f"Human-reviewed local web candidate. Source page: {row.get('source_page','')}",
                    row["player_id"],
                )
                for row, public, sha, phash, width, height in updates
            ],
        )
        cur.executemany(
            """INSERT INTO sport_player_images (sport_id,player_id,source_url,content_type)
               VALUES ('football',%s,%s,'image/jpeg')
               ON CONFLICT (sport_id,player_id)
               DO UPDATE SET source_url=EXCLUDED.source_url,content_type=EXCLUDED.content_type""",
            [(row["player_id"], public) for row, public, *_ in updates],
        )
        cur.executemany(
            """INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
               VALUES ('football',%s,'Human reviewed web candidate','verified',%s)
               ON CONFLICT (sport_id,player_id,provider)
               DO UPDATE SET status='verified',source_url=EXCLUDED.source_url,checked_at=now()""",
            [(row["player_id"], row["source_page"] or row["source_url"]) for row, *_ in updates],
        )
    print(f"Imported {len(updates)} reviewed headshots.")


if __name__ == "__main__":
    main()
