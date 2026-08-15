"""Match FHM's NHL Facepack 24-25 to unresolved playable NHL headshots.

The downloaded community pack is retained under ``raw/fhm``. Files are matched
only when the normalized name and birth year identify exactly one local player,
then extracted into a local review directory. Production publication is a
separate explicit step, as with the OOTP MLB pack.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web")); sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
from audit_runtime_headshots import dhash
from name_normalize import normalize

ARCHIVE = ROOT / "raw" / "fhm" / "NHL_facepack_24-25.zip"
EXTRACTED = ROOT / "raw" / "fhm" / "matched_nhl_headshots"
REPORT = ROOT / "raw" / "fhm_nhl_playtest_headshots.csv"


def face_key(filename: str) -> tuple[str, int] | None:
    stem = Path(filename).stem
    if stem.endswith("_away"):
        return None
    pieces = stem.rsplit("_", 3)
    if len(pieces) != 4 or not all(piece.isdigit() for piece in pieces[1:]):
        return None
    name, _day, _month, year = pieces
    return normalize(name.replace("_", " ")), int(year)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not ARCHIVE.exists():
        raise RuntimeError(f"Missing FHM archive: {ARCHIVE}")
    with server.db() as conn:
        players = conn.execute(
            """SELECT p.player_id, p.display_name, p.birth_year
                 FROM sport_players p JOIN player_headshots h
                   ON h.sport_id=p.sport_id AND h.player_id=p.player_id
                 WHERE p.sport_id='hockey' AND p.final_year>=2000
                   AND h.status IN ('placeholder','missing')"""
        ).fetchall()
    player_index: dict[tuple[str, int], list[tuple[str, str]]] = defaultdict(list)
    for player_id, name, birth_year in players:
        if birth_year:
            player_index[(normalize(name), int(birth_year))].append((player_id, name))
    with zipfile.ZipFile(ARCHIVE) as archive:
        face_index: dict[tuple[str, int], list[zipfile.ZipInfo]] = defaultdict(list)
        for info in archive.infolist():
            if info.is_dir() or Path(info.filename).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            key = face_key(info.filename)
            if key:
                face_index[key].append(info)
        matches = []
        for key, local_players in player_index.items():
            candidates = face_index.get(key, [])
            if len(local_players) == 1 and len(candidates) == 1:
                matches.append((local_players[0][0], local_players[0][1], candidates[0]))
        print(f"Matched {len(matches):,} unambiguous FHM images from {len(players):,} NHL gaps.")
        if args.dry_run:
            return
        EXTRACTED.mkdir(parents=True, exist_ok=True)
        records=[]
        for player_id, name, info in matches:
            content = archive.read(info)
            try:
                perceptual_hash, width, height = dhash(content)
            except Exception as error:
                records.append((player_id, name, info.filename, "missing", "", "", "", "", str(error)[:200]))
                continue
            if width < 80 or height < 80:
                records.append((player_id, name, info.filename, "missing", "", "", width, height, "image too small"))
                continue
            target = EXTRACTED / f"{player_id.replace(':', '_')}.png"
            target.write_bytes(content)
            records.append((player_id, name, info.filename, "verified", hashlib.sha256(content).hexdigest(), perceptual_hash, width, height, ""))
    with REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer=csv.writer(handle)
        writer.writerow(["player_id","display_name","archive_file","status","sha256","perceptual_hash","width","height","note"])
        writer.writerows(records)
    promoted=[row for row in records if row[3] == "verified"]
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
                 VALUES ('hockey',%s,'FHM Facepack 24-25',%s,%s)
                 ON CONFLICT (sport_id,player_id,provider) DO UPDATE SET
                    status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",
            [(player_id, "candidate" if status == "verified" else status, f"/local-headshots/fhm/{player_id.replace(':', '_')}.png")
             for player_id, _, _, status, *_ in records],
        )
        cur.executemany(
            """UPDATE player_headshots SET source_url=%s, fallback_url=NULL,provider='FHM Facepack 24-25',status='verified',
                   content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
                   review_note='Community FHM Facepack image for playtesting; requires source/license review before production.'
                 WHERE sport_id='hockey' AND player_id=%s""",
            [(f"/local-headshots/fhm/{player_id.replace(':', '_')}.png", digest, phash, width, height, player_id)
             for player_id, _, _, _, digest, phash, width, height, _ in promoted],
        )
    print(f"Extracted and registered {len(promoted):,} FHM NHL images for local review.")


if __name__ == "__main__":
    main()
