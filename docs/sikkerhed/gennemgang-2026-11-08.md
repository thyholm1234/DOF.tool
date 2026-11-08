# Sikkerhedsgennemgang 2026-11-08

Gennemgang af DOF.tool (FastAPI-portal, worker, dbadmin, PWA, Capacitor-app, Docker Compose) i syv trin: sikkerhedsreview,
modstander-review, forsyningskæde, vedligeholdelse, QA/misbrugstests, rettelser og release-vurdering. Rettelserne fra
trin 6 er lavet i samme omgang; status pr. fund står i tabellen.

**Dækning.** Gennemgangen dækker vagten i `main.py`, login og sessioner, adgang på objekter (ture, meldinger, områder,
alarmregler, supporttråde), admin-rettigheder, uploads og levering af filer, udgående HTTP (SSRF), SQL bygget med
f-strings, HTML i frontenden, dbadmin, Docker-opsætningen, CI og app-byggeriet. Ikke alle 238 serverfiler er læst linje
for linje. Lærings-, foto-, artikel- og Netfugl-modulernes forretningsregler er kun stikprøvetjekket.

---

## 1. Sikkerhedsreview

### 1.1 Resumé

Applikationen er generelt omhyggeligt bygget. Ikke-trivielle ting, der er gjort rigtigt:

- Fælles login-vagt, og CSRF-værn med custom header eller `Sec-Fetch-Site`.
- Sessioner gemmes kun som hash.
- Ejerskab tjekkes konsekvent på objekter.
- Rettighedsændringer kan ikke give moduler, man ikke selv har.
- Sorteringskolonner i SQL går gennem whitelists.
- Markdown renses med nh3.
- Streng CSP med `script-src 'self'` og hash.
- Uploads konverteres til WebP, og medier leveres med CSP sandbox.
- SSRF-værn på RSS-hentning.
- «Se som bruger» er afgrænset og logget.

De alvorlige risici ligger i drift og tilstand, ikke i koden:

- Sessioner i klartekst var stadig gyldige.
- dbadmin, som har Docker-socket, kunne angribes med brute force fra LAN'et.
- En replika af brugerdatabasen er publiceret på alle netværksinterfaces.

### 1.2 Antagelser i trusselsmodellen

- Portalen står på internettet bag en reverse proxy (Cloudflare/nginx) på en Proxmox-VM. Værten kører også mange andre
  tjenester: vaultwarden, portainer, flere Postgres-instanser m.fl.
- Angribere kan være:
  - anonyme på internettet;
  - godkendte DOFbasen-brugere;
  - en brugerkonto med admin-modul;
  - en maskine på LAN'et (10.0.1.0/24);
  - en, der får fat i en backup eller kopi af Valkey eller databasen.
- DOFbasen er betroet til at verificere adgangskoder. Portalen gemmer ingen adgangskoder for rigtige brugere.
- Superadmin (obserkoden i `.env`) og dbadmin-kodeordet er de stærkeste roller. dbadmin svarer reelt til root på værten,
  fordi det har Docker-socket.
- Appen indlæser portalen fra serveren. Den kan kun stole på serverens TLS.

### 1.3 Fund

Status: **rettet** (i trin 6), **åben** (kræver beslutning eller ændring i driften) eller **accepteret**.

| ID | Alvor | Fil/funktion | Problem | Udnyttelse | Anbefalet rettelse | Sikkerhed | Status |
|---|---|---|---|---|---|---|---|
| S-01 | Høj | `services/auth.session_user_id`, Valkey | 37 sessioner fra før hashingen lå stadig i klartekst (`sess:<token>`, rå tokens i `usess:<id>`) og blev godtaget. `valkey-bak.tgz` (2. nov.) og Valkeys AOF indeholder dem. | Den, der får fat i backuppen eller et dump, kan logge ind som de brugere, evt. også som superadmin, indtil sessionerne udløber (90 dage). | Slet klartekst-sessioner ved opstart, og godtag dem ikke længere. Slet eller flyt `valkey-bak.tgz`. | Høj (37 nøgler talt i den kørende Valkey) | Rettet: i koden, og `valkey-bak.tgz` er slettet 2026-11-08. |
| S-02 | Høj | `dbadmin/app.py` login, `FORWARDED_ALLOW_IPS` | dbadmin begrænsede fejlede forsøg pr. IP, men IP'en tages fra `X-Forwarded-For`, når kilden er et privat net. Panelet lytter på LAN-IP'en 10.0.1.8:8100 og har `/var/run/docker.sock`. | En maskine på LAN'et skifter `X-Forwarded-For` ved hvert forsøg og gætter `DBADMIN_PASSWORD` uden loft. Det giver Docker og dermed root på værten med alle dens tjenester. | Et samlet loft over fejlede forsøg på tværs af IP'er. Stærkt kodeord. Bind panelet til 127.0.0.1 og brug en SSH-tunnel. Overvej at fjerne docker.sock. | Høj | Rettet: loftet (20 fejl pr. 5 min. i alt). Ejeren har bekræftet, at kodeordet er stærkt. Binding til LAN og docker.sock er accepteret. |
| S-03 | Mellem | Container `doftool-db-users-2` | Replikaen af brugerdatabasen er publiceret på `0.0.0.0:5440`. | Brugerdatabasen (brugere, meldinger med positioner, områder, push-abonnementer) kan nås fra netværket. Adgangskoden er et token på 32 bytes, så gætteri er urealistisk, men tjenesten er unødigt eksponeret, også for fremtidige sårbarheder i Postgres. | Publicér kun på 127.0.0.1 eller den IP, replikeringen bruger, eller slet ikke, når replikaen kører på samme vært. Firewall. | Høj (set med `docker ps`) | Accepteret: porten er bevidst åben (replikering), og der er firewall foran værten (bekræftet af ejeren 2026-11-08). |
| S-04 | Mellem | `services/auth._rate_limit` | Login var kun begrænset pr. minut (5 pr. obserkode, 5 pr. IP). Det giver ~7.200 gæt i døgnet pr. konto, og IP-grænsen kan omgås fra en betroet proxy-adresse. Hvert forsøg rammer også DOFbasen. | Langsom brute force mod svage DOFbasen-kodeord. Mange forsøg kan få serveren blokeret hos DOFbasen. | Et loft pr. obserkode pr. time, uafhængigt af IP. | Høj | Rettet: 30 i timen. |
| S-05 | Mellem | `main.guard` | Vagtens undtagelser blev matchet som præfiks: `"/api/me"` åbnede også `/api/me/export`, `/api/me/location/token`, `/api/me/sync` m.fl. for vagten. I dag beskyttes de af hver rutes `require_user`. | Ingen kendt udnyttelse i dag. En fremtidig rute `/api/me…` (eller `/api/medlemmer`) med `optional_user` ville være åben for anonyme uden at nogen opdager det. | Præcise stier adskilt fra præfikser. | Høj | Rettet. |
| S-06 | Mellem | `AndroidManifest.xml` | `allowBackup="true"` får WebView'ens sessionscookie og positionstokenet (SharedPreferences) med i Google-backup og `adb backup`. | Den, der har adgang til brugerens Google-backup eller USB-debugging, kan gendanne en logget-ind app med positionsadgang. | `allowBackup="false"`. | Mellem | Rettet. Kræver ny APK. |
| S-07 | Mellem | `server/Dockerfile`, `dbadmin/Dockerfile`, `pyproject.toml` | Python-afhængigheder havde kun `>=` og ingen lås. api- og dbadmin-images var allerede gledet fra hinanden (fx fastapi 0.162.2 mod 0.162.0). | En kompromitteret eller fejlbehæftet ny version kommer med ved næste build uden review. Builds kan ikke reproduceres. | Låsefil, der bruges i byggeriet. | Høj | Rettet: `server/constraints.txt`. |
| S-08 | Lav | `/DOF.tool/.env` | Filrettighed 644, læsbar for alle lokale brugere på en vært med mange tjenester. | En lokal bruger eller en anden kompromitteret tjeneste med adgang til værtens filsystem læser databasekodeord, dbadmin-kodeord, FCM-nøgle m.fl. | `chmod 600`. | Høj | Rettet. |
| S-09 | Lav | `api/calendar.my_ics`, `api/social.get_thread`, `api/auth.user_out` | «Se som bruger» var ikke helt læsende: GET `/api/me/ics` oprettede et ICS-token for målbrugeren, og supporttråde blev markeret som læst. Superadmin så også `ics_token` i målbrugerens indstillinger. | Superadmin får varig adgang til målbrugerens kalenderfeed og ændrer brugerens tilstand uden at vide det. | Afvis ICS i tilstanden, markér ikke som læst, og skjul `ics_token`. | Høj | Rettet. |
| S-10 | Lav | `services/calendars` | ICS-tokenet ligger i klartekst i `users.prefs`. | Den, der kan læse databasen eller en backup, kan abonnere på brugerens planlagte besøg. | Gem en hash (som positionstokens). | Høj | Åben (lav værdi, kræver migrering). |
| S-11 | Lav | `services/content_media` (`MAX_IMAGE_PIXELS = 120M`) | Pillow fejler først ved 240 MP. Der er ingen upload-ratebegrænsning pr. bruger. | Mange samtidige uploads af billeder med enorme dimensioner, men små filer, kan give hukommelsespres i api'en. | Lavere grænse (og behandl advarslen som fejl), samtidighedsloft, ratebegrænsning. | Mellem | Accepteret: telefoner med 108–200 MP findes; afvej før ændring. |
| S-12 | Lav | `services/article_import` (eksterne billeder) | Ingen SSRF-kontrol ved hentning af billeder fra importeret indhold, i modsætning til `netfugl._public_host`. | En bruger med modulet «artikler» kan få serveren til at lave GET-kald til interne adresser. Svaret gemmes kun, hvis det er et gyldigt billede. | Brug `_public_host`-hooket her også. | Mellem | Rettet i [runde 2](gennemgang-2026-11-08-runde2.md) (R-02). |
| S-13 | Lav | Skrivende endpoints (kommentarer, support, ture) | Ingen generel ratebegrænsning for godkendte brugere. Godkendelse af nye brugere dæmper det. | Spam eller belastning fra en kompromitteret konto. | Fælles ratebegrænsning pr. bruger i vagten for POST. | Mellem | Åben. |
| S-14 | Info | `/api/docs`, `/api/openapi.json` | Hele API-beskrivelsen er offentlig. | Giver anonyme overblik over alle endpoints. | Kræv login, eller slå fra i produktion. | Høj | Accepteret (showcase-API'erne bruger den). |
| S-15 | Info | `app/signing/` | App-signeringsnøglen ligger nu på serveren (600), på samme vært som dbadmin med Docker-socket. | Den, der kompromitterer værten, kan signere ondsindede appopdateringer, som installeres oven på den rigtige app. | Opbevar nøglen offline. Byg releases på en dedikeret maskine. | Høj | Åben (driftsbeslutning). |
| S-16 | Lav | `.github/workflows/ios.yml`, `tools/setup-android.sh` | GitHub Actions er fastgjort på tag (`@v5`), ikke på SHA. `setup-android.sh` henter «latest» JDK, Node og cmdline-tools uden checksum. | En kompromitteret action eller download kommer med i byggeriet. | Fastgør på SHA. Fastgør versioner og verificér SHA-256. | Høj | Åben. |

### 1.4 Falske positiver (tjekket og fundet i orden)

- **SQL med f-strings** (178 steder): det indsatte er konstanter, whitelists (`SORTS`, `SORT_COLUMNS`, `COL`) eller
  prædikater fra koden. Brugerværdier går altid som bundne parametre.
- **Delesiden `/del/traad`**: alt indhold escapes, og den viser kun DOFbasens offentlige data.
- **`innerHTML`/`html:` i frontenden**: kilderne er ikoner (konstanter), HTML fra serveren renset med nh3, eller egne
  små markdown-renderere, der escaper først. `mapHref` tillader kun http(s) og mailto. CSP'en blokerer inline-scripts.
- **CSRF**: cookien har `SameSite=Lax`, ændrende kald kræver `X-Requested-With: fetch` eller `Sec-Fetch-Site: same-origin`,
  og dbadmin bruger `SameSite=Strict` plus header.
- **IDOR**: ture, meldinger, medier på meldinger, områder, alarmregler, push-enheder og supporttråde tjekker ejerskab.
  Filnavne under `/media/obs` er uuid4 og kræver login.
- **Path traversal**: `/v/{build}/{path}` tjekker `resolve()` mod roden, mediernes navne valideres med regex, og backup
  bruger en whitelist af databasenavne.
- **Deserialisering**: ingen pickle, yaml eller eval. Talemodellen hentes på en fastlåst Hugging Face-revision.
- **Blokering af brugere** sletter sessionerne (`sess:*` matcher også `sess:h:*`).

### 1.5 Kræver menneskelig bekræftelse

Besvaret af ejeren 2026-11-08:

1. Porten 5440 for replikaen er bevidst åben, og der er firewall foran værten.
2. `valkey-bak.tgz` måtte slettes. Det er gjort.
3. `DBADMIN_PASSWORD` er stærkt.

Stadig åbne:
1. Står proxyen på en fast IP, så `FORWARDED_ALLOW_IPS` kan indsnævres fra «alle private net» til den ene adresse?
2. Skal app-signeringsnøglen blive på serveren?

---

## 2. Modstander-review

### 2.1 Nye fund

- **M-01: hele værten deler skæbne.** dbadmin (Docker-socket), portainer (0.0.0.0:9000/9443) og vaultwarden kører på
  samme Docker-vært. Den mest sårbare tjeneste på værten bestemmer DOF.tools sikkerhed, og omvendt. Ingen af de andre
  tjenester er gennemgået her.
- **M-02: tillid til proxyen er implicit.** `FORWARDED_ALLOW_IPS` stoler på alle 10/8, 172.16/12 og 192.168/16. api'en er
  publiceret på 0.0.0.0:8090, så alle på LAN'et og alle containere på værten kan skrive deres egen klient-IP og
  `X-Forwarded-Proto`. Det påvirker:
  - IP-grænser (login, positionstoken, dbadmin);
  - aktivitetslogs og privatlivsloggen;
  - HSTS-headeren.
- **M-03: GET med sideeffekter.** Ud over det rettede i S-09 committer `dku.get_case`, `su.get_case`, `netfugl.artikel`/`foto`
  (visningstal) og `fotos.koder` ved GET. Det betyder ikke noget for CSRF (SameSite=Lax tillader top-niveau-GET, men
  effekterne er harmløse). I «se som bruger» ændrer de dog stadig læst-status og visningstal.
- **M-04: dbadmin-loftet kan bruges til at låse administratoren ude.** Det samlede loft betyder, at en maskine på LAN'et
  kan holde det rigtige login ude i 5 minutter ad gangen. Det er en bevidst afvejning: udelåsning er bedre end
  ubegrænset gætteri mod root.

### 2.2 Tidligere undervurderet

- **S-01 er højere, end «sessioner er hashede» lod ane.** Hashingen beskyttede kun nye sessioner. De gamle blev godtaget
  og flyttet over ved første brug, så alle sessioner fra før hashingen er stadig gyldige med tokenet fra backuppen,
  hvis de er brugt siden (6 hashede sessioner kan være flyttede). Rettelsen sletter de resterende 37. De flyttede
  udløber først efter 90 dage. Fuld sikkerhed kræver, at backupfilen destrueres eller at alle sessioner nulstilles.
- **S-03 i samspil med M-02:** replikaen er ikke kun eksponeret mod internettet (hvis der ikke er firewall), men også mod
  alle containere og LAN-maskiner.

### 2.3 Antagelser, der skal verificeres manuelt

- Proxyen fjerner klientens egne `X-Forwarded-For`/`-Proto` og sætter sine egne.
- `COOKIE_SECURE`/`PUBLIC_URL=https://…` er sat i produktion (cookien skal være Secure).
- Værtens firewall blokerer alt andet end 80/443 udefra (især 5440, 5432 for boligbirding, 9000/9443 for portainer).
- Valkey og Postgres er ikke publiceret (bekræftet med `docker ps` for DOF.tools egne containere).
- FCM-nøglen og `DBADMIN_SERVICE_TOKEN` er unikke for denne installation og ikke genbrugt andre steder.

### 2.4 Mest risikable angrebsvej

En maskine på LAN'et (eller en kompromitteret container på værten):

1. brute-forcer dbadmin på 10.0.1.8:8100 med skiftende `X-Forwarded-For` (før rettelsen uden loft);
2. eller finder `valkey-bak.tgz` eller `.env` (644) gennem en anden kompromitteret tjeneste;
3. får dermed dbadmin, som har Docker-socket, og bliver root på værten;
4. har så adgang til alle databaser, alle brugeres positioner, signeringsnøglen til appen, vaultwarden-data osv.

Rettelserne lukker gætteri-trinnet og læsbarheden af `.env`. Binding, docker.sock og backupfilen kræver beslutninger.

---

## 3. Forsyningskæden

### 3.1 Resumé

Lav til mellem risiko:

- Appens npm-pakker er låst.
- Talemodellens byggeri er fastlåst på versioner og en Hugging Face-commit.
- Vendorerede frontend-biblioteker (Leaflet, geoman, lamejs, Inter) ligger i repoet uden CDN.

Svaghederne:

- Python-afhængighederne var ikke låst (rettet).
- Base-images er kun fastgjort på tag.
- CI-actions er fastgjort på tag.
- Android-værktøjerne hentes som «latest» uden checksum.

### 3.2 Afhængigheder

| Pakke/kilde | Risiko | Bemærkning |
|---|---|---|
| Python (fastapi, sqlalchemy, asyncpg, redis, httpx, pillow, nh3, asyncssh, minio, pywebpush …) | Lav | Kendte, vedligeholdte pakker. Nu låst i `constraints.txt`. |
| `docker` (kun dbadmin) | Mellem | Giver kontrol over Docker. Pakken er officiel, men selve brugen er den reelle risiko (S-02). |
| `@capgo/background-geolocation` | Lav–mellem | Lille leverandør, MPL-2.0. Bruges nu kun på iOS og ældre APK'er. Den kører GPS uafbrudt med wakelock på Android, derfor erstattet af `DofPosition`. |
| `@capacitor/*` | Lav | Officielle. |
| `com.microsoft.onnxruntime:onnxruntime-android:1.30.0`, `play-services-location:21.4.0` | Lav | Fastlåste versioner fra Maven Central/Google. |
| CoRal `roest-v3-wav2vec2-315m` | Lav | Fastlåst revision. Modellen bygges på serveren, og appen kontrollerer SHA-256 før brug. |
| Web-vendor (`web/vendor`) | Lav | Ingen versionsnoter i repoet. Opdatering sker manuelt. |

Ingen tegn på typosquatting. Ingen postinstall-scripts i appens direkte afhængigheder. Der er ingen privat
pakkeindeks-konfiguration, så dependency confusion er ikke relevant.

### 3.3 Byggeri og CI

- `server/Dockerfile` kører `apt.postgresql.org.sh` (fra pakken postgresql-common; signeret apt-arkiv). Acceptabelt.
- `python:3.12-slim`, `postgis/postgis:17-3.5`, `valkey/valkey:8.1` og `rustfs/rustfs:1.0.3` er fastgjort på tag, ikke digest.
- `ios.yml`: `actions/checkout@v5` og `actions/setup-node@v5` på tag. Hemmelighederne bruges kun i `ipa`-jobbet, som kun
  kører ved push til main eller manuelt (ikke fra pull requests).
- `tools/setup-android.sh` henter Node (seneste LTS), Temurin JDK 21 «latest» og cmdline-tools «latest» uden checksum.
- `app/build.sh` opretter selv en ny signeringsnøgle, hvis `signing/` mangler. Det kan føre til en APK, der ikke kan
  opdatere den installerede app. Se release-vurderingen.

### 3.4 Krævet før release

1. Låste Python-versioner (gjort).
2. Bekræft, at `google-services.json` og `app/signing/` aldrig kommer i git. De er i `.gitignore`, og det er tjekket i
   historikken.

### 3.5 Gerne

- Fastgør base-images på digest, og opdatér dem bevidst (fx med Renovate/Dependabot).
- Fastgør GitHub Actions på commit-SHA.
- Verificér SHA-256 i `setup-android.sh`, og fastgør versionerne.
- SBOM: `pip freeze` er reelt med i `constraints.txt`, og `npm ls --all` for appen. Gem en CycloneDX-fil pr. release.
- `pip install --require-hashes` (kræver hashes i låsen).

### 3.6 Filer, der skal læses manuelt

`server/Dockerfile`, `dbadmin/Dockerfile`, `dbnode/*`, `tools/speech-model/Dockerfile`, `tools/setup-android.sh`,
`app/build.sh`, `app/release.sh`, `.github/workflows/ios.yml`, `docker-compose.yml`, `server/constraints.txt`.

---

## 4. Vedligeholdelse

### 4.1 Resumé

Koden er usædvanligt godt kommenteret, og kommentarerne forklarer hvorfor, ikke kun hvad. Strukturen
(`api` → `services` → `dof`) håndhæves af en test. Risiciene ligger i:

- store filer;
- sikkerhedsregler, der er spredt mellem vagten og den enkelte rute;
- dataset-lagets omskrivning af SQL;
- at meget afhænger af, at man husker at kalde det rigtige (fx `dataset.execute` frem for `session.execute`).

### 4.2 Top 10 risici for at noget går i stykker

1. **Vagten i `main.py`** samler login, CSRF, «se som bruger», medieadgang og Mobgang-lyd i én funktion med prefix-regler.
   En ny undtagelse sat det forkerte sted åbner meget. Rettet delvist med `public_api()` og tests.
2. **`dataset.include()`** omskriver SQL med regex (`FROM observations` → UNION med JSON). En ny forespørgsel med et
   usædvanligt alias, eller en DELETE skrevet anderledes, kan omskrives forkert, og en læsning kan stille miste
   DOF.tool-rækkerne.
3. **Glemt `dataset.execute`**: nye lister, der bruger `session.execute` direkte, viser kun DOFbasens data. Ingen test fanger det.
4. **«Se som bruger»** blokerer ændrende metoder i vagten, men GET med sideeffekter skal huskes pr. rute (M-03).
5. **Positionsprivatliv**: reglen «superadmin må ikke se positioner» er implementeret i mange endpoints (`viewing_as`).
   En ny visning med position kan glemme den.
6. **Store filer**: `api/dofbasen.py` (991 linjer), `api/netfugl.py` (952), `dbadmin/app.py` (875), `api/su.py` (679) og
   `services/dofbasen.py` (665) blander transport og forretningslogik.
7. **To positionsplugins** (`DofPosition` på Android, `BackgroundGeolocation` på iOS/ældre) med forskellig semantik i
   `native.js`.
8. **Konfiguration flere steder**: `config.py`, `docker-compose.yml`, `.env`, Dockerfile-ENV (`FORWARDED_ALLOW_IPS` står
   tre steder), `setting_store` i databasen og `features`.
9. **dbrouter og grupper**: tabellens navn afgør databasegruppen. En forespørgsel, der blander grupper, fejler først i
   drift med opdelte databaser, ikke i en udviklingsdatabase.
10. **Route-snapshottet** (`tests/fixtures/api_routes.json`) var ude af trit, så testen fejlede uden at nogen reagerede.
    Opdateret nu.

### 4.3 Anbefalede refaktoreringer (efter værdi i forhold til risiko)

1. Del vagten i `main.py` op i små, navngivne funktioner (`login_gate`, `csrf_gate`, `view_as_gate`, `media_gate`), hver
   med egne tests. Høj værdi, lav risiko.
2. En test, der finder `session.execute(text("… FROM observations …"))` uden for `dataset` og et whitelist-sæt. Høj
   værdi, lav risiko.
3. Ét sted for «hvad superadmin ikke må se i se-som» (positioner, ICS, positionsenhed), som `user_out`, `trip_out`,
   feed og lister bruger. Mellem værdi, lav risiko.
4. Del `api/dofbasen.py` og `api/netfugl.py` op efter underområde. Mellem værdi, mellem risiko: rute-snapshottet sikrer, at
   ruterne er uændrede.
5. Erstat regex-omskrivningen i `dataset.include` med eksplicitte CTE-navne (`obs_all`, `trips_all`), som forespørgslerne
   selv bruger. Høj værdi, høj risiko: mange forespørgsler.

### 4.4 Filer, der mangler kommentarer eller dokumentation

- `docs/drift.md`: mangler afsnit om tilbagerulning, overvågning og logs, og om hvem der ejer driften.
- `dbadmin/app.py`: trusselsmodellen (docker.sock = root) bør stå øverst i filen, ikke kun i compose.
- `README.md`: henviser til `Old Apps/`, som ikke findes på serveren.
- `server/portal/services/dataset.py`: hvilke forespørgselsformer omskrivningen understøtter, og hvilke den ikke gør.

### 4.5 Tests, der bør findes før større ændringer

- Login-vagten for alle ruter: anonym → 401 undtagen whitelisten. En parametriseret test over rute-snapshottet.
- «Se som bruger»: ingen GET-rute committer, når `view_as` er sat (ved at spionere på `session.commit`).
- `dataset.include`: snapshot-tests af omskrivningen for de forespørgselsformer, der findes.
- IDOR: bruger A kan ikke læse, rette eller slette bruger B's ture, meldinger, medier, områder eller regler. Kræver test-DB.
- Rettighedseskalering: en bruger med modulet «brugere» kan ikke give moduler, den ikke selv har, eller gøre nogen til admin.

---

## 5. QA og misbrugstests

### 5.1 Testplan

«Blokerer» = skal bestå før release. ✅ = findes nu (`server/tests/test_security.py` m.fl.).

| Test | Formål | Dækker | Forventet | Hvorfor | Blokerer |
|---|---|---|---|---|---|
| ✅ `test_gammel_klartekst_session_godtages_ikke` | S-01 | `auth.session_user_id` | `None`, nøglen flyttes ikke | Backups må ikke give login | Ja |
| ✅ `test_purge_sletter_klartekst_og_beholder_hashede` | S-01 | `auth.purge_legacy_sessions` | Klartekst væk, hashede urørt, idempotent | Engangsoprydning ved opstart | Ja |
| ✅ `test_destroy_sessions_sletter_hashede` | Kontosletning logger ud | `account.destroy_sessions` | Sessionen gælder ikke | GDPR-sletning og blokering | Ja |
| ✅ `test_loginloft_pr_time_gaelder_uanset_ip` | S-04 | `auth._rate_limit` | 429 trods ny IP | Brute force med skiftende IP | Ja |
| ✅ `test_loginloft_pr_minut` | Eksisterende loft | `auth._rate_limit` | 429 efter 5 | Regression | Ja |
| ✅ `test_public_api_er_praecis` | S-05 | `main.public_api` | Kun præcise stier og præfikser | Vagtens whitelist | Ja |
| ✅ `test_anonym_stoppes_af_vagten_paa_me_underruter` | S-05 | `main.guard` | 401 fra vagten | Lag-på-lag-forsvar | Ja |
| ✅ `test_csrf_vaern_afviser_post_uden_header` | CSRF | `main.guard` | 403 | Cross-site POST | Ja |
| ✅ `test_se_som_skjuler_ics_token_og_positionsenhed` | S-09 | `api.auth.user_out` | Skjult i se-som, synligt for brugeren selv | Privatliv | Ja |
| ✅ `test_dbadmin_samlet_loft_selv_med_skiftende_ip` | S-02 | `dbadmin/app.login` | 429 efter 20 fejl i alt | Root via panel | Ja |
| ✅ `test_dbadmin_loft_pr_ip` | Eksisterende loft | `dbadmin/app.login` | 429 efter 5 | Regression | Ja |
| ✅ `test_position_privacy.*` (findes) | Positioner skjules i se-som, tokens hashes, access-log uden koordinater | `position_privacy`, `api/position` | Som beskrevet | Privatliv | Ja |
| ✅ `test_view_as.*` (findes, kræver test-DB) | Se-som er kun superadmin, kun læsning, udløber | `view_as`, vagten | Som beskrevet | Privilegier | Ja (i CI med DB) |
| ✅ `test_routes` | API-kontrakt | Alle ruter | Snapshot uændret | Utilsigtede ruteændringer | Ja |
| `test_alle_ruter_kraever_login` | Vagten for alle ruter | Snapshot + vagt | Anonym → 401 for alt uden for whitelisten | Ny rute uden dependency | Ja (anbefalet) |
| `test_idor_ture_meldinger_omraader` | BOLA | `api/trips`, `api/reports`, `api/geo`, `api/alerts` | 404 for andres objekter | Personlige positioner | Ja (kræver test-DB) |
| `test_rettighedseskalering` | Rettigheder | `api/admin/brugere` | 403 for moduler man ikke har, og for admin-rolle | Privilegieeskalering | Ja (kræver test-DB) |
| `test_upload_afviser_forkert_type_og_stoerrelse` | Validering | `api/reports.upload_media` | 400 for >15 MB, ukendt type, ulæseligt billede | DoS og indhold | Nej |
| `test_upload_billede_mister_exif` | Privatliv | `content_media.to_webp` | Ingen GPS-EXIF i resultatet | Positionslæk | Ja |
| `test_media_navne_valideres` | Path traversal | `api/media` | 404 for `../`, ugyldige navne | Fillæsning | Ja |
| `test_versioned_traversal` | Path traversal | `main.versioned` | 404 for `/v/x/../../etc/passwd` | Fillæsning | Ja |
| `test_shapes_bbox_graense` | Validering | `api/geo.location_shapes` | 400 for for stort udsnit eller ugyldig bbox | DoS | Nej |
| `test_netfugl_ssrf_blokerer_private` | SSRF | `netfugl._public_host` | Fejl for 10.x, 127.x, ::1 og redirect dertil | Interne tjenester | Ja |
| `test_native_location_bad_token_limit` | Misbrug | `api/position.native_location` | 429 efter 20 ugyldige tokens pr. IP | Token-gætteri | Nej |
| Røgtest efter deploy | Drift | `/api/health`, `/`, login, `/api/me` | 200, CSP-header, cookie Secure | Fejlkonfiguration | Ja |

### 5.2 Minimum før release

Alle ✅-tests ovenfor (kører uden database; `test_security.py` med `-v ./dbadmin:/app/dbadmin`), `test_routes`, og
røgtesten efter deploy. IDOR- og eskaleringstestene kræver en test-DB (`ARTICLES_TEST_DSN`) og bør køres, når den er sat op.

---

## 6. Rettelser

Fundene til rettelse er valgt efter alvor, og efter at rettelsen er lille og sikker: S-01, S-02 (loftet), S-04, S-05,
S-06, S-07, S-08 og S-09. Desuden er to tests, der fejlede i forvejen, gjort grønne.

### 6.1 Ændringer

| Fil | Ændring |
|---|---|
| `server/portal/services/auth.py` | `session_user_id` godtager kun hashede sessioner. Ny `purge_legacy_sessions()`. Loft på 30 loginforsøg pr. obserkode pr. time. |
| `server/portal/main.py` | Sletter klartekst-sessioner ved opstart. `PUBLIC_EXACT`/`PUBLIC_PREFIX` og `public_api()` i stedet for præfiks-tuplen. |
| `server/portal/services/account.py` | `destroy_sessions` sletter kun `sess:h:`-nøgler (klartekst findes ikke længere). |
| `server/portal/api/auth.py` | `VIEW_AS_HIDDEN_PREFS` skjuler `position_device` og `ics_token` i se-som. |
| `server/portal/api/calendar.py` | `GET /api/me/ics` afvises i se-som (opretter ikke et token for målbrugeren). |
| `server/portal/api/social.py` | Supporttråde markeres ikke som læst i se-som. |
| `server/portal/services/storage.py`, `api/media.py`, `api/su.py` | Range-mønsteret flyttet til `storage`, så `api/su.py` ikke importerer fra en anden router (arkitekturtesten). |
| `dbadmin/app.py` | Samlet loft på 20 fejlede logins pr. 5 min. på tværs af IP'er. Glemte IP'er ryddes fra hukommelsen. |
| `app/android/app/src/main/AndroidManifest.xml` | `allowBackup="false"`. |
| `server/constraints.txt` (ny), `server/Dockerfile`, `dbadmin/Dockerfile` | Låste versioner bruges i byggeriet. |
| `server/tests/test_security.py` (ny) | 15 regressionstests (se 5.1). |
| `server/tests/test_position_privacy.py` | Testen af overflytningen af gamle sessioner er erstattet af en test af, at de afvises. |
| `server/tests/fixtures/api_routes.json` | Opdateret med `/api/dofbasen/v1/ibas` (fra en tidligere commit) og `/api/locations/shapes`. |
| `README.md` | Afsnit om afhængigheder og sikkerhed. |
| `.env` (værten, ikke i git) | `chmod 600`. |

### 6.2 Tests

`docker compose run --rm --no-deps -v ./server:/app/server -v ./dbadmin:/app/dbadmin -w /app/server api sh -c "pip install
-q pytest pytest-asyncio; python -m pytest -q --ignore=tests/test_netfugl_foto_parser.py"` → **254 bestået, 117
sprunget over (kræver test-DB), 0 fejl**. Før gennemgangen fejlede 2 tests.

### 6.3 Manuel verifikation efter deploy

1. `docker compose up -d --build api worker dbadmin`.
2. api-loggen skal vise «Slettede 37 sessioner fra før hashingen» første gang og intet de følgende gange. Tjek bagefter
   `valkey-cli --scan --pattern 'sess:*' | grep -vc '^sess:h:'`, som skal give 0.
3. Log ind og ud. Kontrollér, at `/api/me/export` giver 401 uden cookie.
4. dbadmin: fem forkerte kodeord giver 429. Det rigtige virker igen efter 5 minutter.
5. Byg en ny APK. `aapt2 dump xmltree` skal vise `allowBackup=false`.

### 6.4 Tilbageværende risici

S-10, S-11, S-12, S-13, S-14, S-15, S-16 og M-01–M-04 (S-03 er accepteret, og backupfilen fra S-01 er slettet). Desuden:

- **Logges ud:** de 37 brugere med en gammel session logges ud én gang.
- **Ny telefon:** man logger ind igen på en ny telefon (`allowBackup=false`).

---

## 7. Release-vurdering

**Beslutning: ship** (efter deploy af rettelserne; begrænset miljø: den nuværende installation og overdragelse til en
anden udvikler).

### 7.1 Blokerende

Ingen. De tre punkter fra første vurdering er afklaret 2026-11-08:

- `valkey-bak.tgz` er slettet.
- `DBADMIN_PASSWORD` er bekræftet stærkt.
- Port 5440 er bevidst åben bag firewall.

Rettelserne fra trin 6 skal deployes (6.3).

### 7.2 Ikke-blokerende

S-10–S-16, M-02–M-04, stale henvisning til `Old Apps/` i README, manglende afsnit om tilbagerulning og overvågning i
`docs/drift.md`, base-images og Actions uden digest/SHA.

### 7.3 Manuel kontrol

- `COOKIE_SECURE`/`PUBLIC_URL` er https i produktion, og proxyen overskriver `X-Forwarded-*`.
- Firewall på værten: kun 80/443 udefra.
- Ingen hemmeligheder i git: tjekket (`.env`, `google-services.json`, `signing/`, nøgler); historikken er gennemsøgt.
- Ingen TODO/FIXME/HACK i koden (0 fundet). Ingen debug-ruter. FastAPI's fejlsider viser ikke stacktraces.
  `/api/docs` er offentlig (S-14).
- Licenser: afhængighederne er MIT, BSD, Apache, EPL (asyncssh) og MPL (capgo-pluginet). Talemodellen er OpenRAIL-M-afledt
  og nævnt i `app/README.md`. Ingen copyleft-konflikter for en server, der ikke distribueres. APK'en indeholder
  MPL-koden uændret.

### 7.4 Overdragelse

- **Kom i gang**: `README.md` → «Kom i gang» og «Udvikling». Drift: `docs/drift.md`. App: `app/README.md`.
- **Tests**: se 6.2. 117 tests kræver en Postgres med testdata (`ARTICLES_TEST_DSN`).
- **Deploy**:
  - Frontend (`web/`) er mountet og live med det samme.
  - Server-kode kræver `docker compose up -d --build api worker` (en genstart er ikke nok).
- **Tilbagerulning**: `git checkout <forrige commit> && docker compose up -d --build api worker`. Databaseskemaet udvides kun
  (ingen destruktive migreringer). Appen: installér den forrige APK fra GitHub-releases (samme signeringsnøgle).
- **Logs**: `docker compose logs api worker` (roteres, 5 × 10 MB). Drift-status under Admin → Drift. Der er ingen
  ekstern overvågning eller alarmering.
- **Hemmeligheder**:
  - `.env` på værten (600).
  - `app/signing/` (signeringsnøgle; der skal tages backup, ellers kan appen ikke opdateres).
  - `app/android/app/google-services.json` (genskabt fra den forrige APK).
  - dbadmins registry (`/data/registry.db` i volumen `dbadmin`) med databasetokens.
- **Ejerskab**: ikke dokumenteret. Skriv, hvem der ejer drift, DOFbasen-aftalen og signeringsnøglen.

### 7.5 Næste opgaver for en ny vedligeholder

1. Indsnævr `FORWARDED_ALLOW_IPS` til proxyens adresse, og bind api'en til 127.0.0.1 eller proxy-nettet.
2. SSRF-hook i artikelimporten (S-12). Ratebegrænsning på skrivende endpoints (S-13).
3. Test af login-vagten for alle ruter, og IDOR-tests med test-DB i CI.
4. Afsnit om tilbagerulning, overvågning og ejerskab i `docs/drift.md`.
5. Fastgør base-images på digest og Actions på SHA. Verificér checksums i `setup-android.sh`.
6. Del vagten i `main.py` op (4.3, punkt 1).
