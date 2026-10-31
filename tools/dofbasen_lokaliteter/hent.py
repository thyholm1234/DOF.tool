#!/usr/bin/env python3
"""Hent alle DOFbasens lokaliteter (grænse, beskrivelse, typer, kommune, afdeling, region, IBA) som JSON.

Trin 1 i docs/lokaliteter-plan.md. Kun standardbiblioteket. Kan stoppes (Ctrl-C) og startes igen – det, der er hentet,
hentes ikke igen.

    hent.py alt            indeks → grænser → sider → byg → tjek
    hent.py indeks         WFS dof:lokaliteter (lokid, loknr, navn, punkt, vis)
    hent.py graenser       WFS dof:lokalitetsgraenser (polygon + oprettet/opdateret/accepteret) og inaktive grænser
    hent.py sider          poplok.php for hver lokalitet i indekset (rå HTML gemmes)
    hent.py byg            fortolk de rå filer og skriv én JSON pr. lokalitet + index.json + manifest.json
    hent.py tjek           rapport over geometri, manglende data og afvigelser
    hent.py pak [--uden-raw]   zip med manifest og SHA-256 pr. fil
    hent.py status         hvor langt er vi

Tempo: højst ét kald pr. --interval sekunder (standard 1), også genforsøg. Ved fejl/429 bremses der op (op til 30 min).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0"
SCHEMA = 1
BASE = "https://dofbasen.dk"
WFS = BASE + "/geoserverwrapper/index.php/wfs"
UA = "DOF.tool/1.0 (lokalitetsarkiv til DOF.tool; https://github.com/thyholm1234/DOF.tool)"
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "dofbasen-lokaliteter"
INDEX_PAGE = 1000
BORDER_PAGE = 250
MAX_CONSECUTIVE_ERRORS = 20


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def shard(loknr: int) -> str:
    return str(loknr)[:3]


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def log(out: Path, msg: str) -> None:
    line = f"{now()} {msg}"
    print(line, flush=True)
    with open(out / "hent.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


class Stop(Exception):
    """Afbrudt (Ctrl-C) eller for mange fejl i træk."""


class Client:
    """HTTP med fast tempo: højst ét kald pr. interval, eksponentiel pause ved fejl."""

    def __init__(self, out: Path, interval: float):
        self.out, self.interval = out, interval
        self.next_at = 0.0
        self.errors = 0
        self.stopping = False
        self.calls = 0

    def _wait(self) -> None:
        while True:
            if self.stopping:
                raise Stop("afbrudt")
            d = self.next_at - time.monotonic()
            if d <= 0:
                return
            time.sleep(min(d, 0.5))

    def get(self, url: str) -> bytes:
        backoff = 30.0
        while True:
            self._wait()
            self.next_at = time.monotonic() + self.interval
            self.calls += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        data = gzip.decompress(data)
                self.errors = 0
                return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise
                wait = backoff
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and ra.isdigit():
                    wait = max(wait, float(ra))
                self._failed(f"HTTP {e.code}", url, wait)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                self._failed(type(e).__name__ + f": {e}", url, backoff)
            backoff = min(backoff * 2, 1800)

    def _failed(self, why: str, url: str, wait: float) -> None:
        self.errors += 1
        log(self.out, f"FEJL ({self.errors} i træk) {why} – venter {int(wait)} s – {url[:140]}")
        if self.errors >= MAX_CONSECUTIVE_ERRORS:
            raise Stop(f"{self.errors} fejl i træk – DOFbasen svarer ikke; prøv igen senere")
        self.next_at = time.monotonic() + wait


# ---------------------------------------------------------------- tilstand (kan genoptages)


def db(out: Path) -> sqlite3.Connection:
    c = sqlite3.connect(out / "state.sqlite")
    c.execute("CREATE TABLE IF NOT EXISTS pages (kind TEXT, idx INTEGER, n INTEGER, at TEXT, PRIMARY KEY (kind, idx))")
    c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS sider (loknr INTEGER PRIMARY KEY, status TEXT, attempts INTEGER DEFAULT 0,
                 error TEXT, at TEXT)""")
    return c


def wfs_url(type_name: str, count: int, start: int, extra: str = "") -> str:
    q = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "outputformat": "application/json",
         "srsName": "EPSG:4326", "typeNames": type_name, "count": count, "startIndex": start, "sortBy": "lokid"}
    return WFS + "?" + urllib.parse.urlencode(q) + extra


def fetch_layer(cl: Client, con: sqlite3.Connection, out: Path, kind: str, type_name: str, page: int) -> int:
    """Hent et WFS-lag side for side til raw/wfs/<kind>_NNNN.json. Returnerer antal features i laget."""
    total = con.execute("SELECT v FROM meta WHERE k = ?", (f"{kind}:total",)).fetchone()
    total = int(total[0]) if total else None
    idx = 0
    while total is None or idx * page < total:
        path = out / "raw" / "wfs" / f"{kind}_{idx:04d}.json"
        if con.execute("SELECT 1 FROM pages WHERE kind = ? AND idx = ?", (kind, idx)).fetchone() and path.exists():
            idx += 1
            continue
        data = cl.get(wfs_url(type_name, page, idx * page))
        d = json.loads(data)
        if total is None:
            total = int(d.get("numberMatched") or d.get("totalFeatures") or 0)
            con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"{kind}:total", str(total)))
        write_atomic(path, data)
        con.execute("INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?)", (kind, idx, len(d["features"]), now()))
        con.commit()
        log(out, f"{kind}: side {idx + 1}/{-(-total // page)} ({len(d['features'])} stk.)")
        if not d["features"]:
            break
        idx += 1
    return total or 0


def layer_features(out: Path, kind: str):
    for p in sorted((out / "raw" / "wfs").glob(f"{kind}_*.json")):
        yield from json.loads(p.read_bytes())["features"]


def cmd_indeks(cl, con, out):
    n = fetch_layer(cl, con, out, "lokaliteter", "dof:lokaliteter", INDEX_PAGE)
    loknrs = sorted({f["properties"]["loknr"] for f in layer_features(out, "lokaliteter")})
    con.executemany("INSERT OR IGNORE INTO sider (loknr, status) VALUES (?, 'mangler')", [(x,) for x in loknrs])
    con.commit()
    log(out, f"indeks: {len(loknrs)} lokaliteter (WFS siger {n})")


def cmd_graenser(cl, con, out):
    n = fetch_layer(cl, con, out, "graenser", "dof:lokalitetsgraenser", BORDER_PAGE)
    m = fetch_layer(cl, con, out, "inaktive", "dof:site_borders_inactive", 500)
    log(out, f"grænser: {n} (heraf {m} på inaktive lokaliteter)")


def cmd_sider(cl, con, out):
    todo = [r[0] for r in con.execute("SELECT loknr FROM sider WHERE status IN ('mangler', 'fejl') AND attempts < 3 ORDER BY loknr")]
    total = con.execute("SELECT count(*) FROM sider WHERE status <> 'udskudt'").fetchone()[0]
    done0 = con.execute("SELECT count(*) FROM sider WHERE status = 'ok'").fetchone()[0]
    t0 = time.monotonic()
    for i, loknr in enumerate(todo, 1):
        path = out / "raw" / "poplok" / shard(loknr) / f"{loknr}.html"
        try:
            data = cl.get(f"{BASE}/poplok.php?loknr={loknr}")
            write_atomic(path, data)
            con.execute("UPDATE sider SET status = 'ok', attempts = attempts + 1, error = NULL, at = ? WHERE loknr = ?", (now(), loknr))
        except urllib.error.HTTPError as e:
            con.execute("UPDATE sider SET status = 'fejl', attempts = attempts + 1, error = ?, at = ? WHERE loknr = ?",
                        (f"HTTP {e.code}", now(), loknr))
        con.commit()
        if i % 100 == 0 or i == len(todo):
            rate = i / max(time.monotonic() - t0, 1e-9)
            left = (len(todo) - i) / rate if rate else 0
            log(out, f"sider: {done0 + i}/{total} – ca. {left / 3600:.1f} t tilbage")


# ---------------------------------------------------------------- fortolkning af poplok.php

ROW = re.compile(r'<acronym class="oneline">(.*?)</acronym>\s*:\s*</td>\s*<td valign="top">(.*?)</td>', re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def text_of(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    s = html.unescape(TAG.sub("", s)).replace("\xa0", " ")
    s = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in s.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def num(s: str | None) -> int | None:
    s = (s or "").strip().replace(".", "")
    return int(s) if s.isdigit() else None


def parse_poplok(raw: bytes) -> dict | None:
    """Felterne på lokalitetssiden. None hvis siden ikke har lokalitetsdata."""
    page = raw.decode("cp1252", errors="replace")
    if 'id="lok_table"' not in page:
        return None
    fields = {}
    out: dict = {}
    for label, value in ROW.findall(page):
        key = text_of(label)
        fields[key] = text_of(value)
        low = key.lower()
        if low.startswith("lokaliteten er del af iba"):
            m = re.search(r"lokid=(\d+)", value)
            out["iba"] = {"id": int(m.group(1)) if m else None, "navn": text_of(value) or None}
    split = lambda v: [x.strip() for x in v.split("/") if x.strip()] if v else []  # noqa: E731
    out["kommuner"] = split(fields.get("Kommune"))
    out["lokalafdelinger"] = split(fields.get("Lokalafdeling"))
    out["region"] = fields.get("Region") or None
    t = fields.get("Lokalitetstype(r)")
    out["typer_tekst"] = t or None  # som DOFbasen skriver det – opdeling afgøres, når hele typekataloget kendes
    out["beskrivelse"] = fields.get("Beskrivelse") or None
    obs = (fields.get("Observationsdage/observationer") or "").split("/")
    out["dofbasen"] = {"arter": num(fields.get("Fuglearter på lokaliteten")),
                       "obsdage": num(obs[0]) if obs else None, "observationer": num(obs[1]) if len(obs) > 1 else None}
    c = re.search(r'id="lok_center_lon">\s*([-\d.]+)\s*</span>\s*/\s*<span id="lok_center_lat">\s*([-\d.]+)', page)
    if c:  # DOFbasen har byttet om på id'erne: teksten er "bredde / længde"
        out["center_side"] = {"lat": float(c.group(1)), "lon": float(c.group(2))}
    m = re.search(r"var\s+lokid\s*=\s*(\d+)", page)
    out["lokid_side"] = int(m.group(1)) if m else None
    out["felter"] = fields
    return out


# ---------------------------------------------------------------- byg JSON


def cmd_byg(con, out):
    idx = {}
    for f in layer_features(out, "lokaliteter"):
        p, g = f["properties"], f.get("geometry") or {}
        lon, lat = (g.get("coordinates") or [None, None])[:2]
        idx[p["loknr"]] = {"loknr": p["loknr"], "lokid": p["lokid"], "navn": p["loknavn"], "vis": p.get("vis"),
                           "center": {"lat": lat, "lon": lon} if lat is not None else None}
    inactive = {f["properties"]["lokid"] for f in layer_features(out, "inaktive")}
    borders = {}
    for f in layer_features(out, "graenser"):
        p = f["properties"]
        borders[p["lokid"]] = {**(f.get("geometry") or {}), "oprettet": p.get("oprettet"), "opdateret": p.get("opdateret"),
                               "accepteret": p.get("accepteret")}  # obserid (hvem der tegnede) gemmes ikke
    fetched = {r[0]: r[1] for r in con.execute("SELECT loknr, at FROM sider WHERE status = 'ok'")}
    border_at = con.execute("SELECT max(at) FROM pages WHERE kind = 'graenser'").fetchone()[0]
    rows, stats = [], {"lokaliteter": 0, "med_graense": 0, "med_side": 0, "uden_data": 0}
    for loknr, base in sorted(idx.items()):
        rec = {"schema": SCHEMA, **base, "aktiv": base["lokid"] not in inactive}
        b = borders.get(base["lokid"])
        if b and b.get("coordinates"):
            pts = sum(len(r) for poly in ([b["coordinates"]] if b["type"] == "Polygon" else b["coordinates"]) for r in poly)
            rec["graense"] = {**b, "punkter": pts}
            stats["med_graense"] += 1
        else:
            rec["graense"] = None
        raw_path = out / "raw" / "poplok" / shard(loknr) / f"{loknr}.html"
        side = parse_poplok(raw_path.read_bytes()) if raw_path.exists() else None
        if side:
            rec.update({k: side[k] for k in ("kommuner", "lokalafdelinger", "region", "typer_tekst", "beskrivelse", "dofbasen")})
            rec["iba"] = side.get("iba")
            rec["felter"] = side["felter"]
            if side.get("lokid_side") and side["lokid_side"] != base["lokid"]:
                rec["advarsel"] = f"lokid på siden ({side['lokid_side']}) ≠ indeks ({base['lokid']})"
            stats["med_side"] += 1
        elif raw_path.exists():
            stats["uden_data"] += 1
        rec["hentet"] = {"side": fetched.get(loknr), "graense": border_at}
        rec["kilde"] = f"{BASE}/poplok.php?loknr={loknr}"
        write_atomic(out / "lokaliteter" / shard(loknr) / f"{loknr}.json", json.dumps(rec, ensure_ascii=False, indent=1).encode())
        rows.append({"loknr": loknr, "lokid": base["lokid"], "navn": base["navn"], "center": base["center"], "vis": base["vis"],
                     "aktiv": rec["aktiv"], "graense": bool(rec["graense"]), "side": bool(side)})
        stats["lokaliteter"] += 1
    types: dict[str, int] = {}
    for r in rows:
        if r["side"]:
            t = json.loads((out / "lokaliteter" / shard(r["loknr"]) / f"{r['loknr']}.json").read_text()).get("typer_tekst")
            if t:
                types[t] = types.get(t, 0) + 1
    write_atomic(out / "typer.json", json.dumps(dict(sorted(types.items(), key=lambda kv: -kv[1])), ensure_ascii=False, indent=1).encode())
    write_atomic(out / "index.json", json.dumps(rows, ensure_ascii=False).encode())
    manifest = {"schema": SCHEMA, "vaerktoej": VERSION, "bygget": now(), "kilde": BASE, **stats,
                "graenser_i_alt": len(borders), "typer": len(types)}
    write_atomic(out / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1).encode())
    log(out, f"byg: {stats} – {len(types)} forskellige typebetegnelser")


# ---------------------------------------------------------------- tjek og pak


def _ring_ok(ring) -> bool:
    return len(ring) >= 4 and ring[0] == ring[-1]


def _point_in_ring(x, y, ring) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def cmd_tjek(out):
    rows = json.loads((out / "index.json").read_text())
    problems: dict[str, list] = {"uden_graense": [], "ugyldig_ring": [], "center_udenfor": [], "uden_side": [], "advarsel": [],
                                 "tegnfejl": []}
    for r in rows:
        rec = json.loads((out / "lokaliteter" / shard(r["loknr"]) / f"{r['loknr']}.json").read_text())
        g = rec.get("graense")
        if not g:
            problems["uden_graense"].append(r["loknr"])
        else:
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            if not all(_ring_ok(ring) for poly in polys for ring in poly):
                problems["ugyldig_ring"].append(r["loknr"])
            c = rec.get("center")
            if c and c.get("lon") is not None and not any(_point_in_ring(c["lon"], c["lat"], poly[0]) for poly in polys):
                problems["center_udenfor"].append(r["loknr"])
        if not r["side"]:
            problems["uden_side"].append(r["loknr"])
        if rec.get("advarsel"):
            problems["advarsel"].append([r["loknr"], rec["advarsel"]])
        if "�" in json.dumps(rec, ensure_ascii=False):
            problems["tegnfejl"].append(r["loknr"])
    report = {"tjekket": now(), "lokaliteter": len(rows), **{k: {"antal": len(v), "eksempler": v[:50]} for k, v in problems.items()}}
    write_atomic(out / "tjek.json", json.dumps(report, ensure_ascii=False, indent=1).encode())
    log(out, "tjek: " + ", ".join(f"{k} {len(v)}" for k, v in problems.items()))


def cmd_pak(out, uden_raw: bool):
    manifest = json.loads((out / "manifest.json").read_text())
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.suffix in (".json", ".html")
                   and p.name not in ("manifest.json",) and not p.name.endswith(".tmp")
                   and not (uden_raw and "raw" in p.relative_to(out).parts))
    manifest["filer"] = {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    zpath = out.parent / f"dofbasen-lokaliteter-{datetime.now():%Y-%m-%d}{'-uden-raw' if uden_raw else ''}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        for p in files:
            z.write(p, p.relative_to(out).as_posix())
    log(out, f"pak: {zpath} ({zpath.stat().st_size / 1e6:.1f} MB, {len(files)} filer)")


def cmd_status(con, out):
    st = dict(con.execute("SELECT status, count(*) FROM sider GROUP BY status").fetchall())
    pages = dict(con.execute("SELECT kind, count(*) FROM pages GROUP BY kind").fetchall())
    totals = dict(con.execute("SELECT k, v FROM meta").fetchall())
    print(json.dumps({"sider": st, "wfs_sider": pages, "wfs_antal": totals}, ensure_ascii=False, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["alt", "indeks", "graenser", "sider", "byg", "tjek", "pak", "status"])
    ap.add_argument("--ud", type=Path, default=DEFAULT_OUT, help=f"mappe (standard {DEFAULT_OUT})")
    ap.add_argument("--interval", type=float, default=1.0, help="sekunder mellem to kald (standard 1)")
    ap.add_argument("--uden-raw", action="store_true", help="pak: udelad de rå sider")
    ap.add_argument("--max", type=int, default=0, help="sider: højst så mange (til prøvekørsel)")
    a = ap.parse_args()
    out = a.ud
    out.mkdir(parents=True, exist_ok=True)
    con = db(out)
    cl = Client(out, max(a.interval, 0.5))

    def stop(*_):
        cl.stopping = True
        print("\nStopper efter det igangværende kald …", flush=True)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    if a.max:
        global cmd_sider
        full = cmd_sider

        def cmd_sider(cl_, con_, out_):  # noqa: F811 – begræns til prøvekørsel
            con_.execute("CREATE TEMP TABLE IF NOT EXISTS lim AS SELECT 1")
            todo = [r[0] for r in con_.execute("SELECT loknr FROM sider WHERE status IN ('mangler','fejl') AND attempts < 3 ORDER BY loknr")]
            keep = set(todo[:: max(1, len(todo) // a.max)][: a.max])
            con_.execute("UPDATE sider SET status = 'udskudt' WHERE status = 'mangler'")
            con_.executemany("UPDATE sider SET status = 'mangler' WHERE loknr = ?", [(x,) for x in keep])
            try:
                full(cl_, con_, out_)
            finally:
                con_.execute("UPDATE sider SET status = 'mangler' WHERE status = 'udskudt'")
                con_.commit()
    try:
        log(out, f"start {a.cmd} (interval {cl.interval} s)")
        if a.cmd in ("indeks", "alt"):
            cmd_indeks(cl, con, out)
        if a.cmd in ("graenser", "alt"):
            cmd_graenser(cl, con, out)
        if a.cmd in ("sider", "alt"):
            cmd_sider(cl, con, out)
        if a.cmd in ("byg", "alt"):
            cmd_byg(con, out)
        if a.cmd in ("tjek", "alt"):
            cmd_tjek(out)
        if a.cmd == "pak":
            cmd_pak(out, a.uden_raw)
        if a.cmd == "status":
            cmd_status(con, out)
        log(out, f"færdig {a.cmd} ({cl.calls} kald)")
        return 0
    except Stop as e:
        log(out, f"stoppet: {e} ({cl.calls} kald) – start igen med samme kommando for at fortsætte")
        return 2


if __name__ == "__main__":
    sys.exit(main())
