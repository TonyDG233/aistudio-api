import json
import sys
from pathlib import Path

import yaml

CFG = Path(r"D:\LLM\self_programming\aistudio-api\config.yaml")
mode = sys.argv[1]
if mode == "restore":
    route_val = False
    groups = []
    build_surface = False
else:
    payload = json.loads(Path(mode).read_text(encoding="utf-8"))
    route_val = bool(payload.get("route_by_tier"))
    groups = payload.get("groups") or []
    build_surface = bool(payload.get("build_surface"))

text = CFG.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)


def find_line(pred):
    for i, ln in enumerate(lines):
        if pred(ln.rstrip("\r\n")):
            return i
    return -1


idx = find_line(lambda s: s.startswith("  route_by_tier:"))
if idx < 0:
    raise SystemExit("route_by_tier line not found")
lines[idx] = f"  route_by_tier: {'true' if route_val else 'false'}\n"

bidx = find_line(lambda s: s.startswith("  build_surface:"))
if bidx < 0:
    raise SystemExit("build_surface line not found")
lines[bidx] = f"  build_surface: {'true' if build_surface else 'false'}\n"

gidx = find_line(lambda s: s == "  groups: []" or s == "  groups:")
if gidx < 0:
    raise SystemExit("groups line not found")

end = gidx + 1
if lines[gidx].rstrip("\r\n") == "  groups:":
    while end < len(lines):
        s = lines[end].rstrip("\r\n")
        if s.startswith("  - ") or s.startswith("    "):
            end += 1
            continue
        break

if groups:
    blob = yaml.safe_dump(
        groups, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    render = "  groups:\n" + "\n".join(
        "  " + ln for ln in blob.rstrip("\n").split("\n")
    ) + "\n"
else:
    render = "  groups: []\n"

lines = lines[:gidx] + [render] + lines[end:]
CFG.write_text("".join(lines), encoding="utf-8")
print(f"config.yaml updated: route_by_tier={route_val} build_surface={build_surface} groups={len(groups)} items")