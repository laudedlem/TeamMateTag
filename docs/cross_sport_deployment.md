# Cross-Sport Deployment

The online catalog is intentionally compact. Supabase stores only the data
needed during a game:

- franchises, team-seasons, players, and player search rows;
- roster appearances, which derive teammate links on demand;
- aggregate and season traits used by Playoffs.

It does not store raw Kaggle/API files, identity-resolution evidence,
unresolved source rows, or the local headshot cache. Do not create a
materialized teammate-pair table. The previous Baseball pair table consumed
about 400 MB alone; indexed appearance rows serve the same runtime purpose.

## Current Import

On 2026-08-01, Basketball, Football, and Hockey were imported to Supabase:

- 39,947 players
- 210,721 roster appearances
- 24,088 aggregate trait rows
- 117,227 player-season trait rows
- 170 MB total Supabase database size after removal of the obsolete pair table

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

## Remaining Runtime Work

The deployed Flask server still uses local SQLite adapters for non-baseball
playtesting. Replace those adapters with the existing Postgres request helper,
keeping the current JSON endpoint contracts. Start with Batting Practice,
then Film Review, Division Rivalry, and Playoffs. Do not enable a sport in
production until its server adapter and smoke tests are complete.
