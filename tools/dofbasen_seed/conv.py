"""Konverterer crawlede dofbasen-sider (HTML) til sektioner med let markup: afsnit, • punkter, | tabeller |, [links](…), **fed**, _kursiv_."""
import re, sys
from html.parser import HTMLParser
from urllib.parse import urljoin

SKIP = {"GENVEJE", "DANMARKS FUGLE", "FOREIGNERS", "TIPS", "IBA LOKALITETER", "DOKUMENTATION", "LINKS"}

class Conv(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = []; self.buf = None; self.in_head = False; self.head = ""
        self.link = None; self.skip = 0; self.cell = False; self.row = None
    def start(self, title):
        self.sections.append({"title": title, "body": ""}); self.buf = self.sections[-1]
    def w(self, s):
        if self.buf is not None and not self.skip:
            if self.link is not None: self.link[1] += s
            elif self.row is not None and self.cell: self.row[-1] += s
            else: self.buf["body"] += s
    def handle_starttag(self, t, a):
        a = dict(a)
        if t in ("script", "style", "select", "noscript"): self.skip += 1
        if t == "td" and "tophead" in (a.get("class") or "") or t == "td" and "mainheader" in (a.get("class") or ""):
            self.in_head = True; self.head = ""; return
        if t == "br": self.w("\n")
        elif t == "p": self.w("\n\n")
        elif t == "li": self.w("\n• ")
        elif t == "b" or t == "strong": self.w("**")
        elif t == "i" or t == "em": self.w("_")
        elif t == "a" and a.get("href") and not a["href"].startswith(("#", "javascript")):
            self.link = [urljoin("https://dofbasen.dk/", a["href"]), ""]
        elif t == "tr" and not self.in_head: self.row = []
        elif t in ("td", "th") and self.row is not None: self.cell = True; self.row.append("")
        elif t == "img" and a.get("alt"): pass
    def handle_endtag(self, t):
        if t in ("script", "style", "select", "noscript"): self.skip = max(0, self.skip - 1)
        if self.in_head and t == "td":
            self.in_head = False
            title = re.sub(r"\s+", " ", self.head).strip()
            if title in SKIP or not title: self.buf = None
            else: self.start(title)
            return
        if t == "b" or t == "strong": self.w("**")
        elif t == "i" or t == "em": self.w("_")
        elif t == "a" and self.link is not None:
            url, txt = self.link; self.link = None
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt: self.w(f"[{txt}]({url})")
        elif t in ("td", "th"): self.cell = False
        elif t == "tr" and self.row is not None:
            cells = [re.sub(r"\s+", " ", c).strip() for c in self.row]; self.row = None
            if any(cells): self.w("\n| " + " | ".join(cells) + " |")
        elif t in ("p", "ul", "table"): self.w("\n")
    def handle_data(self, d):
        if self.in_head: self.head += d; return
        self.w(re.sub(r"[ \t\r\n]+", " ", d))

def clean(body):
    body = body.replace("****", "").replace("** **", " ")
    body = re.sub(r"\*\*•\*\*\s*", "• ", body)
    lines = [l.strip() for l in body.split("\n")]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()

def convert(path):
    c = Conv(); c.feed(open(path, encoding="utf-8").read())
    secs = [{"title": s["title"].title() if s["title"].isupper() else s["title"], "raw_title": s["title"], "body": clean(s["body"])} for s in c.sections]
    return [s for s in secs if s["body"] or s["title"]]

if __name__ == "__main__":
    for p in sys.argv[1:]:
        print("#####", p)
        for s in convert(p): print("==", s["raw_title"]); print(s["body"][:6000]); print()
