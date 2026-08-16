-- Cross-sport data model for NBA, NHL, and NFL expansion.
--
-- Baseball remains in the original MLB-specific tables for now. These tables
-- use sport_id as part of every key so different leagues can reuse external
-- player, team, and franchise identifiers without collisions. The game engine
-- will gain a sport adapter after the first non-baseball graph is loaded.

CREATE TABLE IF NOT EXISTS sports (
    sport_id      TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    league_name   TEXT NOT NULL,
    active         BOOLEAN NOT NULL DEFAULT false,
    first_season   INTEGER,
    last_season    INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE sports ADD COLUMN IF NOT EXISTS first_season INTEGER;
ALTER TABLE sports ADD COLUMN IF NOT EXISTS last_season INTEGER;

INSERT INTO sports (sport_id, display_name, league_name, active) VALUES
    ('basketball', 'Basketball', 'NBA', false),
    ('hockey', 'Hockey', 'NHL', false),
    ('football', 'Football', 'NFL', false)
ON CONFLICT (sport_id) DO UPDATE
SET display_name = EXCLUDED.display_name,
    league_name = EXCLUDED.league_name;

CREATE TABLE IF NOT EXISTS sport_franchises (
    sport_id      TEXT NOT NULL REFERENCES sports(sport_id),
    franchise_id  TEXT NOT NULL,
    name          TEXT NOT NULL,
    active         BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (sport_id, franchise_id)
);

CREATE TABLE IF NOT EXISTS sport_teams (
    sport_id      TEXT NOT NULL REFERENCES sports(sport_id),
    team_id       TEXT NOT NULL,
    season        INTEGER NOT NULL,
    franchise_id  TEXT NOT NULL,
    league         TEXT,
    conference     TEXT,
    division       TEXT,
    name           TEXT NOT NULL,
    PRIMARY KEY (sport_id, team_id, season),
    FOREIGN KEY (sport_id, franchise_id)
        REFERENCES sport_franchises(sport_id, franchise_id)
);

CREATE TABLE IF NOT EXISTS sport_players (
    sport_id       TEXT NOT NULL REFERENCES sports(sport_id),
    player_id      TEXT NOT NULL,
    external_id    TEXT,
    display_name   TEXT NOT NULL,
    first_name     TEXT,
    last_name      TEXT,
    birth_year     INTEGER,
    debut_year     INTEGER,
    final_year     INTEGER,
    primary_pos    TEXT,
    PRIMARY KEY (sport_id, player_id)
);

ALTER TABLE sport_players DROP CONSTRAINT IF EXISTS sport_players_sport_id_external_id_key;
DROP INDEX IF EXISTS idx_sport_players_external_id;
CREATE INDEX IF NOT EXISTS idx_sport_players_name
    ON sport_players(sport_id, last_name, display_name);

CREATE TABLE IF NOT EXISTS sport_player_positions (
    sport_id      TEXT NOT NULL REFERENCES sports(sport_id),
    player_id     TEXT NOT NULL,
    position      TEXT NOT NULL,
    games         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (sport_id, player_id, position),
    FOREIGN KEY (sport_id, player_id)
        REFERENCES sport_players(sport_id, player_id)
);

CREATE TABLE IF NOT EXISTS sport_player_images (
    sport_id      TEXT NOT NULL REFERENCES sports(sport_id),
    player_id     TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    content_type  TEXT,
    PRIMARY KEY (sport_id, player_id),
    FOREIGN KEY (sport_id, player_id)
        REFERENCES sport_players(sport_id, player_id)
);

-- One qualifying roster or regular-season appearance per player, team, season.
-- Exact roster inclusion rules are documented with each source loader.
CREATE TABLE IF NOT EXISTS sport_appearances (
    sport_id       TEXT NOT NULL,
    player_id      TEXT NOT NULL,
    team_id        TEXT NOT NULL,
    season         INTEGER NOT NULL,
    games_total    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (sport_id, player_id, team_id, season),
    FOREIGN KEY (sport_id, player_id)
        REFERENCES sport_players(sport_id, player_id),
    FOREIGN KEY (sport_id, team_id, season)
        REFERENCES sport_teams(sport_id, team_id, season)
);

CREATE INDEX IF NOT EXISTS idx_sport_appearances_team_season
    ON sport_appearances(sport_id, team_id, season);
CREATE INDEX IF NOT EXISTS idx_sport_appearances_player
    ON sport_appearances(sport_id, player_id);

-- Optional strict teammate validation. When a sport/season is listed in
-- sport_teammate_stint_coverage with strict=1, players only count as
-- teammates if their player/team/season stint ranges overlap.
CREATE TABLE IF NOT EXISTS sport_player_stints (
    sport_id       TEXT NOT NULL,
    player_id      TEXT NOT NULL,
    team_id        TEXT NOT NULL,
    season         INTEGER NOT NULL,
    first_unit     INTEGER NOT NULL,
    last_unit      INTEGER NOT NULL,
    first_label    TEXT,
    last_label     TEXT,
    source         TEXT,
    PRIMARY KEY (sport_id, player_id, team_id, season),
    FOREIGN KEY (sport_id, player_id)
        REFERENCES sport_players(sport_id, player_id),
    FOREIGN KEY (sport_id, team_id, season)
        REFERENCES sport_teams(sport_id, team_id, season)
);

CREATE INDEX IF NOT EXISTS idx_sport_stints_link
    ON sport_player_stints(sport_id, team_id, season, player_id);

CREATE TABLE IF NOT EXISTS sport_teammate_stint_coverage (
    sport_id       TEXT NOT NULL REFERENCES sports(sport_id),
    season         INTEGER NOT NULL,
    coverage_type  TEXT NOT NULL,
    strict         INTEGER NOT NULL DEFAULT 1,
    source         TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sport_id, season)
);

-- Derived player-pair graph. player_a_id must sort before player_b_id.
CREATE TABLE IF NOT EXISTS sport_teammates (
    sport_id       TEXT NOT NULL REFERENCES sports(sport_id),
    player_a_id    TEXT NOT NULL,
    player_b_id    TEXT NOT NULL,
    team_id        TEXT NOT NULL,
    season         INTEGER NOT NULL,
    PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season),
    CHECK (player_a_id < player_b_id),
    FOREIGN KEY (sport_id, player_a_id)
        REFERENCES sport_players(sport_id, player_id),
    FOREIGN KEY (sport_id, player_b_id)
        REFERENCES sport_players(sport_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_sport_teammates_pair
    ON sport_teammates(sport_id, player_a_id, player_b_id);
CREATE INDEX IF NOT EXISTS idx_sport_teammates_a
    ON sport_teammates(sport_id, player_a_id);
CREATE INDEX IF NOT EXISTS idx_sport_teammates_b
    ON sport_teammates(sport_id, player_b_id);

CREATE TABLE IF NOT EXISTS sport_players_searchable (
    sport_id       TEXT NOT NULL,
    player_id      TEXT NOT NULL,
    display_name   TEXT NOT NULL,
    disambiguation TEXT NOT NULL,
    search_key     TEXT NOT NULL,
    last_key       TEXT NOT NULL,
    career_games   INTEGER NOT NULL DEFAULT 0,
    teammate_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (sport_id, player_id),
    FOREIGN KEY (sport_id, player_id)
        REFERENCES sport_players(sport_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_sport_searchable_key
    ON sport_players_searchable(sport_id, search_key);
CREATE INDEX IF NOT EXISTS idx_sport_searchable_last_key
    ON sport_players_searchable(sport_id, last_key);

CREATE TABLE IF NOT EXISTS sport_player_aliases (
    sport_id       TEXT NOT NULL,
    player_id      TEXT NOT NULL,
    alias_key      TEXT NOT NULL,
    PRIMARY KEY (sport_id, player_id, alias_key),
    FOREIGN KEY (sport_id, player_id)
        REFERENCES sport_players(sport_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_sport_aliases_key
    ON sport_player_aliases(sport_id, alias_key);

CREATE TABLE IF NOT EXISTS sport_data_provenance (
    sport_id       TEXT NOT NULL REFERENCES sports(sport_id),
    source         TEXT NOT NULL,
    season         INTEGER,
    source_url     TEXT,
    license_note   TEXT,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_count      INTEGER,
    PRIMARY KEY (sport_id, source, season)
);

-- Runtime-only Playoffs data. Historical source observations and unresolved
-- identity records deliberately remain in the local curation database.
CREATE TABLE IF NOT EXISTS sport_player_traits (
    sport_id              TEXT NOT NULL REFERENCES sports(sport_id),
    player_id             TEXT NOT NULL,
    career_games          INTEGER NOT NULL DEFAULT 0,
    career_points         INTEGER NOT NULL DEFAULT 0,
    career_goals          INTEGER NOT NULL DEFAULT 0,
    career_assists        INTEGER NOT NULL DEFAULT 0,
    career_touchdowns     INTEGER NOT NULL DEFAULT 0,
    passing_touchdowns    INTEGER NOT NULL DEFAULT 0,
    rushing_touchdowns    INTEGER NOT NULL DEFAULT 0,
    receiving_touchdowns  INTEGER NOT NULL DEFAULT 0,
    career_sacks          REAL NOT NULL DEFAULT 0,
    career_interceptions  INTEGER NOT NULL DEFAULT 0,
    all_star_count        INTEGER NOT NULL DEFAULT 0,
    mvp_count             INTEGER NOT NULL DEFAULT 0,
    roty_count            INTEGER NOT NULL DEFAULT 0,
    championship_count    INTEGER NOT NULL DEFAULT 0,
    source                TEXT NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sport_id, player_id),
    FOREIGN KEY (sport_id, player_id)
        REFERENCES sport_players(sport_id, player_id)
);

CREATE TABLE IF NOT EXISTS sport_player_season_traits (
    sport_id              TEXT NOT NULL REFERENCES sports(sport_id),
    player_id             TEXT NOT NULL,
    season                INTEGER NOT NULL,
    games                 INTEGER NOT NULL DEFAULT 0,
    points                INTEGER NOT NULL DEFAULT 0,
    goals                 INTEGER NOT NULL DEFAULT 0,
    assists               INTEGER NOT NULL DEFAULT 0,
    touchdowns            INTEGER NOT NULL DEFAULT 0,
    passing_touchdowns    INTEGER NOT NULL DEFAULT 0,
    rushing_touchdowns    INTEGER NOT NULL DEFAULT 0,
    receiving_touchdowns  INTEGER NOT NULL DEFAULT 0,
    sacks                 REAL NOT NULL DEFAULT 0,
    interceptions         INTEGER NOT NULL DEFAULT 0,
    source                TEXT NOT NULL,
    PRIMARY KEY (sport_id, player_id, season),
    FOREIGN KEY (sport_id, player_id)
        REFERENCES sport_players(sport_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_sport_player_season_traits_lookup
    ON sport_player_season_traits(sport_id, player_id);

-- Do not populate sport_teammates. It is retained for compatibility with an
-- early schema draft, but the runtime derives a link from the two indexed
-- appearance rows. Materializing every roster pair wastes the free-tier
-- database budget without making gameplay faster.
