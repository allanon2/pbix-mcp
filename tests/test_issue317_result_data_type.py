"""#317: pbix_evaluate_dax labels a Fixed Decimal (Currency)
result data_type "Decimal", the model's name for the type (a Fixed Decimal
column's DataType), not "Double".

What was wrong (0.9.147): DAXResult typed every float "Double", so a
Currency result -- which Desktop types as Currency (ISCURRENCY TRUE,
build_b254.py a_col, t_add; build_b247.py c_iscur_avg) -- read as a Double.

Expected values: the label is the model schema's name for the Currency type
("Decimal", as the reader reports a Fixed Decimal column); that the value is
a Currency is Desktop's answer (ISCURRENCY(AVERAGE(TCur3[v])) is TRUE).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.models.responses import DAXResult

pytestmark = pytest.mark.unit


def test_currency_results_are_labelled_decimal():
    assert DAXResult(name="m", value=de._Currency(0.1333)).data_type == "Decimal"
    assert DAXResult(name="m", value=0.1333).data_type == "Double"
    assert DAXResult(name="m", value=3).data_type == "Int64"


def test_through_the_server(tmp_path):
    import json
    import warnings

    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    warnings.simplefilter("ignore")
    path = str(tmp_path / "i18.pbix")
    b = PBIXBuilder("i18")
    b.add_table("TCur3", [{"name": "v", "data_type": "Decimal"}], rows=[{"v": 0.1}, {"v": 0.1}, {"v": 0.2}])
    b.add_measure("TCur3", "avg3", "AVERAGE(TCur3[v])")
    b.add_measure("TCur3", "is_cur", "IF(ISCURRENCY(AVERAGE(TCur3[v])), 1, 0)")
    b.add_page("Page 1")
    b.save(path)
    assert json.loads(S.pbix_open(path, "i18"))["success"]
    try:
        out = json.loads(S.pbix_evaluate_dax("i18", "avg3,is_cur", apply_default_filters=False))
    finally:
        S.pbix_close("i18")
    got = {x["name"]: x for x in out["results"]}
    assert got["is_cur"]["value"] == 1                 # Desktop: build_b247.py c_iscur_avg
    assert got["avg3"]["value"] == 0.1333              # Desktop: build_b247.py c_avg3_text
    assert got["avg3"]["data_type"] == "Decimal"
