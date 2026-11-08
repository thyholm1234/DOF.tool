// Server-sent events med automatisk genopkobling. Ét EventSource for hele appen.
// Strøm: mens portalen er skjult (appen i baggrunden, fanen ikke synlig), lukkes forbindelsen efter et halvt minut – en
// åben forbindelse med ping holder telefonens radio vågen hele tiden. Alarmer kommer som push imens. Når siden vises
// igen, genåbnes den, og visningerne får et "feed"-signal (resume), så de henter det, de kan have misset.
import { onVisibility } from "./lifecycle.js";

const handlers = new Map();
const statusHandlers = new Set();
const HIDDEN_CLOSE_MS = 30_000;
let es = null;
let wanted = false;    // start() er kaldt (og ikke stop())
let sleepTimer = null;
let slept = false;     // lukket, fordi siden var skjult

const emit = (type, data) => (handlers.get(type) || []).forEach(f => { try { f(data); } catch { /* ignore */ } });

function connect() {
  es?.close();
  es = new EventSource("/api/stream", { withCredentials: true });
  es.onopen = () => statusHandlers.forEach(f => f(true));
  es.onerror = () => statusHandlers.forEach(f => f(false));
  for (const type of ["feed", "comment", "notification", "like"]) {
    es.addEventListener(type, (e) => {
      let data = {};
      try { data = JSON.parse(e.data); } catch { /* ignore */ }
      emit(type, data);
    });
  }
}

onVisibility((visible) => {
  clearTimeout(sleepTimer);
  if (!wanted) return;
  if (!visible) {
    sleepTimer = setTimeout(() => { es?.close(); es = null; slept = true; statusHandlers.forEach(f => f(false)); }, HIDDEN_CLOSE_MS);
  } else if (slept) {
    slept = false;
    connect();
    emit("feed", { type: "feed", new: true, changed: true, portal: true, resume: true });
    emit("resume", {});
  }
});

export const live = {
  start() { wanted = true; slept = false; connect(); },
  restart() { wanted = true; slept = false; connect(); },
  stop() { wanted = false; clearTimeout(sleepTimer); es?.close(); es = null; statusHandlers.forEach(f => f(false)); },
  on(type, fn) {
    if (!handlers.has(type)) handlers.set(type, new Set());
    handlers.get(type).add(fn);
    return () => handlers.get(type).delete(fn);
  },
  onStatus(fn) { statusHandlers.add(fn); return () => statusHandlers.delete(fn); },
};
