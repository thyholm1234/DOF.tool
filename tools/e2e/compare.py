"""Sammenlign to røgtest-kørsler: fejl, overskrifter, faner, kort, felter og tabeller skal være de samme."""
import json, sys
a, b = (json.load(open(f)) for f in sys.argv[1:3])
bad = 0
for route, x in a.items():
    if route.startswith("_"):
        continue
    y = b.get(route)
    if y is None:
        print("MANGLER", route); bad += 1; continue
    problems = []
    for k in ("pageerror", "console", "http", "errors"):
        if y[k]:
            problems.append(f"{k}: {y[k][:2]}")
    for k in ("h1", "tabs", "maps", "inputs", "tables"):
        if x[k] != y[k]:
            problems.append(f"{k}: {x[k]} → {y[k]}")
    if abs(x["buttons"] - y["buttons"]) > max(3, x["buttons"] * 0.1):
        problems.append(f"buttons: {x['buttons']} → {y['buttons']}")
    if problems:
        bad += 1
        print(route, *problems, sep="\n   ")
print(f"{len([r for r in a if not r.startswith('_')])} sider, {bad} med forskelle")
for k in ("_startup_ms", "_scripts"):
    print(k, a.get(k), "→", b.get(k))
