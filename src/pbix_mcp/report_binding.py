"""Compile a visual's ``projections`` + ``prototypeQuery`` into the compiled
data binding (``query`` + ``dataTransforms``) that Power BI Desktop's report
loader requires on every legacy ``Report/Layout`` visualContainer.

Without this, Desktop fails the whole report with "Something went wrong — Failed
to load the report" once the report carries any report-level ``config`` /
visual ``objects`` (as OpenBI/pbix-mcp reports do), even though the data model
opens cleanly. Verified against real Power BI Desktop: an OpenBI report that
lacked these fails to load; injecting them makes it load.

The mapping is mechanical — everything needed is already in ``projections``
(role -> [{queryRef}]) and ``prototypeQuery`` (Version/From/ordered Select, each
Select carrying its queryRef ``Name`` and a Column/Measure expression). The
type codes were taken from real Desktop reports (sales_demo, GeoSales corpus).
"""

from __future__ import annotations

import copy
import re

_TABLE_TYPES = {"tableEx", "table"}
# matrix / pivotTable cross rows against columns — a fundamentally different
# binding (Primary rows + Secondary columns) from a flat table.
_MATRIX_TYPES = {"matrix", "pivotTable"}
# The classic slicer, the Button slicer and Desktop's native List slicer all
# keep a bare column in Values (issue #187: all 9 List slicers of the community
# files bind dimDate.Year, Parameter.Parameter, ... unwrapped).
_SLICER_TYPES = {"slicer", "advancedSlicerVisual", "listSlicer"}
_CARTESIAN_TYPES = {
    "barChart", "columnChart", "clusteredBarChart", "clusteredColumnChart",
    "stackedBarChart", "stackedColumnChart", "hundredPercentStackedBarChart",
    "hundredPercentStackedColumnChart", "lineChart", "areaChart",
    "stackedAreaChart", "lineClusteredColumnComboChart",
    "lineStackedColumnComboChart", "scatterChart", "ribbonChart", "funnel",
}
# Roles that act as a value/measure axis (paired with Series => pivoted).
_VALUE_ROLES = {"Y", "Values", "Y2", "Size"}
# Model value types Power BI implicitly Sums when a bare column lands on a value
# axis (everything else is Count-ed). QueryAggregateFunction: Sum=0, Avg=1,
# CountNonNull=5.
_NUMERIC_TYPES = {"Int64", "Double", "Decimal"}
_AGG_SUM = 0
_AGG_AVERAGE = 1
_AGG_COUNTNONNULL = 5
# Every QueryAggregateFunction code, as Microsoft's published semantic query
# schema numbers them (microsoft/json-schemas, MIT, report/definition/
# semanticQuery: 0 Sum, 1 Average, 2 Distinct count, 3 Min, 4 Max, 5 Count of
# non-null values, 6 Median, 7 StandardDeviation, 8 Variance), and the name a
# queryRef gives the aggregated column, "<Name>(Entity.Property)". Sum, Avg,
# Min and Max are the names the community Desktop-saved files use (13 / 1 /
# 48 / 4 such selects across 3 / 1 / 7 / 2 files); the others are named after
# the schema's descriptions -- a queryRef is how the projections name the
# select (clean-room remediation 2026-10-19, docs/reverse-engineering/
# experiments/2026-10-19-clean-room-remediation.md).
QUERY_AGGREGATES: dict[int, str] = {
    0: "Sum", 1: "Avg", 2: "DistinctCount", 3: "Min", 4: "Max", 5: "CountNonNull",
    6: "Median", 7: "StandardDeviation", 8: "Variance",
}
# Coordinates must never be summed: Desktop's default summarization for
# latitude/longitude is AVERAGE (summing coordinates of two cities lands the
# point in the ocean). Applies to the map field wells that only ever hold
# coordinates, and to lat/long-named numeric columns in any value role
# (ledger issues-5).
_GEO_ROLES = {"Latitude", "Longitude"}
_LATLONG_NAME_RE = re.compile(r"^(lat(itude)?|lo?ng(itude)?)$", re.IGNORECASE)

# Columns Desktop keeps bare in a well that otherwise summarizes (0.9.139):
# an Azure Map places points by them (4 of 4 bindings in the community
# Desktop files); a multi-row card lists a text's values (8 of 8) -- a number
# there is summarized as anywhere (no file has one; Microsoft Learn's
# default).
_BARE_COLUMN_ROLES = {
    "azureMap": {"X", "Y"},
}
_BARE_TEXT_ROLES = {
    "multiRowCard": {"Values"},
}
# The wells a column dropped into is summarized in: the value wells 0.9.139
# summarized, plus tooltips, a decomposition tree's Analyze and a key
# influencers' Explain by. In the community Desktop files Y, Analyze and a
# number in Explain by are aggregated (6 of 6), a text in Explain by stays
# bare (17 of 17), and every grouping well -- Category, Rows, Columns,
# Series, Target -- keeps even a summing number bare (over 200 bindings).
_SUMMARIZING_ROLES = {"Y", "Values", "Y2", "Size", "Tooltips", "Analyze", "ExplainBy",
                      "Data"}
# The new card's Data well holds measures (74 in 14 files); a scatter chart's
# X does (7 of 7), and Microsoft Learn: scatter charts require aggregated
# values on X and Y. Wells of visuals no community file uses -- a gauge's
# targets, a KPI's goal, colour saturation -- are not summarized until a
# Desktop save shows them (docs/reverse-engineering/experiments/
# 2026-10-19-clean-room-remediation.md, "Open").
_SUMMARIZING_VISUAL_ROLES = {"scatterChart": {"X"}}
# The model's own default summarization (the Tabular model's AggregateFunction,
# Microsoft's public documentation: Default 1, None 2, Sum 3, Min 4, Max 5,
# Count 6, Average 7, DistinctCount 8) -> the query aggregate a number gets,
# matched by name to the public QueryAggregateFunction codes; Default is Sum
# for a number, as the model documents it. The files: Sum -> Sum (6 bindings),
# Average -> Avg (1).
_NUMBER_AGGREGATES = {1: _AGG_SUM, 3: _AGG_SUM, 4: 3, 5: 4, 6: _AGG_COUNTNONNULL, 7: _AGG_AVERAGE, 8: 2}
# "Don't summarize": each value on its own (Microsoft Learn, "Work with
# aggregates"), the column stays bare -- as the files keep it in a table and
# on an Azure Map.
_DONT_SUMMARIZE = 2
# A text or date in a card, a tooltip or a matrix's values is given its first
# value, Min (7 of 7 such bindings across 4 community Desktop files); in a
# chart's value wells it is counted (Microsoft Learn: a categorical field in a
# numbers-only bucket is counted -- no file has one).
_FIRST = 3
_FIRST_VISUAL_ROLES = {"card": {"Values"}, "matrix": {"Values"}, "pivotTable": {"Values"}}


def _implicit_aggregate(visual_type: str, role: str, data_type, summarize_by, data_category,
                        prop: str):
    """The (code, name) a bare column in ``role`` of ``visual_type`` is summarized
    by, or None to keep it bare. ``data_type`` is the model type ("Int64",
    "String", ...), ``summarize_by`` the column's default summarization code,
    ``data_category`` its data category (clean-room remediation 2026-10-19)."""
    if role in _BARE_COLUMN_ROLES.get(visual_type, ()):
        return None
    geo_named = bool(_LATLONG_NAME_RE.match(prop or ""))
    if role not in _SUMMARIZING_ROLES and role not in _GEO_ROLES and not (role == "X" and geo_named) \
            and role not in _SUMMARIZING_VISUAL_ROLES.get(visual_type, ()):
        return None
    numeric = data_type in _NUMERIC_TYPES
    if numeric and summarize_by == _DONT_SUMMARIZE:
        return None                        # a number set not to summarize stays bare
    if not numeric and role in _BARE_TEXT_ROLES.get(visual_type, ()):
        return None
    if role in _GEO_ROLES or (numeric and (geo_named or data_category in _GEO_ROLES)):
        # coordinates average, never sum (0.9.139: two cities summed land in the sea)
        return _AGG_AVERAGE
    if numeric:
        return _NUMBER_AGGREGATES.get(summarize_by, _AGG_SUM)
    if role == "ExplainBy":
        return None
    if role == "Tooltips" or role in _FIRST_VISUAL_ROLES.get(visual_type, ()):
        return _FIRST
    return _AGG_COUNTNONNULL

# (underlyingType, queryMetadata.Type) per model data type — taken verbatim from
# Desktop-authored reports (sales_demo + GeoSales/IT_Support/etc. corpus). Keyed
# on the field's VALUE type, for BOTH columns and measures (an integer measure
# like SUM(int) is 260/3, a currency column like UnitPrice is 259/1).
_TYPE_CODES: dict[str, tuple[int, int]] = {
    "String": (1, 2048),
    "Boolean": (1, 2048),
    "Int64": (260, 3),
    "Double": (259, 1),
    "Decimal": (259, 1),
    "DateTime": (519, 4),
}
_DEFAULT_CODES = (259, 1)  # unknown -> numeric/decimal (the common measure case)

# prototypeQuery.OrderBy Direction codes (Desktop's semantic-query sort enum).
SORT_ASCENDING = 1
SORT_DESCENDING = 2
_DIRECTIONS = {
    "asc": SORT_ASCENDING, "ascending": SORT_ASCENDING, "1": SORT_ASCENDING,
    "desc": SORT_DESCENDING, "descending": SORT_DESCENDING, "2": SORT_DESCENDING,
}

# DAX-style field reference: [Name], 'Table'[Name], or Table[Name].
# The quoted alternative accepts DAX's doubled-quote escape ('O''Brien'[Col]);
# the captured table is unescaped before comparison.
_FIELD_REF_RE = re.compile(
    r"^\s*(?:'((?:[^']|'')+)'|([^'\[\]]+?))?\s*\[\s*([^\[\]]+?)\s*\]\s*$"
)


def _type_codes(data_type: str | None) -> tuple[int, int]:
    return _TYPE_CODES.get(data_type or "", _DEFAULT_CODES)


def _hierarchy_source(hier: dict) -> dict:
    """The expression holding a hierarchy's SourceRef: the hierarchy's own,
    or, for an auto date/time hierarchy, its PropertyVariationSource's."""
    expr: dict = hier.get("Expression", {})
    pvs = expr.get("PropertyVariationSource")
    if pvs is not None:
        expr = pvs.get("Expression", {})
    return expr


def _data_reduction(visual_type: str) -> dict:
    """DataReduction matching Desktop for the visual family."""
    if visual_type in _CARTESIAN_TYPES:
        return {"DataVolume": 4, "Primary": {"Window": {"Count": 1000}}}
    if visual_type in _TABLE_TYPES:
        return {"DataVolume": 3, "Primary": {"Window": {"Count": 500}}}
    if visual_type in _SLICER_TYPES:
        return {"DataVolume": 3, "Primary": {"Window": {"Count": 100}}}
    # card / pie / donut / default
    return {"DataVolume": 3, "Primary": {"Top": {}}}


def apply_implicit_aggregations(single_visual: dict, resolve_type=None) -> dict[str, str]:
    """Rewrite plain value-role columns in the PROTOTYPE query to Aggregations.

    Power BI Desktop re-derives the live data query from
    ``config.singleVisual.prototypeQuery`` + ``projections`` -- aggregating only
    the compiled ``query`` is not enough (Desktop-verified: the chart stays
    empty). A column Desktop's field well summarizes is stored as an
    ``Aggregation`` select named ``<Name>(Entity.Property)`` (QUERY_AGGREGATES)
    and the projection points at that name (ground truth: AI Sample barChart).
    Which columns are summarized, and by what, is _implicit_aggregate: the
    well, the column's type, its default summarization and data category.
    Flat grids (``table``/``tableEx``) and slicers show raw values and are left
    alone; measures and explicit aggregations are untouched. ``resolve_type``
    may carry ``summarize_by(entity, prop)`` and ``data_category(entity, prop)``.

    Returns a mapping of old queryRef -> new queryRef (empty when nothing changed).
    """
    proto = single_visual.get("prototypeQuery") or {}
    projections = single_visual.get("projections") or {}
    visual_type = single_visual.get("visualType", "")
    selects = proto.get("Select") or []
    if not selects or visual_type in _TABLE_TYPES or visual_type in _SLICER_TYPES:
        return {}

    alias2entity = {f.get("Name"): f.get("Entity") for f in proto.get("From", [])}
    ref2role = {it.get("queryRef"): role
                for role, items in projections.items() for it in items}

    # A projection COVERED BY A FIELD PARAMETER must keep the exact queryRef
    # the parameter's NAMEOF ref resolves to -- a bare Column or Measure,
    # never an implicit Aggregation. Every Desktop-authored binding in the
    # corpus (Furniture Sales, Root Cause, Chandoo Performance) projects the
    # covered field un-wrapped; wrapping a covered numeric column into
    # Sum(T.Col) breaks the queryRef <-> parameter-field correspondence
    # Desktop's re-derivation needs, so it drops the well and the visual
    # renders EMPTY -- no bars, no value axis (issue #36).
    param_covered: set = set()
    for role, entries in (single_visual.get("queryFieldParametersByRole")
                          or {}).items():
        items = projections.get(role) or []
        for entry in entries or []:
            try:
                start = int(entry.get("index", 0))
                length = int(entry.get("length", 1))
            except (TypeError, ValueError):
                continue
            for it in items[start:start + length]:
                param_covered.add(it.get("queryRef"))

    def _fact(name, entity, prop):
        reader = getattr(resolve_type, name, None) if resolve_type is not None else None
        if reader is None:
            return None
        try:
            return reader(entity, prop)
        except Exception:
            return None

    renamed: dict[str, str] = {}
    agg_selects: dict = {}   # (source alias/entity, property) -> mutated select
    for sel in selects:
        if "Column" not in sel:
            continue                       # measures / explicit aggregations
        old_ref = sel.get("Name")
        if old_ref in param_covered:
            continue                       # field-parameter well: keep bare
        col = sel["Column"]
        src = col.get("Expression", {}).get("SourceRef", {})
        entity = alias2entity.get(src.get("Source"), src.get("Entity"))
        prop = col.get("Property", "")
        role = ref2role.get(old_ref)
        vtype = None
        if resolve_type is not None:
            try:
                vtype = resolve_type(entity, prop, False)
            except Exception:
                vtype = None
        func = _implicit_aggregate(visual_type, role, vtype, _fact("summarize_by", entity, prop),
                                   _fact("data_category", entity, prop), prop)
        if func is None:
            continue
        fn_name = QUERY_AGGREGATES[func]
        new_ref = (f"{fn_name}({entity}.{prop})" if entity and prop
                   else f"{fn_name}({old_ref})")
        sel.pop("Column")
        sel["Aggregation"] = {"Expression": {"Column": col}, "Function": func}
        sel["Name"] = new_ref
        renamed[old_ref] = new_ref
        agg_selects[(src.get("Source") or src.get("Entity"), prop)] = sel

    if renamed:
        for items in projections.values():
            for it in items:
                ref = it.get("queryRef")
                if ref in renamed:
                    it["queryRef"] = renamed[ref]
        # An OrderBy on a column that just became an implicit Aggregation must
        # sort by the aggregate too (Desktop stores the Aggregation expression
        # in OrderBy for such sorts), or the sort references a field the query
        # no longer selects.
        for ob in (proto.get("OrderBy") or []):
            col = (ob.get("Expression") or {}).get("Column")
            if not col:
                continue
            src = col.get("Expression", {}).get("SourceRef", {})
            key = (src.get("Source") or src.get("Entity"), col.get("Property", ""))
            sel = agg_selects.get(key)
            if sel is not None:
                ob["Expression"] = {"Aggregation": copy.deepcopy(sel["Aggregation"])}
    return renamed


def attach_order_by(single_visual: dict, sort_by: str,
                    direction: str = "desc") -> tuple[str, int]:
    """Attach a Desktop-style ``prototypeQuery.OrderBy`` clause sorting the
    visual by one of its own projected fields.

    Desktop authors ``OrderBy: [{Direction: 1|2, Expression: {Measure|Column|
    Aggregation: ...}}]`` on the prototype; :func:`compile_visual_binding`
    deep-copies the prototype into the compiled ``query``, so calling this
    BEFORE compiling carries the clause into both places. Without an OrderBy
    the Power BI service falls back to category-ascending query order.

    Parameters
    ----------
    single_visual : dict
        The ``config.singleVisual`` object (needs ``prototypeQuery.Select``).
    sort_by : str
        The field to sort by, accepted as a bare name (``Pipeline Value``), a
        DAX-style reference (``[Pipeline Value]``, ``'Table'[Col]``,
        ``Table[Col]``), or a queryRef (``Table.Field``).
    direction : str
        ``asc``/``ascending`` or ``desc``/``descending`` (case-insensitive).

    Returns
    -------
    (query_ref, direction_code) : tuple[str, int]
        The queryRef of the matched Select and the numeric Direction written.

    Raises
    ------
    ValueError
        Unknown direction, no prototypeQuery, or ``sort_by`` matching none of
        the visual's fields (the message lists what IS available).
    """
    dcode = _DIRECTIONS.get(str(direction).strip().lower())
    if dcode is None:
        raise ValueError(
            f"Invalid sort direction {direction!r} — use 'asc' or 'desc'.")

    proto = single_visual.get("prototypeQuery") or {}
    selects = proto.get("Select") or []
    if not selects:
        raise ValueError("Visual has no prototypeQuery.Select — nothing to sort by.")
    alias2entity = {f.get("Name"): f.get("Entity") for f in proto.get("From", [])}

    ref_m = _FIELD_REF_RE.match(sort_by or "")
    want_table = (ref_m.group(1) or ref_m.group(2) or "").strip() if ref_m else None
    if want_table:
        want_table = want_table.replace("''", "'")
    want_name = ref_m.group(3).strip() if ref_m else (sort_by or "").strip()
    if not want_name:
        raise ValueError("sort_by must name a field on the visual.")

    def _inner(sel):
        if "Aggregation" in sel:
            inner = sel["Aggregation"].get("Expression", {})
            return inner.get("Column") or inner.get("Measure") or {}
        return sel.get("Measure") or sel.get("Column") or {}

    def _matches(sel, fold):
        node = _inner(sel)
        prop = node.get("Property", "")
        src = node.get("Expression", {}).get("SourceRef", {})
        entity = alias2entity.get(src.get("Source"), src.get("Entity")) or ""
        name = sel.get("Name", "")
        w_name, w_table = fold(want_name), fold(want_table or "")
        if ref_m:  # bracketed: match Property (+ entity when qualified)
            return fold(prop) == w_name and (not want_table or fold(entity) == w_table)
        return (fold(name) == w_name or fold(prop) == w_name
                or fold(f"{entity}.{prop}") == w_name)

    match = next((s for s in selects if _matches(s, str)), None)
    if match is None:  # retry case-insensitively (PBI names are case-insensitive)
        match = next((s for s in selects if _matches(s, str.lower)), None)
    if match is None:
        available = ", ".join(repr(s.get("Name", "?")) for s in selects)
        raise ValueError(
            f"sort_by {sort_by!r} matches none of the visual's fields. "
            f"Available: {available}")

    expr = None
    for key in ("Aggregation", "Measure", "Column"):
        if key in match:
            expr = {key: copy.deepcopy(match[key])}
            break
    if expr is None:
        # e.g. a HierarchyLevel select (Desktop date hierarchies) — refuse
        # cleanly rather than author an OrderBy shape Desktop never writes.
        raise ValueError(
            f"Field {match.get('Name', sort_by)!r} has an unsupported "
            "expression shape for sorting — only Column, Measure, and "
            "Aggregation fields can be sorted.")
    proto["OrderBy"] = [{"Direction": dcode, "Expression": expr}]
    return match.get("Name", ""), dcode


def compile_visual_binding(single_visual: dict, resolve_type=None):
    """Compile ``query`` and ``dataTransforms`` dicts for one visual.

    Parameters
    ----------
    single_visual : dict
        The ``config.singleVisual`` object (needs ``projections`` and
        ``prototypeQuery`` with a non-empty ``Select``).
    resolve_type : callable | None
        ``resolve_type(entity, prop, is_measure) -> data_type_str | None``.
        Used only to pick a column's type code; measures are self-describing.

    Returns
    -------
    (query, data_transforms) : tuple[dict, dict] | (None, None)
        ``(None, None)`` when the visual has no bindable projections (textbox,
        shape, image, button, …) and therefore needs no data binding.

    Side effect: bare value-role columns are rewritten to implicit Aggregations
    IN ``single_visual`` itself (prototypeQuery + projections) via
    :func:`apply_implicit_aggregations` — the caller must persist the mutated
    config, or Desktop re-derives an unaggregated query and the chart is empty.
    """
    proto = single_visual.get("prototypeQuery")
    projections = single_visual.get("projections") or {}
    visual_type = single_visual.get("visualType", "")
    if not proto or not proto.get("Select"):
        return None, None

    # Mirror Desktop's field well: a numeric column dropped on a value axis is
    # stored as Sum(...) in the PROTOTYPE (and the projection re-pointed).
    apply_implicit_aggregations(single_visual, resolve_type)

    selects = proto["Select"]
    alias2entity = {f.get("Name"): f.get("Entity") for f in proto.get("From", [])}
    ref2idx = {s.get("Name"): i for i, s in enumerate(selects)}

    def _node(sel):
        # Unwrap an Aggregation (e.g. Sum(col)) to its inner Column/Measure so the
        # Property / SourceRef helpers work for aggregate selects too. A
        # hierarchy level (Desktop's Date.Calendar.Year) reads as a column
        # whose property is its level, on its hierarchy's source (issue #190).
        if "Aggregation" in sel:
            inner = sel["Aggregation"].get("Expression", {})
            return inner.get("Column") or inner.get("Measure") or {}
        if "HierarchyLevel" in sel:
            lvl = sel["HierarchyLevel"]
            src = _hierarchy_source(lvl.get("Expression", {}).get("Hierarchy", {}))
            return {"Expression": src, "Property": lvl.get("Level", "")}
        return sel.get("Measure") or sel.get("Column") or {}

    def _native(sel):
        return _node(sel).get("Property", sel.get("Name", ""))

    def _query_native(sel):
        """The query's NativeReferenceName: a hierarchy level's is its
        hierarchy and level ("Calendar Year"), an auto date/time
        hierarchy's its column and level ("Scheduled_Dep Month"), as
        Desktop writes them."""
        if "HierarchyLevel" in sel:
            lvl = sel["HierarchyLevel"]
            hier = lvl.get("Expression", {}).get("Hierarchy", {})
            pvs = hier.get("Expression", {}).get("PropertyVariationSource")
            base = pvs.get("Property", "") if pvs else hier.get("Hierarchy", "")
            return f"{base} {lvl.get('Level', '')}".strip()
        return _native(sel)

    def _entity_of(sel):
        src = _node(sel).get("Expression", {}).get("SourceRef", {})
        return alias2entity.get(src.get("Source"), src.get("Entity"))

    def _rewrite_src(node):
        ref = node.get("Expression", {}).get("SourceRef", {})
        node["Expression"]["SourceRef"] = {
            "Entity": alias2entity.get(ref.get("Source"), ref.get("Source"))}

    def _entity_expr(sel):
        """The Column / Measure / Aggregation expr with SourceRef.Source rewritten
        to .Entity (implicit value-role aggregations were already applied to the
        prototype by :func:`apply_implicit_aggregations`)."""
        if "Aggregation" in sel:
            agg = copy.deepcopy(sel["Aggregation"])
            inner = agg.get("Expression", {})
            for key in ("Column", "Measure"):
                if key in inner:
                    _rewrite_src(inner[key])
            return {"Aggregation": agg}
        if "HierarchyLevel" in sel:
            lvl = copy.deepcopy(sel["HierarchyLevel"])
            hier = lvl.get("Expression", {}).get("Hierarchy")
            if hier is not None:
                pvs = hier.get("Expression", {}).get("PropertyVariationSource")
                _rewrite_src(pvs if pvs is not None else hier)
            return {"HierarchyLevel": lvl}
        key = "Measure" if "Measure" in sel else "Column"
        node = copy.deepcopy(sel[key])
        _rewrite_src(node)
        return {key: node}

    # Deduplicate NativeReferenceName across selects (query only): two fields
    # sharing a Property name (Products.ProductID and Sales.ProductID) get
    # "ProductID" and "ProductID1", matching Desktop. The queryMetadata
    # Restatement and dataTransforms displayName keep the raw name.
    native_names: list[str] = []
    _seen: dict[str, int] = {}
    for s in selects:
        base = _query_native(s)
        cnt = _seen.get(base, -1) + 1
        _seen[base] = cnt
        native_names.append(base if cnt == 0 else f"{base}{cnt}")

    # --- query: SemanticQueryDataShapeCommand ---
    q = copy.deepcopy(proto)
    for i, s in enumerate(q["Select"]):
        s["NativeReferenceName"] = native_names[i]
    n = len(selects)
    all_idx = list(range(n))
    roles_present = set(projections.keys())

    # role -> ordered select indices (used by both binding + dataTransforms)
    projection_ordering: dict[str, list[int]] = {}
    ref2role: dict[str, str] = {}
    for role, items in projections.items():
        idxs = []
        for it in items:
            ref = it.get("queryRef")
            if ref in ref2idx:
                idxs.append(ref2idx[ref])
                ref2role[ref] = role
        if idxs:
            projection_ordering[role] = idxs

    is_matrix = visual_type in _MATRIX_TYPES
    is_slicer = visual_type in _SLICER_TYPES
    is_table = visual_type in _TABLE_TYPES
    # A binding is "pivoted" when a Series/legend dimension is crossed with a
    # value axis (matches the Desktop-authored sales_demo pie: Series + Y). This
    # is a data-shape property of pie/donut/stacked charts — NOT matrices, which
    # express rows-vs-columns through a Secondary grouping instead.
    pivoted = "Series" in roles_present and bool(roles_present & _VALUE_ROLES)

    if is_matrix:
        # matrix / pivotTable: rows on the Primary axis (one grouping per row
        # level), columns + values crossed on the Secondary axis. Ground truth:
        # Matrix Bubble Chart.pbix + Contoso IBCS. A matrix with no column field
        # collapses to a flat single-grouping table.
        rows = projection_ordering.get("Rows", [])
        cols = projection_ordering.get("Columns", [])
        vals = projection_ordering.get("Values", [])
        if not (rows or cols or vals):        # untagged projections -> rows
            rows = all_idx
        if cols:
            primary = [{"Projections": [i]} for i in rows] or [{"Projections": []}]
            binding = {
                "Primary": {"Groupings": primary},
                "Secondary": {"Groupings": [{"Projections": cols + vals}]},
                "DataReduction": {"DataVolume": 3,
                                  "Primary": {"Window": {"Count": 100}},
                                  "Secondary": {"Top": {"Count": 100}}},
                "Version": 1,
            }
        else:
            binding = {
                "Primary": {"Groupings": [{"Projections": rows + vals, "Subtotal": 1}]},
                "DataReduction": {"DataVolume": 3, "Primary": {"Window": {"Count": 500}}},
                "Version": 1,
            }
    elif is_slicer:
        # slicer: enumerate every distinct value (empty Window, no Count) and
        # keep empty groups. Ground truth: AI Sample / Cars Sales / Matrix Bubble.
        binding = {
            "Primary": {"Groupings": [{"Projections": all_idx}]},
            "DataReduction": {"DataVolume": 3, "Primary": {"Window": {}}},
            "IncludeEmptyGroups": True,
            "Version": 1,
        }
    else:
        grouping: dict = {"Projections": all_idx}
        if is_table:
            grouping["Subtotal"] = 1
        binding = {
            "Primary": {"Groupings": [grouping]},
            "DataReduction": _data_reduction(visual_type),
            "Version": 1,
        }
        if pivoted:
            binding["isPivoted"] = True
    query = {"Commands": [{"SemanticQueryDataShapeCommand": {
        "Query": q, "Binding": binding, "ExecutionMetricsKind": 1,
    }}]}

    # --- dataTransforms ---
    # A DataRole is "active" for the dimensions the visual pivots on: matrix
    # rows/columns, and slicer values (Desktop marks these isActive:true).
    def _is_active(role: str) -> bool:
        if is_matrix:
            return role in ("Rows", "Columns")
        if is_slicer:
            return True
        return False

    data_roles: list[dict] = []
    for role, idxs in projection_ordering.items():
        for ix in idxs:
            data_roles.append({"Name": role, "Projection": ix,
                               "isActive": _is_active(role)})

    qm_select: list[dict] = []
    dt_selects: list[dict] = []
    for i, s in enumerate(selects):
        ref = s.get("Name")
        native = _native(s)  # raw (un-deduped) for Restatement / displayName
        is_measure = "Measure" in s
        dtype = None
        if resolve_type is not None:
            try:
                dtype = resolve_type(_entity_of(s), _native(s), is_measure)
            except Exception:
                dtype = None
        underlying, tcode = _type_codes(dtype)
        qm_select.append({"Restatement": native, "Name": ref, "Type": tcode})
        role = ref2role.get(ref)
        dt_selects.append({
            "displayName": native,
            "queryName": ref,
            "roles": ({role: True} if role else {}),
            "type": {"category": None, "underlyingType": underlying},
            "expr": _entity_expr(s),
        })

    data_transforms = {
        "projectionOrdering": projection_ordering,
        "queryMetadata": {"Select": qm_select},
        "visualElements": [{"DataRoles": data_roles}],
        "selects": dt_selects,
    }

    # --- field parameters -------------------------------------------------
    # A field-parameter binding stores the currently-RESOLVED field in the
    # projections/Select (handled above like any field) and the parameter
    # linkage in queryFieldParametersByRole. The COMPILED query additionally
    # needs the parameter table joined in and a Where clause selecting the
    # resolved field through the parameter's hidden "<Display> Fields" column,
    # and the resolved select's dataTransforms entry needs
    # sourceFieldParameters provenance. All three shapes are diffed against a
    # Desktop-authored binding (OpenBI findings #19 ground truth). Without
    # them Desktop silently degrades the well to an implicit Count.
    qfp = single_visual.get("queryFieldParametersByRole") or {}
    if qfp:
        _apply_field_parameters(
            q, data_transforms, qfp, projection_ordering, selects,
            _entity_of, _native)

    return query, data_transforms


def _nameof_literal(entity: str, prop: str) -> str:
    """The Literal.Value Desktop stores for a field selected through a
    parameter: the canonical ``'Table'[Field]`` ref (what NAMEOF() evaluates
    to) wrapped as a TEXT literal -- outer single quotes, interior single
    quotes doubled. ``'Sales'[Total Revenue]`` becomes
    ``'''Sales''[Total Revenue]'``."""
    canonical = f"'{entity}'[{prop}]"
    return "'" + canonical.replace("'", "''") + "'"


def _apply_field_parameters(q, data_transforms, qfp, projection_ordering,
                            selects, _entity_of, _native):
    """Stamp the compiled query + dataTransforms with field-parameter wiring.

    ``q`` is the compiled Query dict (already deep-copied from the prototype),
    ``qfp`` is singleVisual.queryFieldParametersByRole. For every parameter
    entry: join the parameter's table into From, add a Where In-condition over
    its hidden ``<Display> Fields`` column selecting the resolved field(s),
    and mark the covered dataTransforms selects with sourceFieldParameters.
    """
    from_list = q.setdefault("From", [])
    used_aliases = {f.get("Name") for f in from_list}
    entity_alias = {f.get("Entity"): f.get("Name") for f in from_list}

    def _alias_for(entity: str) -> str:
        hit = entity_alias.get(entity)
        if isinstance(hit, str):
            return hit
        base = (entity[:1].lower() or "t")
        name = base
        n = 0
        while name in used_aliases:
            n += 1
            name = f"{base}{n}"
        used_aliases.add(name)
        entity_alias[entity] = name
        from_list.append({"Name": name, "Entity": entity, "Type": 0})
        return name

    dt_selects = data_transforms["selects"]
    for role, entries in qfp.items():
        role_idxs = projection_ordering.get(role, [])
        for entry in entries:
            expr = (entry.get("expr") or {}).get("Column") or {}
            param_entity = (expr.get("Expression", {})
                            .get("SourceRef", {}).get("Entity"))
            display_col = expr.get("Property")
            if not param_entity or not display_col:
                continue
            start = int(entry.get("index", 0))
            length = int(entry.get("length", 1))
            covered = role_idxs[start:start + length]
            if not covered:
                continue
            alias = _alias_for(param_entity)
            # By this project's own add_field_parameter convention (and the
            # Desktop-authored ground truth), the hidden NAMEOF column is
            # "<display column> Fields".
            fields_col = f"{display_col} Fields"
            values = []
            for ix in covered:
                sel = selects[ix]
                values.append([{"Literal": {"Value": _nameof_literal(
                    _entity_of(sel), _native(sel))}}])
            q.setdefault("Where", []).append({"Condition": {"In": {
                "Expressions": [{"Column": {
                    "Expression": {"SourceRef": {"Source": alias}},
                    "Property": fields_col}}],
                "Values": values,
            }}})
            provenance = [{
                "expr": {"_kind": 2,
                         "source": {"_kind": 0, "entity": param_entity},
                         "ref": display_col},
                "displayName": display_col,
            }]
            for ix in covered:
                if 0 <= ix < len(dt_selects):
                    dt_selects[ix]["sourceFieldParameters"] = provenance
