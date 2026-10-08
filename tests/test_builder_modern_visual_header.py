"""PBIXBuilder writes the report setting every Desktop-saved report has:
useStylableVisualContainerHeader ("Use the modern visual header with updated
styling options").

Without it, Power BI Desktop 2.157 draws a built report's visuals in the legacy
visual container: measured on PDF exports of the same built report with and
without the setting, every visual's content sat about 17 px further right and
down without it.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from pbix_mcp.builder import PBIXBuilder

pytestmark = pytest.mark.unit


def test_built_report_uses_the_modern_visual_header(tmp_path):
    b = PBIXBuilder("header")
    b.add_table("T", [{"name": "v", "data_type": "Int64"}], rows=[{"v": 1}])
    p = tmp_path / "header.pbix"
    b.save(str(p))
    with zipfile.ZipFile(p) as z:
        layout = json.loads(z.read("Report/Layout").decode("utf-16-le"))
    settings = json.loads(layout["config"])["settings"]
    assert settings["useStylableVisualContainerHeader"] is True
    assert settings["useNewFilterPaneExperience"] is True      # unchanged
