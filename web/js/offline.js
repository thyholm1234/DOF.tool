// Lokal kø til ture: kladder og ikke-sendte ture ligger i IndexedDB (med foto/lyd) og sendes, når der er forbindelse.
// En tur har et client_id; serveren bruger det til at undgå dubletter, hvis et svar går tabt undervejs.
import { api, toast } from "./core.js";

const DB = "doftool-ture";
let dbp = null;
const open = () => dbp ||= new Promise((resolve, reject) => {
  const req = indexedDB.open(DB, 1);
  req.onupgradeneeded = () => req.result.createObjectStore("ture", { keyPath: "client_id" });
  req.onsuccess = () => resolve(req.result);
  req.onerror = () => reject(req.error);
});
const tx = async (mode, fn) => {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction("ture", mode);
    const out = fn(t.objectStore("ture"));
    t.oncomplete = () => resolve(out?.result);
    t.onerror = () => reject(t.error);
  });
};

export const newId = () => (crypto.randomUUID?.() || `${Date.now()}${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 36);

/** Poster: { client_id, server_id, state: "kladde" | "klar" | "fejl", error, body, files: { [obs.client_id]: File[] }, updated } */
export const putLocal = (rec) => tx("readwrite", (s) => s.put({ ...rec, updated: Date.now() }));
export const getLocal = (cid) => tx("readonly", (s) => s.get(cid));
export const listLocal = async () => (await tx("readonly", (s) => s.getAll()) || []).sort((a, b) => b.updated - a.updated);
export const deleteLocal = (cid) => tx("readwrite", (s) => s.delete(cid));

const isNetworkError = (err) => !err.status || err.status === 503 || err.status === 502 || err.status === 504;

/** Sender én tur (oprettelse eller ændring) og uploader derefter medier. Kaster ved fejl. */
export async function upload(rec) {
  const saved = rec.server_id
    ? await api(`/api/trips/${rec.server_id}`, { method: "PUT", body: rec.body })
    : await api("/api/trips", { method: "POST", body: rec.body });
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
  if (syncing || !navigator.onLine) return 0;
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

/** Kaldes én gang ved opstart: send, når forbindelsen kommer tilbage, og forsøg også nu. */
export function start() {
  window.addEventListener("online", () => syncAll());
  setTimeout(() => syncAll({ quiet: true }), 3000);
}
