"""Issue #213: a DataModel stream's chunk table is checked against the stream
before the codec runs (reported in PR #209).

decompress_datamodel and _parse_session_groups took every count and size of
the XPress9 container straight from the file:
- a chunk's u32 uncompressed size went to the codec, which commits that
  buffer before decoding (126 bytes of stream asked for 4 GiB);
- the multi-threaded header's u64 group counts were loop bounds, even for
  groups of no chunks (2^40 empty groups grew memory ~80 MB/s, also on the
  save path through _compress_hybrid_reuse);
- a payload running past the end was sliced short without a word, and a
  cut-short header raised struct.error;
- nothing bounded the total (1.6 MB of stream legitimately declared 16 GiB),
  and the thread pool queued every group at once and kept decoding after
  one had failed.

Expected values are not Desktop answers (no DAX here; the container is our
own read path). The bounds come from a census of 610 distinct compressed
streams in test_samples and the scratch corpus (chunks <= 2 MiB, ratio
<= 31.4:1, largest model 64.5 MiB, every stream ending exactly at its last
chunk) and from black-box codec measurements: decompress(payload, n) commits
n bytes up front, returns exactly n bytes or raises ValueError, returns b""
for any payload when n == 0, and packs 2 MiB of zeros into 186 bytes.

Every hostile case runs with the codec swapped out or with long loops
refused, so the old code fails here at once instead of allocating.
"""
import builtins
import json
import os
import struct
import zipfile

import pytest
from xpress9 import Xpress9

import pbix_mcp.formats.datamodel_roundtrip as dr
from pbix_mcp.errors import DataModelCompressionError

pytestmark = pytest.mark.unit

MiB = 1 << 20
SAMPLES = os.environ.get("PBIX_TEST_SAMPLES") or os.path.join(
    os.path.dirname(__file__), "..", "test_samples")


# ---------------------------------------------------------------- streams
def _entry(us: int, payload: bytes, cs: int | None = None) -> bytes:
    """One chunk-table entry: u32 uncompressed size, u32 payload size, payload."""
    return struct.pack("<II", us, len(payload) if cs is None else cs) + payload


def _multi(main_per_group: int, prefix_per_group: int, prefix_groups: int,
           main_groups: int, table: bytes = b"") -> bytes:
    """A multi-threaded stream: signature, the five u64 header fields, table."""
    return (dr.HEADER_MULTI
            + struct.pack("<5Q", main_per_group, prefix_per_group, prefix_groups,
                          main_groups, 2 * MiB)
            + table)


def _session(raw: bytes) -> bytes:
    """``raw`` compressed as a session of its own."""
    xp = Xpress9()
    try:
        return xp.compress(raw, len(raw) + len(raw) // 4 + 65536)
    finally:
        del xp


def _groups_stream(parts: list[bytes], prefix: tuple[bytes, ...] = ()) -> bytes:
    """A multi-threaded stream shaped like our hybrids: an optional one-group
    prefix session, then one single-chunk session group per part."""
    table = b""
    if prefix:
        xp = Xpress9()
        try:
            for raw in prefix:
                table += _entry(len(raw), xp.compress(raw, len(raw) * 2 + 65536))
        finally:
            del xp
    table += b"".join(_entry(len(p), _session(p)) for p in parts)
    return _multi(1, len(prefix), 1 if prefix else 0, len(parts), table)


def _empty_prefix_groups(n: int) -> bytes:
    """One valid main group behind a prefix rectangle of ``n`` groups of no
    chunks each."""
    good = _groups_stream([b"pbix" * 100])
    return good[:102] + struct.pack("<5Q", 1, 0, n, 1, 2 * MiB) + good[142:]


@pytest.fixture()
def no_decode(monkeypatch):
    """The codec still compresses, but any decode fails the test: a refusal
    must come before a buffer is requested."""
    class _CompressOnly:
        def __init__(self):
            self._xp = Xpress9()

        def compress(self, data, size):
            return self._xp.compress(data, size)

        def decompress(self, payload, size):
            raise AssertionError(f"codec asked for {size:,} bytes")
    monkeypatch.setattr(dr, "Xpress9", _CompressOnly)


@pytest.fixture()
def no_long_loops(monkeypatch):
    """A loop over a declared count the stream cannot hold fails at once
    (0.9.145 ran range(2^40) here, until memory ran out)."""
    def _range(*args):
        r = builtins.range(*args)
        if len(r) > 1_000_000:
            raise AssertionError(f"loop over {len(r):,} declared items")
        return r
    monkeypatch.setattr(dr, "range", _range, raising=False)


READERS = [pytest.param(dr.decompress_datamodel, id="decompress"),
           pytest.param(dr._parse_session_groups, id="session_groups")]


# ------------------------------------------------------------ one chunk
@pytest.mark.parametrize("read", READERS)
@pytest.mark.parametrize("stream", [
    pytest.param(dr.HEADER_SINGLE + _entry(0xFFFFFFFF, b"x" * 16), id="single"),
    pytest.param(_multi(1, 0, 0, 1, _entry(0xFFFFFFFF, b"x" * 16)), id="multi"),
])
def test_a_chunk_declaring_4_gib_is_refused_before_the_codec_runs(read, stream, no_decode):
    with pytest.raises(DataModelCompressionError,
                       match="declares 4,294,967,295 bytes uncompressed; "
                             "at most 16,777,216 are accepted"):
        read(stream)


def test_the_chunk_bound_is_inclusive(monkeypatch):
    asked = []

    class _Recorder:
        def decompress(self, payload, size):
            asked.append(size)
            return bytes(size)
    monkeypatch.setattr(dr, "Xpress9", _Recorder)
    monkeypatch.setattr(dr, "MAX_CHUNK_UNCOMPRESSED", 4096)
    assert dr.decompress_datamodel(dr.HEADER_SINGLE + _entry(4096, b"p" * 8)) == bytes(4096)
    with pytest.raises(DataModelCompressionError, match="4,097 bytes"):
        dr.decompress_datamodel(dr.HEADER_SINGLE + _entry(4097, b"p" * 8))
    assert asked == [4096]


@pytest.mark.parametrize("read", READERS)
@pytest.mark.parametrize("stream", [
    pytest.param(_multi(1, 0, 0, 2, _entry(0, b"") * 2), id="multi-0-0"),
    pytest.param(_multi(1, 0, 0, 2, _entry(0, b"x" * 5) * 2), id="multi-0-5"),
    pytest.param(_multi(1, 0, 0, 2, _entry(7, b"") * 2), id="multi-7-0"),
    pytest.param(dr.HEADER_SINGLE + _entry(0, b"x" * 5), id="single-0-5"),
    pytest.param(dr.HEADER_SINGLE + _entry(7, b"") + _entry(7, b"x" * 5), id="single-7-0"),
])
def test_empty_chunks_are_refused(read, stream, no_decode):
    # the codec answers b"" for ANY payload asked for 0 bytes: 1.6 MB of such
    # entries decoded nothing while queuing 200,000 tasks (MemoryError at a
    # 400 MB cap on 0.9.145)
    with pytest.raises(DataModelCompressionError, match="is empty"):
        read(stream)


# -------------------------------------------------- counts against bytes
@pytest.mark.parametrize("read", READERS)
@pytest.mark.parametrize("header", [
    pytest.param((1, 0, 0, 1 << 40), id="main"),
    pytest.param((0, 1, 1 << 40, 0), id="prefix"),
])
def test_a_group_count_the_stream_cannot_hold_is_refused(read, header, no_decode, no_long_loops):
    stream = _multi(*header, _entry(10, b"x" * 10))     # 18 bytes of table: room for 2
    with pytest.raises(DataModelCompressionError,
                       match="declares 1,099,511,627,776 chunks; the 160-byte "
                             "stream has room for at most 2"):
        read(stream)


@pytest.mark.parametrize("read", READERS)
def test_the_product_of_the_counts_is_checked(read, no_decode, no_long_loops):
    # 2^33 groups of 2^33 chunks: each factor alone is a plausible u64
    with pytest.raises(DataModelCompressionError,
                       match="declares 73,786,976,294,838,206,464 chunks"):
        read(_multi(1 << 33, 0, 0, 1 << 33, _entry(10, b"x" * 10)))


def test_groups_of_no_chunks_are_skipped_without_iterating(no_long_loops):
    stream = _empty_prefix_groups(1 << 40)
    assert [len(g) for g in dr._parse_session_groups(stream)] == [1]
    assert dr.decompress_datamodel(stream) == b"pbix" * 100


def test_saving_an_edit_over_such_an_original_falls_back(no_long_loops):
    # compress_datamodel reads the original's session groups when the encoder
    # cannot re-emit an incompressible chunk (random bytes here)
    abf = dr.STREAM_STORAGE_SIGNATURE + os.urandom(3 * MiB)
    out = dr.compress_datamodel(abf, original_dm=_empty_prefix_groups(1 << 40))
    assert dr._detect_format(out) == "uncompressed"
    assert dr.decompress_datamodel(out) == abf


def test_saving_an_edit_over_a_refused_original_falls_back(no_decode):
    # the refusal is a DataModelCompressionError, not a ValueError: the
    # fallback ladder must catch it too, or the save breaks
    abf = dr.STREAM_STORAGE_SIGNATURE + os.urandom(3 * MiB)
    hostile = dr.HEADER_SINGLE + _entry(0xFFFFFFFF, b"x" * 16)
    out = dr.compress_datamodel(abf, original_dm=hostile)
    assert out == abf


# ------------------------------------------------------------ truncation
@pytest.mark.parametrize("read", READERS)
def test_a_multi_threaded_header_cut_short_is_refused(read):
    with pytest.raises(DataModelCompressionError,
                       match="114 bytes, shorter than its 142-byte header"):
        read(dr.HEADER_MULTI + bytes(12))


@pytest.mark.parametrize("read", READERS)
@pytest.mark.parametrize("stream", [
    pytest.param(_multi(1, 0, 0, 1, _entry(10, b"x" * 10, cs=1000)), id="multi"),
    pytest.param(dr.HEADER_SINGLE + _entry(10, b"x" * 10, cs=1000), id="single"),
])
def test_a_payload_running_past_the_end_is_refused(read, stream, no_decode):
    # 0.9.145: _parse_session_groups returned the 10 bytes as the payload,
    # and the single-threaded decoder stopped there, handing back a short ABF
    with pytest.raises(DataModelCompressionError, match="1,000-byte payload that runs past the end"):
        read(stream)


@pytest.mark.parametrize("read", READERS)
@pytest.mark.parametrize("stream,at", [
    # the multi-threaded count (2) passes the room check: 21 bytes are left
    pytest.param(_multi(1, 0, 0, 2, _entry(10, b"x" * 10) + b"\x10\0\0"), 160, id="multi"),
    pytest.param(dr.HEADER_SINGLE + _entry(10, b"x" * 10) + b"\x10\0\0", 120, id="single"),
])
def test_a_chunk_header_cut_short_is_refused(read, stream, at, no_decode):
    with pytest.raises(DataModelCompressionError,
                       match=f"ends inside the chunk header at byte {at} of {at + 3}"):
        read(stream)


def test_an_all_zero_chunk_header_still_ends_a_single_threaded_table():
    raw = b"ABF" + bytes(range(256)) * 40
    dm = dr.compress_datamodel(raw)
    assert dr.decompress_datamodel(dm + bytes(8)) == raw
    assert dr.decompress_datamodel(dm + bytes(8) + b"anything") == raw
    assert dr._parse_session_groups(dm + bytes(8)) == dr._parse_session_groups(dm)


# ------------------------------------------------------- totals and ratio
def test_a_ratio_bomb_is_refused_before_decoding(no_decode):
    zeros = _session(bytes(2 * MiB))                        # 186 bytes
    bomb = _multi(1, 0, 0, 64, _entry(2 * MiB, zeros) * 64)  # 128 MiB from ~12 KiB
    assert 128 * MiB > max(dr.RATIO_FLOOR, dr.MAX_RATIO * len(bomb))
    for read in (dr.decompress_datamodel, dr._parse_session_groups):
        with pytest.raises(DataModelCompressionError,
                           match=f"134,217,728 bytes uncompressed from a {len(bomb):,}-byte "
                                 "stream, over 1,000:1"):
            read(bomb)


def test_the_ratio_floor_spares_small_streams():
    parts = [bytes(512 * 1024)] * 4                         # 2 MiB at ~5,000:1
    dm = _groups_stream(parts)
    assert 2 * MiB > dr.MAX_RATIO * len(dm)
    assert dr.decompress_datamodel(dm) == bytes(2 * MiB)


def test_the_total_is_bounded(monkeypatch, no_decode):
    monkeypatch.setattr(dr, "MAX_DECOMPRESSED_BYTES", 3 * 4096 - 1)
    with pytest.raises(DataModelCompressionError,
                       match="declare 12,288 bytes uncompressed; at most 12,287"):
        dr.decompress_datamodel(_multi(1, 0, 0, 3, _entry(4096, b"p" * 64) * 3))


# ------------------------------------------------------------- decoding
def test_the_first_failing_group_stops_the_rest(monkeypatch):
    calls = []

    class _Failing:
        def decompress(self, payload, size):
            calls.append(size)
            raise ValueError("Decompression failed: Expected 64, got 0")
    monkeypatch.setattr(dr, "Xpress9", _Failing)
    with pytest.raises(DataModelCompressionError, match="did not decode"):
        dr.decompress_datamodel(_multi(1, 0, 0, 5000, _entry(64, b"p" * 8) * 5000))
    # 0.9.145 queued all 5,000 groups and decoded every one of them
    assert len(calls) <= dr._MAX_GROUPS_IN_FLIGHT


def test_a_chunk_that_does_not_decode_is_a_datamodel_error():
    # was the codec's bare ValueError, which pbix_append_table_rows reported
    # as INVALID_INPUT
    with pytest.raises(DataModelCompressionError,
                       match=r"chunk at byte 102 \(session group 1 of 1\) did not "
                             "decode: Decompression failed"):
        dr.decompress_datamodel(dr.HEADER_SINGLE + _entry(1000, b"not xpress9" * 4))


def test_many_groups_decode_in_order():
    parts = [bytes([i]) * (3000 + i) for i in range(200)]   # more than the in-flight window
    dm = _groups_stream(parts, prefix=(b"head" * 3000, b"more" * 2000))
    assert dr.decompress_datamodel(dm) == b"head" * 3000 + b"more" * 2000 + b"".join(parts)
    assert [len(g) for g in dr._parse_session_groups(dm)] == [2] + [1] * 200


# --------------------------------------------------------- compress side
@pytest.mark.parametrize("size", [0, -1, 16 * MiB + 1])
def test_compress_refuses_a_chunk_size_the_reader_refuses(size, monkeypatch):
    # 0 and -1 never advanced through the ABF: an endless loop of empty chunks
    def _encoder(abf_bytes, chunk_size):
        raise AssertionError(f"encoder ran with chunk_size {chunk_size}")
    monkeypatch.setattr(dr, "_compress_single_threaded", _encoder)
    with pytest.raises(ValueError, match="chunk_size must be 1 to 16,777,216 bytes"):
        dr.compress_datamodel(b"ABF" * 100, chunk_size=size)


@pytest.mark.parametrize("chunk_size", [4096, dr.COMPRESS_CHUNK_SIZE, 16 * MiB])
def test_round_trips_are_unchanged(chunk_size):
    abf = b"ABF" + bytes(range(256)) * 600
    dm = dr.compress_datamodel(abf, chunk_size=chunk_size)
    assert dr._detect_format(dm) == "single_threaded"
    assert dr.decompress_datamodel(dm) == abf
    groups = dr._parse_session_groups(dm)
    assert len(groups) == 1
    assert [u for u, _ in groups[0]] == [min(chunk_size, len(abf) - o)
                                         for o in range(0, len(abf), chunk_size)]


def test_compress_never_writes_a_stream_its_reader_refuses(monkeypatch):
    monkeypatch.setattr(dr, "RATIO_FLOOR", 0)
    monkeypatch.setattr(dr, "MAX_RATIO", 1)
    with pytest.raises(DataModelCompressionError,
                       match="would not read back: .* over 1:1"):
        dr.compress_datamodel(b"ABF" * 10000)


# ------------------------------------------------------------ real files
@pytest.mark.parametrize("name", [
    "Adventure Works DW 2020.pbix",                                  # Desktop, single-threaded
    os.path.join("_desktop_verify", "aw_hybrid_verify.pbix"),        # our hybrid, multi-threaded
    os.path.join("_desktop_verify", "aw_uncompressed_verify.pbix"),  # uncompressed ABF
    "pbi_created_hierarchy.pbix",                                    # the highest ratio, 31.4:1
])
def test_samples_decode_and_parse(name):
    p = os.path.join(SAMPLES, name)
    if not os.path.exists(p):
        pytest.skip(f"{name} not available")
    with zipfile.ZipFile(p) as z:
        dm = z.read("DataModel")
    abf = dr.decompress_datamodel(dm)
    assert abf[:2] == b"\xff\xfe"
    if dr._detect_format(dm) != "uncompressed":
        assert sum(u for g in dr._parse_session_groups(dm) for u, _ in g) == len(abf)


# ----------------------------------------------------------- MCP surface
@pytest.fixture()
def opened(tmp_path):
    """A small built model, open; yields (alias, path of its work-dir DataModel)."""
    from pbix_mcp import server
    from pbix_mcp.builder import PBIXBuilder

    b = PBIXBuilder("i213")
    b.add_table("T", [{"name": "k", "data_type": "Int64"}], rows=[{"k": 1}, {"k": 2}])
    p = tmp_path / "i213.pbix"
    b.save(str(p))
    assert json.loads(server.pbix_open(str(p), "i213"))["success"]
    try:
        yield "i213", os.path.join(server._open_files["i213"]["work_dir"], "DataModel")
    finally:
        server.pbix_close("i213", force=True)


def test_the_tool_reports_a_refused_stream_with_its_code(opened, no_decode):
    from pbix_mcp import server
    alias, dm_path = opened
    with open(dm_path, "wb") as f:
        f.write(dr.HEADER_SINGLE + _entry(0xFFFFFFFF, b"x" * 16))
    r = json.loads(server.pbix_datamodel_decompress(alias))
    assert r["success"] is False
    assert r["error_code"] == "DATAMODEL_DECOMPRESS_FAILED"
    assert "declares 4,294,967,295 bytes uncompressed" in r["message"]


def test_an_edit_tool_reports_an_undecodable_stream_as_a_datamodel_error(opened):
    from pbix_mcp import server
    alias, dm_path = opened
    with open(dm_path, "wb") as f:
        f.write(dr.HEADER_SINGLE + _entry(1000, b"not xpress9" * 4))
    r = json.loads(server.pbix_append_table_rows(alias, "T", '[{"k": 3}]'))
    assert r["success"] is False
    assert r["error_code"] == "DATAMODEL_DECOMPRESS_FAILED"    # 0.9.145: INVALID_INPUT
    assert "did not decode" in r["message"]
