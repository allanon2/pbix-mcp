"""Clean-room evidence for field-well summarization (pbix-mcp remediation,
2026-10-19). For every column a Desktop-saved visual binds (the community /
Microsoft dashboards in pbix-mcp's test_samples), record the visual type, the
well (projection role), the column's data type, default summarization and data
category from the model's metadata, and what Desktop saved: the plain column,
or an Aggregation and its function. Codes: Microsoft's public Tabular model
enums (DataType, AggregateFunction) and the public semantic query schema
(QueryAggregateFunction: 0 Sum, 1 Average, 2 Distinct count, 3 Min, 4 Max,
5 Count, 6 Median, 7 StdDev, 8 Variance)."""
import collections
import glob
import pathlib
import json
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "src"))
from pbix_mcp.formats.model_reader import ModelReader  # noqa: E402

from census_groupmode import COMMUNITY  # noqa: E402

DT = {1: "auto", 2: "string", 6: "int64", 8: "double", 9: "datetime", 10: "decimal", 11: "boolean", 17: "binary"}
SB = {1: "default", 2: "none", 3: "sum", 4: "min", 5: "max", 6: "count", 7: "average", 8: "distinctcount"}

seen = {}
for p in glob.glob(str(pathlib.Path(__file__).resolve().parents[4] / "test_samples" / "**" / "*.pbix"), recursive=True):
    base = p.replace("\\", "/").split("/")[-1][:-5]
    if base in COMMUNITY and base not in seen:
        seen[base] = p

rows = []
for base, p in sorted(seen.items()):
    try:
        lay = json.loads(zipfile.ZipFile(p).read("Report/Layout").decode("utf-16-le"))
        meta = ModelReader(p)._query_metadata(
            "SELECT t.Name AS tbl, COALESCE(c.ExplicitName, c.InferredName) AS col, "
            "CASE WHEN c.ExplicitDataType IN (1, 19) OR c.ExplicitDataType IS NULL THEN c.InferredDataType ELSE c.ExplicitDataType END AS dt, c.SummarizeBy AS sb, c.DataCategory AS cat "
            "FROM [Column] c JOIN [Table] t ON c.TableID = t.ID")
    except Exception as e:  # noqa: BLE001
        print("skip", base, type(e).__name__, str(e)[:80], file=sys.stderr)
        continue
    cols = {(m["tbl"], m["col"]): m for m in meta}
    for s in lay.get("sections", []):
        for vc in s.get("visualContainers", []):
            try:
                sv = json.loads(vc.get("config", "{}")).get("singleVisual") or {}
            except Exception:
                continue
            q = sv.get("prototypeQuery") or {}
            src = {f.get("Name"): f.get("Entity") for f in q.get("From") or []}
            sel = {x.get("Name"): x for x in q.get("Select") or []}
            for role, projs in (sv.get("projections") or {}).items():
                for pr in projs or []:
                    item = sel.get(pr.get("queryRef"))
                    if not item:
                        continue
                    if "Column" in item:
                        col, outcome = item["Column"], "column"
                    elif "Aggregation" in item and "Column" in (item["Aggregation"].get("Expression") or {}):
                        col = item["Aggregation"]["Expression"]["Column"]
                        outcome = f"agg{item['Aggregation'].get('Function')}"
                    else:
                        continue                                    # measures, hierarchy levels
                    ent = src.get(((col.get("Expression") or {}).get("SourceRef") or {}).get("Source"))
                    m = cols.get((ent, col.get("Property")))
                    if not m:
                        continue
                    rows.append({"file": base, "vtype": sv.get("visualType"), "role": role,
                                 "dt": DT.get(m["dt"], m["dt"]), "sb": SB.get(m["sb"], m["sb"]),
                                 "cat": m["cat"] or "", "out": outcome})

json.dump(rows, open("wells.json", "w"), indent=0)
print("files", len(seen), "bindings", len(rows))
by = collections.Counter((r["role"], r["dt"], r["sb"], r["cat"], r["out"]) for r in rows)
for k, n in sorted(by.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], -kv[1])):
    print(f"{n:5}  role={k[0]:<14} dt={k[1]:<9} sb={k[2]:<13} cat={k[3]:<12} -> {k[4]}")
