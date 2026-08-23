# Film Review Lineups

Daily Film Review decks are deterministic sport-specific chains. Every adjacent
pair needs a unique canonical team-season link. A team-season cannot be reused
elsewhere in the same deck.

| Sport | Cards | Required lineup slots |
| --- | ---: | --- |
| Baseball | 12 | C, 1B, 2B, 3B, SS, LF, CF, RF, DH, SP, RP, CP |
| Football | 12 | Choose offense: QB, RB, 3 WR, TE, 5 OL, K; or defense: 4 DL, 3 LB, 2 CB, 2 S, P |
| Hockey | 11 | 2 LW, 2 C, 2 RW, 4 D, G |
| Basketball | 12 | 2 PG, 2 SG, 2 SF, 2 PF, 2 C, 2 random |

Basketball uses exact PG/SG/SF/PF/C eligibility from Wikidata's NBA.com player
ID and position-played properties. The game-by-game NBA source supplies the
teammate data; the position source only supplies roster eligibility. For
players with multiple career roles, it must not be interpreted as a count of
minutes or starts at each position.
