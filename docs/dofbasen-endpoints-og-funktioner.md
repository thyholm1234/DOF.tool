# DOFbasen (dofbasen.dk) – endpoints, funktioner og backend-krav

Gennemgang af https://dofbasen.dk/ udført 22. oktober 2026: offentlige sider, de indloggede sider (kun læsning – der er
ikke indtastet, rettet eller slettet noget), de JavaScript-filer sitet henter, samt den offentlige REST-API
(`api.dofbasen.dk`), som PWA-indtastningsappen `app.dofbasen.dk` bruger.

**Hvad der er verificeret, og hvad der ikke er.** Alle GET-endpoints markeret ✅ er kaldt og svarer som beskrevet.
Skrive-endpoints (POST/PUT/DELETE) er *ikke* kaldt; deres sti står i app-bundlen, men metoder og felter er udledt af
appens kode og formularerne – de er markeret 🔎. Intet her er officiel dokumentation fra DOF.

## 1. Systemoverblik

| Del | Adresse | Teknik (aflæst) |
|---|---|---|
| Hjemmeside (præsentation, søgning, webindtastning, admin) | `https://dofbasen.dk/` | PHP 5.3, nginx 1.14, server-renderet HTML (latin-1), jQuery 1.4, Leaflet 1.6, session-cookie `PHPSESSID` |
| Indtastnings-PWA | `https://app.dofbasen.dk/` | Vue 3 + Vite, Axios, MapLibre (offline-kort), service worker, IndexedDB |
| REST-API til PWA | `https://api.dofbasen.dk/api/v1.0/` | JSON, Bearer-JWT + refresh-token, CORS åben for alle origins (`Vary: Origin`) |
| Statistik | `https://statistik.dofbasen.dk/` | Egen app (bidragsydere, lokaliteter, observationer pr. år/kommune, arter) |
| Notifikationer | `https://notifikation.dofbasen.dk/` | Egen app (push ved usædvanlige fund) |
| Spontantællinger | `https://spontan.dofbasen.dk/` | Egen app (5-minutters stationære tællinger, FiNN) |
| Punkttællinger | `https://punkt.dofbasen.dk/` | Egen app (punkttællingsprojektet) |
| Atlas III | `https://dofbasen.dk/atlas` | Arter og kvadrater |

Hjemmesiden er et klassisk PHP-site uden REST: funktionerne nås via sider og query-parametre (afsnit 3). Kun PWA'en
bruger en JSON-API (afsnit 4).

## 2. Funktioner (komplet liste)

### 2.1 Uden login
- **Forsiden**: dagens observationer (kort + tabel), bemærkelsesværdige fund, "Dagens kort" (`?kortdag=idag|igaar|spt`),
  job/stillinger (`?jobs=o|spt`), billedudsnit fra dagens obs, graf over observationer pr. dag, art i fokus (Danmarks fugle).
- **Observationer** (`/observationer/`): dagens liste med visninger (alle fugle, bemærkelsesværdige, SU, pattedyr,
  sommerfugle, krybdyr/padder), område (lokalafdeling/kommune), dato, sortering (art/lokalitet). Markering med SU/SUB/
  bemærk-farver, blå pil (sag hos DKU), billed-/lyd-ikon, hemmelig-markering.
- **Søg observationer** (`/search/`): se parametrene i 3.2. Resultat som tabel, kort eller (logget ind) Excel-download.
  Summering pr. tur/art, underarter som arter, udeladelse af burfugle/hybrider/ubestemte, kun med billeder/lyd/koordinat/
  kommentar, største obs, fritekstsøgning i noter.
- **Fotogalleri** (`/fotogalleri.php`), **billedproxy** (`/image_proxy.php`).
- **Fænologi** (`/faeno/…`): artsliste, første iagttagelse, samme dato, artskort, fænologigrafer (`graf=andel`).
- **Atlas III** (`/atlas`, `/atlas/arter`, `/atlas/kvadrat`), **punkttællinger på kort** (`/pkt/kort.php`).
- **Lister/opslag** (`/opslag/…`): lokalitetsliste og -kort, artsliste, adfærds-/alders-/dragtkoder, vindstyrker, SU-/SUB-arter,
  følsomme arter, bemærkelsesværdige (antalsregler), fænologiliste (perioderegler), storlokaliteter, IBA-lokaliteter, observatørliste.
- **Link-generatorer** til egen side (`/opslag/visningstyper.php`, `…_soegning.php`).
- **Tekstsider**: Hvad er det (`/start.php`), Rettigheder (`/rights.php`), Hjælp/FAQ (`/help.php`), Kvalitetssikring/DKU
  (`/kvalitet`), Anvendelse af data (`/viden`), Kontakt (`/kontakt`), engelsk udgave (`/english`).
- **Brugeroprettelse** (`/opretmig.php`), **glemt password** (`/reminder.php`), **login** (`/logind.php`).

### 2.2 Logget ind (menuen *Bruger*)
- **Min side** (`/mine`): egne observationer pr. dato med redigér/slet/tilføj til tur (`/mine/dinefugle_ny.php`),
  personoplysninger + skift password (`/mine/person.php`), profilbillede (`/mine/profilbillede.php`).
- **Indtastning** (`/indtast/it.php`): se 2.3.
- **Bearbejdninger** (`/indtast/it.php?mode=bearb`): ynglefugl-bearbejdning pr. lokalitet/år/art (min/max antal par, første/sidste
  dato, antal besøg, kommentar, hemmelig).
- **Punkttællinger** (`/pkt/pktadmin.php?mode=index`): opret rute, indtast tælling, se egne.
- **Lokalitetsforslag** (`/mine/suggestlok.php`): navn, kommune, koordinat, op til 4 lokalitetstyper, fredning, beskrivelse → til lokalkoordinator.
- **Indstillinger** (`/mine/homepageconf.php`): standardsortering/visning af observationssiden og hvilke felter indtastningen viser/husker.
- **Excel-udtræk** af søgninger (kræver login, jf. rettigheder 1.3) – `/excel/search_result1.php`.
- **Log ud** (`/logud.php`).

### 2.3 Indtastning (webformularen `it.php`, og PWA'en)
**Tur** (obligatorisk: dato + lokalitet; PWA'en kræver også starttid):
lokalitet (autosuggest fra 3 tegn; ~20.000 lokaliteter; kort), dato, fra/til kl., medobservatører, turnoter, **metode** (22–23 typer, se 5.4),
*alle fuglearter indtastet* (komplet liste), **vejr** (vindretning 16 retninger, vindstyrke 0–50 m/s, skydække 0–8, temperatur −50…+100,
sigt i meter, nedbørstype regn/hagl/slud/sne + mm), **forstyrrelser** (op til 20 linjer: type, antal, noter; 27 typer), turens position
(lat/lng/radius). PWA: GPS-rute, "på vegne af observatør", automatisk starttid.

**Observation** (minimum: art, antal, primær adfærd): art (autosuggest, arter + underarter + artsgrupper "sp."), antal, **primær adfærd**
(rastende, trækkende, overflyvende, ynglende, død), **sekundær adfærd** (afhænger af primær) og **trækretning**, fra/til kl., køn, alder (1K…5K+,
AD/IMM/JUV/PUL/SUB), dragt (lys/mørk form, sommer-/vinterdragt …), noter, markeringer (kaldende, kun hørt, indtrækkende, udtrækkende,
olieskadet, burfugl), **hemmelig** (+ "hemmelig til" dato), position på kort med radius, op til **5 billeder** (PWA også lyd).
PWA-tilføjelser: optællingsmetode (uangivet/præcis/estimat), syg, fiskegrej, plastik, genbrug af adfærd, `secret/secretDate`.

**Ynglepar** (YP): art, min/max antal par, YP-adfærd/ynglebevis (15 koder), noter, hemmelig (+ dato), position/radius.

**Redigér/slet/tilføj til eksisterende tur**, kopiér "samme tur"-knap (konfigurerbar), kort i indtastning (konfigurerbar).

**PWA-specifikt**: offline-tilstand med synkronisering (sort sky = ikke sendt, grønt tegn = overført), offline-kort (kortpakker),
GPS-tracking af tur, "på vegne af", tur-sortering/-liste ("Mine ture"), dublet-detektion ("Matchende tur fundet"), billed-/lydupload.

### 2.4 Kvalitetssikring og administration (fra FAQ/DKU/rettigheder)
- **DKU** (kvalitetsudvalget) sætter tvivlsomme obs "til behandling" (blå pil); observatør får mail og har **1 måned** til at reagere.
- **Personliggørelse**: obs fjernes fra offentlige data men kan ses under *Mine observationer*.
- **Automatisk hemmeligholdelse** af følsomme arter (liste i `/opslag/sensitive.php`) + aktiv hemmelighed pr. obs/YP.
- **Inaktivering af brugere** ved gentagne udokumenterede fund.
- **Roller**: almindelig bruger, lokalkoordinator, central koordinator, DKU-medlem, *caretaker* (IBA-tælling), fuglestationer (standardtids-metoder).
- **Dataudlevering** til myndigheder/virksomheder via DOF (aftale med Miljøministeriet 2024-2027).

## 3. Endpoints på hjemmesiden (dofbasen.dk)

Alle er GET medmindre andet er angivet. Svar er HTML (latin-1) medmindre andet er angivet. 🔒 = kræver login-session.

### 3.1 Sider
| Sti | Funktion |
|---|---|
| `/` , `/index.php` | Forside. Parametre: `kortdag=idag|igaar|spt`, `jobs=o|spt`, `sjf=dato|nu`, `stat=stat|afvig|faeno`, `tema=<id>`, `english=<x>` |
| `/observationer/index.php` | Dagens observationer. `lok` (574–586 = lokalafdeling), `visning` (`allfugle|fugle|sufugle|patdyr|somfugle|krybdyr`), `visidag=DD-MM-ÅÅÅÅ`, `showstyle=art|lokalitet` |
| `/search/` (POST → `/search/index.php`) | Søgeformular (3.2) |
| `/search/search1.php?soeg=soeg&…` | Søgning via link (3.3) |
| `/search/result.php` | Resultatside; bruges også af *Mine observationer* (`design`, `soeg`, `periode`, `species`, `egneobs`, `sortering`, `obstype`, `andresobs`) |
| `/search/lokalitet.php`, `storlokalitet.php`, `ibalokalitet.php` | Lokalitets-, storlokalitets- og IBA-lister |
| `/kort.php` | Lokalitetskort (WFS via `/geoserverwrapper/index.php/wfs?`) |
| `/popobs.php?obsid=&summering=tur|…&obs=obs` | Popup: én observation / hele turen |
| `/popobser.php?obserkode=` | Popup: observatør |
| `/poplok.php?loknr=` | Popup: lokalitet |
| `/popposition.php?lng=&lat=&r=` , `/poppositionspontan.php?turid=&lng=&lat=&r=` | Popup: position på kort |
| `/image_proxy.php?mode=o|m&pic=<fil>&resizeto=small` | Billeder (`o` = observation, `m` = medie/forside) |
| `/fotogalleri.php`, `/fotografer.php` | Galleri / fotografer (403 uden browser-headers) |
| `/faeno/art.php?artnmr=<5 cifre>[&graf=andel]`, `/faeno/firstobs.php`, `/faeno/samedate.php`, `/faeno/art_kort.php` | Fænologi |
| `/ART/art.php?art=<artnr>` | Danmarks fugle (artsportræt) |
| `/atlas`, `/atlas/arter`, `/atlas/kvadrat` | Atlas III |
| `/pkt/kort.php` | Punkttællinger på kort |
| `/opslag/` , `/opslag/{artdata,behaviour,alder,dragt,vindstyrke,sudata,subdata,sensitive,bemaerk,wsdata,obserdata,visningstyper,visningstyper_soegning}.php` | Opslagslister |
| `/start.php`, `/rights.php`, `/help.php`, `/kvalitet`, `/viden`, `/kontakt` | Tekstsider |
| `/jpgraph/graf/obs_pr_dag.php` | Graf (billede) over observationer pr. dag |

### 3.2 Søgeformularen (`POST /search/index.php`)
Felter (navne = formfelter): `periode` (`none|dato|mellemdato|antaldage|maanedaar`), `dato`, `dato_first`, `dato_second`, `dage`, `aar_first`,
`aar_second`, `halvaar`, `maaned`, `uge`, `dagen`; `obstype` (`observationer|ynglepar|ibataelling`), `ynglepar` (`alle|observationer|bearbejdede`),
`ibataelling` (`rastefugle|ynglepar`), `species` (`alle|weird|faeno|rare|su|pattedyr|padder|krybdyr|sommerfugl|everynonbird|alt`);
område: `omraade` (`none|lokalitet|lokal|kommune|iba|stor`) + `lok`/`hiddenlok`, `lokal`, `kommune`, `iba`, `stor`;
art: `artdata` (`keine|art|artgruppe|artinterval`), `art`/`hiddenart`, `subspecies`, `artgruppe`, `fra_art`, `til_art`;
antal: `minantal`, `maxantal`, `antalregl` (`stoerre|mindre|ligmed`), `antal`; kode-filtre: `adf`, `alder`, `dragt`, `sex` (`M|F`), `metode`;
rødliste: `tlister` (`bilag|roed`), `roed` (`alle|cr|nt|en|vuna|re|lc`);
flueben: `summering`, `andre_indtastere`, `medobser`, `billeder`, `lyd`, `iba_kriterie`, `kommentar`, `koordinat`, `udelad_burfugle`,
`udelad_hybrider_ubestemte`, `vis_underarter_som_arter`; tekst: `obserdata` (obserkode), `biggestobs`, `textsearch`; `sortering`
(`dato|art|lokalitet|observat`); `soeg` (submit).

### 3.3 Søgning via link
`/search/search1.php?soeg=soeg&datovalg=<idag|betweendato|daysnumber|yeardato|maaned>&omraade=<lokdata|lokal|kommune>&…` – `soeg`, `datovalg`
og `omraade` skal altid med. Øvrige: `art=<artnr 5 cifre>`, `idag=DD-MM-ÅÅÅÅ`, `daysnumber` (maks 30), `yeardato`, `maaned=01..12`, `minantal`,
`lokal` (574–586 eller `alle`), `lokdata=<loknr>`, `obserdata=<obserkode>`, `behaviour` (1 rastende, 2 trækkende, 3 fouragerende, 5 overflyvende,
6 ynglefugle, 7 indtrækkende, 8 udtrækkende, 9 ringmærkede), `visning`.

### 3.4 Excel-/CSV-udtræk 🔒
`GET /excel/search_result1.php?design=excel&soeg=soeg&obstype=observationer&species=alle&sortering=dato&periode=dato&dato=DD-MM-ÅÅÅÅ` –
semikolon-separeret, latin-1, kolonner:
`Dato;Turtidfra;Turtidtil;Loknr;Loknavn;Artnr;Artnavn;Latin;Sortering;Antal;Koen;Adfkode;Adfbeskrivelse;Alderkode;Dragtkode;Dragtbeskrivelse;Obserkode;Fornavn;Efternavn;Obser_by;Medobser;Turnoter;Fuglnoter;Metode;Obstidfra;Obstidtil;Hemmelig;Kvalitet;Turid;Obsid;DOF_afdeling;lok_laengdegrad;lok_breddegrad;obs_laengdegrad;obs_breddegrad;radius;obser_laengdegrad;obser_breddegrad`
(kolonnenavne fra portalens parser; endpointet svarer 200 uden login, men data kræver login jf. rettighederne).

### 3.5 Login og session
| Metode | Sti | Beskrivelse |
|---|---|---|
| GET | `/logind.php` | Loginformular; sætter `PHPSESSID` |
| POST | `/turnmeon.php` | Felter `navn` (obserkode), `password`, `rememberlogin`, `submit`. 302 → `/index.php` ved succes; derefter vises brugermenuen (`MENU_ITEMS_USER`) |
| GET | `/logud.php` | Log ud |
| GET/POST | `/opretmig.php`, `/reminder.php` | Brugeroprettelse / "glemt password" (mail med password – se 6.1) |

### 3.6 Indloggede sider 🔒
| Sti | Funktion |
|---|---|
| `/mine` | Min side |
| `/mine/dinefugle_ny.php` | Egne observationer (søgning `GET /search/result.php?…egneobs=…`) |
| `/mine/person.php` | POST: personoplysninger (`person1..12`, `initi`, `field_gender`, `field_birthYear`) og skift password (`password`, `nypassword1/2`) |
| `/mine/profilbillede.php` | Upload profilbillede |
| `/mine/suggestlok.php` | POST: lokalitetsforslag (`lokalitetsnavn`, `kommunen`, `lon`, `lat`, `lokt1..4`, `fredet`, `beskrivelse`) |
| `/mine/homepageconf.php` | POST: sorterings-/visningspræferencer og indtastningsindstillinger |
| `/indtast/it.php` | POST: indtastning af tur + observation + ynglepar (felter i 2.3; knapper `indtast_observation`, `indtast_reset`) |
| `/indtast/it.php?mode=bearb` | POST: bearbejdning (`ypbearbejd*`) |
| `/indtast/it.php?hiddenlok=<id>`, `…?mode=bearb&hiddenypbearbejdlok=<id>` | Hent eksisterende tur/lokalitet ind i formularen |
| `/indtast/popnote.php?iface=1|2|tur` | Pop-up til lange noter |
| `/indtast_lokalitet.php?it=it` | Lokalitetsvælger |
| `/amt/lok.php?retlok=<id>` | Ret lokalitet på en tur/obs |
| `/pkt/pktadmin.php?mode=index` | Punkttællingsadministration |

### 3.7 Autosuggest / hjælpe-endpoints (JSON)
| Sti | Svar |
|---|---|
| ✅ `/include/autosuggest/jsonlok.php?indtast=yes&q=<tekst>` | `[{"lokid","id" (loknr),"label","value","desc" (afdeling),"info","nord","ost"}]` |
| `/include/autosuggest/jsonart.php?indtast=yes[&order=alfabetisk]` | Artsliste til autosuggest |
| `/geoserverwrapper/index.php/wfs?` | WFS (lokalitetsgrænser, kort) |

## 4. REST-API (`https://api.dofbasen.dk/api/v1.0/`)

Bruges af PWA'en. JSON (`Content-Type`/`Accept: application/json`). Fejl er Spring-format (`{timestamp,status,error,path}`) –
backenden ligner en Spring Boot-tjeneste. CORS tillader enhver origin.

### 4.1 Auth
| Metode | Sti | Beskrivelse |
|---|---|---|
| ✅ POST | `/login` | Body `{"username":"<obserkode>","password":"…"}` → `{"access_token","refresh_token"}` (JWT). 401 ved forkert kombination |
| ✅ GET | `/token/refresh` | Nyt `access_token` ud fra refresh-token (appen genforsøger automatisk ved 401) |
| ✅ GET | `/users/my-obserkode` | Svar: obserkoden som ren tekst |
| – | `Authorization: Bearer <access_token>` | På alle beskyttede kald |

### 4.2 Offentlige opslag (ingen login) ✅
| Sti | Indhold |
|---|---|
| `GET /public/species` , `/public/species/checksum` | Artsliste + checksum til cache-validering |
| `GET /public/sites` , `/public/sites/checksum` | Lokaliteter + checksum |
| `GET /public/gis/site/border/{id}` | Lokalitetsgrænse (GeoJSON) |
| `GET /public/species/phenology/deviant` | Arter, der er usædvanlige nu (afvigende fænologi) |
| `GET /public/behaviours` | Adfærd: `{id,danishCode,danishDescription,englishCode,englishDescription,isPrimary,primaryId,secondaryId,direction}` |
| `GET /public/behaviours/breeding-pair` | YP-adfærd (id 89–103) |
| `GET /public/ages` , `/public/plumages` | Alder / dragt (`id,danishCode,…`) |
| `GET /public/methods` | Turmetoder `{id,methodCode,shortMethodCode}` (23) |
| `GET /public/countingMethods` | `1 Uangivet, 2 Præcis, 3 Estimat` |
| `GET /public/disturbances/types` | 27 forstyrrelsestyper `{id,sortId,title}` |

### 4.3 Beskyttede ressourcer
| Metode | Sti | Beskrivelse |
|---|---|---|
| ✅ GET | `/collections/mine?page=&limit=20&sort=OBSERVED_DATE&ascending=false[&observersCode=]` | Egne ture inkl. børn |
| 🔎 POST/PUT/DELETE | `/collections/`, `/collections/{id}` | Opret/ret/slet tur |
| 🔎 POST/PUT/DELETE | `/observations/`, `/observations/{id}` | Observation på tur |
| 🔎 POST/PUT/DELETE | `/breeding-pairs`, `/breeding-pairs/{id}` | Ynglepar |
| 🔎 POST/DELETE | `/disturbances/`, `/disturbances/collections/{id}`, `/disturbances/{id}` | Forstyrrelser (DELETE 404 behandles som "allerede slettet") |
| 🔎 POST | `/images`, `/images/{id}` , `/audios` | Billede-/lydupload (multipart) |
| 🔎 GET | `/search` | (rute i appen, ikke bekræftet som API) |

**Tur (`collection`) – feltliste (fra svar):** `apiId, date (YYYY-MM-DD), startTime/endTime (HH:MM), site{i,n(loknr),na,m(kommune),s,la,lo},
latitude, longitude, methodId, completeList, spontan, coObserver (maks 50 tegn), notes, temperature, cloudCover (0–8), windDirection, windForce,
downpour, downpourType (Regn|Hagl|Slud|Sne), visibility, observersCode, isGpsTrackEnabled, gpsTrack, observations[], breedingPairs[], disturbances[]`.

**Observation:** `apiId, createtime, edittime, collectionApiId, specie (internt art-id ≠ artnr), count, countingMethodId, behaviourId, age, plumage,
sex ("-"|M|F), startTime, endTime, coordinate, notes, calling, heard, migratingInwards, migratingOutwards, cagedBird, sick, oilDamaged, fishingGear,
plastic, secret, secretDate`.

**Forstyrrelse:** `{id, apiId, collectionId, collectionApiId, disturbanceTypeId, count, notes}`.

## 5. Datamodel (afledt)

### 5.1 Entiteter
`bruger` (obserkode, navn, adresse, tlf, e-mail, fødselsår, køn, initialer, offentlighedsvalg, rolle) · `tur` (Turid) · `observation` (Obsid) ·
`ynglepar` · `bearbejdning` · `forstyrrelse` · `lokalitet` (loknr, navn, kommune, DOF-afdeling, geometri, typer, IBA/storlokalitet) · `art`
(artnr, navn, latin, sortering, art_type art/underart/sp., SU/SUB/bemærk-status, følsom) · `kodelister` (adfærd, alder, dragt, metode, vind) ·
`billede/lyd` · `DKU-sag` · `lokalitetsforslag` · `punkttællingsrute/-tælling` · `spontantælling`.

### 5.2 Nøgleregler for indtastning (fra FAQ)
- Tur kræver **dato + lokalitet**; observation kræver **art + antal + primær adfærd**.
- Ingen omtrentlig dato; ingen lokalitet uden for de ~20.000 eksisterende; **én tur pr. lokalitet pr. dag**; lokaliteten er *fuglenes*, ikke observatørens.
- En tur må **ikke overlappe midnat** (NocMig → mindst to ture).
- Maks **5 billeder** pr. observation; maks **20** forstyrrelser pr. tur (webformular).
- "Antal 0" tillades for observationer man kun *tror* var arten; usikre bestemmelser lægges på sp.-gruppe.
- Underarter kun hvis sikkert bestemt; ingen ukritisk app-bestemmelse (Merlin m.fl.).
- Ynglefugle-adfærd kun hvor reden formodes; sekundær adfærd anbefales; YP-adfærd kræver egnet lokalitet.
- Spontantælling-metoden sættes automatisk af spontan-appen og kan ikke vælges manuelt; IBA-tælling kræver caretaker-rettighed.
- Obserkode kan ikke ændres; password-skift logger ud.

### 5.3 Adfærd (struktur)
Primær: rastende (29), trækkende (58), overflyvende (28), ynglende (75), død (2). Sekundær pr. primær (rastende: fouragerende, sovende/overnatning,
derefter trækkende m.fl.; ynglende: syngende, territoriehævdende, parring, redebygning, ruger, udfløjne unger, afledning m.fl.; død: olie, rovdyr, gift,
sygdom, jagt, kollision m.fl.). Trækretning (`trkret1..7`) pr. retning, fx N/NØ/Ø … samt køn M/F for kønsdeling.

### 5.4 Metoder (turtype)
Uspecificeret/tilfældig · Totaloptælling · Ynglefugletælling · Trækfugletælling · Delvis optælling · Optælling/Udeladelse af artsgruppe (specificeret) ·
Punkttælling · IBA-tælling · Havfugletælling · Rovfugletælling · Rastefugletælling · Vandfugletælling · Diverse obs · Indlandstræktælling ·
Ringmærkning · Træk-/Havtælling/Indlandstælling standardtid · Linjetaksering · Passiv lydoptagelse (NocMig) · Spontantælling · FiNN-tælling ·
FiNN random walk · Ilanddrevne døde fugle (SUPRE).

## 6. Krav til backend (hvis funktionaliteten skal bygges/ageres imod)

### 6.1 Funktionelle krav
1. **Auth**: login med obserkode + password; session-cookie til web og JWT (access + refresh) til API; refresh uden ny login; 5 forsøg/min pr. obserkode/IP anbefales.
   Eksisterende DOFbasen sender *nuværende password i mail* ved "glemt password" – dvs. passwords gemmes reversibelt; en ny backend bør bruge hash + reset-link.
2. **Rollemodel**: bruger / lokalkoordinator / central koordinator / DKU / caretaker / fuglestation; rettighedsstyrede metoder og eksport.
3. **Tur-API**: atomisk oprettelse/ændring af tur med børn (observationer, ynglepar, forstyrrelser, GPS-rute); idempotens (klient-id) til offline-synkronisering;
   dublet-detektion på (dato, lokalitet, start, slut); valg af observatør ("på vegne af") med rettighedstjek.
4. **Validering** efter 5.2 (obligatoriske felter, dato ikke i fremtiden, tidsinterval, lokalitet findes, kodelister, billedgrænser, hemmelig-til-dato).
5. **Kodelister og referencedata** som offentlige, cachebare endpoints med checksum (arter, lokaliteter, adfærd, alder, dragt, metoder, forstyrrelser,
   lokalitetsgrænser) – klienten henter kun ved ændret checksum.
6. **Søgning**: kombinerbare filtre (3.2) over titusindvis af mio. rækker (~30 mio. obs.): dato/periode, område (lokalafdeling, kommune, IBA, storlokalitet,
   lokalitet), art/artsgruppe/-interval, antal, kodefiltre, rødliste, flueben, fritekst, sortering, summering pr. tur/art; resultat som tabel, kort og Excel/CSV.
7. **Klassificering**: SU, SUB, bemærkelsesværdig (antalsregler pr. art/område) og fænologi (periodevindue) beregnes ved visning/ingest; følsomme arter
   hemmeligholdes automatisk; aktiv hemmelighed pr. obs/YP med udløbsdato.
8. **Kvalitetssikring**: DKU-sagsflow (til behandling → svar inden 1 måned → godkendt / rettet / personliggjort / slettet), mailnotifikation, offentlig/privat synlighed.
9. **Medier**: upload af billeder (maks 5/obs; skalering/thumbnail via `image_proxy`) og lyd; copyright pr. fotograf; DOF har brugsret.
10. **Geodata**: lokaliteter som polygoner/punkter (PostGIS/GeoServer WFS), kommune-/afdelingsopslag, nærmeste lokalitet til koordinat, kvadrater til Atlas.
11. **Notifikationer**: push/e-mail ved usædvanlige fund (regler pr. afdeling/kommune/lokalitet/art) – i dag en separat tjeneste.
12. **Brugerfunktioner**: oprettelse, personoplysninger (indsigt/berigtigelse/anonymisering ved sletning), profilbillede, præferencer, lokalitetsforslag til koordinator.
13. **Statistik/fænologi/atlas/punkt-/spontantællinger** som selvstændige moduler på samme datagrundlag.
14. **Eksport/dataudlevering**: Excel kun for loggede brugere; professionel udlevering via DOF; kildeangivelse "(DOFbasen, www.dofbasen.dk, Dansk Ornitologisk Forening)".

### 6.2 Ikke-funktionelle krav
- **Skala**: ~30 mio. observationer, ~20.000 lokaliteter, hundredvis af arter; søgning skal bruge dækkende indekser (dato, lokalitet, art, obserkode).
- **Tegnsæt**: hjemmesiden leverer **ISO-8859-1** (Excel-udtræk også); nye klienter skal afkode latin-1 og håndtere "smart quotes".
- **Tilgængelighed offline**: PWA'en forudsætter caching af reference-data og idempotent upload; serveren må tåle gentagne og forsinkede kald.
- **Rate limits** (anbefalet, og et krav for tredjepartsklienter som DOF.tool): højst ét udtræk i minuttet mod hjemmesiden; login højst 5/min.
- **CORS** til `app.dofbasen.dk` og evt. tredjeparts-PWA'er; HTTPS overalt; `Cache-Control: no-store` på dynamiske sider (er sat i dag).
- **Juridisk**: GDPR (indsigt, berigtigelse, ret til at blive glemt = anonymisering, observationer bevares), cookies (session + statistik), ingen
  videregivelse af kontaktoplysninger, kommerciel brug kun efter skriftlig tilladelse.
- **Drift**: log/overvågning af indtastningsfejl, backup af observationsdata, adskilte miljøer for hjemmeside, API og delapps.
- **Sikkerhed**: sessionfiksering/CSRF-beskyttelse på `turnmeon.php` og `it.php` (ikke verificeret – sitet kører PHP 5.3 og nginx 1.14, begge uden sikkerhedsstøtte),
  adgangskontrol på `/mine/*` og eksport, input-escaping (formularer rummer fritekst).

### 6.3 Hvad DOF.tool kan bruge direkte
- Reference-data uden login via `api.dofbasen.dk/api/v1.0/public/*` (allerede brugt til `seed/trip_lookups.json`).
- `include/autosuggest/jsonlok.php` til lokalitetssøgning (offentlig).
- Søge-/Excel-endpoints begrænset til ét udtræk i minuttet via portalens gateway.
- Skrivning til DOFbasen er **ikke** implementeret i DOF.tool; hvis det ønskes, er de interessante endpoints `POST /collections/`, `/observations/`,
  `/breeding-pairs/`, `/disturbances/` (🔎, skal verificeres mod en testbruger før brug) og kræver, at brugerens DOFbasen-login holdes i browseren.
