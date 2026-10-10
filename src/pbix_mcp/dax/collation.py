"""Power BI Desktop's text collation: the order its engine sorts text in and
the comparison DAX's text operators make (issues #156, #157, #185).

Desktop's engine compares text by the sorting weight table of SQL Server's
version-100 collations (case-insensitive, accent-sensitive, kana- and
width-insensitive for a model this package builds), not by the Windows NLS
version it runs on. Each UTF-16 code unit has a script member, a primary
weight, a diacritic weight and a case weight ([MS-UCODEREF] 3.1.5.2 lays the
table and the sort key out). Texts compare by their primary weights; then,
left to right, by the accents; then by the case weights where they count;
then by the "word sort" punctuation -- hyphens and ASCII apostrophes weigh
nothing at first and only order the strings they alone tell apart, earliest
first. "É" = "é", "æ" = "ae", "ß" = "ss", "²" = "2", "Ａ" = "A";
"_x" < "1x" < "a10" < "a2" < "Americas" < "APAC"; "coop" < "co-op" < "cop";
"cote" < "coté" < "côte".

Case and width only drop out in the cased scripts (script members up to
Coptic's: digits, Latin, Greek, Cyrillic, Armenian, Georgian, Glagolitic,
Coptic): there "a" = "A" and "ᴀ" = "A" and "①" = "1". Elsewhere the case
weight keeps what it marks -- a Hebrew final letter (כ <> ך), a small kana
(ぁ <> あ), a final jamo -- and only the kana type and width drop out
(あ = ア = ｱ). The table's own weights keep apart what Windows' newer
tables merge: the Kelvin sign and K, the Ohm sign and Ω, Ƒ and ƒ.

Context rules: a nonspacing mark adds to the accent of what it follows; an
expansion weighs as the letters it expands to; a kana iteration or
prolonged-sound mark repeats the weight before it (alone, it sorts after
everything); a conjoining jamo weighs among the Hangul syllables; a CJK
ideograph weighs after them. Characters beyond the BMP are weighted by
their UTF-16 surrogates.

``sort_key(s)`` gives a key whose order is Desktop's and whose equality is
DAX's text equality in expressions. (The column store is stricter: it folds
ASCII case only, so "æ" and "ae" are two values of a column.)
"""
from __future__ import annotations

import lzma
from collections.abc import Sequence
from functools import lru_cache

_TABLE: list = []
_EXP: list = []
_KEYS: dict[str, tuple] = {}
_KEYS_MAX = 200_000

# script members ([MS-UCODEREF] 3.1.5.2.4)
_UNSORTABLE, _NONSPACE_MARK, _EXPANSION, _EASTASIA_SPECIAL, _JAMO_SPECIAL, _EXTENSION_A, _PUNCTUATION = range(7)
_KANA = 34
_LAST_CASED = 0x14          # Coptic: the last script whose case and width drop out
_CASE_MASK = 0xDE           # case kept, kana type and width dropped (0xc6 | the case bits 0x18)
_REPEAT_ALONE = 0xFFFF      # a repeat / prolonged-sound mark with nothing before it


def _load() -> None:
    if _TABLE:
        return
    from pbix_mcp.dax import _collation_data as data

    raw = lzma.decompress(bytes.fromhex("".join(data.DATA)))
    n = int.from_bytes(raw[:4], "little")
    table = raw[4:4 + 4 * n]
    exp = raw[4 + 4 * n:]
    # (script member, primary weight, diacritic weight, case weight) per code unit
    _EXP[:] = [(exp[i] | exp[i + 1] << 8, exp[i + 2] | exp[i + 3] << 8) for i in range(0, len(exp), 4)]
    _TABLE[:] = [(table[i + 1], table[i], table[i + 2], table[i + 3]) for i in range(0, len(table), 4)]


def _expansion(cu: int) -> list:
    """The weights an expansion stands for: its two characters', the second
    expanding once more where it does (U+FB03 ffi -> f + U+FB01 fi)."""
    _sm, pw, _dw, cw = _TABLE[cu]
    first, second = _EXP[pw | cw << 8]
    out = [_TABLE[first]]
    w = _TABLE[second]
    if w[0] == _EXPANSION:
        a, b = _EXP[w[1] | w[3] << 8]
        out.append(_TABLE[a])
        w = _TABLE[b]
    out.append(w)
    return out


def _units(s: str) -> list:
    if s.isascii():
        return [ord(c) for c in s]
    out = []
    for c in s:
        cp = ord(c)
        if cp < 0x10000:
            out.append(cp)
        else:
            v = cp - 0x10000
            out.append(0xD800 + (v >> 10))
            out.append(0xDC00 + (v & 0x3FF))
    return out


def _unicode_weight(sm: int, pw: int, dw: int) -> list:
    """The unicode weights of a character with a primary weight, its special
    script members resolved: a kana weighs as KANA, a conjoining jamo by the
    real script member and weight the table keeps in its primary and
    diacritic fields (among the Hangul syllables), a CJK ideograph by
    (0xFE, 0xFF) and then its real weight (after everything but the lone
    repeats)."""
    if sm >= 7:
        return [sm << 8 | pw]
    if sm == _EASTASIA_SPECIAL:
        return [_KANA << 8 | pw]
    if sm == _JAMO_SPECIAL:
        return [pw << 8 | dw]
    return [0xFEFF, pw << 8 | dw]          # _EXTENSION_A


def weights(s: str) -> tuple:
    """(primary, diacritic, case, special) weights of a text, before any
    flags: the primaries as 16-bit unicode weights (-1 for a nonspacing mark
    with nothing before it), one diacritic and one raw case weight per
    primary, and the word-sort punctuation as (number of primaries before
    it, its weight, its diacritic, its case weight)."""
    _load()
    prim: list = []
    dia: list = []
    case: list = []
    spec: list = []

    last_special = [False]          # did word-sort punctuation come last?

    def push(sm: int, pw: int, dw: int, cw: int) -> None:
        if sm == _NONSPACE_MARK:
            # a nonspacing mark adds to the accent of what it follows -- the
            # punctuation's own, when that came last ("e-" + U+0301 <> "é-")
            if last_special[0]:
                p, u, d, c = spec[-1]
                spec[-1] = (p, u, (d + dw) & 0xFF, c)
            elif dia:
                dia[-1] = (dia[-1] + dw) & 0xFF
            else:
                prim.append(-1)
                dia.append(dw)
                case.append(2)
            return
        last_special[0] = False
        uws = _unicode_weight(sm, pw, dw)
        prim.extend(uws)
        if len(uws) == 1:
            dia.append(2 if sm == _JAMO_SPECIAL else dw)
            case.append(cw)
        else:
            dia.extend((2, 2))
            case.extend((2, 2))

    units = _units(s)
    for i, cu in enumerate(units):
        sm, pw, dw, cw = _TABLE[cu]
        if sm == _UNSORTABLE:
            continue
        if sm == _EXPANSION:
            for part in _expansion(cu):     # letters, kana and nonspacing marks
                push(*part)
        elif sm == _PUNCTUATION:
            # its place: the letters (and lone marks) before it
            spec.append((len(prim), sm << 8 | pw, dw, cw))
            last_special[0] = True
        elif sm == _EASTASIA_SPECIAL and pw <= 1:
            # repeat (0) / prolonged sound (1): the weight of the character
            # before it -- a letter's, a CJK ideograph's, a kana's (its vowel
            # for a prolonged sound), punctuation's as a letter's; after an
            # expansion, a jamo or nothing it weighs as alone, after all
            uws = [_REPEAT_ALONE]
            j = i - 1
            while j >= 0:
                psm, ppw, pdw, _pcw = _TABLE[units[j]]
                if psm in (_UNSORTABLE, _NONSPACE_MARK) or (psm == _EASTASIA_SPECIAL and ppw <= 1):
                    j -= 1
                    continue
                if psm == _EASTASIA_SPECIAL:
                    uws = [_KANA << 8 | (ppw if pw == 0 else ppw & 0x87)]
                elif psm >= _EXTENSION_A:
                    uws = [psm << 8 | ppw] if psm == _PUNCTUATION else _unicode_weight(psm, ppw, pdw)
                break
            last_special[0] = False
            prim.extend(uws)
            dia.extend([2] * (len(uws) - 1) + [2 if uws[0] == _REPEAT_ALONE else dw])
            case.extend([2] * len(uws))
        else:
            push(sm, pw, dw, cw)
    return prim, dia, case, spec


def _case_level(prim: list, case: list) -> tuple:
    """The case weights DAX's comparison keeps: none in the cased scripts
    (case and width ignored there), the case bits elsewhere."""
    return tuple(0 if (p >> 8) <= _LAST_CASED else c & _CASE_MASK for p, c in zip(prim, case))


def sort_key(s: str) -> tuple:
    """Desktop's key for a text: (primary weights, trailing punctuation,
    diacritic weights, case weights, word-sort punctuation as (position,
    weight) pairs). Keys order as Desktop sorts, and compare equal exactly
    when DAX finds the texts equal.

    Hyphens and apostrophes weigh last of all ("a-e" < "aé", "coop" <
    "co-op"), except at the end of a text, where they weigh more than any
    accent: a text ending in them sorts after one that does not -- "é" <
    "e-", "a-é" < "ae-" -- those with no others first, by all of them in
    order ("a--b" < "-ab-" < "a'b'" < "a-b-"), then those with them only at
    the end, by the run's length, then its weights ("a-b-" < "ab'" < "ab-"
    < "ab–" < "ab--"). Desktop 2.152, build_b157f.py / build_b157g.py."""
    hit = _KEYS.get(s)
    if hit is not None:
        return hit
    prim, dia, case, specials = weights(s)
    # punctuation weighs by its weight, then its case and width, then its accent
    spec = [(pos, uw, cw, dw) for pos, uw, dw, cw in specials]
    real = [p for p in prim if p >= 0]
    trailing = sum(1 for pos, _uw, _d, _c in specials if pos == len(prim)) if real else 0
    if real:
        while dia and dia[-1] == 2:
            dia.pop()
    else:
        # no letter at all: lone nonspacing marks weigh by their accent, all
        # of it (ẞ < U+0E4C (0) < "-" < U+3099 (1) < U+05A2 (2)), punctuation
        # between an accent of 0 and one of 1
        dia = [b for d in dia for b in ((0, 0) if d == 0 else (1, d))] if dia else ([0, 1] if spec else [])
    tail: tuple = ()
    if real and trailing:
        if trailing == len(spec):
            # only after the last letter: by the run, before the accents
            tail = ((1, len(spec)) + tuple(uw for _p, uw, _c, _d in spec)
                    + tuple((c, d) for _p, _uw, c, d in spec))
            spec = []
        else:
            tail = (0,)     # at the end and elsewhere: by all of them, last
    cases = _case_level(real, [c for p, c in zip(prim, case) if p >= 0])
    key = (b"".join(p.to_bytes(2, "big") for p in real), tail, bytes(dia), cases, tuple(spec))
    if len(_KEYS) >= _KEYS_MAX:
        _KEYS.clear()
    _KEYS[s] = key
    return key


def column_order_key(s: str) -> tuple:
    """The order of a text column's values in its attribute hierarchy, which
    Desktop's ORDER BY, MIN / MAX and sorted visuals read: the collation,
    then -- for values it finds equal, which a column keeps apart ("æ" /
    "ae", "é" / "É") -- their code points (Desktop's own order among those
    follows no rule we can see) (issue #156)."""
    return (sort_key(s), s)


def compare(a: str, b: str) -> int:
    """-1, 0 or 1 as Desktop orders ``a`` and ``b``."""
    ka, kb = sort_key(a), sort_key(b)
    return (ka > kb) - (ka < kb)


def equal(a: str, b: str) -> bool:
    """DAX's text equality in an expression: "É" = "é", "æ" = "ae"."""
    return a == b or sort_key(a) == sort_key(b)


# ---- SEARCH / FIND: matching as the collation compares (issue #180) ---------
# Desktop finds text by elements: a character with the nonspacing marks right
# after it (an expansion is one element: "ß" weighs as "ss" but is matched
# whole -- SEARCH("ss", "xß") is 2, SEARCH("s", "xß") fails); ignorable
# characters belong to no element and are skipped ("ab" is found in "a" +
# U+00AD + "b"). SEARCH ignores case, kana type and width (masked as the
# comparison masks them, except that superscripts and the like stay apart:
# SEARCH("2", "x²") fails); FIND ignores nothing. Power BI Desktop 2.152 over
# ADOMD (build_b174*.py, build_b190.py) and its own find routine, run against
# random texts.

ANY = object()      # `?`: one element
RUN = object()      # `*`: any run of elements
_ALONE_W = (_REPEAT_ALONE, 2, 2)


def _find_elements(units: list, drop_first_mark: bool = True) -> list:
    """[(first unit, end unit, weights, weights as a match's first)] -- one
    per weighted character. A nonspacing mark right after a character adds to
    its last diacritic; one after an ignorable stands alone; one at the very
    start weighs nothing. A repeat / prolonged-sound mark repeats the letter
    before it (with its own accent), but not a CJK ideograph or a kana, and
    weighs as alone where a match starts with it."""
    _load()
    out: list = []
    for i, cu in enumerate(units):
        sm, pw, dw, cw = _TABLE[cu]
        if sm == _UNSORTABLE:
            continue
        if sm == _NONSPACE_MARK:
            if i == 0 and drop_first_mark:
                continue
            if out and out[-1][1] == i:
                a, _b, ws, first = out[-1]
                if ws[-1][0] != _REPEAT_ALONE:
                    u, d, c = ws[-1]
                    ws = ws[:-1] + [(u, (d + dw) & 0xFF, c)]
                if first[-1][0] != _REPEAT_ALONE:
                    u, d, c = first[-1]
                    first = first[:-1] + [(u, (d + dw) & 0xFF, c)]
                out[-1] = (a, i + 1, ws, first)
            else:
                lone = [(-1, dw, 2)]
                out.append((i, i + 1, lone, lone))
            continue
        if sm == _EXPANSION:
            ws = [(uw, 2 if psm == _JAMO_SPECIAL else pdw, pcw)
                  for psm, ppw, pdw, pcw in _expansion(cu) for uw in _unicode_weight(psm, ppw, pdw)]
            first = ws
        elif sm == _PUNCTUATION:
            ws = first = [(sm << 8 | pw, dw, cw)]
        elif sm == _EASTASIA_SPECIAL and pw <= 1:
            uw = _REPEAT_ALONE
            j = i - 1
            while j >= 0:
                psm, ppw, _pdw, _pcw = _TABLE[units[j]]
                if psm in (_UNSORTABLE, _NONSPACE_MARK) or (psm == _EASTASIA_SPECIAL and ppw <= 1):
                    j -= 1
                    continue
                if psm == _PUNCTUATION or psm >= 7:
                    uw = psm << 8 | ppw
                break
            ws = [(uw, dw, 2)]
            first = [_ALONE_W]
        else:
            ws = first = [(uw, 2 if sm == _JAMO_SPECIAL else dw, cw) for uw in _unicode_weight(sm, pw, dw)]
        out.append((i, i + 1, ws, first))
    return out


def _masked(ws: list, ignore_case: bool) -> tuple:
    out = []
    for u, d, c in ws:
        if u == _REPEAT_ALONE:
            out.append(_ALONE_W)
        elif ignore_case:
            out.append((u, d, c & (0xC6 if 0 <= u and (u >> 8) <= _LAST_CASED else 0xDE)))
        else:
            out.append((u, d, c))
    return tuple(out)


def _match_items(items: tuple, k: int, te: Sequence, j: int, ignore_case: bool, memo: dict):
    """The end element of a match of items[k:] at text element j, or None."""
    key = (k, j)
    if key in memo:
        return memo[key]
    res = None
    if k == len(items):
        res = j
    else:
        item = items[k]
        if item is ANY:
            if j < len(te):
                res = _match_items(items, k + 1, te, j + 1, ignore_case, memo)
        elif item is RUN:
            for jj in range(j, len(te) + 1):
                res = _match_items(items, k + 1, te, jj, ignore_case, memo)
                if res is not None:
                    break
        else:
            acc: tuple = ()
            jj = j
            while jj < len(te) and len(acc) < len(item):
                acc += te[jj][2]
                jj += 1
                if acc != item[:len(acc)]:
                    break
            if acc == item:
                res = _match_items(items, k + 1, te, jj, ignore_case, memo)
    memo[key] = res
    return res


@lru_cache(maxsize=4096)
def _pattern_items(tokens: tuple, ignore_case: bool) -> tuple:
    """The find text's tokens as items: ANY, RUN, or a literal run's weights
    (each run weighed as a text of its own)."""
    items: list = []
    lit: list = []

    def flush():
        if lit:
            ws = tuple(w for e in _find_elements(_units("".join(lit))) for w in _masked(e[2], ignore_case))
            if ws:
                items.append(ws)
            lit.clear()

    for t in tokens:
        if t is ANY or t is RUN:
            flush()
            items.append(t)
        else:
            lit.append(t)
    flush()
    return tuple(items)


@lru_cache(maxsize=65536)
def _text_layout(text: str, ignore_case: bool) -> tuple:
    """(units, element starts, tricky, elements) of a text to search in;
    ``tricky`` when a nonspacing or kana-special mark makes the elements
    depend on where a match starts (elements is then empty)."""
    units = _units(text)
    starts = tuple(a for a, _b, _ws, _f in _find_elements(units))
    tricky = any(_TABLE[cu][0] in (_NONSPACE_MARK, _EASTASIA_SPECIAL) for cu in units)
    return units, starts, tricky, (() if tricky else tuple(_text_elements(units, 0, ignore_case)))


def _text_elements(units: list, start: int, ignore_case: bool) -> list:
    """The text's elements from unit ``start`` on, compared as a text of their
    own: a repeat mark there looks back no further, a mark there weighs as
    alone (or nothing at the text's very start)."""
    els = _find_elements(units[start:], drop_first_mark=start == 0)
    return [(a + start, b + start, _masked(first if n == 0 else ws, ignore_case))
            for n, (a, b, ws, first) in enumerate(els)]


def search(tokens, text: str, start0: int, ignore_case: bool):
    """(0-based unit position, end element's end unit) where the find text's
    tokens (literal characters, ANY, RUN) match ``text`` from unit ``start0``
    on, or None. A pattern with no weighed character in it matches nothing."""
    items = _pattern_items(tuple(tokens), ignore_case)
    if not any(i is not ANY and i is not RUN for i in items) and ANY not in items:
        return None
    _load()
    units, starts, tricky, full = _text_layout(text, ignore_case)
    for n, a in enumerate(starts):
        if a < start0:
            continue
        if tricky:
            te, j0 = _text_elements(list(units), a, ignore_case), 0
            if not te or te[0][0] != a:
                continue
        else:
            te, j0 = full, n
        end = _match_items(items, 0, te, j0, ignore_case, {})
        if end is not None:
            return a, (te[end - 1][1] if end > j0 else a)
    return None
