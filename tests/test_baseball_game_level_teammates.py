import sqlite3

from game.engine import get_shared_seasons


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE appearances (
            player_id TEXT,
            team_id TEXT,
            season INTEGER,
            games_total INTEGER,
            games_pitched INTEGER,
            games_batted INTEGER
        );
        CREATE TABLE teammate_exclusions (
            player_a_id TEXT,
            player_b_id TEXT,
            team_id TEXT,
            season INTEGER,
            reason TEXT
        );
        CREATE TABLE player_stints (
            player_id TEXT,
            team_id TEXT,
            season INTEGER,
            first_unit INTEGER,
            last_unit INTEGER,
            first_label TEXT,
            last_label TEXT,
            source TEXT
        );
        CREATE TABLE teammate_stint_coverage (
            season INTEGER,
            coverage_type TEXT,
            strict INTEGER,
            source TEXT
        );
        CREATE TABLE mlb_teammate_game_proofs (
            player_a_id TEXT,
            player_b_id TEXT,
            team_id TEXT,
            season INTEGER,
            shared_games INTEGER,
            first_game_pk INTEGER,
            first_game_date TEXT,
            source TEXT
        );
        """
    )
    return conn


def test_baseball_game_boxscore_coverage_requires_proof_row():
    conn = make_conn()
    conn.executemany(
        "INSERT INTO appearances VALUES (?, 'NYA', 2024, 80, 0, 80)",
        [("aaron",), ("loose_only",), ("proofed",)],
    )
    conn.execute(
        "INSERT INTO teammate_stint_coverage VALUES (2024, 'game_boxscore', 1, 'test')"
    )
    conn.execute(
        "INSERT INTO mlb_teammate_game_proofs VALUES ('aaron', 'proofed', 'NYA', 2024, 1, 777, '2024-04-01', 'test')"
    )

    assert get_shared_seasons(conn, "aaron", "proofed") == [("NYA", 2024)]
    assert get_shared_seasons(conn, "aaron", "loose_only") == []


def test_baseball_stint_coverage_still_uses_overlap_without_game_boxscore():
    conn = make_conn()
    conn.executemany(
        "INSERT INTO appearances VALUES (?, 'NYA', 2024, 80, 0, 80)",
        [("aaron",), ("overlap",), ("missed",)],
    )
    conn.execute(
        "INSERT INTO teammate_stint_coverage VALUES (2024, 'stint_range', 1, 'test')"
    )
    conn.executemany(
        "INSERT INTO player_stints VALUES (?, 'NYA', 2024, ?, ?, ?, ?, 'test')",
        [
            ("aaron", 20240401, 20240430, "2024-04-01", "2024-04-30"),
            ("overlap", 20240420, 20240515, "2024-04-20", "2024-05-15"),
            ("missed", 20240501, 20240515, "2024-05-01", "2024-05-15"),
        ],
    )

    assert get_shared_seasons(conn, "aaron", "overlap") == [("NYA", 2024)]
    assert get_shared_seasons(conn, "aaron", "missed") == []
