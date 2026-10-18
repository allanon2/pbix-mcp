"""Clean-room evidence for a visual group's layout mode (pbix-mcp remediation,
2026-10-19): in Desktop-saved classic layouts, what values does
singleVisualGroup.groupMode take, and do the groups with each value hold
members that extend past the group's own frame (content one would have to
scroll to)? Input: the community / Microsoft dashboards in pbix-mcp's
test_samples (Desktop-authored; engine-built variants excluded by name)."""
import collections
import glob
import pathlib
import json
import zipfile

COMMUNITY = {"Adventure Works DW 2020", "Agents Performance - Dashboard", "Arrow Chart - Microsot Financial Dataset",
 "Artificial Intelligence Sample", "Awesome Chocolates - Performance Dashboard - Chandoo.org", "Briqlab-dashboard",
 "Briqlab_Pink", "Cars Sales", "Contoso BI, Sales Dashboard - IBCS Style", "Dashboard Help Modal View",
 "Dynamic Bullet Points", "Ecommerce Conversion Dashboard", "Exceutive Sales Report - Toast Notifictaion",
 "Furniture Sales - Final - Dashboard", "GeoSales Dashboard - Azure Map", "Google Analytics Style of Reporting",
 "IT_Support_Ticket_Desk", "Map with Pie Chart Bubbles", "Matrix Bubble Chart", "Medium Blog - Example",
 "PQ - Getting Data from Todoist via API", "PremiumFoodSales - Final Dashboard", "Root Cause Analysis Dashboard",
 "Root-Cause Top-Bottom Visual", "Sales Teams Quarterly Performance", "Skyline Visual", "Target Line Bar Charts",
 "Wow2025W41"}


def rect(vc, cfg):
    lay = (cfg.get("layouts") or [{}])[0].get("position") or {}
    return [float(lay.get(k, vc.get(k, 0)) or 0) for k in ("x", "y", "width", "height")]


seen = {}
for p in glob.glob(str(pathlib.Path(__file__).resolve().parents[4] / "test_samples" / "**" / "*.pbix"), recursive=True):
    base = p.replace("\\", "/").split("/")[-1][:-5]
    if base in COMMUNITY and base not in seen:
        seen[base] = p

modes = collections.Counter()
overflow = collections.Counter()
examples = collections.defaultdict(list)
keys = collections.Counter()
for base, p in sorted(seen.items()):
    try:
        lay = json.loads(zipfile.ZipFile(p).read("Report/Layout").decode("utf-16-le"))
    except Exception:
        continue
    for s in lay.get("sections", []):
        groups, members = {}, collections.defaultdict(list)
        for vc in s.get("visualContainers", []):
            try:
                cfg = json.loads(vc.get("config", "{}"))
            except Exception:
                continue
            g = cfg.get("singleVisualGroup")
            if isinstance(g, dict):
                groups[cfg.get("name")] = (g, rect(vc, cfg))
                keys.update(g.keys())
            if cfg.get("parentGroupName"):
                members[cfg["parentGroupName"]].append(rect(vc, cfg))
        for name, (g, (gx, gy, gw, gh)) in groups.items():
            mode = g.get("groupMode", "<absent>")
            modes[mode] += 1
            out = any(x + w > gx + gw + 0.5 or y + h > gy + gh + 0.5 or x < gx - 0.5 or y < gy - 0.5
                      for x, y, w, h in members.get(name, []))
            overflow[(mode, out)] += 1
            if out and len(examples[mode]) < 3:
                examples[mode].append(f"{base} / {s.get('displayName')} / {g.get('displayName')}")

print("files:", len(seen))
print("keys in singleVisualGroup:", dict(keys))
print("groupMode values:", dict(modes))
print("(groupMode, a member extends past the group's frame):", dict(overflow))
for m, ex in examples.items():
    print("  overflowing groups with groupMode", m, ":", ex)
