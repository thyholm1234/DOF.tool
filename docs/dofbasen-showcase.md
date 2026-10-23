# DOFbasen – showcase af en ny dofbasen.dk

Modulet viser, hvordan dofbasen.dk kan se ud og virke på en moderne backend (FastAPI + PostgreSQL/PostGIS + Valkey)
og frontend (ES-moduler uden build-trin), i stedet for den nuværende PHP 5.3-løsning. Det bygger på portalens egne data
(~30 mio. observationer fra DOFbasen) og ligger under `#/dofbasen`.

> **Ophavsret.** Tekster, lister og links er kopieret fra dofbasen.dk den 23. oktober 2026 *som eksempel* på, hvordan
> indholdet kan forvaltes i den nye løsning. Indholdet tilhører Dansk Ornitologisk Forening. Kontaktoplysninger
> (adresser, telefonnumre, mailformularer) er ikke kopieret; fotos fra DOFbasen hentes ikke, og DOF's logo/grafik er
> erstattet af en enkel tegning.

## Slå til og fra

Alt nyt styres af superadmin under **Admin → Funktioner** (også fra modulets eget admin-panel). Hver funktion har tre
tilstande: *Fra* (slået fra for alle, API'et svarer 404), *Forhåndsvisning* (kun superadmin og brugere med en
DOFbasen-rolle) og *Til* (alle indloggede). En underfunktion virker kun, når modulet selv er synligt.

| Funktion | Nøgle | Standard |
|---|---|---|
| DOFbasen-modulet (menupunkt, alle sider, admin-panel) | `dofbasen` | forhåndsvisning |
| Søgning (søgeside, kort, CSV) | `dofbasen_search` | til |
| Nyheder og fokus | `dofbasen_news` | til |
| Redigering direkte på siderne | `dofbasen_edit` | til |
| Kvalitetssikring (DKU-markeringer) | `dofbasen_dku` | til |
| API og tokens | `dofbasen_api` | forhåndsvisning |

Tilstanden ligger i `settings` (nøglen `features`) og caches pr. proces med en versionsnøgle i Valkey
(`services/features.py`). Menupunktet *DOFbasen* (under Værktøjer) vises kun, når modulet er synligt for brugeren.

## Sider (original → showcase)

| dofbasen.dk | Showcase | Bemærkning |
|---|---|---|
| `/` | `#/dofbasen` | Kort i dag/i går (opdateres hvert 5. min.), obs. pr. dag 20 dage, fordeling pr. lokalafdeling, statistik/fænologi/afvigelser, Om DOFbasen (live tal), Fokus, Nyt på DOFbasen, Sjældne fugle (seneste / samme dato tidligere år), fotos, Danmarks fugle |
| `/observationer/` | `#/dofbasen/observationer` | visning, dato (dd-mm-åååå), sortering art/lokalitet, summering tur/dato/ingen, afdeling, forrige/næste dag, tegnforklaring, DKU-pile |
| `/search/`, `search1.php`, `result.php` | `#/dofbasen/soeg` | alle filtre fra formularen; tabel, kort og CSV (DOFbasens kolonnenavne); gamle link-parametre oversættes |
| `popobs.php`, `poplok.php`, `popobser.php` | popup eller `#/dofbasen/obs/…`, `lok/…`, `obser/…` | observatør uden adresse/telefon |
| `/faeno/art.php`, `firstobs`, `samedate`, `art_kort` | `#/dofbasen/faeno/…` | 10-dagesprofil med DOFbasens farvekoder, første iagttagelse 10 år, kort |
| `/opslag` og alle opslagslister | `#/dofbasen/opslag/…` | redigerbare lister (SU, SUB, følsomme, fænologi, adfærd, alder, dragt, vindstyrke); levende lister fra databasen (arter, observatører, lokaliteter, kort, bemærk-tærskler) |
| `storlokalitet.php`, `ibalokalitet.php` | `#/dofbasen/opslag/omraader/stor|iba` | 32 storlokaliteter og 162 IBA-områder med lokalitetsnumre; bruges også i søgningen |
| `start.php`, `rights.php`, `kvalitet`, `help.php`, `viden`, `kontakt`, `features.php`, `english`, `visningstyper*.php`, `opretmig.php`, `reminder.php` | `#/dofbasen/side/<adresse>` | redigerbare tekstsider; hjælp med indholdsfortegnelse |
| `/news/index.php` | `#/dofbasen/nyheder` | |
| Menuen *Links*, *Støt DOF*, *Statistik* | redigerbare lister `links`, `stoet`, `statistik` | |
| *Bruger* | `#/dofbasen/bruger`, `#/dofbasen/api` | login sker med portalens DOFbasen-login |
| Atlas III, punkttællinger, fotogalleri | infoside med link til originalen / portalens egne fotos | selvstændige apps hos DOF |

Ikke i portalens data (vises som deaktiveret eller med en note): andre dyr end fugle, ynglepar/bearbejdninger,
IBA-tællinger, bilag 1/rødliste og DOFbasens fotos/lyd.

## Roller og redigering

DOFbasen-modulet har egne roller (`dofb_roles`, `dofb_user_roles`), adskilt fra portalens roller. Rettigheder:
`sider` (tekstsider og forsidebokse), `nyheder` (nyheder og fokus), `lister` (opslagslister, links, områder), `dku`
(kvalitetssikring), `api` (API-adgang) og `roller`. Standardroller: DOFbasen-administrator (alle), Redaktør, Listeansvarlig,
DKU-medlem, API-ansvarlig. Superadmin og brugere med portal-modulet *DOFbasen* har alle rettigheder.

Med en rolle vises *Redigér*-knapper direkte på siderne (tekstsider, Om-boksen, fokus, nyheder, lister, koordinatorer,
områder), og DKU kan sætte en observation *til behandling* (blå pil), *behandlet af DKU* eller *godkendt af SU*.
Admin-panelet (`#/dofbasen/admin`) samler oversigt med ændringslog, sider, nyheder, fokus, lister, områder, roller,
API-adgang og funktioner. Teksterne bruger let markup: tom linje = afsnit, `• ` = punkt, `| a | b |` = tabel,
`[tekst](adresse)`, `**fed**`, `_kursiv_`; links til dofbasen.dk vises som modulets egne sider.

## API v1

`/api/dofbasen/v1` – kun GET, JSON, åben CORS, `Authorization: Bearer dofb_…`.

1. En bruger med rettigheden *api* giver adgang (kald pr. minut, antal nøgler, note).
2. Brugeren laver selv nøgler på sin profil eller under DOFbasen → Bruger → API-nøgler. Nøglen vises én gang; kun en
   SHA-256 gemmes. Fjernes adgangen, spærres alle brugerens nøgler.
3. Grænsen håndhæves pr. nøgle pr. minut i Valkey (429 + `Retry-After`). Brug og senest brugt vises i admin-panelet.

Endpoints: `/me`, `/observations` (alle søgefiltre, højst 500 pr. side), `/observations/{obsid}`, `/days/{dato}`, `/rare`,
`/stats/daily`, `/species`, `/species/{artnr}`, `/locations`, `/locations/{loknr}`, `/areas`, `/news`, `/lists`,
`/lists/{key}`. Hemmelige observationer udleveres kun til observatøren selv. Beskrivelse i OpenAPI under `/api/docs`.

## Teknik

* Backend: `api/dofbasen.py` (modul og admin), `api/dofbasen_v1.py` (token-API), `services/dofbasen.py` (seed, roller,
  søgning), `services/features.py` (funktioner). Tabeller `dofb_*` oprettes og fyldes ved opstart (eksisterende rækker
  røres ikke).
* Søgningen bygger én parametriseret SQL over `observations` med dækkende indekser, højst ti år uden art/lokalitet/
  observatør, `statement_timeout` 25 s og uden parallelle scanninger (Postgres-containerens `/dev/shm` er kun 64 MB).
* Forsidens tal caches 5 minutter i Valkey ligesom på dofbasen.dk; fænologi og statistik længere.
* Frontend: `web/js/views/dofbasen.js` + `web/js/dofbasen/*.js`, CSS i `web/css/dofbasen.css` (alt afgrænset til `.dofb`).
  Portalens topbar og menuer skjules, mens modulet vises.
* Indholdet genskabes med `tools/dofbasen_seed/` (crawl med Playwright, konvertering til markup, storlokaliteter/IBA).
