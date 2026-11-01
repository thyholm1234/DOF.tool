#!/usr/bin/env python3
"""Arkiverer publicerede fotos fra netfugl.dk/fotos til én mappe pr. foto (foto.<ext> + meta.json).

  hent     [--fra 125245] [--til 1] [--stop-efter 10] [--pause 3] [--igen] [--ids 125244 125238 …]
  kontrol                       tæller mapper, sammenligner med galleriets totaltal, skriver _mangler.json
  indeks                        bygger _index.json, _fotografer.json, _arter.json, _koder.json, manifest.json
  zip      [--del-gb 20]        pakker til Fotos-001.zip, Fotos-002.zip … (højst --del-gb pr. zip)

Overslag: ~125 000 sider + ~80 000 billeder x 3 s ≈ 7 døgn; billederne fylder anslået 40-80 GB.
Scriptet kan afbrydes og genoptages når som helst (en mappe uden meta.json hentes igen).

Kør med `python3 -I netfugl_fotos.py ...` (kræver beautifulsoup4).
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

BASE = "https://netfugl.dk"
UA = "DOF-arkivering (christianhelligsoe@gmail.com)"
SCHEMA_VERSION = 1
MAANEDER = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}
DATO_RE = re.compile(r"(\d{1,2})\.\s*([A-Za-zæøåÆØÅ]+)\.?\s*(\d{4})\s*kl\.\s*(\d{1,2}):(\d{2})")
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
RETTIGHEDER = {
    "Reference til fotografen er påkrævet ved brug af foto (kun ikke-kommerciel brug)": 1,
    "Personlig aftale med fotografen er påkrævet for brug af foto": 2,
    "Henvendelser om brugsret for foto frabedes af fotografen": 3,
}
KAMERA_FELTER = {"Kameramodel": "model", "Objektiv": "linse", "Linse": "linse", "Blænde": "blaende",
                 "Brændvidde": "braendvidde", "Lukkertid": "lukkertid", "ISO": "iso", "Flash": "flash"}


# ---------- hentning ----------

def kod_url(url):
    """Procent-koder tegn uden for ASCII (æ, ø, å, mellemrum) i sti og query; bevarer eksisterende %xx."""
    d = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((d.scheme, d.netloc, urllib.parse.quote(d.path, safe="/%:@+,;=~"),
                                    urllib.parse.quote(d.query, safe="=&%:@+,;/?~"), ""))


def hent_bytes(url):
    """(statuskode, indhold). Netfugl svarer nogle gange 500 med fuld side, så HTTPError-kroppen bruges.
    Ved 429/503 ventes 60 s (højst 5 gange)."""
    url = kod_url(url)
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        return 0, b""
    for forsoeg in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and forsoeg < 4:
                time.sleep(60)
                continue
            return e.code, e.read()
        except (urllib.error.URLError, TimeoutError):
            if forsoeg == 4:
                raise
            time.sleep(5 * (forsoeg + 1))
    return 0, b""


# ---------- parser ----------

def parse_dato(tekst):
    """'2. jul. 2026 kl. 23:17' → '2026-07-02T23:17'; ren ISO-dato returneres uændret; ellers None."""
    m = DATO_RE.search(tekst or "")
    if m:
        d, mdr, aar, t, mi = m.groups()
        mnd = MAANEDER.get(mdr[:3].lower())
        if mnd:
            return f"{int(aar):04d}-{mnd:02d}-{int(d):02d}T{int(t):02d}:{mi}"
    m = ISO_RE.search(tekst or "")
    return m.group(0) if m else None


def _t(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _tal(tekst):
    m = re.search(r"\d+", (tekst or "").replace(".", ""))
    return int(m.group(0)) if m else 0


def _id_fra_href(href, moenster):
    m = re.search(moenster, href or "")
    return int(m.group(1)) if m else None


def _felt(blok, label):
    """Værdien efter en `<span class="font-weight-light">Label:</span><span>Værdi</span>`."""
    for s in blok.find_all("span", class_="font-weight-light"):
        if _t(s).rstrip(":").strip().lower() == label.lower():
            n = s.find_next_sibling("span")
            return _t(n) or None
    return None


def _fugl(blok):
    a = blok.find("a", href=re.compile(r"^/fotos/\?birdId=\d+"))
    links = blok.find_all("a", href=re.compile(r"^/fotos/\?birdId=\d+"))
    spans = a.find_all("span", recursive=False)
    dansk = _t(spans[0]) if spans else None
    latin = _t(a.find("span", class_="font-italic")) or None
    engelsk = None
    for s in spans[1:]:
        if not s.find("span", class_="font-italic"):
            engelsk = _t(s) or None
    ssp = None
    if len(links) > 1:
        ssp = {"latin": _t(links[1]), "netfugl_id": _id_fra_href(links[1]["href"], r"birdId=(\d+)")}
    ikon = blok.find("i", class_=re.compile(r"fa-(mars|venus)\b"))
    koen = None
    if ikon:
        koen = "Han" if "fa-mars" in ikon["class"] else "Hun"
    info = blok.find("a", href=re.compile(r"^/arter/specie/\d+"))
    fugl = {"art": {"dansk": dansk, "latin": latin, "engelsk": engelsk,
                    "netfugl_id": _id_fra_href(a["href"], r"birdId=(\d+)")},
            "adfaerd": _felt(blok, "Adfærd"), "koen": koen, "alder": _felt(blok, "Alder"),
            "dragt": _felt(blok, "Dragt"), "sjaeldenhed": _felt(blok, "Sjældenhed")}
    if ssp:
        fugl["underart"] = ssp
    if info and not fugl["art"]["netfugl_id"]:
        fugl["art"]["netfugl_id"] = _id_fra_href(info["href"], r"specie/(\d+)")
    return fugl


def _kommentar(el):
    navn = el.select_one(".comment-content-author-name")
    a = navn.find("a") if navn else None
    tekst = el.select_one(".comment-content-text")
    id_ = _id_fra_href(el.get("id", ""), r"(\d+)$")
    return {"id": id_, "forfatter": _t(a or navn) or None,
            "forfatter_id": _id_fra_href(a.get("href") if a else "", r"/bruger/profil/(\d+)"),
            "dato": parse_dato(_t(el.select_one(".comment-content-time"))),
            "tekst_html": tekst.decode_contents().strip() if tekst else "", "svar": []}


def _laes_kommentarer(omr):
    """Top-niveau `.parrent-comment`; svar (`.child-comment`) enten inde i forælderen eller som efterfølgende søskende."""
    ud, sidste = [], None
    for el in omr.find_all(class_="comment"):
        klasser = el.get("class", [])
        if "child-comment" in klasser:
            if el.find_parent(class_="parrent-comment"):
                forael = el.find_parent(class_="parrent-comment")
                forael_id = _id_fra_href(forael.get("id", ""), r"(\d+)$")
                forælder = next((k for k in ud if k["id"] == forael_id), None)
            else:
                forælder = sidste
            if forælder is not None:
                forælder["svar"].append(_kommentar(el))
        else:
            sidste = _kommentar(el)
            ud.append(sidste)
    return ud


def parse_foto(html, id):
    """Ren parser: (side-HTML, id) → meta-dict (§2.3) eller None, hvis siden ikke viser et publiceret foto."""
    soup = BeautifulSoup(html or "", "html.parser")
    info = soup.find(id="photo-info")
    img = soup.select_one(".card-img-top img")
    if not info or not img:
        return None
    artsdel = info.find("i", class_="fa-feather-alt")
    artsomr = artsdel.find_parent("div") if artsdel else None
    fugle = []
    if artsomr:
        for blok in artsomr.find_all("div", recursive=False):
            if blok.find("a", href=re.compile(r"^/fotos/\?birdId=\d+")):
                fugle.append(_fugl(blok))
    if not fugle:
        return None

    # rettighed
    rtekst = _t(info.select_one("small span"))
    rettighed = {"id": RETTIGHEDER.get(rtekst), "tekst": rtekst} if rtekst else None

    # lokalitet / land
    lok = info.find("a", href=re.compile(r"\?location="))
    land = info.find("a", href=re.compile(r"\?countryId=\d+"))
    land_navn = _t(land).lstrip("-").strip() if land else None
    globe = info.find("img", attrs={"data-latitude": True})
    koordinat = None
    if globe:
        try:
            koordinat = {"lat": float(globe["data-latitude"]), "lon": float(globe["data-longitude"])}
        except (ValueError, KeyError):
            pass

    # dato (fotodato = tekst ved kalenderikonet)
    ikon = info.find("i", class_="fa-calendar-alt")
    dato = parse_dato(_t(ikon.parent)) if ikon else None

    # fotograf
    fot = info.find("a", href=re.compile(r"\?photographer="))
    prof = info.find("a", href=re.compile(r"/bruger/profil/\d+"))

    # titel og beskrivelse
    titel_p = info.find("p", class_="card-title")
    titel = _t(titel_p)
    titel = re.sub(r"^Titel:\s*", "", titel) or None
    beskr = None
    if titel_p:
        n = titel_p.find_next_sibling("p")
        if n and _t(n):
            beskr = re.sub(r"^(\s*(?:<[^>]+>\s*)*?)Beskrivelse:\s*", r"\1", n.decode_contents().strip()) or None

    # kamera / dimensioner
    kamera = {v: None for v in set(KAMERA_FELTER.values())}
    for label, key in KAMERA_FELTER.items():
        v = _felt(info, label)
        if v:
            kamera[key] = v
    dim = _felt(info, "Dimensioner")
    bredde = hoejde = None
    m = re.match(r"(\d+)\s*x\s*(\d+)", dim or "")
    if m:
        bredde, hoejde = int(m.group(1)), int(m.group(2))

    # uploadet / publiceret / visninger / likes
    lille = info.select_one("small.text-muted")
    ltekst = _t(lille)
    up = re.search(r"Uploadet\s+(\d{4}-\d{2}-\d{2})", ltekst)
    pub = re.search(r"Publiceret\s+(\d{4}-\d{2}-\d{2})", ltekst)
    eye = info.find("i", class_="fa-eye")
    visninger = _tal(_t(eye.parent)) if eye else 0
    like = info.find("span", class_=re.compile(r"like-count-display"))
    likes = _tal(_t(like))

    # emneord (hvis siden viser dem)
    emneord = [_t(a) for a in info.select(".bird-photo-tags a")]

    # gruppe: andre foto-id'er på siden (karrusel)
    gruppe = []
    for a in soup.select("#photo-info a[href^='/fotos/'], .carousel a[href^='/fotos/']"):
        gid = _id_fra_href(a.get("href"), r"^/fotos/(\d+)(?:$|[?#])")
        if gid and gid != id and gid not in gruppe:
            gruppe.append(gid)

    kommentarer = _laes_kommentarer(soup.find(id=f"comment-area{id}") or soup.find(id="comments") or soup)

    return {
        "schema_version": SCHEMA_VERSION,
        "id": id,
        "url": f"{BASE}/fotos/{id}",
        "hentet": None, "fil": None, "fil_sha256": None,
        "bredde": bredde, "hoejde": hoejde,
        "titel": titel, "beskrivelse_html": beskr,
        "dato": dato,
        "uploadet": up.group(1) if up else None, "publiceret": pub.group(1) if pub else None,
        "land": {"navn": land_navn, "netfugl_id": _id_fra_href(land["href"], r"countryId=(\d+)")} if land else None,
        "lokalitet": _t(lok) or None if lok else None,
        "koordinat": koordinat,
        "fotograf": {"navn": _t(fot) or None, "netfugl_bruger_id": _id_fra_href(prof["href"], r"/bruger/profil/(\d+)")
                     if prof else None} if fot else None,
        "fugle": fugle,
        "emneord": emneord,
        "kamera": kamera,
        "rettighed": rettighed,
        "likes": likes, "visninger": visninger,
        "gruppe": gruppe,
        "kommentarer": kommentarer,
    }


# ---------- hentning af ét foto ----------

def sniff_ext(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def hent_foto(id, ud, pause_s, log):
    """'ok' / 'tom' / 'fejl'. meta.json skrives sidst og atomisk."""
    status, body = hent_bytes(f"{BASE}/fotos/{id}")
    html = body.decode("utf-8", "replace")
    meta = parse_foto(html, id) if status in (200, 500) else None
    if not meta:
        return "tom", 0
    time.sleep(pause_s)
    data = b""
    for url in (f"{BASE}/fotos/view/original/{id}", f"{BASE}/fotos/view/{id}"):
        st, d = hent_bytes(url)
        if st == 200 and sniff_ext(d):
            data = d
            break
        time.sleep(pause_s)
    if not data:
        raise RuntimeError("billedet kunne ikke hentes")
    mappe = ud / str(id)
    mappe.mkdir(parents=True, exist_ok=True)
    ext = sniff_ext(data)
    (mappe / f"foto.{ext}").write_bytes(data)
    meta["fil"] = f"foto.{ext}"
    meta["fil_sha256"] = hashlib.sha256(data).hexdigest()
    meta["hentet"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = mappe / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(mappe / "meta.json")
    return "ok", len(data)


def _logger(ud):
    ud.mkdir(parents=True, exist_ok=True)
    f = open(ud / "hent.log", "a", encoding="utf-8")

    def log(linje):
        print(linje, flush=True)
        f.write(linje + "\n")
        f.flush()
    return log


def cmd_hent(a):
    ud = Path(a.ud)
    log = _logger(ud)
    tomme = open(ud / "_tomme.txt", "a", encoding="utf-8")
    ids = a.ids if a.ids else range(a.fra, a.til - 1, -1)
    i_traek = 0
    for id in ids:
        if not a.igen and (ud / str(id) / "meta.json").exists():
            i_traek = 0
            continue
        try:
            res, n = hent_foto(id, ud, a.pause, log)
        except Exception as e:  # noqa: BLE001 – ét fejlet foto må ikke stoppe en crawl på dage
            log(f"{id} fejl: {e}")
            time.sleep(a.pause)
            continue
        if res == "ok":
            log(f"{id} ok {n / 1e6:.1f} MB")
            i_traek = 0
        else:
            log(f"{id} tom")
            tomme.write(f"{id}\n")
            tomme.flush()
            i_traek += 1
            if not a.ids and a.stop_efter and i_traek >= a.stop_efter:
                log(f"Stoppet ved id {id} efter {i_traek} tomme i træk")
                return
        time.sleep(a.pause)


# ---------- kontrol / indeks / zip ----------

def _metas(ud):
    for p in sorted((q for q in ud.iterdir() if q.is_dir() and q.name.isdigit()), key=lambda q: int(q.name)):
        m = p / "meta.json"
        if m.exists():
            yield json.loads(m.read_text(encoding="utf-8"))


def cmd_kontrol(a):
    ud = Path(a.ud)
    status, body = hent_bytes(f"{BASE}/fotos")
    m = re.search(r"Søgeresultater\s*\(\s*([\d.]+)\s*fotos\)", body.decode("utf-8", "replace"))
    if not m:
        sys.exit("Kunne ikke læse totaltallet fra galleriet")
    total = int(m.group(1).replace(".", ""))
    ids = [meta["id"] for meta in _metas(ud)]
    forskel = total - len(ids)
    (ud / "_mangler.json").write_text(json.dumps(
        {"galleri_total": total, "hentet": len(ids), "forskel": forskel, "laveste_id": min(ids, default=None)}, indent=2))
    print(f"Mapper med meta.json: {len(ids)} · galleriet: {total} · forskel: {forskel} · laveste id: {min(ids, default='-')}")
    if total and forskel / total > 0.005:
        print(f"ADVARSEL: {forskel} fotos mangler (>0,5 %). Fortsæt med: "
              f"python3 -I netfugl_fotos.py hent --fra {min(ids, default=1)} --stop-efter 500")


def cmd_indeks(a):
    ud = Path(a.ud)
    index, fot, arter, koder = [], {}, {}, {}
    laveste = hoejeste = None
    hentet = []

    def kode(art, v):
        if v:
            koder.setdefault(art, {})
            koder[art][v] = koder[art].get(v, 0) + 1
    for m in _metas(ud):
        index.append({"id": m["id"], "titel": m["titel"], "dato": m["dato"],
                      "arter": [f["art"]["latin"] for f in m["fugle"]], "fotograf": (m["fotograf"] or {}).get("navn")})
        f = m["fotograf"] or {}
        if f.get("netfugl_bruger_id"):
            e = fot.setdefault(str(f["netfugl_bruger_id"]), {"navn": f["navn"], "antal": 0})
            e["antal"] += 1
        for b in m["fugle"]:
            if b["art"]["netfugl_id"]:
                e = arter.setdefault(str(b["art"]["netfugl_id"]), {k: b["art"][k] for k in ("dansk", "latin", "engelsk")} | {"antal": 0})
                e["antal"] += 1
            for k in ("adfaerd", "koen", "alder", "dragt", "sjaeldenhed"):
                kode(k, b.get(k))
        for t in m["emneord"]:
            kode("emneord", t)
        kode("rettighed", (m["rettighed"] or {}).get("tekst"))
        kode("land", (m["land"] or {}).get("navn"))
        laveste = m["id"] if laveste is None else min(laveste, m["id"])
        hoejeste = m["id"] if hoejeste is None else max(hoejeste, m["id"])
        hentet.append(m["hentet"] or "")

    def skriv(navn, data):
        (ud / navn).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    skriv("_index.json", index)
    skriv("_fotografer.json", fot)
    skriv("_arter.json", arter)
    skriv("_koder.json", koder)
    skriv("manifest.json", {"schema_version": SCHEMA_VERSION, "antal": len(index), "hentet_fra": min(hentet, default=None),
                            "hentet_til": max(hentet, default=None), "lavest_id": laveste, "hoejest_id": hoejeste,
                            "laget": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
    print(f"{len(index)} fotos, {len(fot)} fotografer, {len(arter)} arter")


def cmd_zip(a):
    ud = Path(a.ud).resolve()
    faste = ["manifest.json", "_index.json", "_fotografer.json", "_arter.json", "_koder.json"]
    grænse = int(a.del_gb * 1e9)
    dele, aktuel, stoerrelse = [], [], 0
    for p in sorted((q for q in ud.iterdir() if q.is_dir() and q.name.isdigit() and (q / "meta.json").exists()),
                    key=lambda q: int(q.name)):
        s = sum(f.stat().st_size for f in p.iterdir())
        if aktuel and stoerrelse + s > grænse:
            dele.append(aktuel)
            aktuel, stoerrelse = [], 0
        aktuel.append(p)
        stoerrelse += s
    if aktuel:
        dele.append(aktuel)
    for n, mapper in enumerate(dele, 1):
        mål = ud.parent / f"Fotos-{n:03d}.zip"
        with zipfile.ZipFile(mål, "w") as z:
            for navn in faste:
                if (ud / navn).exists():
                    z.write(ud / navn, f"netfugl-fotos/{navn}", compress_type=zipfile.ZIP_DEFLATED)
            for p in mapper:
                for f in sorted(p.iterdir()):
                    if f.name == "meta.json.tmp":
                        continue
                    z.write(f, f"netfugl-fotos/{p.name}/{f.name}",
                            compress_type=zipfile.ZIP_DEFLATED if f.suffix == ".json" else zipfile.ZIP_STORED)
        print(f"{mål} ({mål.stat().st_size / 1e9:.2f} GB, {len(mapper)} fotos)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ud", default="/home/chris/Dokumenter/DOF.tool/Fotos")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hent")
    h.add_argument("--ids", type=int, nargs="*")
    h.add_argument("--fra", type=int, default=125245)
    h.add_argument("--til", type=int, default=1)
    h.add_argument("--stop-efter", type=int, default=10, help="stop efter N id'er i træk uden data (0 = aldrig)")
    h.add_argument("--pause", type=float, default=3.0)
    h.add_argument("--igen", action="store_true", help="hent også fotos, der allerede har meta.json")
    sub.add_parser("kontrol")
    sub.add_parser("indeks")
    z = sub.add_parser("zip")
    z.add_argument("--del-gb", type=float, default=20)
    a = p.parse_args()
    {"hent": cmd_hent, "kontrol": cmd_kontrol, "indeks": cmd_indeks, "zip": cmd_zip}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
