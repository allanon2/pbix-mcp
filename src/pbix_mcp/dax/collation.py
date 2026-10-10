"""Power BI Desktop's text collation: the order its engine sorts text in, the
comparison DAX's text operators make, and the matching SEARCH / FIND /
CONTAINSSTRING(EXACT) / SUBSTITUTE do (issues #156, #157, #180, #185, #198).

Clean-room derivation (issue #198; the record is
docs/reverse-engineering/experiments/2026-10-19-clean-room-collation.md):

- The weights are Microsoft's published "Windows Server 2008 Sorting Weight
  Table" (SORTKEY DEFAULT, EXPANSION, COMPATIBILITY), compiled into
  _collation_data by the experiment's gen_collation_data.py.
- The way they combine is [MS-UCODEREF] 3.1.5.2.4 GetWindowsSortKey: per
  UTF-16 code unit a script member (SM) and alphabetic weight (AW) -- the
  primary level --, a diacritic weight (DW) to which the nonspacing marks
  after it add, a case weight (CW); "punctuation" (SM 6: hyphens,
  apostrophes, controls) weighs only by position, after everything else
  (the word sort); unsortable characters weigh nothing. Characters beyond
  the BMP weigh by their two surrogates, as the table lists them.
- Where Desktop's answers differ from that, the rule here follows Desktop's
  black-box answers (tests/test_issue157 / 180 / 185, batteries b156-b190)
  and says so with the evidence.
"""
from __future__ import annotations

import lzma
from functools import lru_cache

# [MS-UCODEREF] 3.1.5.2.4 script member values
_UNSORTABLE = 0
_NONSPACE_MARK = 1
_EXPANSION = 2
_EASTASIA_SPECIAL = 3
_JAMO_SPECIAL = 4
_EXTENSION_A = 5
_PUNCTUATION = 6
_KANA = 34
_SCRIPT_MEMBER_EXT_A = 254
_PRIMARY_WEIGHT_EXT_A = 255
_MAP_INVALID_WEIGHT = 0xFF
_MAX_SPECIAL_PW = 1          # PW_REPEAT 0, PW_CHO_ON 1
_MIN_DW = 2
_MIN_CW = 2

# The case weight's bits ([MS-UCODEREF] 3.1.5.2.4): bit 0 width, bits 1-2
# small kana / sei-on, bits 3-4 upper / lower case, bit 5 kana.
_CW_WIDTH = 0x01
_CW_KANA = 0x20
# Desktop's text comparison is case-, kana-type- and width-insensitive and
# accent-sensitive. For the scripts up to Coptic (SM 7-20: symbols, digits,
# Latin, Greek, Cyrillic, Armenian, Georgian, Glagolitic, Coptic) the case
# weight plays no part at all: "a" = "A", "2" = "²" (CW 14),
# "A" = "ᴀ" (CW 26), "1" = "①" -- 3,052 equal pairs of b181 /
# b185 / b187 that differ only in CW, none apart. For every other script it
# drops only its width and kana bits and keeps the rest: Hebrew final letters
# (CW 2 / 18) and Tibetan retroflex letters stay apart, as do U+05D0 / U+2135
# (CW 2 / 6), small kana (bit 1), jamo leading / trailing (CW 2 / 4);
# hiragana = katakana = halfwidth katakana (bits 5, 0), U+FB21 = U+05D0
# (CW 3 / 2) -- every such pair in the batteries.
_CW_IGNORED_UP_TO_SM = 20
_CW_KEEP = 0xFF & ~(_CW_WIDTH | _CW_KANA)

_TABLE: bytes = b""
_EXPANSIONS: dict = {}
_UNITS: dict = {}
_KEYS: dict = {}
_KEYS_MAX = 200_000


def _load() -> None:
    global _TABLE, _EXPANSIONS
    if _TABLE:
        return
    from pbix_mcp.dax import _collation_data as data
    _TABLE = lzma.decompress(bytes.fromhex("".join(data.WEIGHTS)))
    _EXPANSIONS = data.EXPANSIONS


# ---- corrections Desktop's answers call for -----------------------------------

# Arabic harakat weigh, in Desktop, as the combining marks U+0349-U+0357 do in
# the table: b187 finds each of these pairs equal (U+0352 = U+064C, ...,
# U+0357 = U+0651, U+0349 = U+0670), and the Thaana vowel signs equal the
# ligatures of two harakat only with these weights (U+07AB = U+FC5E:
# 86 + 91 = 177, U+07AC = U+FC5F, ..., U+1361 = U+FC63: 91 + 92 = 183).
# U+064B (fathatan) and U+0652 (sukun) are not in any such pair: they keep the
# table's weights: probe Q3a finds U+064B <> U+0351, Q3b U+0652 = U+20D5
# (226 in the table). Q3c1 / Q3c2 confirm the rest after a letter.
_DW_AS = {0x064C: 0x0352, 0x064D: 0x0353, 0x064E: 0x0354, 0x064F: 0x0355,
          0x0650: 0x0356, 0x0651: 0x0357, 0x0670: 0x0349}
# U+FB26 HEBREW LETTER WIDE FINAL MEM: the table gives CW 19; Desktop finds it
# equal to U+05DD (CW 2) and apart from U+05DE (CW 18), as the other wide
# letters U+FB21-U+FB28 (CW 3) are equal to theirs (b181, b187; probes Q5a,
# Q5b).
_CW_AS = {0xFB26: 3}
# U+FDC5 ARABIC LIGATURE SAD WITH MEEM WITH MEEM INITIAL FORM: the table
# expands it to U+FCB1 (sad with hah) + meem, against its own name; Desktop
# finds it equal to U+FD66, the final form of the same three letters (b187),
# and to sad meem meem, not sad hah meem (probes Q4a, Q4b).
_EXPANSION_AS = {0xFDC5: 0xFD66}


def _raw(cp: int) -> tuple:
    i = 4 * cp
    return tuple(_TABLE[i:i + 4])


def _records(cp: int, depth: int = 0) -> list:
    """The table records a code unit weighs as: one, or the records of its
    expansion ([MS-UCODEREF] 3.1.5.2.10: an expansion's second character may
    expand once more, as U+FB03 does)."""
    sm, aw, dw, cw = _raw(cp)
    if sm == _EXPANSION and cp in _EXPANSIONS and depth < 2:
        a, b = _EXPANSIONS[_EXPANSION_AS.get(cp, cp)]
        out = _records(a, depth + 1) + _records(b, depth + 1)
        # An expansion of a space and nonspacing marks (the isolated Arabic
        # harakat U+FE70-U+FE7F, the ligatures U+FC5E-U+FC63) weighs as the
        # marks alone: b187 finds U+FE70 = U+064B, U+FE70 = U+FE71 (tatweel and
        # the mark), U+FC5E = U+07AB.
        if len(out) > 1 and out[0][:2] == (7, 2) and all(r[0] == _NONSPACE_MARK for r in out[1:]):
            out = out[1:]
        return out
    if sm == _NONSPACE_MARK and cp in _DW_AS:
        dw = _raw(_DW_AS[cp])[2]
    if cp in _CW_AS:
        cw = _CW_AS[cp]
    return [(sm, aw, dw, cw)]


def _unit(cp: int) -> tuple:
    """A code unit's weights, by kind: a tuple of elements
    (primary, dw, cw, raw cw) -- a letter, digit or symbol; cw as text
    comparison sees it, raw cw as FIND does --, or ("mark", dw),
    ("punct", weight), or () for an unsortable one."""
    hit = _UNITS.get(cp)
    if hit is not None:
        return hit
    _load()
    out: list = []
    for sm, aw, dw, cw in _records(cp):
        if sm == _UNSORTABLE or sm == _EXPANSION:
            continue
        if sm == _NONSPACE_MARK:
            out.append(("mark", dw))
        elif sm == _PUNCTUATION:
            out.append(("punct", aw))
        elif sm == _EASTASIA_SPECIAL:
            if aw <= _MAX_SPECIAL_PW:
                # Repeat / cho-on marks. [MS-UCODEREF] 3.1.5.2.14 gives them
                # the weight of the kana before them; Desktop compares them as
                # characters of their own, all one weight whatever their DW /
                # CW (b187: U+3005 = U+3031 = ... = U+30FC = U+FF70; b190:
                # SEARCH("佐佐", "佐々木") finds
                # nothing; probes Q1a-Q1c: "佐々" <> "佐佐",
                # "あゝ" <> "ああ", "カー" <>
                # "カア"). They take the spec's weight for one with
                # nothing before it, the invalid weight, and sort after
                # everything (Q1d1, R1a-R1e: after U+4E2D, U+1F600, U+F0000,
                # U+10FFFD, U+F8FF; U+3005 = U+30FC).
                out.append((((_MAP_INVALID_WEIGHT << 8) | _MAP_INVALID_WEIGHT,), _MIN_DW, _MIN_CW, _MIN_CW))
            else:
                out.append((((_KANA << 8) | aw,), dw, cw & _CW_KEEP, cw))
        elif sm == _JAMO_SPECIAL:
            # [MS-UCODEREF] 3.1.5.2.14: the jamo's real script member and
            # weight are its AW and DW fields (U+1135 sorts among the Hangul
            # syllables of SM 217, b186). Old-Hangul sequences
            # (MapOldHangulSortKey) are not composed: Desktop compares jamo
            # one by one (b181: U+11A8 <> U+1100).
            out.append((((aw << 8) | dw,), _MIN_DW, cw & _CW_KEEP, cw))
        elif sm == _EXTENSION_A:
            # [MS-UCODEREF] 3.1.5.2.14 EXTENSION_A: two weights, (254, 255)
            # then (AW, DW) -- the CJK ideographs.
            out.append((((_SCRIPT_MEMBER_EXT_A << 8) | _PRIMARY_WEIGHT_EXT_A, (aw << 8) | dw),
                        _MIN_DW, _MIN_CW, _MIN_CW))
        else:
            out.append((((sm << 8) | aw,), dw,
                        _MIN_CW if sm <= _CW_IGNORED_UP_TO_SM else cw & _CW_KEEP, cw))
    hit = tuple(out)
    _UNITS[cp] = hit
    return hit


def _units(s: str):
    """The UTF-16 code units of a text."""
    for ch in s:
        cp = ord(ch)
        if cp < 0x10000:
            yield cp
        else:
            v = cp - 0x10000
            yield 0xD800 + (v >> 10)
            yield 0xDC00 + (v & 0x3FF)


def sort_key(s: str) -> tuple:
    """Desktop's key for a text: (primary weights, trailing punctuation,
    marks with nothing before them, diacritic weights, case weights,
    word-sort punctuation as (position, weight, marks)). The levels rank in
    that order: trailing punctuation before case and accents ("ך-" >
    "כ", "ぁ-" > "あ": probes R2a-R2c), accents before case
    ("ך" + U+05BC > "כ", "ぁ" + U+3099 > "あ": R3a-R3c). Keys order as Desktop sorts, and compare equal exactly
    when DAX finds the texts equal.

    Punctuation (SM 6: hyphens, apostrophes) weighs last of all ("a-e" <
    "aé", "coop" < "co-op"), except at the end of a text, where it
    weighs more than any accent: a text ending in it sorts after one that
    does not -- "é" < "e-", "a-é" < "ae-" -- those with
    punctuation elsewhere too first, by all of it in order ("a--b" < "-ab-"
    < "a'b'" < "a-b-"), then those with it only at the end, by the run's
    length, then its weights ("a-b-" < "ab'" < "ab-" < "ab–" <
    "ab--"). Desktop 2.152, b157f / b157g (the tests' ORDER157G); the
    position-only word sort is [MS-UCODEREF] 3.1.5.2.14 PUNCTUATION."""
    hit = _KEYS.get(s)
    if hit is not None:
        return hit
    prim: list = []
    orphan: list = []       # marks with nothing before them
    dia: list = []
    case: list = []
    spec: list = []
    trailing = 0            # punctuation since the last primary weight
    after_punct = False     # the last weighed element was punctuation
    for cp in _units(s):
        for el in _unit(cp):
            kind = el[0]
            if kind == "mark":
                if after_punct:
                    # A mark right after punctuation goes with it, not with
                    # the letter before: b189 finds "é-" <> "e-" +
                    # U+0301 and "-" + U+0301 <> U+0301 + "-", with "-" <
                    # "-" + U+0301 < U+0301 < U+0301 + "-".
                    pos, w, m = spec[-1]
                    spec[-1] = (pos, w, (m + el[1]) & 0xFF)
                # [MS-UCODEREF] 3.1.5.2.14 NONSPACE_MARK: adds to the last
                # diacritic weight (b189: "e" + U+0301 = "é", marks add
                # up in any order)
                elif dia:
                    dia[-1] = (dia[-1] + el[1]) & 0xFF
                # With nothing before it, a mark weighs after the trailing
                # punctuation and before every accent: U+0301 + "e" sorts
                # after "e" + U+0308 + U+0308 and every accented "e", and
                # before "f", "e-" and "e" + U+0301 + "-" (probes Q7a,
                # R6a-R6d; b189: U+0301 + "e" <> "é").
                elif orphan:
                    orphan[-1] = (orphan[-1] + el[1]) & 0xFF
                else:
                    orphan.append(el[1])
            elif kind == "punct":
                spec.append((len(prim), el[1], 0))
                trailing += 1
                after_punct = True
            else:
                p, d, c, _raw_cw = el
                prim.extend(p)
                dia.extend([d] + [_MIN_DW] * (len(p) - 1))
                case.extend([c] + [_MIN_CW] * (len(p) - 1))
                trailing = 0
                after_punct = False
    # trailing minimum weights are dropped ([MS-UCODEREF] 3.1.5.2.4)
    while dia and dia[-1] == _MIN_DW:
        dia.pop()
    while case and case[-1] == _MIN_CW:
        case.pop()
    tail: tuple = ()
    if prim and trailing:
        if trailing == len(spec):
            # only after the last letter: by the run, before the accents
            tail = (1, len(spec)) + tuple((w, m) for _p, w, m in spec)
            spec = []
        else:
            tail = (0,)     # at the end and elsewhere: by all of it, last
    key = (tuple(prim), tail, bytes(orphan), bytes(dia), tuple(case), tuple(spec))
    if len(_KEYS) >= _KEYS_MAX:
        _KEYS.clear()
    _KEYS[s] = key
    return key


def column_order_key(s: str) -> tuple:
    """The order of a text column's values in its attribute hierarchy, which
    Desktop's ORDER BY, MIN / MAX and sorted visuals read: the collation,
    then -- for values it finds equal, which a column keeps apart ("æ" /
    "ae", "é" / "É") -- their code points (b186 samples Desktop's
    ORDER BY of every BMP character in that order; issue #156)."""
    return (sort_key(s), s)


def compare(a: str, b: str) -> int:
    """-1, 0 or 1 as Desktop orders ``a`` and ``b``."""
    ka, kb = sort_key(a), sort_key(b)
    return (ka > kb) - (ka < kb)


def equal(a: str, b: str) -> bool:
    """DAX's text equality in an expression: "É" = "é", "æ" = "ae"."""
    return a == b or sort_key(a) == sort_key(b)


# ---- finding text: SEARCH, FIND, CONTAINSSTRING(EXACT), SUBSTITUTE ----------

# SEARCH's wildcards as the engine tokenizes them: `?` one element, `*` any run
ANY = object()
RUN = object()


def _elements(units: str, exact: bool) -> list:
    """A text (UTF-16 code units, one character each) as the elements a find
    matches whole (issue #180, b190): a character with the nonspacing marks
    after it ("e" + U+0301 is one element, as "é" is), an expansion
    whole ("ß", "æ": SEARCH("s", "xß") and SEARCH("a?",
    "æ") find nothing), punctuation with the marks after it, a mark
    with nothing before it. Unsortable characters belong to no element:
    "a­b" has two (SEARCH("ab", ...) is 1, SEARCH("a?b", ...) fails).

    Each element is (start unit, weights), its weights one item per primary
    weight -- (primary, dw, cw), the marks added to the last dw -- or
    ("p", weight, marks) / ("m", dw). ``exact`` keeps the whole case weight
    (FIND: "A" <> "Ａ", "SS" does not find "ß"); otherwise case,
    kana type and width drop out as the comparison drops them."""
    out: list = []
    for i, ch in enumerate(units):
        els = _unit(ord(ch))
        if not els:
            continue
        if els[0][0] == "mark" and out:
            items = out[-1][1]
        else:
            items = []
            out.append((i, items))
        for el in els:
            kind = el[0]
            if kind == "mark":
                if items and items[-1][0] == "m":
                    items[-1] = ("m", (items[-1][1] + el[1]) & 0xFF)
                elif items and items[-1][0] == "p":
                    _k, w, m = items[-1]
                    items[-1] = ("p", w, (m + el[1]) & 0xFF)
                elif items:
                    p, d, c = items[-1]
                    items[-1] = (p, (d + el[1]) & 0xFF, c)
                else:
                    items.append(("m", el[1]))
            elif kind == "punct":
                items.append(("p", el[1], 0))
            else:
                p, d, c, raw = el
                items.append((p, d, raw if exact else c))
    return [(i, tuple(items)) for i, items in out]


@lru_cache(maxsize=256)
def _text_elements(units: str, exact: bool) -> tuple:
    return tuple(_elements(units, exact))


def _pattern(tokens: tuple, exact: bool) -> list:
    """The find text as a list of ANY, RUN and runs of weights: the literal
    characters between two wildcards, their elements' items joined. Marks
    right after a `?` go with it and match whatever the element has
    (SEARCH("?" & U+0301, "e" & U+0301) is 1, b190; SEARCH("?" & U+0301,
    "e") and SEARCH("?" & U+0301, "e" & U+0300) are 1, probes Q2a, Q2b). Unsortable characters weigh nothing ("­b" finds "b")."""
    out: list = []
    lit: list = []

    def flush():
        if lit:
            out.append(tuple(item for _i, items in _elements("".join(lit), exact) for item in items))
            lit.clear()
    for t in tokens:
        if t is ANY or t is RUN:
            flush()
            out.append(t)
        elif out and out[-1] is ANY and not lit and _unit(ord(t)) and all(
                el[0] == "mark" for el in _unit(ord(t))):
            continue
        else:
            lit.append(t)
    flush()
    return out


def _is_mark(ch) -> bool:
    """A code unit that weighs only as a nonspacing mark."""
    if not isinstance(ch, str):
        return False
    els = _unit(ord(ch))
    return bool(els) and all(el[0] == "mark" for el in els)


def search(tokens, text: str, start0: int, ignore_case: bool) -> int:
    """The 0-based UTF-16 position of the first element from ``start0`` on
    where ``tokens`` -- characters, or the wildcards ANY / RUN -- match
    ``text`` (UTF-16 code units, one character each), or -1. The literal
    characters match whole elements of the text by their weights: "ss"
    finds "ß" and "ß" finds "ss", "é" finds "e" + U+0301, an
    unsortable character is skipped; ANY takes one element, RUN any run of
    them. ``ignore_case`` compares as SEARCH does (case, kana type and width
    ignored); otherwise as FIND does. An empty find text is found at
    ``start0``; one of unsortable characters only is found nowhere
    (FIND(U+00AD, "a" & U+00AD & "b") fails, b190). The engine applies
    SEARCH's rules for the start and a leading RUN (engine._wild_search).

    The text is taken from ``start0`` on: a start between a letter and its
    mark leaves the mark an element of its own (FIND(U+0301, "e" & U+0301 &
    "x", 2) is 2, probes R5a-R5e). ANY never takes such a lone mark
    (SEARCH("?", U+0301) and SEARCH("?", "e" & U+0301, 2) fail: R4a, Q8b,
    R4d, R4e).

    A find text that starts with a nonspacing mark (probes S1-S128,
    T1-T23):

    - one mark and nothing else is found at ``start0`` when the unit there,
      or the one after it, is a nonspacing mark -- any mark, whatever its
      weight: FIND(U+0301, "e" & U+0300) is 1, FIND(U+0301, "é") and
      FIND(U+0301, "xe" & U+0301) fail; never further on;
    - two or more marks (or a mark and an unsortable) are found nowhere;
    - marks followed by more drop out of SEARCH's find text
      (SEARCH(U+0301 & "x", "ex", 2) is 2); FIND finds the rest only where a
      unit from ``start0`` on precedes it or a unit that is not a mark
      follows it (FIND(U+0301 & "x", "ex", 2) and FIND(U+0301 & "x", "x")
      fail, FIND(U+0301 & "x", "xe") is 1)."""
    tokens = tuple(tokens)
    if not tokens:
        return start0
    lead = 0
    while lead < len(tokens) and _is_mark(tokens[lead]):
        lead += 1
    if lead:
        rest = tokens[lead:]
        if not rest:
            if lead > 1:
                return -1
            hit = any(_is_mark(ch) for ch in text[start0:start0 + 2])
            return start0 if hit else -1
        if all(isinstance(t, str) and not _unit(ord(t)) for t in rest):
            return -1
        if ignore_case:
            return search(rest, text, start0, True)
        pos = start0
        while True:
            hit = search(rest, text, pos, False)
            if hit < 0:
                return -1
            end = _match_end(rest, text, hit)
            if hit > start0 or (end < len(text) and not _is_mark(text[end])):
                return hit
            pos = hit + 1
    exact = not ignore_case
    pat = _pattern(tokens, exact)
    if all(p == () for p in pat):
        return -1           # unsortable characters only
    # between wildcards they drop out: SEARCH("a*" & U+00AD, "ab") is 1 (b190)
    pat = [p for p in pat if p != ()]
    found = _match(pat, text, start0, exact)
    return found[0] if found else -1


def _match_end(tokens, text: str, start: int) -> int:
    """Where the match of ``tokens`` (literal, FIND's) that starts at
    ``start`` ends: the unit after its last element."""
    found = _match([p for p in _pattern(tuple(tokens), True) if p != ()], text, start, True)
    return found[1] if found else start


def _match(pat: list, text: str, start0: int, exact: bool):
    """(start, end) of the first match of ``pat`` in ``text`` from
    ``start0`` on, in UTF-16 units, or None."""
    els = [(i + start0, items) for i, items in _text_elements(text[start0:], exact)]
    n = len(els)
    seen: dict = {}

    def end_of(j: int) -> int:
        return els[j][0] if j < n else len(text)

    def match(k: int, j: int):
        """pattern from item k matches the text's elements from j on: the
        index of the element after the match, or None"""
        if k == len(pat):
            return j
        p = pat[k]
        if p is ANY:
            if j < n and els[j][1][0][0] != "m":
                return match(k + 1, j + 1)
            return None
        if p is RUN:
            if (k, j) not in seen:
                seen[(k, j)] = next((r for r in (match(k + 1, jj) for jj in range(j, n + 1))
                                     if r is not None), None)
            return seen[(k, j)]
        pos = 0
        while pos < len(p):
            if j >= n:
                return None
            items = els[j][1]
            if p[pos:pos + len(items)] != items:
                return None
            pos += len(items)
            j += 1
        return match(k + 1, j)

    for j in range(n):
        r = match(0, j)
        if r is not None:
            return els[j][0], end_of(r)
    return None
