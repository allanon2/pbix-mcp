"""DataMashup: read Formulas/Section1.m from the PackageParts zip by its length prefix.

MS-QDEFF lays the part out as Version (u32) | PackagePartsLength (u32) |
PackageParts (a zip) | Permissions | Metadata | PermissionBindings, and the
Metadata ends with its own content zip -- which can be an EMPTY zip, a bare
end-of-central-directory record. Scanning for the LAST EOCD picked that one, so
the archive read as [] and pbix_get_m_code returned success with no M. Seen on
Microsoft's MIT samples "2018SU10 Fuzzy Matching Demo - October.pbix" and
"COVID-19 US Tracking Sample.pbit"; both now return their M.
"""
from __future__ import annotations

import io
import struct
import zipfile

from pbix_mcp import server

M = 'section Section1;\r\n\r\nshared Sales = let\r\n    Source = 1\r\nin\r\n    Source;'


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def _datamashup(package: bytes, metadata_content: bytes) -> bytes:
    metadata = struct.pack("<I", 0) + struct.pack("<I", 0) + struct.pack("<I", len(metadata_content)) + metadata_content
    return (struct.pack("<II", 0, len(package)) + package
            + struct.pack("<I", 0)                           # Permissions
            + struct.pack("<I", len(metadata)) + metadata
            + struct.pack("<I", 0))                          # PermissionBindings


EMPTY_ZIP = _zip({})          # a bare 22-byte end-of-central-directory record


def test_m_is_read_when_the_metadata_ends_with_an_empty_zip(tmp_path):
    (tmp_path / "DataMashup").write_bytes(_datamashup(_zip({"Formulas/Section1.m": M}), EMPTY_ZIP))
    assert server._read_datamashup_m_code(str(tmp_path)) == M


def test_m_is_read_without_metadata_content_too(tmp_path):
    (tmp_path / "DataMashup").write_bytes(_datamashup(_zip({"Formulas/Section1.m": M}), b""))
    assert server._read_datamashup_m_code(str(tmp_path)) == M
