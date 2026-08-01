# Player Identity Reconciliation

## Purpose

TeamMateTag keeps a canonical playable player graph in `sport_players` and
`sport_appearances`. External sources are never allowed to overwrite that
graph based only on a display name. Instead, the local SQLite database keeps
the raw source reference, every imported fact, automated candidate matches,
and reviewed decisions separately.

This is the project-owned reconciliation layer. It supports adding sources
incrementally while keeping an evidence trail for every assignment.

## Refresh

Run the existing loaders first, then build the reconciliation layer:

```powershell
python scripts\load_local_sport_traits.py --nfl-last 2024
python scripts\supplement_hockeydb_history.py
python scripts\load_local_honors_history.py
python scripts\supplement_nfl_reference_ids.py
python scripts\reconcile_local_identities.py
```

The final command creates or refreshes these ignored local SQLite tables:

- `source_player_references`: exactly what a source called a player in a
  particular season.
- `source_fact_observations`: source facts such as MVP, Pro Bowl, All-Star,
  or championship membership.
- `player_identity_claims`: accepted, rejected, or review-needed assignments
  from a source reference to one canonical TeamMateTag player ID.
- `player_identity_candidates`: ranked possible canonical players. These are
  suggestions, never gameplay data on their own.

It also writes `db/identity_review_queue.csv`, which can be opened in Excel.
It lists every unmatched source reference and up to five candidates with the
matching rationale.

Records outside the currently playable scope are retained but closed with an
auditable disposition. NFL honors before the Super Bowl-era roster graph begins
in 1966 are retained but not active review items. ABA data is filtered from the
NBA source at ingestion, rather than inferred from its date.

## Historical Product Scope

- MLB: 1903 World Series era onward.
- NBA: 1946-47 BAA season and all NBA seasons, never ABA facts.
- NHL: 1917-18 NHL season onward.
- NFL: 1966 Super Bowl era onward.

Each sport needs player identity, player-team-season appearances, season and
career statistics, individual awards, and championship roster membership.

## Review Rules

1. Prefer an independent player identifier, roster page, or authoritative
   biographical source over a name-only similarity.
2. Confirm the player was active in the source season.
3. Confirm the canonical player has a usable player-team-season history before
   using the fact in game behavior.
4. Keep ambiguous same-name records unresolved until stronger evidence exists.
5. Record each reviewed decision through the command below, including concise
   evidence. Do not patch the loader with a one-off alias unless that alias is
   a broadly reusable, unambiguous identity rule.

Example, after finding the local player ID in the candidate report:

```powershell
python scripts\reconcile_local_identities.py --accept basketball nba_award_audit "Larry Brown" 1968 <PLAYER_ID> --evidence "Verified against Basketball-Reference player page and 1968 ABA award listing."
```

Replace `<PLAYER_ID>` with the exact local ID shown in the report or SQLite
query. Manual decisions survive later refreshes.

## Current Scope

The first import covers `sport_honors` and `sport_honor_unresolved`. The same
tables are intentionally generic so later roster, career-stat, award, and
championship sources can be added without redesigning the database. A future
source loader should insert raw source references and observations first, then
generate candidates and promote only reviewed matches to accepted claims.
