# Funktioner i detaljer

Modul for modul. Overblikket står i [README](../README.md).

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
* **GPS-rute.** Start/pause/stop under *Redigér tur → Turens data*; punkter gemmes løbende på enheden og sendes med turen
  (kun synlig for den, der har indtastet). *Afslut tur* (rød, øverst ved *Redigér tur*) stopper og beskærer ruten.
* **Offline.** Kladder gemmes løbende på enheden (IndexedDB). Uden forbindelse lægges turen i en kø med fotos og lyd og
  sendes automatisk, når der er net igen; et fælles `client_id` forhindrer dubletter, hvis et svar går tabt. Arter,
  kodelister (adfærd m.m.), turopslag, seneste lokaliteter og alle lokaliteter (`/api/trips/offline/locations`, hentes
  ugentligt) ligger på enheden, så søgning og *nærmeste lokaliteter* virker uden net. De seneste 14 dages ture
  (`/api/trips/recent`) gemmes også, så de kan åbnes og rettes offline.
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

**Moderation** (rolle *moderator* og op; eget menupunkt ved siden af Admin med fanerne Kommentarer, Nyheder, Ture og Arter – listekategorierne ligger under Arter → Kategorier). Fanen Kommentarer har tre underfaner: *Kommentarer* (skjul/vis,
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


## Artikler (`#/artikler`)

Selvstændigt modul bag funktionsflaget `artikler` (standard: forhåndsvisning). Beskrivelse og beslutninger: `docs/artikler-modul-spec.md`.

* **Import af netfugl.dk-arkivet** fra en zip under Artikler → Admin → Import (upload i dele, kan genoptages; kan køres igen uden dubletter;
  artikler rettet i portalen overskrives ikke). Kommentarer fra netfugl.dk vises som arkiv, kun til læsning.
* **Avisforside** med hovedhistorie, billeder (PDF-artikler viser side 1), seneste, mest læst/kommenteret, «på denne dag» og arter i fokus.
* **Søgning** på ord (dansk stemming) eller præcis tekst (også delord og OCR-tekst fra PDF'er), emneord, arter, år, type og forfatter.
* **Arter** findes automatisk i alle artikler (emneord, tekst og PDF-tekst); redaktører kan rette dem. Boks «Artikler om arten» på artssiden.
* **Kommentarer** med anmeld-knap, automatisk skjulning ved flere anmeldelser, moderatorkø (Admin → Moderation → Artikelkommentarer) og udelukkelser.
* **Notifikationer** om nye artikler og om kommentarer på artikler, man selv har kommenteret (og svar på ens kommentarer).
* **Markdown-editor** med overskrifter, tabeller, billeder, gallerier, faktabokse, PDF-vedhæftning, indlejring fra godkendte værter, arter,
  planlagt publicering og revisioner. Samme editor bruges til **portalens nyheder** (Admin → Indhold).
