"""Udgående HTTP til adresser, som brugere eller indhold bestemmer (RSS-kilder, billeder i importerede artikler).

SIKKERHED (SSRF): sådanne kald må aldrig ramme portalens egne tjenester (postgres, valkey, rustfs, dbadmin, api) eller
andre interne adresser på værten/LAN'et. Brug altid hooket på klienten, så også omdirigeringer kontrolleres:

    httpx.AsyncClient(..., follow_redirects=True, event_hooks={"request": [egress.check_request]})

Kendt begrænsning: navnet slås op her og igen af httpx (DNS-rebinding mellem de to opslag er ikke udelukket)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx


def public_host(url: str) -> None:
    """Fejl (ValueError), medmindre adressen er http(s) og alle værtens IP-adresser er offentlige."""
    u = httpx.URL(url)
    if u.scheme not in ("http", "https") or not u.host:
        raise ValueError("Kun http(s)-adresser")
    for info in socket.getaddrinfo(u.host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"Adressen {u.host} er ikke offentlig")


async def check_request(request: httpx.Request) -> None:
    """httpx-hook (også ved hver omdirigering). DNS-opslaget blokerer, så det køres uden for event-loopet."""
    await asyncio.to_thread(public_host, str(request.url))
