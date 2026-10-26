"""Issue #250: USERELATIONSHIP over an INACTIVE DatePartOnly relationship joins by date part.

What was wrong: _relationship_indexes took the DatePartOnly joins (JoinOnDateBehavior 2) of the
ACTIVE relationships only, and DAXContext.with_relationships -- the context USERELATIONSHIP /
CROSSFILTER build -- copied every lookup of the new relationship set but those joins. So a
USERELATIONSHIP context joined the activated F[Ship] by value, while a context the constructor
rebuilt from the same list (without_filters, DATEADD, ...) joined it by date part, and the
memos keyed by the relationship signature ('blank-row-tables', 'unmatched', 'xtf') handed one
reading to the other: the answer depended on how the CALCULATE was written and on what ran
before it in the same model cache. 0.9.147 on a fresh cache answered CALCULATE([ShipSum],
Cal[Date] = DATE(2024, 1, 2)) 16 (by value) and the filter-argument form 24; in one shared cache
CALCULATE([RTship], Cal[Date] = DATE(2024, 1, 2)) was 6431 fresh and 6175 after other probes.

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256
(F:\\_desktop_oracle\\build_b256.py, its model rebuilt here row for row). F[Order] -> Cal[Date] is
active and joins by value; F[Ship] -> Cal[Date] is inactive with DatePartOnly, half the ships
carry a time of day; each F row's v is a distinct power of two, so a sum names the rows it
took. Desktop joins the activated relationship by date part in every CALCULATE shape --
measure reference, nested, filter argument in either order, TREATAS, IN, Year, iterator,
DATESYTD, PREVIOUSDAY, DATEADD, CROSSFILTER -- and an activated by-value relationship (H[Ship])
keeps its own join beside an active DatePartOnly one (Cal3 / H). Every value below is
Desktop's answer to the probe of the same name. The answers must be the same on a fresh cache,
in one shared cache in either order, in one batch, and with _FAST_PROPAGATION on and off. Cal2 /
G (the inactive DatePartOnly relationship's blank row) is #281's test.
"""
from datetime import datetime as D

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def _t(cols, rows):
    return {"columns": cols, "rows": rows}


TABLES = {
    'Cal': _t(['Date', 'Year', 'Bit'], [
        [D(2023, 12, 30), 2023, 1],
        [D(2023, 12, 31), 2023, 2],
        [D(2024, 1, 1), 2024, 4],
        [D(2024, 1, 2), 2024, 8],
        [D(2024, 1, 3), 2024, 16],
        [D(2024, 1, 4), 2024, 32],
        [D(2024, 1, 5), 2024, 64],
    ]),
    'F': _t(['Id', 'Order', 'Ship', 'v', 'pk'], [
        [0, D(2023, 12, 30), D(2023, 12, 31), 1, 1],
        [1, D(2023, 12, 30), D(2024, 1, 1, 9, 15, 0), 2, 2],
        [2, D(2023, 12, 31), D(2024, 1, 1), 4, 3],
        [3, D(2024, 1, 1), D(2024, 1, 2, 12, 30, 0), 8, 1],
        [4, D(2024, 1, 1), D(2024, 1, 2), 16, 2],
        [5, D(2024, 1, 2), D(2024, 1, 3, 23, 59, 59), 32, 3],
        [6, D(2024, 1, 2), D(2024, 1, 4), 64, 1],
        [7, D(2024, 1, 3), D(2024, 1, 4, 6, 0, 0), 128, 2],
        [8, D(2024, 1, 3), D(2024, 1, 9, 10, 0, 0), 256, 3],
        [9, D(2024, 1, 4), D(2024, 1, 5, 18, 45, 0), 512, 1],
        [10, D(2024, 1, 4, 14, 0, 0), D(2024, 1, 5), 1024, 2],
        [11, D(2023, 12, 29), D(2023, 12, 30, 16, 0, 0), 2048, 3],
        [12, D(2024, 1, 5), None, 4096, 1],
        [13, D(2024, 1, 3), D(2024, 1, 3, 20, 0, 0), 8192, 2],
    ]),
    'P': _t(['pk', 'x'], [
        [1, 1],
        [2, 2],
        [3, 3],
    ]),
    'Cal2': _t(['Date', 'Bit2'], [
        [D(2024, 3, 1), 1],
        [D(2024, 3, 2), 2],
        [D(2024, 3, 3), 4],
        [D(2024, 3, 4), 8],
        [D(2024, 3, 5), 16],
    ]),
    'G': _t(['Order', 'Ship', 'w'], [
        [D(2024, 3, 1), D(2024, 3, 2, 10, 0, 0), 1],
        [D(2024, 3, 1), D(2024, 3, 2), 2],
        [D(2024, 3, 2), D(2024, 3, 3, 15, 30, 0), 4],
        [D(2024, 3, 3), D(2024, 3, 5, 8, 0, 0), 8],
        [D(2024, 3, 4), D(2024, 3, 4), 16],
        [D(2024, 3, 5), D(2024, 3, 5, 23, 0, 0), 32],
    ]),
    'Cal3': _t(['Date', 'Bit3'], [
        [D(2024, 5, 1), 1],
        [D(2024, 5, 2), 2],
        [D(2024, 5, 3), 4],
        [D(2024, 5, 4), 8],
    ]),
    'H': _t(['Order', 'Ship', 'u'], [
        [D(2024, 5, 1, 9, 0, 0), D(2024, 5, 2), 1],
        [D(2024, 5, 1), D(2024, 5, 2, 13, 0, 0), 2],
        [D(2024, 5, 2, 18, 0, 0), D(2024, 5, 3), 4],
        [D(2024, 5, 3, 7, 0, 0), D(2024, 5, 4, 11, 0, 0), 8],
    ]),
}


def _rel(ft, fc, tt, tc, active, jod):
    return {"FromTable": ft, "FromColumn": fc, "ToTable": tt, "ToColumn": tc, "IsActive": int(active),
            "FromCardinality": 2, "ToCardinality": 1, "CrossFilteringBehavior": 1, "JoinOnDateBehavior": jod}


RELS = [_rel('F', 'Order', 'Cal', 'Date', True, 1), _rel('F', 'Ship', 'Cal', 'Date', False, 2), _rel('F', 'pk', 'P', 'pk', True, 1), _rel('G', 'Order', 'Cal2', 'Date', True, 1), _rel('G', 'Ship', 'Cal2', 'Date', False, 2), _rel('H', 'Order', 'Cal3', 'Date', True, 2), _rel('H', 'Ship', 'Cal3', 'Date', False, 1)]

HELPERS = {'ShipSum': 'CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]))',
 'RT': 'CALCULATE(SUM(F[v]), Cal[Date] <= MAX(Cal[Date]))',
 'RTship': 'CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] <= MAX(Cal[Date]))',
 'RTshipRev': 'CALCULATE(SUM(F[v]), Cal[Date] <= MAX(Cal[Date]), USERELATIONSHIP(F[Ship], Cal[Date]))',
 'RTshipInner': 'CALCULATE([ShipSum], Cal[Date] <= MAX(Cal[Date]))',
 'RTshipNested': 'CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date])), Cal[Date] <= '
                 'MAX(Cal[Date]))',
 'YTD': 'TOTALYTD(SUM(F[v]), Cal[Date])',
 'YTDshipOuter': 'CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date]), USERELATIONSHIP(F[Ship], Cal[Date]))',
 'YTDshipInner': 'TOTALYTD([ShipSum], Cal[Date])',
 'DYTDship': 'CALCULATE(SUM(F[v]), DATESYTD(Cal[Date]), USERELATIONSHIP(F[Ship], Cal[Date]))',
 'DYTDshipInner': 'CALCULATE([ShipSum], DATESYTD(Cal[Date]))',
 'XShipBits': 'CALCULATE(SUM(Cal[Bit]), USERELATIONSHIP(F[Ship], Cal[Date]), CROSSFILTER(F[Ship], Cal[Date], '
              'Both))',
 'GShip': 'CALCULATE(SUM(G[w]), USERELATIONSHIP(G[Ship], Cal2[Date]))',
 'HShip': 'CALCULATE(SUM(H[u]), USERELATIONSHIP(H[Ship], Cal3[Date]))',
 'HRTship': 'CALCULATE(SUM(H[u]), USERELATIONSHIP(H[Ship], Cal3[Date]), Cal3[Date] <= MAX(Cal3[Date]))'}
PROBES = {'a_ship_dec30': 'CALCULATE([ShipSum], Cal[Date] = DATE(2023, 12, 30))',
 'a_ship_jan1': 'CALCULATE([ShipSum], Cal[Date] = DATE(2024, 1, 1))',
 'a_ship_jan2': 'CALCULATE([ShipSum], Cal[Date] = DATE(2024, 1, 2))',
 'a_ship_jan3': 'CALCULATE([ShipSum], Cal[Date] = DATE(2024, 1, 3))',
 'a_ship_jan5': 'CALCULATE([ShipSum], Cal[Date] = DATE(2024, 1, 5))',
 'a_arg_jan2': 'CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] = DATE(2024, 1, 2))',
 'a_arg_rev_jan2': 'CALCULATE(SUM(F[v]), Cal[Date] = DATE(2024, 1, 2), USERELATIONSHIP(F[Ship], Cal[Date]))',
 'a_arg_jan3': 'CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] = DATE(2024, 1, 3))',
 'a_nested_jan2': 'CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date])), Cal[Date] = '
                  'DATE(2024, 1, 2))',
 'a_nested_jan3': 'CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date])), Cal[Date] = '
                  'DATE(2024, 1, 3))',
 'a_nested_jan4': 'CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date])), Cal[Date] = '
                  'DATE(2024, 1, 4))',
 'a_treatas_jan2': 'CALCULATE([ShipSum], TREATAS({DATE(2024, 1, 2)}, Cal[Date]))',
 'a_in_jan1_jan3': 'CALCULATE([ShipSum], Cal[Date] IN {DATE(2024, 1, 1), DATE(2024, 1, 3)})',
 'a_year2024': 'CALCULATE([ShipSum], Cal[Year] = 2024)',
 'a_year2023': 'CALCULATE([ShipSum], Cal[Year] = 2023)',
 'a_filter_year2023': 'CALCULATE([ShipSum], FILTER(ALL(Cal[Date]), YEAR(Cal[Date]) = 2023))',
 'a_table_filter_jan2': 'CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), FILTER(ALL(Cal), '
                        'Cal[Date] = DATE(2024, 1, 2)))',
 'a_total': '[ShipSum]',
 'a_ship_blank': 'CALCULATE([ShipSum], ISBLANK(Cal[Date]))',
 'a_order_blank': 'CALCULATE(SUM(F[v]), ISBLANK(Cal[Date]))',
 'a_order_jan2': 'CALCULATE(SUM(F[v]), Cal[Date] = DATE(2024, 1, 2))',
 'a_rows_cal': 'COUNTROWS(VALUES(Cal[Date]))',
 'a_rows_cal_ship': 'CALCULATE(COUNTROWS(VALUES(Cal[Date])), USERELATIONSHIP(F[Ship], Cal[Date]))',
 'a_values_ship_jan2': 'CALCULATE(COUNTROWS(VALUES(F[Ship])), USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] '
                       '= DATE(2024, 1, 2))',
 'a_dcount_jan4': 'CALCULATE(DISTINCTCOUNT(F[Id]), USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] = '
                  'DATE(2024, 1, 4))',
 'b_rt_jan2': 'CALCULATE([RT], Cal[Date] = DATE(2024, 1, 2))',
 'b_rtship_jan2': 'CALCULATE([RTship], Cal[Date] = DATE(2024, 1, 2))',
 'b_rtshiprev_jan2': 'CALCULATE([RTshipRev], Cal[Date] = DATE(2024, 1, 2))',
 'b_rtshipinner_jan2': 'CALCULATE([RTshipInner], Cal[Date] = DATE(2024, 1, 2))',
 'b_rtshipnested_jan2': 'CALCULATE([RTshipNested], Cal[Date] = DATE(2024, 1, 2))',
 'b_rtship_jan3': 'CALCULATE([RTship], Cal[Date] = DATE(2024, 1, 3))',
 'b_rtshipinner_jan3': 'CALCULATE([RTshipInner], Cal[Date] = DATE(2024, 1, 3))',
 'b_rtshipnested_jan3': 'CALCULATE([RTshipNested], Cal[Date] = DATE(2024, 1, 3))',
 'b_const_pred': 'CALCULATE([ShipSum], Cal[Date] <= DATE(2024, 1, 2))',
 'b_const_nested': 'CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date])), Cal[Date] <= '
                   'DATE(2024, 1, 2))',
 'b_const_arg': 'CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] <= DATE(2024, 1, 2))',
 'b_const_ge': 'CALCULATE([ShipSum], Cal[Date] >= DATE(2024, 1, 4))',
 'b_between': 'CALCULATE([ShipSum], Cal[Date] >= DATE(2024, 1, 1), Cal[Date] <= DATE(2024, 1, 3))',
 'b_datesbetween': 'CALCULATE([ShipSum], DATESBETWEEN(Cal[Date], DATE(2024, 1, 1), DATE(2024, 1, 3)))',
 'b_repro_inner': 'CALCULATE([RTshipInner], Cal[Year] = 2024, Cal[Date] = DATE(2024, 1, 2))',
 'b_repro_arg': 'CALCULATE([RTship], Cal[Year] = 2024, Cal[Date] = DATE(2024, 1, 2))',
 'b_repro_nested': 'CALCULATE([RTshipNested], Cal[Year] = 2024, Cal[Date] = DATE(2024, 1, 2))',
 'b_repro_order': 'CALCULATE([RT], Cal[Year] = 2024, Cal[Date] = DATE(2024, 1, 2))',
 'b_pred_same_year': 'CALCULATE([ShipSum], Cal[Year] = 2024, Cal[Date] <= DATE(2024, 1, 2))',
 'b_pred_outer_year': 'CALCULATE(CALCULATE([ShipSum], Cal[Date] <= DATE(2024, 1, 2)), Cal[Year] = 2024)',
 's_shipsum': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [ShipSum] & "", ",", '
              'Cal[Date], ASC)',
 's_rt': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [RT] & "", ",", Cal[Date], ASC)',
 's_rtship': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [RTship] & "", ",", Cal[Date], '
             'ASC)',
 's_rtshiprev': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [RTshipRev] & "", ",", '
                'Cal[Date], ASC)',
 's_rtshipinner': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [RTshipInner] & "", ",", '
                  'Cal[Date], ASC)',
 's_rtshipnested': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [RTshipNested] & "", '
                   '",", Cal[Date], ASC)',
 'sy_rt': 'CONCATENATEX(FILTER(ALL(Cal[Year], Cal[Date]), NOT ISBLANK(Cal[Date])), [RT] & "", ",", '
          'Cal[Date], ASC)',
 'sy_rtship': 'CONCATENATEX(FILTER(ALL(Cal[Year], Cal[Date]), NOT ISBLANK(Cal[Date])), [RTship] & "", ",", '
              'Cal[Date], ASC)',
 'sy_rtshipinner': 'CONCATENATEX(FILTER(ALL(Cal[Year], Cal[Date]), NOT ISBLANK(Cal[Date])), [RTshipInner] & '
                   '"", ",", Cal[Date], ASC)',
 'sy_rtshipnested': 'CONCATENATEX(FILTER(ALL(Cal[Year], Cal[Date]), NOT ISBLANK(Cal[Date])), [RTshipNested] '
                    '& "", ",", Cal[Date], ASC)',
 's_ytd': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [YTD] & "", ",", Cal[Date], ASC)',
 's_ytdshipouter': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [YTDshipOuter] & "", '
                   '",", Cal[Date], ASC)',
 's_ytdshipinner': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [YTDshipInner] & "", '
                   '",", Cal[Date], ASC)',
 's_dytdship': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [DYTDship] & "", ",", '
               'Cal[Date], ASC)',
 's_dytdshipinner': 'CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [DYTDshipInner] & "", '
                    '",", Cal[Date], ASC)',
 'c_ytdshipouter_jan3': 'CALCULATE([YTDshipOuter], Cal[Date] = DATE(2024, 1, 3))',
 'c_ytdshipinner_jan3': 'CALCULATE([YTDshipInner], Cal[Date] = DATE(2024, 1, 3))',
 'c_dytdship_jan3': 'CALCULATE([DYTDship], Cal[Date] = DATE(2024, 1, 3))',
 'c_dytdshipinner_jan3': 'CALCULATE([DYTDshipInner], Cal[Date] = DATE(2024, 1, 3))',
 'c_ytd_jan3': 'CALCULATE([YTD], Cal[Date] = DATE(2024, 1, 3))',
 'c_prevday_jan3': 'CALCULATE(CALCULATE([ShipSum], PREVIOUSDAY(Cal[Date])), Cal[Date] = DATE(2024, 1, 3))',
 'c_dateadd_jan4': 'CALCULATE(CALCULATE([ShipSum], DATEADD(Cal[Date], -1, DAY)), Cal[Date] = DATE(2024, 1, '
                   '4))',
 'd_both_bits_k3': 'CALCULATE([XShipBits], F[v] = 8)',
 'd_both_bits_set': 'CALCULATE([XShipBits], F[v] IN {2, 8, 32, 128})',
 'd_both_count_set': 'CALCULATE(COUNTROWS(Cal), USERELATIONSHIP(F[Ship], Cal[Date]), CROSSFILTER(F[Ship], '
                     'Cal[Date], Both), F[v] IN {2, 8, 32, 128})',
 'd_both_blankrow_k3': 'CALCULATE(COUNTROWS(FILTER(VALUES(Cal[Date]), ISBLANK(Cal[Date]))), '
                       'USERELATIONSHIP(F[Ship], Cal[Date]), CROSSFILTER(F[Ship], Cal[Date], Both), F[v] = '
                       '8)',
 'd_both_blankrow_k8': 'CALCULATE(COUNTROWS(FILTER(VALUES(Cal[Date]), ISBLANK(Cal[Date]))), '
                       'USERELATIONSHIP(F[Ship], Cal[Date]), CROSSFILTER(F[Ship], Cal[Date], Both), F[v] = '
                       '256)',
 'd_both_fwd_jan4': 'CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), CROSSFILTER(F[Ship], '
                    'Cal[Date], Both), Cal[Date] = DATE(2024, 1, 4))',
 'd_oneway_jan4': 'CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), CROSSFILTER(F[Ship], Cal[Date], '
                  'OneWay), Cal[Date] = DATE(2024, 1, 4))',
 'd_cf_inactive_only': 'CALCULATE(SUM(F[v]), CROSSFILTER(F[Ship], Cal[Date], Both), Cal[Date] = DATE(2024, '
                       '1, 2))',
 'd_none_order': 'CALCULATE(SUM(F[v]), CROSSFILTER(F[Order], Cal[Date], None), Cal[Date] = DATE(2024, 1, 2))',
 'd_none_order_userel': 'CALCULATE(SUM(F[v]), CROSSFILTER(F[Order], Cal[Date], None), '
                        'USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] = DATE(2024, 1, 2))',
 'd_related_plain': 'SUMX(F, RELATED(Cal[Bit]))',
 'e_forder_jan4': 'CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] = '
                  'DATE(2024, 1, 4)), F[Order] = DATE(2024, 1, 3))',
 'e_forder_jan3': 'CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] = '
                  'DATE(2024, 1, 3)), F[Order] = DATE(2024, 1, 3))',
 'e_keep_window': 'CALCULATE(CALCULATE([ShipSum], KEEPFILTERS(Cal[Date] <= DATE(2024, 1, 2))), Cal[Date] >= '
                  'DATE(2024, 1, 1))',
 'e_iter_sumx': 'SUMX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [ShipSum])',
 'e_iter_count': 'COUNTROWS(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date]) && NOT ISBLANK([ShipSum])))',
 'p_inner_jan2': 'CONCATENATEX(VALUES(P[x]), CALCULATE(CALCULATE([ShipSum], P[x] <= MAX(P[x])), Cal[Date] = '
                 'DATE(2024, 1, 2)) & "", ",", P[x], ASC)',
 'p_inner_jan3': 'CONCATENATEX(VALUES(P[x]), CALCULATE(CALCULATE([ShipSum], P[x] <= MAX(P[x])), Cal[Date] = '
                 'DATE(2024, 1, 3)) & "", ",", P[x], ASC)',
 'p_inner_jan4': 'CONCATENATEX(VALUES(P[x]), CALCULATE(CALCULATE([ShipSum], P[x] <= MAX(P[x])), Cal[Date] = '
                 'DATE(2024, 1, 4)) & "", ",", P[x], ASC)',
 'p_arg_jan3': 'CONCATENATEX(VALUES(P[x]), CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], '
               'Cal[Date]), P[x] <= MAX(P[x])), Cal[Date] = DATE(2024, 1, 3)) & "", ",", P[x], ASC)',
 'p_arg_jan4': 'CONCATENATEX(VALUES(P[x]), CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], '
               'Cal[Date]), P[x] <= MAX(P[x])), Cal[Date] = DATE(2024, 1, 4)) & "", ",", P[x], ASC)',
 'g_rows': 'COUNTROWS(VALUES(Cal2[Date]))',
 'g_rows_all': 'COUNTROWS(ALL(Cal2[Date]))',
 'g_rows_userel': 'CALCULATE(COUNTROWS(VALUES(Cal2[Date])), USERELATIONSHIP(G[Ship], Cal2[Date]))',
 'g_blankrow_count': 'COUNTROWS(FILTER(VALUES(Cal2[Date]), ISBLANK(Cal2[Date])))',
 'g_blank_ship': 'CALCULATE([GShip], ISBLANK(Cal2[Date]))',
 'g_blank_order': 'CALCULATE(SUM(G[w]), ISBLANK(Cal2[Date]))',
 'g_day_ship': 'CALCULATE([GShip], Cal2[Date] = DATE(2024, 3, 5))',
 'g_window_ship': 'CALCULATE(SUM(G[w]), USERELATIONSHIP(G[Ship], Cal2[Date]), Cal2[Date] <= DATE(2024, 3, '
                  '2))',
 'g_window_nested': 'CALCULATE(CALCULATE(SUM(G[w]), USERELATIONSHIP(G[Ship], Cal2[Date])), Cal2[Date] <= '
                    'DATE(2024, 3, 2))',
 'g_window_order': 'CALCULATE(SUM(G[w]), Cal2[Date] <= DATE(2024, 3, 2))',
 'g_series_all': 'CONCATENATEX(VALUES(Cal2[Date]), [GShip] & "", ",", Cal2[Date], ASC)',
 'h_order_may1': 'CALCULATE(SUM(H[u]), Cal3[Date] = DATE(2024, 5, 1))',
 'h_ship_may2': 'CALCULATE(SUM(H[u]), USERELATIONSHIP(H[Ship], Cal3[Date]), Cal3[Date] = DATE(2024, 5, 2))',
 'h_ship_may2_nested': 'CALCULATE([HShip], Cal3[Date] = DATE(2024, 5, 2))',
 'h_ship_blank': 'CALCULATE([HShip], ISBLANK(Cal3[Date]))',
 'h_order_blank': 'CALCULATE(SUM(H[u]), ISBLANK(Cal3[Date]))',
 'h_rows': 'COUNTROWS(VALUES(Cal3[Date]))',
 'h_rows_userel': 'CALCULATE(COUNTROWS(VALUES(Cal3[Date])), USERELATIONSHIP(H[Ship], Cal3[Date]))',
 'h_window_ship': 'CALCULATE(SUM(H[u]), USERELATIONSHIP(H[Ship], Cal3[Date]), Cal3[Date] <= DATE(2024, 5, '
                  '2))',
 'h_window_order': 'CALCULATE(SUM(H[u]), Cal3[Date] <= DATE(2024, 5, 2))',
 'h_xf_both': 'CALCULATE(SUM(Cal3[Bit3]), CROSSFILTER(H[Order], Cal3[Date], Both), H[u] = 4)',
 'h_rtship_series': 'CONCATENATEX(FILTER(VALUES(Cal3[Date]), NOT ISBLANK(Cal3[Date])), [HRTship] & "", ",", '
                    'Cal3[Date], ASC)'}
# Power BI Desktop 2.152 (B256, desk.json): each probe's answer
DESKTOP = {'a_ship_dec30': 2048,
 'a_ship_jan1': 6,
 'a_ship_jan2': 24,
 'a_ship_jan3': 8224,
 'a_ship_jan5': 1536,
 'a_arg_jan2': 24,
 'a_arg_rev_jan2': 24,
 'a_arg_jan3': 8224,
 'a_nested_jan2': 24,
 'a_nested_jan3': 8224,
 'a_nested_jan4': 192,
 'a_treatas_jan2': 24,
 'a_in_jan1_jan3': 8230,
 'a_year2024': 9982,
 'a_year2023': 2049,
 'a_filter_year2023': 2049,
 'a_table_filter_jan2': 24,
 'a_total': 16383,
 'a_ship_blank': 4352,
 'a_order_blank': 3072,
 'a_order_jan2': 96,
 'a_rows_cal': 8,
 'a_rows_cal_ship': 8,
 'a_values_ship_jan2': 2,
 'a_dcount_jan4': 2,
 'b_rt_jan2': 3199,
 'b_rtship_jan2': 6431,
 'b_rtshiprev_jan2': 6431,
 'b_rtshipinner_jan2': 6431,
 'b_rtshipnested_jan2': 6431,
 'b_rtship_jan3': 14655,
 'b_rtshipinner_jan3': 14655,
 'b_rtshipnested_jan3': 14655,
 'b_const_pred': 6431,
 'b_const_nested': 6431,
 'b_const_arg': 6431,
 'b_const_ge': 1728,
 'b_between': 8254,
 'b_datesbetween': 8254,
 'b_repro_inner': 6431,
 'b_repro_arg': 6431,
 'b_repro_nested': 6431,
 'b_repro_order': 3199,
 'b_pred_same_year': 30,
 'b_pred_outer_year': 6431,
 's_shipsum': '2048,1,6,24,8224,192,1536',
 's_rt': '3075,3079,3103,3199,11775,12287,16383',
 's_rtship': '6400,6401,6407,6431,14655,14847,16383',
 's_rtshiprev': '6400,6401,6407,6431,14655,14847,16383',
 's_rtshipinner': '6400,6401,6407,6431,14655,14847,16383',
 's_rtshipnested': '6400,6401,6407,6431,14655,14847,16383',
 'sy_rt': '3075,3079,3103,3199,11775,12287,16383',
 'sy_rtship': '6400,6401,6407,6431,14655,14847,16383',
 'sy_rtshipinner': '6400,6401,6407,6431,14655,14847,16383',
 'sy_rtshipnested': '6400,6401,6407,6431,14655,14847,16383',
 's_ytd': '3,7,24,120,8696,9208,13304',
 's_ytdshipouter': '2048,2049,6,30,8254,8446,9982',
 's_ytdshipinner': '2048,2049,6,30,8254,8446,9982',
 's_dytdship': '2048,2049,6,30,8254,8446,9982',
 's_dytdshipinner': '2048,2049,6,30,8254,8446,9982',
 'c_ytdshipouter_jan3': 8254,
 'c_ytdshipinner_jan3': 8254,
 'c_dytdship_jan3': 8254,
 'c_dytdshipinner_jan3': 8254,
 'c_ytd_jan3': 8696,
 'c_prevday_jan3': 24,
 'c_dateadd_jan4': 8224,
 'd_both_bits_k3': 8,
 'd_both_bits_set': 60,
 'd_both_count_set': 4,
 'd_both_blankrow_k3': None,
 'd_both_blankrow_k8': 1,
 'd_both_fwd_jan4': 192,
 'd_oneway_jan4': 192,
 'd_cf_inactive_only': 96,
 'd_none_order': 16383,
 'd_none_order_userel': 24,
 'd_related_plain': 172,
 'e_forder_jan4': 128,
 'e_forder_jan3': 8192,
 'e_keep_window': 30,
 'e_iter_sumx': 12031,
 'e_iter_count': 7,
 'p_inner_jan2': '8,24,24',
 'p_inner_jan3': ',8192,8224',
 'p_inner_jan4': '64,192,192',
 'p_arg_jan3': ',8192,8224',
 'p_arg_jan4': '64,192,192',
 'g_rows': 5,
 'g_rows_all': 5,
 'g_rows_userel': 5,
 'g_blankrow_count': None,
 'g_blank_ship': None,
 'g_blank_order': None,
 'g_day_ship': 40,
 'g_window_ship': 3,
 'g_window_nested': 3,
 'g_window_order': 7,
 'g_series_all': ',3,4,16,40',
 'h_order_may1': 3,
 'h_ship_may2': 1,
 'h_ship_may2_nested': 1,
 'h_ship_blank': 10,
 'h_order_blank': None,
 'h_rows': 5,
 'h_rows_userel': 5,
 'h_window_ship': 11,
 'h_window_order': 7,
 'h_xf_both': 2,
 'h_rtship_series': '10,11,15,15'}


# B256's helper for the TOTALYTD filter argument (#282)
HELPERS['YTDship'] = 'TOTALYTD(SUM(F[v]), Cal[Date], USERELATIONSHIP(F[Ship], Cal[Date]))'
MEASURES = {**HELPERS, **PROBES}
# the probes of this issue: Cal2 / G is #281's
MAIN = [n for n in DESKTOP if not n.startswith('g_')]


def evaluate(name, measures=None):
    return de.evaluate_measure(name, TABLES, measures or MEASURES, {}, relationships=RELS)


def same(got, want):
    if want is None:
        return got is None or got == ""
    if isinstance(want, str):
        return str(got) == want
    return got is not None and not isinstance(got, str) and float(got) == float(want)


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    """Both propagation paths: _FAST_PROPAGATION on and off."""
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


@pytest.mark.parametrize("name", MAIN)
def test_each_probe_on_a_fresh_cache(name, path):
    got = evaluate(name)
    assert same(got, DESKTOP[name]), (name, got, DESKTOP[name])


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_all_probes_in_one_cache(order, path):
    """0.9.147: 8 probes changed with the order on the fast path, 40 on the
    slow one (b_rtship_jan2 6431 fresh, 6175 after others)."""
    names = MAIN if order == "forward" else list(reversed(MAIN))
    got = [(n, evaluate(n)) for n in names]
    assert [(n, g, DESKTOP[n]) for n, g in got if not same(g, DESKTOP[n])] == []


def test_all_probes_in_one_batch(path):
    got = de.evaluate_measures_batch(MAIN, TABLES, MEASURES, {}, relationships=RELS)
    assert [(n, got[n], DESKTOP[n]) for n in MAIN if not same(got[n], DESKTOP[n])] == []


def test_the_review_repro_in_either_order(path):
    """The #231 review's repro (dp_userel4.py): a window inside the
    USERELATIONSHIP and outside it agree, in either order in one cache --
    0.9.147 answered the second one 303 / 355 depending on which ran first."""
    names = ['b_repro_inner', 'b_repro_arg', 'b_repro_nested']
    for order in (names, list(reversed(names))):
        de._SHARED_FILTER_CACHES.clear()
        assert [evaluate(n) for n in order] == [6431, 6431, 6431]


def test_the_date_part_joins_are_the_relationship_lists_own():
    """Every context's DatePartOnly joins are its relationship list's, the
    inactive ones included, so USERELATIONSHIP / CROSSFILTER cannot change
    them and they are a function of the relationship signature."""
    ctx = de.DAXContext(TABLES, MEASURES, None, None, {}, RELS)
    assert ("F", "Ship", "Cal", "Date") in ctx._date_part_joins
    assert ("Cal", "Date", "F", "Ship") in ctx._date_part_joins
    assert ("G", "Ship", "Cal2", "Date") in ctx._date_part_joins
    assert ("H", "Order", "Cal3", "Date") in ctx._date_part_joins
    assert ("H", "Ship", "Cal3", "Date") not in ctx._date_part_joins
    assert ("F", "Order", "Cal", "Date") not in ctx._date_part_joins
    eng = de.DAXEngine()
    for arg, fn in (("USERELATIONSHIP(F[Ship], Cal[Date])", eng._apply_userelationship),
                    ("CROSSFILTER(F[Ship], Cal[Date], Both)", eng._apply_crossfilter),
                    ("CROSSFILTER(F[Order], Cal[Date], None)", eng._apply_crossfilter)):
        got = fn(arg, ctx)
        assert got.relationships is not ctx.relationships
        assert got._date_part_joins == de._relationship_indexes(got.relationships)[6] == ctx._date_part_joins
        assert got.without_filters([])._date_part_joins == got._date_part_joins
