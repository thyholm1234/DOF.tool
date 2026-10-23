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
let running = null;      // distanceFilter tjenesten er startet med
let chain = Promise.resolve();

const dispatch = (fn) => { for (const s of [...subs.values()]) { try { fn(s); } catch { /* ét abonnement må ikke vælte de andre */ } } };

function reconcile() {
  chain = chain.then(async () => {
    const want = subs.size ? Math.min(...[...subs.values()].map((s) => s.distance)) : null;
    if (want === running) return;
    if (running !== null) { try { await bg.stop(); } catch { /* ignore */ } running = null; }
    if (want === null) return;
    running = want;
    try {
      bg.start({
        backgroundTitle: "DOF.tool bruger din position",
        backgroundMessage: [...subs.keys()].includes("route") ? "Optager din rute" : "Opdaterer Nær mig-filteret",
        requestPermissions: true, stale: false, distanceFilter: want,
      }, (loc, err) => {
        if (err) {
          if (err.code === "ALREADY_STARTED") return;
          dispatch((s) => s.onError?.({ code: err.code === "NOT_AUTHORIZED" ? 1 : 2, message: err.message || String(err.code) }));
          return;
        }
        if (loc) dispatch((s) => s.onPoint(fromNative(loc)));
      });
    } catch (e) {
      running = null;
      dispatch((s) => s.onError?.({ code: 2, message: e.message || "Positionstjenesten kunne ikke startes" }));
    }
  });
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
let lastSent = null;  // { lat, lon, at }
const SHARE_EVERY_MS = 60_000, SHARE_REFRESH_MS = 10 * 60_000, SHARE_MIN_M = 100;

const metersBetween = (a, b) => {
  const R = 6371000, rad = Math.PI / 180, dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
};

export async function sendPosition(p, source = "app") {
  await api("/api/me/location", { method: "POST", body: { lat: p.lat, lon: p.lon, accuracy_m: p.accuracy, source } });
  lastSent = { lat: p.lat, lon: p.lon, at: Date.now() };
}

function onSharePoint(p) {
  if (p.accuracy != null && p.accuracy > 500) return;
  const now = Date.now();
  if (lastSent) {
    const age = now - lastSent.at;
    if (age < SHARE_EVERY_MS) return;
    if (age < SHARE_REFRESH_MS && metersBetween(lastSent, p) < SHARE_MIN_M) return;
  }
  lastSent = { ...p, at: now };  // markér straks, så et langsomt svar ikke giver dobbelte kald
  sendPosition(p).catch(() => { lastSent = null; });  // offline: næste punkt prøver igen
}

/** Start eller stop deling af baggrundsposition efter brugerens valg (profilen) – kaldes ved login/logout og ændring. */
export function syncSharing() {
  const want = isApp() && !!state.user?.position && state.user?.prefs?.share_location === true;
  if (want && !stopShare) {
    stopShare = watchPosition("share", { distance: SHARE_MIN_M, precise: false }, onSharePoint,
      (err) => { toast(err.code === 1 ? "Positionsdeling kræver tilladelse til position. Slå den til i Android-indstillinger." : "Position: " + err.message, "err"); });
  } else if (!want && stopShare) {
    stopShare(); stopShare = null; lastSent = null;
  }
}

// ---------------------------------------------------------------- push (Firebase) til appen
// Web Push findes ikke i en WebView. Appen får i stedet et Firebase-token (plugin @capacitor/push-notifications), som
// sendes til serveren; serveren sender via FCM (services/fcm.py). Samme bro-teknik som positionen: broens egne kald.

const PUSH = "PushNotifications";
const pn = (method, options) => cap().nativePromise(PUSH, method, options);
let pushOn = null;  // { token, handles }

const goto = (url) => { if (url) location.hash = String(url).replace(/^.*#/, "#"); };

/** Er push slået til i appen? "on" | "off" | "denied" | "unsupported" (appen er bygget uden push-pluginet). */
export async function appPushStatus() {
  if (!isApp() || typeof cap().nativePromise !== "function") return "unsupported";
  try {
    const { receive } = await pn("checkPermissions");
    if (receive === "denied") return "denied";
    return receive === "granted" && pushOn?.token ? "on" : "off";
  } catch { return "unsupported"; }
}

/** Bed om tilladelse, hent token fra Firebase og meld det til serveren. */
export async function enableAppPush() {
  if (pushOn) return;
  const state_ = { token: null, handles: [] };
  pushOn = state_;
  const fail = (msg) => { pushOn = null; state_.handles.forEach((x) => x?.remove?.()); throw new Error(msg); };
  try {
    state_.handles.push(cap().addListener(PUSH, "registration", (t) => {
      state_.token = t.value;
      api("/api/push/fcm", { method: "POST", body: { token: t.value, device: "Android-app" } }).catch(() => {});
    }));
    state_.handles.push(cap().addListener(PUSH, "registrationError", (e) => {
      console.warn("Push kunne ikke aktiveres", e);
      toast("Push kunne ikke aktiveres – appen mangler Firebase-opsætning (google-services.json).", "err");
    }));
    // Tryk på en notifikation: gå til siden i beskeden
    state_.handles.push(cap().addListener(PUSH, "pushNotificationActionPerformed", (a) => goto(a?.notification?.data?.url)));
    try { await pn("createChannel", { id: "alerts", name: "Alarmer", description: "Alarmer om fugle i dine områder", importance: 5, visibility: 1, vibration: true }); } catch { /* findes allerede */ }
    let { receive } = await pn("checkPermissions");
    if (receive !== "granted") ({ receive } = await pn("requestPermissions"));
    if (receive !== "granted") fail("Notifikationer er ikke tilladt for appen. Slå dem til i Android-indstillinger.");
    await pn("register");
  } catch (e) {
    if (pushOn === state_) fail(e.message || "Push kunne ikke slås til");
    throw e;
  }
}

/** Fjern enhedens token fra serveren (ved logout eller når push slås fra). Skal kaldes, mens brugeren stadig er logget ind. */
export async function stopAppPush() {
  const cur = pushOn;
  if (!cur) return;
  pushOn = null;
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
