"""Clean-room evidence for ThemeDataColor: which ColorIds and Percents the
Desktop-saved community files use (pbix-mcp remediation, 2026-10-19)."""
import collections
import glob
import pathlib
import re
import zipfile

from census_groupmode import COMMUNITY

seen = {}
for p in glob.glob(str(pathlib.Path(__file__).resolve().parents[4] / "test_samples" / "**" / "*.pbix"), recursive=True):
    b = p.replace("\\", "/").split("/")[-1][:-5]
    if b in COMMUNITY and b not in seen:
        seen[b] = p
pat = re.compile(r'ColorId\\?"\s*:\s*(\d+)\s*,\s*\\?"Percent\\?"\s*:\s*(-?[\d.]+)')
ids = collections.Counter()
files = collections.defaultdict(set)
shades = collections.defaultdict(collections.Counter)
for b, p in sorted(seen.items()):
    try:
        lay = zipfile.ZipFile(p).read("Report/Layout").decode("utf-16-le")
    except Exception:
        continue
    for m in pat.finditer(lay):
        n, pct = int(m.group(1)), float(m.group(2))
        ids[n] += 1
        files[n].add(b)
        shades[n]["darker" if pct < 0 else "lighter" if pct > 0 else "plain"] += 1
print("ColorId -> references:", sorted(ids.items()))
print("highest ColorId:", max(ids) if ids else None)
print("files per ColorId:", {n: len(f) for n, f in sorted(files.items())})
print("shading per ColorId:", {n: dict(c) for n, c in sorted(shades.items())})
