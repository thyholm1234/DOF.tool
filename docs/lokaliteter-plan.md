# Lokaliteter: hent alt fra DOFbasen, og administrér grænserne i DOF.tool

Mål: alle DOFbasens lokaliteter med **grænse (hjørnepunkter)**, **beskrivelse**, **lokalitetstyper**, kommune, afdeling,
region og IBA gemt som JSON – hentet roligt på denne computer (trin 1), pakket i én zip og importeret på serveren
(trin 2). Derefter kan admin rette grænser, oprette nye lokaliteter, lægge lokaliteter inden i andre og dele dem op.
Diagrammer og statistiksider hentes ikke.

## Hvad DOFbasen faktisk har (undersøgt 31.10.2026)

| Kilde | Indhold | Antal | Kald |
|---|---|---|---|
| WFS `dof:lokaliteter` | `lokid`, `loknr`, `loknavn`, punkt, `vis` | ~22.600 | kan sides (fx 500 pr. kald) |
| WFS `dof:site_borders_active` / `_inactive` | grænsen som GeoJSON-polygon (EPSG:4326) + `lokid`, `loknr`, `loknavn` | 20.551 + 271 | kan sides |
| WFS `dof:lokalitetsgraenser` | pr. grænse: `oprettet`, `opdateret`, `accepteret` (og `obserid`) | ~20.800 | kan sides, uden geometri |
| `poplok.php?loknr=N` (HTML, Latin-1) | navn, kommune(r), lokalafdeling(er), region, **lokalitetstype(r)**, **beskrivelse**, IBA (navn + id), centrum, antal arter/obs.-dage/obs. | én side pr. lokalitet | ~22.600 |
| `kort.php?east=…` | lokaliteterne i et kortudsnit (`lokid`, `loknr`, navn, centrum, `vis`) | – | ikke nødvendig – WFS'en dækker det |

Vigtigt:
- **`lokid` ≠ `loknr`.** Grænserne slås op på `lokid` (Bølling Sø: lokid 6243, loknr 663010). Begge gemmes.
- Grænsen for Bølling Sø har 179 hjørnepunkter – det er den form, der skal bevares præcist (ingen forenkling i lageret).
- Portalen har i dag 22.569 lokaliteter som **punkter** (`locations.geom`), og 17 steder i koden bruger punktet.
  Punktet bevares; grænsen bliver et nyt lag ved siden af.
- WFS'en har også `vw_gps_observation` (GPS-observationer) – **den hentes ikke** (persondata og skjulte observationer).
- `obserid` i `lokalitetsgraenser` (hvem der tegnede grænsen) gemmes ikke – det er ikke nødvendigt og er persondata.

## Trin 1 – hent alt på denne computer

Et selvstændigt værktøj `tools/dofbasen_lokaliteter/hent.py` (kun standardbibliotek + `httpx`), der kan stoppes og
startes igen når som helst.

### Tempo og høflighed
- **Ét kald pr. 5 sekunder** – globalt for hele værktøjet, også genforsøg. En simpel "tidligst næste kald"-lås.
- Identificerbar `User-Agent: DOF.tool/1.0 (lokalitetsarkiv; kontakt: …)`.
- Fejl: 429/5xx → vent 1, 2, 4 … 30 min (og respektér `Retry-After`); 3 fejl i træk på samme lokalitet → markér den
  som fejlet og gå videre; 20 fejl i træk → stop helt (DOFbasen er nede eller har lukket for os).
- Kan begrænses til et tidsrum (`--kun-om-natten 22-07`), så det ikke belaster i dagtimerne.

### Rækkefølge (det billige først)
1. **Indeks** – WFS `lokaliteter` i sider af 500: ~46 kald ≈ 4 min. Giver hovedlisten (`lokid` ↔ `loknr`, navn, punkt,
   `vis`). Sammenholdes med portalens 22.569 lokaliteter (nye/manglende rapporteres).
2. **Grænser** – WFS `site_borders_active` + `_inactive` i sider af 200: ~105 kald ≈ 9 min. Lokaliteter, der mangler i
   siderne, hentes enkeltvis bagefter (`CQL_FILTER=lokid=…`).
   *Alternativ, hvis DOF foretrækker det:* én grænse pr. kald = ~20.800 kald ≈ 29 timer.
3. **Grænse-metadata** – WFS `lokalitetsgraenser` uden geometri (`propertyName=lokid,oprettet,opdateret,accepteret`):
   ~21 kald ≈ 2 min.
4. **Detaljer** – `poplok.php` for hver lokalitet: ~22.600 kald ≈ **31 timer** (to nætter). Den rå side gemmes, så den
   kan fortolkes igen uden at hente den igen.

I alt ≈ 31½ time, hvoraf alt andet end trin 4 tager et kvarter.

### Mappe og filer
```
data/dofbasen-lokaliteter/
  manifest.json              skemaversion, tidspunkter, antal, kilder, værktøjets version
  index.json                 alle lokaliteter: loknr, lokid, navn, centrum, vis, status (ok/mangler-grænse/fejl)
  lokaliteter/663/663010.json   én fil pr. lokalitet (mapper efter de første 3 cifre – ikke 22.600 filer i én mappe)
  raw/poplok/663/663010.html    DOFbasens side som hentet (Latin-1)
  raw/wfs/borders_active_0001.json …
  state.sqlite               fremdrift: hentet/fejlet, forsøg, seneste fejl – gør det muligt at fortsætte
  hent.log
```

### Én lokalitet (skema 1)
```json
{
  "schema": 1,
  "loknr": 663010, "lokid": 6243, "navn": "Bølling Sø og områder omkr. søen", "aktiv": true, "vis": 1,
  "center": {"lat": 56.174642, "lon": 9.377032},
  "graense": {
    "type": "Polygon", "coordinates": [[[9.349095, 56.184472], "…"]],
    "punkter": 179, "lag": "site_borders_active",
    "oprettet": "…", "opdateret": "…", "accepteret": "…"
  },
  "kommuner": ["Ikast-Brande", "Silkeborg"],
  "lokalafdelinger": ["DOF Vestjylland", "DOF Østjylland"],
  "region": "Region Midtjylland",
  "typer": ["Sø/Permanent vandhul/mose"],
  "beskrivelse": "Består af gammel højmose, tørvemose, eng og sump …",
  "iba": {"id": 133, "navn": "Bølling Sø"},
  "dofbasen": {"arter": 209, "obsdage": 3252, "observationer": 51151},
  "hentet": {"poplok": "<tidspunkt>", "graense": "<tidspunkt>"},
  "kilde": "https://dofbasen.dk/poplok.php?loknr=663010"
}
```
- Typerne gemmes som DOFbasen skriver dem; "Sø/Permanent vandhul/mose" er formentlig *én* type med skråstreg i navnet.
  Når alle sider er hentet, laves kataloget over de faktiske typer (`typer.json`), og først da afgøres opdelingen.
- Beskrivelsen gemmes som ren tekst (HTML-entiteter som `&aring;` afkodet, linjeskift bevaret); originalen ligger i `raw/`.

### Kontrol og pakning
- `hent.py tjek` – rapport: gyldige polygoner (selvskæring, for få punkter), centrum uden for egen grænse, lokaliteter
  uden grænse, dubletter af `lokid`, tegnsætningsfejl, forskelle til portalens liste.
- `hent.py pak` → `dofbasen-lokaliteter-2026-11-01.zip` med `manifest.json` og en SHA-256 pr. fil. Forventet størrelse:
  grænser ~70 MB JSON + sider ~170 MB HTML → ~40 MB zip (`raw/` kan udelades med `--uden-raw`).

## Trin 2 – upload og import på serveren

> **Status:** bygget (`services/location_import.py`, `api/admin/lokaliteter.py`, Admin → Lokaliteter bag funktionsflaget
> `lokalitetsadmin` og admin-modulet `lokaliteter`). Afprøvet mod udviklingsdatabasen med en zip fra prøvehøsten:
> 23.496 lokaliteter valideret og sammenlignet på ~7 s, 61 ugyldige polygoner repareret automatisk, 927 nye lokaliteter,
> 20.822 grænser anvendt; samme zip igen giver 0 ændringer; en portalrettelse bevares, og en konflikt opdages og kan
> beholdes (DOFbasens version gemmes som historik). Tabellerne ligger i observationsgruppen.

### Upload (Admin → Data → Lokaliteter)
- Upload i bidder (samme mekanisme som arkivupload og talemodel – store filer og afbrudte forbindelser).
- Serveren tjekker før import: kun forventede filnavne (ingen `..`, ingen symlinks), størrelsesgrænser pr. fil og i alt
  (zip-bombe), SHA-256 fra manifestet, JSON-skemaet, højst fx 20.000 punkter pr. polygon.
- Import kører i workeren som job (`location_import`) med fremdrift i jobstatus.

### Import i to skridt: forhåndsvisning → anvend
1. Indlæs i staging-tabeller og vis en **ændringsrapport**: nye lokaliteter, ændrede navne/typer/beskrivelser, ændrede
   grænser (med kort før/efter), lokaliteter der er forsvundet hos DOFbasen, og **konflikter** med grænser, som admin har
   rettet i portalen siden sidste import.
2. Admin trykker *Anvend* (evt. pr. gruppe). Portalens egne rettelser overskrives aldrig stille – konflikter løses én ad
   gangen (behold portalens / tag DOFbasens).

Den samme zip kan importeres igen senere (fx en ny høst om et år) – importen er idempotent.

### Datamodel (nye tabeller; referencedata i observationsgruppen, kopieret til brugergruppen af `sync_reference`)
- `location_details` – `loknr` (PK), `lokid`, `typer text[]`, `beskrivelse`, `region`, `iba_id`, `kommuner text[]`,
  `afdelinger text[]`, `dofbasen jsonb` (antal arter/obs.), `source` (dofbasen/portal), `fetched_at`.
- `location_shapes` – historik over grænser: `id`, `loknr`, `geom MultiPolygon(4326)`, `version`, `valid_from`,
  `valid_to`, `source` (dofbasen/portal), `status` (aktiv/kladde/afvist), `user_id`, `note`. Den aktuelle grænse er
  `status = 'aktiv' AND valid_to IS NULL` (eget indeks og en view `location_borders`).
- `locations` får tre kolonner: `parent_loknr` (lokalitet inden i en anden), `origin` (dofbasen/portal) og `merged_into`
  (sammenlagt lokalitet → den, der lever videre). `geom` forbliver punktet.
- `location_edits` – revisionslog: hvem, hvornår, handling (ret grænse, opret, del, læg sammen, flyt, ret tekst),
  før/efter som GeoJSON/JSON og begrundelse. Gør *fortryd* muligt.
- Portalens egne nye lokaliteter får numre i et reserveret interval (fx fra 9.000.000), så de aldrig støder sammen med
  DOFbasens `loknr`.

## Trin 3 – administration af lokaliteter

> **Status:** bygget (`services/location_edit.py`, ruterne under `/api/admin/locations/…`, Admin → Lokaliteter → Kort i
> `web/js/views/admin/lokaliteter-kort.js`, Leaflet-Geoman 2.20.4 i `web/vendor/geoman/`). Alle handlinger er afprøvet mod
> udviklingsdatabasen (ret, forældet version afvist med 409, opret inden i med klipning, del op, læg sammen, ret
> oplysninger, cyklus afvist, fjern overlap fra nabo, gendan, slet-regler) og kortets flow i en browser mod et simuleret API.
> Portalens lokaliteter får numre fra 90.000.000. Ikke gjort endnu: flytning af observationer med GPS-punkt til det barn,
> punktet ligger i, ved opdeling, og fire-øjne-godkendelse (kladder).

Nyt modul **Admin → Lokaliteter** (funktionsflag `lokalitetsadmin`, først forhåndsvisning; admin-modulet `lokaliteter`
kan tildeles roller).

### Kortet
- Grænser hentes pr. kortudsnit og zoom (`/api/locations/borders?bbox=…&zoom=…`), forenklet til zoomniveauet med
  `ST_SimplifyPreserveTopology` og caches – 20.000 polygoner kan ikke sendes på én gang.
- Redigering med **Leaflet-Geoman** (MIT-licens, lagt i `web/vendor/` – ingen CDN, passer til CSP'en og ønsket om open
  source): flyt/tilføj/slet hjørner, **fastgør til nabogrænser** (snapping), tegn nyt, klip.

### Handlinger
| Handling | Hvad sker der | Observationerne |
|---|---|---|
| **Ret grænse** | Ny version i `location_shapes`; den gamle får `valid_to` | uændret (de hører til `loknr`) |
| **Flyt fælles grænse** | Den delte kant mellem to naboer rettes i begge på én gang (PostGIS `ST_Snap`/fælles kant) | uændret |
| **Opret ny** | Tegn polygon → navn, typer, beskrivelse; nyt portalnummer | – |
| **Opret inden i** | Tegnes inde i en eksisterende; klippes til forælderen (`ST_Intersection`); `parent_loknr` sættes | – |
| **Del op** | Tegn en linje → `ST_Split` → to eller flere *nye* børn; den oprindelige bliver forælder | gamle observationer bliver på forælderen; observationer med GPS-punkt kan flyttes til det barn, punktet ligger i (forhåndsvist, kræver bekræftelse) |
| **Læg sammen** | `ST_Union` → én lever videre, den anden får `merged_into` | forbliver på deres `loknr`; lister og sider viser dem samlet |
| **Ret oplysninger** | Navn, typer (fra kataloget), beskrivelse, IBA | – |
| **Fortryd** | Fra revisionsloggen | – |

En lokalitet med observationer slettes aldrig – den kan kun deles (bliver forælder) eller lægges sammen (alias). Det
holder observationer, lister og lokale hits konsistente.

### Kontroller ved hver gem
- Gyldig geometri (`ST_IsValid`, ellers `ST_MakeValid`-forslag), mindst 3 punkter, højst N punkter.
- Overlap med naboer over fx 2 % af arealet → advarsel med kort over overlappet (ikke forbud – DOFbasen har overlap).
- Barn helt inden i forælder (`ST_Covers`); punktet (centrum) inden i egen grænse – ellers foreslås nyt centrum
  (`ST_PointOnSurface`).
- Valgfrit *fire-øjne-princip*: ændringer gemmes som kladde og godkendes af en anden med modulet.

## Trin 4 – brug grænserne i resten af portalen
- Lokalitetssiden og tråde viser grænsen; trækkortet og dagskortet kan vise den ved zoom.
- **Indtastning i felten:** GPS-positionen vælger lokaliteten via punkt-i-polygon i stedet for "nærmeste punkt" – den
  største gevinst for brugerne.
- **Lokale hits** kan tælle fund i lokaliteten og dens børn (forslag 4 fra fund-gennemgangen).
- Kommune/afdeling udledes af grænsen i stedet for punktet.

## Rækkefølge og omfang

| Fase | Indhold | Varighed |
|---|---|---|
| 1a | `hent.py`: indeks, grænser, metadata, tjek, pak – og en prøvehøst af ~200 lokaliteter | ½–1 dag + 15 min hentning |
| 1b | Fortolkning af `poplok.php` testet på 50 gemte sider (kommuner, afdelinger, typer, IBA, beskrivelse, tegnsæt) | ½ dag |
| 1c | Fuld høst | ~31 timer (to nætter), uden opsyn |
| 2 | Tabeller, upload, validering, staging, ændringsrapport, anvend | 1–2 dage |
| 3 | Admin-kortet: vis, ret, opret, inden i, del, læg sammen, historik, fortryd | 3–5 dage |
| 4 | Grænser i lokalitetsside, felt-indtastning og lokale hits | 1–2 dage |

Test: fortolkeren testes mod gemte sider (fixtures), importen mod en lille zip, geometrihandlingerne (del, læg sammen,
inden i, fælles grænse) mod PostGIS i testdatabasen, og admin-kortet i røgtesten (`tools/e2e`).

## Åbne spørgsmål
1. **Spørg DOFbasen først?** Et udtræk direkte fra dem (eller lov til WFS-siderne) er hurtigere og pænere end 31 timers
   sidekald. Høsten er bygget, så den virker uden.
2. Skal portalens rettelser kunne sendes tilbage til DOFbasen (fx som GeoJSON-fil pr. ændring)?
3. Skal fire-øjne-princippet være slået til fra start?
