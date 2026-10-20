"""Delelinks til tråde (/del/traad/<dato>/<artnr>/<loknr>), som også kan åbnes af folk uden bruger.

Indloggede sendes videre til tråden i appen (på Android åbner linket i den installerede app, da det ligger i dens scope).
Gæster får en lille server-renderet side med fundet – kun DOFbasens offentlige oplysninger: ingen hemmelige observationer,
noter, kommentarer eller egne indtastninger – plus opfordring til at hente appen og logge ind. Siden har Open Graph-tags,
så linket får et preview i Messenger, SMS osv.
"""

from __future__ import annotations

from datetime import date
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import COOKIE
from portal.cache import cached, kv
from portal.config import get_settings
from portal.db import get_session
from portal.dof.jobs import media_many
from portal.services import feed

router = APIRouter(include_in_schema=False)
MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"]
KAT = {"SU": "SU-art", "SUB": "SUB-art", "bemaerk": "Bemærkelsesværdig"}


def _num(n: int | None) -> str:
    return f"{n:,}".replace(",", ".") if n is not None else "–"


def _day(d: date) -> str:
    return f"{d.day}. {MONTHS[d.month - 1]} {d.year}"


async def _thread(db: AsyncSession, day: date, artnr: int, loknr: int) -> dict | None:
    """Trådens offentlige del (caches 5 min., så et link der deles bredt ikke rammer databasen hver gang)."""
    async def produce():
        t = await feed.thread_detail(db, day, artnr, loknr)
        if t is None:
            return {"missing": True}
        obs = t["observations"]
        media = await media_many([o["obsid"] for o in obs])
        n_comments = (await db.execute(text("""
            SELECT count(*) FROM thread_comments WHERE dato = :d AND artnr = :a AND loknr = :l AND NOT hidden
        """), {"d": day, "a": artnr, "l": loknr})).scalar()
        return {
            "art": t.get("art"), "latin": t.get("latin"), "lok": t.get("lok"), "kommune": t.get("kommune"),
            "kategori": next((o["kategori"] for o in obs if o["kategori"] in KAT), None),
            "max": max((o["antal"] or 0) for o in obs),
            "observations": [{"antal": o["antal"], "tid": o["obstidfra"] if o["obstidfra"] not in (None, "00:00") else o["turtidfra"],
                              "adf": o["adf"] or o["adfkode"], "koen": o["koen"], "alder": o["alderkode"], "observer": (o["observer"] or "").strip()}
                             for o in obs],
            "image": next((img for o in obs for img in ((media.get(o["obsid"]) or {}).get("images") or [])), None),
            "n_comments": n_comments,
        }
    data = await cached(f"share:{day}:{artnr}:{loknr}", 300, produce)
    return None if data.get("missing") else data


@router.get("/del/traad/{day}/{artnr}/{loknr}")
async def share_thread(day: date, artnr: int, loknr: int, request: Request, db: AsyncSession = Depends(get_session)):
    app_url = f"/#/traad/{day.isoformat()}/{artnr}/{loknr}"
    token = request.cookies.get(COOKIE)
    if token and await kv().exists(f"sess:{token}"):
        return RedirectResponse(app_url, status_code=302)
    t = await _thread(db, day, artnr, loknr)
    login_url = escape(f"/#/login?next={quote('#' + app_url[2:], safe='')}")
    if t is None:
        return HTMLResponse(_page("Fundet findes ikke", "", f"""
            <section class="card share-card"><h1>Fundet findes ikke (længere)</h1>
            <p class="muted">Observationen kan være slettet eller rettet i DOFbasen.</p></section>""", login_url), status_code=404)

    title = f"{_num(t['max'])} {t['art']} – {t['lok']}"
    n = len(t["observations"])
    desc = f"{_day(day)}{', ' + t['kommune'] + ' Kommune' if t['kommune'] else ''}. {n} {'observation' if n == 1 else 'observationer'} i DOFbasen."
    rows = "".join(f"""<li><span><b class="num">{_num(o['antal'])}</b> {escape(o['adf'] or '')}
        <small class="muted">{escape(' · '.join(x for x in (o['tid'], o['koen'], o['alder']) if x))}</small></span>
        <small class="muted">{escape(o['observer'])}</small></li>""" for o in t["observations"])
    kat = f'<span class="tag {escape(t["kategori"])}">{KAT[t["kategori"]]}</span> ' if t["kategori"] else ""
    image = f'<img class="share-img" src="{escape(t["image"])}" alt="Foto fra DOFbasen">' if t["image"] else ""
    comments = f" og {t['n_comments']} {'kommentar' if t['n_comments'] == 1 else 'kommentarer'}" if t["n_comments"] else ""
    body = f"""
      <section class="card share-card">
        <p class="muted share-kicker">{kat}{escape(_day(day))}</p>
        <h1>{_num(t['max'])} {escape(t['art'] or '')}</h1>
        <p class="lead">{f'<i>{escape(t["latin"])}</i> · ' if t['latin'] else ''}{escape(t['lok'] or '')}{escape(', ' + t['kommune'] + ' Kommune') if t['kommune'] else ''}</p>
        {image}
        <ul class="mini-list">{rows}</ul>
        <small class="muted">Fra DOFbasen. Kort, billeder, lyd, vejret{comments} kan ses i DOF.tool.</small>
      </section>
      <section class="card share-cta">
        <h2>Se hele fundet i DOF.tool</h2>
        <p>DOF.tool er feltornitologernes app: live-fund fra DOFbasen med kort og push-alarmer, årslister, trækprognoser og
        meget mere. Log ind med din obserkode fra DOFbasen – det er gratis.</p>
        <div class="share-actions">
          <a class="btn primary" href="{login_url}">Log ind og se fundet</a>
          <button class="btn hide" id="install" type="button">Installér appen</button>
        </div>
        <p class="muted hide" id="ios-hint">På iPhone: tryk på Del-knappen <b>⎙</b> og vælg <b>Føj til hjemmeskærm</b>.</p>
        <p class="muted share-small">Ingen obserkode? <a href="https://dofbasen.dk" target="_blank" rel="noopener">Opret dig gratis på DOFbasen</a>.</p>
      </section>"""
    base = get_settings().public_url.rstrip("/")
    og = f"""<meta property="og:type" content="article">
  <meta property="og:site_name" content="DOF.tool">
  <meta property="og:title" content="{escape(title)}">
  <meta property="og:description" content="{escape(desc)}">
  <meta property="og:url" content="{escape(base + request.url.path)}">
  <meta property="og:image" content="{escape(t['image'] or base + '/icons/icon-512.png')}">
  <meta name="twitter:card" content="{'summary_large_image' if t['image'] else 'summary'}">
  <meta name="description" content="{escape(desc)}">"""
    return HTMLResponse(_page(title, og, body, login_url), headers={"Cache-Control": "no-cache"})


def _page(title: str, head: str, body: str, login_url: str) -> str:
    return f"""<!doctype html>
<html lang="da">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#1f5a44">
  <title>{escape(title)} · DOF.tool</title>
  {head}
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/icons/icon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/icons/apple-icon-180.png">
  <link rel="stylesheet" href="/css/app.css">
</head>
<body class="share-page">
  <header class="topbar">
    <a class="brand" href="/">
      <svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="9" fill="var(--brand)"/><path d="M7 19c3.5-1 6-3.6 7.6-7.2.4-.9 1.6-1 2.1-.1l1 1.8 4.6-1.3c.6-.2 1 .5.6 1l-3.2 3.4c-1.4 4.3-5 6.9-9.6 6.9H7.6c-.9 0-1.2-1.2-.6-1.5z" fill="#fff"/><circle cx="17.6" cy="13.2" r=".9" fill="var(--brand)"/></svg>
      <span>DOF.tool<small>feltornitologernes portal</small></span>
    </a>
    <span class="spacer"></span>
    <a class="btn sm primary" href="{login_url}">Log ind</a>
  </header>
  <main class="share-main">{body}</main>
  <script src="/js/share-page.js"></script>
</body>
</html>"""
