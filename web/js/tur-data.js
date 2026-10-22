// Fælles data og logik for tur-siderne: opslag, GPS-rute, tur-modellen og lagring (server eller offline-kø).
import { api, toast, todayISO } from "./core.js";
import { newId, putLocal, getLocal, deleteLocal, listLocal, upload, isNetworkError } from "./offline.js";

export const nowHM = () => new Date().toTimeString().slice(0, 5);

let REF = null;  // arter, kodelister, turopslag
export async function loadRef() {
  if (REF) return REF;
  const [species, codes, lookups] = await Promise.all([api("/api/reports/species"), api("/api/reports/codes"), api("/api/trips/lookups")]);
  return REF = { species, codes, ...lookups };
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
  tracker.watch = navigator.geolocation.watchPosition((p) => {
    const pt = [Math.round(p.coords.latitude * 1e6) / 1e6, Math.round(p.coords.longitude * 1e6) / 1e6, Math.round(p.timestamp / 1000), Math.round(p.coords.accuracy)];
    const last = tracker.points[tracker.points.length - 1];
    if (pt[3] > 100 || (last && meters(last, pt) < 5) || tracker.points.length >= 20000) return;
    tracker.points.push(pt);
    tracker.listeners.forEach(fn => fn());
  }, (err) => {
    toast(err.code === 1 ? "Lokationstilladelse er afvist – GPS-ruten kan ikke optages" : "Position ikke tilgængelig: " + err.message, "err");
    trackPause();
  }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 30000 });
  navigator.wakeLock?.request("screen").then(l => { tracker.lock = l; }).catch(() => {});
  // Ruten gemmes i den lokale kopi hvert 20. sekund, så den overlever, at appen lukkes
  tracker.saver ||= setInterval(async () => {
    const rec = tracker.cid && await getLocal(tracker.cid);
    if (rec?.draft) await putLocal({ ...rec, draft: { ...rec.draft, gps_track: tracker.points } });
  }, 20000);
}
export function trackPause() {
  if (tracker.watch != null) navigator.geolocation.clearWatch(tracker.watch);
  tracker.watch = null; tracker.paused = true;
  tracker.lock?.release?.().catch(() => {}); tracker.lock = null;
  tracker.listeners.forEach(fn => fn());
}
export function trackStop() {
  trackPause();
  clearInterval(tracker.saver); tracker.saver = null;
  tracker.cid = null; tracker.points = []; tracker.paused = false;
}

// ---------------------------------------------------------------- tur-modellen

const TRIP_FIELDS = ["dato", "loknr", "turtidfra", "turtidtil", "metode_id", "komplet", "spontan", "medobser", "turnoter", "on_behalf_of", "lat", "lon",
  "temperatur", "skydaekke", "vindretning", "vindstyrke", "nedboer", "nedboertype", "sigtbarhed", "gps_aktiv"];

export function blankTrip() {
  return { client_id: newId(), server_id: null, dato: todayISO(), loknr: null, lok: null, turtidfra: nowHM(), turtidtil: "", metode_id: 1, komplet: null,
    spontan: false, medobser: "", turnoter: "", on_behalf_of: "", lat: null, lon: null, temperatur: null, skydaekke: null, vindretning: null,
    vindstyrke: null, nedboer: null, nedboertype: null, sigtbarhed: null, gps_aktiv: false, gps_track: [], observations: [], breeding_pairs: [],
    disturbances: [] };
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
  return { T: fromServer(await api(`/api/trips/${which}`), ref), files: {} };
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
