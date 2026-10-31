"""Fælles markdown-renderer til artikler, nyheder og kommentarer (docs/artikler-modul-spec.md §7.3).

Markdown renderes med markdown-it-py og renses bagefter med nh3, så al HTML – også rå HTML en redaktør har skrevet ind –
kun indeholder tilladte tags, attributter og adresser. Teksten gemmer portable referencer, ikke adresser:

* ``media:<id>``      → billede/fil i content_media (/media/art/…); ``![titel](media:<id>)`` af en lydfil giver en afspiller
* ``netfugl:<NNNN>``  → artikel med dette netfugl.dk-nummer (#/artikler/n/NNNN)
* ``artikel:<id>``    → artikel (#/artikler/<id>)
* ``[[art:<artnr>]]`` → artschip

Rendereren er ren (ingen I/O). ``render_for`` henter de medier og artsnavne, teksten henviser til, og kalder den.
"""

from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import nh3
from markdown_it import MarkdownIt
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.footnote import footnote_plugin
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RENDER_VERSION = 1
PROFILES = ("artikel", "nyhed", "kommentar")
MEDIA_URL = "/media/art/"

MEDIA_REF = re.compile(r"media:(\d+)")
ART_REF = re.compile(r"\[\[art:(\d+)\]\]")
SANDBOX = "allow-scripts allow-same-origin allow-popups allow-presentation"

RICH_TAGS = {"p", "h2", "h3", "h4", "h5", "strong", "em", "del", "s", "a", "ul", "ol", "li", "blockquote", "code", "pre",
             "table", "thead", "tbody", "tr", "th", "td", "img", "hr", "br", "sup", "sub", "span", "div", "details",
             "summary", "iframe", "figure", "figcaption", "section", "audio"}
COMMENT_TAGS = {"p", "strong", "em", "del", "a", "ul", "ol", "li", "blockquote", "code", "br"}
CLASSES = {"art-galleri", "art-boks", "art-boks-titel", "art-chip", "art-pdf", "art-fig", "art-cap", "art-video", "art-lyd",
           "footnotes", "footnote-ref", "footnote-item", "footnotes-sep"}
RICH_ATTRS = {
    "a": {"href", "title", "id"},
    "img": {"src", "alt", "title", "width", "height", "srcset", "sizes", "loading"},
    "iframe": {"src", "width", "height", "allow", "allowfullscreen", "title"},
    "audio": {"src", "controls", "preload", "title"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align"},
    "li": {"id"},
    "sup": {"id"},
    "h2": {"id"}, "h3": {"id"}, "h4": {"id"},
}


@dataclass
class Media:
    id: int
    kind: str  # billede / pdf / lyd / fil
    url: str
    medium_url: str | None = None
    thumb_url: str | None = None
    width: int | None = None
    height: int | None = None
    alt: str = ""
    caption: str = ""
    credit: str = ""
    filename: str = ""
    size: int = 0
    pages: int | None = None

    @classmethod
    def from_row(cls, r) -> "Media":
        return cls(id=r["id"], kind=r["kind"], url=media_url(r["storage_key"]), medium_url=media_url(r["medium_key"]),
                   thumb_url=media_url(r["thumb_key"]), width=r["width"], height=r["height"], alt=r["alt"] or "",
                   caption=r["caption"] or "", credit=r["credit"] or "", filename=r["filename"], size=r["size"] or 0,
                   pages=r["pages"])


def media_url(key: str | None) -> str | None:
    return MEDIA_URL + key if key else None


@dataclass
class Rendered:
    html: str
    text: str
    warnings: list[str] = field(default_factory=list)
    media_ids: list[int] = field(default_factory=list)
    species: list[int] = field(default_factory=list)


@dataclass
class Ctx:
    media: dict[int, Media]
    species: dict[int, str]
    embed_hosts: set[str]
    warnings: list[str]


def media_ids(md: str) -> list[int]:
    return sorted({int(m) for m in MEDIA_REF.findall(md or "")})


def species_refs(md: str) -> list[int]:
    return sorted({int(m) for m in ART_REF.findall(md or "")})


def size_label(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB".replace(".", ",")
    return f"{max(1, round(n / 1000))} kB"


def host_allowed(url: str, hosts: set[str]) -> bool:
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in hosts)


# ---------------------------------------------------------------- markdown-it

def _build(profile: str) -> MarkdownIt:
    if profile == "kommentar":
        md = MarkdownIt("commonmark", {"html": False, "breaks": True}).disable(["image", "table", "heading", "lheading", "html_block", "html_inline"])
        return md
    md = MarkdownIt("commonmark", {"html": True, "breaks": False}).enable(["table", "strikethrough"])
    md.use(footnote_plugin)
    md.use(container_plugin, "galleri", render=_render_galleri)
    md.use(container_plugin, "boks", render=_render_boks)
    md.inline.ruler.before("link", "art_ref", _art_ref_rule)
    return md


def _render_galleri(self, tokens, idx, options, env):
    return '<div class="art-galleri">\n' if tokens[idx].nesting == 1 else "</div>\n"


def _render_boks(self, tokens, idx, options, env):
    t = tokens[idx]
    if t.nesting == 1:
        title = t.info.strip()[len("boks"):].strip()
        head = f'<div class="art-boks-titel">{htmllib.escape(title)}</div>' if title else ""
        return f'<div class="art-boks">{head}\n'
    return "</div>\n"


def _art_ref_rule(state, silent):
    src, pos = state.src, state.pos
    if not src.startswith("[[art:", pos):
        return False
    m = ART_REF.match(src, pos)
    if not m:
        return False
    if not silent:
        tok = state.push("art_ref", "", 0)
        tok.content = m.group(1)
    state.pos = m.end()
    return True


def _install_renderers(md: MarkdownIt, ctx: Ctx, profile: str) -> None:
    rules = md.renderer.rules

    def art_ref(tokens, idx, options, env):
        n = int(tokens[idx].content)
        name = ctx.species.get(n)
        if not name:
            return htmllib.escape(f"[[art:{n}]]")
        return f'<a class="art-chip" href="#/arter/{n}">{htmllib.escape(name)}</a>'

    rules["art_ref"] = art_ref

    def link_open(tokens, idx, options, env):
        t = tokens[idx]
        href = t.attrGet("href") or ""
        cls = None
        if href.startswith("media:"):
            m = ctx.media.get(int(href[6:] or 0)) if href[6:].isdigit() else None
            if m is None:
                ctx.warnings.append(f"Linket {href} peger på et medie, der ikke findes")
                href = "#"
            else:
                href = m.url
                cls = {"pdf": "art-pdf", "lyd": "art-lyd"}.get(m.kind)
        elif href.startswith("netfugl:") and href[8:].isdigit():
            href = f"#/artikler/n/{int(href[8:])}"
        elif href.startswith("artikel:") and href[8:].isdigit():
            href = f"#/artikler/{int(href[8:])}"
        if profile == "kommentar" and not re.match(r"(https?://|mailto:)", href, re.I):
            t.attrs = {}  # kun ægte adresser i kommentarer: et relativt link bliver ren tekst
            return md.renderer.renderToken(tokens, idx, options, env)
        t.attrSet("href", href)
        if cls:
            t.attrSet("class", cls)
        if re.match(r"https?://", href):
            t.attrSet("target", "_blank")
            t.attrSet("rel", "noopener noreferrer" + (" nofollow ugc" if profile == "kommentar" else ""))
        return md.renderer.renderToken(tokens, idx, options, env)

    rules["link_open"] = link_open

    def image(tokens, idx, options, env):
        t = tokens[idx]
        src = t.attrGet("src") or ""
        alt = t.content or ""
        caption = t.attrGet("title") or ""
        if src.startswith("media:") and src[6:].isdigit() and int(src[6:]) in ctx.media:
            m = ctx.media[int(src[6:])]
            if m.kind == "lyd":
                cap = caption or alt or m.caption or m.filename
                return (f'<span class="art-fig art-lyd"><audio controls preload="metadata" src="{htmllib.escape(m.url)}" title="{htmllib.escape(cap)}"></audio>'
                        f'<span class="art-cap">{htmllib.escape(cap)}</span></span>')
            main = m.medium_url or m.url
            attrs = f'src="{htmllib.escape(main)}" alt="{htmllib.escape(alt or m.alt)}" loading="lazy"'
            if m.width and m.height:
                attrs += f' width="{m.width}" height="{m.height}"'
            if m.thumb_url and m.medium_url:
                attrs += f' srcset="{htmllib.escape(m.thumb_url)} 400w, {htmllib.escape(m.medium_url)} 1200w"'
                attrs += ' sizes="(max-width: 700px) 100vw, 700px"'
            cap = caption or m.caption
            if m.credit:
                cap = f"{cap} ({m.credit})" if cap else m.credit
            tail = f'<span class="art-cap">{htmllib.escape(cap)}</span>' if cap else ""
            return f'<span class="art-fig"><a href="{htmllib.escape(m.url)}" target="_blank" rel="noopener"><img {attrs}></a>{tail}</span>'
        if src.startswith("media:"):
            ctx.warnings.append(f"Billedet {src} findes ikke")
            return ""
        if src.startswith("/media/"):
            return f'<img src="{htmllib.escape(src)}" alt="{htmllib.escape(alt)}" loading="lazy">'
        # Eksterne billeder indlæses ikke (læserens IP ville blive sendt til tredjepart): vis et link i stedet
        ctx.warnings.append("Eksternt billede erstattet af et link – upload billedet i stedet")
        if re.match(r"https?://", src):
            return f'<a href="{htmllib.escape(src)}" target="_blank" rel="noopener noreferrer">{htmllib.escape(alt or src)}</a>'
        return ""

    if profile != "kommentar":
        rules["image"] = image


# ---------------------------------------------------------------- sanitering

_IFRAME_NO_SRC = re.compile(r"<iframe(?![^>]*\ssrc=)[^>]*>\s*</iframe>", re.I)
_IMG_NO_SRC = re.compile(r"<img(?![^>]*\ssrc=)[^>]*>", re.I)
_AUDIO_NO_SRC = re.compile(r"<audio(?![^>]*\ssrc=)[^>]*>\s*</audio>", re.I)


def sanitize(html: str, profile: str, embed_hosts: set[str], warnings: list[str] | None = None) -> str:
    warnings = warnings if warnings is not None else []
    if profile == "kommentar":
        return nh3.clean(html, tags=COMMENT_TAGS, attributes={"a": {"href", "target"}}, url_schemes={"http", "https", "mailto"},
                         link_rel="noopener noreferrer nofollow ugc")

    def attr_filter(tag: str, attr: str, value: str) -> str | None:
        if tag == "iframe" and attr == "src":
            if host_allowed(value, embed_hosts):
                return value
            warnings.append(f"Indlejring fra {urlsplit(value).hostname or value} er ikke tilladt (Artikler → Admin → Indstillinger)")
            return None
        if tag in ("img", "audio") and attr == "src":
            return value if value.startswith("/media/") else None
        if tag == "img" and attr == "srcset":
            return value if all(p.strip().startswith("/media/") for p in value.split(",")) else None
        if tag == "a" and attr == "href":
            return value
        return value

    attrs = {k: set(v) for k, v in RICH_ATTRS.items()}
    attrs["a"] = attrs["a"] | {"target"}
    out = nh3.clean(
        html, tags=RICH_TAGS, attributes=attrs, attribute_filter=attr_filter, url_schemes={"http", "https", "mailto"},
        clean_content_tags={"script", "style", "template"}, allowed_classes={t: set(CLASSES) for t in ("div", "span", "a", "section", "sup", "li", "ol", "hr")},
        link_rel="noopener noreferrer", set_tag_attribute_values={"iframe": {"sandbox": SANDBOX, "loading": "lazy", "referrerpolicy": "no-referrer"}},
    )
    out = _IFRAME_NO_SRC.sub("", out)
    return _AUDIO_NO_SRC.sub("", _IMG_NO_SRC.sub("", out))


_BLOCK_END = re.compile(r"</(p|h[1-6]|li|tr|blockquote|div|pre|figcaption)>|<br\s*/?>|</t[dh]>", re.I)


def to_text(html: str) -> str:
    """Ren tekst (til søgning og uddrag) af renset HTML."""
    spaced = _BLOCK_END.sub(lambda m: "\n" if m.group(0).lower().startswith(("</p", "</h", "</li", "<br", "</tr", "</block", "</div", "</pre", "</fig")) else " ", html)
    plain = htmllib.unescape(nh3.clean(spaced, tags=set())).replace("\xa0", " ").replace("\u200b", "")
    plain = re.sub(r"[ \t\r\f\v]+", " ", plain)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r" ?\n ?", "\n", plain)).strip()


def teaser(plain: str, n: int = 280) -> str:
    one = re.sub(r"\s+", " ", plain).strip()
    if len(one) <= n:
        return one
    cut = one[:n].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:–-") + " …"


def slugify(title: str, n: int = 80) -> str:
    t = title.lower().translate(str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "é": "e", "ü": "u", "ö": "o"}))
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:n].strip("-")


# ---------------------------------------------------------------- offentligt

def render(md_text: str, profile: str = "artikel", *, media: dict[int, Media] | None = None, species: dict[int, str] | None = None,
           embed_hosts: set[str] | None = None) -> Rendered:
    if profile not in PROFILES:
        raise ValueError(f"Ukendt profil {profile}")
    ctx = Ctx(media=media or {}, species=species or {}, embed_hosts={h.lower() for h in (embed_hosts or set())}, warnings=[])
    md = _build(profile)
    _install_renderers(md, ctx, profile)
    raw = md.render(md_text or "")
    html = sanitize(raw, profile, ctx.embed_hosts, ctx.warnings)
    return Rendered(html=html, text=to_text(html), warnings=list(dict.fromkeys(ctx.warnings)), media_ids=media_ids(md_text),
                    species=species_refs(md_text))


async def load_context(session: AsyncSession, md_text: str) -> tuple[dict[int, Media], dict[int, str]]:
    ids = media_ids(md_text)
    media: dict[int, Media] = {}
    if ids:
        rows = (await session.execute(text(
            "SELECT id, kind, storage_key, medium_key, thumb_key, width, height, alt, caption, credit, filename, size, pages "
            "FROM content_media WHERE id = ANY(:ids)"), {"ids": ids})).mappings().all()
        media = {r["id"]: Media.from_row(r) for r in rows}
    arts = species_refs(md_text)
    species: dict[int, str] = {}
    if arts:
        rows = (await session.execute(text("SELECT artnr, navn FROM species WHERE artnr = ANY(:a)"), {"a": arts})).all()
        species = {r[0]: r[1] for r in rows}
    return media, species


async def render_for(session: AsyncSession, md_text: str, profile: str = "artikel") -> Rendered:
    from portal.services import article_settings

    media, species = await load_context(session, md_text) if profile != "kommentar" else ({}, {})
    hosts = set((await article_settings.get(session))["embed_hosts"]) if profile != "kommentar" else set()
    return render(md_text, profile, media=media, species=species, embed_hosts=hosts)
