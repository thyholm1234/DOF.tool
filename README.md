# DOF.tool – feltornitologernes portal

Én PWA med fælles login og fælles database, der samler de tidligere apps:

| Tidligere app | I DOF.tool |
|---|---|
| **DOF.not** (notifikationer) | *Nu*-feedet med tråde, kort, kommentarer, følg tråd, push-notifikationer, stille timer |
| **Boligbirding / Fugleliga** | *Lister*: årslister, matrikellister (`#BB26`, `#BB26-2`), afdelinger, kommuner, egne områder, scoreboards, kapløbsgraf, matrix, unikke arter, grupper |
| **Trækfugl** | *Træk*: tidligere/senere end normalt, ankomster, trækvejr (DMI), klima |
| (nyt) | *Alarmer* for afdeling, kommune, lokalitet, tegnet polygon eller radius – SU, SUB, Bemærk, **lokale hits** (< N obs. i området) eller alle |
| (nyt) | *Læring*: kurser med multiple choice (billede/lyd), spaced repetition og datadrevne quizzer |

## Arkitektur

```
docker compose
├── postgres  (PostGIS 17)  – al data: observationer (1960'erne →), lokaliteter, kommunepolygoner, brugere …
├── valkey    (open source-afløseren for Redis) – sessioner, gateway-kø, cache, scoreboards, pub/sub til live-opdateringer
├── api       (FastAPI)  – REST + server-sent events + statisk PWA (web/)
├── worker    – DOFbasen-gateway, planlagte opgaver, arkivimport, fænologi-beregninger
├── minio     – objektlager (S3) til fotos og lyd i observationer; leveres med login via /media/obs/<navn>
└── dbadmin   – superadmin-panel til databaserne (se Databaser): noder, tokens, IP og port
```

**Objektlager.** Filer uploadet til feltmeldinger og ture gemmes i MinIO (bucket `doftool-media`), ikke på disken.
MinIO udgiver ikke længere færdige images, og projektet blev arkiveret i 2026, så `minio/Dockerfile` bygger den sidste
udgave (RELEASE.2025-10-15T17-29-55Z) fra kildekoden – den får ingen sikkerhedsopdateringer. Appen taler almindelig S3,
så en anden tjeneste (Garage, SeaweedFS, Backblaze B2 …) kan tage over via `S3_ENDPOINT`, `S3_ACCESS_KEY`,
`S3_SECRET_KEY` og `S3_BUCKET`. Eksisterende filer under `/data/media/obs` flyttes til lageret første gang workeren
starter. Lagerforbruget for databaser, objektlager, filer og Valkey kan følges under **Admin → Drift → Lagerplads**.

### Databaser: opdeling, replikaer og backup

Som standard bruger alt den ene `postgres`-container (`DBADMIN_SERVICE_TOKEN` skal være sat i `.env`). Efter
migreringen i panelet deler portalen i stedet data i to **grupper** af Postgres-noder på vilkårlige servere:

* **Brugerdata** (`users`): brugere, lister, push, kommentarer, kalendere, DOFbasen-/Netfugl-indhold …
* **Observationer** (`observations`): observationer, ture, aggregater, vejr og DKU-status.
* Referencedata (arter, lokaliteter, kommuner …) skrives i observationsgruppen og kopieres hver time til brugergruppen
  (`dbops.sync_reference`), så joins mod dem stadig er lokale.

Hver gruppe har én **primary** (skrivning) og valgfrit flere **replikaer** (streaming-replikering på andre servere). Hver
replika har et formål: *aflaster læsning* (tager læsetrafik, hvis "Læs fra replikaer" er slået til for gruppen) eller *kun
backup* (levende kopi, bruges aldrig til læsning; kan skiftes når som helst). **API'et er load balanceren**: det henter
topologien fra db-admin hvert 15. sekund, sender hver sætning til den gruppe tabellen hører til (`dbgroups.py`), skriver
til primary, læser fra replikaer (en session der har skrevet læser resten fra primary) og springer en replika over, der
ikke svarer. En sætning må ikke blande tabeller fra begge grupper – `python3 tools/sql_groups.py` tjekker det.

**db-admin** (`http://localhost:8100`, eget login med `DBADMIN_PASSWORD`, uafhængigt af portalen – virker også når en
database er nede, så adressen kan rettes):

1. *Tilføj node*: navn, gruppe, primary/replika, IP og port. Panelet genererer et **token** – det er databasens adgangskode –
   og et script, der indsættes i en terminal på serveren og opretter og starter noden (bygger image med replikeringsopsætning, hvis det mangler).
2. Start noden. Replikaer tager selv en basebackup af deres primary første gang.
3. *Gem adresse* ændrer IP/port; API'et følger med uden genstart. Panelet viser status og replikaforsinkelse.
4. *Gør til primary* promoverer en replika i registret (kør `pg_ctl promote` på noden; kommandoen vises).

**Første opsætning og migrering sker i panelet** (kortet *Opsætning og migrering*): panelet opretter selv en brugerdata- og en
observations-database som containere (kræver `/var/run/docker.sock`, som compose monterer), stopper api og worker, kopierer
alle data fra den nuværende samlede database, kontrollerer rækkeantal pr. tabel, slår opdelingen til og starter api/worker igen.
Fejler noget, bruger portalen stadig den gamle database. Den gamle database røres ikke og kan bruges til *Tilbage til samlet
database*. Uden Docker-adgang oprettes noderne manuelt (*Tilføj node*), og panelet flytter dataene. Kommandolinje:
`docker compose run --rm api python -m portal.dbmigrate --from <dsn>` (kun når opdelingen allerede er slået til).

`docker compose -f dbnode/compose.dev.yml up -d --build` giver en lokal prøve-klynge (bruger-DB, obs-DB + replika).
Begrænsninger: skrivning går til gruppens primary (ingen automatisk failover), og skrivning på tværs af grupper i én
forespørgsel er ikke atomisk (ingen 2PC). Data deles *mellem* grupperne, ikke endnu *inden for* en gruppe (sharding).

### Hensyn til dofbasen.dk: højst ét udtræk i minuttet

**Alt**, der går til dofbasen.dk, går gennem gatewayen i worker-processen
(`server/portal/dof/gateway.py`). Der er to baner:

* **Udtræk** (live-hentning, referencedata, historik, måneder): én kø i Valkey, og der går
  altid mindst 60 sekunder fra ét kald er færdigt til det næste starter. Grænsen kan ikke sættes lavere
  end 60 sekunder (`DOF_MIN_INTERVAL`), og den håndhæves med et globalt tidsstempel og en lås, så den
  også holder på tværs af genstarter. Eneste undtagelse er knappen *Hent nu* ved "Dagen i dag" under **Admin → Data**:
  den henter dagens liste med det samme én gang (højst hvert 15. sekund).
* **Opslag** (login-tjek samt billeder, lyd og DKU-status for én observation): små kald, der ikke trækker data ud af
  DOFbasens database. De har deres egen kø og udføres med det samme, højst fire ad gangen, uden at vente på
  minimumsintervallet og uden at påvirke det. Login er begrænset til fem forsøg i minuttet pr. obserkode og pr. IP.

Prioritering af udtrækkene pr. ledigt minut:
1. live-hentning af dagens observationer
2. øvrige job (i går hver time, sidste 7 dage om natten, referencedata kl. 3, huller i data).
   Er der ventende job, skiftes der mellem live og kø, så feedet opdateres mindst hvert 2. minut.

Hvad sparer kald:
* Én dagsliste dækker *alle* moduler: notifikationer, lister, scoreboards og fænologi læser samme data.
* Historik kommer fra det lokale arkiv (`dofbasen_ÅÅÅÅ_MM.csv`), der importeres automatisk.
* Kommunetilhørsforhold beregnes med kommunepolygoner i PostGIS, ikke med opslag hos DOFbasen.

Status, kø og log kan ses under **Admin → Drift**.

## Kom i gang

```bash
cp .env.example .env        # ret adgangskoder, superadmin og stien til arkivet
docker compose up -d --build
```

Åbn `http://localhost:8090` (porten styres af `PORT`). Første opstart:

1. Databasen oprettes, referencedata (arter, SU/SUB, bemærk-tærskler, fænologivinduer, 99 kommuner)
   indlæses fra `server/portal/seed/`.
2. Worker importerer alle månedsfiler i `DOF_ARCHIVE_DIR` (≈ 30 mio. observationer tager ca. en halv time)
   og genberegner derefter fænologi og lokale artstal. Fremdrift ses under Admin → Drift.
3. Live-hentningen går i gang med det samme.

Superadmin sættes ud fra `SUPERADMIN_OBSERKODE` ved hver opstart og logger ind med sin DOFbasen-adgangskode.
Portalen gemmer intet om adgangskoder (heller ikke hashes): hvert login tjekkes hos DOFbasen.

### Push-notifikationer

Web Push kræver HTTPS (undtagen `localhost`). Sæt portalen bag en reverse proxy med TLS
(fx Caddy/Traefik/nginx) og sæt `COOKIE_SECURE=true` og `PUBLIC_URL`. VAPID-nøgler genereres
automatisk og gemmes i databasen. På iPhone skal appen føjes til hjemmeskærmen før push virker.

## Funktioner i detaljer

**Nu (feed).** Dagens observationer samlet i tråde (art + lokalitet + dag). Filtre: SU, SUB+, Bemærk+,
*Lokale hits* og *Alle* (kræver område). Område: hele landet, lokalafdeling, egne kommuner og områder.
Opdateres live via server-sent events. Dagens største træklokaliteter. *Tæt på mig* bruger din position.

**Tråd.** Alle observationer med adfærd, dragt, tider, noter; kort; kommentarer i realtid; følg tråden
for besked om hver ny observation; billeder/lyd fra DOFbasen hentes på forespørgsel (køes, caches 7 dage).

**Indtast (ture og feltmeldinger).** Modulet *Mine indtastninger* følger app.dofbasen.dk, men gemmer i portalens egen
database. Alt er mærket *Indtastet i DOF.tool* (kontra DOFbasen) og sendes **ikke** til dofbasen.dk.

* **Ny tur** (`#/indtast/tur/ny`): dato, tid fra/til, lokalitet (søg, nærmeste til din position, seneste), metode, komplet
  artsliste, spontan, medobservatører, turnoter og vejr (temperatur, skydække, vind, nedbør, sigtbarhed). Under turen:
  *observationer* (art, antal + optællingsmetode, adfærd – kan genbruges til næste –, markeringer som kalder/burfugl/syg,
  køn/alder/dragt, position på kort, sikkerhed og grundlag, hemmelig, foto/lyd, status for SU/SUB og DOF's
  retningslinjer), *ynglepar* og *forstyrrelser*. Kort over lokalitet, rute og fund.
* **GPS-rute.** Start/pause/stop; punkter gemmes løbende på enheden og sendes med turen (kun synlig for den, der har indtastet).
* **Offline.** Kladder gemmes løbende på enheden (IndexedDB). Uden forbindelse lægges turen i en kø med fotos og lyd og
  sendes automatisk, når der er net igen; et fælles `client_id` forhindrer dubletter, hvis et svar går tabt.
* **På vegne af en anden observatør.** Obserkoden skal være kendt (portalbruger eller i DOFbasen-data); observationerne
  registreres på den, men vises som indtastet af dig.
* **Hurtig observation** (`Alle fugle`): den oprindelige én-fugl-formular med sortérbar liste over alle egne fugle.

API: `/api/trips` (opslag, liste, hent, opret, ret, slet – hele turen i ét kald, børn matches på `id`/`client_id`).
Tabeller: `field_trips`, `field_reports` (observationer), `field_breeding_pairs`, `field_disturbances`.
**Status på SU/SUB-tråde.** På alle tråde med en SU- eller SUB-art – fra DOFbasen eller portalen – kan alle
indloggede melde "stadig til stede", "fløjet" eller "ikke genfundet" (samme dag eller dagen efter). Hver melding
skal bekræftes, kan trækkes tilbage af den, der meldte den, og trådens følgere får besked. Den seneste melding
vises på tråden og i feedet.
Kodelister bygges fra DOFbasen-data (metoder, optællingsmetoder og forstyrrelser fra `seed/trip_lookups.json`). Portalen tilføjer **status** (er fuglen under observation?) og
**sikkerhed** (sikker/sandsynlig/mulig + grundlag: set, hørt, foto, lyd, i hånden, beskrevet; samt
). Meldingerne klassificeres som
DOFbasen-data, vises i de samme tråde og kører gennem de samme alarmregler. De sendes **ikke** til
dofbasen.dk; brug CSV-eksporten (DOFbasens kolonnenavne) eller indtast også i DOFbasen – meldingen
markeres, når fundet dukker op dér.

**Android-app og Nær mig.** `app/` er en Capacitor-app (APK) om portalen med native positionstjeneste, så GPS-ruten
også optages med slukket skærm. Med *Del min position i baggrunden* (Profil) sender appen sin position, og *Nær mig* i
feedet (eller en alarmregel med området) gælder en radius, der følger brugeren (`areas.ref = 'me'`, flyttes af
`POST /api/me/location`). Funktionen kan slås fra under Admin → Funktioner (`position`). Push til appen sendes via Firebase (`FCM_SERVICE_ACCOUNT_B64`). Se `app/README.md` for byg og opsætning.

**Alarmer der følger dig.** I en alarmregel kan området være *📍 Min position – følger mig* med en radius (1–100 km). Reglen bruger et Nær mig-område pr. radius (`areas.ref = 'me'`). I appen slås positionsdeling til, og Androids positionstjeneste sender selv positionen (højst hvert minut, også når appen er lukket) til `POST /api/location/native` med et positionstoken, der kun kan melde en position. Hver ny position flytter områderne, så notifikationerne gælder, hvor brugeren er. Under Alarmer → *Push & enheder* vises appens diagnose (tilladelser, Firebase-token, fejl), som appen melder til `POST /api/push/diag`.

**Nær mig uden område.** Under *Nær mig* i feedet kan man vælge *Brug min position som filter*: enhedens position er midtpunkt for en radius (2–100 km), og der oprettes ingen lokation eller noget område. Feedet kalder `scope=near:breddegrad,længdegrad,km`, og positionen hentes igen højst hvert 2. minut.

**Testbrugere.** Admin → Brugere → *Opret testbruger* laver en konto (`TEST0001`, …) med lokal adgangskode (scrypt-hash i
`users.test_pw_hash`), der logger ind uden DOFbasen – til at afprøve appen. Testbrugere vises ikke på scoreboards, og
adgangskoden vises kun ved oprettelse/nulstilling.

**Fænologi-filter.** Niveauet *Fænologi* i feedet (og som valg i alarmregler) viser arter, der ses uden for deres normale
forekomstperiode. Perioden pr. art ligger i `species_season` og beregnes natligt (`services/seasons.py`): har DOFbasen ét
bemærk-vindue for arten, er resten af året normalt; ellers bruges det korteste stykke af året (også over nytår), der rummer
99 % af de dage arten er set de seneste 15 år, plus 5 dages margen. Arter set hele året og arter med for få data (< 60 dage
eller < 5 år) er aldrig fænologi-fund. Tråden viser den normale periode.

**Tommel op og kommentarer.** Man kan give tommel op til en fugl (tråden: art + lokalitet + dag) og til
kommentarer. Feedet viser antal, tråden "X, Y og N andre", og et klik viser alle. Fuglens ejere (portalbrugere
med observationer i tråden eller egne feltmeldinger) får notifikation om tommel op og nye kommentarer; trådens
følgere får besked om kommentarer, og kommentarens forfatter om tommel op.

**Billeder.** Store billeder skaleres i browseren før upload: længste side højst 2048 px, JPEG 60 %
(indtastning og e-læring). GIF og lyd sendes uændret.

**Feedback og support.** Brugere skriver til administratorerne (feedback, hjælp, fejl, idé) og får svar
i samme samtale. Side, appversion og browser sendes med. Admins får notifikation om nye beskeder,
brugeren om svar (Admin → Support).

**Aktivitet.** Sidevisninger (højst én pr. side pr. minut) og handlinger (login, feltmelding, kommentar,
like, supportbesked) logges og vises under Admin → Aktivitet: aktive brugere, mest besøgte sider,
handlinger og den enkelte brugers forløb. Slettes efter 180 dage.

**Datapanel og opdateringsplaner** (Admin → Data / Vejr, inspireret af DML-Bird-Populations). Et år ×
måned-gitter viser for hver måned antal observationer og hvornår den sidst er hentet komplet
(månedsudtræk, arkivfil eller alle dage enkeltvis); klik henter måneden igen. For vinduerne *dagen i dag,
de sidste 3 dage, den sidste uge, den sidste måned, det sidste år* og *alle data* vælges, om og hvor ofte
de hentes igen. DOFbasen: i dag = live-intervallet, 3 dage/uge = dagsudtræk, måned/år/alle = månedsudtræk –
alt gennem gatewayen (ét kald i minuttet), og panelet regner belastningen ud.

**Vejrarkiv.** DMI's 10 km-grid (Det Danske Kvadratnet) hentes som månedsfiler fra
`opendataapi.dmi.dk/v2/climateData/bulk/10kmgridValue` (≈0,5 GB, én ad gangen, ingen nøgle), parses
direkte fra zip i flere processer (kun ønskede timeparametre; dubletter afgøres af `calculatedAt`) og
gemmes pr. kvadrat og dansk døgn: temperatur (middel/min/maks), vind, vindstød, nedbør, skydække, solskin
og morgenen kl. 4–10 (vektormiddel af vindretning). Lokaliteter får deres kvadrat (`locations.cell10`),
og tråde viser "Vejret den dag". Samme planer som for DOFbasen; zip-filerne slettes efter parsing
(volumen `weather`).

**Login.** Hele portalen kræver login: uden session vises kun forsiden med login, og API'et svarer 401
(undtagen login, `/api/me`, sundhedstjek og personlige ICS-feeds).

**DOF-ture** (`#/ture`). Kommende ture og aktiviteter hentes fra dof.dk (oversigtens `DOFKommendeTure` + hver
turs side, med pause mellem kaldene) flere gange i døgnet. Hver tur kodes med lokalafdeling (fra arrangøren),
kommune (GPS i kommunepolygon) og lokalitet (DOFbasen-lokalitet med navnematch inden for 5 km, ellers nærmeste
inden for 2 km). Filtre: mine afdelinger, afdeling, kommune, type; sortering efter dato eller afstand; kort;
ICS-download pr. tur.

**Kalendere for lokaliteter** (`#/kalender`) – planlægning af indsats på trækposter. Én fælles kalender pr.
lokalitet; hver bruger kan oprette højst 5, alle kan se og bruge dem. Oversigten sorteres efter næste aktivitet,
afstand eller navn. Månedsvisning: tryk på en dag, se hvem der kommer, og meld dig til med forventet tidsrum,
offentligt eller privat, medobservatører, note og evt. telefonnummer fra profilen. Andre med offentlige planer
samme dag får besked. Kalenderen viser trækvejr (DMI HARMONIE for punktet, morgen kl. 5–12), DOF-ture på stedet og
"typisk på denne tid" (arter set ±10 dage omkring datoen de seneste 15 år). *Mine planer* viser egne besøg uge for
uge. Abonnement i Google/Outlook/Apple via personlig ICS-adresse (egne planer eller en kalender).

**Kategorier** (samme regler som DOF.not): SU/SUB fra artsklassifikationen → bemærk-vindue
(sommer-/vinterforekomst) → afdelingens bemærk-tærskel. **Lokalt hit**: arten har færre end N
estimerede fund gennem alle år på lokaliteten, observationen kommer fra. Dage med arten på lokaliteten med højst 7 dages
mellemrum er ét fund (højst 1 dag for trækkende fugle). Tallene genberegnes én gang om måneden, én lokalafdeling ad gangen
(Admin → Drift → Planlagte opgaver → Fordeling). En alarmregel kan i stedet tælle i hele sit område (summen af fundene
på områdets lokaliteter).

**Notifikationer.** En tråd udløser regel-notifikationer, når den er ny, eller når max-antallet stiger.
Nye observationer i en eksisterende tråd går kun til dem der følger den. Egne observationer og tråde
man selv har observeret i, giver aldrig besked. Alt lander i indbakken (klokken), push respekterer
stille timer.

**Lister.** Første fund pr. art (underarter tæller som nominatarten; sp./hybrider/tamformer tæller ikke).
Scopes: Danmark, matrikel 1/2 (tag i tur- eller fuglenoter med årets to cifre), lokalafdeling,
kommune, egne områder; år eller livsliste. Scoreboards caches i Valkey og invalideres når nye
observationer fra deltagere kommer ind.

**Matrikler** (Lister → Matrikler, som i Boligbirding). Matrikel 1 (#BB26 / #BB26-1) tæller på
scoreboards; matrikel 2+ (#BB26-2 …) er private. Matrikler kan navngives og have perioder (adresse,
fra, evt. til). *Flyt matrikel* afslutter perioden dagen før flyttedatoen og starter en ny – listen og
scoreboardet tæller kun den aktive periode, så fremgangen nulstilles ved flytning; tidligere perioder og
"alle perioder" kan vælges på listen. Uden perioder tæller alle fund med tagget.

**Træk & fænologi.** Indsatskorrigeret rapporteringsrate pr. dag (andel af alle ture med arten),
sæsonens 10/50/90 %-datoer pr. år, Theil–Sen-trend med Kendalls τ, "i år"-forskydning ved
kurvetilpasning, sammenhæng med DMI's forårstemperatur. Trækvejr fra DMI HARMONIE for klassiske
træksteder med enkle tommelfingerregler. Se fanen *Metode* i appen.

**Læring.** Redaktører (rolle *editor*) opretter kurser og spørgsmål med billede/lyd-upload.
Forkerte svar kommer igen (Leitner-kasser). Fire automatiske quizzer genereres af portalens egne data.

**Moderation** (rolle *moderator* og op). Admin → Moderation har tre faner: *Kommentarer* (skjul/vis,
skriv til brugeren, udeluk), *Beskeder* (samtaler startet af moderatorer – de ligger i brugerens
Feedback og support, og alle moderatorer kan svare) og *Udelukkede*. En udelukkelse (som DOF.not's
blacklist) har en påkrævet årsag, varighed (1/7/30 dage eller indtil videre) og kan skjule brugerens
kommentarer for andre, mens den gælder. Moderatorer opretter også portalens egne ture under Ture.
Kommentarspor opdateres live (SSE): ved enhver ændring henter åbne tråde den aktuelle version.

**DOFbasen (showcase)** (`#/dofbasen`). En ny udgave af dofbasen.dk på portalens backend og data – tro mod det
nuværende layout (blå header, fanemenu med undermenuer, grå sektionsbjælker, Verdana 11 px), men responsiv. Forside
(kort i dag/i går, observationer pr. dag, statistik, Om DOFbasen, Fokus, Nyt på DOFbasen, sjældne fugle, Danmarks fugle),
dagens observationer (visning, dato, sortering, summering, afdeling), søgning med alle DOFbasens filtre (tabel, kort,
CSV; DOFbasens link-parametre virker), fænologi (artsside, første iagttagelse, samme dato, artskort), alle opslagslister,
tekstsider, nyheder og popups for observation, lokalitet og observatør (uden adresse/telefon). Tekster og lister er
kopieret fra dofbasen.dk som eksempel (`seed/dofbasen/content.json`, bygget med `tools/dofbasen_seed/`) – indholdet
tilhører DOF. Modulet har eget admin-panel og egne roller (redaktør, listeansvarlig, DKU, API-ansvarlig, administrator);
med rollen kan man redigere direkte på siderne. REST-API til egne apps: `/api/dofbasen/v1` (Bearer-token, kun læsning,
åben CORS, grænse pr. minut) – en administrator giver adgang, brugeren laver selv tokens på sin profil.
Alt kan slås fra, vises som forhåndsvisning (kun superadmin og DOFbasen-roller) eller slås til under **Admin → Funktioner**
(kun superadmin). Se `docs/dofbasen-showcase.md`.

**Netfugl (showcase)** (`#/netfugl`). En ny udgave af netfugl.dk i Netfugls layout, integreret med DOFbasen: krydslisterne
(DK, årsarter, månedsarter, lokalarter) beregnes automatisk fra brugernes observationer og suppleres med manuelle kryds
(VP, ringmærkning, ædelarter); fotos kan knyttes til en observation og vises så også i DOFbasen-modulet; DK-listens
artssider viser alle danske fund. Desuden nyheder og artikler med kommentarer, fotogalleri med likes og moderation,
RSS fra danske og udenlandske kilder, trækvejr fra DMI, brugerprofiler, søgning, eget admin-panel, egne roller og
API (`/api/netfugl/v1`). Alt kan slås fra/til under **Admin → Funktioner**. Se `docs/netfugl-showcase.md`.

## Udvikling

```bash
docker compose run --rm --no-deps -v ./server:/app/server -w /app/server api \
  sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q"
```

`web/` er mountet ind i api-containeren, så frontend-ændringer kræver ikke rebuild.
Backend-ændringer: `docker compose up -d --build api worker`.

```
server/portal/
  dof/        gateway (rate limit), jobs (ét kald hver), parse, classify, ingest (COPY), archive, reference
  services/   feed, alerts, push, lists, areas (PostGIS), phenology, dmi, learning, auth
  api/        FastAPI-routere
  seed/       referencedata, kommunepolygoner, kursusindhold
web/          PWA uden build-trin (ES-moduler), Leaflet vendoreret
```

De gamle apps ligger i `Old Apps/` (inkl. `DOF.tool-v0.1`) til reference.
