"""
vocabulary.py
=============
DIVISION VOCABULARY + NUMBERING SYSTEMS (deterministic, multilingual).

This module removes the "everything is a chapter" assumption. It knows a lexicon
of division labels across English/French/German and can parse every numbering
system the discovery engine must support. Nothing here is hard-coded to
"chapter"; "chapter" is just one entry in the lexicon.

Public API
  RANKS                       canonical partial order (smaller = higher in tree)
  MATTER                      set of front/back-matter canonical types
  parse_number(token) -> (system, value) | None
  match_division(text) -> dict | None
      {type, number, system, role, surface}
      role ∈ {labeled, ordinal_label, named, matter, bare}
"""

from __future__ import annotations
import re
import unicodedata

# ---- canonical division types and their surface forms (EN / FR / DE) --------
LEXICON = {
    "volume":   ["volume", "tome", "band", "vol"],
    "book":     ["book", "livre", "buch"],
    "part":     ["part", "partie", "teil"],
    "act":      ["act", "acte", "akt", "aufzug"],
    "day":      ["day", "journee", "tag"],
    "chapter":  ["chapter", "chapitre", "kapitel", "chap"],
    "letter":   ["letter", "lettre", "brief"],
    "story":    ["story", "conte", "geschichte", "novella", "tale"],
    "night":    ["night", "nuit", "nacht"],
    "canto":    ["canto", "gesang"],
    "lecture":  ["lecture", "vorlesung"],
    "lesson":   ["lesson", "lecon", "lektion"],
    "essay":    ["essay", "essai"],
    "paper":    ["paper"],
    "work":     ["work", "werk", "oeuvre"],
    "scene":    ["scene", "szene", "auftritt"],
    "section":  ["section", "abschnitt"],
    "subsection": ["subsection"],
    # front / back matter
    "preface":      ["preface", "vorwort", "avant-propos"],
    "introduction": ["introduction", "einleitung"],
    "prologue":     ["prologue", "prolog"],
    "epilogue":     ["epilogue", "epilog"],
    "interlude":    ["interlude", "zwischenspiel"],
    "appendix":     ["appendix", "appendice", "anhang"],
    "foreword":     ["foreword"],
    "afterword":    ["afterword", "nachwort"],
    "conclusion":   ["conclusion", "schluss"],
    "contents":     ["contents", "sommaire", "inhalt", "inhaltsverzeichnis",
                     "table des matieres"],
}

# canonical partial order (containers small, serial leaves large)
RANKS = {
    "volume": 0, "book": 1,
    "part": 2, "act": 2, "day": 2,
    "chapter": 3, "letter": 3, "story": 3, "canto": 3, "night": 3,
    "essay": 3, "lecture": 3, "lesson": 3, "paper": 3, "work": 3,
    "scene": 4, "section": 4,
    "subsection": 5,
}

MATTER = {"preface", "introduction", "prologue", "epilogue", "interlude",
          "appendix", "foreword", "afterword", "conclusion", "contents"}

# reverse map: surface form -> canonical type
_SURFACE = {}
for _canon, _forms in LEXICON.items():
    for _f in _forms:
        _SURFACE[_f] = _canon


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def _norm_token(tok: str) -> str:
    return _strip_accents(tok).lower().strip(".:);,-\u2013\u2014")


# ---- numbering systems ------------------------------------------------------
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_FR_ORD = re.compile(r"^(\d+)(?:er|ere|re|eme|e|nde?|ieme)$")
_EN_ORD = re.compile(r"^(\d+)(?:st|nd|rd|th)$")

ORDINAL_WORDS = {
    # English
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "twentieth": 20,
    # French (accents stripped)
    "premier": 1, "premiere": 1, "deuxieme": 2, "seconde": 2, "second": 2,
    "troisieme": 3, "quatrieme": 4, "cinquieme": 5, "sixieme": 6,
    "septieme": 7, "huitieme": 8, "neuvieme": 9, "dixieme": 10, "onzieme": 11,
    "douzieme": 12,
}

# Cardinal words used as labels' numbers (e.g. "Volume Five").
CARDINAL_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10,
    "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}

# German ordinal STEMS (declension-independent): erste/erster/erstes/ersten...
_DE_ORD_STEM = {
    "erst": 1, "zweit": 2, "dritt": 3, "viert": 4, "funft": 5, "sechst": 6,
    "siebt": 7, "siebent": 7, "acht": 8, "neunt": 9, "zehnt": 10, "elft": 11,
    "zwolft": 12,
}


def _german_ordinal(low: str):
    for suf in ("es", "er", "en", "em", "e"):
        if low.endswith(suf):
            stem = low[:-len(suf)]
            if stem in _DE_ORD_STEM:
                return _DE_ORD_STEM[stem]
    return _DE_ORD_STEM.get(low)


def _roman_to_int(s: str):
    s = s.lower()
    if not s or any(c not in _ROMAN for c in s):
        return None
    total, prev = 0, 0
    for c in reversed(s):
        v = _ROMAN[c]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def parse_number(token: str):
    """Return (system, value) or None. Tries localized, arabic, roman, ordinal
    word, single-letter alpha — in that order (most specific first)."""
    raw = (token or "").strip().strip(".:);,-\u2013\u2014")
    if not raw:
        return None
    low = _strip_accents(raw).lower()

    m = _FR_ORD.match(low)
    if m:
        return ("fr_ordinal", int(m.group(1)))
    m = _EN_ORD.match(low)
    if m:
        return ("en_ordinal", int(m.group(1)))
    if low.isdigit():
        return ("arabic", int(low)) if int(low) >= 1 else None
    r = _roman_to_int(low)
    if r is not None:
        return ("roman", r)
    if low in ORDINAL_WORDS:
        return ("ordinal_word", ORDINAL_WORDS[low])
    if low in CARDINAL_WORDS:
        return ("cardinal_word", CARDINAL_WORDS[low])
    g = _german_ordinal(low)
    if g is not None:
        return ("de_ordinal", g)
    if len(low) == 1 and low.isalpha():
        return ("alpha", ord(low) - ord("a") + 1)
    return None


# ---- the master matcher -----------------------------------------------------
def match_division(text: str):
    """Classify a heading line into a division. Returns a dict or None.

    Handles:  <label> <number>      e.g. Chapter IV / Buch I / Letter 7
              <number/ordinal> <label>  e.g. 1ere nuit / Premiere Partie
              <label>              e.g. Prologue / Inhaltsverzeichnis (matter)
              <named label> ...    e.g. Story of the Trader and the Jinni
              <bare number>        e.g. I. / II. / A.   (unknown vocabulary)
    """
    t = (text or "").strip()
    if not t:
        return None
    tokens = t.split()
    if not tokens:
        return None

    first = _norm_token(tokens[0])
    second = _norm_token(tokens[1]) if len(tokens) > 1 else ""
    lead_punct = bool(re.search(r"[.)\]:]$", tokens[0]))   # "A." / "1)" / "I:"

    # multi-word matter label (e.g. "table des matieres")
    joined3 = " ".join(_norm_token(x) for x in tokens[:3])
    for canon in ("contents",):
        for form in LEXICON[canon]:
            if joined3.startswith(form):
                return {"type": canon, "number": None, "system": None,
                        "role": "matter", "surface": form}

    # 1) <label> <number?>   (labeled / named)
    if first in _SURFACE:
        canon = _SURFACE[first]
        num = parse_number(tokens[1]) if len(tokens) > 1 else None
        if canon in MATTER:
            return {"type": canon, "number": None, "system": None,
                    "role": "matter", "surface": first}
        if num:
            return {"type": canon, "number": num[1], "system": num[0],
                    "role": "labeled", "surface": first}
        # label present but no parsable number -> a NAMED division
        return {"type": canon, "number": None, "system": None,
                "role": "named", "surface": first}

    # 2) <number/ordinal> <label>   (1ere nuit / Premiere Partie / a. ... Story)
    n0 = parse_number(tokens[0])
    # An ambiguous single-letter alpha or bare roman lead ("A", "I") only counts
    # as a number when punctuated ("A.", "I)") -- this avoids matching English
    # article "A" / pronoun "I" in lines like "A SIGNET BOOK".
    ambiguous_lead = n0 and n0[0] in ("alpha", "roman") and not lead_punct
    if n0 and not ambiguous_lead:
        for tok in tokens[1:3]:                       # label as 2nd/3rd token
            canon = _SURFACE.get(_norm_token(tok))
            if canon:
                return {"type": canon, "number": n0[1], "system": n0[0],
                        "role": "ordinal_label", "surface": _norm_token(tok)}
        for tok in tokens[1:]:                         # label later in the line
            canon = _SURFACE.get(_norm_token(tok))
            if canon and canon not in MATTER:
                return {"type": canon, "number": n0[1], "system": n0[0],
                        "role": "ordinal_label", "surface": canon}
        if len(tokens) <= 2:                           # bare number line
            return {"type": None, "number": n0[1], "system": n0[0],
                    "role": "bare", "surface": tokens[0]}
    return None