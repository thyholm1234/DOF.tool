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

### Hensyn til dofbasen.dk: højst ét kald i minuttet

**Alt**, der går til dofbasen.dk, går gennem én kø i Valkey og udføres af worker-processen
(`server/portal/dof/gateway.py`). Der går altid mindst 60 sekunder fra ét kald er færdigt til det
næste starter. Det gælder live-hentning, login-tjek, referencedata, historik og billeder. Grænsen kan
ikke sættes lavere end 60 sekunder (`DOF_MIN_INTERVAL`), og den håndhæves med et globalt tidsstempel
og en lås, så den også holder på tværs af genstarter.

Prioritering pr. ledigt minut:
1. interaktive job (første login, billeder/lyd som en bruger har bedt om)
2. live-hentning af dagens observationer
3. øvrige job (i går hver time, sidste 7 dage om natten, referencedata kl. 3, huller i data).
   Er der ventende job, skiftes der mellem live og kø, så feedet opdateres mindst hvert 2. minut.

Hvad sparer kald:
* Én dagsliste dækker *alle* moduler: notifikationer, lister, scoreboards og fænologi læser samme data.
* Historik kommer fra det lokale arkiv (`dofbasen_ÅÅÅÅ_MM.csv`), der importeres automatisk.
* Login tjekkes kun hos DOFbasen første gang (eller ved ændret kode); derefter bruges en argon2-hash.
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

Superadmin oprettes ud fra `SUPERADMIN_OBSERKODE`/`SUPERADMIN_PASSWORD` ved hver opstart.

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

**Indtast (feltmeldinger).** Ture (lokalitet + dato) med observationer i DOFbasens felter: art, antal,
adfærd, køn, alder, dragt, metode, obs.-tider, position med radius, fuglenoter, hemmelig, foto/lyd.
Kodelister bygges fra DOFbasen-data. Portalen tilføjer **status** (er fuglen under observation? – andre
kan melde "stadig til stede", "fløjet" eller "ikke genfundet", og trådens følgere får besked) og
**sikkerhed** (sikker/sandsynlig/mulig + grundlag: set, hørt, foto, lyd, i hånden, beskrevet; samt
*hemmelig* = kun synlig for indtasteren og *skjul præcis position*). Meldingerne klassificeres som
DOFbasen-data, vises i de samme tråde og kører gennem de samme alarmregler. De sendes **ikke** til
dofbasen.dk; brug CSV-eksporten (DOFbasens kolonnenavne) eller indtast også i DOFbasen – meldingen
markeres, når fundet dukker op dér.

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
