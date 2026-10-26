"""#289: a DATESYTD / TOTALYTD year end written as an ISO date is honoured.

What was wrong: _fn_datesytd read the year end only as "m/d"; "2023-12-30" -- the ISO form,
which reads the same in every locale -- was ignored and the year started on January 1:
TOTALYTD(SUM(F[v]), Cal[Date], "2023-12-30") under January 2 2024 answered 120 instead of 124.

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256b
(build_b256b.py), over the model of tests/test_issue250_userelationship_date_part.py: with year
end December 30 (the year is ignored) the year of January 2 2024 starts on December 31 2023
(t_ytd_ye_j2 124, the same as "12/30": t_ytd_ye_md_j2, and DATESYTD's: t_dytd_ye_j2), and
December 31 2023 is the first day of its year (t_ytd_ye_dec31 4, not the 7 of Dec 30 + Dec 31).
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES, same

pytestmark = pytest.mark.unit

J2 = "Cal[Date] = DATE(2024, 1, 2)"
PROBES = {
    "t_ytd_ye_j2": (f'CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date], "2023-12-30"), {J2})', 124),
    "t_ytd_ye_dec31": ('CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date], "2023-12-30"), Cal[Date] = DATE(2023, 12, 31))', 4),
    "t_ytd_ye_md_j2": (f'CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date], "12/30"), {J2})', 124),
    "t_dytd_ye_j2": (f'CALCULATE(CALCULATE(SUM(F[v]), DATESYTD(Cal[Date], "2023-12-30")), {J2})', 124),
}
ALL_MEASURES = {**MEASURES, **{k: e for k, (e, _w) in PROBES.items()}}


@pytest.mark.parametrize("name", list(PROBES))
def test_the_year_end(name):
    de._SHARED_FILTER_CACHES.clear()
    got, want = de.evaluate_measure(name, TABLES, ALL_MEASURES, {}, relationships=RELS), PROBES[name][1]
    assert same(got, want), (name, got, want)
