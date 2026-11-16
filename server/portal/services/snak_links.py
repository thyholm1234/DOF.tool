"""Forhåndsvisning af eksterne links i Snak (docs/snak-modul-spec.md §5.4).

Serveren henter siden med SSRF-hooket fra ``egress``: kun http(s), offentlige adresser, 5 s, højst 1 MB og kun ``text/html``.
Billedet hentes ikke – kun dets adresse (https) gemmes. Resultatet caches 24 timer pr. adresse; kald begrænses pr. bruger."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin, urlsplit

import httpx

from portal.cache import kv
from portal.services import egress

MAX_BYTES = 1_000_000
TTL = 86_400
PER_MINUTE = 20
_META = re.compile(r"<meta\s+[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z:_-]+)\s*=\s*("([^"]*)"|'([^']*)')""")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


class LinkError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def valid_url(url: str) -> str:
    url = (url or "").strip()
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or len(url) > 2000:
        raise LinkError(400, "Ugyldigt link")
    return url


def parse(page: str, base: str) -> dict:
    """Læs og:title, og:description og og:image (ellers <title>) ud af HTML."""
    og: dict[str, str] = {}
    for tag in _META.findall(page[:200_000]):
        attrs = {m[1].lower(): html.unescape(m[3] or m[4] or "") for m in _ATTR.finditer(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in ("og:title", "og:description", "og:image", "description") and "content" in attrs:
            og.setdefault(key, attrs["content"])
    title = og.get("og:title") or (html.unescape(re.sub(r"\s+", " ", m[1]).strip()) if (m := _TITLE.search(page[:200_000])) else "")
    image = urljoin(base, og["og:image"]) if og.get("og:image") else None
    if image and not image.startswith("https://"):
        image = None
    return {"title": title[:200], "teaser": (og.get("og:description") or og.get("description") or "")[:300], "image": image}


async def fetch(url: str) -> dict:
    out = {"type": "link", "ref": url, "url": url, "host": host_of(url), "title": "", "teaser": "", "image": None}
    try:
        async with egress.client(timeout=5, max_redirects=3, headers={"User-Agent": "DOF.tool-linkpreview", "Accept": "text/html"}) as client:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", "").lower():
                    return out
                buf = b""
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        break
                out.update(parse(buf[:MAX_BYTES].decode(r.encoding or "utf-8", errors="replace"), str(r.url)))
    except (httpx.HTTPError, ValueError, OSError):
        pass  # kortet viser kun værtsnavnet
    return out


async def preview(user_id: int, url: str) -> dict:
    url = valid_url(url)
    key = f"snak:link:rate:{user_id}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 60)
    if n > PER_MINUTE:
        raise LinkError(429, "For mange forhåndsvisninger – vent lidt")
    ck = "snak:link:" + url
    if hit := await kv().get(ck):
        return json.loads(hit)
    out = await fetch(url)
    await kv().set(ck, json.dumps(out), ex=TTL)
    return out
