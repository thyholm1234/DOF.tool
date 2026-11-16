# Spec: ruteoptagelsen må ikke kunne køre videre uden en tur (strømforbrug)

Skrevet 2026-11-16. Målgruppe: den, der udfører ændringerne (ingen forhåndsviden om samtalen forudsat).

## Problemet

Android-appen (SM-S911B) brugte på 8 timer: 3 t 41 min GPS, 550 vækninger, 43 min vækningslåse og 14 min CPU – med kun
20 min skærmtid.

## Hvad der er undersøgt

Appen bruger Googles fused-positionstjeneste (`FusedLocationProviderClient`) i en forgrundstjeneste
(`app/android/app/src/main/java/dk/dof/tool/DofPositionService.java`). Tjenesten har to tilstande:

| Tilstand | Forespørgsel | Strøm |
|---|---|---|
| Rute (`route=true`) | `PRIORITY_HIGH_ACCURACY`, 5 s, ≥ 5 m, i baggrunden samlet pr. 60 s | GPS tændt hele tiden, ca. én vækning i minuttet |
| Kun Nær mig (`share=true`) | `PRIORITY_BALANCED_POWER_ACCURACY`, 3–5 min, ≥ 150 m, samlet pr. 10 min | ingen GPS |

Nær mig-delingen opfører sig som beskrevet: serverloggen for 16/11 viser 19 afsendelser til `/api/location/native` på
12 timer, og efter dagens tur kommer de i 8–10-minutters klumper (delingens samling). Den er **ikke** årsagen.

Årsagen er, at **rute-tilstanden kan blive hængende uden en tur**. Forløbet natten til 16/11 (lokal tid, fra API-loggen):

- 01:55:51 tur 38 oprettes i appen (ved «Gem tur» tilbydes/startes GPS-ruten, `web/js/views/tur-form.js:179-186`)
- 01:58:00 tur 38 slettes med «Slet tur»
- 08:39:11 appen startes forfra (ny JS-indlæsning) og beder tjenesten om kun Nær mig → GPS slukker
- 08:50–09:25 tur 40 med rute (35 min, tilsigtet GPS)

Det passer med målingen: GPS fra vinduets start til 08:39 plus turens 35 min, og ca. én vækning i minuttet i rute-tilstand.
**Ikke bevist:** at brugeren svarede ja til GPS på tur 38 (turen er slettet, så sporet er væk). Hullerne nedenfor findes
dog i koden uanset, og hvert af dem giver præcis dette billede.

### Hullerne

1. **«Slet tur» stopper ikke optagelsen.** `web/js/views/tur-form.js:192-195` sletter turen på serveren og lokalt, men
   kalder ikke `trackStop()`. `tracker` (`web/js/tur-data.js:67`) lever videre med abonnementet `"route"`, så tjenesten
   bliver i rute-tilstand. («Annullér» på en ny tur stopper korrekt, linje 197.)
2. **Tjenesten opdager ikke, at appen er fjernet.** `DofPosition.handleOnDestroy()` kalder `dropRoute()`, men Android
   kalder ikke `onDestroy`, når appen stryges væk fra oversigten. Tjenesten har ingen `onTaskRemoved`, så `route=true`
   og GPS'en fortsætter i en proces uden WebView – ingen modtager punkterne.
3. **En frisk WebView rydder ikke en hængende rute.** Ved ny indlæsning er `subs` tom i `web/js/native.js`. Er Nær mig
   slået til, retter første `reconcile()` tilfældigvis tjenesten (`route:false`); er Nær mig slået fra, sendes intet, og
   en hængende rute fortsætter på ubestemt tid.
4. **Logout stopper ikke optagelsen** (`web/js/app.js:285`).
5. **Ingen øvre grænse.** En glemt rute (turen aldrig afsluttet) optager, til appen lukkes.

## Ændringer

Hold ændringerne kirurgiske: ingen omstrukturering, ingen nye afhængigheder, kommentarer på dansk i samme stil som
resten. Rør ikke iOS (`app/ios`) eller Nær mig-tilstandens tal.

### A. Web (virker straks for alle, også uden ny APK)

Web-mappen er live-monteret i produktion – arbejd i et git-worktree, ikke direkte i `/DOF.tool/web`.

**A1. `web/js/views/tur-form.js` – «Slet tur»:** stop optagelsen, før turen kasseres. I `onclick` på linje 192-195,
efter bekræftelsen og før `store_.discard()`:

```js
if (tracker.cid === T.client_id) trackStop();
```

Læg den, så den også kører, når serverkaldet fejler *efter* at turen er slettet lokalt – enklest: som første linje i
`try`-blokken. (`tracker` og `trackStop` er allerede importeret i filen.)

**A2. `web/js/app.js` – logout:** kald `trackStop()` (importér fra `./tur-data.js`) i `logout()` før API-kaldet.

**A3. `web/js/tur-data.js` – øvre grænse:** i `trackStart` får `tracker` et starttidspunkt (`tracker.since = Date.now()`,
kun når optagelsen faktisk startes – ikke ved gentaget kald for samme tur). I `saver`-intervallet (kører hvert 20. s)
tilføjes først i funktionen:

```js
if (tracker.watch != null && Date.now() - tracker.since > TRACK_MAX_MS) { trackPause(); return; }
```

med `const TRACK_MAX_MS = 12 * 3600_000;`. `trackPause()` (ikke `trackStop()`), så de optagede punkter bliver på turen og
kan gemmes. Nulstil `since` i `trackStop()` og tilføj feltet i `tracker`-objektets startværdi.

**A4. `web/js/native.js` – ryd en hængende rute ved indlæsning:** en frisk indlæst side har ingen optagelse i gang, så
tjenesten må ikke være i rute-tilstand. Tilføj til `bg`:

```js
dropRoute: () => cap().nativePromise(POS, "dropRoute"),
```

og kald den én gang ved modulindlæsning, lagt i samme kø som `reconcile()`, så den ikke kan overhale en senere start:

```js
if (bg.available() && own()) chain = chain.then(() => bg.dropRoute()).catch(() => {});  // ældre APK'er har ikke metoden
```

Linjen skal stå efter erklæringen af `chain`. Tjek at `reconcile()` stadig bygger videre på `chain` (den gør
`chain = chain.then(...)`).

**A5. Diagnose:** i `reconcile()` i `web/js/native.js` kaldes `diag("position-tilstand", …)` med `"rute"`,
`"naer-mig"` eller `"stoppet"`, hver gang tjenesten omstilles (dvs. lige efter `running`-tjekket, når nøglen er ny).
Så kan forløbet næste gang læses i serverloggen (`Push-diagnose bruger …`). Kun ved omstilling – ikke pr. punkt.

### B. Android (kræver ny APK)

**B1. `DofPosition.java` – ny pluginmetode `dropRoute`:**

```java
@PluginMethod
public void dropRoute(PluginCall call) {
    DofPositionService.dropRoute();
    call.resolve();
}
```

**B2. `DofPosition.java` – ryd ved oprettelse:** overskriv `load()` og kald `DofPositionService.dropRoute()`. Pluginnet
oprettes sammen med aktiviteten; en ny aktivitet har en ny WebView uden optagelse, så en rute i tjenesten er forældreløs.

**B3. `DofPositionService.java` – `onTaskRemoved`:**

```java
/** Appen er strøget væk fra oversigten: Android kalder ikke aktivitetens onDestroy, så ruten droppes her. */
@Override
public void onTaskRemoved(Intent rootIntent) {
    listener = null;
    dropRoute();
    super.onTaskRemoved(rootIntent);
}
```

`dropRoute()` findes allerede (linje 107-115): den slukker GPS'en og lukker tjenesten, medmindre Nær mig er slået til –
så fortsætter den i den billige tilstand. Tjek at den er sikker at kalde, når `instance` er `null` (det er den i dag).

**B4. `DofPositionService.java` – færre vækninger under en rute i baggrunden:** `ROUTE_BATCH_HIDDEN_MS` hæves fra
`60_000L` til `300_000L` (5 min). Punkterne samles af positionschippen og leveres samlet; ruten bliver lige nøjagtig,
men processoren vækkes en femtedel så ofte. Konsekvens, som skal med i klassens kommentar og `app/README.md`: lukkes
appen hårdt i baggrunden, kan op til 5 min af ruten gå tabt (før 1 min).

**B5. Dokumentation:** opdatér klassekommentaren i `DofPositionService.java` og punkterne «Ruteoptagelse» / «Strøm i
øvrigt» i `app/README.md` med de nye regler (rute droppes, når appen fjernes eller turen slettes; højst 12 timer;
samling 5 min i baggrunden).

### C. «Tillad altid» til Nær mig (kræver ny APK + web)

**Baggrund.** Manifestet erklærer kun `ACCESS_FINE_LOCATION`/`ACCESS_COARSE_LOCATION`, så Android tilbyder kun «Mens
appen bruges». Nær mig virker i dag, fordi forgrundstjenesten startes, mens appen er åben. Lukker Android processen,
eller genstartes telefonen, kan tjenesten ikke få position fra baggrunden (`promote()` fejler → `shutdown()`), og
alarmfilteret står stille, til appen åbnes. Med `ACCESS_BACKGROUND_LOCATION` kan tjenesten genopstå af sig selv.

Tilladelsen er **valgfri**: uden den virker alt som i dag. Den bruges kun til Nær mig – ruteoptagelse kræver den ikke og
må aldrig bede om den.

**Læs først** Androids dokumentation for de to regelsæt, før der kodes (reglerne er ændret i Android 11, 12, 14 og 15):
«Request background location» og «Restrictions on starting a foreground service from the background» på
developer.android.com. Afviger dokumentationen fra antagelserne her, så følg dokumentationen og skriv afvigelsen i
afleveringen.

**C1. `AndroidManifest.xml`:** tilføj

```xml
<!-- Nær mig, når appen ikke er åben (valgfri: «Tillad altid»). Uden den virker delingen kun, mens tjenesten lever -->
<uses-permission android:name="android.permission.ACCESS_BACKGROUND_LOCATION" />
<!-- Nær mig-delingen genstartes efter opstart af telefonen (DofBootReceiver) -->
<uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
```

og modtageren `DofBootReceiver` (`exported="false"` er ikke nok til BOOT_COMPLETED på alle versioner – brug
`android:exported="true"` med intent-filter kun for `android.intent.action.BOOT_COMPLETED`).

**C2. `DofPosition.java` – tilladelsen og to nye metoder.** Tilføj aliaset `backgroundLocation`
(`ACCESS_BACKGROUND_LOCATION`) til `@CapacitorPlugin(permissions = …)`.

- `backgroundStatus()` → `{ state: "granted" | "denied" | "prompt" | "unsupported" }`. `unsupported` under Android 10
  (API 29), hvor tilladelsen ikke findes (dér gælder almindelig position også i baggrunden – behandl som `granted` i
  tjenesten).
- `requestBackground()` → beder om tilladelsen og svarer med samme form. Den **skal** bedes om alene og først, når
  almindelig position er givet (ellers afviser Android uden dialog): er `location` ikke `GRANTED`, afvis med
  `NOT_AUTHORIZED`. På Android 11+ sender systemet selv brugeren til indstillingssiden, hvor «Tillad altid» vælges.

`start()` ændres ikke og beder aldrig om baggrundstilladelsen.

**C3. `DofBootReceiver.java` (ny):** ved `BOOT_COMPLETED`: er `share` sat i `SharedPreferences("dof_position")`, og er
baggrundstilladelsen givet, startes tjenesten med `ContextCompat.startForegroundService(ctx, new Intent(ctx,
DofPositionService.class))` (uden extras → tjenestens eksisterende genstartsgren læser url og token fra prefs). Alt i
`try/catch` – en fejl må ikke vælte opstarten. Ingen tilladelse eller ingen deling → gør intet.

**C4. `DofPositionService.java`:** genstartsgrenen i `onStartCommand` findes allerede (`restart == true` → kun Nær mig
fra prefs). Tilføj en hjælper `hasBackgroundPermission(ctx)` (sand under API 29) og brug den i genstartsgrenen: mangler
tilladelsen, så `shutdown()` med det samme uden at forsøge `promote()`. `promote()` beholder sin `try/catch`. Ingen
vagthund, ingen alarmer, ingen wakelock.

**C5. `web/js/native.js`:** to eksporterede funktioner, der kalder pluginmetoderne og tåler en gammel APK (kaldet afvises
→ `"unsupported"`): `backgroundPositionStatus()` og `requestBackgroundPosition()` (sidstnævnte gennem `serial(...)`
ligesom de øvrige tilladelsesanmodninger). Meld udfaldet med `diag("position-baggrund", state)`.

**C6. Hvor der spørges (web).** Kun som følge af en brugerhandling – aldrig ved opstart:

- Når brugeren slår deling til eller lader positionen følge denne enhed (kaldet til `takeOverPosition()` i
  `web/js/views/profile.js`): er status `prompt`/`denied`, vis først en forklaring med `ask(...)` og bed derefter om
  tilladelsen. Tekst: «Skal Nær mig også følge dig, når appen er lukket? Så kan dine alarmer følge med, selv efter
  telefonen har været genstartet. Vælg «Tillad altid» på næste side. Positionen bruges kun til dine egne Nær
  mig-områder og afrundes til ca. 100 m.» Svarer brugeren nej, spørges der ikke igen automatisk.
- Samme sted i profilen (ved delingens indstilling, kun i appen og kun når denne enhed følges): en statuslinje
  «Følger dig også, når appen er lukket» eller «Følger dig kun, mens appen kører» med knappen «Tillad altid», der
  kalder `requestBackgroundPosition()`. Følg sidens eksisterende mønstre for rækker og knapper – ingen ny styling.

**C7. Dokumentation:** nyt punkt i `app/README.md` under Nær mig, og en linje om, at udgivelse på Google Play kræver
særskilt godkendelse af baggrundsposition (i dag deles APK'en uden om Play).

## Uden for opgaven

- At erstatte forgrundstjenesten med positionsopdateringer via `PendingIntent` (muligt med «Tillad altid», men en
  større omlægning).
- Nær mig-tilstandens intervaller og serverens `/api/location/native`.
- At fjerne `@capgo/background-geolocation` fra Android-bygget (bruges stadig af iOS og ældre APK'er).
- WebView'ens egne `navigator.geolocation.getCurrentPosition`-kald (enkeltopslag med timeout – ikke en kilde til
  vedvarende GPS).

## Verifikation

Web (A): kør portalen i en engangscontainer og tjek i browseren (her bruges `navigator.geolocation`, men `tracker` er den
samme):

1. Opret en tur i dag, sig ja til GPS → `tracker.watch != null`. Åbn «Ret tur» → «Slet tur» → `tracker.cid === null` og
   `tracker.watch === null`.
2. Start en rute, log ud → samme.
3. Sæt `tracker.since` 13 timer tilbage i konsollen → inden for 20 s er `tracker.watch === null`, og punkterne står
   stadig på turen.
4. `tools/e2e/smoke.py` kører som før.

Android (B), på en telefon med `adb`:

```bash
adb shell dumpsys location | grep -A3 com.chfotofilm.doftool   # aktive forespørgsler: HIGH_ACCURACY = rute
adb shell dumpsys activity services com.chfotofilm.doftool     # kører DofPositionService?
```

1. Nær mig til, start en rute → `HIGH_ACCURACY`. Slet turen → skifter til `BALANCED` inden for få sekunder.
2. Start en rute, stryg appen væk fra oversigten → `BALANCED` (Nær mig til) eller ingen tjeneste (Nær mig fra).
3. Nær mig fra, start en rute, tving WebView'en til genindlæsning (eller dræb aktiviteten med «Behold ikke aktiviteter»)
   → tjenesten stopper, når appen åbnes igen.
4. Afslut en tur normalt («Afslut tur») → uændret adfærd, ruten er på turen.
5. Del C: slå deling til i profilen → forklaring → indstillingssiden; vælg «Tillad altid» → statuslinjen skifter.
   `adb shell am kill com.chfotofilm.doftool` (eller genstart telefonen) → tjenesten kører igen kort efter, og
   `/api/location/native` får stadig positioner. Uden «Tillad altid»: uændret adfærd, ingen nedbrud ved opstart.
   Genstarten efter proceslukning afhænger af Android-versionens regler og **skal** prøves på en rigtig telefon.
6. Lad telefonen ligge natten over med Nær mig til og tjek batteristatistikken: GPS-tid ≈ 0 uden for ture.

## Rækkefølge og levering

A1 alene lukker det hul, der efter alt at dømme ramte i nat; A2-A5 og B er sikkerhedsnet. A og B kan leveres hver for
sig – A4 tåler en gammel APK (kaldet afvises og ignoreres), og en ny APK tåler gammel web. C bygger oven på B og har
både en Android- og en web-del; web-delen tåler en gammel APK. Én commit pr. del (A web, B Android, C «Tillad altid»)
med dansk commit-besked i depotets stil.
