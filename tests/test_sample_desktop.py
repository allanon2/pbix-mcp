"""SAMPLE as Power BI Desktop answers it: spacing, direction, order columns, n = 0.

Expected values: Power BI Desktop 2.157's engine, one EVALUATE query per case
over its local Analysis Services instance (black-box; tables made by
GENERATESERIES, so no model's data is involved). Generated from Desktop's
output; record: docs/reverse-engineering/experiments/2026-10-22-sample.md.
Each case wraps Desktop's table query in CONCATENATEX (or COUNTROWS) so the
picked rows read as one value.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

CASES = [
    ('spacing-n2-of10', 'CONCATENATEX(SAMPLE(2, SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,10'),
    ('spacing-n3-of10', 'CONCATENATEX(SAMPLE(3, SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,6,10'),
    ('spacing-n4-of10', 'CONCATENATEX(SAMPLE(4, SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,4,7,10'),
    ('spacing-n5-of10', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,3,6,8,10'),
    ('spacing-n7-of10', 'CONCATENATEX(SAMPLE(7, SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,3,4,6,7,9,10'),
    ('spacing-n10-of10', 'CONCATENATEX(SAMPLE(10, SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,2,3,4,5,6,7,8,9,10'),
    ('spacing-n2-of11', 'CONCATENATEX(SAMPLE(2, SELECTCOLUMNS(GENERATESERIES(1, 11), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,11'),
    ('spacing-n3-of11', 'CONCATENATEX(SAMPLE(3, SELECTCOLUMNS(GENERATESERIES(1, 11), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,6,11'),
    ('spacing-n4-of11', 'CONCATENATEX(SAMPLE(4, SELECTCOLUMNS(GENERATESERIES(1, 11), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,4,8,11'),
    ('spacing-n5-of11', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 11), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,4,6,9,11'),
    ('spacing-n7-of11', 'CONCATENATEX(SAMPLE(7, SELECTCOLUMNS(GENERATESERIES(1, 11), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,3,4,6,8,9,11'),
    ('spacing-n10-of11', 'CONCATENATEX(SAMPLE(10, SELECTCOLUMNS(GENERATESERIES(1, 11), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,2,3,4,5,7,8,9,10,11'),
    ('spacing-n2-of20', 'CONCATENATEX(SAMPLE(2, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,20'),
    ('spacing-n3-of20', 'CONCATENATEX(SAMPLE(3, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,11,20'),
    ('spacing-n4-of20', 'CONCATENATEX(SAMPLE(4, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,7,14,20'),
    ('spacing-n5-of20', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,6,11,15,20'),
    ('spacing-n7-of20', 'CONCATENATEX(SAMPLE(7, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,4,7,11,14,17,20'),
    ('spacing-n10-of20', 'CONCATENATEX(SAMPLE(10, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,3,5,7,9,12,14,16,18,20'),
    ('spacing-n2-of100', 'CONCATENATEX(SAMPLE(2, SELECTCOLUMNS(GENERATESERIES(1, 100), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,100'),
    ('spacing-n3-of100', 'CONCATENATEX(SAMPLE(3, SELECTCOLUMNS(GENERATESERIES(1, 100), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,51,100'),
    ('spacing-n4-of100', 'CONCATENATEX(SAMPLE(4, SELECTCOLUMNS(GENERATESERIES(1, 100), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,34,67,100'),
    ('spacing-n5-of100', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 100), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,26,51,75,100'),
    ('spacing-n7-of100', 'CONCATENATEX(SAMPLE(7, SELECTCOLUMNS(GENERATESERIES(1, 100), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,18,34,51,67,84,100'),
    ('spacing-n10-of100', 'CONCATENATEX(SAMPLE(10, SELECTCOLUMNS(GENERATESERIES(1, 100), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,12,23,34,45,56,67,78,89,100'),
    ('spacing-n2-of101', 'CONCATENATEX(SAMPLE(2, SELECTCOLUMNS(GENERATESERIES(1, 101), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,101'),
    ('spacing-n3-of101', 'CONCATENATEX(SAMPLE(3, SELECTCOLUMNS(GENERATESERIES(1, 101), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,51,101'),
    ('spacing-n4-of101', 'CONCATENATEX(SAMPLE(4, SELECTCOLUMNS(GENERATESERIES(1, 101), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,34,68,101'),
    ('spacing-n5-of101', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 101), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,26,51,76,101'),
    ('spacing-n7-of101', 'CONCATENATEX(SAMPLE(7, SELECTCOLUMNS(GENERATESERIES(1, 101), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,18,34,51,68,84,101'),
    ('spacing-n10-of101', 'CONCATENATEX(SAMPLE(10, SELECTCOLUMNS(GENERATESERIES(1, 101), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,12,23,34,45,57,68,79,90,101'),
    ('spacing-n2-of912', 'CONCATENATEX(SAMPLE(2, SELECTCOLUMNS(GENERATESERIES(1, 912), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,912'),
    ('spacing-n3-of912', 'CONCATENATEX(SAMPLE(3, SELECTCOLUMNS(GENERATESERIES(1, 912), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,457,912'),
    ('spacing-n4-of912', 'CONCATENATEX(SAMPLE(4, SELECTCOLUMNS(GENERATESERIES(1, 912), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,305,608,912'),
    ('spacing-n5-of912', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 912), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,229,457,684,912'),
    ('spacing-n7-of912', 'CONCATENATEX(SAMPLE(7, SELECTCOLUMNS(GENERATESERIES(1, 912), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,153,305,457,608,760,912'),
    ('spacing-n10-of912', 'CONCATENATEX(SAMPLE(10, SELECTCOLUMNS(GENERATESERIES(1, 912), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,102,203,305,406,507,608,710,811,912'),
    ('spacing-n2-of1000', 'CONCATENATEX(SAMPLE(2, SELECTCOLUMNS(GENERATESERIES(1, 1000), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,1000'),
    ('spacing-n3-of1000', 'CONCATENATEX(SAMPLE(3, SELECTCOLUMNS(GENERATESERIES(1, 1000), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,501,1000'),
    ('spacing-n4-of1000', 'CONCATENATEX(SAMPLE(4, SELECTCOLUMNS(GENERATESERIES(1, 1000), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,334,667,1000'),
    ('spacing-n5-of1000', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 1000), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,251,501,750,1000'),
    ('spacing-n7-of1000', 'CONCATENATEX(SAMPLE(7, SELECTCOLUMNS(GENERATESERIES(1, 1000), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,168,334,501,667,834,1000'),
    ('spacing-n10-of1000', 'CONCATENATEX(SAMPLE(10, SELECTCOLUMNS(GENERATESERIES(1, 1000), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,112,223,334,445,556,667,778,889,1000'),
    ('spacing-n50-of912', 'CONCATENATEX(SAMPLE(50, SELECTCOLUMNS(GENERATESERIES(1, 912), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1,20,38,57,75,94,113,131,150,168,187,206,224,243,261,280,298,317,336,354,373,391,410,429,447,466,484,503,522,540,559,577,596,615,633,652,670,689,707,726,745,763,782,800,819,838,856,875,893,912'),
    ('spacing-n3502-of912', 'COUNTROWS(SAMPLE(3502, SELECTCOLUMNS(GENERATESERIES(1, 912), "v", [Value]), [v], ASC))', 912),
    ('spacing-n1-of10', 'CONCATENATEX(SAMPLE(1, SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value]), [v], ASC), [v], ",", [v], ASC)', '1'),
    ('spacing-n0-of10', 'COUNTROWS(SAMPLE(0, SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value]), [v], ASC))', None),
    ('by-position-squares', 'CONCATENATEX(SAMPLE(4, SELECTCOLUMNS(GENERATESERIES(1, 30), "v", [Value] * [Value]), [v], ASC), [v], ",", [v], ASC)', '1,121,400,900'),
    ('dir-desc', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v], DESC), [v], ",", [v], ASC)', '1,6,10,15,20'),
    ('dir-1', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v], 1), [v], ",", [v], ASC)', '1,6,11,15,20'),
    ('dir-0', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v], 0), [v], ",", [v], ASC)', '1,6,10,15,20'),
    ('dir-default', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value]), [v]), [v], ",", [v], ASC)', '1,6,10,15,20'),
    ('ties-second-order-desc', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 20), "a", INT(([Value] - 1) / 4) + 1, "v", [Value]), [a], ASC, [v], DESC), [v], ",", [v], ASC)', '4,7,10,14,17'),
    ('ties-first-order-only', 'CONCATENATEX(SAMPLE(5, SELECTCOLUMNS(GENERATESERIES(1, 20), "a", INT(([Value] - 1) / 4) + 1, "v", [Value]), [a], ASC), [a], ",", [a], ASC)', '1,2,3,4,5'),
]


@pytest.mark.parametrize("name, expr, expected", CASES, ids=[c[0] for c in CASES])
def test_sample_as_desktop(name, expr, expected):
    tables = {"D": {"columns": ["x"], "rows": [[1]]}}
    assert de.evaluate_measure("m", tables, {"m": expr}) == expected
