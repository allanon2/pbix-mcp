"""#295: RELATED in a calculated column joins a DatePartOnly relationship by date part.

What was wrong: the calculated-column evaluator (calc_tables._build_related_resolver) keyed the
one side by value, so over an active DatePartOnly relationship whose many-side keys carry a time
of day, RELATED found no row for them: RELATED(Cal3[Bit3]) over H was [BLANK, 1, BLANK, BLANK].
#288 fixed RELATED in measures; this is the same join where a calculated column is
computed.

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256b
(build_b256b.py rl_dp_active: SUMX(H, RELATED(Cal3[Bit3])) over the active DatePartOnly H[Order]
is 8 = 1 + 1 + 2 + 4, each order's day), over the model of
tests/test_issue250_userelationship_date_part.py. A calculated column is RELATED in the row
context of each row of H, so its values are those four. Controls: a by-value relationship keeps
its join (the inactive H[Ship] is no path; F[pk] -> P by value), and a relationship to an auto
date/time table with no JoinOnDateBehavior given joins by date part, as Desktop writes it.
"""
from datetime import datetime as D

import pytest

from pbix_mcp.dax import calc_tables as ct
from tests.test_issue250_userelationship_date_part import RELS, TABLES

pytestmark = pytest.mark.unit


def test_related_over_an_active_date_part_relationship():
    h = TABLES["H"]
    vals, err = ct.evaluate_row_context_column(h["columns"], h["rows"], "RELATED(Cal3[Bit3])", "H",
                                               TABLES, RELS)
    assert err is None
    assert vals == [1, 1, 2, 4]


def test_a_by_value_relationship_keeps_its_join():
    f = TABLES["F"]
    vals, err = ct.evaluate_row_context_column(f["columns"], f["rows"], "RELATED(P[x])", "F", TABLES, RELS)
    assert err is None
    assert vals == [r[4] for r in f["rows"]]
    # F[Order] -> Cal is by value: the order with a time of day (row 10) and the one before the
    # calendar (row 11) find no day
    vals, err = ct.evaluate_row_context_column(f["columns"], f["rows"], "RELATED(Cal[Bit])", "F", TABLES, RELS)
    assert err is None
    assert vals == [1, 1, 2, 4, 4, 8, 8, 16, 16, 32, None, None, 64, 16]


def test_an_auto_date_time_join_without_its_behaviour():
    tables = {"T": {"columns": ["When", "n"], "rows": [[D(2024, 2, 1, 8, 30), 1], [D(2024, 2, 2), 2]]},
              "LocalDateTable_1": {"columns": ["Date", "Day"], "rows": [[D(2024, 2, 1), 1], [D(2024, 2, 2), 2]]}}
    rels = [{"FromTable": "T", "FromColumn": "When", "ToTable": "LocalDateTable_1", "ToColumn": "Date",
             "IsActive": 1, "FromCardinality": 2, "ToCardinality": 1, "CrossFilteringBehavior": 1}]
    t = tables["T"]
    vals, err = ct.evaluate_row_context_column(t["columns"], t["rows"], "RELATED(LocalDateTable_1[Day])", "T",
                                               tables, rels)
    assert err is None
    assert vals == [1, 2]
