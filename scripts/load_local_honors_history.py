"""Load locally queryable honors, championship results, and resolution audits.

This supplements ``sport_player_traits``. Every source row is retained in
``sport_honors`` when it resolves unambiguously, otherwise in
``sport_honor_unresolved``. Run after ``load_local_sport_traits.py``:

    python scripts/load_local_honors_history.py
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import zipfile
from collections import defaultdict
from html.parser import HTMLParser

import requests

from build_local_sports_dataset import ROOT
from name_normalize import normalize


DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
NBA_AWARDS_URL = "https://www.kaggle.com/api/v1/datasets/download/sumitrodatta/nba-aba-baa-stats"
HOCKEYDB_MASTER_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/Master.csv"
HOCKEYDB_AWARDS_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/AwardsPlayers.csv"
HOCKEYDB_TEAMS_URL = "https://raw.githubusercontent.com/rippinrobr/hockey-databank/master/Teams.csv"
NHL_STATS_CACHE = ROOT / "raw" / "nhl_player_database.zip"
SUPER_BOWL_URL = "https://www.kaggle.com/api/v1/datasets/download/ronitagarwal1/super-bowl-dataset-i-lix"
WIKI = "https://en.wikipedia.org/wiki/"
WIKI_HEADERS = {"User-Agent": "TeamMateTag/0.1 data refresh (contact@teammatetag.com)"}
PRO_BOWL_PAGES = ("A", "B", "C%E2%80%93F", "G%E2%80%93H", "I%E2%80%93K", "L%E2%80%93M", "N%E2%80%93R", "S%E2%80%93V", "W%E2%80%93Z")
# Source names used for well-known nicknames or translated NHL first names.
# These are only applied where the canonical local display name is unique.
NAME_ALIASES = {
    "tiny archibald": "nate archibald",
    "fat lever": "lafayette lever",
    "cadillac williams": "carnell williams",
    "patrick surtain ii": "pat surtain ii",
    "yegor zamula": "egor zamula",
}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS sport_honors (
  sport_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  honor TEXT NOT NULL,
  season INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (sport_id, player_id, honor, season, source)
);
CREATE INDEX IF NOT EXISTS idx_sport_honors_player ON sport_honors(sport_id, player_id, honor);
CREATE TABLE IF NOT EXISTS sport_honor_unresolved (
  sport_id TEXT NOT NULL,
  category TEXT NOT NULL,
  season INTEGER,
  source_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source TEXT NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY (sport_id, category, season, source_name, source)
);
"""


class WikiTables(HTMLParser):
    """Small dependency-free parser for Wikipedia's sortable tables."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.table: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] = []
        self.in_table = False
        self.in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class") or ""
        if tag == "table" and "wikitable" in classes:
            self.in_table = True
            self.table = []
        elif self.in_table and tag == "tr":
            self.row = []
        elif self.in_table and tag in {"td", "th"}:
            self.in_cell = True
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_table and tag in {"td", "th"}:
            self.row.append("".join(self.cell).strip())
            self.in_cell = False
        elif self.in_table and tag == "tr" and self.row:
            self.table.append(self.row)
        elif self.in_table and tag == "table":
            self.tables.append(self.table)
            self.in_table = False


class WikiLinkedTables(WikiTables):
    """Wikipedia table parser that retains linked text within each cell."""

    def __init__(self) -> None:
        super().__init__()
        self.link_cells: list[list[list[str]]] = []
        self.link_table: list[list[list[str]]] = []
        self.link_row: list[list[str]] = []
        self.links: list[str] = []
        self.in_link = False
        self.link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        href = dict(attrs).get("href") or ""
        if self.in_cell and tag == "a" and (href.startswith("/wiki/") or href.startswith("//en.wikipedia.org/wiki/") or href.startswith("./")):
            self.in_link = True; self.link_text = []

    def handle_data(self, data: str) -> None:
        super().handle_data(data)
        if self.in_link:
            self.link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_link and tag == "a":
            name = "".join(self.link_text).strip()
            if name:
                self.links.append(name)
            self.in_link = False
        if self.in_table and tag in {"td", "th"}:
            self.link_row.append(self.links)
            self.links = []
        if self.in_table and tag == "tr" and self.link_row:
            self.link_table.append(self.link_row)
            self.link_row = []
        if self.in_table and tag == "table":
            self.link_cells.append(self.link_table)
            self.link_table = []
        super().handle_endtag(tag)


def integer(value: str | None) -> int:
    match = re.search(r"(?:19|20)\d{2}", value or "")
    return int(match.group()) if match else 0


def clean_name(value: str) -> str:
    value = re.sub(r"\[[^]]*\]", "", value)
    value = re.sub(r"\s*\([^)]*\)", "", value)
    value = value.replace("†", "").strip()
    return re.sub(r"\s+", " ", value)


def match_keys(value: str) -> list[str]:
    """Return full and suffix-free keys without changing public display names."""
    cleaned = clean_name(value)
    keys = [normalize(cleaned)]
    parts = cleaned.split()
    if len(parts) >= 3 and normalize(parts[-1]) in NAME_SUFFIXES:
        keys.append(normalize(" ".join(parts[:-1])))
    return list(dict.fromkeys(key for key in keys if key))


def player_resolver(conn: sqlite3.Connection, sport: str):
    rows = conn.execute(
        "SELECT player_id, display_name, first_name, last_name, debut_year, final_year FROM sport_players WHERE sport_id=?",
        (sport,),
    ).fetchall()
    exact: dict[str, list[tuple]] = defaultdict(list)
    by_last: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        for key in match_keys(row[1]):
            exact[key].append(row)
        by_last[normalize(row[3] or row[1].rsplit(" ", 1)[-1])].append(row)

    def resolve(name: str, season: int | None = None) -> tuple[str | None, str]:
        keys = match_keys(name)
        key = keys[0]
        candidates = []
        for candidate_key in keys:
            candidates.extend(exact.get(candidate_key, []))
        candidates = list(dict.fromkeys(candidates))
        if not candidates and key in NAME_ALIASES:
            candidates = exact.get(normalize(NAME_ALIASES[key]), [])
            if len(candidates) == 1:
                return candidates[0][0], "known_alias"
        if len(candidates) > 1 and season:
            active = [row for row in candidates if (not row[4] or row[4] <= season + 1) and (not row[5] or row[5] >= season - 1)]
            if len(active) == 1:
                return active[0][0], "exact_name_career"
        if len(candidates) == 1:
            return candidates[0][0], "exact_name"
        bits = clean_name(name).split()
        if len(bits) >= 3 and normalize(bits[-1]) in NAME_SUFFIXES:
            bits = bits[:-1]
        if len(bits) >= 2:
            candidates = [row for row in by_last.get(normalize(bits[-1]), []) if normalize(row[2] or row[1])[:1] == normalize(bits[0])[:1]]
            if season:
                active = [row for row in candidates if (not row[4] or row[4] <= season + 1) and (not row[5] or row[5] >= season - 1)]
                if active:
                    candidates = active
            if len(candidates) == 1:
                return candidates[0][0], "last_name_initial_career"
        # Historical hockey sources frequently use a formal given name while
        # the local roster uses a nickname, and newer rows can contain only a
        # surname. A unique surname among players active in the source season
        # is a defensible link; multiple candidates remain unresolved.
        last_candidates = by_last.get(normalize(bits[-1]), [])
        if season:
            last_candidates = [row for row in last_candidates if (not row[4] or row[4] <= season + 1) and (not row[5] or row[5] >= season - 1)]
        if len(last_candidates) == 1:
            return last_candidates[0][0], "unique_last_name_career"

        # The official NHL roster history has incomplete debut/final years for
        # a portion of early players. A globally unique surname is still a
        # safe identity match, while shared surnames remain unresolved.
        all_last_candidates = by_last.get(normalize(bits[-1]), [])
        if len(all_last_candidates) == 1:
            return all_last_candidates[0][0], "unique_last_name"
        return None, "ambiguous_or_missing"

    return resolve


def record(conn: sqlite3.Connection, sport: str, category: str, name: str, season: int, url: str, source: str, resolve) -> bool:
    player_id, reason = resolve(name, season)
    if player_id:
        conn.execute(
            "INSERT OR REPLACE INTO sport_honors VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sport, player_id, category, season, clean_name(name), url, source),
        )
        return True
    conn.execute(
        "INSERT OR REPLACE INTO sport_honor_unresolved VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sport, category, season or None, clean_name(name), url, source, reason),
    )
    return False


def record_known_player(conn: sqlite3.Connection, sport: str, category: str, player_id: str,
                        name: str, season: int, url: str, source: str) -> None:
    """Store a fact supplied with a verified source-specific player ID."""
    conn.execute(
        "INSERT OR REPLACE INTO sport_honors VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sport, player_id, category, season, clean_name(name), url, source),
    )


def wiki_tables(page: str) -> list[list[list[str]]]:
    response = requests.get(WIKI + page, headers=WIKI_HEADERS, timeout=60)
    response.raise_for_status()
    parser = WikiTables()
    parser.feed(response.text)
    return parser.tables


def wiki_linked_tables(page: str) -> list[list[list[list[str]]]]:
    response = requests.get(WIKI + page, headers=WIKI_HEADERS, timeout=60)
    response.raise_for_status()
    parser = WikiLinkedTables()
    parser.feed(response.text)
    return parser.link_cells


def load_nfl_awards(conn: sqlite3.Connection) -> tuple[int, int]:
    resolve = player_resolver(conn, "football")
    conn.execute("DELETE FROM sport_honors WHERE sport_id='football' AND source='wikipedia_nfl_honors'")
    conn.execute("DELETE FROM sport_honor_unresolved WHERE sport_id='football' AND source='wikipedia_nfl_honors'")
    loaded = unresolved = 0
    pages = (("AP_NFL_Most_Valuable_Player", "mvp"),)
    for page, category in pages:
        url = WIKI + page
        for table in wiki_tables(page):
            if not table or len(table[0]) < 2 or table[0][0] != "Season" or table[0][1] != "Player":
                continue
            for row in table[1:]:
                if len(row) < 2 or not integer(row[0]):
                    continue
                if record(conn, "football", category, row[1], integer(row[0]), url, "wikipedia_nfl_honors", resolve):
                    loaded += 1
                else:
                    unresolved += 1
    # The Rookie of the Year page has offensive and defensive tables in order.
    rookie_tables = [t for t in wiki_tables("AP_NFL_Rookie_of_the_Year") if t and t[0][:2] == ["Season", "Player"]]
    for index, table in enumerate(rookie_tables):
        category = "offensive_roty" if index == 0 else "defensive_roty"
        for row in table[1:]:
            if len(row) >= 2 and integer(row[0]):
                if record(conn, "football", category, row[1], integer(row[0]), WIKI + "AP_NFL_Rookie_of_the_Year", "wikipedia_nfl_honors", resolve):
                    loaded += 1
                else:
                    unresolved += 1
    for suffix in PRO_BOWL_PAGES:
        page = f"List_of_Pro_Bowl_players%2C_{suffix}"
        for table in wiki_tables(page):
            if not table or table[0][:3] != ["Name", "Position", "Year(s) selected"]:
                continue
            for row in table[1:]:
                if len(row) < 3:
                    continue
                for season_text in re.findall(r"(?:19|20)\d{2}", row[2]):
                    if record(conn, "football", "pro_bowl", row[0], int(season_text), WIKI + page, "wikipedia_nfl_honors", resolve):
                        loaded += 1
                    else:
                        unresolved += 1
    conn.execute("UPDATE sport_player_traits SET mvp_count=0, roty_count=0, all_star_count=0 WHERE sport_id='football'")
    conn.execute("""UPDATE sport_player_traits SET mvp_count=(SELECT COUNT(*) FROM sport_honors h WHERE h.sport_id='football' AND h.player_id=sport_player_traits.player_id AND h.honor='mvp'),
        roty_count=(SELECT COUNT(*) FROM sport_honors h WHERE h.sport_id='football' AND h.player_id=sport_player_traits.player_id AND h.honor IN ('offensive_roty','defensive_roty')),
        all_star_count=(SELECT COUNT(*) FROM sport_honors h WHERE h.sport_id='football' AND h.player_id=sport_player_traits.player_id AND h.honor='pro_bowl')
        WHERE sport_id='football'""")
    return loaded, unresolved


def load_nfl_all_pro(conn: sqlite3.Connection) -> tuple[int, int]:
    """Load AP first-team selections from the available yearly All-Pro pages."""
    resolve = player_resolver(conn, "football")
    conn.execute("DELETE FROM sport_honors WHERE sport_id='football' AND source='wikipedia_nfl_all_pro'")
    conn.execute("DELETE FROM sport_honor_unresolved WHERE sport_id='football' AND source='wikipedia_nfl_all_pro'")
    loaded = unresolved = 0
    team_names = {normalize(name) for (name,) in conn.execute("SELECT DISTINCT name FROM sport_teams WHERE sport_id='football'")}
    for season in range(1999, 2026):
        page = f"{season}_All-Pro_Team"
        try:
            tables = wiki_linked_tables(page)
        except requests.RequestException:
            continue
        for table in tables:
            if len(table) < 3:
                continue
            for row in table[2:]:
                if len(row) < 2:
                    continue
                # Cells link player then team, repeatedly for tied positions.
                for name in row[1][::2]:
                    if normalize(name) in team_names:
                        continue
                    if record(conn, "football", "all_pro_first_team", name, season, WIKI + page, "wikipedia_nfl_all_pro", resolve):
                        loaded += 1
                    else:
                        unresolved += 1
    return loaded, unresolved


def load_nfl_championships(conn: sqlite3.Connection) -> tuple[int, int]:
    response = requests.get(SUPER_BOWL_URL, timeout=120)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        rows = list(csv.DictReader(io.TextIOWrapper(archive.open(archive.namelist()[0]), encoding="utf-8-sig")))
    team_ids = {normalize(name): team_id for team_id, name in conn.execute("SELECT DISTINCT team_id, name FROM sport_teams WHERE sport_id='football'")}
    team_ids.update({"washington redskins": "WAS", "los angeles raiders": "RAI"})
    champions: dict[int, str] = {}
    for row in rows:
        season = integer(row.get("Date")) - 1
        team_id = team_ids.get(normalize(row.get("Winner") or ""))
        if season and team_id:
            champions[season] = team_id
    counts: dict[str, int] = defaultdict(int)
    for season, team_id in champions.items():
        for (player_id,) in conn.execute("SELECT DISTINCT player_id FROM sport_appearances WHERE sport_id='football' AND season=? AND team_id=?", (season, team_id)):
            counts[player_id] += 1
    conn.execute("UPDATE sport_player_traits SET championship_count=0 WHERE sport_id='football'")
    conn.executemany("UPDATE sport_player_traits SET championship_count=? WHERE sport_id='football' AND player_id=?", [(count, player_id) for player_id, count in counts.items()])
    return len(champions), len(counts)


def load_nhl_pre1986_championships(conn: sqlite3.Connection) -> tuple[int, int]:
    # Championship totals are reset and rebuilt in load_local_sport_traits.py.
    # Keeping this legacy refresh non-additive prevents totals from growing each
    # time honors history is loaded.
    return 0, 0


def audit_existing_gaps(conn: sqlite3.Connection) -> dict[str, int]:
    """Persist the source rows still unmatched by the traits loader."""
    conn.execute("""DELETE FROM sport_honors WHERE source IN ('nba_award_audit', 'hockeydb_award_audit', 'kaggle_nhl_stat_audit')""")
    conn.execute("""DELETE FROM sport_honor_unresolved WHERE source IN ('nba_award_audit', 'hockeydb_award_audit', 'kaggle_nhl_stat_audit')""")
    resolve_nba = player_resolver(conn, "basketball")
    resolve_nhl = player_resolver(conn, "hockey")
    totals: dict[str, int] = defaultdict(int)
    nba = requests.get(NBA_AWARDS_URL, timeout=120); nba.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(nba.content)) as archive:
        for row in csv.DictReader(io.TextIOWrapper(archive.open("Player Award Shares.csv"), encoding="utf-8-sig")):
            # The source contains NBA, BAA, and ABA history. NBA gameplay
            # includes the BAA predecessor but excludes the separate ABA.
            is_aba = (row.get("award") or "").strip().lower().startswith("aba ")
            if not is_aba and (row.get("winner") or "").upper() == "TRUE" and not record(conn, "basketball", "nba_award_source", row.get("player") or "", integer(row.get("season")), NBA_AWARDS_URL, "nba_award_audit", resolve_nba):
                totals["nba_awards"] += 1
        for row in csv.DictReader(io.TextIOWrapper(archive.open("All-Star Selections.csv"), encoding="utf-8-sig")):
            if (row.get("lg") or "").upper() in {"NBA", "BAA"} and not record(conn, "basketball", "nba_all_star_source", row.get("player") or "", integer(row.get("season")), NBA_AWARDS_URL, "nba_award_audit", resolve_nba):
                totals["nba_all_star"] += 1
    master = {row.get("playerID"): row for row in csv.DictReader(io.StringIO(requests.get(HOCKEYDB_MASTER_URL, timeout=90).text))}
    hdb_ids: dict[str, str] = {}
    try:
        hdb_ids = {
            external_id: player_id for external_id, player_id in conn.execute(
                """SELECT external_id, player_id FROM sport_player_external_ids
                   WHERE sport_id='hockey' AND source='hockeydb'"""
            )
        }
    except sqlite3.OperationalError:
        # The supplemental HockeyDB identity loader is optional but should run
        # before this script when historical NHL awards are required.
        pass
    for row in csv.DictReader(io.StringIO(requests.get(HOCKEYDB_AWARDS_URL, timeout=90).text)):
        player = master.get(row.get("playerID"), {})
        name = f"{player.get('nameGiven', '')} {player.get('lastName', '')}".strip()
        player_id = hdb_ids.get(row.get("playerID") or "")
        if player_id:
            record_known_player(conn, "hockey", "nhl_award_source", player_id, name, integer(row.get("year")), HOCKEYDB_AWARDS_URL, "hockeydb_award_audit")
        elif name and not record(conn, "hockey", "nhl_award_source", name, integer(row.get("year")), HOCKEYDB_AWARDS_URL, "hockeydb_award_audit", resolve_nhl):
            totals["nhl_awards"] += 1
    if NHL_STATS_CACHE.exists():
        with zipfile.ZipFile(NHL_STATS_CACHE) as archive:
            for filename in (name for name in archive.namelist() if name.lower().endswith(".csv")):
                with archive.open(filename) as raw:
                    for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")):
                        name = (row.get("name") or row.get("player") or "").strip()
                        # This source records the ending calendar year of an
                        # NHL season. TeamMateTag uses the starting year, so a
                        # 2024-25 debut is stored as 2024 here.
                        first_year = integer(row.get("first"))
                        first_season = first_year - 1 if first_year else 0
                        if name and not record(conn, "hockey", "nhl_career_stat_source", name, first_season, NHL_STATS_CACHE.as_uri(), "kaggle_nhl_stat_audit", resolve_nhl):
                            totals["nhl_career_stats"] += 1
    return totals


def main() -> None:
    conn = sqlite3.connect(DATABASE)
    try:
        conn.executescript(SCHEMA)
        nfl_honors, nfl_unresolved = load_nfl_awards(conn)
        all_pro, all_pro_unresolved = load_nfl_all_pro(conn)
        nfl_champions, nfl_players = load_nfl_championships(conn)
        # Championship totals are rebuilt from the complete champion-season
        # map by load_local_sport_traits.py. Do not add pre-1986 seasons here:
        # repeated honors refreshes would otherwise inflate player totals.
        nhl_champions, nhl_players = 0, 0
        audits = audit_existing_gaps(conn)
        conn.commit()
    finally:
        conn.close()
    print(f"NFL honors: {nfl_honors}; unresolved: {nfl_unresolved}")
    print(f"NFL AP first-team All-Pro: {all_pro}; unresolved: {all_pro_unresolved}")
    print(f"NFL Super Bowl seasons: {nfl_champions}; rostered players credited: {nfl_players}")
    print(f"NHL Cup totals are maintained by load_local_sport_traits.py; additive rows: {nhl_champions}.")
    print(f"Unresolved audit rows: {dict(audits)}")


if __name__ == "__main__":
    main()
