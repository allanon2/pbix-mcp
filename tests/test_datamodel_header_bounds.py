"""A DataModel stream's header is bounded before anything is decoded.

The XPress9 stream declares its own chunk counts (8-byte thread counts) and
chunk sizes (4 bytes each). Trusted as given, a few bytes could ask the
decoder to loop over 2^40 chunk groups or hand the codec a 4 GiB buffer per
chunk. These streams are made here, by hand.
"""
import struct
import time

import pytest
from xpress9 import Xpress9

from pbix_mcp.errors import DataModelCompressionError
from pbix_mcp.formats import datamodel_roundtrip as dr

pytestmark = pytest.mark.unit


def _multi(main_cpt, prefix_cpt, prefix_tc, main_tc, chunks=b"", chunk_size=2 * 1024 * 1024):
    return (dr.HEADER_MULTI + struct.pack("<5Q", main_cpt, prefix_cpt, prefix_tc, main_tc, chunk_size)
            + chunks)


def _chunk(us, payload):
    return struct.pack("<II", us, len(payload)) + payload


def _session(raw: bytes) -> bytes:
    """One chunk compressed in a codec context of its own (one session)."""
    xp = Xpress9()
    try:
        return xp.compress(raw, len(raw) + len(raw) // 4 + 65536)
    finally:
        del xp


def _valid_multi(parts):
    return _multi(1, 0, 0, len(parts), b"".join(_chunk(len(p), _session(p)) for p in parts))


@pytest.mark.parametrize("decode", [dr.decompress_datamodel, dr._parse_session_groups])
def test_thread_count_past_the_stream_is_refused_at_once(decode):
    data = _multi(1, 0, 0, 1 << 40, _chunk(10, b"x" * 10))
    t = time.perf_counter()
    with pytest.raises(DataModelCompressionError, match="room for"):
        decode(data)
    assert time.perf_counter() - t < 1.0


def test_thread_groups_of_no_chunks_are_not_looped_over():
    # decompress_datamodel already skipped such groups; reading the session groups
    # (as saving an edited model does) looped over every one
    data = _valid_multi([b"pbix" * 100])
    hostile = data[:102] + struct.pack("<5Q", 1, 0, 1 << 40, 1, 2 * 1024 * 1024) + data[142:]
    t = time.perf_counter()
    assert [len(g) for g in dr._parse_session_groups(hostile)] == [1]
    assert time.perf_counter() - t < 1.0
    assert dr.decompress_datamodel(hostile) == b"pbix" * 100


@pytest.mark.parametrize("decode", [dr.decompress_datamodel, dr._parse_session_groups])
def test_chunk_declaring_4_gib_is_refused(decode):
    data = _multi(1, 0, 0, 1, _chunk(0xFFFFFFFF, b"x" * 16))
    with pytest.raises(DataModelCompressionError, match="uncompressed"):
        decode(data)


def test_single_threaded_chunk_declaring_4_gib_is_refused():
    data = dr.HEADER_SINGLE + _chunk(0xFFFFFFFF, b"x" * 16)
    with pytest.raises(DataModelCompressionError, match="uncompressed"):
        dr.decompress_datamodel(data)


@pytest.mark.parametrize("decode", [dr.decompress_datamodel, dr._parse_session_groups])
def test_chunk_running_past_the_end_is_refused(decode):
    data = _multi(1, 0, 0, 1, struct.pack("<II", 10, 1000) + b"x" * 10)
    with pytest.raises(DataModelCompressionError, match="past the end"):
        decode(data)


@pytest.mark.parametrize("decode", [dr.decompress_datamodel, dr._parse_session_groups])
def test_truncated_header_is_refused(decode):
    with pytest.raises(DataModelCompressionError, match="shorter than its header"):
        decode(dr.HEADER_MULTI + b"\0" * 12)


@pytest.mark.parametrize("decode", [dr.decompress_datamodel, dr._parse_session_groups])
def test_ratio_bound_refuses_a_decompression_bomb(decode, monkeypatch):
    monkeypatch.setattr(dr, "RATIO_FLOOR", 1024 * 1024)
    bomb = _valid_multi([b"\0" * (512 * 1024)] * 4)     # 2 MiB from a few kilobytes
    assert len(bomb) * dr.MAX_RATIO < 2 * 1024 * 1024
    with pytest.raises(DataModelCompressionError, match=f"over {dr.MAX_RATIO}:1"):
        decode(bomb)


def test_ratio_bound_spares_small_streams():
    # under RATIO_FLOOR any ratio decodes: a small model of repeated values compresses far past 200:1
    parts = [b"\0" * (512 * 1024)] * 4
    assert dr.decompress_datamodel(_valid_multi(parts)) == b"".join(parts)


def test_compress_refuses_chunks_its_decoder_would_refuse():
    with pytest.raises(ValueError, match="chunk_size"):
        dr.compress_datamodel(b"abc", chunk_size=dr.MAX_CHUNK_UNCOMPRESSED + 1)


def test_total_bound(monkeypatch):
    parts = [bytes(range(256)) * 64, b"pbix" * 4096]
    monkeypatch.setattr(dr, "MAX_DECOMPRESSED_BYTES", len(parts[0]) + len(parts[1]) - 1)
    with pytest.raises(DataModelCompressionError, match="declare over"):
        dr.decompress_datamodel(_valid_multi(parts))


def test_streams_within_the_bounds_decode_unchanged():
    parts = [bytes(range(256)) * 64, b"pbix" * 4096, b"\0" * 9000]
    assert dr.decompress_datamodel(_valid_multi(parts)) == b"".join(parts)
    assert [len(g) for g in dr._parse_session_groups(_valid_multi(parts))] == [1, 1, 1]
    abf = b"ABF" + bytes(range(256)) * 300
    assert dr.decompress_datamodel(dr.compress_datamodel(abf, chunk_size=4096)) == abf
