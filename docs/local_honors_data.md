# Local Honors Data

Run these commands in order to refresh the ignored local SQLite dataset:

```powershell
python scripts\load_local_sport_traits.py --nfl-last 2024
python scripts\load_local_honors_history.py
```

The second loader enriches `db/teammatetag_local.sqlite` with:

- `sport_honors`: queryable player-level awards and selections.
- `sport_honor_unresolved`: source rows that could not safely be assigned to
  one local player. These are retained with a source URL and resolution reason.
- Updated `sport_player_traits`: NFL MVP, offensive/defensive Rookie of the
  Year, Pro Bowl counts, and full Super Bowl-era roster championship counts.

Current honor coverage:

- NFL AP MVP and offensive/defensive Rookie of the Year.
- NFL Pro Bowl selections from the Wikipedia player lists.
- NFL AP first-team All-Pro selections, 1999-2025.
- Super Bowl champion roster-season counts, Super Bowl I through LIX.
- Stanley Cup champion roster-season counts, including Hockey Databank `SC`
  results before 1986 and the existing modern NHL source.

These are roster-season counts. They indicate that a player appeared for the
champion in that season and do not assert that every club issued a ring.
