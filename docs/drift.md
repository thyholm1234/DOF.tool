# Drift og arkitektur

Detaljer om opsætning, databaser, objektlager og hensynet til dofbasen.dk. Overblikket står i [README](../README.md).

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

## Arkitektur

```
docker compose
├── postgres  (PostGIS 17)  – al data: observationer (1960'erne →), lokaliteter, kommunepolygoner, brugere …
├── valkey    (open source-afløseren for Redis) – sessioner, gateway-kø, cache, scoreboards, pub/sub til live-opdateringer
├── api       (FastAPI)  – REST + server-sent events + statisk PWA (web/)
├── worker    – DOFbasen-gateway, planlagte opgaver, arkivimport, fænologi-beregninger
├── rustfs    – objektlager (S3) til fotos og lyd i observationer; leveres med login via /media/obs/<navn>
└── dbadmin   – superadmin-panel til databaserne (se Databaser): noder, tokens, IP og port
```

**Objektlager.** Filer uploadet til feltmeldinger og ture gemmes i RustFS (`rustfs/rustfs`, Apache-2.0; bucket
`doftool-media`), ikke på disken. RustFS afløste MinIO, som ikke længere udgiver images og blev arkiveret i 2026.
Nøglerne er `RUSTFS_ACCESS_KEY` og `RUSTFS_SECRET_KEY` (de gamle `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` virker stadig).
**Fra MinIO:** efter opdateringen køres `tools/minio-to-rustfs.sh` én gang; det starter den gamle MinIO, kopierer alle
objekter over og stopper den igen (volumen `minio` slettes ikke af scriptet). Appen taler almindelig S3, så en anden
tjeneste (Garage, SeaweedFS, Backblaze B2 …) kan tage over via `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` og
`S3_BUCKET`. Eksisterende filer under `/data/media/obs` flyttes til lageret første gang workeren
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
