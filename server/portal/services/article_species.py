"""Hvilke arter en artikel handler om (docs/artikler-modul-spec.md §5.3).

Tre kilder, i prioriteret rækkefølge: emneord der er en art (``emneord``), artsnavne i titel/manchet/brødtekst (``tekst``) og i
OCR-teksten fra PDF'er (``ocr``). Redaktørens egne valg (``redaktoer``) og fravalg (``fravalgt``) bliver aldrig overskrevet.

Matcheren tokeniserer teksten og slår 1–4 ord op i en ordbog (længste træf først) i stedet for én stor regex med tusindvis
af alternativer – det er hurtigt nok til hele arkivet. Danske bøjninger genereres for artsnavnets sidste ord
(«Dværgterne» → «Dværgterner», «Dværgternerne», «Dværgternes» …), og «Almindelig» kan skrives «Alm.».
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.services import article_settings

WORD = re.compile(r"[\wæøåÆØÅ]+", re.UNICODE)
SENTENCE_END = ".!?:;\n"
MAX_NGRAM = 4
MIN_LEN = 4  # kortere navne (And, Ørn …) giver for mange falske træf


def inflections(word: str) -> set[str]:
    """Bøjningsformer af et dansk substantiv (grundform, flertal, bestemt form, genitiv)."""
    w = word.lower()
    forms = {w, w + "s"}
    if w.endswith("e"):
        forms |= {w + "r", w + "rne", w + "rs", w + "rnes", w + "n", w + "ns"}
    else:
        forms |= {w + "e", w + "er", w + "erne", w + "en", w + "ens", w + "ernes", w + "ne"}
    return forms


def name_forms(name: str) -> set[str]:
    """Alle opslagsnøgler (små bogstaver, ord adskilt af mellemrum) for ét artsnavn."""
    words = [w.lower() for w in WORD.findall(name)]
    if not words:
        return set()
    head, last = words[:-1], words[-1]
    forms = {" ".join(head + [f]) for f in inflections(last)}
    if head and head[0] == "almindelig":
        forms |= {" ".join(["alm"] + head[1:] + [f]) for f in inflections(last)}
    return forms


@dataclass
class Hit:
    hits: int
    first_pos: int


class SpeciesMatcher:
    def __init__(self, rows: list[tuple[int, str, str | None]], ambiguous: set[str] | None = None):
        """rows: (artnr, dansk navn, latinsk navn)."""
        self.forms: dict[str, int] = {}
        self.exact: dict[str, int] = {}  # navn/latin (små bogstaver) → artnr, til emneord
        self.ambiguous = {a.lower() for a in (ambiguous or set())}
        self.ambiguous_keys: set[str] = set()
        for artnr, navn, latin in rows:
            if navn:
                self.exact.setdefault(navn.lower(), artnr)
            if latin:
                self.exact.setdefault(latin.lower(), artnr)
            keys = set()
            if navn and len(navn) >= MIN_LEN:
                keys |= name_forms(navn)
            if latin and " " in latin:
                keys.add(" ".join(w.lower() for w in WORD.findall(latin)))
            for k in keys:
                if len(k.split()) <= MAX_NGRAM:
                    self.forms.setdefault(k, artnr)
            if navn and navn.lower() in self.ambiguous:
                self.ambiguous_keys |= name_forms(navn)

    def tag_species(self, tag: str) -> int | None:
        return self.exact.get(tag.strip().lower())

    def find(self, text_: str) -> dict[int, Hit]:
        """artnr → (antal træf, første position). Tvetydige navne tæller kun med stort begyndelsesbogstav midt i en sætning."""
        found: dict[int, Hit] = {}
        if not text_:
            return found
        toks = [(m.group(), m.start(), m.end()) for m in WORD.finditer(text_)]
        keys = [t[0].lower() for t in toks]
        i = 0
        while i < len(toks):
            for n in range(min(MAX_NGRAM, len(toks) - i), 0, -1):
                key = " ".join(keys[i:i + n])
                artnr = self.forms.get(key)
                if artnr is None:
                    continue
                start = toks[i][1]
                if key in self.ambiguous_keys and not self._plausible(text_, start):
                    continue
                h = found.get(artnr)
                if h:
                    h.hits += 1
                else:
                    found[artnr] = Hit(1, start)
                i += n - 1
                break
            i += 1
        return found

    @staticmethod
    def _plausible(text_: str, start: int) -> bool:
        if not text_[start].isupper():
            return False
        j = start - 1
        while j >= 0 and text_[j] in " \t\"'«»(":
            j -= 1
        return j >= 0 and text_[j] not in SENTENCE_END  # ikke første ord i en sætning (der er alle ord store)


# ---------------------------------------------------------------- database

_cache: dict = {"at": 0.0, "matcher": None, "amb": None}


async def load_matcher(session: AsyncSession, *, fresh: bool = False) -> SpeciesMatcher:
    amb = tuple(sorted((await article_settings.get(session))["ambiguous_species"]))
    if not fresh and _cache["matcher"] is not None and time.monotonic() - _cache["at"] < 600 and _cache["amb"] == amb:
        return _cache["matcher"]
    rows = (await session.execute(text("""
        SELECT CASE WHEN art_type = 'underart' THEN coalesce(list_artnr, artnr) ELSE artnr END, navn, latin
        FROM species WHERE NOT excluded AND art_type IN ('art', 'underart') ORDER BY (art_type = 'art') DESC, sortering NULLS LAST
    """))).all()
    matcher = SpeciesMatcher([(r[0], r[1], r[2]) for r in rows], set(amb))
    _cache.update(at=time.monotonic(), matcher=matcher, amb=amb)
    return matcher


async def index_article(session: AsyncSession, article_id: int, matcher: SpeciesMatcher | None = None) -> int:
    """Genberegn artikelens arter. Returnerer antal arter. Redaktørens valg og fravalg bevares."""
    matcher = matcher or await load_matcher(session)
    a = (await session.execute(text("SELECT title, lead, body_text, ocr_text FROM art_articles WHERE id = :i"), {"i": article_id})).mappings().first()
    if a is None:
        return 0
    keep = {r[0]: r[1] for r in (await session.execute(text(
        "SELECT artnr, source FROM art_species WHERE article_id = :i AND source IN ('redaktoer', 'fravalgt')"), {"i": article_id})).all()}
    found: dict[int, tuple[str, int, int | None]] = {}
    tag_species = {r[0] for r in (await session.execute(text(
        "SELECT t.artnr FROM art_article_tags x JOIN art_tags t ON t.id = x.tag_id WHERE x.article_id = :i AND t.kind = 'art' AND t.artnr IS NOT NULL"),
        {"i": article_id})).all()}
    for n in tag_species:
        found[n] = ("emneord", 1, None)
    main = "\n".join(filter(None, [a["title"], a["lead"], a["body_text"]]))
    for n, h in matcher.find(main).items():
        # Tvetydige navne (Ravn …) tæller i teksten kun, hvis de er markeret som emneord – det er filtreret i find()
        found[n] = ("emneord", h.hits, h.first_pos) if n in tag_species else ("tekst", h.hits, h.first_pos)
    if a["ocr_text"]:
        for n, h in matcher.find(a["ocr_text"]).items():
            if n not in found:
                found[n] = ("ocr", h.hits, h.first_pos)
    await session.execute(text("DELETE FROM art_species WHERE article_id = :i AND source NOT IN ('redaktoer', 'fravalgt')"), {"i": article_id})
    rows = [{"i": article_id, "n": n, "s": s, "h": h, "p": p} for n, (s, h, p) in found.items() if n not in keep]
    if rows:
        await session.execute(text("INSERT INTO art_species (article_id, artnr, source, hits, first_pos) VALUES (:i, :n, :s, :h, :p) "
                                   "ON CONFLICT (article_id, artnr) DO NOTHING"), rows)
    return len(rows) + sum(1 for v in keep.values() if v == "redaktoer")


async def detect(session: AsyncSession, plain: str) -> list[dict]:
    """Arter nævnt i en tekst (forhåndsvisning i editoren): [{artnr, navn, hits}]."""
    found = (await load_matcher(session)).find(plain)
    if not found:
        return []
    names = {r[0]: r[1] for r in (await session.execute(text("SELECT artnr, navn FROM species WHERE artnr = ANY(:a)"), {"a": list(found)})).all()}
    return sorted(({"artnr": n, "navn": names.get(n, str(n)), "hits": h.hits} for n, h in found.items()), key=lambda x: -x["hits"])
