"""Issue #83: results computed under USERELATIONSHIP / CROSSFILTER leaked into
other evaluations.

The relationship propagation is memoized on the model-wide filter cache, and
its key did not carry the relationship set, so a USERELATIONSHIP context and a
plain one under the same filters served each other's answers -- across calls.
The USERELATIONSHIP context also started from scratch, dropping the grouping
(ISINSCOPE) and the slicer selection (ALLSELECTED). Expected values are Power
BI Desktop 2.152's, over ADOMD.
"""
from __future__ import annotations

import pytest

from pbix_mcp import server as S
from pbix_mcp.dax import engine as de
from tests.test_issue82_blank_member import build_blank_member_model, grouped, scalar

pytestmark = pytest.mark.unit

REL = {'u_q1': ('CALCULATE([QTotal], USERELATIONSHIP(QF[Key], QD[Key]), QD[Name] = "Q1")', 1),
 'u_q1_plain': ('CALCULATE([QTotal], QD[Name] = "Q1")', 7)}

MEASURES3 = {'UIsIn': 'CALCULATE(ISINSCOPE(QD[Name]), USERELATIONSHIP(QF[Key], QD[Key]))',
 'UAs': 'CALCULATE(COUNTROWS(ALLSELECTED(QD[Name])), USERELATIONSHIP(QF[Key], QD[Key]))',
 'UTot': 'CALCULATE([QTotal], USERELATIONSHIP(QF[Key], QD[Key]))'}

GROUPED_REL = {'g_u': ('QD[Name]',
         ['UIsIn', 'UAs', 'UTot', 'QTotal'],
         [['Q1', True, 3, 1, 7], ['Q2', True, 3, 4, 7], [None, True, 3, 2, 7]])}


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("i83") / "rels.pbix")
    alias = build_blank_member_model(
        path, {**{n: e for n, (e, _v) in REL.items()}, **MEASURES3})
    yield alias
    S._open_files.pop(alias, None)
    S._dax_cache.pop(alias, None)


def test_userelationship_then_plain_in_the_same_model(model):
    # USERELATIONSHIP first, then the plain measure under the same filter:
    # the plain one used to come back as 1, the USERELATIONSHIP answer
    assert scalar(model, "u_q1") == REL["u_q1"][1] == 1
    assert scalar(model, "u_q1_plain") == REL["u_q1_plain"][1] == 7


def test_grouped_userelationship_keeps_scope_and_selection(model):
    col, measures, want = GROUPED_REL["g_u"]
    assert grouped(model, col, measures) == want


QT = {"QD": {"columns": ["Key", "Name"], "rows": [[1, "Q1"], [2, "Q2"]]},
      "QF": {"columns": ["Key", "amt"], "rows": [[1, 1], [7, 2], [2, 4]]}}
QR = [{"FromTable": "QF", "FromColumn": "Key", "ToTable": "QD", "ToColumn": "Key",
       "IsActive": 0, "CrossFilteringBehavior": 1}]
QM = {"QTotal": "SUM(QF[amt])",
      "QTotalU": "CALCULATE([QTotal], USERELATIONSHIP(QF[Key], QD[Key]))",
      "QBoth": "CALCULATE([QTotal], USERELATIONSHIP(QF[Key], QD[Key]), "
               "CROSSFILTER(QF[Key], QD[Key], Both))"}


def _ev(names, fc):
    return de.evaluate_measures_smart(names, QT, QM, fc, simulate_row_context=False,
                                      relationships=QR)


@pytest.mark.parametrize("order", [("QTotal", "QTotalU"), ("QTotalU", "QTotal"),
                                   ("QBoth", "QTotal"), ("QTotal", "QBoth")])
def test_answers_do_not_depend_on_what_ran_before(order):
    want = {"QTotal": {"Q1": 7, "Q2": 7}, "QTotalU": {"Q1": 1, "Q2": 4},
            "QBoth": {"Q1": 1, "Q2": 4}}
    for member in ("Q1", "Q2"):
        for name in order:          # separate calls, one shared model cache
            got = _ev([name], {"QD.Name": [member]})[name]
            assert got == want[name][member], (order, member, name)
