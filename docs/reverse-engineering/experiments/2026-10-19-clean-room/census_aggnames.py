"""Clean-room evidence for the queryRef names Desktop gives aggregates: every
"Name" / "queryRef" of the form Prefix(Entity.Property) in the community
Desktop-saved files, with the Function code of the Aggregation it names when
the same select carries one (pbix-mcp remediation, 2026-10-19)."""
import collections
import glob
import pathlib
import json
import re
import zipfile

from census_groupmode import COMMUNITY

seen = {}
for p in glob.glob(str(pathlib.Path(__file__).resolve().parents[4] / "test_samples" / "**" / "*.pbix"), recursive=True):
    b = p.replace("\\", "/").split("/")[-1][:-5]
    if b in COMMUNITY and b not in seen:
        seen[b] = p

NAME = re.compile(r"^([A-Za-z]+)\((.+)\)$")
pairs = collections.Counter()
files = collections.defaultdict(set)


def walk(node, base):
    if isinstance(node, dict):
        agg = node.get("Aggregation")
        name = node.get("Name")
        if isinstance(agg, dict) and isinstance(name, str):
            m = NAME.match(name)
            if m:
                pairs[(m.group(1), agg.get("Function"))] += 1
                files[(m.group(1), agg.get("Function"))].add(base)
        for v in node.values():
            walk(v, base)
    elif isinstance(node, list):
        for v in node:
            walk(v, base)
    elif isinstance(node, str) and node.startswith("{") and ("Aggregation" in node):
        try:
            walk(json.loads(node), base)
        except Exception:
            pass


for b, p in sorted(seen.items()):
    try:
        lay = json.loads(zipfile.ZipFile(p).read("Report/Layout").decode("utf-16-le"))
    except Exception:
        continue
    walk(lay, b)

for (prefix, fn), n in sorted(pairs.items(), key=lambda kv: (kv[0][1] if kv[0][1] is not None else 99, kv[0][0])):
    print(f"Function {fn}: name prefix {prefix!r}  x{n} in {len(files[(prefix, fn)])} files")
