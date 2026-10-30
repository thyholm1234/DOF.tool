# Arkitektur og modulregler

Kort guide til, hvor koden ligger, og hvor ny kode skal hen. Formålet er, at hvert modul kan forstås, ændres og testes
for sig.

## Backend (`server/portal/`)

| Lag | Mappe | Ansvar | Må importere |
|---|---|---|---|
| Domæne | `domain/` | Rene regler uden I/O: kategorier (`kategori.py`), adfærd/træk (`adfaerd.py`), afstand/retning (`geo.py`) | kun standardbiblioteket |
| Model | `models/` | Tabeller, ét modul pr. domæne; `models/ddl.py` = ekstra DDL. `from portal.models import X` virker for alle | `db` |
| Services | `services/` | Forretningslogik og SQL: feed, fund, lister, fænologi, alarmer, … Ét modul pr. emne | `domain`, `models`, `cache`, andre services |
| Integration | `dof/` | DOFbasen: gateway/kø, hentning, parsing, arkiv, referencedata | `domain`, `models`, `services` |
| API | `api/` | HTTP: validering (Pydantic), adgang (`api/deps.py`) og kald til services. Store paneler er pakker (`api/admin/`) | alt ovenfor |
| Drift | `worker.py`, `bootstrap.py`, `db*.py`, `cache.py` | Baggrundsjob og planlagte opgaver, opstart, databasegrupper og routing, Valkey | – |

**Regler**

- **Domæneregler skrives én gang.** Kategoriernes rang, trækkoder og afstand hentes fra `portal.domain`, også når de
  skal bruges i SQL (`kategori.rank_sql()`, `adfaerd.first_migration_sql()`, `adfaerd.last_migration_sql()`).
  - *Første led er træk* (`FIRST_MIGRATION`, inkl. TF) bruges til at tælle trækkende på trækstedet (fænologi, trækkort, kalendere).
  - *Sidste led er træk* (`MIGRATING`, ikke TF) bruges til "er fuglen videre?" (fund-estimat, lokale hits).
- **Indstillinger** i tabellen `settings` læses og skrives med `services/setting_store.py`
  (`get_setting`, `get_setting_dict`, `put_setting`, `setting_exists`).
- **Databasegrupper:** en SQL-sætning må kun røre én gruppe (brugere *eller* observationer; referencedata findes i
  begge). Se `dbgroups.py`; `tests/test_dbrouter.py` holder øje.
- **Nye moduler** ligger bag et funktionsflag (`services/features.py`, Admin → Funktioner): off/preview/on. API'et
  svarer 404, når flaget ikke er synligt for brugeren, og `/api/me` fortæller frontenden, om menupunktet skal vises.
- **Planlagte opgaver:** tilføj opgaven i `services/schedule.TASKS` *og* i `worker.SCHEDULED` (evt. med flag i
  `worker.FEATURE_OF`). `tests/test_schedule.py` fejler, hvis de to ikke passer sammen.
- **API'ets ruter** er låst af `tests/test_routes.py` (snapshot i `tests/fixtures/api_routes.json`). Ændres en rute med
  vilje: `UPDATE_ROUTES=1 python -m pytest tests/test_routes.py`.
- Svar komprimeres med GZip (`SelectiveGZip` i `main.py`), undtagen SSE, medier, lyd, downloads og Range-forespørgsler.

## Frontend (`web/js/`)

| Del | Fil(er) | Ansvar |
|---|---|---|
| Kerne | `core.js` | `api()`, `h()`, formatering, `KAT`/`KAT_RANK`, `seg`, `modal`, `store`, … |
| Router og menu | `app.js` | Ruter → visninger (lazy `import()`), sidepanel, bundmenu, brugermenu |
| Visninger | `views/<side>.js` | Én side pr. fil med `export async function render(view)`; returnerer evt. en oprydningsfunktion |
| Faner i store sider | `views/admin/<fane>.js`, `views/kort-*.js` | Hentes først, når fanen åbnes |
| Kort | `maps.js` | Grundkort og hjælpere. Henter Leaflet første gang, et modul importerer filen (top-level `await`) – importér altid `maps.js`, før `L` bruges |
| Live | `live.js` | SSE-strømmen (`live.on("feed", …)`) |
| Showcases | `dofbasen/`, `netfugl/` | Selvstændige moduler med egen skal, CSS og admin |
| Felt | `tur-*.js`, `offline.js`, `voice*.js` | Indtastning, offline-kø og tale |

**Regler**

- En ny side er en ny fil i `views/` plus en linje i `ROUTES` (og evt. `SECTIONS`) i `app.js`.
- En side med mange uafhængige faner deles i en mappe med én fil pr. fane, som skallen henter med `import()`.
- Brug `KAT_RANK`/`KAT` fra `core.js` – skriv ikke rangordenen igen.

## Test

- `server/tests/` (pytest): domæneregler, parsing, gateway, databasegrupper, rutetabel, planlagte opgaver, komprimering,
  fund-estimat, settings-lager m.m. Kør `python -m pytest` i `server/` (dev-afhængigheder: `pip install -e '.[dev]'`).
- `tools/e2e/` (Playwright): røgtest af alle sider og faner i en browser og sammenligning af to kørsler – se README.
