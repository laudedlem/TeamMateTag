"""
name_normalize.py — turn human-typed names into a search-friendly form.

The same normalization runs (a) when we build the search index, on every
player name and nickname, and (b) when the user types a query. As long as
both sides use this function, prefix matching works regardless of accents,
periods, suffixes, or capitalization.

Examples (lowercased throughout):
    "José Bautista"        -> "jose bautista"
    "J.D. Drew"            -> "jd drew"
    "C.C. Sabathia"        -> "cc sabathia"
    "Cal Ripken Jr."       -> "cal ripken jr"
    "Vladimir Guerrero Jr."-> "vladimir guerrero jr"     (NOTE: distinct from Sr.)
    "Ichiro Suzuki"        -> "ichiro suzuki"
    "Big Papi"             -> "big papi"
"""
import re
import unicodedata


def normalize(s: str) -> str:
    if not s:
        return ""
    # NFD splits accented chars into base + combining mark; we drop the mark.
    nfd = unicodedata.normalize("NFD", s)
    no_accents = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    # Drop punctuation entirely (periods in J.D., apostrophes in O'Connor, etc.).
    # Keep word chars and spaces.
    cleaned = re.sub(r"[^\w\s]", "", no_accents, flags=re.UNICODE)
    # Collapse whitespace, lowercase.
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def first_last(first: str | None, last: str | None) -> str:
    return normalize(f"{first or ''} {last or ''}")


# Self-test when invoked directly.
if __name__ == "__main__":
    cases = [
        ("José Bautista",         "jose bautista"),
        ("J.D. Drew",             "jd drew"),
        ("C.C. Sabathia",         "cc sabathia"),
        ("Cal Ripken Jr.",        "cal ripken jr"),
        ("Vladimir Guerrero Jr.", "vladimir guerrero jr"),
        ("Ichiro Suzuki",         "ichiro suzuki"),
        ("Big Papi",              "big papi"),
        ("Adrián Béltre",         "adrian beltre"),
        ("D'Angelo Jiménez",      "dangelo jimenez"),
    ]
    failures = 0
    for inp, expected in cases:
        got = normalize(inp)
        ok = got == expected
        print(f"  {'OK ' if ok else 'BAD'}  {inp!r:30s} -> {got!r}")
        if not ok:
            failures += 1
            print(f"        expected {expected!r}")
    raise SystemExit(failures)
