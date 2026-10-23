"""Henter lokaliteterne i hver storlokalitet og hvert IBA-område fra dofbasen.dk (ét kald hvert 2,5 sekund).

Brug:  python areas.py <mappe>   (mappen skal indeholde pages/_search_storlokalitet_php.html og pages/_search_ibalokalitet_php.html
fra crawl.py). Resultatet skrives til <mappe>/areas/areas.json.
"""
import re, subprocess, time, sys, json
S = sys.argv[1]
def opts(f):
    h = open(f).read()
    m = re.search(r'<select[^>]*name="storlokal".*?</select>', h, re.S)
    return [(v, re.sub(r"<[^>]+>", "", t).strip()) for v, t in re.findall(r'<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', m.group(0), re.S)]
out = {}
for kind, page, f in (("stor", "storlokalitet.php", "_search_storlokalitet_php.html"), ("iba", "ibalokalitet.php", "_search_ibalokalitet_php.html")):
    out[kind] = []
    for v, name in opts(f"{S}/pages/{f}"):
        html = subprocess.run(["curl", "-s", "-A", "Mozilla/5.0", "-d", f"storlokal={v}", f"https://dofbasen.dk/search/{page}"], capture_output=True).stdout.decode("latin-1")
        locs = [(int(n), re.sub(r"<[^>]+>", "", t).strip()) for t, n in re.findall(r"<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*\((\d+)\)", html, re.S)]
        out[kind].append({"id": int(v), "navn": name, "loknr": [n for n, _ in locs]})
        print(kind, v, name, len(locs), flush=True)
        time.sleep(2.5)
__import__('os').makedirs(f"{S}/areas", exist_ok=True)
json.dump(out, open(f"{S}/areas/areas.json", "w"), ensure_ascii=False)
