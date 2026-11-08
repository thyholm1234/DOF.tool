# Sikkerhedsgennemgang 2026-11-08, runde 2

Anden runde af de syv trin samme dag. Den bygger på [første runde](gennemgang-2026-11-08.md) og gentager ikke dens fund
(S-01–S-16, M-01–M-04), medmindre der er ny viden. Rettelserne fra trin 6 er lavet i samme omgang.

**Fokus.** Første runde dækkede vagten, login, sessioner, drift og Docker, men læste kun stikprøver af lærings-, foto-,
artikel-, Netfugl- og DOFbasen-modulerne. Siden er DOF.tool-gruppen kommet til (commits `4b62e14` og `43d4e76`): brugernes
egne indtastninger flettes ind i lister, statistik og forudberegnede tal. Denne runde går derfor efter:

- rettighedstjek på alle 735 ruter, der er gennemgået systematisk via en udtrukket liste over hver rutes afhængigheder;
- ejerskab i foto-, Netfugl-, krydsliste-, lærings- og backupmodulerne;
- det flettede datasæt (`services/dataset.py`) og hemmelige observationer;
- udgående HTTP, zip-import, uploads og filstier;
- de åbne punkter fra første runde.

---

## 1. Sikkerhedsreview

### 1.1 Resumé

Rettighedstjekket er konsekvent:

- Alle `/api/admin/*`-ruter (183) kræver et modul, superadmin eller en kontrol inde i ruten.
- Objekter (fotos, kommentarer, kryds, kurser, enheder, aktiviteter, API-tokens) tjekkes for ejerskab eller moderatorret.
- Filstier i fotoimporten og zip-læsningen er afgrænset med `resolve()`/`is_relative_to` og størrelsesloft.
- Subprocesser kaldes med argumentlister, aldrig med shell.

**Det væsentlige nye fund er R-01.** Indtastningen lover «Hemmelig (kun synlig for dig)». Alligevel kom hemmelige
DOF.tool-observationer med i det samlede datasæt, som andre ser:

- andres årslister med art, dato og lokalitet;
- ranglister;
- lokaliteternes artslister;
- forudberegnede tal;
- Netfugl- og krydslisternes automatiske lister.

Før DOF.tool-gruppen havde alle observationer `hemmelig = 0` (DOFbasens udtræk er offentligt), så antagelsen «data er
offentlige» holdt. Den holder ikke længere. Lige nu findes ingen hemmelige rækker (44 meldinger, alle offentlige), så
lækket er latent, men afkrydsningsfeltet er i brug i appen. Rettelsen afviser nu hemmelige rækker centralt, som standard.

### 1.2 Antagelser i trusselsmodellen

Som i første runde (1.2). Desuden:

- **Godkendte brugere er potentielle modstandere** over for hinanden. Hemmelige observationer kan dreje sig om
  ynglende rovfugle og andre arter, der skal beskyttes. Et læk er både et privatlivs- og et naturbeskyttelsesproblem.
- **DOFbasens data er offentlige, DOF.tools er ikke nødvendigvis.** Al kode, der læser «hele datasættet», krydser
  denne grænse.
- **Testbrugere** logger ind med en lokal adgangskode, uden DOFbasen, og har forudsigelige obserkoder (`TEST0001` …).

### 1.3 Fund

| ID | Alvor | Fil/funktion | Problem | Udnyttelse | Anbefalet rettelse | Sikkerhed | Status |
|---|---|---|---|---|---|---|---|
| R-01 | Mellem | `services/dataset.include` og alle, der kalder den: `lists.year_list`/`scoreboard`/`matrikel_home`, `areas.refresh_*`, `locfinds`, `regions`, `places.location`, `observers`, `netfugl`/`krydslister` (automatiske lister), `calendars.typical_species`, `phenology`, `finds.species_finds` (`members`) | Hemmelige DOF.tool-observationer blev flettet ind uden filter. Kun omkring halvdelen af forespørgslerne havde `hemmelig = 0`. | Bruger A markerer en ynglende fugl som hemmelig. Enhver indlogget bruger åbner `GET /api/lists/user/A` (lister er offentlige som standard) og ser art, dato, lokalitet og obsid. Lokalitetens artsliste viser seneste dato. Fundberegningen afslører `dato\|lokalitet` i `members`, selvom fundets sted er skjult. | Afvis hemmelige rækker som standard i `include()`. Ejeren får sine egne med `secret_for`. Fundberegningen, der selv skjuler stedet, bruger `secret_all`. | Høj: koden er læst, og kæden er bekræftet mod rutens kode. Latent: 0 hemmelige rækker i dag. | Rettet |
| R-02 | Lav | `services/article_import._fetch_external` (S-12) | Billedhentningen fulgte omdirigeringer uden SSRF-kontrol. Kun første adresse var låst til netfugl.dk. | En omdirigering fra netfugl.dk (eller et kompromitteret netfugl.dk) til `http://10.0.1.8:8100/` eller `valkey:6379`. Svaret gemmes kun som billede, hvis det er gyldigt, så det giver blind SSRF. | SSRF-hook på klienten (også ved omdirigering), højst 5 omdirigeringer. | Høj | Rettet |
| R-03 | Lav | `.github/workflows/ios.yml` | Ingen `permissions:`, så `GITHUB_TOKEN` får repoets standardrettigheder (kan være skriveadgang). Gælder også jobbet, der kører med signeringshemmelighederne. | En kompromitteret action (fastgjort på tag, S-16) får et token med skriveadgang til repoet. | `permissions: contents: read`. | Høj | Rettet. SHA-fastgørelse er stadig åben (S-16). |
| R-04 | Lav | `server/Dockerfile`, `dbadmin/Dockerfile` | api og worker kører som root i containeren (ingen `USER`). | En sårbarhed med kodekørsel i api'en giver root i containeren og skriveadgang til alle volumener (medier, arkiv-upload, modeller). Uden docker.sock er det ikke root på værten. | Egen bruger og `chown` af volumenerne. Ikke for dbadmin, som alligevel har docker.sock. | Høj | Åben: kræver en migrering af volumenernes ejerskab. |
| R-05 | Lav | `api/admin/brugere.TestUserIn`, `reset_test_password` | En admin kunne vælge en adgangskode på 6 tegn til en testbruger. Obserkoderne er forudsigelige, og loftet er 30 forsøg i timen (720 i døgnet). | Svage testbrugere kan gættes på uger. Kontoen kan have fået moduler. | Mindst 12 tegn (den genererede er 12). Nulstil eksisterende testbrugere med korte koder. | Mellem: det vides ikke, hvilke koder der er brugt (de er hashede). | Rettet for nye koder. Eksisterende testbrugere: accepteret (ejeren 2026-11-08). |
| R-06 | Info | `netfugl._public_host` (brugt i et httpx-hook) | DNS-opslaget kørte blokerende i event-loopet. | En langsom DNS for en RSS-kilde fryser api'en eller workeren et øjeblik. | `asyncio.to_thread`. | Høj | Rettet (flyttet til `services/egress.py`). |
| R-07 | Info | `services/photo_import.DirSource._dir/read` | `is_symlink()` kaldes på en sti, der allerede er `resolve()`'et, og er derfor altid falsk. | Ingen: `is_relative_to(root)` afviser alligevel stier uden for roden. | Fjern eller flyt tjekket, så koden ikke giver falsk tryghed. | Høj | Åben (vedligeholdelse). |
| R-08 | Info | «Se som bruger» | Superadmin ser målbrugerens hemmelige meldinger (`reports`: viewer = målbrugeren) og nu også de hemmelige observationer på egne lister. Positioner skjules derimod i tilstanden. | Superadmin ser noget, brugeren har fået at vide, er «kun synligt for dig». Begrænset (15 minutter, logget), men i modstrid med løftet. | Beslutning: skal hemmelige skjules i se-som ligesom positioner? | Høj | Accepteret (ejeren 2026-11-08): den nuværende adfærd beholdes. |

### 1.4 Falske positiver (tjekket og fundet i orden)

- **Ruter uden `require_user`** (110): alle ligger under vagtens loginkrav eller har egen token-godkendelse
  (`/api/dofbasen/v1`, `/api/netfugl/v1`, ICS, `/api/location/native`). `/media/art/` validerer navnet med regex, før
  stien bruges.
- **Admin-ruter uden modul-dependency** (`/api/admin/support`, `/settings`, `/data/{source}`): tjekket inde i ruten med `can()`.
- **Netfugl/fotos**: ret, slet og grupér kræver ejerskab eller moderatorret. Grupper kan kun samle egne fotos.
  `PhotoIn` kan ikke sætte `user_id` eller `status` (ingen mass assignment).
- **Læring**: enheder og aktiviteter tjekkes mod kursets id. Kurset tjekkes med `load_editable`.
- **Backup**: kun superadmin. Databasenavnet kommer fra en whitelist. `pg_dump` køres med en argumentliste, og
  adgangskoden sendes via miljøet, ikke kommandolinjen.
- **XML (RSS)**: `xml.etree` i Python 3.12 med expat ≥ 2.6 henter ikke eksterne entiteter og har værn mod «billion laughs».
- **HTML**: nh3 med whitelist. Iframes får `sandbox` og kun tilladte værter. Kommentarer tillader kun http(s)/mailto.
- **Feed, alarmer, push, puls, SU og DKU** filtrerede allerede `hemmelig = 0`.
- **Søgning og dagslister** i DOF.tool-gruppen (`tool_fetch`) har `(hemmelig = 0 OR obserkode = :me)` i selve SQL'en.
- **Sessionscookien** er `HttpOnly`, `SameSite=Lax` og `Secure` (`PUBLIC_URL` er https i produktion, tjekket).

### 1.5 Kræver menneskelig bekræftelse

Besvaret af ejeren 2026-11-08:

1. **R-08**: superadmin må fortsat se målbrugerens hemmelige meldinger og observationer i «se som bruger» (tilstanden er
   tidsbegrænset, kun læsende og logget). Den nuværende adfærd beholdes.
2. **R-05**: eksisterende testbrugere tjekkes ikke. Kun nye adgangskoder skal have mindst 12 tegn.
3. **R-01**: reglen er bekræftet. Hemmelige observationer tæller ikke på ranglister og i offentlige tal. Ejerens egen liste
   og egne «huller» tæller dem. Matrikel-oversigten tæller dem ikke, så egen placering passer med ranglisten.

---

## 2. Modstander-review

### 2.1 Nye fund

- **A-01: en tillidsgrænse forsvandt uden at blive modelleret.** DOF.tool-gruppen blev lagt ind i et datasæt, hvor al kode
  i forvejen antog, at data var offentlige. `dataset.include()` blev kaldt fra 24 filer uden en fælles regel for
  `hemmelig`. Første runde blev lavet få commits efter ændringen og opdagede det ikke. Rettelsen gør afvisning til
  standarden, så nye forespørgsler er sikre uden at huske noget.
- **A-02: caches på tværs af brugere.** Mange læsninger går gennem `cached(...)` med nøgler uden seer (fx
  `dofb:obser:{kode}`, `sb2:…`, `finds3:…`). Sendes `secret_for` fra en sådan funktion, går ejerens hemmelige rækker til
  alle. Ingen gør det i dag. Reglen står nu i `include()`'s docstring og bør have en test (5.1).
- **A-03: forudberegnede tabeller bevarer data til næste genberegning.** `area_species_counts`, `loc_species_finds`,
  `location_year`, regionstabellerne, `nf_auto_lists` og `kl_auto_lists` er beregnet med den gamle kode. I dag er der
  ingen hemmelige rækker, så intet er lækket. Var der det, skulle genberegningen køres efter deploy.
- **A-04: Valkey rummer hele DOF.tool-datasættet i klartekst** (`doftool:rows:<v>`, også hemmelige rækker og præcise
  positioner, undtagen `skjul_position`). Det forstærker S-01: Valkey-dumps og -backups skal behandles som persondata.
- **A-05: testbrugere er en login-vej uden om DOFbasen** med forudsigelige brugernavne. Første runde nævnte dem ikke (R-05).

### 2.2 Tidligere undervurderet

- **S-12 (SSRF i artikelimporten)** var vurderet som «kun admin-modulet artikler». Adressen kommer dog fra indholdet i en
  uploadet zip, og omdirigeringer blev fulgt. Den, der kan få en zip importeret, styrer derfor ikke selve adressen
  (den er låst til netfugl.dk), men en omdirigering fra netfugl.dk blev fulgt frit. Nu rettet.
- **S-13 (ingen ratebegrænsning på skrivende kald)**: offline-køen sender mange meldinger på én gang, når forbindelsen
  vender tilbage. En naiv grænse vil derfor ramme rigtige brugere. Grænsen skal tillade bursts (fx token bucket med
  200 kald og 60 i minuttet pr. bruger) og undtage `/api/me/sync`.

### 2.3 Antagelser, der skal verificeres manuelt

- Proxyen overskriver `X-Forwarded-For`/`-Proto` (uændret fra første runde, M-02).
- Ingen kopi af Valkey (`*.rdb`/`*.aof`) ligger uden for værten uden kryptering (A-04).
- Ingen andre tjenester på værten kan nå `valkey:6379` eller `rustfs:9000` (Docker-netværket `doftool_default`).

### 2.4 Mest risikable angrebsvej

- **Fra LAN'et:** uændret fra første runde. dbadmin har docker.sock og giver dermed root på værten (M-01/S-02).
  Den er fortsat den alvorligste.
- **Fra internettet med en almindelig konto (før rettelsen):**
  1. log ind;
  2. hent scoreboardet og alle deltageres offentlige lister (`/api/lists/scoreboard` → `/api/lists/user/{kode}`);
  3. filtrér på arter, der er sjældne eller følsomme som ynglefugle;
  4. få lokalitet og dato for hemmelige observationer.

  Det kræver hverken særlige rettigheder eller fejl i andre lag. R-01 lukker vejen.

---

## 3. Forsyningskæden

### 3.1 Resumé

Siden første runde er Python-afhængighederne låst (`server/constraints.txt`, også `docker==7.2.0` til dbadmin).
Tilbage står:

- base-images, der kun er fastgjort på tag;
- GitHub Actions, der kun er fastgjort på tag;
- `setup-android.sh` uden checksum;
- talemodel-imaget, hvor de direkte pakker er låst, men de transitive ikke er.

`ios.yml` har nu mindst mulige token-rettigheder.

### 3.2 Afhængigheder (ændringer siden første runde)

| Pakke/kilde | Risiko | Bemærkning |
|---|---|---|
| Python (api, worker, dbadmin) | Lav | Låst. Ingen nye pakker i denne runde. |
| `tools/speech-model` (torch, transformers, onnx …) | Lav–mellem | Direkte versioner er låst. Transitive afhængigheder (fx `tokenizers`, `safetensors`, `requests`) er ikke. torch hentes fra download.pytorch.org (ingen dependency confusion: et navngivet indeks pr. kommando). Bruges kun til at bygge modellen. |
| `@capgo/background-geolocation`, `@capacitor/*` | Lav | Låst i `package-lock.json` (102 pakker). Ingen `postinstall` i `app/package.json`. |
| `python:3.11.10-slim` (speech-model) | Lav | Fastgjort på patchversion, men ikke digest. |

### 3.3 Byggeri og CI

- `ios.yml`: actions på tag (`@v5`, `upload-artifact@v4`). Rettet nu: `permissions: contents: read`.
- `server/Dockerfile`: `apt.postgresql.org.sh` (signeret arkiv). Containeren kører som root (R-04).
- `dbnode/Dockerfile`, `docker-compose.yml`: `postgis/postgis:17-3.5`, `valkey/valkey:8.1`, `rustfs/rustfs:1.0.3` på tag.

### 3.4 Krævet før release

Ingen nye. Kravene fra første runde (låste versioner, ingen hemmeligheder i git) er opfyldt.

### 3.5 Gerne

- Fastgør actions på commit-SHA (S-16) og base-images på digest. Opdatér med Renovate/Dependabot.
- `pip freeze` i speech-model-imaget skrevet til en lås (`tools/speech-model/constraints.txt`).
- Egen ikke-root-bruger i api/worker (R-04).

### 3.6 Filer, der skal læses manuelt

`tools/speech-model/Dockerfile`, `tools/setup-android.sh`, `.github/workflows/ios.yml`, `server/Dockerfile`, `app/build.sh`.

---

## 4. Vedligeholdelse

### 4.1 Resumé

Koden er stadig gennemgående godt kommenteret. Den største risiko for fremtidige fejl er, at sikkerhedsregler
håndhæves pr. forespørgsel i stedet for i et fælles lag: `hemmelig`, positionsprivatliv i se-som og `dataset.execute` i
stedet for `session.execute`. R-01 viser, hvad det koster. Rettelsen flytter `hemmelig` ind i det fælles lag.

### 4.2 Top 10 risici for at noget går i stykker (nye eller ændrede siden første runde)

1. **`secret_for` i en cachet funktion** (A-02) lækker ejerens hemmelige rækker til alle.
2. **Ny forespørgsel med `tool_fetch`** (direkte mod DOF.tool-gruppen) går uden om `include()` og dens standard.
   Den skal selv have `hemmelig = 0 OR obserkode = :me` (som `day_observations` og søgningen).
3. **`dataset.include`'s regex-omskrivning** (første runde, punkt 2) er nu også et sikkerhedslag. En forespørgsel, den
   ikke omskriver, mister DOF.tool-rækkerne (ufarligt). Én, den omskriver forkert, kan fejle.
4. **`year_list(own=True)`**: en ny kalder, der sender `own=True` for en anden brugers liste, viser hemmelige rækker.
5. **Forudberegnede tabeller** (A-03) skal genberegnes, når synlighedsreglerne ændres.
6. **SSRF**: ny udgående HTTP til brugerstyrede adresser skal bruge `egress.check_request`.
   `sound_quiz`, `dof_events`, `netfugl_list` og `dmi` bruger faste værter og har ikke brug for det.
7. **Vagten i `main.py`** (uændret, første runde punkt 1).
8. **Testbrugere** har en egen login-vej (`is_test`). Ændringer i login skal huske den.
9. **`photo_import`** har et symlink-tjek, der ser ud til at beskytte, men ikke gør (R-07).
10. **Rute-snapshottet** skal opdateres ved nye ruter (uændret).

### 4.3 Anbefalede refaktoreringer (efter værdi i forhold til risiko)

1. En test, der fejler, hvis `secret_for=` bruges i en funktion med `cached(`. Høj værdi, lav risiko.
2. En hjælper `dataset.tool_fetch_visible(sql, params, viewer)`, der lægger `hemmelig`-filteret på, så direkte
   DOF.tool-forespørgsler ikke kan glemme det. Mellem værdi, lav risiko.
3. Ét modul for «hvad superadmin ikke ser i se-som» (første runde 4.3, punkt 3). Hemmelige observationer er bevidst ikke med (R-08, accepteret).
4. R-04: egen bruger i api/worker-imaget. Mellem værdi, mellem risiko (volumenernes ejerskab).

### 4.4 Filer, der mangler kommentarer eller dokumentation

- `services/dataset.py`: er dokumenteret nu (sikkerhedsreglen i `include()` og `visible()`).
- `services/egress.py` (ny): dokumenteret, inkl. den kendte begrænsning med DNS-rebinding.
- `docs/arkitektur.md`: bør nævne, at DOF.tool-data kan være hemmelige, og at `include()` filtrerer.

### 4.5 Tests, der bør findes før større ændringer

- Hemmelige rækker i alle offentlige læsninger: en test med en hemmelig række i en test-DB, der kalder `scoreboard`,
  `area_species`, `places.location` og de automatiske lister og forventer, at den ikke er med. Kræver test-DB.
- `secret_for` aldrig i en cachet funktion (statisk test, se 4.3).
- Alle ruter kræver login (første runde 4.5, stadig åben).

---

## 5. QA og misbrugstests

### 5.1 Testplan

✅ = findes nu (`server/tests/test_security_hemmelig.py`).

| Test | Formål | Dækker | Forventet | Hvorfor | Blokerer |
|---|---|---|---|---|---|
| ✅ `test_include_udelader_hemmelige_som_standard` | R-01 | `dataset.include` | Kun offentlige DOF.tool-rækker og deres ture | Standarden skal være sikker | Ja |
| ✅ `test_include_ejeren_ser_kun_egne_hemmelige` | R-01 | `dataset.include(secret_for=)` | Egne hemmelige med, andres ikke | Ejeren mister intet | Ja |
| ✅ `test_include_secret_all_til_kaldere_der_selv_skjuler_stedet` | R-01 | `include(secret_all=)` | Alle rækker | Fundberegningen tæller fortsat | Ja |
| ✅ `test_include_kun_hemmelige_giver_uaendret_forespoergsel` | R-01 | `include` | SQL uændret, ingen JSON-parameter | Ingen tom UNION | Nej |
| ✅ `test_visible` | R-01 | `dataset.visible` | Sandhedstabel | Kernereglen | Ja |
| ✅ `test_year_list_hemmelige_kun_naar_ejeren_selv_ser` | R-01 | `lists.year_list` | `secret_for` kun ved `own=True` | Angrebsvejen i 2.4 | Ja |
| ✅ `test_fund_viser_ikke_hemmelige_steder` | R-01 | `finds.species_finds` | Tæller 2 fund, intet sted og ingen `members`-nøgle for det hemmelige | Læk via `members` | Ja |
| ✅ `test_egress_afviser_interne_adresser` (5 tilfælde) | R-02 | `egress.check_request` | `ValueError` for 127.0.0.1, 10.x, ::1, file:, 169.254.169.254 | SSRF | Ja |
| ✅ `test_artikelimport_omdirigering_til_internt_stoppes` | R-02 | Hooket ved omdirigering | Stop før forbindelse til 127.0.0.1 | Omdirigering var hullet | Ja |
| ✅ `test_artikelimport_bruger_egress_hook` | R-02 | `article_import` | Klienten har hooket | Regression | Ja |
| ✅ `test_testbruger_adgangskode_skal_vaere_lang` | R-05 | `TestUserIn` | 422 under 12 tegn | Gætteri | Ja |
| `test_hemmelig_raekke_i_offentlige_laesninger` | R-01 | `scoreboard`, `areas`, `places`, `observers`, auto-lister | Ikke med | Hele fladen | Ja (kræver test-DB) |
| `test_secret_for_ikke_i_cached` | A-02 | Statisk over `portal/` | Ingen fund | Fremtidigt læk | Nej (anbefalet) |
| `test_se_som_hemmelige` | R-08 | se-som + `reports`/`lists` | Målbrugerens hemmelige vises (accepteret adfærd) | Utilsigtet ændring opdages | Nej |
| `test_skriv_ratebegraensning_tillader_burst` | S-13 | Vagten (når bygget) | 200 hurtige kald ok, derefter 429 | Offline-kø mod spam | Nej |
| Røgtest efter deploy | Drift | Se 6.5 | Som beskrevet | Fejlkonfiguration | Ja |

### 5.2 Minimum før release

Testene i `test_security.py` (første runde) og `test_security_hemmelig.py`, `test_routes`, `test_architecture` og
røgtesten efter deploy (6.5). Testen med en hemmelig række i en test-DB bør køres, når `ARTICLES_TEST_DSN` er sat op.

---

## 6. Rettelser

Rettet: **R-01, R-02 (S-12), R-03, R-05 og R-06**. Ikke rettet:

- **R-04**: kræver migrering af volumenernes ejerskab.
- **R-07**: ufarligt.
- **R-08**: accepteret af ejeren.
- **S-13**: kræver et design, der ikke rammer offline-køen.

### 6.1 Ændringer

| Fil | Ændring |
|---|---|
| `server/portal/services/dataset.py` | Ny `visible()`. `include()`/`execute()` udelader hemmelige DOF.tool-rækker, medmindre `secret_for` (ejeren) eller `secret_all` er sat. Sikkerhedsreglen er dokumenteret. |
| `server/portal/services/lists.py` | `year_list(..., own=False)`: egne hemmelige kun når ejeren selv ser listen. |
| `server/portal/api/lists.py` | `user_list` sender `own`. `gaps` (egne huller) sender `own=True`. |
| `server/portal/api/dofbasen.py`, `api/dofbasen_v1.py` | `_row`/`_rows` med `secret_for`. Den enkelte observation, turlisten og dagslisten i API'et viser stadig ejerens egne hemmelige. |
| `server/portal/services/photo_obs.py`, `services/places.py` (`own_species`), `services/learn_train.py`, `api/auth.py` (`my_stats`) | `secret_for` = egen obserkode (kun egne data). |
| `server/portal/services/finds.py` | `secret_all=True` (hemmelige tæller stadig med). `members` springer hemmelige celler over. |
| `server/portal/services/egress.py` (ny) | SSRF-værnet (`public_host`, `check_request`), hvor DNS-opslaget køres i en tråd. |
| `server/portal/services/netfugl.py` | `_public_host`/`_check_request` peger nu på `egress` (samme navne, så eksisterende tests og kald er uændrede). |
| `server/portal/services/article_import.py` | httpx-klienten har `egress.check_request` og højst 5 omdirigeringer. En afvist adresse noteres som fejl for billedet. |
| `server/portal/api/admin/brugere.py`, `web/js/views/admin/brugere.js` | Testbrugeres adgangskode skal have mindst 12 tegn (`TEST_PW_MIN`). |
| `.github/workflows/ios.yml` | `permissions: contents: read`. |
| `server/tests/test_security_hemmelig.py` (ny) | 15 regressionstests (5.1). |
| `docs/sikkerhed/gennemgang-2026-11-08.md` | S-12 er markeret som rettet. |
| `README.md` | Henviser også til denne runde og den nye testfil. |

Ingen nye afhængigheder. Ingen ændringer i skema eller ruter (rute-snapshottet er uændret).

### 6.2 Adfærd, der er ændret med vilje

- Hemmelige DOF.tool-observationer tæller ikke længere på andres visning af ens liste, på ranglister, i lokaliteternes og
  områdernes tal, i regionslister, fænologi, kalenderens «typisk på denne tid» eller i de automatiske kryds- og
  Netfugl-lister.
- Ejeren ser dem stadig på sin egen liste, i egne huller, i egne tal, ved foto-tilknytning og i DOFbasen-modulets
  visning af den enkelte observation, tur og dag.
- Matrikel-oversigten tæller dem ikke, så ens egen placering stemmer med ranglisten.
- Fundberegningen tæller dem stadig, men viser hverken sted eller `dato|lokalitet`-nøgle.

### 6.3 Tests

`docker compose run --rm --no-deps -v ./server:/app/server -v ./dbadmin:/app/dbadmin -w /app/server api sh -c "pip install
-q pytest pytest-asyncio; python -m pytest -q --ignore=tests/test_netfugl_foto_parser.py"`:

- **Før rettelserne:** 254 bestået, 117 sprunget over.
- **Efter:** **269 bestået, 117 sprunget over (kræver test-DB), 0 fejl.**

Desuden en røgtest mod den kørende database med den nye kode monteret, men ikke deployet: `year_list` (offentlig og egen),
`dataset.execute(..., secret_for=)` og `own_species` kører uden fejl.

### 6.4 Deploy

`docker compose up -d --build api worker`. Frontendændringen (`brugere.js`) er live med det samme. Ny APK er ikke nødvendig.

### 6.5 Manuel verifikation efter deploy

1. Opret en testmelding med «Hemmelig» i appen med bruger A.
2. Som bruger B skal den ikke ses på A's liste (`#/lister/…/A`), på ranglisten, på lokalitetens side eller i
   DOFbasen-modulets dagsliste.
3. Som A skal den ses på egen liste, egen tur og i DOFbasen-modulets observation.
4. Admin → Brugere → Testbruger: en adgangskode på 11 tegn afvises.
5. Admin → Artikler: en import med billeder virker stadig (netfugl.dk er offentlig).
6. Var der hemmelige rækker før deploy (der var 0), skal genberegningen af områder, lokaliteter og automatiske lister
   køres fra Admin → Drift.

### 6.6 Tilbageværende risici

- Uændret fra første runde: S-03 (accepteret), S-10, S-11, S-13, S-14, S-15, S-16 og M-01–M-04.
- Fra denne runde: R-04 og R-07. R-08 og de eksisterende testbrugere (R-05) er accepteret.
- Direkte forespørgsler med `tool_fetch` filtrerer stadig selv (4.2, punkt 2).

---

## 7. Release-vurdering

**Beslutning: ship with conditions** (begrænset miljø: den nuværende installation og overdragelse til en anden udvikler).

### 7.1 Blokerende

Ingen i koden. Betingelse: rettelserne deployes (6.4) og verificeres (6.5, punkt 1–3), før flere brugere tager
«Hemmelig» i brug.

### 7.2 Ikke-blokerende

- R-04 og R-07.
- S-10, S-11, S-13, S-14, S-15 og S-16.
- Manglende afsnit om tilbagerulning, overvågning og ejerskab i `docs/drift.md` (første runde).
- Henvisningen til `Old Apps/` i README (første runde).

### 7.3 Manuel kontrol

- Hemmeligheder i git: `git ls-files` rummer ingen `.env`, nøgler, `google-services.json` eller `signing/`.
  `.gitignore` dækker dem.
- `TODO`/`FIXME`/`HACK`: ingen nye.
- Ingen debug-ruter. Fejl vises uden stacktraces.
- `/api/openapi.json` og `/api/docs` er offentlige (S-14, accepteret).
- `.env` er stadig 600.
- Licenser: ingen nye afhængigheder.

### 7.4 Overdragelse

Som første runde (7.4). Desuden:

- **Hemmelige observationer**: reglen bor i `services/dataset.visible()`. Læs docstringen i `include()`, før du skriver
  nye forespørgsler over hele datasættet.
- **Udgående HTTP til brugerstyrede adresser**: brug `services/egress.check_request`.
- **Testbrugere**: lokal adgangskode på mindst 12 tegn. De bør spærres, når de ikke bruges.

### 7.5 Næste opgaver for en ny vedligeholder

1. Deploy og verificér (6.4–6.5).
2. Saml reglerne for se-som ét sted (R-08 er afgjort: hemmelige vises for superadmin).
3. Statisk test af `secret_for` i cachede funktioner, og en `tool_fetch`-hjælper med filter (4.3, punkt 1–2).
4. Test-DB i CI, så IDOR-testene og testene af hemmelige rækker kører.
5. Ikke-root-bruger i api/worker (R-04). Actions på SHA og images på digest (S-16).
6. Ratebegrænsning med burst for skrivende kald (S-13, 2.2).
