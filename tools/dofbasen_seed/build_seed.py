"""Bygger server/portal/seed/dofbasen/content.json ud fra de crawlede sider fra dofbasen.dk (22.-23. okt. 2026).

Brug:  python build_seed.py <mappe fra crawl.py/areas.py> ../../server/portal/seed/dofbasen/content.json
Den engelske side, nyheder (nid=165, 296-299), fokus (?tema=44) og fænologilistens vinterfane (wsdata_vinter.html)
skal være hentet med i mappen.

Indholdet er kopieret som rå tekst til brug i en showcase. Personlige kontaktoplysninger (adresser, telefonnumre)
kopieres ikke over.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conv import convert, Conv, clean  # noqa: E402

S = Path(sys.argv[1])
OUT = Path(sys.argv[2])
P = S / "pages"
FOOTER = re.compile(r"\s*\[DOF\]\(http://www\.dof\.dk\) Vesterbrogade.*$", re.S)
SOURCE = "Kopieret fra dofbasen.dk 23-10-2026 som eksempel. Indholdet tilhører Dansk Ornitologisk Forening."


def secs(name, drop=(), unwrap=False, rename=None):
    out = []
    for s in convert(P / f"{name}.html"):
        if s["raw_title"] in drop:
            continue
        body = FOOTER.sub("", s["body"]).strip()
        # Mail-formularer til enkeltpersoner kopieres ikke – linket peger i stedet på Kontakt-siden
        body = re.sub(r"\]\(https://dofbasen\.dk/[^)]*sendmail\.php[^)]*\)", "](https://dofbasen.dk/kontakt)", body)
        if unwrap and body.startswith("_") and body.endswith("_"):
            body = body[1:-1].strip()
        title = (rename or {}).get(s["raw_title"], s["raw_title"])
        out.append({"title": title, "body": body})
    return out


def txt_rows(name, start_marker, min_cols=2, stop=None):
    """Tab-separerede rækker fra en sides tekstudgave (efter start_marker)."""
    lines = (P / f"{name}.txt").read_text().split("\n")
    rows, on = [], False
    for line in lines:
        if start_marker in line:
            on = True
            continue
        if not on:
            continue
        if stop and stop in line:
            break
        if "Vesterbrogade 140" in line:
            break
        cells = [c.strip() for c in line.split("\t")]
        cells = [c for c in cells if c != ""]
        if len(cells) >= min_cols:
            rows.append(cells)
    return rows


def strip_paren(s):
    return s[1:-1] if s.startswith("(") and s.endswith(")") else s


pages = {}
pages["start"] = {"title": "Hvad er DOFbasen?", "sections": secs("_start_php")}
pages["funktioner"] = {"title": "DOFbasens funktioner", "sections": secs("_features_php")}
pages["rettigheder"] = {"title": "Rettigheder", "sections": secs("_rights_php")}
pages["kvalitet"] = {"title": "Kvalitetssikring", "sections": secs("_kvalitet")}
pages["hjaelp"] = {"title": "Hjælp", "sections": secs("_help_php", drop={"SPØRGSMÅL"}, unwrap=True)}
pages["viden"] = {"title": "Anvendelse af data", "sections": secs("_viden")}
pages["opslag"] = {"title": "Lister", "sections": secs("_opslag")}
pages["visningstyper"] = {"title": "Links til observationssiden", "sections": secs("_opslag_visningstyper_php")}
pages["visningstyper_soegning"] = {"title": "Links til søgesiden", "sections": secs("_opslag_visningstyper_soegning_php")}
pages["brugeroprettelse"] = {"title": "Brugeroprettelse", "sections": secs("_opretmig_php")}
pages["glemt"] = {"title": "Glemt password", "sections": [s for s in secs("_reminder_php") if "| Obserkode" not in s["body"]]}

# Engelsk side: tekstudgaven (siden bruger en anden opbygning)
eng = (P / "_english.txt").read_text()
eng_secs, cur = [], None
for line in eng.split("\n"):
    t = line.strip()
    if not t or "Vesterbrogade 140" in t or "sekunder)" in t:
        continue
    if t.isupper() and len(t) > 12:
        cur = {"title": t, "body": ""}
        eng_secs.append(cur)
    elif cur is not None:
        cur["body"] += ("\n\n" if cur["body"] and not t.startswith("-") else ("\n" if cur["body"] else "")) + t
pages["english"] = {"title": "English", "sections": eng_secs}

# Kontakt: kun navne, afdeling og kommuner – ingen adresser eller telefonnumre
kontakt = secs("_kontakt")
intro = [s for s in kontakt if s["title"] == "KONTAKT"]
coords = []
for s in kontakt:
    if s["title"] not in ("LOKALKOORDINATORER", "CENTRAL KOORDINATOR"):
        continue
    for line in s["body"].split("\n"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0].endswith("Lokalafdeling:"):
            continue
        m = re.search(r"Send en e-mail til ([^\]]+)\]", cells[1])
        if not m:
            continue
        coords.append({"afdeling": cells[0].rstrip(":"), "navn": m.group(1).strip(),
                       "kommuner": cells[2] if len(cells) > 2 else "",
                       "rolle": "Central koordinator" if s["title"] == "CENTRAL KOORDINATOR" else "Lokalkoordinator"})
pages["kontakt"] = {"title": "Kontakt", "sections": intro}

# Forsidens "Om DOFbasen" (tallene for lokaliteter/observationer sættes live af siden)
pages["om"] = {"title": "Om DOFbasen", "sections": [{"title": "OM DOFBASEN", "body": "\n".join([
    "• DOFbasen er [Dansk Ornitologisk Forenings](http://www.dof.dk/) (DOF) database over observationer af fugle",
    "• Alle kan indtaste og udsøge data i DOFbasen ([når man er oprettet som bruger](https://dofbasen.dk/opretmig.php))",
    "• Find [DOFbasens indtastningsapp](https://app.dofbasen.dk/) i App Store eller Google Play",
    "• Der sker løbende [kvalitetssikring af data](https://dofbasen.dk/kvalitet) i DOFbasen",
    "• DOFbasen er grundlaget for dataindsamlingen i [DOFs projekter](http://www.dof.dk/), og dermed [fugle- og naturbeskyttelse i Danmark](http://www.dof.dk/naturbeskyttelse)",
    "• Der kan indtastes på **{lokaliteter}** lokaliteter i DOFbasen",
    "• Der er **{observationer}** observationer i DOFbasen",
    "• DOFbasen anvender cookies. Dette accepterer du, når du bruger siden. [Læs mere](https://dofbasen.dk/rights.php).",
])}]}


def news_body(nid):
    h = (P / f"_news_index_php_nid_{nid}.html").read_text()
    m = re.search(r'index\.php\?nid=%s".*?</td>\s*<td[^>]*>(.*?)</td></tr></tbody></table>' % nid, h, re.S)
    c = Conv()
    c.start("x")
    c.feed(m.group(1))
    return clean(c.sections[0]["body"]).strip()


news = [
    {"dato": "2026-10-22", "title": "DOFbasen nede, men kører igen", "body": news_body(299)},
    {"dato": "2026-10-12", "title": "Ny version af DOFbasens indtastningsapp (2.10.9)", "body": news_body(298)},
    {"dato": "2026-10-08", "title": "Gammel version af indtastningsappen på iOS", "body": news_body(297)},
    {"dato": "2026-10-06", "title": "Gammel version af indtastningsappen på Android", "body": news_body(296)},
    {"dato": "2026-03-12", "title": "Kendte fejl i DOFbasen (opdateres løbende)", "body": re.sub(r"^\*\*Kendte fejl i DOFbasen \(opdateres løbende\)\*\*\s*", "", news_body(165)), "pinned": True},
]

focus_sec = [s for s in convert(P / "_tema_44.html") if s["raw_title"] == "FOKUS"][0]
focus_body = re.sub(r"^• \[Optælling af Hjejle og Vibe\]\([^)]*\)\s*", "", focus_sec["body"]).strip()
focus = [{"title": "Optælling af Hjejle og Vibe", "body": focus_body, "active": True}]

lists = {}
lists["su"] = {"title": "SU-arter", "intro": "For observationer af nedenstående arter og racer gælder det, at de skal godkendes af Sjældenhedsudvalget (SU) i Dansk Ornitologisk Forening, før der må refereres til disse.",
               "columns": ["Artnr", "Art", "Latin", "Kategori"],
               "rows": [[r[0], r[1], strip_paren(r[2]) if len(r) > 2 else "", strip_paren(r[3]) if len(r) > 3 else ""] for r in txt_rows("_opslag_sudata_php", "SJÆLDNE ARTER", 3) if r[0].isdigit()]}
lists["sub"] = {"title": "SUB-arter", "intro": "Oversigt over de arter, som kan betegnes som subhits (fåtallige arter), og som på observationssiderne vises med grøn skrift.",
                "columns": ["Artnr", "Art", "Latin"],
                "rows": [[r[0], r[1], strip_paren(r[2]) if len(r) > 2 else ""] for r in txt_rows("_opslag_subdata_php", "FÅTALLIGE ARTER", 3) if r[0].isdigit()]}
lists["foelsomme"] = {"title": "Følsomme arter", "intro": "For nedenstående arter gælder det, at alle observationer med angivelse af adfærd som syngende, territoriehævdende, ynglefugle eller indtastninger som ynglepar automatisk hemmeligholdes.",
                      "columns": ["Artnr", "Art", "Latin"],
                      "rows": [[r[0], r[1], strip_paren(r[2]) if len(r) > 2 else ""] for r in txt_rows("_opslag_sensitive_php", "TRUEDE YNGLEFUGLEARTER", 3) if r[0].isdigit()]}


def ws_rows(html, season):
    return [[a, re.sub(r"<[^>]+>", "", b).strip(), c.strip("()"), season]
            for a, b, c in re.findall(r"<tr><td>(\d{5})</td><td[^>]*>&nbsp;</td><td>(.*?)</td><td>(\([^)]*\))</td></tr>", html)]


ws = ws_rows((P / "_opslag_wsdata_php.html").read_text(), "sommer") + ws_rows((P / "wsdata_vinter.html").read_text(), "vinter")
lists["faenologi"] = {"title": "Fænologiliste", "intro": "Oversigt over de arter, som altid vises under bemærkelsesværdige observationer på hjemmesiden, når de ses uden for deres normale forekomstperiode i Danmark. Sommer/vinter henviser til perioden de vises, og datoerne til perioden, hvor alle observationer vises.",
                      "columns": ["Artnr", "Art", "Periode", "Sæson"], "rows": ws}
beh = []
for line in (P / "_opslag_behaviour_php.txt").read_text().split("\n"):
    if "\t" not in line or "adfýrdskode" in line or "Vesterbrogade" in line:
        continue
    cells = line.split("\t")
    cells = [c.strip() for c in cells]
    if cells[0]:
        beh.append([cells[0], "", cells[2] if len(cells) > 2 else "", cells[3] if len(cells) > 3 else ""])
    elif len(cells) > 2 and cells[1]:
        beh.append([beh[-1][0] if beh else "", cells[1], cells[2] if len(cells) > 2 else "", cells[3] if len(cells) > 3 else ""])
lists["adfaerd"] = {"title": "Adfærdskoder", "intro": "", "columns": ["Primær adfærdskode", "Evt. sekundær adfærdskode", "Forkortelse", "Evt. trækretning"], "rows": beh}
lists["alder"] = {"title": "Alderskoder", "intro": "", "columns": ["Alderskode", "Beskrivelse"], "rows": txt_rows("_opslag_alder_php", "Alderskode:")}
lists["dragt"] = {"title": "Dragtkoder", "intro": "", "columns": ["Dragtkode", "Beskrivelse"], "rows": txt_rows("_opslag_dragt_php", "Dragtkode:")}
lists["vindstyrke"] = {"title": "Vindstyrker", "intro": "En oversigt over vindstyrker i såvel m/s som beaufort.", "columns": ["Beaufort", "m/s", "Betegnelse", "Beskrivelse"],
                       "rows": txt_rows("_opslag_vindstyrke_php", "Beaufort:", 4)}
lists["koordinatorer"] = {"title": "Koordinatorer", "intro": "Lokalkoordinatorer og central koordinator. Kontaktoplysninger er udeladt i showcasen.",
                          "columns": ["Afdeling", "Navn", "Rolle", "Kommuner"], "rows": [[c["afdeling"], c["navn"], c["rolle"], c["kommuner"]] for c in coords]}

menu = [
    ["DOF", "http://www.dof.dk", "Dansk Ornitologisk Forening"],
    ["Fuglværnsfonden", "http://www.dof.dk/fvf", "Fuglværnsfonden"],
    ["Naturbutikken", "http://www.naturbutikken.dk/", "Naturbutikken"],
    ["Netfugl 2.0", "https://www.netfugl.dk/", "Netfugl 2.0"],
    ["Danmarks fugle", "https://dofbasen.dk/ART", "Danmarks fugle"],
    ["Punkttællingsprojektet", "https://www.dof.dk/punkt", "Punkttællingsprojektet"],
    ["Spontantællinger", "https://www.dof.dk/fakta-om-fugle/spontantaellinger", "Spontantællinger"],
    ["Fugle i Ny Natur (FiNN)", "https://www.dof.dk/naturbeskyttelse/projekter/fugle-i-ny-natur-finn", "Fugle i Ny Natur (FiNN)"],
    ["Sjældenhedsudvalget", "https://www.dof.dk/om-dof/aktiv-i-dof/grupper-og-udvalg/sjaeldenhedsudvalget", "Sjældenhedsudvalget"],
    ["Euro Bird Portal", "http://eurobirdportal.org/den/da/", "Euro Bird Portal"],
    ["Birdlife International", "http://www.birdlife.org/", "Birdlife International"],
    ["GBIF", "https://www.gbif.org/dataset/95db4db8-f762-11e1-a439-00145eb45e9a", "DOFbasens datasæt i GBIF"],
]
lists["links"] = {"title": "Links", "intro": "Eksterne links i menuen Links.", "columns": ["Titel", "Adresse", "Beskrivelse"], "rows": menu}
lists["stoet"] = {"title": "Støt DOF", "intro": "Eksterne links i menuen Støt DOF.", "columns": ["Titel", "Adresse", "Beskrivelse"], "rows": [
    ["Bliv medlem", "https://minside.dof.dk/bliv-medlem/", "Bliv medlem af Dansk Ornitologisk Forening"],
    ["Giv bidrag", "https://minside.dof.dk/stoet-naturen/", "Giv bidrag"],
]}
lists["statistik"] = {"title": "Statistik (eksterne)", "intro": "Undermenuen DOFbasen → Statistik peger på statistik.dofbasen.dk.", "columns": ["Titel", "Adresse", "Beskrivelse"], "rows": [
    ["Bidragydere", "https://statistik.dofbasen.dk/bidragsydere", "Statistik for bidragydere"],
    ["Lokaliteter", "https://statistik.dofbasen.dk/lokaliteter", "Statistik for lokaliteter"],
    ["Observationer pr. år", "https://statistik.dofbasen.dk/observationer/aar", "Observationer pr. år"],
    ["Kommuner", "https://statistik.dofbasen.dk/observationer/kommune/2022", "Statistik for kommuner"],
    ["Arter", "https://statistik.dofbasen.dk/arter", "Statistik for arter"],
]}

import html as _html
areas = json.loads((S / "areas" / "areas.json").read_text()) if (S / "areas" / "areas.json").exists() else {"stor": [], "iba": []}
for _k in areas.values():
    for _a in _k:
        _a["navn"] = _html.unescape(_a["navn"])

content = {"source": SOURCE, "pages": pages, "news": news, "focus": focus, "lists": lists, "areas": areas}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(content, ensure_ascii=False, indent=1))
print("sider", len(pages), "nyheder", len(news), "lister", {k: len(v["rows"]) for k, v in lists.items()},
      "områder", {k: len(v) for k, v in areas.items()})
