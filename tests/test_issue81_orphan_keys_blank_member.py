"""Issue #81: the builder joined a fact key with no dimension row to the
dimension's FIRST row.

The relationship's R$ index has one slot per distinct FK value, holding the
1-based dimension row; an unmatched key fell back to row 0 and was written as
row 1. Desktop writes 0 there (the blank, unknown member) and sets
RelationshipIndexStorage.Flags = 1 whenever some fact rows reach the blank
member, through an unmatched key or a blank key. Without the flag the rows land
on the blank member but VALUES() does not show it.

Expected encodings are read back from the same model after Power BI Desktop
(2.152) refreshed it; the built file then answered all ten A/B queries over
ADOMD exactly as the refreshed one did.
"""
from __future__ import annotations

import sqlite3
import struct
import tempfile
import zipfile
from pathlib import Path

import pytest

from pbix_mcp.builder import PBIXBuilder
from pbix_mcp.formats.abf_rebuild import list_abf_files, read_abf_file, read_metadata_sqlite
from pbix_mcp.formats.datamodel_roundtrip import decompress_datamodel

pytestmark = pytest.mark.unit


def _build(path):
    b = PBIXBuilder("orphans")
    b.add_table("BD", [{"name": "Key", "data_type": "Int64"}, {"name": "Name", "data_type": "String"}],
                rows=[{"Key": 10, "Name": "X"}, {"Key": 20, "Name": "Y"}, {"Key": 30, "Name": "Z"}])
    b.add_table("BF", [{"name": "Key", "data_type": "Int64"}, {"name": "amt", "data_type": "Int64"}],
                rows=[{"Key": 10, "amt": 1}, {"Key": 20, "amt": 2}, {"Key": 99, "amt": 4},
                      {"Key": 30, "amt": 8}, {"Key": 98, "amt": 16}, {"Key": 10, "amt": 32}])
    b.add_relationship("BF", "Key", "BD", "Key")
    # string keys: the FK dictionary is in insertion order, not sorted
    b.add_table("SD", [{"name": "Code", "data_type": "String"}, {"name": "Label", "data_type": "String"}],
                rows=[{"Code": "b", "Label": "Bee"}, {"Code": "a", "Label": "Ay"}])
    b.add_table("SF", [{"name": "Code", "data_type": "String"}, {"name": "amt", "data_type": "Int64"}],
                rows=[{"Code": "a", "amt": 1}, {"Code": "zz", "amt": 2},
                      {"Code": "b", "amt": 4}, {"Code": "q", "amt": 8}])
    b.add_relationship("SF", "Code", "SD", "Code")
    # blank keys, no orphans
    b.add_table("ND", [{"name": "Key", "data_type": "Int64"}, {"name": "Name", "data_type": "String"}],
                rows=[{"Key": 1, "Name": "N1"}, {"Key": 2, "Name": "N2"}])
    b.add_table("NF", [{"name": "Key", "data_type": "Int64"}, {"name": "amt", "data_type": "Int64"}],
                rows=[{"Key": 1, "amt": 1}, {"Key": None, "amt": 2},
                      {"Key": 2, "amt": 4}, {"Key": None, "amt": 8}])
    b.add_relationship("NF", "Key", "ND", "Key")
    # control: every key matches
    b.add_table("CD", [{"name": "Key", "data_type": "Int64"}, {"name": "Name", "data_type": "String"}],
                rows=[{"Key": 1, "Name": "C1"}, {"Key": 2, "Name": "C2"}])
    b.add_table("CF", [{"name": "Key", "data_type": "Int64"}, {"name": "amt", "data_type": "Int64"}],
                rows=[{"Key": 2, "amt": 1}, {"Key": 1, "amt": 2}, {"Key": 2, "amt": 4}])
    b.add_relationship("CF", "Key", "CD", "Key")
    b.add_measure("BF", "Total", "SUM(BF[amt])")
    b.add_page("Page 1")
    b.save(path)
    return b


def _decode_nosplit(idf: bytes, idfmeta: bytes, count: int) -> list[int]:
    """One-segment NoSplit<N> IDF: a u64 word count, then u64 words holding
    the values LSB-first. N is in the IDFMETA CS block as 0xABA36 + N."""
    cs = idfmeta.index(b"<1:CS\x00") + 6
    bit_width = struct.unpack_from("<I", idfmeta, cs + 16)[0] - 0xABA36
    words = struct.unpack_from("<Q", idf, 0)[0]
    mask, per_word, out = (1 << bit_width) - 1, 64 // bit_width, []
    for w in range(words):
        word = struct.unpack_from("<Q", idf, 8 + 8 * w)[0]
        out.extend((word >> (j * bit_width)) & mask for j in range(per_word))
    return out[:count]


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    path = tmp_path_factory.mktemp("i81") / "orphans.pbix"
    builder = _build(str(path))
    abf = decompress_datamodel(zipfile.ZipFile(path).read("DataModel"))
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "meta.db"
        db.write_bytes(read_metadata_sqlite(abf))
        con = sqlite3.connect(db)
        flags = dict(con.execute(
            """SELECT ft.Name, ris.Flags || ':' || ris.RecordCount FROM Relationship r
               JOIN [Table] ft ON ft.ID = r.FromTableID
               JOIN RelationshipStorage rs ON rs.ID = r.RelationshipStorageID
               JOIN RelationshipIndexStorage ris ON ris.ID = rs.RelationshipIndexStorageID"""
        ).fetchall())
        con.close()
    files = {f["Path"]: f for f in list_abf_files(abf)}
    index = {}
    for fact, fc in flags.items():
        ris_flags, count = (int(x) for x in fc.split(":"))
        (idf,) = [p for p in files if p.startswith(f"R${fact} (") and p.endswith(".idf")]
        (meta,) = [p for p in files if p.startswith(f"R${fact} (") and p.endswith(".idfmeta")]
        index[fact] = (ris_flags, _decode_nosplit(read_abf_file(abf, files[idf]),
                                                  read_abf_file(abf, files[meta]), count))
    return builder, index


def test_unmatched_integer_keys_point_at_the_blank_row(model):
    _, index = model
    # FK dictionary 10, 20, 30, 98, 99 (numerics sort); 98 and 99 match no row.
    # Before the fix: [0, 0, 0, 1, 2, 3, 1, 1] -- both orphans joined to X.
    assert index["BF"][1] == [0, 0, 0, 1, 2, 3, 0, 0]


def test_unmatched_string_keys_point_at_the_blank_row(model):
    _, index = model
    # FK dictionary a, zz, b, q (insertion order) -> Ay is row 2, Bee row 1.
    # Desktop's own index for this data: [0, 0, 0, 2, 0, 1, 0].
    assert index["SF"][1] == [0, 0, 0, 2, 0, 1, 0]


def test_flags_mark_relationships_whose_rows_reach_the_blank_member(model):
    _, index = model
    assert index["BF"][0] == 1      # unmatched keys
    assert index["SF"][0] == 1
    assert index["NF"][0] == 1      # blank keys: data_id 2, a padding slot
    assert index["CF"][0] == 0      # every key matches


def test_blank_keys_and_matched_keys_are_unchanged(model):
    _, index = model
    assert index["NF"][1] == [0, 0, 0, 1, 2]
    assert index["CF"][1] == [0, 0, 0, 1, 2]


def test_the_orphan_warning_still_reports_the_keys(model):
    builder, _ = model
    kinds = sorted(w["kind"] for w in builder.build_warnings)
    assert kinds == ["orphan_keys", "orphan_keys"]
