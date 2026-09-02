import sqlite3

from game.engine import GameState
from web.server import _local_po_powerup_move


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE sport_players (
            sport_id TEXT,
            player_id TEXT,
            display_name TEXT,
            primary_pos TEXT,
            final_year INTEGER
        );
        CREATE TABLE sport_players_searchable (
            sport_id TEXT,
            player_id TEXT,
            display_name TEXT,
            disambiguation TEXT,
            search_key TEXT,
            career_games INTEGER
        );
        CREATE TABLE sport_appearances (
            sport_id TEXT,
            player_id TEXT,
            team_id TEXT,
            season INTEGER,
            games INTEGER
        );
        CREATE TABLE sport_teams (
            sport_id TEXT,
            team_id TEXT,
            season INTEGER,
            name TEXT,
            franchise_id TEXT
        );
        CREATE TABLE sport_player_traits (
            sport_id TEXT,
            player_id TEXT,
            career_games INTEGER,
            career_points INTEGER,
            career_goals INTEGER,
            career_assists INTEGER,
            career_touchdowns INTEGER,
            passing_touchdowns INTEGER,
            rushing_touchdowns INTEGER,
            receiving_touchdowns INTEGER,
            career_sacks INTEGER,
            career_interceptions INTEGER,
            mvp_count INTEGER,
            roty_count INTEGER,
            all_star_count INTEGER,
            championship_count INTEGER
        );
        CREATE TABLE sport_player_season_traits (
            sport_id TEXT,
            player_id TEXT,
            season INTEGER,
            points INTEGER,
            goals INTEGER,
            assists INTEGER,
            touchdowns INTEGER,
            passing_touchdowns INTEGER,
            rushing_touchdowns INTEGER,
            receiving_touchdowns INTEGER,
            sacks INTEGER,
            interceptions INTEGER
        );
        INSERT INTO sport_teams VALUES ('hockey', 'TOR', 2008, 'Toronto Maple Leafs', 'TOR');
        INSERT INTO sport_teams VALUES ('hockey', 'TOR', 2018, 'Toronto Maple Leafs', 'TOR');
        INSERT INTO sport_players VALUES ('hockey', 'sundin', 'Mats Sundin', 'C', 2009);
        INSERT INTO sport_players VALUES ('hockey', 'matthews', 'Auston Matthews', 'C', 2026);
        INSERT INTO sport_players VALUES ('hockey', 'tavares', 'John Tavares', 'C', 2026);
        INSERT INTO sport_players_searchable VALUES ('hockey', 'sundin', 'Mats Sundin', 'C, 2009-2009', 'matssundin', 1346);
        INSERT INTO sport_players_searchable VALUES ('hockey', 'matthews', 'Auston Matthews', 'C, 2026-2026', 'austonmatthews', 629);
        INSERT INTO sport_players_searchable VALUES ('hockey', 'tavares', 'John Tavares', 'C, 2026-2026', 'johntavares', 1184);
        INSERT INTO sport_appearances VALUES ('hockey', 'sundin', 'TOR', 2008, 74);
        INSERT INTO sport_appearances VALUES ('hockey', 'matthews', 'TOR', 2018, 68);
        INSERT INTO sport_appearances VALUES ('hockey', 'tavares', 'TOR', 2018, 82);
        INSERT INTO sport_player_traits VALUES ('hockey', 'sundin', 1346, 1349, 564, 785, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        INSERT INTO sport_player_traits VALUES ('hockey', 'matthews', 629, 727, 401, 326, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        INSERT INTO sport_player_traits VALUES ('hockey', 'tavares', 1184, 1110, 494, 616, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        """
    )
    return conn


def _game(current_player_id="sundin"):
    return {
        "sport": "hockey",
        "state": GameState(
            chain=[current_player_id],
            chain_names=["Mats Sundin"],
            chain_shared_with_prev=[[]],
            strikes={},
        ),
        "active_turn_powerup": "veteran_presence",
    }


def test_veteran_presence_stat_failure_returns_ineligible_message():
    result = _local_po_powerup_move(_conn(), _game(), "Auston Matthews", None)

    assert result["outcome"] == "powerup_not_eligible"
    assert result["powerup_label"] == "Veteran Presence"
    assert "727 career points" in result["reason"]
    assert "requires 800" in result["reason"]


def test_veteran_presence_allows_same_franchise_qualified_non_teammate():
    game = _game()
    result = _local_po_powerup_move(_conn(), game, "John Tavares", None)

    assert result["outcome"] == "valid"
    assert result["move_via_powerup"] is True
    assert result["shared_seasons"] == [{"team_id": "TOR", "season": 2018}]
    assert game["chain_link_meta"][-1]["powerup_key"] == "veteran_presence"
