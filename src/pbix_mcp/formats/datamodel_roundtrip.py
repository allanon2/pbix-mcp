"""
DataModel round-trip compression/decompression for PBIX files.

PBIX ZIP files contain a 'DataModel' entry that is XPress9 compressed.
This module handles decompressing it to raw ABF bytes and re-compressing
modified ABF bytes back into the DataModel format.

Formats supported:
  - Single-threaded XPress9 (signature: "This backup was created using XPress9 compression.")
  - Multi-threaded XPress9  (signature: "This backup was created using multithreaded XPrs9.")
  - Uncompressed ABF (starts with STREAM_STORAGE_SIGNATURE)

Every count and size in a compressed stream's chunk table comes from the
file, so the whole table is checked against the stream and the bounds below
before the first chunk is decoded (#213).
"""

import concurrent.futures
import struct
from array import array

from xpress9 import Xpress9

from pbix_mcp.errors import DataModelCompressionError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SINGLE_THREAD_SIGNATURE = "This backup was created using XPress9 compression."
MULTI_THREAD_SIGNATURE = "This backup was created using multithreaded XPrs9."

# 50 UTF-16-LE chars = 100 bytes + 2 null bytes padding = 102 bytes total
HEADER_SINGLE = SINGLE_THREAD_SIGNATURE.encode("utf-16-le") + b"\x00\x00"  # 102 bytes
HEADER_MULTI = MULTI_THREAD_SIGNATURE.encode("utf-16-le") + b"\x00\x00"    # 102 bytes

STREAM_STORAGE_SIGNATURE = (
    b"\xff\xfe"
    + "STREAM_STORAGE_SIGNATURE_)!@#$%^&*(".encode("utf-16le")
)

# Chunk size used when compressing (2 MiB, same as Power BI)
COMPRESS_CHUNK_SIZE = 2 * 1024 * 1024

# What a chunk table may declare (#213). The codec commits a chunk's declared
# size before it decodes anything (measured: asking for 256 MiB from a 4 KB
# payload committed the 256 MiB) and hands back exactly that many bytes or
# fails ("Expected X, got Y"), so the declared sizes ARE the output: a few
# hundred bytes of header could ask for 4 GiB per chunk, or 2^64 groups.
# Measured envelope, 610 distinct compressed streams (test_samples and the
# scratch corpus, deduplicated by SHA-1): chunks of 34,728 to 2,097,152
# bytes, at most 33 per stream, every stream ending exactly at its last
# chunk; the largest model declares 67,616,768 bytes; ratios 4.4:1 median,
# 31.4:1 max, 4.2:1 among models over 64 MiB. A model PBIXBuilder writes
# from one text column of near-identical 16 KB values comes to 92:1, while
# XPress9 packs 2 MiB of zeros into 186 bytes (11,275:1).
MAX_CHUNK_UNCOMPRESSED = 16 * 1024 * 1024  # one chunk: 8x the 2 MiB Power BI and we write
MAX_DECOMPRESSED_BYTES = 16 * 1024 ** 3    # all chunks: 8x the zip guard's 2 GiB member cap
MAX_RATIO = 1000                           # all chunks per stream byte (11x that 92:1 model),
RATIO_FLOOR = 64 * 1024 * 1024             # checked only past this total

_SIGNATURE_LEN = 102                         # both compressed formats open with it
_MULTI_TABLE_START = _SIGNATURE_LEN + 5 * 8  # then multi-threaded has five u64 fields
_CHUNK_HEADER = 8                            # u32 uncompressed size, u32 payload size
_END_OF_TABLE = bytes(_CHUNK_HEADER)         # single-threaded: an all-zero header ends it
_MAX_WORKERS = 16                            # threads decoding session groups
_MAX_GROUPS_IN_FLIGHT = 32                   # groups queued on them at one time


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_format(dm_bytes: bytes) -> str:
    """Return 'single_threaded', 'multi_threaded', or 'uncompressed'."""
    if dm_bytes[:72] == STREAM_STORAGE_SIGNATURE or STREAM_STORAGE_SIGNATURE in dm_bytes[:72]:
        return "uncompressed"
    try:
        sig = dm_bytes[:102].decode("utf-16-le", errors="ignore")
    except Exception:
        sig = ""
    if SINGLE_THREAD_SIGNATURE in sig:
        return "single_threaded"
    if MULTI_THREAD_SIGNATURE in sig:
        return "multi_threaded"
    raise ValueError(
        "Unknown DataModel format: first 102 bytes do not match any known signature."
    )


class _ChunkTable:
    """A compressed stream's chunk table, every entry checked: chunk ``i``
    decodes the ``length[i]`` payload bytes at ``at[i]`` into ``size[i]``
    bytes, and session group ``g`` (one codec context, decoded in order) is
    chunks ``first[g]`` up to ``first[g + 1]``.

    Flat arrays rather than a tuple per chunk: a hostile table of minimal
    9-byte entries costs about 20 bytes per entry here instead of ~120."""

    __slots__ = ("size", "at", "length", "first")

    def __init__(self) -> None:
        self.size = array("I")
        self.at = array("Q")
        self.length = array("I")
        self.first = array("Q", [0])

    @property
    def group_count(self) -> int:
        return len(self.first) - 1

    def chunks_of(self, g: int) -> range:
        return range(self.first[g], self.first[g + 1])

    def payload(self, dm: bytes, i: int) -> bytes:
        return dm[self.at[i] : self.at[i] + self.length[i]]

    def add_chunk(self, dm: bytes, off: int) -> int:
        """Check the entry at ``off`` against the stream and the per-chunk
        bound, record it, and return the offset after it."""
        if off + _CHUNK_HEADER > len(dm):
            raise DataModelCompressionError(
                f"DataModel stream ends inside the chunk header at byte "
                f"{off:,} of {len(dm):,} (truncated file?)")
        us, cs = struct.unpack_from("<II", dm, off)
        if us == 0 or cs == 0:
            # the codec answers b"" for any payload asked for 0 bytes, so an
            # empty entry decodes nothing and costs a decode task each
            raise DataModelCompressionError(
                f"DataModel chunk at byte {off:,} is empty ({us:,} bytes "
                f"uncompressed from a {cs:,}-byte payload)")
        if us > MAX_CHUNK_UNCOMPRESSED:
            raise DataModelCompressionError(
                f"DataModel chunk at byte {off:,} declares {us:,} bytes "
                f"uncompressed; at most {MAX_CHUNK_UNCOMPRESSED:,} are accepted")
        at = off + _CHUNK_HEADER
        end: int = at + cs
        if end > len(dm):
            raise DataModelCompressionError(
                f"DataModel chunk at byte {off:,} has a {cs:,}-byte payload "
                f"that runs past the end of the {len(dm):,}-byte stream "
                f"(truncated file?)")
        self.size.append(us)
        self.at.append(at)
        self.length.append(cs)
        return end

    def end_group(self) -> None:
        self.first.append(len(self.size))


def _read_chunk_table(dm: bytes, fmt: str) -> _ChunkTable:
    """The checked chunk table of a compressed stream -- the one parser every
    reader goes through. Refuses with DataModelCompressionError, before any
    chunk is decoded, a table the stream cannot hold or the module bounds do
    not allow."""
    table = _ChunkTable()
    if fmt == "single_threaded":
        # One session: entries to the end of the stream. An all-zero header
        # also ends it (where the reader always stopped). Any other entry cut
        # short or running past the end is a truncated file, which the reader
        # used to stop at silently, handing back a cut-short ABF.
        off = _SIGNATURE_LEN
        while off < len(dm) and dm[off : off + _CHUNK_HEADER] != _END_OF_TABLE:
            off = table.add_chunk(dm, off)
        if len(table.size):
            table.end_group()
    elif fmt == "multi_threaded":
        if len(dm) < _MULTI_TABLE_START:
            raise DataModelCompressionError(
                f"multi-threaded DataModel stream is {len(dm):,} bytes, "
                f"shorter than its {_MULTI_TABLE_START}-byte header")
        (main_per_group, prefix_per_group, prefix_groups, main_groups,
         _chunk_size) = struct.unpack_from("<5Q", dm, _SIGNATURE_LEN)
        # Two rectangles, prefix then main, each `groups` session groups of
        # `per_group` chunks. Every entry takes at least a header, so the
        # declared PRODUCTS (Python ints, no wrap-around) must fit in the
        # bytes left; that bounds the loops below by the stream's length.
        declared = prefix_groups * prefix_per_group + main_groups * main_per_group
        room = (len(dm) - _MULTI_TABLE_START) // _CHUNK_HEADER
        if declared > room:
            raise DataModelCompressionError(
                f"DataModel header declares {declared:,} chunks; the "
                f"{len(dm):,}-byte stream has room for at most {room:,}")
        off = _MULTI_TABLE_START
        for per_group, groups in ((prefix_per_group, prefix_groups),
                                  (main_per_group, main_groups)):
            if not per_group:
                continue  # groups of no chunks hold nothing, whatever their count
            for _ in range(groups):
                for _ in range(per_group):
                    off = table.add_chunk(dm, off)
                table.end_group()
    else:
        raise ValueError(f"no chunk table in format {fmt!r}")

    total = sum(table.size)
    if total > MAX_DECOMPRESSED_BYTES:
        raise DataModelCompressionError(
            f"DataModel chunks declare {total:,} bytes uncompressed; "
            f"at most {MAX_DECOMPRESSED_BYTES:,} are accepted")
    if total > max(RATIO_FLOOR, MAX_RATIO * len(dm)):
        raise DataModelCompressionError(
            f"DataModel chunks declare {total:,} bytes uncompressed from a "
            f"{len(dm):,}-byte stream, over {MAX_RATIO:,}:1")
    return table


def _decompress_chunk_group(dm: bytes, table: _ChunkTable, g: int) -> list[bytes]:
    """Decode session group ``g`` with one Xpress9 context: its chunks chain
    one LZ77 window, so they decode in order, in one context."""
    xp = Xpress9()
    try:
        parts = []
        for i in table.chunks_of(g):
            try:
                parts.append(xp.decompress(table.payload(dm, i), table.size[i]))
            except ValueError as err:  # the codec's "Decompression failed: ..."
                raise DataModelCompressionError(
                    f"DataModel chunk at byte {table.at[i] - _CHUNK_HEADER:,} "
                    f"(session group {g + 1:,} of {table.group_count:,}) did "
                    f"not decode: {err}") from err
        return parts
    finally:
        del xp


def _decompress_groups(dm: bytes, table: _ChunkTable) -> list[bytes]:
    """Every chunk decoded, in stream order.

    Session groups are independent, so several decode at once -- but on at
    most _MAX_WORKERS threads with at most _MAX_GROUPS_IN_FLIGHT queued, the
    next group submitted as one finishes, and the first failure cancels the
    rest. The pool used to take every group up front and decode all of them
    even after one had failed. Hybrid-reuse containers declare one group per
    tail chunk, so the count can reach the hundreds on a large model: it is a
    GROUPING, not a concurrency requirement."""
    n = table.group_count
    if n <= 1:
        return _decompress_chunk_group(dm, table, 0) if n else []
    decoded: list[list[bytes] | None] = [None] * n
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(n, _MAX_WORKERS))
    try:
        pending: dict[concurrent.futures.Future[list[bytes]], int] = {}
        queued = 0
        while queued < n or pending:
            while queued < n and len(pending) < _MAX_GROUPS_IN_FLIGHT:
                pending[pool.submit(_decompress_chunk_group, dm, table, queued)] = queued
                queued += 1
            finished, _ = concurrent.futures.wait(
                pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in finished:
                decoded[pending.pop(fut)] = fut.result()
    finally:
        # after a failure: drop what is still queued, let the running few end
        pool.shutdown(wait=True, cancel_futures=True)
    out: list[bytes] = []
    for parts in decoded:
        out.extend(parts or ())
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decompress_datamodel(dm_bytes: bytes) -> bytes:
    """
    Decompress the raw DataModel bytes extracted from a PBIX ZIP file.

    Parameters
    ----------
    dm_bytes : bytes
        Raw bytes of the ``DataModel`` entry inside the PBIX ZIP archive.

    Returns
    -------
    bytes
        The decompressed ABF (Analysis Backup Format) binary blob.

    Raises
    ------
    DataModelCompressionError
        The chunk table does not fit the stream or declares more than the
        module bounds allow (checked before anything is decoded), or a chunk
        does not decode.
    """
    fmt = _detect_format(dm_bytes)

    if fmt == "uncompressed":
        return bytes(dm_bytes)

    table = _read_chunk_table(dm_bytes, fmt)
    # one copy into the result; extending a bytearray and then taking
    # bytes() of it made two (peak ~3x the ABF, now ~2x)
    return b"".join(_decompress_groups(dm_bytes, table))


def _compress_single_threaded(abf_bytes: bytes, chunk_size: int) -> bytes:
    """Today's primary path: one XPress9 session over uniform chunks."""
    parts: list[bytes] = []
    parts.append(HEADER_SINGLE)  # 102-byte header

    xp = Xpress9()
    try:
        offset = 0
        total = len(abf_bytes)
        while offset < total:
            end = min(offset + chunk_size, total)
            raw_chunk = abf_bytes[offset:end]
            # max_compressed_size: worst case is input + overhead
            max_compressed = len(raw_chunk) + (len(raw_chunk) // 4) + 65536
            compressed_chunk = xp.compress(raw_chunk, max_compressed)
            uncompressed_size = len(raw_chunk)
            compressed_size = len(compressed_chunk)
            parts.append(struct.pack("<I", uncompressed_size))
            parts.append(struct.pack("<I", compressed_size))
            parts.append(compressed_chunk)
            offset = end
    finally:
        del xp

    return b"".join(parts)


def _common_prefix_len(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    lo, step = 0, 1 << 20
    # coarse block scan, then byte-exact within the mismatching block
    while lo < n and a[lo : lo + step] == b[lo : lo + step]:
        lo += step
    hi = min(lo + step, n)
    while lo < hi and a[lo] == b[lo]:
        lo += 1
    return lo


def _parse_session_groups(dm_bytes: bytes) -> list[list[tuple[int, bytes]]]:
    """The stream's chunks partitioned into SESSION groups — the units a
    decoder context consumes whole. A single-threaded stream is one session;
    a multi-threaded stream has one session per thread-group. Read through
    the same checked chunk table as decompress_datamodel, so the save path
    (_compress_hybrid_reuse) never loops over a count the stream cannot hold."""
    fmt = _detect_format(dm_bytes)
    if fmt == "uncompressed":
        raise ValueError(f"no session groups in format {fmt!r}")
    table = _read_chunk_table(dm_bytes, fmt)
    return [[(table.size[i], table.payload(dm_bytes, i)) for i in table.chunks_of(g)]
            for g in range(table.group_count)]


def _compress_hybrid_reuse(abf_bytes: bytes, original_dm: bytes,
                           chunk_size: int) -> bytes:
    """Multi-threaded container that REUSES the original stream's unchanged
    session prefix verbatim and re-encodes only the changed tail.

    Why this shape ('Compression failed or not effective'): the xpress9
    binding's encoder cannot emit incompressible chunks (its output lands a
    few hundred bytes OVER the input and the wrapper raises), while Power
    BI's own encoder stores them slightly UNDER — so a full re-encode of a
    real-world model fails on already-compressed VertiPaq chunks. Those
    chunks are almost always in the UNCHANGED part of a metadata edit (the
    sqlite lives near the end of the ABF), so reusing them verbatim
    sidesteps the encoder entirely and keeps Power BI's superior window-22
    encoding.

    Format notes, all empirically verified against Desktop-authored streams:
    XPress9 blocks chain their LZ77 window across a session, so only a
    session PREFIX is reusable, in order (a mid-session chunk decoded in a
    fresh context hangs the codec). The multi-threaded container decodes
    each thread-group with an independent codec context — i.e. each group is
    its own session — which legally mixes the original's sessions (reused
    groups, byte-verbatim) with ours (one chunk per main group, each a
    fresh session). Repeated edits stay in this family: a hybrid output's
    groups are reusable by the NEXT edit the same way.
    """
    groups = _parse_session_groups(original_dm)
    if not groups:
        raise ValueError("original stream has no parsable session groups")
    orig_plain = decompress_datamodel(original_dm)
    prefix_len = _common_prefix_len(bytes(orig_plain), abf_bytes)

    # Reusable = leading WHOLE groups inside the unchanged prefix; within
    # the first group that doesn't fully fit, a chunk-prefix is still a
    # valid (truncated) session.
    reused: list[list[tuple[int, bytes]]] = []
    cum = 0
    for gi, g in enumerate(groups):
        g_size = sum(u for u, _ in g)
        if cum + g_size <= prefix_len:
            reused.append(g)
            cum += g_size
            continue
        part: list[tuple[int, bytes]] = []
        for u, payload in g:
            if cum + u <= prefix_len:
                part.append((u, payload))
                cum += u
            else:
                break
        if part:
            reused.append(part)
        break

    # Rectangle assembly: the container has exactly two uniform rectangles.
    # First reused group -> the prefix rectangle (1 thread x its length);
    # further reused groups join the main rectangle only if single-chunk
    # (the shape our own hybrids emit). Anything else is re-encoded.
    prefix_group = reused[0] if reused else []
    reused_singles: list[tuple[int, bytes]] = []
    for g in reused[1:]:
        if len(g) == 1:
            reused_singles.append(g[0])
        else:
            break
    reused_plain = sum(u for u, _ in prefix_group) + \
        sum(u for u, _ in reused_singles)

    # encode the tail: one chunk per thread-group, each its own session
    tail = abf_bytes[reused_plain:]
    tail_chunks: list[tuple[int, bytes]] = []
    o = 0
    while o < len(tail):
        piece = tail[o : o + chunk_size]
        xp = Xpress9()
        try:
            payload = xp.compress(
                piece, len(piece) + (len(piece) // 4) + 65536)
        finally:
            del xp
        tail_chunks.append((len(piece), payload))
        o += len(piece)

    main_chunks = reused_singles + tail_chunks
    parts: list[bytes] = [HEADER_MULTI]
    parts.append(struct.pack("<Q", 1))                      # main chunks/thread
    parts.append(struct.pack("<Q", len(prefix_group)))      # prefix chunks/thread
    parts.append(struct.pack("<Q", 1 if prefix_group else 0))  # prefix threads
    parts.append(struct.pack("<Q", len(main_chunks)))       # main threads
    parts.append(struct.pack("<Q", chunk_size))
    for u, payload in prefix_group + main_chunks:
        parts.append(struct.pack("<I", u))
        parts.append(struct.pack("<I", len(payload)))
        parts.append(payload)
    out = b"".join(parts)

    # Safety: the produced container must round-trip to exactly the input.
    if decompress_datamodel(out) != abf_bytes:
        raise ValueError("hybrid container failed round-trip verification")
    return out


def compress_datamodel(abf_bytes: bytes, chunk_size: int = COMPRESS_CHUNK_SIZE,
                       original_dm: bytes | None = None) -> bytes:
    """
    Compress raw ABF bytes into an XPress9 DataModel stream.

    Primary path: the single-threaded format (one session, uniform chunks) —
    byte-compatible with everything this library ever produced. When the
    encoder fails on incompressible chunks ("Compression failed or not
    effective" — already-compressed VertiPaq data, present in any real-world
    model), two fallbacks engage in order:

    1. With ``original_dm`` (the pre-edit DataModel bytes): a multi-threaded
       container reusing the original's unchanged chunk prefix VERBATIM and
       re-encoding only the changed tail (see :func:`_compress_hybrid_reuse`).
    2. The uncompressed ABF format (``STREAM_STORAGE_SIGNATURE``), which every
       reader of this format — including this library — accepts; the PBIX ZIP
       layer still deflates it.

    Parameters
    ----------
    abf_bytes : bytes
        The raw ABF blob.
    chunk_size : int, optional
        Chunk size for compression.  Defaults to 2 MiB (matching Power BI);
        at most MAX_CHUNK_UNCOMPRESSED, the largest chunk the reader accepts.
    original_dm : bytes, optional
        The DataModel bytes this ABF was decompressed from, when the caller
        is round-tripping an edit. Enables chunk reuse.

    Returns
    -------
    bytes
        DataModel bytes ready to be stored in a PBIX ZIP.
    """
    if not 1 <= chunk_size <= MAX_CHUNK_UNCOMPRESSED:
        # 0 or less never advanced through the ABF (an endless loop of empty
        # chunks); past the bound, decompress_datamodel would refuse the file
        raise ValueError(
            f"chunk_size must be 1 to {MAX_CHUNK_UNCOMPRESSED:,} bytes (the "
            f"largest chunk decompress_datamodel accepts); got {chunk_size:,}")
    try:
        out = _compress_single_threaded(abf_bytes, chunk_size)
    except ValueError as primary_err:
        if "not effective" not in str(primary_err):
            raise

        if original_dm is not None:
            try:
                return _compress_hybrid_reuse(abf_bytes, original_dm, chunk_size)
            except (ValueError, DataModelCompressionError):
                # an original the reader refuses or cannot decode (#213)
                # falls through like a failed re-encode, not out of the save
                pass  # fall through to the uncompressed format

        # Uncompressed ABF: recognized by every reader of this entry
        # (see _detect_format), and what unblocks the file when even the
        # changed chunks are incompressible.
        head = abf_bytes[:72]
        if head.startswith(STREAM_STORAGE_SIGNATURE) or \
                STREAM_STORAGE_SIGNATURE in head:
            return bytes(abf_bytes)

        raise ValueError(
            "XPress9 re-encode failed on an incompressible chunk (the "
            "xpress9 binding cannot emit literal blocks as compactly as "
            "Power BI's encoder), no reusable original stream was provided, "
            "and the ABF blob does not carry STREAM_STORAGE_SIGNATURE for "
            "the uncompressed fallback. Original error: " + str(primary_err)
        ) from primary_err

    # What we write passes our own reader's bounds, so a saved file always
    # reopens here: a header walk, nothing decoded (the hybrid path above
    # decodes its output in full anyway).
    try:
        _read_chunk_table(out, "single_threaded")
    except DataModelCompressionError as err:
        raise DataModelCompressionError(
            "refusing to write a DataModel stream that decompress_datamodel "
            f"would not read back: {err.message}") from err
    return out
