-- Emergency quota recovery: remove Football runtime/catalog rows from Supabase.
-- Local raw/SQLite Football data is not affected.
--
-- Run the DELETE block first in Supabase SQL Editor. It skips tables that do
-- not exist in the current production schema. If it succeeds, run the VACUUM
-- statements after it as a separate batch to release physical storage.

BEGIN;

DO $$
DECLARE
    table_name text;
    deleted_count bigint;
    tables text[] := ARRAY[
        'sport_online_queue',
        'sport_online_invites',
        'guest_random_playoff_conditions',
        'sport_online_games',
        'sport_player_usage',
        'player_headshot_source_attempts',
        'player_headshots',
        'sport_teammate_exclusions',
        'sport_teammates',
        'sport_live_player_games',
        'sport_live_game_imports',
        'sport_player_season_traits',
        'sport_player_traits',
        'sport_player_positions',
        'sport_player_images',
        'sport_players_searchable',
        'sport_player_aliases',
        'sport_player_external_ids',
        'sport_data_provenance',
        'sport_appearances',
        'sport_player_stints',
        'sport_teammate_stint_coverage',
        'sport_players',
        'sport_teams',
        'sport_franchises'
    ];
BEGIN
    FOREACH table_name IN ARRAY tables LOOP
        IF to_regclass('public.' || table_name) IS NOT NULL THEN
            EXECUTE format('DELETE FROM %I WHERE sport_id = %L', table_name, 'football');
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RAISE NOTICE '% deleted rows: %', table_name, deleted_count;
        ELSE
            RAISE NOTICE '% missing, skipped', table_name;
        END IF;
    END LOOP;
END $$;

UPDATE sports SET active = false WHERE sport_id = 'football';

COMMIT;

-- Run separately after the COMMIT above, if Supabase allows it:
-- VACUUM (FULL, ANALYZE) sport_teammates;
-- VACUUM (FULL, ANALYZE) sport_live_player_games;
-- VACUUM (FULL, ANALYZE) sport_live_game_imports;
-- VACUUM (FULL, ANALYZE) sport_appearances;
-- VACUUM (FULL, ANALYZE) sport_player_stints;
-- VACUUM (FULL, ANALYZE) sport_players;
-- VACUUM (FULL, ANALYZE) sport_teams;
