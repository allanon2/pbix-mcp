"""Issue #91: pbix_set_m_code reported success but destroyed the DataMashup
when its Metadata ends with a content zip.

The writer found "the inner zip" by scanning to the LAST end-of-central-
directory record -- the Metadata's content zip -- so it rebuilt the wrong
archive, spliced it over PackageParts + Permissions + Metadata, and left the
length prefix stale. On Microsoft's 2018 Fuzzy Matching demo and COVID-19 US
Tracking template (Metadata ending with an empty zip) it returned True and left
a DataMashup that no longer parsed. It now rewrites only the PackageParts
(MS-QDEFF) and its length.

Desktop check: the Fuzzy Matching demo edited through pbix_set_m_code (People
gains Table.FirstN(..., 5)) opens in Power BI Desktop 2.152, whose Power Query
editor shows the added step and a 5-row preview.
"""
from __future__ import annotations

import io
import struct
import zipfile

import pytest

from pbix_mcp import server
from tests.test_datamashup_package_parts import EMPTY_ZIP, M, _datamashup, _zip

pytestmark = pytest.mark.unit

NEW_M = "section Section1;\r\n\r\nshared Sales = let\r\n    Source = 2\r\nin\r\n    Source;"
CONTENTS = {"empty content zip": EMPTY_ZIP,
            "no content": b"",
            "non-empty content zip": _zip({"Formulas/Other.m": "x"})}


def _package():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in (("[Content_Types].xml", "<Types/>"),
                           ("Config/Package.xml", "<Package/>"),
                           ("Formulas/Section1.m", M)):
            info = zipfile.ZipInfo(name, date_time=(2018, 10, 12, 18, 31, 46))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    return buf.getvalue()


def _parts(data: bytes) -> tuple[zipfile.ZipFile, bytes]:
    version, length = struct.unpack_from("<II", data, 0)
    assert version == 0
    return zipfile.ZipFile(io.BytesIO(data[8:8 + length])), data[8 + length:]


@pytest.mark.parametrize("shape", list(CONTENTS))
def test_only_the_package_parts_are_rewritten(tmp_path, shape):
    old = _datamashup(_package(), CONTENTS[shape])
    (tmp_path / "DataMashup").write_bytes(old)
    assert server._write_datamashup_m_code(str(tmp_path), NEW_M) is True
    new = (tmp_path / "DataMashup").read_bytes()

    old_zip, old_tail = _parts(old)
    new_zip, new_tail = _parts(new)
    # Permissions, Metadata (content zip included) and PermissionBindings are
    # untouched; 0.9.114 spliced over them (378 -> 34 bytes on the empty shape)
    assert new_tail == old_tail
    assert new_zip.namelist() == old_zip.namelist()
    for info in old_zip.infolist():
        got = new_zip.getinfo(info.filename)
        assert (got.date_time, got.compress_type) == (info.date_time, info.compress_type)
        if info.filename != "Formulas/Section1.m":
            assert new_zip.read(info.filename) == old_zip.read(info.filename)
    assert new_zip.read("Formulas/Section1.m") == NEW_M.encode("utf-8")
    assert server._read_datamashup_m_code(str(tmp_path)) == NEW_M


def test_a_part_without_the_qdeff_prefix_still_uses_the_signature_scan(tmp_path):
    # no Version / PackagePartsLength header: the old scan applies, as before
    (tmp_path / "DataMashup").write_bytes(b"junk" + _package() + b"tail")
    assert server._write_datamashup_m_code(str(tmp_path), NEW_M) is True
    assert server._read_datamashup_m_code(str(tmp_path)) == NEW_M


def test_a_package_without_section1_is_a_failure_not_a_silent_success(tmp_path):
    original = _datamashup(_zip({"Config/Package.xml": "<Package/>"}), EMPTY_ZIP)
    (tmp_path / "DataMashup").write_bytes(original)
    assert server._write_datamashup_m_code(str(tmp_path), NEW_M) is False
    assert (tmp_path / "DataMashup").read_bytes() == original     # untouched
