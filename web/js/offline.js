// Lokal kø til ture: kladder og ikke-sendte ture ligger i IndexedDB (med foto/lyd) og sendes, når der er forbindelse.
// En tur har et client_id; serveren bruger det til at undgå dubletter, hvis et svar går tabt undervejs.
import { api, state, toast } from "./core.js";

const DB = "doftool-ture";
let dbp = null;
// "ture" er køen af ture; "cache" har opslag (arter, koder, lokaliteter) og de seneste ture, så man kan indtaste og rette uden net
const open = () => dbp ||= new Promise((resolve, reject) => {
  const req = indexedDB.open(DB, 2);
  req.onupgradeneeded = () => {
    const db = req.result;
    if (!db.objectStoreNames.contains("ture")) db.createObjectStore("ture", { keyPath: "client_id" });
    if (!db.objectStoreNames.contains("cache")) db.createObjectStore("cache", { keyPath: "key" });
  };
  req.onsuccess = () => resolve(req.result);
  req.onerror = () => reject(req.error);
});
const tx = async (mode, fn, store = "ture") => {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const out = fn(t.objectStore(store));
    t.oncomplete = () => resolve(out?.result);
    t.onerror = () => reject(t.error);
  });
};

/** Lokal cache: { key, value, at }. Fejl (fx privat vindue uden IndexedDB) giver bare ingen cache. */
export const cachePut = (key, value) => tx("readwrite", (s) => s.put({ key, value, at: Date.now() }), "cache").catch(() => {});
export const cacheGet = (key) => tx("readonly", (s) => s.get(key), "cache").catch(() => null);

/** Henter fra serveren og gemmer svaret lokalt; uden forbindelse bruges den lokale kopi (andre fejl kastes videre). */
export async function cachedApi(key, path) {
  try { const v = await api(path); cachePut(key, v); return v; }
  catch (err) {
    if (!isNetworkError(err)) throw err;
    const c = await cacheGet(key);
    if (c) return c.value;
    throw err;
  }
}

// De seneste 14 dages ture i fuld form ("trips14"), så de kan åbnes og rettes uden net. Holdes ajour ved hver gemning.
export const RECENT_DAYS = 14;
const recentCutoff = () => new Date(Date.now() - RECENT_DAYS * 86400000).toISOString().slice(0, 10);
export async function rememberTrip(saved) {
  const list = ((await cacheGet("trips14"))?.value || []).filter(t => t.id !== saved.id && t.dato >= recentCutoff());
  if (saved.dato >= recentCutoff()) list.unshift(saved);
  await cachePut("trips14", list);
}
export async function forgetTrip(id) {
  const c = await cacheGet("trips14");
  if (c) await cachePut("trips14", c.value.filter(t => t.id !== id));
}
export const cachedTrip = async (id) => (await cacheGet("trips14"))?.value.find(t => t.id === id) || null;

export const newId = () => (crypto.randomUUID?.() || `${Date.now()}${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 36);

/** Poster: { client_id, server_id, owner, state: "kladde" | "klar" | "fejl", error, body, files: { [obs.client_id]: File[] }, updated }
 *  owner er brugerens id, så en anden, der logger ind på samme enhed, hverken ser eller kommer til at sende turen.
 *  Poster fra før owner fandtes, regnes som den indloggedes. */
const mine = (rec) => rec && (rec.owner ?? state.user?.id) === state.user?.id;
export const putLocal = (rec) => tx("readwrite", (s) => s.put({ owner: state.user?.id, ...rec, updated: Date.now() }));
export const getLocal = async (cid) => { const rec = await tx("readonly", (s) => s.get(cid)); return mine(rec) ? rec : undefined; };
export const listLocal = async () => (await tx("readonly", (s) => s.getAll()) || []).filter(mine).sort((a, b) => b.updated - a.updated);
export const deleteLocal = (cid) => tx("readwrite", (s) => s.delete(cid));

const isNetworkError = (err) => !err.status || err.status === 503 || err.status === 502 || err.status === 504;

/** Sender én tur (oprettelse eller ændring) og uploader derefter medier. Kaster ved fejl. */
export async function upload(rec) {
  const saved = rec.server_id
    ? await api(`/api/trips/${rec.server_id}`, { method: "PUT", body: rec.body })
    : await api("/api/trips", { method: "POST", body: rec.body });
  rememberTrip(saved);
  const left = {};
  for (const [cid, files] of Object.entries(rec.files || {})) {
    const obs = saved.observations.find(o => o.client_id === cid);
    if (!obs) continue;
    for (const f of files) {
      try {
        const fd = new FormData(); fd.append("file", f);
        await api(`/api/reports/${obs.id}/media`, { method: "POST", form: fd });
      } catch (err) { (left[cid] ||= []).push(f); toast(`${f.name}: ${err.message}`, "err"); }
    }
  }
  return { saved, left };
}

let syncing = false;
/** Sender alle ture, der er markeret klar. Returnerer antal sendte. Ture med forbindelsesfejl beholdes til næste forsøg. */
export async function syncAll({ quiet = false } = {}) {
  if (syncing || !navigator.onLine || !state.user) return 0;
  syncing = true;
  let sent = 0;
  try {
    for (const rec of (await listLocal()).filter(r => r.state === "klar" || r.state === "fejl")) {
      try {
        const { saved, left } = await upload(rec);
        if (Object.keys(left).length) await putLocal({ ...rec, server_id: saved.id, files: left, state: "klar" });
        else await deleteLocal(rec.client_id);
        sent++;
      } catch (err) {
        if (isNetworkError(err) || err.status === 401) break;  // ikke logget ind eller ingen forbindelse: prøv igen senere
        await putLocal({ ...rec, state: "fejl", error: err.message });
        if (!quiet) toast(`Turen kunne ikke sendes: ${err.message}`, "err");
      }
    }
  } finally { syncing = false; }
  if (sent && !quiet) toast(sent === 1 ? "Turen er sendt" : `${sent} ture er sendt`);
  if (sent) window.dispatchEvent(new CustomEvent("ture-synkroniseret"));
  return sent;
}

export { isNetworkError };

/** Ved logud: slet opslag, de seneste ture og API-svarene i service workerens cache, så den næste bruger på enheden
 *  ikke kan se dem offline. Ture, der ikke er sendt endnu, bliver liggende til ejeren logger ind igen. */
export async function forgetUser() {
  await tx("readwrite", (s) => s.clear(), "cache").catch(() => {});
  await globalThis.caches?.delete("doftool-api").catch(() => {});
}

/** Kaldes én gang ved opstart: send, når forbindelsen kommer tilbage, og forsøg også nu. */
export function start() {
  window.addEventListener("online", () => syncAll());
  setTimeout(() => syncAll({ quiet: true }), 3000);
}
