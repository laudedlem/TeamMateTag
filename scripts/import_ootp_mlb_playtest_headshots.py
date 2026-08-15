"""Match OOTP's local historical MLB Facepack to active baseball image gaps."""
from __future__ import annotations

import csv
import hashlib
import io
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web")); sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
from audit_runtime_headshots import dhash  # noqa: E402
from name_normalize import normalize  # noqa: E402

ARCHIVE = ROOT / "raw" / "ootp" / "COFacepackV18.zip"
EXTRACTED = ROOT / "raw" / "ootp" / "matched_mlb_headshots"
REPORT = ROOT / "raw" / "ootp_mlb_playtest_headshots.csv"
MANUAL_FILENAME_OVERRIDES = {
    # Lahman omits suffixes in these display names, while OOTP includes them.
    "hairsje02": "Jerry Hairston Jr",
    "castrra01": "Ramon Castro",
    "castial01": "Alberto Castillo",
    "raineti02": "Tim Raines Jr",
    "rodried03": "Eddy Rodriguez P",
}
UNSAFE_OOTP_PLAYER_IDS = {
    # These names have multiple MLB players and the OOTP filename is either
    # generic or known to be the wrong same-name player.
    "castrra02",
    "deshide01",
    "nunezab01",
    "penato02",
    "wilsocr02",
    "wilsocr03",
}


def main() -> None:
    if not ARCHIVE.exists():
        raise RuntimeError(f"Missing OOTP Facepack at {ARCHIVE}")
    with zipfile.ZipFile(ARCHIVE) as archive:
        files = [info for info in archive.infolist() if not info.is_dir() and info.filename.lower().endswith((".jpg", ".jpeg", ".png"))]
        by_name: dict[str, list[zipfile.ZipInfo]] = defaultdict(list)
        for info in files:
            by_name[normalize(Path(info.filename).stem.replace("_", " "))].append(info)
        with server.db() as conn:
            players = conn.execute("""SELECT p.player_id,concat_ws(' ',p.name_first,p.name_last),p.retro_id
                FROM players p JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=p.player_id
                WHERE h.status IN ('placeholder','missing')""").fetchall()
        local_names = Counter(normalize(name) for _, name, _ in players)
        name_retro_ids: dict[str, set[str]] = defaultdict(set)
        for _player_id, name, retro_id in players:
            if retro_id:
                name_retro_ids[normalize(name)].add(retro_id)
        matches = [(player_id, name, candidates[0]) for player_id, name, retro_id in players
                   if player_id not in UNSAFE_OOTP_PLAYER_IDS
                   and len(candidates := by_name.get(normalize(name), [])) == 1
                   and (local_names[normalize(name)] == 1
                        or (retro_id and name_retro_ids[normalize(name)] == {retro_id}))]
        matched_ids = {player_id for player_id, _name, _info in matches}
        for player_id, name, _retro_id in players:
            if player_id in matched_ids or player_id not in MANUAL_FILENAME_OVERRIDES:
                continue
            candidates = by_name.get(normalize(MANUAL_FILENAME_OVERRIDES[player_id]), [])
            if len(candidates) == 1:
                matches.append((player_id, name, candidates[0]))
                matched_ids.add(player_id)
        EXTRACTED.mkdir(parents=True, exist_ok=True)
        records=[]
        for player_id, name, info in matches:
            content = archive.read(info)
            try:
                perceptual_hash, width, height = dhash(content)
            except Exception as error:
                records.append((player_id, name, info.filename, "missing", "", "", "", "", str(error)[:200])); continue
            if width < 80 or height < 80:
                records.append((player_id, name, info.filename, "missing", "", "", width, height, "image too small")); continue
            target = EXTRACTED / f"{player_id}.jpg"
            target.write_bytes(content)
            records.append((player_id, name, info.filename, "verified", hashlib.sha256(content).hexdigest(), perceptual_hash, width, height, ""))
    with REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer=csv.writer(handle); writer.writerow(["player_id","display_name","archive_file","status","sha256","perceptual_hash","width","height","note"]); writer.writerows(records)
    promoted=[row for row in records if row[3]=='verified']
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany("""INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
          VALUES ('baseball',%s,'OOTP Facepack',%s,%s) ON CONFLICT (sport_id,player_id,provider) DO UPDATE SET
          status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",
          [(player_id, 'candidate' if status=='verified' else status, f'/local-headshots/ootp/{player_id}.jpg') for player_id,_,_,status,*_ in records])
        cur.executemany("""UPDATE player_headshots SET source_url=%s,fallback_url=NULL,provider='OOTP Facepack',status='verified',
          content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
          review_note='Local playtest OOTP Facepack image; requires storage and license/source review before production.'
          WHERE sport_id='baseball' AND player_id=%s""",
          [(f'/local-headshots/ootp/{player_id}.jpg',digest,phash,width,height,player_id) for player_id,_,_,_,digest,phash,width,height,_ in promoted])
    print(f'Matched {len(matches):,} OOTP filename identities; promoted {len(promoted):,} local MLB images.')


if __name__ == '__main__':
    main()
