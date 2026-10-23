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
