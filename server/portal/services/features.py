"""Funktioner, som superadmin kan slå til og fra (Admin → Funktioner).

Hver funktion har tre tilstande:
* ``off``     – slået fra for alle (også API'et svarer 404)
* ``preview`` – kun synlig for superadmin og brugere med en DOFbasen-rolle (forhåndsvisning)
* ``on``      – slået til for alle indloggede

En underfunktion (fx ``dofbasen_api``) virker kun, hvis forælderen (``dofbasen``) også er synlig for brugeren.
Tilstanden ligger i tabellen settings (nøglen ``features``) og caches i processen; en ændring tæller en version op i
Valkey, så alle processer henter den igen ved næste kald.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.models import Setting

STATES = ("off", "preview", "on")
# nøgle: (navn, beskrivelse, forælder, standard)
FEATURES: dict[str, tuple[str, str, str | None, str]] = {
    "position": ("Position og 'Nær mig' (app)", "Appen sender brugerens position i baggrunden, og feed og alarmer kan filtreres på en radius "
                 "om, hvor man er (Nær mig). Brugeren kan slå positionsdeling fra og slette sin position.", None, "on"),
    "voice": ("Tale-indtastning på ture (app, eksperiment)", "I Android-appen kan man holde en knap inde på turen og sige fx "
              "«2 bomlærker syd»; talen genkendes på dansk af telefonen og lægges ind som observationer (antal, art, adfærd). "
              "Kræver en APK bygget med talegenkendelse.", None, "preview"),
    "trip_weather": ("Vejret fra DMI på ture", "Når en ny tur får lokalitet, dato og starttid, udfyldes de tomme vejrfelter "
                     "(temperatur, skydække, vind, nedbør, sigtbarhed) med DMI's målinger fra de nærmeste stationer i turens "
                     "tidsrum. Felterne kan rettes bagefter, og vejret kan hentes igen under fanen Vejret.", None, "preview"),
    "dku": ("DKU: tip om tvivlsomme observationer", "Brugere kan tippe DKU om observationer, der mangler dokumentation eller "
            "virker forkerte. DKU-ansvarlige pr. lokalafdeling behandler sagen, kan inddrage observatøren og skrive frem og "
            "tilbage. Forhåndsvisning ses af superadmin, DKU-ansvarlige og dem med admin-modulet DKU.", None, "preview"),
    "search": ("Søg i observationer", "Søgeside (menuen Statistik → Søg) med samme felter som DOFbasens søgning: resultatet "
               "listes efter dato og kan sorteres på alle kolonner, et kort viser alle fundene, og resultatet kan hentes som CSV, "
               "ODS eller XLSX.", None, "preview"),
    "statistik": ("Statistik-modulet", "Menupunktet Statistik → Statistik med fanerne Arter pr. år (flyttes hertil fra Lister) "
                  "og NFL: nye fuglearter for landet pr. år siden 1990 med graf og artsliste. Når modulet ikke er synligt, "
                  "ligger Arter pr. år fortsat under Lister.", None, "preview"),
    "dofbasen": ("DOFbasen (showcase)", "Hele modulet: en ny udgave af dofbasen.dk med forside, observationer, lister, fænologi, "
                 "tekstsider og eget admin-panel. Menupunktet DOFbasen vises kun, når modulet er synligt for brugeren.", None, "preview"),
    "dofbasen_search": ("DOFbasen: søgning", "Søgesiden med alle filtre, kort og CSV-udtræk.", "dofbasen", "on"),
    "dofbasen_news": ("DOFbasen: nyheder og fokus", "Nyheder (Nyt på DOFbasen) og fokus-bokse på forsiden.", "dofbasen", "on"),
    "dofbasen_edit": ("DOFbasen: redigering på siderne", "Redigér-knapper direkte på tekstsider, lister og forsidebokse for dem med en "
                      "DOFbasen-rolle. Admin-panelet virker uanset denne indstilling.", "dofbasen", "on"),
    "dofbasen_dku": ("DOFbasen: kvalitetssikring (DKU)", "DKU-rollen kan sætte observationer til behandling/godkendt (blå pil).", "dofbasen", "on"),
    "dofbasen_api": ("DOFbasen: API og tokens", "Offentligt REST-API (/api/dofbasen/v1) med personlige tokens. Admin tildeler adgang; "
                     "brugeren laver selv tokens på sin profil.", "dofbasen", "preview"),
    "netfugl": ("Netfugl (showcase)", "Hele modulet: en ny udgave af netfugl.dk med forside, nyheder og artikler, krydslister, "
                "artslister, fotogalleri, RSS, vejr, brugere og eget admin-panel. Menupunktet Netfugl vises kun, når modulet er synligt.",
                None, "preview"),
    "netfugl_kryds": ("Netfugl: krydslister", "Krydslister beregnet automatisk fra brugernes DOFbasen-observationer og suppleret "
                      "med manuelle kryds (VP, ringmærkning, ædelarter).", "netfugl", "on"),
    "netfugl_fotos": ("Netfugl: fotogalleri", "Upload af fotos (kan knyttes til en observation i DOFbasen), likes og kommentarer.",
                      "netfugl", "on"),
    "netfugl_artikler": ("Netfugl: nyheder, artikler og kommentarer", "Artikler, kort nyt og kommentarer.", "netfugl", "on"),
    "netfugl_rss": ("Netfugl: RSS (Netfugl live)", "Workeren henter RSS fra danske og udenlandske kilder hver time.", "netfugl", "on"),
    "netfugl_edit": ("Netfugl: redigering på siderne", "Redigér-knapper direkte på tekstsider og lister for dem med en Netfugl-rolle.",
                     "netfugl", "on"),
    "netfugl_dofbasen": ("Netfugl ↔ DOFbasen", "Netfugl-fotos vises på observationer i DOFbasen-modulet, og DOFbasen-fund linker "
                         "til Netfugl. Kræver at begge moduler er synlige.", "netfugl", "on"),
    "netfugl_api": ("Netfugl: API og tokens", "REST-API (/api/netfugl/v1) med personlige tokens: artikler, krydslister, fotos, "
                    "RSS og artslister.", "netfugl", "preview"),
}
KEY = "features"
VERSION = "features:version"
_cache: dict = {"ver": None, "value": {}}


def defaults() -> dict[str, str]:
    return {k: v[3] for k, v in FEATURES.items()}


async def load(session: AsyncSession) -> dict[str, str]:
    """Funktionernes tilstand (cachet i processen; ét Valkey-opslag pr. kald)."""
    ver = await kv().get(VERSION) or "0"
    if _cache["ver"] != ver:
        row = await session.get(Setting, KEY)
        stored = row.value if row is not None and isinstance(row.value, dict) else {}
        _cache["value"] = {k: stored.get(k) if stored.get(k) in STATES else d for k, d in defaults().items()}
        _cache["ver"] = ver
    return dict(_cache["value"])


async def save(session: AsyncSession, changes: dict[str, str]) -> dict[str, str]:
    for k, v in changes.items():
        if k not in FEATURES:
            raise ValueError(f"Ukendt funktion {k}")
        if v not in STATES:
            raise ValueError(f"Ugyldig tilstand {v}")
    current = await load(session)
    current.update(changes)
    row = await session.get(Setting, KEY)
    if row is None:
        session.add(Setting(key=KEY, value=current))
    else:
        row.value = current
    await session.commit()
    await kv().incr(VERSION)
    _cache["ver"] = None
    return current


def visible(flags: dict[str, str], key: str, *, superadmin: bool, insider: bool) -> bool:
    """Er funktionen synlig for brugeren? insider = har en DOFbasen-rolle (ser forhåndsvisninger)."""
    state = flags.get(key, "off")
    if state == "off":
        return False
    if state == "preview" and not (superadmin or insider):
        return False
    parent = FEATURES[key][2]
    return parent is None or visible(flags, parent, superadmin=superadmin, insider=insider)
