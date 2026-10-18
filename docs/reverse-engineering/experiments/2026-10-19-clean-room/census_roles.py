"""Clean-room evidence for which wells are VALUE wells: in the community
Desktop-saved files, how often each (visual type, role) holds a measure, an
aggregated column, or a bare column (pbix-mcp remediation, 2026-10-19). A
well Desktop fills with measures and aggregates is a value well; one it
fills with bare columns is a grouping well."""
import collections
import glob
import pathlib
import json
import zipfile

from census_groupmode import COMMUNITY

seen = {}
for p in glob.glob(str(pathlib.Path(__file__).resolve().parents[4] / "test_samples" / "**" / "*.pbix"), recursive=True):
    b = p.replace("\\", "/").split("/")[-1][:-5]
    if b in COMMUNITY and b not in seen:
        seen[b] = p

held = collections.defaultdict(collections.Counter)
files = collections.defaultdict(set)
for b, p in sorted(seen.items()):
    try:
        lay = json.loads(zipfile.ZipFile(p).read("Report/Layout").decode("utf-16-le"))
    except Exception:
        continue
    for s in lay.get("sections", []):
        for vc in s.get("visualContainers", []):
            try:
                sv = json.loads(vc.get("config", "{}")).get("singleVisual") or {}
            except Exception:
                continue
            sel = {x.get("Name"): x for x in (sv.get("prototypeQuery") or {}).get("Select") or []}
            for role, projs in (sv.get("projections") or {}).items():
                for pr in projs or []:
                    item = sel.get(pr.get("queryRef")) or {}
                    kind = ("measure" if "Measure" in item else "aggregate" if "Aggregation" in item
                            else "column" if "Column" in item else "hierarchy" if "HierarchyLevel" in item
                            else "other")
                    held[(sv.get("visualType"), role)][kind] += 1
                    files[(sv.get("visualType"), role)].add(b)

for (vt, role), c in sorted(held.items(), key=lambda kv: (kv[0][1], str(kv[0][0]))):
    if vt and not any(ch.isdigit() for ch in vt[-6:]):        # built-in visuals only
        print(f"{role:<16} {vt:<34} {dict(c)}  files={len(files[(vt, role)])}")
