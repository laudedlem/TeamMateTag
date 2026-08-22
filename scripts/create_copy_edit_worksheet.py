from __future__ import annotations

import html
import os
import zipfile
from datetime import date
from pathlib import Path


OUT = Path("docs/TeamMateTag_Copy_Edit_Worksheet.docx")


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def p(text: str = "", style: str | None = None) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{style_xml}<w:r><w:t xml:space=\"preserve\">{esc(text)}</w:t></w:r></w:p>"


def cell(text: str, width: int, fill: str | None = None, bold: bool = False) -> str:
    fill_xml = f'<w:shd w:fill="{fill}"/>' if fill else ""
    bold_xml = "<w:b/>" if bold else ""
    lines = str(text).split("\n") or [""]
    paras = []
    for line in lines:
        paras.append(
            "<w:p><w:r>"
            f"<w:rPr>{bold_xml}</w:rPr>"
            f"<w:t xml:space=\"preserve\">{esc(line)}</w:t>"
            "</w:r></w:p>"
        )
    return (
        "<w:tc>"
        f"<w:tcPr><w:tcW w:w=\"{width}\" w:type=\"dxa\"/>{fill_xml}"
        "<w:tcMar><w:top w:w=\"90\" w:type=\"dxa\"/><w:bottom w:w=\"90\" w:type=\"dxa\"/>"
        "<w:start w:w=\"110\" w:type=\"dxa\"/><w:end w:w=\"110\" w:type=\"dxa\"/></w:tcMar></w:tcPr>"
        + "".join(paras)
        + "</w:tc>"
    )


def table(headers: list[str], rows: list[list[str]], widths: list[int]) -> str:
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    border = (
        '<w:tblBorders><w:top w:val="single" w:sz="6" w:color="DADCE0"/>'
        '<w:left w:val="single" w:sz="6" w:color="DADCE0"/>'
        '<w:bottom w:val="single" w:sz="6" w:color="DADCE0"/>'
        '<w:right w:val="single" w:sz="6" w:color="DADCE0"/>'
        '<w:insideH w:val="single" w:sz="6" w:color="DADCE0"/>'
        '<w:insideV w:val="single" w:sz="6" w:color="DADCE0"/></w:tblBorders>'
    )
    xml = [
        "<w:tbl>",
        f'<w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/>{border}<w:tblLook w:firstRow="1"/></w:tblPr>',
        f"<w:tblGrid>{grid}</w:tblGrid>",
        "<w:tr>" + "".join(cell(h, widths[i], "E8EEF5", True) for i, h in enumerate(headers)) + "</w:tr>",
    ]
    for row in rows:
        padded = row + [""] * (len(headers) - len(row))
        xml.append("<w:tr>" + "".join(cell(padded[i], widths[i]) for i in range(len(headers))) + "</w:tr>")
    xml.append("</w:tbl>")
    return "".join(xml)


rules_modes = [
    ["RULES.MODE.MANAGER", "Manager Mode", "Starting with the Leadoff Player, name TeamMates of the Top Player until time runs out.", "Goal: set your longest lineup.", ""],
    ["RULES.MODE.FILM", "Film Review", "Build your daily Lineup by naming the team and season two TeamMates played together.", "Goal: complete every TeamMate Link in the Lineup.", ""],
    ["RULES.MODE.DIVISION", "Division Rivalry", "Head-to-head, back-and-forth naming TeamMates of the Top Player and avoiding Struck Out Team-Seasons.", "Goal: outlast your opponent.", ""],
    ["RULES.MODE.PLAYOFFS", "Playoffs", "Division Rivalry with Powerups and Win Conditions.", "Goal: complete your Win Condition before your opponent, or outlast them.", ""],
]

rules_vocab = [
    ["RULES.VOCAB.LINEUP", "Lineup", "The player chain. Example: Anthony Rizzo -> Kris Bryant -> Javier Baez.", ""],
    ["RULES.VOCAB.TEAMMATE_LINK", "Teammate Link", "Two players connect if they ever played together. Example: Rizzo and Bryant connect through the 2016 Cubs.", ""],
    ["RULES.VOCAB.TEAM_SEASON", "Team-Season", "One team in one season. Example: 2016 Cubs or 2019-20 Lakers.", ""],
    ["RULES.VOCAB.TEAM_STRIKES", "Team Strikes", "Each used Team-Season gets a strike. Three strikes means that team is out.", ""],
    ["RULES.VOCAB.BLOCKED_GUESS", "Blocked Guess", "If a player only links through an out team, the guess does not count.", ""],
    ["RULES.VOCAB.POWERUP", "Powerup", "A one-use Playoffs move. Some let you play a same-franchise player instead of a direct teammate.", ""],
    ["RULES.VOCAB.WIN_CONDITION", "Win Condition", "Your Playoffs target. Complete it first and the game ends.", ""],
]

sport_terms = [
    ["RULES.TERM.STARTER", "Leadoff", "Tipoff", "Snapper", "Faceoff", "The First Player in a Lineup.", ""],
    ["RULES.TERM.OUT_TEAM", "Struck Out", "Fouled Out", "Punted", "Game Misconduct", "The Same Team-Season used 3 Times. TeamMates from that Team-Season can no longer be used.", ""],
    ["RULES.TERM.FR_HIT", "Hit", "Bucket", "Completion", "Goal", "Correct Film Review Link.", ""],
    ["RULES.TERM.FR_FOUL", "Foul", "Rim Out", "Tipped Pass", "Offside", "Correct Film Review Team OR Year. 2 in a row is a Miss.", ""],
    ["RULES.TERM.FR_STRIKE", "Strike", "Foul", "Turnover", "Penalty", "Missed Film Review Link. 3 Misses and you're Benched.", ""],
]

ref_general = [
    ["REF.INTRO.MODE_HUB", "Mode hub Ref intro", "Playoffs adds powerups and win conditions to the head-to-head lineup game.", ""],
    ["REF.INTRO.SPORT_PAGE", "Sport page Ref intro", "Each Playoffs Game gives both Players 1 use of every Powerup. You can activate Powerups on your turn.", ""],
    ["REF.INTRO.IN_GAME", "In-game Ref intro", "Each Playoffs game gives both players one use of every powerup. You can activate one powerup on a turn.", ""],
]

condition_rows = [
    ["REF.WIN.AWARD_CIRCLES", "Award Circles", "Name players with the listed major award.", ""],
    ["REF.WIN.CAREER_MILESTONES", "Career Milestones", "Name players who reached the listed career stat threshold.", ""],
    ["REF.WIN.PEAK_SEASONS", "Peak Seasons", "Name players with a qualifying single-season feat.", ""],
    ["REF.WIN.ROSTER_PATHS", "Roster Paths", "Name one-franchise players or well-traveled players.", ""],
    ["REF.WIN.RING_CHASER", "Ring Chaser", "Build the required combined championship total.", ""],
]

powerups = {
    "Baseball": [
        ("Bubblegum", "Play a same-franchise 40 home run season batter. Team Strikes still apply. Adds 5 seconds."),
        ("Pine Tar", "Play a same-franchise 200 strikeout season pitcher. Team Strikes still apply. Adds 5 seconds."),
        ("Bat Donut", "Play a same-franchise Silver Slugger winner. Team Strikes still apply. Adds 5 seconds."),
        ("Sunglasses", "Play a same-franchise All-Star. Team Strikes still apply. Adds 5 seconds."),
        ("Backup Mitt", "Play a same-franchise Gold Glove winner. Team Strikes still apply. Adds 5 seconds."),
        ("ABS", "Add 15 seconds to your current turn."),
        ("Quick Pitch", "Limit your opponent to 10 seconds on their next turn."),
    ],
    "Basketball": [
        ("Heat Check", "Play a same-franchise 2,000-point season scorer. Team Strikes still apply. Adds 5 seconds."),
        ("Sixth Man", "Play a same-franchise 7,000-assist player. Team Strikes still apply. Adds 5 seconds."),
        ("Switch", "Play a same-franchise same-position-group player. Team Strikes still apply. Adds 5 seconds."),
        ("MVP Badge", "Play a same-franchise MVP winner. Team Strikes still apply. Adds 5 seconds."),
        ("All-Star Call-Up", "Play a same-franchise All-Star. Team Strikes still apply. Adds 5 seconds."),
        ("Timeout", "Add 15 seconds to your current turn."),
        ("Full-Court Press", "Limit your opponent to 10 seconds on their next turn."),
    ],
    "Football": [
        ("Trick Play", "Play a same-franchise 20-touchdown scorer. Team Strikes still apply. Adds 5 seconds."),
        ("Iron Man", "Play a same-franchise 100-game veteran. Team Strikes still apply. Adds 5 seconds."),
        ("Package Change", "Play a same-franchise same-unit player. Team Strikes still apply. Adds 5 seconds."),
        ("MVP Badge", "Play a same-franchise MVP winner. Team Strikes still apply. Adds 5 seconds."),
        ("Pro Bowl Call-Up", "Play a same-franchise Pro Bowl player. Team Strikes still apply. Adds 5 seconds."),
        ("Timeout", "Add 15 seconds to your current turn."),
        ("Blitz", "Limit your opponent to 10 seconds on their next turn."),
    ],
    "Hockey": [
        ("Breakaway", "Play a same-franchise 250-goal scorer. Team Strikes still apply. Adds 5 seconds."),
        ("Veteran Presence", "Play a same-franchise 500-point scorer. Team Strikes still apply. Adds 5 seconds."),
        ("Line Change", "Play a same-franchise same-position-group player. Team Strikes still apply. Adds 5 seconds."),
        ("Hart Honor", "Play a same-franchise Hart Trophy winner. Team Strikes still apply. Adds 5 seconds."),
        ("All-Star Call-Up", "Play a same-franchise All-Star. Team Strikes still apply. Adds 5 seconds."),
        ("Timeout", "Add 15 seconds to your current turn."),
        ("Forecheck", "Limit your opponent to 10 seconds on their next turn."),
    ],
}

condition_options = {
    "Baseball": ["Random", "Sunset Kingdom", "Havana Heat", "Maple Corridor", "MVP Circle", "Young Buck", "Gonna Be Golden", "Secretariat", "Hound-dog", "Great Bambinos", "Ring Chaser", "Journeyman"],
    "Basketball": ["Random", "Bucket Getter", "Scoring Run", "Table Setter", "Deep Range", "Ironhorse", "Home Court", "Frequent Flyer", "MVP Circle", "All-Star Marathon", "Ring Chaser", "Young Guns"],
    "Football": ["Random", "End Zone", "Season Scorer", "Air Raid", "Sunday Slingers", "Sack Master", "Ballhawk", "One Club", "Journeyman", "MVP Circle", "Pro Bowl Marathon", "Ring Chaser", "Fresh Faces"],
    "Hockey": ["Random", "Sniper", "Rocket Season", "Playmaker", "Point Machine", "Lifer", "Journeyman", "Hart Club", "All-Star Marathon", "Ironhorse", "Cup Chasers", "Fresh Ice"],
}


def build_document() -> str:
    body = []
    body.append(p("TeamMateTag Copy Editing Worksheet", "Title"))
    body.append(p(f"Generated {date.today().isoformat()} from the current local site copy. Edit the rightmost column, then send this file back so the changes can be mapped into the app."))
    body.append(p("How to Play (?)", "Heading1"))
    body.append(p("Game Modes", "Heading2"))
    body.append(table(["ID", "Game Mode", "Current Text", "Current Goal/Win Text", "Your Edit"], rules_modes, [1700, 1550, 2850, 2250, 3350]))
    body.append(p("Vocabulary", "Heading2"))
    body.append(table(["ID", "Term", "Current Definition", "Your Edit"], rules_vocab, [2100, 1700, 4500, 3700]))
    body.append(p("Sport Terms", "Heading2"))
    body.append(table(["ID", "Baseball", "Basketball", "Football", "Hockey", "Meaning", "Your Edit"], sport_terms, [1600, 1200, 1200, 1200, 1400, 2700, 2500]))
    body.append(p("Playoffs Reference (Ref)", "Heading1"))
    body.append(p("General Copy", "Heading2"))
    body.append(table(["ID", "Where It Appears", "Current Text", "Your Edit"], ref_general, [2100, 2300, 5000, 3500]))
    body.append(p("Win Condition Category Table", "Heading2"))
    body.append(table(["ID", "Type", "Current Text", "Your Edit"], condition_rows, [2200, 2400, 4600, 3500]))
    body.append(p("Powerups", "Heading2"))
    for sport, rows in powerups.items():
        body.append(p(f"{sport} Powerups", "Heading3"))
        body.append(table(["ID", "Powerup", "Current Text", "Your Edit"], [[f"REF.POWERUP.{sport.upper()}.{name.upper().replace(' ', '_').replace('-', '_')}", name, desc, ""] for name, desc in rows], [2700, 2000, 4800, 3500]))
    body.append(p("Win Condition Dropdown Names", "Heading2"))
    body.append(p("These names appear in Playoffs win-condition selectors. Edit only if you want the labels renamed."))
    for sport, rows in condition_options.items():
        body.append(p(f"{sport} Win Conditions", "Heading3"))
        body.append(table(["ID", "Current Label", "Your Edit"], [[f"REF.CONDITION.{sport.upper()}.{name.upper().replace(' ', '_').replace('-', '_')}", name, ""] for name in rows], [3900, 3900, 3900]))
    body.append(
        '<w:sectPr><w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/>'
        '<w:pgMar w:top="900" w:right="720" w:bottom="900" w:left="720" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
    )
    return "".join(body)


def write_docx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{build_document()}</w:body></w:document>"
    )
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="20"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="180"/></w:pPr><w:rPr><w:b/><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="38"/><w:color w:val="0B2545"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="300" w:after="100"/></w:pPr><w:rPr><w:b/><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="28"/><w:color w:val="2E74B5"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="220" w:after="80"/></w:pPr><w:rPr><w:b/><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="24"/><w:color w:val="1F4D78"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="180" w:after="60"/></w:pPr><w:rPr><w:b/><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="22"/><w:color w:val="434343"/></w:rPr></w:style>
</w:styles>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    doc_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/document.xml", document_xml)
        z.writestr("word/styles.xml", styles_xml)


if __name__ == "__main__":
    write_docx(OUT)
    print(os.path.abspath(OUT))
