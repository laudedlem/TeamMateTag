# Cross-Sport Deployment

The online catalog is intentionally compact. Supabase stores only the data
needed during a game:

- franchises, team-seasons, players, and player search rows;
- roster appearances, which derive teammate links on demand;
- aggregate and season traits used by Playoffs;
- position history for Film Review and one known remote headshot URL per
  player when a source provides it.

It does not store raw Kaggle/API files, identity-resolution evidence,
unresolved source rows, or the local headshot cache. Do not create a
materialized teammate-pair table. The previous Baseball pair table consumed
about 400 MB alone; indexed appearance rows serve the same runtime purpose.

## Current Import

On 2026-08-01, Basketball, Football, and Hockey were imported to Supabase:

- 39,947 players
- 37,261 player-position rows
- 210,721 roster appearances
- 24,088 aggregate trait rows
- 117,227 player-season trait rows
- 27,431 player image URL rows, with no headshot binaries stored in Supabase
- about 165-171 MB total Supabase database size after removal of the obsolete
  pair table

This is below Supabase Free's 500 MB database quota. Check the current limit
in the Supabase dashboard before a large data refresh.

## Refreshing Online Data

From the repository root, after rebuilding and validating local data:

```bash
python scripts/load_local_sport_traits.py
python scripts/load_local_honors_history.py
python scripts/migrate_cross_sport_to_postgres.py
```

The importer reads `DATABASE_URL` from `.env`, applies the additive cross-sport
schema, and atomically replaces only the three non-baseball sports. It prints
the resulting Supabase database size. It is safe to run again after a refresh.

## Runtime Status

The Flask server now has persistent Postgres adapters for cross-sport Batting
Practice and Film Review under `/api/sports/<sport>/...`. They use the same
appearance-based link engine and persist game JSON in the existing `bp_games`
and `fr_games` tables. The browser keeps using the local adapter only when
`TEAMMATETAG_LOCAL_SPORTS=1`; a deployed server uses the Postgres path.

Division Rivalry and Playoffs still need their persistent cross-sport queue,
challenge, rematch, results, and rating adapters. Do not present those two
non-baseball modes as production-ready until their server contracts have been
implemented and smoke-tested.
