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
└── worker    – DOFbasen-gateway, planlagte opgaver, arkivimport, fænologi-beregninger
```

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

**Indtast (feltmeldinger).** Modulet *Mine indtastninger* viser egne fugle som én liste med sortering
(dato/tid, art, systematik, antal, lokalitet, kategori) og filtre. *Ny observation*: vælg en DOFbasen-lokalitet
og dato; derefter art (dropdown med dine seneste arter), antal, tidspunkt og adfærd (påkrævet), noter, fuglens
position på kortet, køn/alder/dragt/metode og foto/lyd. Afkrydsning af DOF's retningslinjer *Når du ser en
sjælden fugl* er påkrævet for SU/SUB-arter.
Kodelister bygges fra DOFbasen-data. Portalen tilføjer **status** (er fuglen under observation? – andre
kan melde "stadig til stede", "fløjet" eller "ikke genfundet", og trådens følgere får besked) og
**sikkerhed** (sikker/sandsynlig/mulig + grundlag: set, hørt, foto, lyd, i hånden, beskrevet; samt
). Meldingerne klassificeres som
DOFbasen-data, vises i de samme tråde og kører gennem de samme alarmregler. De sendes **ikke** til
dofbasen.dk; brug CSV-eksporten (DOFbasens kolonnenavne) eller indtast også i DOFbasen – meldingen
markeres, når fundet dukker op dér.

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
observationer i området i hele databasen (beregnes hver nat pr. afdeling, kommune og eget område).

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
