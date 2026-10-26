// Fælles data og logik for tur-siderne: opslag, GPS-rute, tur-modellen og lagring (server eller offline-kø).
import { api, toast, todayISO } from "./core.js";
import { watchPosition, isApp } from "./native.js";
import { newId, putLocal, getLocal, deleteLocal, listLocal, upload, isNetworkError, cachedApi, cacheGet, cachePut, cachedTrip, rememberTrip, RECENT_DAYS } from "./offline.js";

export const nowHM = () => new Date().toTimeString().slice(0, 5);

let REF = null;  // arter, kodelister, turopslag – gemt på enheden, så formularerne virker uden net
export async function loadRef() {
  if (REF) return REF;
  const [species, codes, lookups] = await Promise.all([cachedApi("species", "/api/reports/species"), cachedApi("codes", "/api/reports/codes"),
    cachedApi("lookups", "/api/trips/lookups")]);
  return REF = { species, codes, ...lookups };
}
/** Seneste lokaliteter, arter og adfærd (også uden net). */
export const loadRecent = () => cachedApi("recent", "/api/reports/recent").catch(() => ({ locations: [], species: [], adf: [] }));

// ---------------------------------------------------------------- lokaliteter på enheden (søgning og nærmeste uden net)

const LOC_MAX_AGE = 7 * 86400000;
let LOCS = null, locLoading = null;
const unpackLocs = ({ kommuner, rows }) => rows.map(([loknr, navn, lat, lon, k]) => ({ loknr, navn, lat, lon, kommune: k == null ? null : kommuner[k] }));
function refreshLocs() {
  return locLoading ||= api("/api/trips/offline/locations")
    .then((v) => { LOCS = unpackLocs(v); return cachePut("locations", v); })
    .catch(() => {}).finally(() => { locLoading = null; });
}
/** Alle lokaliteter fra enheden (hentes igen, når kopien er over en uge gammel). null, hvis de aldrig er hentet. */
async function locIndex() {
  if (LOCS) return LOCS;
  const c = await cacheGet("locations");
  if (c) LOCS = unpackLocs(c.value);
  if (!c || Date.now() - c.at > LOC_MAX_AGE) { const p = refreshLocs(); if (!c) await p; }
  return LOCS;
}
/** Søg lokalitet på navn – som /api/locations (navne der begynder med søgeordet først, så de korteste). */
export async function findLocations(q) {
  const locs = await locIndex();
  if (!locs) return api(`/api/locations?q=${encodeURIComponent(q)}`);
  const ql = q.toLowerCase();
  return locs.filter(l => l.navn.toLowerCase().includes(ql))
    .sort((a, b) => b.navn.toLowerCase().startsWith(ql) - a.navn.toLowerCase().startsWith(ql) || a.navn.length - b.navn.length).slice(0, 25);
}
/** Nærmeste lokaliteter med afstand i km – som /api/reports/near. */
export async function nearestLocations(lat, lon, n = 20) {
  const locs = await locIndex();
  if (!locs) return api(`/api/reports/near?lat=${lat}&lon=${lon}`);
  return locs.filter(l => l.lat != null).map(l => ({ ...l, km: Math.round(meters([lat, lon], [l.lat, l.lon]) / 10) / 100 }))
    .sort((a, b) => a.km - b.km).slice(0, n);
}

/** Lægger opslag, lokaliteter og de seneste ture på enheden. Kaldes ved opstart (højst hvert 10. minut). */
export async function warmOffline() {
  try { if (Date.now() - Number(localStorage.getItem("offline-warm") || 0) < 600000) return; localStorage.setItem("offline-warm", String(Date.now())); } catch { /* ingen localStorage */ }
  await Promise.allSettled([loadRef(), loadRecent(), locIndex(), cachedApi("tripsmine", "/api/trips/mine"),
    api(`/api/trips/recent?days=${RECENT_DAYS}`).then(v => cachePut("trips14", v))]);
  import("./views/tur.js").catch(() => {});  // tur-siderne hentes, så service workeren har dem til brug uden net
}

// ---------------------------------------------------------------- GPS-rute (lever videre, når man forlader siden)

export const tracker = { cid: null, watch: null, points: [], paused: false, lock: null, listeners: new Set(), saver: null };
export const meters = (a, b) => {
  const R = 6371000, rad = Math.PI / 180, dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
};
export const trackLength = (pts) => pts.reduce((s, p, i) => i ? s + meters(pts[i - 1], p) : 0, 0);

export function trackStart(cid, points) {
  if (tracker.cid !== cid) { trackStop(); tracker.cid = cid; tracker.points = points; }
  tracker.paused = false;
  if (tracker.watch != null) return;
  // I appen kommer punkterne fra Androids positionstjeneste (forgrundstjeneste), så ruten optages med slukket skærm
  tracker.watch = watchPosition("route", { distance: 5, precise: true }, (p) => {
    const pt = [p.lat, p.lon, p.ts, Math.round(p.accuracy ?? 0)];
    const last = tracker.points[tracker.points.length - 1];
    if (pt[3] > 100 || (last && (meters(last, pt) < 5 || pt[2] < last[2])) || tracker.points.length >= 20000) return;
    tracker.points.push(pt);
    tracker.listeners.forEach(fn => fn());
  }, (err) => {
    toast(err.code === 1 ? "Lokationstilladelse er afvist – GPS-ruten kan ikke optages" : "Position ikke tilgængelig: " + err.message, "err");
    trackPause();
  });
  if (!isApp()) navigator.wakeLock?.request("screen").then(l => { tracker.lock = l; }).catch(() => {});
  // Ruten gemmes i den lokale kopi hvert 20. sekund, så den overlever, at appen lukkes
  tracker.saver ||= setInterval(async () => {
    const rec = tracker.cid && await getLocal(tracker.cid);
    if (rec?.draft) await putLocal({ ...rec, draft: { ...rec.draft, gps_track: tracker.points } });
  }, 20000);
}
export function trackPause() {
  if (tracker.watch != null) tracker.watch();
  tracker.watch = null; tracker.paused = true;
  tracker.lock?.release?.().catch(() => {}); tracker.lock = null;
  tracker.listeners.forEach(fn => fn());
}
export function trackStop() {
  trackPause();
  clearInterval(tracker.saver); tracker.saver = null;
  tracker.cid = null; tracker.points = []; tracker.paused = false;
}

/** Beskærer ruten til turens start- og sluttidspunkt (punkternes tidsstempel er sekunder; turens tider er lokale).
 *  Uden sluttid beskæres kun starten. Bliver intet tilbage, rører vi ikke ruten – så er tiderne sandsynligvis forkerte. */
export function trimTrack(T) {
  const at = (hm) => new Date(`${T.dato}T${hm}`).getTime() / 1000;
  if (!T.gps_track?.length || !T.dato || !T.turtidfra) return;
  const from = at(T.turtidfra);
  let to = T.turtidtil ? at(T.turtidtil) + 59 : Infinity;  // sluttiden gælder hele minuttet
  if (to < from) to += 86400;                                // turen gik over midnat
  const kept = T.gps_track.filter(p => p[2] >= from && p[2] <= to);
  if (kept.length) T.gps_track = kept;
}

/** Afslutter ruten: stopper optagelsen, hvis den kører for turen, og beskærer til turens tidsrum. */
export function finishTrack(T) {
  if (tracker.cid === T.client_id) { const pts = tracker.points; trackStop(); T.gps_track = pts; }
  trimTrack(T);
  T.gps_aktiv = T.gps_track.length > 0;
}

// ---------------------------------------------------------------- tur-modellen

const TRIP_FIELDS = ["dato", "loknr", "turtidfra", "turtidtil", "metode_id", "komplet", "spontan", "medobser", "turnoter", "on_behalf_of", "lat", "lon",
  "temperatur", "skydaekke", "vindretning", "vindstyrke", "nedboer", "nedboertype", "sigtbarhed", "gps_aktiv"];

export function blankTrip() {
  return { client_id: newId(), server_id: null, dato: todayISO(), loknr: null, lok: null, turtidfra: nowHM(), turtidtil: "", metode_id: 1, komplet: null,
    spontan: false, medobser: "", turnoter: "", on_behalf_of: "", lat: null, lon: null, temperatur: null, skydaekke: null, vindretning: null,
    vindstyrke: null, nedboer: null, nedboertype: null, sigtbarhed: null, gps_aktiv: false, gps_track: [], observations: [], breeding_pairs: [],
    disturbances: [], speech: [] };  // speech: indtalte poster (kun på enheden; summen står i observations)
}

export function fromServer(t, ref) {
  const klass = (artnr) => ref.species.find(s => s.artnr === artnr)?.klass || "Alm";
  return { ...blankTrip(), ...t, client_id: t.client_id || newId(), server_id: t.id, turtidfra: t.turtidfra || "", turtidtil: t.turtidtil || "",
    on_behalf_of: t.for_andre ? t.obserkode : "", medobser: t.medobser || "", turnoter: t.turnoter || "", gps_track: t.gps_track || [],
    observations: t.observations.map(o => ({ ...o, client_id: o.client_id || newId(), klass: klass(o.artnr), status: "ukendt", _media: o.media?.length || 0 })),
    breeding_pairs: t.breeding_pairs.map(p => ({ ...p, client_id: p.client_id || newId() })),
    disturbances: t.disturbances.map(d => ({ ...d, client_id: d.client_id || newId() })) };
}

export function toBody(T) {
  const body = {};
  for (const k of TRIP_FIELDS) body[k] = T[k] === "" ? null : T[k];
  body.client_id = T.client_id;
  body.on_behalf_of = T.on_behalf_of || null;
  body.gps_track = T.gps_track || [];
  body.observations = T.observations.map(o => ({
    id: o.id ?? null, client_id: o.client_id, artnr: o.artnr, antal: o.antal ?? 1, antalmetode: o.antalmetode ?? null, maerker: o.maerker || [], koen: o.koen || null,
    adfkode: o.adfkode || null, alderkode: o.alderkode || null, dragtkode: o.dragtkode || null, obstidfra: o.obstidfra || null, obstidtil: o.obstidtil || null,
    obs_lat: o.obs_lat ?? null, obs_lon: o.obs_lon ?? null, radius_m: o.radius_m ?? null, fuglnoter: o.fuglnoter || null, hemmelig: !!o.hemmelig,
    skjul_position: !!o.skjul_position, sikkerhed: o.sikkerhed || "sikker", grundlag: o.grundlag || [], status: o.status || "ukendt", retningslinjer: !!o.retningslinjer }));
  body.breeding_pairs = T.breeding_pairs.map(p => ({ id: p.id ?? null, client_id: p.client_id, artnr: p.artnr, antal: p.antal, adfkode: p.adfkode || null,
    lat: p.lat ?? null, lon: p.lon ?? null, noter: p.noter || null, hemmelig: !!p.hemmelig }));
  body.disturbances = T.disturbances.map(d => ({ id: d.id ?? null, client_id: d.client_id, type_id: d.type_id, antal: d.antal ?? null, noter: d.noter || null }));
  return body;
}

/** Henter en tur til visning/redigering: lokal kopi med ikke-sendte ændringer går forud for serverens. */
export async function openTrip(which, ref) {
  if (which === "ny") return { T: blankTrip(), files: {} };
  const local = which.startsWith("lokal/") ? await getLocal(which.slice(6))
    : (await listLocal()).find(r => r.server_id === Number(which));
  if (local?.draft) return { T: { ...local.draft, server_id: local.server_id ?? local.draft.server_id }, files: local.files || {} };
  if (which.startsWith("lokal/")) throw new Error("Turen findes ikke på denne enhed");
  let t;
  try { t = await api(`/api/trips/${which}`); rememberTrip(t); }
  catch (err) {
    if (!isNetworkError(err)) throw err;
    t = await cachedTrip(Number(which));  // uden net: kopien fra de seneste 14 dage
    if (!t) throw new Error(`Ingen forbindelse – kun ture fra de seneste ${RECENT_DAYS} dage kan åbnes uden net`);
  }
  return { T: fromServer(t, ref), files: {} };
}

export const tripHash = (T, suffix = "") => `#/indtast/tur/${T.server_id || `lokal/${T.client_id}`}${suffix}`;

/** Lagring af én tur. save() samler hurtige ændringer (fx +1 +1 +1) i ét kald; kaldene køres efter hinanden.
 *  Uden forbindelse lægges turen i den lokale kø og sendes af offline.js, når der er net. */
export function tripStore(T, files) {
  const subs = new Set();
  let status = "ok", message = "", chain = Promise.resolve(), timer = null;
  const set = (s, m = "") => { status = s; message = m; subs.forEach(fn => fn(s, m)); };

  const adopt = (saved, left, sent) => {
    T.server_id = saved.id;
    for (const o of T.observations) {
      const s = saved.observations.find(x => x.client_id === o.client_id);
      if (s) { o.id = s.id; o._media = s.media?.length || 0; }
    }
    for (const [list, back] of [[T.breeding_pairs, saved.breeding_pairs], [T.disturbances, saved.disturbances]]) {
      for (const x of list) { const s = back.find(y => y.client_id === x.client_id); if (s) x.id = s.id; }
    }
    for (const cid of Object.keys(sent)) { if (left[cid]) files[cid] = left[cid]; else delete files[cid]; }
  };

  const run = () => {
    chain = chain.then(async () => {
      set("gemmer");
      const sent = Object.fromEntries(Object.entries(files).filter(([, v]) => v?.length));
      const rec = { client_id: T.client_id, server_id: T.server_id, state: "klar", draft: T, body: toBody(T), files: sent };
      try {
        const { saved, left } = await upload(rec);
        adopt(saved, left, sent);
        if (Object.keys(left).length) await putLocal({ ...rec, server_id: saved.id, files: left });
        else await deleteLocal(T.client_id);
        set("ok");
      } catch (err) {
        const offline = isNetworkError(err) || err.status === 401;
        await putLocal({ ...rec, state: offline ? "klar" : "fejl", error: offline ? null : err.message }).catch(() => {});
        set(offline ? "offline" : "fejl", err.message);
        if (!offline) toast(err.message, "err");
      }
      return status;
    });
    return chain;
  };

  return {
    T, files,
    get status() { return status; }, get message() { return message; },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    save() { clearTimeout(timer); set("gemmer"); timer = setTimeout(run, 500); },
    saveNow() { clearTimeout(timer); return run(); },
    async discard() { clearTimeout(timer); await deleteLocal(T.client_id).catch(() => {}); },
  };
}
