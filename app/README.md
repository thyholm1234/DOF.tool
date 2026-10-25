# DOF.tool som Android-app (Capacitor)

Appen er en indpakning om portalen: den åbner portalens adresse i en WebView (login-cookien er førsteparts, og siderne
opdateres med serveren), men position kommer fra Android selv via pluginnet
[`@capgo/background-geolocation`](https://github.com/Cap-go/capacitor-background-geolocation) (Google Play-lokation +
forgrundstjeneste med synlig notifikation), så den virker med slukket skærm.

* **Ruteoptagelse** (Indtast → tur → Start rute) bruger den native tjeneste i appen og `navigator.geolocation` i browseren.
* **Nær mig**: slår man *Del min position i baggrunden* til under Profil, sender appen sin position (højst hvert minut,
  og kun når man har flyttet sig >100 m eller efter 10 min.). Serveren flytter brugerens *Nær mig*-område med, så feed og
  alarmregler gælder en radius om, hvor man er. Positionen kan slettes ved at slå delingen fra.
* Kode: `web/js/native.js` (bro), `server/portal/api/position.py` (API).

## Byg APK

```bash
DOF_SERVER_URL=https://portal.example.dk ./build.sh      # → dist/DOF-tool.apk
```

* Første gang hentes Node, JDK 21 og Android SDK til `~/.local/share/dof-android` (`../tools/setup-android.sh`).
* Uden `DOF_SERVER_URL` bruges `PUBLIC_URL` fra `../.env`; er den `localhost`, bruges maskinens LAN-adresse.
  `http://`-adresser tillades (klartekst) – brug `https://` til produktion.
* APK'en signeres med `signing/dof-tool.jks` (oprettes første gang, ligger ikke i git). **Tag backup af `signing/`** –
  uden nøglen kan en ny version ikke installeres oven på en gammel.
* Installér: `adb install -r dist/DOF-tool.apk`, eller send filen til telefonen og tillad installation fra ukendte kilder.

## Tale-indtastning (talemodel på telefonen)

Tale-indtastningen på ture (Admin → Funktioner → «Tale-indtastning») genkender talen på telefonen med CoRal-projektets
danske model `roest-v3-wav2vec2-315m`, kørt med ONNX Runtime (MIT) i pluginnet `DofAsr`. Lyden forlader ikke telefonen,
og der bruges ingen Google-tjenester. Modellens output afkodes i `web/js/voice-ctc.js` mod artslisten, tallene og
adfærdslisten, så kun ord derfra kan komme ud.

Modellen (int8, ca. 355 MB) ligger ikke i APK'en: den bygges på serveren (volumen `models`, leveres fra `/models/`), og
appen tilbyder at hente den første gang en tur åbnes. En ny model på serveren tilbydes automatisk. Byggeriet køres af
compose-servicen `speech-model` (`tools/speech-model/agent.py`) på kommando fra Admin → Drift (boksen «Talemodel til
tale-indtastning», eller «Kør nu» under Worker-opgaver); workeren starter det også ugentligt, men en model bygget af samme
revision springes over. `tools/speech-model/build.sh` gør det samme i hånden. Byggeriet kræver ca. 6,5 GB fri hukommelse; har
serveren mindre, bygges modellen på en anden maskine, pakkes med `tools/speech-model/pack.sh` og lægges på serveren med
`tools/speech-model/install.sh speech-model.tar`.

## Push via Firebase (FCM)

Web Push findes ikke i en WebView, så appen bruger Firebase. Appen henter et token (`@capacitor/push-notifications`),
melder det til `POST /api/push/fcm`, og serveren sender alarmer via FCM HTTP v1 (`server/portal/services/fcm.py`) –
samme alarmregler, stille timer og push til/fra som for browser-push. Tryk på en notifikation åbner den rigtige side.

Mens appen er åben viser Android ikke selv notifikationen, så appen viser den som en lokal notifikation
(`@capacitor/local-notifications`). Under Alarmer → *Push & enheder* vises status og fejl, og *Send testbesked* fortæller,
om serveren mangler nøglen, om Firebase afviste beskeden, eller om ingen enhed er registreret.

Opsætning (engangs, kræver en Google-konto):

1. [console.firebase.google.com](https://console.firebase.google.com) → opret projekt → *Tilføj app* → Android med
   pakkenavnet **`com.chfotofilm.doftool`**. Hent **`google-services.json`** og læg den i `app/android/app/` (ligger ikke i git).
2. Projektindstillinger → *Tjenestekonti* → *Generér ny privat nøgle*. Sæt filen i serverens `.env` som base64:
   `FCM_SERVICE_ACCOUNT_B64=$(base64 -w0 nøgle.json)` og genstart `api` og `worker` (`docker compose up -d api worker`).
3. Byg appen igen (`./build.sh`). Under Alarmer → *Push & enheder* kan man sende en testbesked.

Uden trin 1 bygges appen uden push (den viser en fejl, når man forsøger at slå push til); uden trin 2 modtager serveren
tokens, men sender ikke.

## Begrænsninger

* Position og push kræver Google Play-tjenester på telefonen.

## iOS

iOS-projektet ligger i `ios/` (CocoaPods). Det kan kun bygges på macOS med Xcode, så det bygges af GitHub Actions
(`.github/workflows/ios.yml`) ved push til `app/` eller manuelt under *Actions → iOS → Run workflow*:

* **simulator** – oversætter appen uden signering og viser, at projektet bygger. Kræver ingen hemmeligheder.
* **ipa** – signeret `DOF-tool.ipa` som artefakt (og evt. TestFlight), når signeringshemmelighederne er sat.

Appen gør det samme som på Android: portalen fra serveren, positionen fra iOS (Core Location, også i baggrunden med
tilladelsen *Altid*) og push via Firebase. iOS stopper positionen, hvis brugeren lukker appen helt (det kan ingen app
komme uden om); ligger den bare i baggrunden, følger Nær mig-alarmerne med.

Engangsopsætning (kræver Apple Developer Program, 99 USD/år):

1. **Apple Developer** → Identifiers: App ID `com.chfotofilm.doftool` med *Push Notifications*. Keys: opret en
   **APNs-nøgle** (.p8). Certificates: et *Apple Distribution*-certifikat (eksportér som .p12). Profiles: en
   *App Store*- (eller *Ad Hoc*-) profil for app-id'et.
2. **Firebase** → Projektindstillinger → *Cloud Messaging* → Apple-app-konfiguration: upload APNs-nøglen.
   *Tilføj app* → iOS med bundle-id `com.chfotofilm.doftool`, hent `GoogleService-Info.plist`.
3. **GitHub** → Settings → Secrets and variables → Actions:
   * Secrets: `IOS_CERT_P12_B64`, `IOS_CERT_PASSWORD`, `IOS_PROFILE_B64`, `IOS_TEAM_ID`, `GOOGLE_SERVICE_INFO_PLIST_B64`
     (alle filer som `base64 -w0 fil`). Til TestFlight også `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8_B64`
     (App Store Connect → Users and Access → Integrations → API-nøgle).
   * Variables: `DOF_SERVER_URL` (standard `https://doftool.chfotofilm.dk`), `IOS_EXPORT_METHOD` (`app-store-connect`
     eller `ad-hoc`), `IOS_TESTFLIGHT=true` for upload.

Serveren skal ikke ændres: iPhone får et Firebase-token som Android, og afsendelsen (`services/fcm.py`) sender også
iOS-felterne (lyd, prioritet, samme tag).
