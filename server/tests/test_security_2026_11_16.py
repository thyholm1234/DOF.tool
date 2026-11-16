"""Sikkerhedsregressioner fra gennemgangen 2026-11-16 (non-root, hemmelige filer, SSRF-forbindelsen).

* SSRF: egress-klientens netværkslag forbinder kun til den IP, der blev tjekket (ingen DNS-rebinding mellem tjek og forbindelse),
  og IPv4 gemt i IPv6 (::ffff:, 6to4, NAT64) afvises som IPv4-adressen.
* Filer fra hemmelige feltmeldinger (/media/obs/<navn>) ses kun af indtasteren.
Kører uden database og Valkey.
"""

from __future__ import annotations

import inspect
import ipaddress
import json
import socket
from types import SimpleNamespace

import httpcore
import httpx
import pytest

from portal.services import egress, reports


def _infos(*ips: str) -> list:
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.1.8", "169.254.169.254", "::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
                                "2002:7f00:1::", "64:ff9b::a00:1", "fe80::1", "100.64.0.1", "0.0.0.0"])
def test_ikke_offentlige_adresser_afvises(ip):
    assert egress._blocked(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["93.184.216.34", "2606:4700::1111", "::ffff:93.184.216.34"])
def test_offentlige_adresser_tillades(ip):
    assert not egress._blocked(ipaddress.ip_address(ip))


class _Recorder(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.hosts: list[str] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.hosts.append(host)
        raise httpcore.ConnectError("testen åbner ingen forbindelser")


async def test_backend_forbinder_til_den_tjekkede_ip(monkeypatch):
    async def fake_lookup(host, port, **kw):
        return _infos("93.184.216.34")

    monkeypatch.setattr(egress.anyio, "getaddrinfo", fake_lookup)
    rec = _Recorder()
    with pytest.raises(httpcore.ConnectError):
        await egress.GuardedBackend(rec).connect_tcp("eksempel.dk", 443)
    assert rec.hosts == ["93.184.216.34"]  # IP'en – ikke navnet, som ellers ville blive slået op igen


async def test_backend_afviser_intern_ip_uden_at_forbinde(monkeypatch):
    async def fake_lookup(host, port, **kw):
        return _infos("93.184.216.34", "127.0.0.1")  # ét internt svar er nok til at afvise

    monkeypatch.setattr(egress.anyio, "getaddrinfo", fake_lookup)
    rec = _Recorder()
    with pytest.raises(httpcore.ConnectError, match="ikke offentlig"):
        await egress.GuardedBackend(rec).connect_tcp("rebind.eksempel.dk", 443)
    assert rec.hosts == []


async def test_dns_rebinding_stoppes_ved_forbindelsen(monkeypatch):
    """Hooket ser en offentlig adresse; navnet peger på 127.0.0.1, når forbindelsen åbnes. Netværkslaget stopper den."""
    monkeypatch.setattr(egress, "public_host", lambda url: None)  # første opslag: «offentlig»

    async def rebound(host, port, **kw):  # andet opslag: intern
        return _infos("127.0.0.1")

    monkeypatch.setattr(egress.anyio, "getaddrinfo", rebound)
    async with egress.client(timeout=2) as client:
        with pytest.raises(httpx.ConnectError, match="ikke offentlig"):
            await client.get("http://rebind.eksempel.dk/")


def test_ukendt_vaert_giver_valueerror(monkeypatch):
    def fail(*a, **kw):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(egress.socket, "getaddrinfo", fail)
    with pytest.raises(ValueError):
        egress.public_host("https://findes-ikke.invalid/")


def test_klienten_bruger_ikke_miljoets_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://10.0.0.1:3128")
    c = egress.client()
    assert isinstance(c._transport, egress.GuardedTransport) and not c._mounts


def test_alle_brugerstyrede_hentninger_bruger_egress_klienten():
    from portal.services import article_import, netfugl, snak_links

    for mod in (article_import, netfugl, snak_links):
        src = inspect.getsource(mod)
        assert "egress.client(" in src and "httpx.AsyncClient(" not in src, mod.__name__


# ---------------------------------------------------------------- hemmelige meldingers filer


class _Session:
    def __init__(self, row):
        self.row, self.params = row, None

    async def execute(self, stmt, params):
        self.params = params
        return SimpleNamespace(first=lambda: self.row)


URL = "/media/obs/" + "a" * 32 + ".webp"
OWNER, OTHER = SimpleNamespace(id=1), SimpleNamespace(id=2)


async def test_fil_fra_almindelig_melding_ses_af_alle_indloggede():
    s = _Session(SimpleNamespace(user_id=1, hemmelig=False))
    assert await reports.media_visible(s, URL, OTHER)
    assert json.loads(s.params["m"]) == [{"url": URL}]


async def test_fil_fra_hemmelig_melding_kun_for_indtasteren():
    row = SimpleNamespace(user_id=1, hemmelig=True)
    assert await reports.media_visible(_Session(row), URL, OWNER)
    assert not await reports.media_visible(_Session(row), URL, OTHER)
    assert not await reports.media_visible(_Session(row), URL, None)


async def test_fil_uden_melding_vises_ikke():
    assert not await reports.media_visible(_Session(None), URL, OWNER)


def test_obs_ruten_tjekker_adgang():
    from portal.api import media

    assert "reports.media_visible" in inspect.getsource(media.obs_media)
