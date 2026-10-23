# Netfugl – showcase af en ny netfugl.dk, integreret med DOFbasen

Modulet (`#/netfugl`) viser, hvordan netfugl.dk kan se ud på portalens backend – og især hvad der bliver muligt, når
Netfugl og DOFbasen deler data. Layoutet følger netfugl.dk (navigationsbjælke med undermenuer, banner med rød kant,
røde versaltitler med streg, tabeller med CSV/Print/Søg), men fotos, logoer og bannere er erstattet af enkle tegninger.

> **Ophavsret.** Tekstsider, ordforklaringer, krydsregler, DK- og VP-listen, fuglefamilier, links og RSS-kilder er kopieret
> fra netfugl.dk den 23. oktober 2026 *som eksempel*. Artikler er kun kopieret som titel, kort uddrag og link til
> originalen. Fotos, kommentarer og brugerprofiler fra netfugl.dk er ikke kopieret. Indholdet tilhører Netfugl/DOF.

## Hvor integrationen giver mening

| Netfugl i dag | I showcasen |
|---|---|
| Krydslister vedligeholdes i hånden af hver bruger | **DK-listen, årsarter, månedsarter og lokalarter (afdelinger og kommuner) beregnes automatisk** fra brugerens observationer i DOFbasen (første fund pr. art, kun krydsbare kategori A–C). Brugeren supplerer manuelt med arter fra før DOFbasen, VP-arter, ringmærkningsarter og ædelarter – og kan skjule et automatisk kryds, der bygger på en fejl |
| "Seneste sjældne og fåtallige fugle i DOFbasen" linker ud | Fundet åbnes i DOFbasen-modulet med hele turen |
| Fotos er løsrevet fra observationerne | Ved upload foreslås brugerens egne observationer af arten på datoen; fotoet knyttes til observationen og **vises på observationen i DOFbasen-modulet** og i DOFbasens "Seneste oploadede fotos" |
| DK-listens artssider | Fund pr. måned og pr. lokalafdeling og – for sjældne arter – **samtlige danske fund** direkte fra DOFbasen-data |
| Vejret (Windy-kort) | Portalens trækvejrsprognose fra DMI for de klassiske træksteder |
| Brugerprofil | Link til observatøren i DOFbasen; kryds og blockers pr. liste |

Integrationen er en selvstændig funktion (`netfugl_dofbasen`) og kræver, at begge moduler er synlige for brugeren.

## Funktioner (Admin → Funktioner, kun superadmin)

| Funktion | Nøgle | Standard |
|---|---|---|
| Netfugl-modulet | `netfugl` | forhåndsvisning |
| Krydslister | `netfugl_kryds` | til |
| Fotogalleri | `netfugl_fotos` | til |
| Nyheder, artikler og kommentarer | `netfugl_artikler` | til |
| RSS (Netfugl live) | `netfugl_rss` | til |
| Redigering på siderne | `netfugl_edit` | til |
| Netfugl ↔ DOFbasen | `netfugl_dofbasen` | til |
| API og tokens | `netfugl_api` | forhåndsvisning |

*Forhåndsvisning* = kun superadmin og brugere med en Netfugl-rolle. Når modulet er slået fra, kører workeren heller ikke
krydsberegning eller RSS-hentning.

## Sider

Forside · Nyheder & artikler (søgning på tekst, emneord, år og type; kommentarer) · RSS nyheder · Vejret ·
Krydslister: Om krydslister, Krydsliste, Seneste kryds, Hvem har set hvad?, Blockers, Blockers statistik, Årstophits for DK,
VP, Falsk VP, ringmærkning, ædelarter, ædelårsarter, årsarter, månedsarter og lokalarter; Alle krydslister; Krydsregler;
Mine kryds · Arter: World Bird List (fuglefamilier), DK listen (med kategorier), VP listen, artssider · Fotogalleri
(søgning, sortering, SU-filter, fotoside med likes, visninger og kommentarer, upload) · Om Netfugl, FAQ, Ordforklaringer,
Støt Netfugl · Alle brugere og profiler · Søg på Netfugl (arter, artikler, fotos, brugere, RSS).

## Roller

Egne roller (`nf_roles`), adskilt fra portalens og DOFbasens: Netfugl-administrator, Redaktør (artikler, sider, RSS),
Fotomoderator (fotos, kommentarer), Listeansvarlig (krydslister og artslister) og API-ansvarlig. Superadmin og portal-modulet
*Netfugl* har alle rettigheder. Admin-panelet (`#/netfugl/admin`): oversigt og indstillinger, artikler, sider og links,
fotomoderation (afventer/afvist/publiceret), kommentarer, RSS-kilder, roller, API-adgang og funktioner.

Indstillinger: deltagere i krydslisterne (*portalens brugere, der viser deres lister* – standard – eller *demo: også de mest
aktive DOFbasen-observatører*), antal år med årsartslister og om nye fotos skal godkendes.

## API v1

`/api/netfugl/v1` – kun GET, JSON, åben CORS, `Authorization: Bearer nf_…`. En bruger med rettigheden *api* giver adgang;
brugeren laver selv nøgler på profilen eller under Netfugl → API-nøgler. Endpoints: `/me`, `/articles`, `/articles/{id}`,
`/photos`, `/rss`, `/lists`, `/lists/{key}/ranking`, `/lists/{key}/latest`, `/lists/{key}/blockers`, `/species/{dk|vp}`,
`/users/{obserkode}`.

## Teknik

* Backend: `api/netfugl.py`, `api/netfugl_v1.py`, `services/netfugl.py`; tabeller `nf_*`, fyldt fra `seed/netfugl/content.json`
  (bygget med `tools/dofbasen_seed/netfugl_build_seed.py`).
* Workeren beregner `nf_auto` hver 6. time (og når indstillingerne ændres): fem `INSERT … SELECT DISTINCT ON` over deltagernes
  observationer, 25 deltagere ad gangen. Listerne caches i Valkey og invalideres ved hver ændring.
* RSS: hver time, én kilde ad gangen, højst 3 MB pr. svar; feed-adressen findes via `<link rel="alternate">` eller
  almindelige stier (`/feed/`, `/rss.xml` …). Feeds med `<!ENTITY>` afvises.
* Fotos gemmes i `media/netfugl/` (skaleret i browseren til højst 2048 px).
* Frontend: `web/js/views/netfugl.js` + `web/js/netfugl/*.js`, CSS i `web/css/netfugl.css` (afgrænset til `.nfu`).
