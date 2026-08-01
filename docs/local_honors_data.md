# Local Honors Data

Run these commands in order to refresh the ignored local SQLite dataset:

```powershell
python scripts\load_local_sport_traits.py --nfl-last 2024
python scripts\supplement_hockeydb_history.py
python scripts\load_local_honors_history.py
python scripts\supplement_nfl_reference_ids.py
python scripts\reconcile_local_identities.py
```

The second loader enriches `db/teammatetag_local.sqlite` with:

- `sport_honors`: queryable player-level awards and selections.
- `sport_honor_unresolved`: source rows that could not safely be assigned to
  one local player. These are retained with a source URL and resolution reason.
- Updated `sport_player_traits`: NFL MVP, offensive/defensive Rookie of the
  Year, Pro Bowl counts, and full Super Bowl-era roster championship counts.

Current honor coverage (last refreshed 2026-07-31):

- NFL AP MVP and offensive/defensive Rookie of the Year.
- NFL Pro Bowl selections from the Wikipedia player lists.
- NFL AP first-team All-Pro selections, 1999-2025.
- Super Bowl champion roster-season counts, Super Bowl I through LIX.
- Stanley Cup champion roster-season counts, including Hockey Databank `SC`
  results before 1986 and the existing modern NHL source.
- HockeyDB NHL player IDs and 48,000-plus historical player-team-season
  scoring stints, used as a supplement to the NHL roster API.
- Official NHL stats player IDs and team-season appearances for 2024-25 and
  2025-26, including recent call-ups omitted by the roster-history source.

These are roster-season counts. They indicate that a player appeared for the
champion in that season and do not assert that every club issued a ring.

## Historical Boundaries

- AP MVP and the original AP Rookie of the Year data begin in 1957. Separate
  offensive and defensive Rookie of the Year records begin in 1967.
- Pro Bowl selection history begins with the 1950 season. The 1961-69 AFL
  All-Star selections are included in the published player lists.
- The Super Bowl results source begins with the 1966 season and has no missing
  championship season in that span.
- NHL's 1919 Stanley Cup Final was cancelled because of the influenza epidemic.
  No champion should be credited for that season.
- The 2004-05 NHL lockout cancelled the season, so no Stanley Cup champion is
  credited. Any data source row claiming a champion for that season is rejected.

## Remaining Gaps

- 43 NBA archive rows are unresolved: 41 All-Star rows and 2 award-winner
  rows. They are primarily ABA-era players, nicknames, and same-name players
  without a safe local graph match.
- 565 NHL source rows remain unresolved: 130 award rows and 435 career-stat
  rows. The award loader now resolves formal names and surname-only records
  whenever there is one safe local match. The remainder are ambiguous or
  missing from the local NHL roster graph.
- 825 NFL honor rows remain unresolved: 789 Pro Bowl rows, 24 All-Pro rows,
  9 MVP rows, 2 offensive ROY rows, and 1 defensive ROY row. They mostly
  refer to historical players absent from the local roster graph or names that
  collide with another player.
- First-team NFL All-Pro coverage is currently 1999-2025. Earlier All-Pro
  history is not yet imported because the public yearly source format changes
  by era.
- `sport_honors` currently contains 19,051 resolved player-level records.
