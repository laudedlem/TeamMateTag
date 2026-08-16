"""Crop recent fallback headshots into consistent card portraits.

This is intended for the web-image/manual fallback photos that are more likely
to be wide screenshots, social posts, or full-body action shots. Originals stay
recorded in ``review_note`` while the game uses the cropped Supabase Storage URL.

Required local .env values for publishing:
  DATABASE_URL
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

import server  # noqa: E402
from audit_runtime_headshots import dhash  # noqa: E402
from publish_ootp_mlb_headshots import BUCKET, ensure_public_bucket, headers, public_url, storage_config  # noqa: E402

TARGET_SIZE = (360, 450)
PORTRAIT_ZOOM = 0.82
DEFAULT_PROVIDERS = (
    "Web image search",
    "Wikimedia Commons",
    "HockeyDB",
    "manual_submission",
)
OUT_DIR = ROOT / "raw" / "cropped_headshots"
REPORT = ROOT / "raw" / "cropped_headshots_review.md"
CSV_REPORT = ROOT / "raw" / "cropped_headshots_review.csv"
USER_AGENT = "TeamMateTag headshot cropper/0.2.10"


def fetch_bytes(url: str) -> bytes:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.content


def trim_plain_border(image: Image.Image) -> Image.Image:
    """Trim simple white/black/page-color borders without cutting into photos."""
    rgb = image.convert("RGB")
    bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    diff = ImageChops.difference(rgb, bg)
    diff = ImageChops.add(diff, diff, 2.0, -20)
    box = diff.getbbox()
    if not box:
        return rgb
    left, top, right, bottom = box
    width, height = rgb.size
    if (right - left) < width * 0.45 or (bottom - top) < height * 0.45:
        return rgb
    return rgb.crop(box)


def window_score(image: Image.Image, x: int, y: int, width: int, height: int) -> float:
    # Score the upper/middle body area, avoiding bottom captions when possible.
    sample = image.crop((x, y, x + width, y + max(1, int(height * 0.78)))).resize((90, 90))
    hsv = sample.convert("HSV")
    saturation = ImageStat.Stat(hsv.getchannel("S")).mean[0]
    brightness = ImageStat.Stat(hsv.getchannel("V")).mean[0]
    edges = sample.convert("L").filter(ImageFilter.FIND_EDGES)
    edge_mean = ImageStat.Stat(edges).mean[0]
    # Very bright/very dark windows are often logos, empty backgrounds, or text cards.
    exposure_penalty = abs(brightness - 125) * 0.12
    return saturation * 0.75 + edge_mean * 0.55 - exposure_penalty


def choose_crop_box(image: Image.Image) -> tuple[int, int, int, int]:
    width, height = image.size
    target_aspect = TARGET_SIZE[0] / TARGET_SIZE[1]

    crop_h = height
    crop_w = int(crop_h * target_aspect)
    if crop_w > width:
        crop_w = width
        crop_h = int(crop_w / target_aspect)

    crop_w = max(1, min(width, crop_w))
    crop_h = max(1, min(height, crop_h))
    crop_w = max(1, int(crop_w * PORTRAIT_ZOOM))
    crop_h = max(1, int(crop_h * PORTRAIT_ZOOM))
    max_x = max(0, width - crop_w)
    max_y = max(0, height - crop_h)

    y = min(max_y, int(height * 0.04))
    if max_x == 0:
        x = 0
    else:
        candidates = sorted({int(max_x * i / 16) for i in range(17)} | {max_x // 2})
        x = max(candidates, key=lambda candidate: window_score(image, candidate, y, crop_w, crop_h))
    return x, y, x + crop_w, y + crop_h


def crop_image(content: bytes) -> tuple[bytes, tuple[int, int], tuple[int, int, int, int]]:
    image = Image.open(io.BytesIO(content))
    image = ImageOps.exif_transpose(image).convert("RGB")
    image = trim_plain_border(image)
    box = choose_crop_box(image)
    cropped = image.crop(box).resize(TARGET_SIZE, Image.Resampling.LANCZOS)
    output = io.BytesIO()
    cropped.save(output, format="JPEG", quality=88, optimize=True, progressive=True)
    return output.getvalue(), image.size, box


def selected_rows(sports: list[str], providers: list[str], limit: int) -> list[dict]:
    params: list[object] = [sports, providers]
    sql = """
        SELECT h.sport_id,h.player_id,h.provider,h.source_url,h.width,h.height,
               COALESCE(sp.display_name, concat_ws(' ', p.name_first, p.name_last)) AS display_name
          FROM player_headshots h
          LEFT JOIN sport_players sp ON sp.sport_id=h.sport_id AND sp.player_id=h.player_id
          LEFT JOIN players p ON h.sport_id='baseball' AND p.player_id=h.player_id
         WHERE h.sport_id = ANY(%s)
           AND h.provider = ANY(%s)
           AND h.status='verified'
           AND h.source_url IS NOT NULL
           AND h.source_url NOT LIKE %s
         ORDER BY h.sport_id,h.provider,display_name,h.player_id
    """
    params.append("%/storage/v1/object/public/player-headshots/%/cropped/%")
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with server.db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "sport": sport,
            "player_id": player_id,
            "provider": provider,
            "source_url": source_url,
            "width": width,
            "height": height,
            "display_name": display_name,
        }
        for sport, player_id, provider, source_url, width, height, display_name in rows
    ]


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)


def process_row(row: dict) -> dict:
    try:
        content = fetch_bytes(row["source_url"])
        cropped, original_size, box = crop_image(content)
        sport_dir = OUT_DIR / row["sport"]
        sport_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{safe_name(row['player_id'])}.jpg"
        path = sport_dir / filename
        path.write_bytes(cropped)
        perceptual_hash, out_w, out_h = dhash(cropped)
        return {
            **row,
            "status": "cropped",
            "local_path": str(path),
            "object_path": f"{row['sport']}/cropped/{filename}",
            "original_size": f"{original_size[0]}x{original_size[1]}",
            "crop_box": ",".join(str(value) for value in box),
            "sha256": hashlib.sha256(cropped).hexdigest(),
            "perceptual_hash": perceptual_hash,
            "out_width": out_w,
            "out_height": out_h,
            "error": "",
        }
    except Exception as error:
        return {**row, "status": "failed", "error": str(error)[:300]}


def upload_image(base_url: str, key: str, object_path: str, path: Path) -> str:
    response = requests.post(
        f"{base_url}/storage/v1/object/{BUCKET}/{quote(object_path, safe='/')}",
        headers={**headers(key, "image/jpeg"), "x-upsert": "true"},
        data=path.read_bytes(),
        timeout=60,
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:240]}")
    return public_url(base_url, object_path)


def write_reports(rows: list[dict]) -> None:
    CSV_REPORT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "sport", "player_id", "display_name", "provider", "status", "source_url",
        "public_url", "local_path", "original_size", "crop_box", "error",
    ]
    with CSV_REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

    lines = ["# Cropped Headshot Review", ""]
    for row in rows:
        if row.get("status") != "cropped":
            lines.append(f"- {row['sport']} | {row['display_name']} | FAILED: {row.get('error', '')}")
            continue
        public = row.get("public_url") or row.get("local_path")
        lines.append(
            f"- {row['sport']} | {row['display_name']} | {row['provider']} | "
            f"[original]({row['source_url']}) | [crop]({public})"
        )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", action="append", choices=("baseball", "basketball", "football", "hockey"))
    parser.add_argument("--provider", action="append")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="Create local crops and reports only.")
    args = parser.parse_args()

    sports = args.sport or ["basketball", "hockey"]
    providers = args.provider or list(DEFAULT_PROVIDERS)
    rows = selected_rows(sports, providers, args.limit)
    print(f"Preparing {len(rows):,} recent fallback headshots for {', '.join(sports)}.", flush=True)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(process_row, row): row for row in rows}
        for index, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if index % 25 == 0 or index == len(rows):
                ok = sum(row["status"] == "cropped" for row in results)
                print(f"  cropped {index:,}/{len(rows):,} ({ok:,} ok)", flush=True)

    if not args.dry_run:
        base_url, key = storage_config()
        ensure_public_bucket(base_url, key)
        for row in results:
            if row.get("status") != "cropped":
                continue
            row["public_url"] = upload_image(base_url, key, row["object_path"], Path(row["local_path"]))

        updates = [row for row in results if row.get("public_url")]
        with server.db() as conn, conn.cursor() as cur:
            cur.executemany(
                """UPDATE player_headshots
                      SET fallback_url=source_url, source_url=%s, status='verified',
                          content_sha256=%s, perceptual_hash=%s, width=%s, height=%s,
                          reviewed_at=now(),
                          review_note=concat_ws(' ', review_note, %s::text)
                    WHERE sport_id=%s AND player_id=%s""",
                [
                    (
                        row["public_url"], row["sha256"], row["perceptual_hash"],
                        row["out_width"], row["out_height"],
                        f"Cropped card portrait from original {row['source_url']}.",
                        row["sport"], row["player_id"],
                    )
                    for row in updates
                ],
            )
            cur.executemany(
                """INSERT INTO sport_player_images (sport_id,player_id,source_url,content_type)
                   VALUES (%s,%s,%s,'image/jpeg')
                   ON CONFLICT (sport_id,player_id)
                   DO UPDATE SET source_url=EXCLUDED.source_url, content_type=EXCLUDED.content_type""",
                [(row["sport"], row["player_id"], row["public_url"]) for row in updates if row["sport"] != "baseball"],
            )
        print(f"Published and updated {len(updates):,} cropped headshots.", flush=True)

    write_reports(results)
    failed = sum(row["status"] != "cropped" for row in results)
    print(f"Done. Failed {failed:,}. Review: {REPORT}")


if __name__ == "__main__":
    main()
