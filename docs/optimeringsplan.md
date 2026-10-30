# Kodegennemgang og optimeringsplan (oktober 2026)

Grundlag: README og `docs/` (funktioner, drift, showcases) i stedet for en separat plan/specifikation; projektets regler:
nye moduler bag funktionsflag (Admin → Funktioner), open source frem for Google, og brugere/observationer i hver sin
databasegruppe (en SQL-sætning må aldrig blande grupperne).

Udgangspunkt før ændringerne: 104 pytest-tests grønne; røgtest i browser (69 sider og faner) uden JS-fejl og uden
mislykkede API-kald; alle API-kald under 250 ms.

<analysis>

**1. Kodens organisering og struktur**

- *Godt:* klar lagdeling i `api/` (HTTP), `services/` (domæne), `dof/` (integration med DOFbasen), `db*` (routing mellem
  grupper); frontend med én visning pr. fil, der lazy-loades af routeren; showcase-modulerne (DOFbasen, Netfugl) er
  selvstændige under egne mapper og bag funktionsflag.
- *Monolitter:* `models.py` (1.641 linjer, 100+ tabeller fra 12 domæner i én fil), `api/admin.py` (1.002 linjer, otte
  paneler), `web/js/views/admin.js` (1.591 linjer – hele admin- og moderationssiden parses, selvom man kun åbner én fane).
- *Domæneregler spredt og dubleret:*
  - kategoriernes rangorden (SU > SUB > Bemærk) står som dict eller SQL-`CASE` 7 steder (feed, reports, pulse, locyear,
    api/geo, kort-live.js, map.js) – og reports har sin egen `LEVELS` uden `pheno`;
  - trækkende adfærd har to definitioner, der begge er kopieret: «første led i T/TF/N/…» (dof/parse, feed, phenology,
    calendars – fire kopier af samme liste) og «sidste led i retning/T/TU/OF» (finds, locfinds);
  - afstand (haversine) er implementeret to gange (finds, dmi).
- *Gentaget infrastruktur:* «læs/skriv en række i `settings`» er skrevet i hånden ~30 steder i 19 filer.
- *Worker:* `run_scheduled` er en lang `if/elif`-kæde; en ny planlagt opgave kræver ændringer tre steder.

**2. Kodekvalitet og god praksis**

- Typede Python-signaturer, ruff-ren kode (bortset fra ældre advarsler), gode docstrings på dansk – bevares.
- Fejl i baggrundsjob fanges og logges via `run_safely`; API-fejl som `HTTPException` – konsekvent.
- En visning (admin.js) importerer `app.js` (cirkulær afhængighed); acceptabelt, men skal ikke spredes.
- Tests dækker parsing, gateway, router, skemaer m.m., men ikke domænereglerne (kategori, adfærd) og ikke at API'ets
  ruter forbliver de samme ved omstrukturering. Der er ingen automatisk test af frontend.
- Containerbillederne var ældre end `pyproject.toml` (manglede `asyncssh`, `openpyxl`) – drift, ikke kode.

**3. UI/UX og ydelse**

- Ingen komprimering i appen: dagskortet (251 KB), feedet (230 KB) og artsindekset (172 KB) sendes ukomprimeret, når
  der ikke står en proxy foran (appen, LAN, udvikling). JSON komprimerer typisk 8–10×.
- Leaflet (148 KB JS + CSS) indlæses blokerende ved hver opstart, også på forsiden, der ikke har et kort.
- Admin/moderation: hele 1.591-linjers modulet hentes for én fane.
- Ellers konsistent design (fælles `core.js`-hjælpere, `seg`, `chip`, `card`), tilgængelighed med `aria-label` på
  ikonknapper, lazy-loadede visninger og versionerede, uforanderligt cachede app-filer.

</analysis>

# Optimeringsplan

## Struktur og organisering

- [x] Trin 1: Fælles domænemodul `portal/domain/`
  - **Opgave**: Saml kategoriernes rangorden (`kategori.py`), adfærdskoder og træk (`adfaerd.py`) og afstand/retning
    (`geo.py`) ét sted og brug dem overalt. Ingen ændring af semantik: de to trækdefinitioner bevares under hvert sit navn.
  - **Filer**: `domain/__init__.py`, `domain/kategori.py`, `domain/adfaerd.py`, `domain/geo.py`; brugere: `services/feed.py`,
    `services/reports.py`, `services/pulse.py`, `services/locyear.py`, `services/finds.py`, `services/locfinds.py`,
    `services/calendars.py`, `services/phenology.py`, `services/dmi.py`, `services/daymap.py`, `dof/parse.py`, `api/geo.py`
  - **Afhængigheder**: ingen
  - **Succes**: ingen kopier af listerne tilbage (grep), tests grønne, feed/fænologi giver samme svar.
- [x] Trin 2: Fælles lager for `settings`-rækker
  - **Opgave**: `services/setting_store.py` med `get_setting` / `put_setting`; erstat de håndskrevne kopier.
  - **Filer**: ny fil + de services, der har kopien (schedule, backup, datasync, locfinds, features, …)
  - **Afhængigheder**: ingen
  - **Succes**: samme værdier læses/skrives; tests grønne.
- [x] Trin 3: `models.py` → pakken `portal/models/` pr. domæne
  - **Opgave**: Del i `reference`, `observations`, `aggregates`, `users`, `field`, `learning`, `social`, `datastatus`,
    `events`, `dofbasen`, `netfugl`, `dku` og `ddl`; `portal/models/__init__.py` re-eksporterer alt, så
    `from portal.models import X` virker uændret.
  - **Afhængigheder**: ingen
  - **Succes**: samme tabelsæt i `Base.metadata` (test), samme `EXTRA_DDL`.
- [x] Trin 4: `api/admin.py` → pakken `portal/api/admin/` pr. panel
  - **Opgave**: `drift`, `backup`, `brugere`, `moderation`, `indhold`, `data`, `indstillinger`; `__init__` samler én router.
  - **Afhængigheder**: ingen
  - **Succes**: identisk rutetabel (snapshot-test af alle ruter, metoder og navne).
- [x] Trin 5: Planlagte opgaver som register i workeren
  - **Opgave**: `run_scheduled` slår op i et register (navn → funktion, evt. funktionsflag) i stedet for `if/elif`.
  - **Filer**: `worker.py`
  - **Succes**: alle navne i `schedule.TASKS` har en handler (test).

## Ydelse

- [x] Trin 6: Komprimering af API og app-filer
  - **Opgave**: GZip for JSON, HTML, JS og CSS (ikke SSE-strømmen, medier, lyd eller Range-forespørgsler).
  - **Filer**: `main.py`
  - **Succes**: `Content-Encoding: gzip` på `/api/daymap` og `/v/…/app.js`; `/api/stream` og `/media/` uberørt.
- [x] Trin 7: Leaflet indlæses først, når et kort skal vises
  - **Opgave**: `maps.js` henter Leaflet (JS + CSS) med top-level `await`; moduler, der bruger `L`, importerer
    `maps.js`. Fjern Leaflet fra `index.html` (service workeren har den stadig i sin offline-skal).
  - **Filer**: `web/index.html`, `web/js/maps.js`, `web/js/dofbasen/*.js` (dem der bruger `L`), `web/js/wind.js`
  - **Succes**: forsiden indlæser ikke `leaflet.js`; alle kortsider viser stadig kort (røgtest).
- [x] Trin 8: Admin/moderation delt i faner, der lazy-loades
  - **Opgave**: `views/admin.js` bliver en skal; fanerne i `views/admin/*.js` hentes, når de åbnes.
  - **Succes**: alle admin- og moderationsfaner i røgtesten uden fejl og med samme indhold.
- [x] Trin 9: Fælles kategorikonstanter i frontend
  - **Opgave**: `KAT_RANK` i `core.js`; brug den i `kort-live.js`, `map.js`, `kort-dag.js`.

## Test

- [x] Trin 10: Tests af domæneregler, rutetabel, registre og røgtest
  - **Opgave**: pytest for `domain/*`, `setting_store`, rutesnapshot og worker-registret; røgtesten i browser
    (`tools/e2e/smoke.py`) lægges i repoet med vejledning.
  - **Succes**: alle tests grønne; røgtesten uden JS-fejl, mislykkede kald eller ændret sidestruktur i forhold til
    udgangspunktet.

## Dokumentation

- [x] Trin 11: `docs/arkitektur.md` – moduloversigt og regler for, hvor ny kode skal ligge.

## Næste logiske skridt (ikke gennemført her)

- [x] Flyt den resterende SQL fra `api/dku.py`, `api/geo.py`, `api/netfugl.py` og `api/calendar.py` ned i services, så
  api-laget kun validerer og kalder services (gennemført; låst af `tests/test_architecture.py`).
- Fjern de syv ældre krydsimporter mellem API-moduler (listen `KNOWN_CROSS_IMPORTS` i `tests/test_architecture.py`) –
  især `services/auth.py` → `api/social.py`, hvor servicelaget afhænger af API-laget. Flyt også SQL'en i de øvrige
  API-moduler (fx `api/social.py`, `api/categories.py`, `api/refdata.py`, `api/netfugl_v1.py`).
- Del `app.css` (932 linjer) pr. modul og indlæs DOFbasen/Netfugl-CSS kun i deres moduler (sker allerede for de to).
- Genbyg containerbillederne, så de følger `pyproject.toml` (se driftsnoten i opsummeringen).
