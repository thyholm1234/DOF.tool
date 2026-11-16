"""Udgående HTTP til adresser, som brugere eller indhold bestemmer (RSS-kilder, billeder i importerede artikler, link-forhåndsvisninger).

SIKKERHED (SSRF): sådanne kald må aldrig ramme portalens egne tjenester (postgres, valkey, rustfs, dbadmin, api) eller
andre interne adresser på værten/LAN'et. Brug altid klienten herfra:

    async with egress.client(timeout=10, headers=...) as client:
        await client.get(url)

To lag:
* ``check_request`` (httpx-hook, også ved hver omdirigering): kun http(s), og navnet skal pege på offentlige adresser –
  giver en tidlig, læselig fejl.
* ``GuardedBackend`` (transportens netværkslag): selve forbindelsen. Navnet slås op én gang, alle adresser tjekkes, og der
  forbindes til netop den tjekkede IP. Der er derfor intet andet opslag, en DNS-rebinding kan nå at ændre svaret i (den
  klassiske «tjek → nyt opslag → forbind»-race). TLS bruger stadig værtsnavnet (SNI og certifikattjek i httpcore).
Miljøets proxy (HTTP(S)_PROXY) bruges ikke af klienten – den ville flytte forbindelsen uden om tjekket."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import typing

import anyio
import httpcore
import httpx


NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Ikke-offentlig adresse? IPv4 indlejret i IPv6 (::ffff:127.0.0.1, 64:ff9b::/96 m.fl.) vurderes som IPv4-adressen."""
    if isinstance(ip, ipaddress.IPv6Address):
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is None and ip in NAT64:
            inner = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if inner is not None and not inner.is_global:
            return True
    return not ip.is_global


def _check_ips(host: str, infos: list) -> list[str]:
    ips = [info[4][0] for info in infos]
    if not ips:
        raise ValueError(f"Adressen {host} kan ikke slås op")
    for ip in ips:
        if _blocked(ipaddress.ip_address(ip.split("%", 1)[0])):
            raise ValueError(f"Adressen {host} er ikke offentlig")
    return list(dict.fromkeys(ips))


def public_host(url: str) -> None:
    """Fejl (ValueError), medmindre adressen er http(s) og alle værtens IP-adresser er offentlige."""
    u = httpx.URL(url)
    if u.scheme not in ("http", "https") or not u.host:
        raise ValueError("Kun http(s)-adresser")
    try:
        infos = socket.getaddrinfo(u.host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError as exc:  # ukendt navn: samme fejltype som en intern adresse, så kalderne kun skal fange ValueError
        raise ValueError(f"Adressen {u.host} kan ikke slås op") from exc
    _check_ips(u.host, infos)


async def check_request(request: httpx.Request) -> None:
    """httpx-hook (også ved hver omdirigering). DNS-opslaget blokerer, så det køres uden for event-loopet."""
    await asyncio.to_thread(public_host, str(request.url))


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Netværkslag, der kun forbinder til offentlige IP-adresser – og kun til dem, opslaget her gav."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None):
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                          socket_options: typing.Iterable | None = None) -> httpcore.AsyncNetworkStream:
        try:
            infos = await anyio.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise httpcore.ConnectError(f"Adressen {host} kan ikke slås op") from exc
        try:
            ips = _check_ips(host, infos)
        except ValueError as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        last: Exception | None = None
        for ip in ips:  # samme rækkefølge som systemet foreslår; alle er tjekket ovenfor
            try:
                return await self._inner.connect_tcp(ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise last or httpcore.ConnectError(f"Kunne ikke forbinde til {host}")

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: typing.Iterable | None = None):
        raise httpcore.ConnectError("Unix-sokler er ikke tilladt")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """httpx' almindelige transport (TLS, HTTP/1.1, pulje), men med GuardedBackend som netværkslag."""

    def __init__(self, *, verify: bool = True, limits: httpx.Limits = httpx.Limits(), retries: int = 0):
        super().__init__(verify=verify, limits=limits, retries=retries)
        old = self._pool  # bygget af httpx med samme ssl-kontekst og grænser; kun netværkslaget skiftes
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=old._ssl_context, max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections, keepalive_expiry=limits.keepalive_expiry,
            retries=retries, network_backend=GuardedBackend())


def client(**kwargs) -> httpx.AsyncClient:
    """AsyncClient til adresser udefra: SSRF-værnet i både hook og transport. Omdirigeringer følges som standard (og tjekkes)."""
    hooks = kwargs.pop("event_hooks", {}) or {}
    kwargs.setdefault("follow_redirects", True)
    return httpx.AsyncClient(transport=GuardedTransport(), trust_env=False,
                             event_hooks={**hooks, "request": [check_request, *hooks.get("request", [])]}, **kwargs)
