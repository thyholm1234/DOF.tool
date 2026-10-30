"""Røgtest af frontend: besøg alle sider og faner, registrér JS-fejl, mislykkede API-kald, API-tider og sidens struktur.

Brug: smoke.py <session-token> <out.json>      (se README.md)
Miljø: E2E_BASE (standard http://localhost:8090), CLICK=1 (klik alle faner/filtre), OLD_WEB=<mappe> (kør en anden
frontend, fx fra en git-worktree, mod samme API – til før/efter-sammenligning).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE", "http://localhost:8090").rstrip("/")
TOKEN, OUT = sys.argv[1], sys.argv[2]


def api(path):
    req = urllib.request.Request(BASE + path, headers={"Cookie": f"dt_session={TOKEN}"})
    return json.load(urllib.request.urlopen(req))


IDS = os.path.join(os.path.dirname(os.path.abspath(OUT)), "ids.json")
if os.path.exists(IDS):  # samme tråde/arter/lokaliteter som første kørsel, så kørslerne kan sammenlignes
    t, su = json.load(open(IDS))
else:
    feed = api("/api/feed?level=bemaerk&scope=dk")["threads"]
    t = feed[0]
    su = next((x for x in feed if x["kategori"] in ("SU", "SUB")), t)
    json.dump([{k: t[k] for k in ("dato", "artnr", "loknr")}, {k: su[k] for k in ("dato", "artnr", "loknr")}], open(IDS, "w"))
thread = f"/traad/{t['dato']}/{t['artnr']}/{t['loknr']}"
ROUTES = [
    "/", "/?level=SU", "/?level=local", "/?level=pheno", thread, f"/traad/{su['dato']}/{su['artnr']}/{su['loknr']}",
    "/lister", "/lister?tab=huller", "/lister?tab=score", "/lister?tab=grupper", "/matrikel", "/matrikel?tab=liste",
    "/matrikel?tab=score", "/matrikel?tab=matrikler",
    "/traek", "/traek?tab=status", "/traek?tab=ankomst", "/traek?tab=vejr", "/traek?tab=klima", "/traek?tab=metode",
    "/kort", "/kort?tab=dag", "/puls", "/arter", f"/art/{su['artnr']}", f"/art/{t['artnr']}", "/lokaliteter",
    f"/lok/{t['loknr']}", f"/lok/{t['loknr']}/art/{t['artnr']}", "/soeg", "/statistik", "/statistik?tab=nfl",
    "/laer", "/mobgang", "/alarmer", "/alarmer?tab=omraader", "/alarmer?tab=enheder", "/profil",
    "/admin?tab=drift", "/admin?tab=data", "/admin?tab=vejr", "/admin?tab=aktivitet", "/admin?tab=support",
    "/admin?tab=brugere", "/admin?tab=roller", "/admin?tab=funktioner",
    "/moderation?tab=moderation", "/moderation?tab=indhold", "/moderation?tab=ture", "/moderation?tab=arter",
    "/indtast", "/indtast/tur/ny", "/support", "/dku", "/ture", "/kalender", "/kalender/mine",
    "/dofbasen", "/dofbasen/observationer", "/dofbasen/soeg", "/dofbasen/opslag", "/dofbasen/faeno", "/dofbasen/nyheder",
    "/netfugl", "/netfugl/artikler", "/netfugl/kryds", "/netfugl/arter", "/netfugl/fotos",
]
SKIP_URLS = ("tile.openstreetmap.org",)


def snapshot(page):
    return page.evaluate("""() => {
      const v = document.querySelector('#view');
      const q = (s) => v.querySelectorAll(s).length;
      return {
        h1: [...v.querySelectorAll('h1,h2')].slice(0, 6).map(e => e.textContent.trim().slice(0, 60)),
        tabs: [...v.querySelectorAll('.seg button')].slice(0, 12).map(e => e.textContent.trim()),
        maps: q('.leaflet-container'), buttons: q('button'), inputs: q('input,select,textarea'), links: q('a'),
        charts: q('svg'), tables: q('table'), errors: [...v.querySelectorAll('.notice.err')].map(e => e.textContent.slice(0, 120)),
        text: v.innerText.length,
      };
    }""")


results = {}
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="da-DK", timezone_id="Europe/Copenhagen")
    ctx.add_cookies([{"name": "dt_session", "value": TOKEN, "url": BASE}])
    page = ctx.new_page()
    old_web = os.environ.get("OLD_WEB")  # frontend fra en anden mappe (fx en git-worktree) – til før/efter-målinger
    if old_web:
        import mimetypes, re as _re

        def serve(route):
            path = urllib.parse.urlsplit(route.request.url).path
            rel = "index.html" if path in ("/", "/index.html") else _re.sub(r"^/v/[^/]+/", "", path).lstrip("/")
            f = os.path.join(old_web, rel)
            if not os.path.isfile(f):
                return route.continue_()
            body = open(f, "rb").read()
            if rel == "index.html":
                body = body.replace(b"__BUILD__", b"old")
            route.fulfill(status=200, body=body, headers={"Content-Type": mimetypes.guess_type(f)[0] or "application/octet-stream",
                                                          "Cache-Control": "no-cache"})
        page.route(_re.compile("^" + _re.escape(BASE) + r"/($|index\.html|v/|vendor/)"), serve)
    log = {"console": [], "page": [], "http": []}
    page.on("console", lambda m: m.type == "error" and log["console"].append(m.text[:200]))
    page.on("pageerror", lambda e: log["page"].append(str(e)[:200]))
    page.on("response", lambda r: r.status >= 400 and not any(s in r.url for s in SKIP_URLS)
            and log["http"].append(f"{r.status} {r.request.method} {r.url.replace(BASE, '')[:120]}"))
    page.on("requestfailed", lambda r: not any(s in r.url for s in SKIP_URLS)
            and log["http"].append(f"FAILED {r.url.replace(BASE, '')[:120]} {r.failure}"))
    page.goto(BASE + "/")
    page.wait_for_selector("#sidenav a", timeout=20000)
    times = []
    for _ in range(6):  # første er opvarmning; median af resten
        t0 = time.time()
        page.goto(BASE + "/")
        page.wait_for_selector("#view .threads > *, #view .empty", timeout=20000)
        times.append(round((time.time() - t0) * 1000))
    results["_startup_ms"] = sorted(times[1:])[2]
    results["_nav"] = page.evaluate("() => [...document.querySelectorAll('#sidenav a')].map(a => a.textContent.trim())")
    results["_scripts"] = page.evaluate("() => performance.getEntriesByType('resource').filter(r => /\\.js|\\.css/.test(r.name)).map(r => r.name.replace(location.origin, '').replace(/\\/v\\/[^/]+/, '/v/B'))")
    for route in ROUTES:
        for k in log:
            log[k].clear()
        page.goto(BASE + "/#" + route)
        page.wait_for_timeout(300)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001 – SSE/polling holder forbindelsen åben
            pass
        page.wait_for_timeout(700)
        api_ms = page.evaluate("() => performance.getEntriesByType('resource').filter(r => r.name.includes('/api/') && !r.name.includes('/api/stream')).map(r => [r.name.replace(location.origin, '').slice(0, 90), Math.round(r.duration)])")
        page.evaluate("() => performance.clearResourceTimings()")
        clicks = 0
        if os.environ.get("CLICK"):  # klik alle faner og filterknapper på siden (kun .seg og .chip – de ændrer ingen data)
            n = page.evaluate("() => document.querySelectorAll('#view .seg button, #view .chips button.chip').length")
            for i in range(min(n, 40)):
                try:
                    btn = page.locator("#view .seg button, #view .chips button.chip").nth(i)
                    if btn.is_visible():
                        btn.click(timeout=2000)
                        clicks += 1
                        page.wait_for_timeout(350)
                except Exception:  # noqa: BLE001 – knappen kan være forsvundet efter forrige klik
                    pass
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
        results[route] = {**snapshot(page), "api_ms": api_ms, "clicks": clicks, "console": list(log["console"]), "pageerror": list(log["page"]), "http": list(log["http"])}
        print(route, "FEJL" if log["page"] or log["console"] or results[route]["errors"] else "ok", flush=True)
    browser.close()
json.dump(results, open(OUT, "w"), ensure_ascii=False, indent=1)
