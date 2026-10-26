"""
How Power BI Desktop's storage engine adds a column of Doubles (issue #243)
===========================================================================

A sum of Doubles depends on the order of its additions, and Desktop's storage
engine (VertiPaq) adds along the column's PHYSICAL layout: its segments, and in
each segment its runs -- an RLE run (one value repeated) or a bit-packed run
(one value per row). The decoder hands the layout over with the rows
(vertipaq_decoder.read_table_from_abf ``storage``); this module turns it into
the sequence of IEEE additions Desktop makes. Every rule below was measured
against Power BI Desktop 2.152's answers, never read from its binaries (the
clean-room rule, #198):

1. One scan walks PIECES: the union of the run boundaries of every column the
   scan reads -- the aggregated columns, the columns of the filters on the
   scanned table (a filter on another table reaches it through the scanned
   table's join column), and the columns of the other aggregations of the same
   table it is FUSED with (``cut`` here). Every piece is cut into chunks of
   16,384 rows counted from the piece's start (storage positions, not
   selected ranks). In a piece where every column of the aggregation itself
   (``own``) is RLE a chunk adds ``value x k``, k its selected rows; in any
   other piece a chunk's selected values are folded left to right from 0.0;
   each chunk total is added to the accumulator. Blank rows are skipped.
   An RLE piece of 16,384 rows or fewer is one product (a_run_0 below); a
   longer one is several (build_b243b.py, Contoso V_Fact, whose RLE runs are
   up to a segment long: cs_cur, cs_salesquantity, cs_discountquantity; one
   product per run gave none of their answers; #346).
   build_b243.py (one segment, one bit-packed run): b_first_100000 = 83616
   (chunk 0 swallows its 16,383 ones), b_mid_200000 = 185316, f_g8_200000
   0.933166414499283 -- 78/78. Agents FactSales (Desktop-saved, 230 runs of
   SalesAmount): a_total 0.215398073196411, a_run_0 (2999.9 x 518 once)
   0.199999999953434; build_b243b.py: a filter cuts (af_sk_all
   0.215399503707886, af_dimstore through the relationship, x_filter), the RLE
   rule is the aggregation's own (af_run_tc, fu_run0_pk), aggregations fuse
   (fu_sa_tc 0.445394039154053, fu_meas, fu_pm; #339), COUNTROWS
   cuts nothing (fu_sa_cnt), another filter is another scan (fu_sa_tc_f).
2. A GROUPED scan (a query's group-by, an iteration's rows; the columns
   ``group``) adds into one accumulator per group. A piece where every group
   column is RLE belongs to one group and adds as in 1 (its chunk totals
   straight into the group's accumulator, as a_total shows for an ungrouped
   scan; Agents cannot tell this from adding the piece's total, and a
   group-RLE piece past 16,384 rows is not probed); in any other piece
   each selected row is added to its group's accumulator on its own, an RLE
   run of the aggregated column row by row too. build_b243b.py gb_ch 4/4,
   gb_store 306/306, gb_emp_mtd 262/262 (DimEmployee through DimStore: the
   fact's StoreKey decides); no other model of the many tried matched
   (#340).
3. Segments: each segment's terms go to a partial of its own, and the partials
   are added in segment order. Desktop itself varies here: Contoso's 13
   segments (build_b243b.py cs_*) gave different last bits in two sessions,
   and even two spellings of one query in one session (cs_pk_r1 vs cs_pk_r2),
   so its segment jobs finish in no fixed order. With the chunks of rule 1
   in RLE pieces too, every one of the 32 answers of the two sessions is the
   segments' partials added in some order -- each partial is right -- and
   segment order gives 19 of them (one product per RLE run gave 15). Other
   fixed orders do no better beyond chance (of the 28 answers that are not
   cs_pk_avgx or cs_fused, segment order 16, the last segment first 18,
   reverse order 13): the engine takes one fixed sequence of IEEE additions,
   the partials in segment order (parts_fold; #341).
4. A value-encoded column (DictionaryStorage.Type 2) adds its stored integers
   exactly and divides by its Magnitude once (Root Cause rc_sum_profit
   0.0216999999829568, rc_sumx_profit, rc_sum_profit_f); an expression over
   it is a column of Doubles like any other (rc_sumx_mul 0.723870001267642).
   Whole numbers add exactly in any order.
5. An expression that reads no column (SUMX(T, 0.1)) has no runs of its
   own: each chunk of 16,384 rows of a piece adds the value times its
   selected rows, and the chunk totals go to the accumulator as in 1.
   build_b243c.py x_const: SUMX(FactSales, 0.1) - 19999 over Agents' 199,999
   rows is 0.899999999997817 -- 12 chunks of 1638.4 and one of 339.1 --
   where the rows folded one by one give 0.899999989455 and 0.1 x 199,999
   0.9 (#343). In a grouped scan too, also in a group-bit-packed
   piece (not rule 2): build_b243d.py k_const_gb, SUMMARIZECOLUMNS(
   FactSales[channelKey], "v", <SUMX(FactSales, 0.1)'s fraction>), gives
   channels 1, 3 and 4 as each chunk's value x the group's rows in it
   (0.800000000001091, 0.500000000000227, 0.300000000001546); channel 2's
   0.299999999999272 is matched by no chunking tried (the rule gives
   0.299999999999727).

A table without a layout is one segment holding one bit-packed run per
column. That is measured for the tables our encoder writes (one segment, one
bit-packed run in row order: the layout Desktop reads them with,
build_b243.py 78/78). For a calculated table, a calculated column the engine
adds to a decoded table and a hand-built table it is an assumption: Desktop
compresses those itself, and no battery has probed them yet.
"""

import bisect
import collections
import threading
from itertools import accumulate, chain
from typing import Any, Optional

# rows per chunk of a bit-packed piece (b_first_100000 = 83616 = 100000 - 16384)
SE_CHUNK = 16384

_NUM = (int, float)


def seq_sum(values) -> Any:
    """``values`` added left to right, in IEEE: the last item of
    itertools.accumulate (in C). Never the builtin sum(), which is
    compensated from Python 3.12 (issue #246). 0 for no values."""
    d = collections.deque(accumulate(values), maxlen=1)
    return d[0] if d else 0


def _clean(values) -> bool:
    """Is every value a number (int, float or bool) -- no BLANK, no text?"""
    return all(issubclass(t, _NUM) for t in set(map(type, values)))


class Pieces:
    """The pieces one scan walks over a table of ``n`` rows (see the module
    docstring): ``starts`` ascending from 0 (a piece ends where the next one
    starts, the last at ``n``), ``own_rle`` / ``grp_rle`` 1 where every own /
    group column is RLE in the piece, ``seg`` the segment of each piece, and
    with a group, ``grp_next``: for a piece whose group column is
    bit-packed, the first piece after it that is not (or starts another
    segment) -- a grouped scan adds the rows of all those pieces one by one
    into the group's accumulator (rule 2), one fold."""

    __slots__ = ('n', 'starts', 'own_rle', 'grp_rle', 'seg', 'grp_next')

    def __init__(self, n: int, starts: list, own_rle: bytes, grp_rle: Optional[bytes], seg: list):
        self.n = n
        self.starts = starts
        self.own_rle = own_rle
        self.grp_rle = grp_rle
        self.seg = seg
        nxt: Optional[list] = None
        if grp_rle is not None:
            npieces = len(starts)
            nxt = [0] * npieces
            end = npieces
            for i in range(npieces - 1, -1, -1):
                if grp_rle[i]:
                    nxt[i] = i + 1
                    end = i
                    continue
                if i + 1 < npieces and seg[i + 1] != seg[i]:
                    end = i + 1
                nxt[i] = end
        self.grp_next = nxt

    def __len__(self) -> int:
        return len(self.starts)

    def weight(self) -> int:
        """About how many units of the model cache it holds (issue #229):
        two lists of one item per piece (three with a group), and the flags."""
        n = len(self.starts)
        return 2 * n + (n if self.grp_next is not None else 0) + (n >> 5) + 1


def storage_rows(tbl: dict) -> int:
    """The number of rows the layout of ``tbl`` describes: its own rows, or
    for a bucket copy of evaluate_per_dimension (``storage_index``: the
    parent row of each of its rows) the parent's, ``storage_n``."""
    if 'storage_index' in tbl:
        n: int = tbl.get('storage_n', 0)
        return n
    return len(tbl.get('rows') or ())


def _layout(tbl: dict) -> Optional[dict]:
    """``tbl['storage']`` when it describes the table's rows, else None."""
    st = tbl.get('storage')
    if not isinstance(st, dict):
        return None
    if st.get('rows') != storage_rows(tbl):
        return None
    return st


def segment_starts(tbl: dict) -> list:
    """The first row of each segment of ``tbl``."""
    n = storage_rows(tbl)
    st = _layout(tbl)
    segs = st.get('segments') if st is not None else None
    if not segs or sum(segs) != n:
        return [0]
    out = list(accumulate(segs, initial=0))[:-1]
    return out or [0]


def column_runs(tbl: dict, name: str) -> Optional[tuple]:
    """``(starts, rle)`` of the column's runs, or None: no layout (one
    bit-packed run per segment)."""
    st = _layout(tbl)
    if st is None:
        return None
    col = (st.get('columns') or {}).get(name)
    if col is None:
        return None
    return col['starts'], col['rle']


def column_encoding(tbl: dict, name: str) -> tuple:
    """``(encoding, magnitude)`` of the column: ('value', Magnitude) for a
    value-encoded column, ('hash', 1.0) otherwise."""
    st = _layout(tbl)
    col = (st.get('columns') or {}).get(name) if st is not None else None
    if col is None:
        return 'hash', 1.0
    return col.get('enc', 'hash'), col.get('mag', 1.0) or 1.0


def dictionary_raw(tbl: dict, name: str) -> Optional[list]:
    """The dictionary of a hash-encoded column as the decoder left it in
    the layout (one object, so a memo can check it by identity), or None
    (see dictionary_values)."""
    st = _layout(tbl)
    col = (st.get('columns') or {}).get(name) if st is not None else None
    if col is None:
        return None
    raw: Optional[list] = col.get('dict')
    return raw


def dictionary_values(tbl: dict, name: str) -> Optional[list]:
    """The values of a hash-encoded column in its dictionary's order (its
    data IDs), as the rows hold them, or None: no dictionary decoded (a
    value-encoded column, a table without a layout). A DateTime, Decimal or
    Boolean dictionary is rebuilt on every call: callers memoize it
    (DAXEngine._fe_dictionary_rank, checked against dictionary_raw)."""
    raw = dictionary_raw(tbl, name)
    if raw is None:
        return None
    st = _layout(tbl)
    if st is None:
        return None
    dtype = st['columns'][name].get('dtype')
    if dtype in ('String', 'Int64', 'Float64'):
        held: list = raw
        return held
    from pbix_mcp.formats.vertipaq_decoder import _reconstruct_value
    return [_reconstruct_value(v, dtype, False) for v in raw]


def build_pieces(tbl: dict, own: tuple, cut: tuple = (), group: tuple = ()) -> Pieces:
    """The pieces of a scan of ``tbl`` aggregating columns ``own``, cut by the
    columns ``cut`` (filters, fused aggregations) and grouped by ``group``
    (column names of tbl)."""
    n = storage_rows(tbl)
    seg_starts = segment_starts(tbl)
    names = list(dict.fromkeys(tuple(own) + tuple(cut) + tuple(group)))
    runs = {c: column_runs(tbl, c) for c in names}
    bounds = set(seg_starts)
    for r in runs.values():
        if r is not None:
            bounds.update(r[0])
    starts = sorted(b for b in bounds if 0 <= b < n) or [0]
    if starts[0] != 0:
        starts.insert(0, 0)

    def flags(cols) -> bytes:
        out = bytearray(b'\x01') * len(starts)
        for c in cols:
            r = runs.get(c)
            if r is None:
                # no layout: every row bit-packed
                return bytes(len(starts))
            cs, rle = r
            j = 0
            last = len(cs) - 1
            for i, s in enumerate(starts):
                while j < last and cs[j + 1] <= s:
                    j += 1
                if not rle[j]:
                    out[i] = 0
        return bytes(out)

    own_rle = flags(own) if own else bytes(len(starts))
    grp_rle = flags(group) if group else None
    seg = []
    k = 0
    for s in starts:
        while k + 1 < len(seg_starts) and seg_starts[k + 1] <= s:
            k += 1
        seg.append(k)
    return Pieces(n, starts, own_rle, grp_rle, seg)


# The pieces of the buckets of evaluate_per_dimension, which share their
# parent's layout: (id of the layout, rows, own, cut, group) -> (layout,
# pieces), the layout held so its id stays its own. A few entries: one query's
# buckets ask for the same pieces.
_BUCKET_PIECES: 'collections.OrderedDict' = collections.OrderedDict()
_BUCKET_PIECES_MAX = 16
_BUCKET_LOCK = threading.Lock()


def bucket_pieces(tbl: dict, own: tuple, cut: frozenset, group: frozenset) -> Pieces:
    """build_pieces for a bucket copy (``storage_index``), shared by the
    buckets of one parent."""
    st = _layout(tbl)
    key = (id(st), storage_rows(tbl), own, cut, group)
    with _BUCKET_LOCK:
        hit = _BUCKET_PIECES.get(key)
        if hit is not None and hit[0] is st:
            _BUCKET_PIECES.move_to_end(key)
            held: Pieces = hit[1]
            return held
    pieces = build_pieces(tbl, own, tuple(sorted(cut)), tuple(sorted(group)))
    with _BUCKET_LOCK:
        _BUCKET_PIECES[key] = (st, pieces)
        while len(_BUCKET_PIECES) > _BUCKET_PIECES_MAX:
            _BUCKET_PIECES.popitem(last=False)
    return pieces


def _chunk_total(nums: list, const: bool) -> Any:
    """One chunk's total: its values folded left to right from 0.0, or --
    ``const``, an expression that reads no column, every value the same --
    the value times the chunk's count (rule 5)."""
    if const:
        return nums[0] * len(nums)
    return seq_sum(nums)


def scan_sum(pieces: Pieces, positions: Optional[list], vals: list, grouped: bool = False,
             clean: Optional[bool] = None, const: bool = False) -> tuple:
    """``(total, count)`` of the numbers ``vals`` as one scan adds them over
    ``pieces``: ``positions`` the sorted storage positions of the selected
    rows, ``vals[j]`` the value at ``positions[j]`` -- or with ``positions``
    None every row, ``vals[j]`` row j's. ``grouped``: the scan is one group
    of a grouped scan (rule 2). ``total`` is None when no value is a number.
    ``clean``: whether every value is a number, when the caller knows.
    ``const``: the values are an expression that reads no column, so all
    equal; a chunk adds the value times its count (rule 5).

    The loops run in C where they can (accumulate, slices, bisect): the cost
    is about that of gathering the values."""
    n = pieces.n
    starts = pieces.starts
    own_rle = pieces.own_rle
    grp_rle = pieces.grp_rle if grouped else None
    grp_next = pieces.grp_next if grp_rle is not None else None
    seg = pieces.seg
    if clean is None:
        clean = _clean(vals)
    npieces = len(starts)
    B = SE_CHUNK
    m = len(vals)
    if npieces == 1 and positions is not None and not own_rle[0] and m:
        # One bit-packed piece from row 0 (a table without a layout, or one
        # run): the selected rows of one chunk, or a group's rows one by
        # one, are a single fold -- the loop below, without its steps.
        bp_group = grp_rle is not None and not grp_rle[0] and not const
        if bp_group or positions[-1] // B == positions[0] // B:
            nums = vals if clean else [v for v in vals if isinstance(v, _NUM)]
            if not nums:
                return None, 0
            if bp_group:
                return seq_sum(chain((0,), nums)), len(nums)
            return 0 + _chunk_total(nums, const), len(nums)
    parts: list = []
    acc: Any = 0
    count = 0
    cur_seg = -1
    have = False
    j = 0
    pi = 0
    while True:
        if positions is None:
            if pi >= npieces:
                break
        else:
            if j >= m:
                break
            pi = bisect.bisect_right(starts, positions[j]) - 1
        # the piece's end -- in a grouped scan, a piece whose group column is
        # bit-packed ends with the last such piece of its segment: their rows
        # all go one by one into the group's accumulator, one fold (rule 2)
        nxt = pi + 1
        if grp_next is not None and grp_rle is not None and not grp_rle[pi]:
            nxt = grp_next[pi]
        a = starts[pi]
        z = starts[nxt] if nxt < npieces else n
        if positions is None:
            lo, hi = a, z                      # indices into vals
            if lo >= hi:
                pi = nxt
                continue
        else:
            lo = j
            hi = bisect.bisect_left(positions, z, j)
        s = seg[pi]
        if s != cur_seg:
            if have:
                parts.append(acc)
            acc = 0
            have = False
            cur_seg = s
        if grp_rle is not None and not grp_rle[pi] and not const:
            # pieces of several groups: each row on its own (rule 2); a
            # constant chunks as anywhere (rule 5, below)
            chunk = vals[lo:hi]
            nums = chunk if clean else [v for v in chunk if isinstance(v, _NUM)]
            if nums:
                acc = seq_sum(chain((acc,), nums))
                count += len(nums)
                have = True
        elif own_rle[pi]:
            # value x rows, chunk by chunk of 16,384 rows from the piece's
            # start (rule 1)
            v = vals[lo]
            if isinstance(v, _NUM):
                if positions is None:
                    for c in range(lo, hi, B):
                        acc += v * (min(c + B, hi) - c)
                else:
                    q = lo
                    while q < hi:
                        cend = a + ((positions[q] - a) // B + 1) * B
                        r = hi if cend >= z else bisect.bisect_left(positions, cend, q, hi)
                        acc += v * (r - q)
                        q = r
                count += hi - lo
                have = True
        elif positions is None:
            for c in range(lo, hi, B):
                chunk = vals[c:min(c + B, hi)]
                nums = chunk if clean else [v for v in chunk if isinstance(v, _NUM)]
                if nums:
                    acc += _chunk_total(nums, const)
                    count += len(nums)
                    have = True
        else:
            q = lo
            while q < hi:
                cend = a + ((positions[q] - a) // B + 1) * B
                r = hi if cend >= z else bisect.bisect_left(positions, cend, q, hi)
                chunk = vals[q:r]
                nums = chunk if clean else [v for v in chunk if isinstance(v, _NUM)]
                if nums:
                    acc += _chunk_total(nums, const)
                    count += len(nums)
                    have = True
                q = r
        if positions is None:
            pi = nxt
        else:
            j = hi
    if have:
        parts.append(acc)
    if not count:
        return None, 0
    return (parts[0] if len(parts) == 1 else seq_sum(parts)), count


def exact_total(nums: list, enc: str, mag: float) -> Any:
    """The total of a column's numbers where the order cannot matter, or
    None: whole numbers (and bools) add exactly as Python ints; a
    value-encoded column's numbers are its stored integers over its
    Magnitude, added exactly and divided once (rule 4)."""
    kinds = set(map(type, nums))
    if kinds <= {int, bool}:
        return sum(nums)
    if enc == 'value' and kinds <= {float, int, bool}:
        scale = float(mag).__mul__
        return sum(map(round, map(scale, nums))) / mag
    return None
