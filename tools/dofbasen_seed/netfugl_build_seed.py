"""Bygger server/portal/seed/netfugl/content.json ud fra de crawlede sider fra netfugl.dk (23. okt. 2026).

Brug:  python nf_build_seed.py <mappe med pages/ fra crawl.py> <ud-fil>

Kun tekst, lister og links kopieres – som eksempel; indholdet tilhører Netfugl/DOF. Fotos, artikler i fuld længde,
kommentarer og brugerprofiler kopieres ikke (artikler kun som titel, kort uddrag og link til originalen).
"""
import html as H
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conv import Conv, clean  # noqa: E402

S = Path(sys.argv[1])
OUT = Path(sys.argv[2])
P = S / "pages"
BASE = "https://www.netfugl.dk"


def page(name: str) -> str:
    return (P / f"{name}.html").read_text(encoding="utf-8")


def content(name: str) -> str:
    """Selve indholdet (efter topbanneret, før annoncerne) som let markup."""
    h = page(name)
    a = h.find('class="content-wrapper container"')
    b = h.find("ANNONCER", a)
    frag = h[a:b if b > 0 else None]
    frag = frag[frag.find(">") + 1:]
    c = Conv()
    c.start("x")
    c.feed(frag)
    body = clean(c.sections[0]["body"])
    body = body.replace("https://beta.netfugl.dk", BASE)
    body = re.sub(r"\]\(mailto:%20", "](mailto:", body)
    body = re.sub(r"\n?\[FAQ oversigt!\]\([^)]*\)", "", body)
    return body.strip()


class Tables:
    def __init__(self, html):
        from html.parser import HTMLParser

        class TP(HTMLParser):
            def __init__(s):
                super().__init__(convert_charrefs=True)
                s.tables, s.row, s.cell, s.cls = [], None, None, ""

            def handle_starttag(s, t, a):
                a = dict(a)
                if t == "table":
                    s.tables.append([])
                elif t == "tr" and s.tables:
                    s.row, s.cls = [], a.get("class", "") or ""
                elif t in ("td", "th") and s.row is not None:
                    s.cell = ""

            def handle_endtag(s, t):
                if t in ("td", "th") and s.cell is not None:
                    s.row.append(re.sub(r"\s+", " ", s.cell).strip())
                    s.cell = None
                elif t == "tr" and s.row is not None:
                    if s.row:
                        s.tables[-1].append((s.row, s.cls))
                    s.row = None

            def handle_data(s, d):
                if s.cell is not None:
                    s.cell += d

        tp = TP()
        tp.feed(html)
        self.tables = tp.tables


pages = {
    "om": {"title": "Om Netfugl", "body": content("_om")},
    "faq": {"title": "FAQ", "body": content("_faq")},
    "krydslister": {"title": "Om krydslister", "body": "\n\n".join([
        "Velkommen til brugernes krydslister på Netfugl",
        "På denne side gives der overblik over seneste aktiviteter på både DK og VP krydslisterne, ud fra Netfugl brugernes egne opdateringer.",
        "Netfugl byder på mange forskellige typer af krydslister, en komplet oversigt af alle krydslister kan ses [her](https://www.netfugl.dk/ranking/alle).",
        "**Nyt i showcasen:** DK-krydslisterne, årsarter, månedsarter og lokalarter beregnes automatisk ud fra dine observationer i DOFbasen. "
        "Du supplerer kun manuelt (fx arter set før DOFbasen, i udlandet, ringmærkningsarter og ædelarter)."])},
    "regler": {"title": "Krydsregler", "body": content("_ranking_regler")},
    "stoet": {"title": "Støt Netfugl", "body": content("_stoet")},
}

# Ordforklaringer: <b>term</b> forklaring … → liste (term, forklaring)
h = page("_ordforklaringer")
frag = h[h.find('class="content-wrapper container"'):h.find("ANNONCER")]
words = []
for m in re.finditer(r"<b>(.*?)</b>(.*?)(?=<b>|</section>)", frag, re.S):
    term = H.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    expl = H.unescape(re.sub(r"<br\s*/?>", "\n", m.group(2)))
    expl = re.sub(r"<[^>]+>", "", expl).replace("\xa0", " ")
    expl = re.sub(r"\n{3,}", "\n\n", "\n".join(x.strip() for x in expl.split("\n"))).strip()
    if term:
        words.append([term, expl])

# DK-listen og VP-listen
dk = [r for r, _ in Tables(page("_artslister_dk")).tables[0][1:] if len(r) == 4]
vp = [r for r, _ in Tables(page("_artslister_vp")).tables[0][1:] if len(r) == 5]

# Kategorier (A–F) fra "Se kategorier"
cat_html = page("_artslister_dk")
cats = []
for m in re.finditer(r"Kategori ([A-F])\s*</[^>]+>\s*(.*?)(?=Kategori [A-F]\s*<|Krydsbare arter)", cat_html, re.S):
    txt = re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
    cats.append([m.group(1), txt])
cats = list({c[0]: c for c in cats}.values())

# Verdens fuglefamilier
arter = page("_arter")
families, order = [], None
for m in re.finditer(r'<h6 class="red-text">([A-Z]+)</h6>|<a href="/arter/(\d+)" class="row">\s*<div[^>]*>(.*?)</div>\s*<div[^>]*>(.*?)</div>\s*<div[^>]*>(\d+) species</div>', arter, re.S):
    if m.group(1):
        order = m.group(1)
    else:
        families.append([order, H.unescape(m.group(3).strip()), H.unescape(m.group(4).strip()), m.group(5)])

# Links på forsiden (fire grupper)
front = page("_")
links = []
seg = front[front.find('id="frontpage-links"'):]
seg = seg[:seg.find("ANNONCER")]
group = None
for m in re.finditer(r"<b>(.*?)</b>|<a target=\"_blank\" href=\"([^\"]+)\">(.*?)</a>", seg, re.S):
    if m.group(1):
        group = H.unescape(m.group(1).strip())
    elif group:
        links.append([group, H.unescape(re.sub(r"<[^>]+>", "", m.group(3)).strip()), m.group(2)])

# RSS-kilder: navne fra RSS-siden, adresser fra forsidens live-felter
rss_page = page("_rss")
names = [H.unescape(x.strip()) for x in re.findall(r'<option[^>]*value="[^"]*"[^>]*>([^<]+)</option>', rss_page[rss_page.find("Site"):rss_page.find("Land")])]
lands = {}
site_urls = {}
link_lines = [x.split(" | ") for x in (P / "_.links").read_text().split("\n") if " | " in x]
for url, label in link_lines:
    for n in names:
        if label.strip() == n:
            site_urls[n] = url
KNOWN = {  # kendte adresser for kilder, der ikke står på forsiden
    "Audubon": "https://www.audubon.org/", "Norsk Ornitologisk Forening": "https://www.birdlife.no/",
    "Surfbirds": "https://www.surfbirds.com/", "Birdcast": "https://birdcast.info/",
    "The Cornell Lab of Ornithology - All About Birds": "https://www.allaboutbirds.org/",
    "American Birding Association Blog": "https://blog.aba.org/", "British Birds Rarities Committee (BBRC)": "https://www.bbrc.org.uk/",
    "British Ornithologists' Union": "https://www.bou.org.uk/", "BirdWatching": "https://www.birdwatchingdaily.com/",
    "DOF": "https://www.dof.dk/", "Danmarks Ringmærkerforening": "https://www.ringmaerkning.dk/",
}
LAND = {"Audubon": "USA", "American Birding Association Blog": "USA", "Birdcast": "USA", "BirdWatching": "USA", "Bird Watching HQ": "USA",
        "The Cornell Lab of Ornithology - All About Birds": "USA", "Birding Planet": "USA", "Norsk Ornitologisk Forening": "Norge",
        "Oslo Birder RSS Feed": "Norge", "British Birds Rarities Committee (BBRC)": "Storbritannien", "British Ornithologists' Union": "Storbritannien",
        "Surfbirds": "Storbritannien", "Saving the Spoon Billed Sandpiper": "Storbritannien"}
feeds = [[n, site_urls.get(n) or KNOWN.get(n, ""), LAND.get(n, "Danmark")] for n in names]

# Artikler: titel, dato, forfattere, kort uddrag og link (ikke hele teksten)
articles = []
for url, label in link_lines:
    if re.match(r"https://www\.netfugl\.dk/artikler/\d+/", url) and label.strip() and not any(a["source_url"] == url for a in articles):
        articles.append({"source_url": url, "title": label.strip()})
art_text = (P / "_artikler_3580_felttraefafrunding_2026.txt").read_text()
first_par = next((x for x in art_text.split("\n") if x.startswith("Perioden op til")), "")
dates = {"Felttræfafrunding 2026": ("2026-10-18", "Andreas Kristian Pedersen, Jens Ryge Petersen"),
         "Hjejler og Viber skal skrives i mandtal!": ("2026-10-17", "Lars Maltha Rasmussen"),
         "Prognose for Felttræf 2026": ("2026-10-09", "Henrik Böhmer")}
seed_articles = []
for a in articles:
    if a["title"] not in dates:
        continue
    d, w = dates[a["title"]]
    teaser = first_par if a["title"].startswith("Felttræfafrunding") else ""
    seed_articles.append({"title": a["title"], "dato": d, "writers": w, "kind": "artikel",
                          "tags": ["Felttræf", "2026"] if "Felttræf" in a["title"] else ["Optælling", "2026"],
                          "body": (teaser + "\n\n" if teaser else "") + f"_Uddrag – læs hele artiklen på netfugl.dk:_ [{a['title']}]({a['source_url']})",
                          "source_url": a["source_url"]})
kort = []
for line in (P / "_.links").read_text().split("\n"):
    m = re.match(r"(https://www\.netfugl\.dk/artikler/(\d+)) \| (.+)$", line)
    if m and int(m.group(2)) in (3551, 3552, 3553, 3554, 3581):
        kort.append({"title": m.group(3).strip(), "source_url": m.group(1)})
kort_dates = {3581: "2026-10-20", 3554: "2026-02-17", 3553: "2026-02-17", 3552: "2026-02-15", 3551: "2026-02-13"}
full_titles = {3581: "Dutch Birding: Holland har offentliggjort deres kategori C-liste",
               3554: "Det Trilaterale Vadehavssamarbejde: New report reveals trend for breeding birds in the Wadden Sea 1991-2023",
               3553: "Birdguides: Review of the Week: 9-15 February 2026", 3552: "Rare Bird Alert: Weekly birding round-up: 6 - 12 Feb 2026",
               3551: "Hedehøg i Danmark 2025"}
for nid, d in kort_dates.items():
    url = f"{BASE}/artikler/{nid}"
    seed_articles.append({"title": full_titles[nid], "dato": d, "writers": "", "kind": "kort", "tags": ["Kort nyt"],
                          "body": f"Kort nyt. Læs mere på netfugl.dk: [{full_titles[nid]}]({url})", "source_url": url})

content = {
    "source": "Kopieret fra netfugl.dk 23-10-2026 som eksempel. Indholdet tilhører Netfugl.dk / Dansk Ornitologisk Forening.",
    "pages": pages, "ordforklaringer": words, "kategorier": cats,
    "dk_list": dk, "vp_list": vp, "families": families, "links": links, "feeds": feeds, "articles": seed_articles,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(content, ensure_ascii=False, indent=1))
print({k: len(v) for k, v in content.items() if isinstance(v, (list, dict))})
