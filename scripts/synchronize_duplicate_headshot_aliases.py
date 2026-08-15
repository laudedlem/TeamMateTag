"""Propagate a verified headshot across known duplicate source identities.

This does not merge player records or alter teammate data. It only prevents a
known source alias for the same person from appearing as a separate missing
headshot task. Baseball aliases are identified by shared RetroSheet ID; NHL
aliases must have identical name, birth year, position, and team-season career.
"""
from __future__ import annotations

import sys
from collections import defaultdict

sys.path.insert(0, "web")
import server  # noqa: E402


def baseball_groups(conn):
    rows=conn.execute("""SELECT p.player_id,p.retro_id,p.mlbam_id,h.status,h.source_url,h.fallback_url,h.provider,
      h.content_sha256,h.perceptual_hash,h.width,h.height,h.review_note
      FROM players p JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=p.player_id
      WHERE p.final_year>=2000 AND p.retro_id IS NOT NULL""").fetchall()
    groups=defaultdict(list)
    for row in rows:
        groups[row[1]].append(row)
    return [group for group in groups.values() if len(group) > 1]


def hockey_groups(conn):
    rows=conn.execute("""WITH signatures AS (
      SELECT p.player_id,p.display_name,p.birth_year,p.debut_year,p.final_year,
       CASE p.primary_pos WHEN 'L' THEN 'LW' WHEN 'R' THEN 'RW' ELSE p.primary_pos END AS primary_pos,
       string_agg((CASE regexp_replace(a.team_id, '^hdb:', '')
                    WHEN 'WAS' THEN 'WSH' WHEN 'CBS' THEN 'CBJ'
                    ELSE regexp_replace(a.team_id, '^hdb:', '') END)
                  || ':' || a.season::text, ',' ORDER BY a.team_id,a.season) AS teams
      FROM sport_players p JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
      WHERE p.sport_id='hockey' AND p.final_year>=2000
      GROUP BY p.player_id,p.display_name,p.birth_year,p.debut_year,p.final_year,p.primary_pos
    ) SELECT s.player_id,s.display_name,s.birth_year,s.debut_year,s.final_year,s.primary_pos,s.teams,
       h.status,h.source_url,h.fallback_url,h.provider,h.content_sha256,h.perceptual_hash,h.width,h.height,h.review_note
      FROM signatures s JOIN player_headshots h ON h.sport_id='hockey' AND h.player_id=s.player_id""").fetchall()
    groups=defaultdict(list)
    for row in rows:
        groups[row[1:7]].append(row)
    return [group for group in groups.values() if len(group) > 1]


def sync_group(cur, sport: str, group: list[tuple], status_index: int, source_index: int, provider_index: int,
               metadata_start: int, player_index: int = 0) -> int:
    verified=[row for row in group if row[status_index] == "verified" and row[source_index]]
    if not verified:
        return 0
    # For baseball, MLBAM-backed records sort first; for hockey, an NHL ID sorts first.
    source=sorted(verified, key=lambda row: (not str(row[player_index]).startswith("nhl:"), row[player_index]))[0]
    changed=[]
    for row in group:
        if row[status_index] == "verified" and row[source_index]:
            continue
        source_url,fallback_url,provider,digest,phash,width,height,note = (
            source[source_index], source[source_index + 1], source[provider_index], *source[metadata_start:metadata_start + 5]
        )
        changed.append((source_url,fallback_url,provider,digest,phash,width,height,
                        f"Shared verified headshot with duplicate source identity {source[player_index]}. {note or ''}"[:1000],
                        sport,row[player_index]))
    cur.executemany(
        """UPDATE player_headshots SET source_url=%s,fallback_url=%s,provider=%s,status='verified',
               content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,review_note=%s,reviewed_at=now()
             WHERE sport_id=%s AND player_id=%s""", changed
    )
    return len(changed)


def main() -> None:
    with server.db() as conn, conn.cursor() as cur:
        baseball=0
        for group in baseball_groups(conn):
            # columns: pid, retro, mlbam, status, url, fallback, provider, digest, phash, width, height, note
            baseball += sync_group(cur, "baseball", group, 3, 4, 6, 7)
        hockey=0
        for group in hockey_groups(conn):
            # columns: pid, name, birth, debut, final, pos, teams, status, url, fallback, provider, digest, phash, width, height, note
            hockey += sync_group(cur, "hockey", group, 7, 8, 10, 11)
    print(f"Synchronized {baseball} baseball and {hockey} hockey duplicate-identity headshots.")


if __name__ == "__main__":
    main()
