-- Emergency quota recovery step 1: free disk immediately by dropping large
-- derived sports indexes. These indexes can be recreated after compact data is
-- restored. Run this before row deletes if the project is near 100% disk.

DROP INDEX IF EXISTS idx_sport_live_player_games_pair;
DROP INDEX IF EXISTS idx_sport_live_player_games_rollup;
DROP INDEX IF EXISTS idx_sport_live_player_games_date;
DROP INDEX IF EXISTS idx_sport_teammates_pair;
DROP INDEX IF EXISTS idx_sport_teammates_a;
DROP INDEX IF EXISTS idx_sport_teammates_b;
DROP INDEX IF EXISTS idx_mlb_tgp_pair;
DROP INDEX IF EXISTS idx_mlb_tgp_b_a;
DROP INDEX IF EXISTS idx_mlb_tgp_team_season;

-- Recreate later, after cleanup/reload:
-- CREATE INDEX IF NOT EXISTS idx_sport_teammates_pair
--     ON sport_teammates(sport_id, player_a_id, player_b_id);
-- CREATE INDEX IF NOT EXISTS idx_sport_teammates_a
--     ON sport_teammates(sport_id, player_a_id);
-- CREATE INDEX IF NOT EXISTS idx_sport_teammates_b
--     ON sport_teammates(sport_id, player_b_id);
-- CREATE INDEX IF NOT EXISTS idx_sport_live_player_games_rollup
--     ON sport_live_player_games(sport_id, season, player_id, team_id);
-- CREATE INDEX IF NOT EXISTS idx_sport_live_player_games_pair
--     ON sport_live_player_games(sport_id, game_id, team_id, player_id);
-- CREATE INDEX IF NOT EXISTS idx_sport_live_player_games_date
--     ON sport_live_player_games(sport_id, game_date DESC);
-- CREATE INDEX IF NOT EXISTS idx_mlb_tgp_pair
--     ON mlb_teammate_game_proofs(player_a_id, player_b_id);
-- CREATE INDEX IF NOT EXISTS idx_mlb_tgp_b_a
--     ON mlb_teammate_game_proofs(player_b_id, player_a_id);
-- CREATE INDEX IF NOT EXISTS idx_mlb_tgp_team_season
--     ON mlb_teammate_game_proofs(team_id, season);
