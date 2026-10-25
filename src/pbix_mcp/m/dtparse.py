"""Text to a date and time as .NET's lenient ``DateTime.TryParse`` reads it in a culture: the parse a Desktop
refresh falls back to when the text isn't one of M's ISO forms (pivot-studio #189's probes: "20-11-15",
"Fri, 20 Nov 2015", "2015.11.20", "10:59:30.5", "Nov 2015" and a time alone all convert).

A Python port of dotnet/runtime's ``System.Globalization.DateTimeParse`` (tag v8.0.0; MIT License, Copyright (c)
.NET Foundation and Contributors; see THIRD_PARTY_NOTICES.md): its token table, lexer, state table and terminal
states, for the Gregorian calendar and the cultures in ``CULTURE_DATA``. CJK and Hebrew suffixes, genitive and leap
month names are left out (no culture here has them). A time zone ("GMT", "Z", "+10:00") is refused, not guessed:
a refresh converts it to the machine's local time.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


@dataclass(frozen=True)
class DateCulture:
    """What a culture's DateTimeFormatInfo gives the parse (Windows' culture data, as .NET Framework reads it)."""
    name: str
    short_date: str
    long_date: str
    month_day: str
    year_month: str
    am: str = "AM"
    pm: str = "PM"
    date_sep: str = "/"
    time_sep: str = ":"
    two_digit_year_max: Optional[int] = 2049   # "11/20/49" is 2049, "11/20/50" 1950 in a Desktop refresh (#104)
    months: tuple[str, ...] = _MONTHS
    abbr_months: tuple[str, ...] = tuple(m[:3] for m in _MONTHS)
    days: tuple[str, ...] = _DAYS
    abbr_days: tuple[str, ...] = tuple(d[:3] for d in _DAYS)
    eras: tuple[str, ...] = ("A.D.",)
    abbr_eras: tuple[str, ...] = ("AD",)
    _tokens: list = field(default_factory=list, compare=False, repr=False)


CULTURE_DATA: dict[str, DateCulture] = {
    "en-US": DateCulture("en-US", "M/d/yyyy", "dddd, MMMM d, yyyy", "MMMM d", "MMMM yyyy"),
    "en-AU": DateCulture("en-AU", "d/MM/yyyy", "dddd, d MMMM yyyy", "d MMMM", "MMMM yyyy"),
    # The invariant culture, which M names "" (.NET's invariant data; its date text is measured, #189). Its
    # two-digit years aren't measured, so they are refused.
    "": DateCulture("", "MM/dd/yyyy", "dddd, dd MMMM yyyy", "MMMM dd", "yyyy MMMM", two_digit_year_max=None),
}


class Unsupported(Exception):
    """Text the port refuses rather than guess at; the message says why."""


class TimeZoneUnsupported(Unsupported):
    def __init__(self) -> None:
        super().__init__("A date and time with a time zone or an offset isn't supported yet.")


# --- token types (TokenType) ---------------------------------------------------------------------------------
NUMBER, YEAR_NUMBER, AM, PM, MONTH, END_OF_STRING, DAY_OF_WEEK, TIME_ZONE, ERA, DATE_WORD, UNKNOWN = range(1, 12)
IGNORABLE = 15
SEP_UNK, SEP_END, SEP_SPACE, SEP_AM, SEP_PM, SEP_DATE, SEP_TIME = (0x100, 0x200, 0x300, 0x400, 0x500, 0x600, 0x700)
SEP_LOCAL_TIME_MARK, SEP_DATE_OR_OFFSET = 0xE00, 0xF00
REGULAR_MASK, SEPARATOR_MASK = 0x00FF, 0xFF00
_HASH_SIZE, _SECOND_PRIME = 199, 197


def _lower(c: str) -> str:
    return c.lower() if len(c.lower()) == 1 else c


def _same(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _insert_at(table: list, s: str, ch: str, ttype: int, tvalue: int, pos: int, code: int, probe: int) -> None:
    previous = table[code]
    table[code] = [s, ttype, tvalue]
    while pos + 1 < _HASH_SIZE:
        pos += 1
        code += probe
        if code >= _HASH_SIZE:
            code -= _HASH_SIZE
        temp = table[code]
        if temp is not None and _lower(temp[0][0]) != ch:
            continue
        table[code] = previous
        if temp is None:
            return
        previous = temp


def _insert(table: list, s: str, ttype: int, tvalue: int) -> None:
    if not s:
        return
    if s[0].isspace() or s[-1].isspace():
        s = s.strip()
        if not s:
            return
    ch = _lower(s[0])
    code, probe = ord(ch) % _HASH_SIZE, 1 + ord(ch) % _SECOND_PRIME
    for i in range(_HASH_SIZE):
        value = table[code]
        if value is None:
            table[code] = [s, ttype, tvalue]
            return
        if len(s) >= len(value[0]) and _same(s[:len(value[0])], value[0]):
            if len(s) > len(value[0]):
                _insert_at(table, s, ch, ttype, tvalue, i, code, probe)
                return
            # The same token: add the other class (regular or separator) if it has none yet.
            if (not value[1] & REGULAR_MASK and ttype & REGULAR_MASK
                    or not value[1] & SEPARATOR_MASK and ttype & SEPARATOR_MASK):
                value[1] |= ttype
                if tvalue:
                    value[2] = tvalue
            return
        code += probe
        if code >= _HASH_SIZE:
            code -= _HASH_SIZE


def _token_table(c: DateCulture) -> list:
    """CreateTokenHashTable, for the parts these cultures have, in its order."""
    if c._tokens:
        return c._tokens
    t: list = [None] * _HASH_SIZE
    sep = c.time_sep.strip()
    if sep != ",":
        _insert(t, ",", IGNORABLE, 0)
    if sep != ".":
        _insert(t, ".", IGNORABLE, 0)
    _insert(t, c.time_sep, SEP_TIME, 0)
    _insert(t, c.am, SEP_AM | AM, 0)
    _insert(t, c.pm, SEP_PM | PM, 1)
    _insert(t, "-", SEP_DATE_OR_OFFSET, 0)
    _insert(t, c.date_sep, SEP_DATE, 0)
    for i in range(12):
        _insert(t, c.months[i], MONTH, i + 1)
        _insert(t, c.abbr_months[i], MONTH, i + 1)
    for i in range(12):
        _insert(t, c.abbr_months[i], MONTH, i + 1)
    for i in range(7):
        _insert(t, c.days[i], DAY_OF_WEEK, i)
        _insert(t, c.abbr_days[i], DAY_OF_WEEK, i)
    for i, (era, abbr) in enumerate(zip(c.eras, c.abbr_eras)):
        _insert(t, era, ERA, i + 1)
        _insert(t, abbr, ERA, i + 1)
    _insert(t, "AM", SEP_AM | AM, 0)                       # the invariant culture's
    _insert(t, "PM", SEP_PM | PM, 1)
    for i in range(12):
        _insert(t, _MONTHS[i], MONTH, i + 1)
        _insert(t, _MONTHS[i][:3], MONTH, i + 1)
    for i in range(7):
        _insert(t, _DAYS[i], DAY_OF_WEEK, i)
        _insert(t, _DAYS[i][:3], DAY_OF_WEEK, i)
    _insert(t, "AD", ERA, 1)
    _insert(t, "T", SEP_LOCAL_TIME_MARK, 0)
    _insert(t, "GMT", TIME_ZONE, 0)
    _insert(t, "Z", TIME_ZONE, 0)
    _insert(t, "/", SEP_DATE, 0)
    _insert(t, ":", SEP_TIME, 0)
    c._tokens.extend(t)
    return c._tokens


class _Str:
    """__DTString: the text, the index of the character being read and that character."""

    def __init__(self, value: str):
        self.value, self.index, self.current = value, -1, "\0"

    @property
    def length(self) -> int:
        return len(self.value)

    def get_next(self) -> bool:
        self.index += 1
        if self.index < self.length:
            self.current = self.value[self.index]
            return True
        return False

    def at_end(self) -> bool:
        return self.index >= self.length

    def advance(self, count: int) -> bool:
        self.index += count
        if self.index < self.length:
            self.current = self.value[self.index]
            return True
        return False

    def tokenize(self, mask: int, c: DateCulture) -> tuple[bool, int, int]:
        ch = self.current
        is_letter = ch.isalpha()
        if is_letter:
            ch = _lower(ch)
        code, probe = ord(ch) % _HASH_SIZE, 1 + ord(ch) % _SECOND_PRIME
        remaining = self.length - self.index
        table = _token_table(c)
        for _ in range(_HASH_SIZE):
            value = table[code]
            if value is None:
                break
            s, ttype, tvalue = value
            if ttype & mask and len(s) <= remaining:
                compare = True
                if is_letter:
                    nxt = self.index + len(s)
                    if nxt > self.length:
                        compare = False
                    elif nxt < self.length:
                        compare = not self.value[nxt].isalpha()
                if compare and (len(s) == 1 and self.value[self.index] == s
                                or _same(self.value[self.index:self.index + len(s)], s)):
                    self.advance(len(s))
                    return True, ttype & mask, tvalue
            code += probe
            if code >= _HASH_SIZE:
                code -= _HASH_SIZE
        return False, UNKNOWN, 0

    def regular_token(self, c: DateCulture) -> tuple[int, int]:
        if self.index >= self.length:
            return END_OF_STRING, 0
        while True:
            if "0" <= self.current <= "9":
                value = ord(self.current) - 48
                start = self.index
                self.index += 1
                while self.index < self.length:
                    self.current = self.value[self.index]
                    if "0" <= self.current <= "9":
                        value = value * 10 + ord(self.current) - 48
                        self.index += 1
                    else:
                        break
                n = self.index - start
                if n > 8:
                    return NUMBER, -1
                return (NUMBER if n < 3 else YEAR_NUMBER), value
            if self.current.isspace():
                while True:
                    self.index += 1
                    if self.index >= self.length:
                        return END_OF_STRING, 0
                    self.current = self.value[self.index]
                    if not self.current.isspace():
                        break
                continue
            _, ttype, tvalue = self.tokenize(REGULAR_MASK, c)
            return ttype, tvalue

    def skip_space_and_rtl(self) -> bool:
        if self.index >= self.length:
            return False
        if not self.current.isspace() and self.current != "‏":
            return True
        while True:
            self.index += 1
            if self.index >= self.length:
                return False
            self.current = self.value[self.index]
            if not self.current.isspace() and self.current != "‏":
                return True

    def separator_token(self, c: DateCulture) -> tuple[int, int, str]:
        before, ch_before = self.index, self.current
        if not self.skip_space_and_rtl():
            return SEP_END, before, ch_before
        if not "0" <= self.current <= "9":
            found, ttype, _ = self.tokenize(SEPARATOR_MASK, c)
            if not found:
                ttype = SEP_SPACE
        else:
            ttype = SEP_SPACE
        return ttype, before, ch_before

    def skip_white_spaces(self) -> None:
        while self.index + 1 < self.length and self.value[self.index + 1].isspace():
            self.index += 1

    def match(self, ch: str) -> bool:
        self.index += 1
        if self.index >= self.length:
            return False
        if self.value[self.index] == ch or ch == " " and self.value[self.index] in "  ":
            self.current = ch
            return True
        self.index -= 1
        return False

    def parse_digits(self, n: int) -> Optional[int]:
        start, result, got = self.index, 0, 0
        while got < n:
            self.index += 1
            if not (self.index < self.length and "0" <= self.value[self.index] <= "9"):
                self.index -= 1
                break
            result = result * 10 + ord(self.value[self.index]) - 48
            got += 1
        if got < n:
            self.index = start
            return None
        return result


# --- tokens and states (DTT, DS) and the state table ---------------------------------------------------------
(T_END, T_NUM_END, T_NUM_AMPM, T_NUM_SPACE, T_NUM_DATESEP, T_NUM_TIMESEP, T_MONTH_END, T_MONTH_SPACE,
 T_MONTH_DATESEP, T_NUM_DATESUFF, T_NUM_TIMESUFF, T_DAY_OF_WEEK, T_YEAR_SPACE, T_YEAR_DATESEP, T_YEAR_END,
 T_TIME_ZONE, T_ERA, T_NUM_UTC_MARK, T_UNK, T_NUM_LOCAL_MARK) = range(20)
(BEGIN, N, NN, D_Nd, D_NN, D_NNd, D_M, D_MN, D_NM, D_MNd, D_NDS, D_Y, D_YN, D_YNd, D_YM, D_YMd, D_S, T_S, T_Nt,
 T_NNt, ERROR, DX_NN, DX_NNN, DX_MN, DX_NM, DX_MNN, DX_DS, DX_DSN, DX_NDS, DX_NNDS, DX_YNN, DX_YMN, DX_YN, DX_YM,
 TX_N, TX_NN, TX_NNN, TX_TS, DX_NNY) = range(39)

# Columns: End NumEnd NumAmPm NumSpace NumDaySep NumTimesep MonthEnd MonthSpace MonthDSep NumDateSuff NumTimeSuff
# DayOfWeek YearSpace YearDateSep YearEnd TimeZone Era UTCTimeMark.
_STATES = [
    [BEGIN, ERROR, TX_N, N, D_Nd, T_Nt, ERROR, D_M, D_M, D_S, T_S, BEGIN, D_Y, D_Y, ERROR, BEGIN, BEGIN, ERROR],
    [ERROR, DX_NN, TX_NN, NN, D_NNd, ERROR, DX_NM, D_NM, D_MNd, D_NDS, ERROR, N, D_YN, D_YNd, DX_YN, N, N, ERROR],
    [DX_NN, DX_NNN, TX_NNN, DX_NNN, ERROR, T_Nt, DX_MNN, DX_MNN, ERROR, ERROR, T_S, NN, DX_NNY, ERROR, DX_NNY, NN, NN,
     ERROR],
    [ERROR, DX_NN, ERROR, D_NN, D_NNd, ERROR, DX_NM, D_MN, D_MNd, ERROR, ERROR, D_Nd, D_YN, D_YNd, DX_YN, ERROR, D_Nd,
     ERROR],
    [DX_NN, DX_NNN, TX_N, DX_NNN, ERROR, T_Nt, DX_MNN, DX_MNN, ERROR, DX_DS, T_S, D_NN, DX_NNY, ERROR, DX_NNY, ERROR,
     D_NN, ERROR],
    [ERROR, DX_NNN, DX_NNN, DX_NNN, ERROR, ERROR, DX_MNN, DX_MNN, ERROR, DX_DS, ERROR, D_NNd, DX_NNY, ERROR, DX_NNY,
     ERROR, D_NNd, ERROR],
    [ERROR, DX_MN, ERROR, D_MN, D_MNd, ERROR, ERROR, ERROR, ERROR, ERROR, ERROR, D_M, D_YM, D_YMd, DX_YM, ERROR, D_M,
     ERROR],
    [DX_MN, DX_MNN, DX_MNN, DX_MNN, ERROR, T_Nt, ERROR, ERROR, ERROR, DX_DS, T_S, D_MN, DX_YMN, ERROR, DX_YMN, ERROR,
     D_MN, ERROR],
    [DX_NM, DX_MNN, DX_MNN, DX_MNN, ERROR, T_Nt, ERROR, ERROR, ERROR, DX_DS, T_S, D_NM, DX_YMN, ERROR, DX_YMN, ERROR,
     D_NM, ERROR],
    [ERROR, DX_MNN, ERROR, DX_MNN, ERROR, ERROR, ERROR, ERROR, ERROR, ERROR, ERROR, D_MNd, DX_YMN, ERROR, DX_YMN, ERROR,
     D_MNd, ERROR],
    [DX_NDS, DX_NNDS, DX_NNDS, DX_NNDS, ERROR, T_Nt, ERROR, ERROR, ERROR, D_NDS, T_S, D_NDS, ERROR, ERROR, ERROR, ERROR,
     D_NDS, ERROR],
    [ERROR, DX_YN, ERROR, D_YN, D_YNd, ERROR, DX_YM, D_YM, D_YMd, D_YM, ERROR, D_Y, ERROR, ERROR, ERROR, ERROR, D_Y,
     ERROR],
    [DX_YN, DX_YNN, DX_YNN, DX_YNN, ERROR, ERROR, DX_YMN, DX_YMN, ERROR, ERROR, ERROR, D_YN, ERROR, ERROR, ERROR, ERROR,
     D_YN, ERROR],
    [ERROR, DX_YNN, DX_YNN, DX_YNN, ERROR, ERROR, DX_YMN, DX_YMN, ERROR, ERROR, ERROR, D_YN, ERROR, ERROR, ERROR, ERROR,
     D_YN, ERROR],
    [DX_YM, DX_YMN, DX_YMN, DX_YMN, ERROR, ERROR, ERROR, ERROR, ERROR, ERROR, ERROR, D_YM, ERROR, ERROR, ERROR, ERROR,
     D_YM, ERROR],
    [ERROR, DX_YMN, DX_YMN, DX_YMN, ERROR, ERROR, ERROR, ERROR, ERROR, ERROR, ERROR, D_YM, ERROR, ERROR, ERROR, ERROR,
     D_YM, ERROR],
    [DX_DS, DX_DSN, TX_N, T_Nt, ERROR, T_Nt, ERROR, ERROR, ERROR, D_S, T_S, D_S, ERROR, ERROR, ERROR, ERROR, D_S, ERROR],
    [TX_TS, TX_TS, TX_TS, T_Nt, D_Nd, ERROR, ERROR, ERROR, ERROR, D_S, T_S, T_S, ERROR, ERROR, ERROR, T_S, T_S, ERROR],
    [ERROR, TX_NN, TX_NN, TX_NN, ERROR, T_NNt, DX_NM, D_NM, ERROR, ERROR, T_S, ERROR, ERROR, ERROR, ERROR, T_Nt, T_Nt,
     TX_NN],
    [ERROR, TX_NNN, TX_NNN, TX_NNN, ERROR, ERROR, ERROR, ERROR, ERROR, ERROR, T_S, T_NNt, ERROR, ERROR, ERROR, T_NNt,
     T_NNt, TX_NNN],
]
_TM_NOT_SET, _TM_AM, _TM_PM = -1, 0, 1
_ORDER_YMD, _ORDER_MDY, _ORDER_DMY, _ORDER_YDM, _ORDER_YM, _ORDER_MY, _ORDER_MD, _ORDER_DM = range(8)


class _Fail(Exception):
    pass


@dataclass
class _Raw:
    nums: list = field(default_factory=list)
    month: int = -1
    year: int = -1
    day_of_week: int = -1
    time_mark: int = _TM_NOT_SET
    fraction: float = -1.0


@dataclass
class _Result:
    year: int = -1
    month: int = -1
    day: int = -1
    hour: int = 0
    minute: int = 0
    second: int = 0
    have_date: bool = False
    have_time: bool = False


def _now() -> dt.datetime:
    """The machine's local time, which a parse with no date takes its day from (and one with no year its year)."""
    return dt.datetime.now()


def _orders(pattern: str, letters: str) -> list[str]:
    """The order of the pattern's y, M and d fields (GetYearMonthDayOrder and its kin); "ddd" and longer are day
    names, not days."""
    out: list[str] = []
    i, quoted = 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch in "\\%":
            i += 2
            continue
        if ch in "'\"":
            quoted = not quoted
        if not quoted and ch in "yMd":
            j = i
            while j + 1 < len(pattern) and pattern[j + 1] == ch:
                j += 1
            if ch in letters and not (ch == "d" and j - i + 1 > 2):
                out.append(ch)
            i = j
        i += 1
    return out[:len(letters)]


def _ymd_order(pattern: str) -> int:
    return {"yMd": _ORDER_YMD, "Mdy": _ORDER_MDY, "dMy": _ORDER_DMY, "ydM": _ORDER_YDM}.get(
        "".join(_orders(pattern, "yMd")), -1)


def _ym_order(pattern: str) -> int:
    return {"yM": _ORDER_YM, "My": _ORDER_MY}.get("".join(_orders(pattern, "yM")), -1)


def _md_order(pattern: str) -> int:
    return {"Md": _ORDER_MD, "dM": _ORDER_DM}.get("".join(_orders(pattern, "Md")), -1)


def _valid_day(y: int, m: int, d: int) -> bool:
    if not (1 <= y <= 9999 and 1 <= m <= 12 and d >= 1):
        return False
    try:
        dt.date(y, m, d)
    except ValueError:
        return False
    return True


def _four_digit(year: int, c: DateCulture) -> int:
    if year < 0:
        raise _Fail
    if year < 100:
        if c.two_digit_year_max is None:
            raise Unsupported(f"A two-digit year in the culture '{c.name}' isn't supported yet.")
        return (c.two_digit_year_max // 100 - (1 if year > c.two_digit_year_max % 100 else 0)) * 100 + year
    if year > 9999:
        raise _Fail
    return year


def _set(r: _Result, y: int, m: int, d: int) -> bool:
    if _valid_day(y, m, d):
        r.year, r.month, r.day = y, m, d
        return True
    return False


def _date(r: _Result, y: int, m: int, d: int) -> None:
    if not _set(r, y, m, d):
        raise _Fail
    r.have_date = True


def _no_date_yet(r: _Result) -> None:
    if r.have_date:
        raise _Fail                                  # two dates in one text


def _default_year(r: _Result) -> None:
    r.year = _now().year


def _terminal(state: int, r: _Result, raw: _Raw, c: DateCulture) -> None:
    """ProcessTerminalState: fill the result from the numbers read so far, then start the numbers again."""
    _fill(state, r, raw, c)
    raw.nums = []


def _fill(state: int, r: _Result, raw: _Raw, c: DateCulture) -> None:
    n = raw.nums
    if state == DX_NN:
        _no_date_yet(r)
        _default_year(r)
        if _md_order(c.month_day) == _ORDER_MD:
            _date(r, r.year, n[0], n[1])
        else:
            _date(r, r.year, n[1], n[0])
    elif state == DX_NNN:
        _no_date_yet(r)
        order = _ymd_order(c.short_date)
        if order == _ORDER_YMD:
            _date(r, _four_digit(n[0], c), n[1], n[2])
        elif order == _ORDER_MDY:
            _date(r, _four_digit(n[2], c), n[0], n[1])
        elif order == _ORDER_DMY:
            _date(r, _four_digit(n[2], c), n[1], n[0])
        elif order == _ORDER_YDM:
            _date(r, _four_digit(n[0], c), n[2], n[1])
        else:
            raise _Fail
    elif state in (DX_MN, DX_NM):
        _no_date_yet(r)
        md = _md_order(c.month_day)
        # MN reads the number as a year when the culture writes day before month and month before year; NM when
        # it writes month before day and year before month. Otherwise the number is the day.
        if md == (_ORDER_DM if state == DX_MN else _ORDER_MD):
            if _ym_order(c.year_month) == (_ORDER_MY if state == DX_MN else _ORDER_YM):
                if not _set(r, _four_digit(n[0], c), raw.month, 1):
                    raise _Fail
                return
        _default_year(r)
        if not _set(r, r.year, raw.month, n[0]):
            raise _Fail
    elif state == DX_MNN:
        _no_date_yet(r)
        order = _ymd_order(c.short_date)
        tries = {_ORDER_MDY: ((1, 0), (0, 1)), _ORDER_YMD: ((0, 1), (1, 0)), _ORDER_DMY: ((1, 0), (0, 1))}.get(order)
        if tries is None:
            raise _Fail
        for yi, di in tries:
            try:
                year = _four_digit(n[yi], c)
            except _Fail:
                continue
            if _valid_day(year, raw.month, n[di]):
                _date(r, year, raw.month, n[di])
                return
        raise _Fail
    elif state == DX_YNN:
        _no_date_yet(r)
        if _ymd_order(c.short_date) == _ORDER_YDM:
            _date(r, raw.year, n[1], n[0])
        else:
            _date(r, raw.year, n[0], n[1])
    elif state == DX_NNY:
        _no_date_yet(r)
        order = _ymd_order(c.short_date)
        if order == -1:
            raise _Fail
        if order in (_ORDER_MDY, _ORDER_YMD):
            _date(r, raw.year, n[0], n[1])
        else:
            _date(r, raw.year, n[1], n[0])
    elif state == DX_YMN:
        _no_date_yet(r)
        _date(r, raw.year, raw.month, n[0])
    elif state == DX_YN:
        _no_date_yet(r)
        _date(r, raw.year, n[0], 1)
    elif state == DX_YM:
        _no_date_yet(r)
        _date(r, raw.year, raw.month, 1)
    elif state in (TX_N, TX_NN, TX_NNN):
        if r.have_time or state == TX_N and raw.time_mark == _TM_NOT_SET:
            raise _Fail
        r.hour = n[0]
        if state != TX_N:
            r.minute = n[1]
        if state == TX_NNN:
            r.second = n[2]
        r.have_time = True
    elif state in (DX_DS, TX_TS, DX_DSN, DX_NDS, DX_NNDS):
        raise _Fail                                  # date and time suffixes: no culture here has them


def _fraction(s: _Str) -> tuple[float, int]:
    """ParseFraction, from the "." or "," under the index: (the fraction, summed digit by digit as .NET does, and
    how many digits it read)."""
    result, base, digits = 0.0, 0.1, 0
    while s.get_next() and "0" <= s.current <= "9":
        result += (ord(s.current) - 48) * base
        base *= 0.1
        digits += 1
    return result, digits


def _check_zone(s: _Str) -> None:
    """HandleTimeZone: an offset after a time is refused."""
    if s.index < s.length - 1:
        i = s.index
        while s.value[i].isspace() and i < s.length - 1:
            i += 1
        if s.value[i] in "+-":
            raise TimeZoneUnsupported


def _lex(state: int, s: _Str, raw: _Raw, r: _Result, c: DateCulture) -> int:
    """One token's type (DTT), with what it carries put in ``raw``."""
    ttype, tvalue = s.regular_token(c)
    if ttype in (NUMBER, YEAR_NUMBER):
        if len(raw.nums) == 3 or tvalue == -1:
            raise _Fail
        if state == T_NNt and s.index < s.length - 1 and s.value[s.index] in ".,":
            raw.fraction = _fraction(s)[0]      # no digits after the "." only drops it
        if state in (T_NNt, T_Nt) and s.index < s.length - 1:
            _check_zone(s)
        if ttype == YEAR_NUMBER:
            if raw.year != -1:
                raise _Fail
            raw.year = tvalue
            sep, before, ch_before = s.separator_token(c)
            if sep == SEP_END:
                return T_YEAR_END
            if sep in (SEP_AM, SEP_PM):
                if raw.time_mark != _TM_NOT_SET:
                    return T_UNK                     # a second AM/PM: .NET notes a failure it never reads
                raw.time_mark = _TM_AM if sep == SEP_AM else _TM_PM
                return T_YEAR_SPACE
            if sep == SEP_SPACE:
                return T_YEAR_SPACE
            if sep == SEP_DATE:
                return T_YEAR_DATESEP
            if sep == SEP_DATE_OR_OFFSET:
                if _STATES[state][T_YEAR_DATESEP] == ERROR and _STATES[state][T_YEAR_SPACE] > ERROR:
                    s.index, s.current = before, ch_before
                    return T_YEAR_SPACE
                return T_YEAR_DATESEP
            raise _Fail                              # a time separator after a year
        sep, before, ch_before = s.separator_token(c)
        if sep == SEP_END:
            raw.nums.append(tvalue)
            return T_NUM_END
        if sep in (SEP_AM, SEP_PM):
            if raw.time_mark != _TM_NOT_SET:
                return T_UNK                         # as above: the number is dropped and the parse goes on
            raw.time_mark = _TM_AM if sep == SEP_AM else _TM_PM
            if state == D_NN:                         # "1/10 5 AM"
                _terminal(DX_NN, r, raw, c)
            raw.nums.append(tvalue)
            if state in (T_NNt, T_Nt):
                _check_zone(s)
            return T_NUM_AMPM
        if sep == SEP_SPACE:
            raw.nums.append(tvalue)
            return T_NUM_SPACE
        if sep == SEP_DATE:
            raw.nums.append(tvalue)
            return T_NUM_DATESEP
        if sep == SEP_DATE_OR_OFFSET:
            raw.nums.append(tvalue)
            if _STATES[state][T_NUM_DATESEP] == ERROR and _STATES[state][T_NUM_SPACE] > ERROR:
                s.index, s.current = before, ch_before
                return T_NUM_SPACE
            return T_NUM_DATESEP
        if sep == SEP_TIME:
            raw.nums.append(tvalue)
            return T_NUM_TIMESEP
        if sep == SEP_LOCAL_TIME_MARK:
            raw.nums.append(tvalue)
            return T_NUM_LOCAL_MARK
        raise _Fail
    if ttype == DAY_OF_WEEK:
        if raw.day_of_week != -1:
            raise _Fail
        raw.day_of_week = tvalue
        return T_DAY_OF_WEEK
    if ttype == MONTH:
        if raw.month != -1:
            raise _Fail
        sep, before, ch_before = s.separator_token(c)
        if sep == SEP_END:
            dtt = T_MONTH_END
        elif sep == SEP_SPACE:
            dtt = T_MONTH_SPACE
        elif sep == SEP_DATE:
            dtt = T_MONTH_DATESEP
        elif sep == SEP_DATE_OR_OFFSET:
            if _STATES[state][T_MONTH_DATESEP] == ERROR and _STATES[state][T_MONTH_SPACE] > ERROR:
                s.index, s.current = before, ch_before
                dtt = T_MONTH_SPACE
            else:
                dtt = T_MONTH_DATESEP
        else:
            raise _Fail
        raw.month = tvalue
        return dtt
    if ttype == ERA:
        if tvalue != 1:
            raise _Fail
        return T_ERA
    if ttype == TIME_ZONE:
        raise TimeZoneUnsupported
    if ttype == END_OF_STRING:
        return T_END
    if ttype in (DATE_WORD, IGNORABLE):
        return T_UNK
    if ttype in (AM, PM):
        if raw.time_mark != _TM_NOT_SET:
            raise _Fail
        raw.time_mark = _TM_AM if ttype == AM else _TM_PM
        return T_UNK
    # An unknown token: a word fails; "+" or "-" may start an offset; "#...#" and trailing NULs are allowed.
    if s.current.isalpha():
        raise _Fail
    if s.current in "+-" and s.index + 1 < s.length and "0" <= s.value[s.index + 1] <= "9":
        raise TimeZoneUnsupported
    if _valid_punctuation(s):
        return T_UNK
    raise _Fail


def _valid_punctuation(s: _Str) -> bool:
    ch = s.value[s.index]
    if ch == "#":
        start = end = False
        for x in s.value:
            if x == "#":
                if start:
                    if end:
                        return False
                    end = True
                else:
                    start = True
            elif x == "\0":
                if not end:
                    return False
            elif not x.isspace() and (not start or end):
                return False
        if not end:
            return False
        s.get_next()
        return True
    if ch == "\0":
        if any(x != "\0" for x in s.value[s.index + 1:]):
            return False
        s.index = s.length
        return True
    return False


def _iso8601(s: _Str, raw: _Raw) -> dt.datetime:
    """ParseISO8601: a date read as year, number, number, then "T" and the time."""
    s.index -= 1
    second, part = 0, 0.0
    s.skip_white_spaces()
    hour = s.parse_digits(2)
    s.skip_white_spaces()
    if hour is None or not s.match(":"):
        raise _Fail
    s.skip_white_spaces()
    minute = s.parse_digits(2)
    if minute is None:
        raise _Fail
    s.skip_white_spaces()
    if s.match(":"):
        s.skip_white_spaces()
        sec = s.parse_digits(2)
        if sec is None:
            raise _Fail
        second = sec
        if s.match(".") or s.match(","):
            part, digits = _fraction(s)
            if not digits:
                raise _Fail
            s.index -= 1
        s.skip_white_spaces()
    if s.get_next():
        ch = s.value[s.index]
        if ch in "+-zZ":
            raise TimeZoneUnsupported
        s.index -= 1
        s.skip_white_spaces()
        if s.match("#"):
            if not _valid_punctuation(s):
                raise _Fail
            s.skip_white_spaces()
        if s.match("\0") and not _valid_punctuation(s):
            raise _Fail
        if s.get_next():
            raise _Fail
    n = raw.nums
    if not (_valid_day(raw.year, n[0], n[1]) and hour < 24 and minute < 60 and second < 60):
        raise _Fail
    return dt.datetime(raw.year, n[0], n[1], hour, minute, second) + _ticks(part)


def _ticks(fraction: float) -> dt.timedelta:
    """A fraction of a second as 100 ns ticks (rounded half to even, as Math.Round), kept to the microsecond."""
    return dt.timedelta(microseconds=round(fraction * 10_000_000) // 10)


def try_parse(text: str, culture: str) -> Optional[dt.datetime]:
    """DateTime.TryParse(text, culture, DateTimeStyles.None) for a culture in CULTURE_DATA: the value, or None when
    .NET can't read it. Raises Unsupported for text naming a zone or an offset, and for what isn't measured."""
    c = CULTURE_DATA[culture]
    if not text:
        return None
    try:
        return _parse(text, c)
    except _Fail:
        return None


def _parse(text: str, c: DateCulture) -> dt.datetime:
    state, terminal = BEGIN, False
    raw, r = _Raw(), _Result()
    s = _Str(text)
    s.get_next()
    while True:
        dtt = _lex(state, s, raw, r, c)
        if dtt != T_UNK:
            if dtt == T_NUM_LOCAL_MARK:
                if state in (D_YNd, D_YN):
                    return _iso8601(s, raw)
                raise _Fail
            state = _STATES[state][dtt]
            if state == ERROR:
                raise _Fail
            if state > ERROR:
                _terminal(state, r, raw, c)
                terminal = True
                state = BEGIN
        if dtt in (T_END, T_NUM_END, T_MONTH_END):
            break
    if not terminal:
        raise _Fail
    if raw.time_mark == _TM_AM:
        if not 0 <= r.hour <= 12:
            raise _Fail
        r.hour = 0 if r.hour == 12 else r.hour
    elif raw.time_mark == _TM_PM:
        if not 0 <= r.hour <= 23:
            raise _Fail
        r.hour = r.hour + 12 if r.hour < 12 else r.hour
    # CheckDefaultDateTime: a missing year is this year, a missing month or day the first, and no date at all today.
    if r.year == -1 or r.month == -1 or r.day == -1:
        now = _now()
        if r.month == -1 and r.day == -1:
            if r.year == -1:
                r.year, r.month, r.day = now.year, now.month, now.day
            else:
                r.month = r.day = 1
        else:
            r.year = now.year if r.year == -1 else r.year
            r.month = 1 if r.month == -1 else r.month
            r.day = 1 if r.day == -1 else r.day
    if not (_valid_day(r.year, r.month, r.day) and 0 <= r.hour < 24 and 0 <= r.minute < 60 and 0 <= r.second < 60):
        raise _Fail
    value = dt.datetime(r.year, r.month, r.day, r.hour, r.minute, r.second)
    if raw.fraction > 0:
        value += _ticks(raw.fraction)
    if raw.day_of_week != -1 and raw.day_of_week != (value.weekday() + 1) % 7:
        raise _Fail
    return value
