// Bro til Android-appen (Capacitor): position fra Android selv, også når skærmen er slukket.
// I browseren falder alt tilbage til navigator.geolocation, så resten af portalen ikke skal vide, hvor den kører.
import { api, state, toast } from "./core.js";

const cap = () => window.Capacitor;
export const isApp = () => !!cap()?.isNativePlatform?.();

/** Pluginnet BackgroundGeolocation (@capgo/background-geolocation). Portalen indlæses fra serveren i stedet for at ligge i
 *  appen, så der er ingen plugin-pakke her: vi kalder broens egne funktioner, som appen injicerer på portalens adresse. */
const PLUGIN = "BackgroundGeolocation";
const bg = {
  available: () => isApp() && typeof cap().nativeCallback === "function",
  start: (options, callback) => cap().nativeCallback(PLUGIN, "start", options, callback),
  stop: () => cap().nativePromise(PLUGIN, "stop"),
};

// Android viser kun én tilladelsesdialog ad gangen: en samtidig anmodning (fx position og notifikationer ved opstart)
// kan blive afvist uden dialog. Derfor køres alle anmodninger efter hinanden.
let permChain = Promise.resolve();
const serial = (fn) => { const p = permChain.then(fn, fn); permChain = p.catch(() => {}); return p; };

/** Diagnose: appen melder trinene i push- og positionsopsætningen til serveren (vises under Alarmer → Push & enheder). */
export function diag(step, detail) {
  if (!state.user) return;
  api("/api/push/diag", { method: "POST", body: { step, detail: detail == null ? null : String(detail).slice(0, 480) } }).catch(() => {});
}

const round6 = (x) => Math.round(x * 1e6) / 1e6;
const fromNative = (l) => ({ lat: round6(l.latitude), lon: round6(l.longitude), accuracy: l.accuracy ?? null, ts: Math.round((l.time || Date.now()) / 1000) });
const fromWeb = (p) => ({ lat: round6(p.coords.latitude), lon: round6(p.coords.longitude), accuracy: p.coords.accuracy ?? null, ts: Math.round(p.timestamp / 1000) });

/** Én position: { lat, lon, accuracy, ts }. */
export function currentPosition({ precise = false, timeout = 15000 } = {}) {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error("Enheden understøtter ikke positionsbestemmelse"));
    navigator.geolocation.getCurrentPosition((p) => resolve(fromWeb(p)),
      (e) => reject(new Error(e.code === 1 ? "Tilladelse til position er afvist" : "Position ikke tilgængelig: " + e.message)),
      { enableHighAccuracy: precise, timeout, maximumAge: precise ? 5000 : 60000 });
  });
}

// ---------------------------------------------------------------- fælles positionsstrøm
// Android-tjenesten kan kun køre én gang, så ruteoptagelse og positionsdeling abonnerer på den samme strøm.
// Tjenesten kører som forgrundstjeneste med en synlig notifikation, så Android ikke lukker den ned i baggrunden.

const subs = new Map();  // id → { onPoint, onError, distance }
let running = null;      // konfigurationen tjenesten er startet med (JSON)
let nativeDelivery = null;  // { url, headers } når positionsdelingen er slået til
let chain = Promise.resolve();

const dispatch = (fn) => { for (const s of [...subs.values()]) { try { fn(s); } catch { /* ét abonnement må ikke vælte de andre */ } } };

function reconcile() {
  chain = chain.then(() => serial(async () => {
    const opts = subs.size ? {
      backgroundTitle: "DOF.tool bruger din position",
      backgroundMessage: subs.has("route") ? "Optager din rute" : "Opdaterer Nær mig – dine alarmer følger dig",
      requestPermissions: true, stale: false, distanceFilter: Math.min(...[...subs.values()].map((s) => s.distance)),
      // Positionsdeling: Android sender selv positionen til serveren (højst hvert minut) – også når appen er lukket
      ...(nativeDelivery ? { url: nativeDelivery.url, headers: nativeDelivery.headers, minIntervalMs: 60_000, networkFallback: true } : {}),
    } : null;
    const key = opts && JSON.stringify(opts);
    if (key === running) return;
    if (running !== null) { try { await bg.stop(); } catch { /* ignore */ } running = null; }
    if (!opts) return;
    running = key;
    try {
      await new Promise((resolve) => {
        let first = true;
        bg.start(opts, (loc, err) => {
          if (first) { first = false; resolve(); }
          if (err) {
            if (err.code === "ALREADY_STARTED") return;
            running = null;
            diag("position-fejl", `${err.code || ""} ${err.message || ""}`);
            dispatch((s) => s.onError?.({ code: err.code === "NOT_AUTHORIZED" ? 1 : 2, message: err.message || String(err.code) }));
            return;
          }
          if (loc) dispatch((s) => s.onPoint(fromNative(loc)));
        });
        setTimeout(resolve, 15000);  // tilladelsesdialogen er besvaret, eller tjenesten kører
      });
    } catch (e) {
      running = null;
      dispatch((s) => s.onError?.({ code: 2, message: e.message || "Positionstjenesten kunne ikke startes" }));
    }
  }));
  return chain;
}

/** Abonnér på positioner. Returnerer en funktion der stopper abonnementet. distance = mindste flytning i meter mellem punkter. */
export function watchPosition(id, { distance = 10, precise = true } = {}, onPoint, onError) {
  if (bg.available()) {
    subs.set(id, { onPoint, onError, distance });
    reconcile();
    return () => { if (subs.delete(id)) reconcile(); };
  }
  const w = navigator.geolocation.watchPosition((p) => onPoint(fromWeb(p)), (e) => onError?.({ code: e.code, message: e.message }),
    { enableHighAccuracy: precise, maximumAge: 5000, timeout: 30000 });
  return () => navigator.geolocation.clearWatch(w);
}

// ---------------------------------------------------------------- positionsdeling til 'Nær mig'

let stopShare = null;
const SHARE_MIN_M = 100;

export async function sendPosition(p, source = "app") {
  await api("/api/me/location", { method: "POST", body: { lat: p.lat, lon: p.lon, accuracy_m: p.accuracy, source } });
}

/** Start eller stop deling af baggrundsposition efter brugerens valg (profilen) – kaldes ved login/logout og ændring.
 *  Androids positionstjeneste sender selv positionen med et positionstoken, så Nær mig-områderne (og alarmerne) følger
 *  brugeren, også når appen er lukket. */
export async function syncSharing() {
  const want = isApp() && !!state.user?.position && state.user?.prefs?.share_location === true;
  if (want && !stopShare) {
    stopShare = () => {};  // optaget, mens tokenet hentes
    try {
      const { token } = await api("/api/me/location/token", { method: "POST" });
      nativeDelivery = { url: `${location.origin}/api/location/native`, headers: { Authorization: `Bearer ${token}`, "X-Requested-With": "fetch" } };
    } catch (e) { diag("position-token-fejl", e.message); }
    // første position med det samme, så områderne flytter sig nu og ikke først efter et minut
    stopShare = watchPosition("share", { distance: SHARE_MIN_M, precise: false }, (p) => {
      if (!nativeDelivery) sendPosition(p).catch(() => {});
    }, (err) => { toast(err.code === 1 ? "Nær mig kræver tilladelse til position. Slå den til i Android-indstillinger." : "Position: " + err.message, "err"); });
    diag("position-deling", nativeDelivery ? "native" : "via appen");
  } else if (!want && stopShare) {
    nativeDelivery = null;
    stopShare(); stopShare = null;
  }
}

// ---------------------------------------------------------------- push (Firebase) til appen
// Web Push findes ikke i en WebView. Appen får i stedet et Firebase-token (plugin @capacitor/push-notifications), som
// sendes til serveren; serveren sender via FCM (services/fcm.py). Samme bro-teknik som positionen: broens egne kald.

const PUSH = "PushNotifications";
const LOCAL = "LocalNotifications";
const pn = (method, options) => cap().nativePromise(PUSH, method, options);
const ln = (method, options) => cap().nativePromise(LOCAL, method, options);
let pushOn = null;     // { token, handles }
export let pushError = null;  // seneste fejl fra Firebase/Android, vises under Alarmer → Push & enheder

const goto = (url) => { if (url) location.hash = String(url).replace(/^.*#/, "#"); };

/** Er push slået til i appen? "on" | "off" | "denied" | "error" | "unsupported" (appen er bygget uden push-pluginet). */
export async function appPushStatus() {
  if (!isApp() || typeof cap().nativePromise !== "function") return "unsupported";
  try {
    const { receive } = await pn("checkPermissions");
    if (receive === "denied") return "denied";
    if (pushOn?.token) return "on";
    return pushError ? "error" : "off";
  } catch { return "unsupported"; }
}

/** Android viser ikke selv en notifikation, mens appen er åben – så vis den som lokal notifikation. */
async function showForeground(n) {
  try {
    let { display } = await ln("checkPermissions");
    if (display !== "granted") ({ display } = await serial(() => ln("requestPermissions")));
    if (display !== "granted") throw new Error("ikke tilladt");
    await ln("schedule", { notifications: [{ id: Math.floor(Math.random() * 2_000_000_000), title: n.title || "DOF.tool", body: n.body || "",
      channelId: "alerts", extra: { url: n.data?.url } }] });
  } catch { toast(`${n.title || "DOF.tool"}: ${n.body || ""}`); }
}

/** Bed om tilladelse, hent token fra Firebase og meld det til serveren. */
export async function enableAppPush() {
  if (pushOn) {  // allerede startet: er der intet token endnu, så prøv at registrere igen
    if (!pushOn.token) { pushError = null; await pn("register"); }
    return;
  }
  const cur = { token: null, handles: [] };
  pushOn = cur;
  pushError = null;
  const fail = (msg) => { pushOn = null; pushError = msg; diag("push-fejl", msg); cur.handles.forEach((x) => x?.remove?.()); throw new Error(msg); };
  diag("push-start", `bro: ${typeof cap()?.nativePromise}, lyttere: ${typeof cap()?.addListener}`);
  try {
    cur.handles.push(cap().addListener(PUSH, "registration", (t) => {
      cur.token = t.value;
      pushError = null;
      diag("push-token", `${String(t.value).slice(0, 12)}… (${String(t.value).length} tegn)`);
      api("/api/push/fcm", { method: "POST", body: { token: t.value, device: "Android-app" } })
        .then(() => diag("push-registreret"))
        .catch((e) => { pushError = "Serveren afviste enhedens token: " + e.message; diag("push-fejl", pushError); });
    }));
    cur.handles.push(cap().addListener(PUSH, "registrationError", (e) => {
      pushError = e?.error || "Firebase kunne ikke registrere enheden (tjek google-services.json og Google Play-tjenester)";
      diag("push-fejl", "registrationError: " + pushError);
      toast("Push kunne ikke aktiveres: " + pushError, "err");
    }));
    // Tryk på en notifikation: gå til siden i beskeden
    cur.handles.push(cap().addListener(PUSH, "pushNotificationActionPerformed", (a) => goto(a?.notification?.data?.url)));
    cur.handles.push(cap().addListener(PUSH, "pushNotificationReceived", (n) => showForeground(n)));
    cur.handles.push(cap().addListener(LOCAL, "localNotificationActionPerformed", (a) => goto(a?.notification?.extra?.url)));
    try { await pn("createChannel", { id: "alerts", name: "Alarmer", description: "Alarmer om fugle i dine områder", importance: 5, visibility: 1, vibration: true }); } catch { /* findes allerede */ }
    let { receive } = await pn("checkPermissions");
    if (receive !== "granted") ({ receive } = await serial(() => pn("requestPermissions")));
    diag("push-tilladelse", receive);
    if (receive !== "granted") fail("Notifikationer er ikke tilladt for appen. Slå dem til i Android-indstillinger.");
    await pn("register");
    diag("push-register-kaldt");
  } catch (e) {
    if (pushOn === cur) fail(e.message || "Push kunne ikke slås til");
    throw e;
  }
}

/** Fjern enhedens token fra serveren (ved logout eller når push slås fra). Skal kaldes, mens brugeren stadig er logget ind. */
export async function stopAppPush() {
  const cur = pushOn;
  if (!cur) return;
  pushOn = null;
  pushError = null;
  cur.handles.forEach((x) => x?.remove?.());
  if (cur.token) await api("/api/push/fcm/unregister", { method: "POST", body: { token: cur.token } }).catch(() => {});
  try { await pn("unregister"); } catch { /* ignore */ }
}

/** Slå push til i appen for den indloggede (hvis brugeren ikke har slået det fra) – kaldes ved login/start. */
export function syncPush() {
  const want = isApp() && !!state.user && state.user.prefs?.push_enabled !== false;
  if (want && !pushOn) enableAppPush().catch(() => { /* brugeren kan slå det til under Alarmer → Push & enheder */ });
  else if (!want && pushOn) stopAppPush();
}
