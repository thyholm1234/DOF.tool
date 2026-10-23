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

## Begrænsninger

* Push-notifikationer (Web Push) virker ikke i en WebView. Alarmer ses i indbakken, når appen er åben; rigtig push til
  appen kræver Firebase (FCM) og et native push-plugin.
* Position kræver Google Play-tjenester på telefonen.
