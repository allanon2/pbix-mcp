# 2026-10-19 — clean-room remediation: code that cited Power BI Desktop's application scripts

## Why

`docs/legal-and-cleanroom.md` (and OpenBI / ComposeBI's clean-room policy,
which covers pbix-mcp) forbids "proprietary Power BI JavaScript/CSS extracted
from Microsoft's application" as a source, and says that functionality
already implemented from such material is to be replaced by independently
derived behaviour, with the remediation recorded. ComposeBI's audit of
2026-10-11 (its `docs/pbix-mcp-issues-64.md`) found pbix-mcp code that named
Desktop's `desktop.min.js` as its source; this remediation also found an
older citation of Desktop's `customVisualsHost.js` the audit had missed.

| what | where (0.9.142) | cited |
|---|---|---|
| ThemeDataColor: the picker row a ColorId indexes, and the Percent shading | `server.py` `_theme_picker_colors`, `_shade_color` (0.9.121, #119 / #120) | `desktop.min.js` |
| A visual group's layout mode number | `server.py` `_PBIR_GROUP_MODES` | `desktop.min.js` |
| Field-well summarization of a bare column | `report_binding.py` `_desktop_aggregate` and its tables (0.9.140, #191) | `desktop.min.js` |
| The queryRef names of aggregates | `report_binding.py` `QUERY_AGGREGATES` (0.9.118, #104) | `customVisualsHost.js` |

Tests that encoded those sources went with them: `tests/test_issue119_theme_data_color.py`
held shade vectors computed by running Desktop's script; `tests/test_issue191_well_summarization.py`
described the script's rules.

## How this was done, honestly

The remediation was made by an AI session (Claude, for ComposeBI's owner) on
2026-10-19, on branch `clean-room-remediation` from v0.9.142. To find the
code to replace it read the flagged functions' names, their call sites and
the comments around them, which state some of the cited facts (the group
mode numbers, the names of Desktop's functions); it did not open
`desktop.min.js`, `customVisualsHost.js` or any other Microsoft application
script, and it did not read the bodies of `_desktop_aggregate` or of 0.9.140's
`apply_implicit_aggregations`. The new code was written from the evidence
below; `apply_implicit_aggregations` starts again from 0.9.139's own version,
which the audit found clean. Where the evidence does not settle a value, this
record says so and the code does not claim it.

## Evidence used

- **The community Desktop-saved files** in `test_samples` (28: Adventure Works
  DW 2020, Artificial Intelligence Sample, Awesome Chocolates, GeoSales,
  IT_Support_Ticket_Desk, ... -- the list is `COMMUNITY` in
  `2026-10-19-clean-room/census_groupmode.py`). Lawfully obtained files saved
  by Desktop; their licences are not established, so they are a format
  reference.
- **Microsoft's published schemas** (microsoft/json-schemas, MIT):
  `fabric/item/report/definition/visualContainer/2.13.0` (`GroupLayoutMode`:
  ScaleMode, ScrollMode, with their meanings) and
  `fabric/item/report/definition/semanticQuery/1.4.0`
  (`QueryAggregateFunction` 0..8 with their meanings).
- **Microsoft's public documentation**: the Tabular model's `DataType`
  and `AggregateFunction` enums, and Learn's "Work with aggregates (sum,
  average, and so on) in Power BI".
- **OpenBI's own Desktop probe 33** (ComposeBI ledger F119 / F123): shapes
  filled with ThemeDataColor references under a theme whose background and
  foreground differ from white and black, as Desktop drew them.

The census scripts are in `2026-10-19-clean-room/`; they read the samples in
`test_samples` and print the counts quoted below.

## Findings and the code that implements them

### ThemeDataColor (`server.py` `_theme_picker_colors`, `_shade_color`)

- `census_colorid.py`: ColorId runs 0..9 across 1,365 references in the
  files, never higher (9 is used 210 times in 9 files). ColorId 0 is darkened
  350 times and never lightened; 1 is lightened 106 times and never darkened
  -- the shades a picker offers for white and black.
- Probe 33: 0 drew #FFFFFF (#E6E6E6 at -10%) and 1 #000000 (#1A1A1A at +10%)
  under a theme whose background and foreground were other colours; 2 and 3
  drew the first and second data colours (#E81123, #D10F20 at -10%; #00B294).
- So the row is white, black, then the first eight data colours (ten
  swatches, as the picker shows); 10 and up resolve to nothing.
- Shading: each channel moves |Percent| of the way to white or black and a
  half rounds up -- the three halves probe 33 shows (229.5 -> 230, 25.5 -> 26,
  31.5 -> 32) settle the rounding.

### A visual group's layout mode (`server.py` `_PBIR_GROUP_MODES`)

- The schema names ScaleMode ("visuals are scaled as group resizes") and
  ScrollMode ("a scrollbar is added").
- `census_groupmode.py`: all 103 groups in the files store 0; a group scales
  by default, so 0 is ScaleMode.
- **Open:** no file stores ScrollMode. 1 is inferred from the schema's order,
  not observed.

### Aggregate codes and names (`report_binding.py` `QUERY_AGGREGATES`)

- Codes: the schema's `QueryAggregateFunction`.
- Names (`census_aggnames.py`): the files name Function 0 `Sum` (13 selects,
  3 files), 1 `Avg` (1), 3 `Min` (48, 7 files), 4 `Max` (4, 2 files). The
  other names follow the schema's descriptions: 2 `DistinctCount` (0.9.118
  called it `Count`), 5 `CountNonNull`, 6 `Median`, 7 `StandardDeviation`,
  8 `Variance`. A queryRef is how the visual's projections name the select.

### Field-well summarization (`report_binding.py` `_implicit_aggregate`, `apply_implicit_aggregations`)

`census_wells.py` (276 column bindings) and `census_roles.py` (what each well
holds):

| case | observed | rule |
|---|---|---|
| Category, Rows, Columns, Series, Target, ExplainBy text, custom visuals' grouping wells | bare, even summing numbers (over 200) | bare |
| table, slicers | bare (12 / 80), numbers too | left alone (0.9.139) |
| multi-row card Values, text | bare (8 / 8) | bare; a number is summarized (Learn's default; none observed) |
| Azure Map X / Y coordinates | bare (4 / 4) | bare (0.9.139) |
| Y, Analyze, ExplainBy numbers | aggregated by the column's default summarization: Sum -> 0 (6), Average -> 1 (1) | the default summarization, matched by name to the query codes; Default is Sum (the model's documentation) |
| a number set to "Don't summarize" | (tables, maps: bare) | bare (Learn: each value separately) |
| card / tooltips / matrix Values, text | Min (7 / 7, 4 files) | Min ("First") |
| chart value wells, text | none observed | CountNonNull (Learn: a categorical field in a numbers-only bucket is counted) |
| cardVisual Data, scatter X | hold measures (74 in 14 files; 7 / 7) | summarized like Y (Learn: scatter needs aggregated X and Y) |
| coordinates in a value well | -- | Avg (0.9.139's own rule) |

**Open:** no community file uses a gauge's Min / Max / Target wells, a KPI's
Goal or colour saturation (Gradient); they are not summarized until a Desktop
save shows how Desktop stores a column dropped there
(`tests/test_clean_room_well_summarization.py` marks them xfail, strict). A
boolean in a tooltip follows text (Min); no file shows one.

## Experiments still to run (a Desktop save each)

1. A group set to scroll: the classic `groupMode` number.
2. A numeric column dropped into a gauge's Target / Maximum, a KPI's Goal and
   a column chart's colour saturation: the saved select.
3. A numeric column in a multi-row card, and a boolean in a tooltip.

## Tests

`tests/test_clean_room_well_summarization.py` (new, each case naming its
evidence), `tests/test_issue119_theme_data_color.py` (probe 33's colours
replace the script-computed vectors), `tests/test_issue104_builder_roles.py`
(`DistinctCount`).
