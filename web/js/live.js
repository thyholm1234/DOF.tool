// Server-sent events med automatisk genopkobling. Ét EventSource for hele appen.
const handlers = new Map();
const statusHandlers = new Set();
let es = null;

function connect() {
  es?.close();
  es = new EventSource("/api/stream", { withCredentials: true });
  es.onopen = () => statusHandlers.forEach(f => f(true));
  es.onerror = () => statusHandlers.forEach(f => f(false));
  for (const type of ["feed", "comment", "notification", "like"]) {
    es.addEventListener(type, (e) => {
      let data = {};
      try { data = JSON.parse(e.data); } catch { /* ignore */ }
      (handlers.get(type) || []).forEach(f => f(data));
    });
  }
}

export const live = {
  start: connect,
  restart: connect,
  stop() { es?.close(); es = null; statusHandlers.forEach(f => f(false)); },
  on(type, fn) {
    if (!handlers.has(type)) handlers.set(type, new Set());
    handlers.get(type).add(fn);
    return () => handlers.get(type).delete(fn);
  },
  onStatus(fn) { statusHandlers.add(fn); return () => statusHandlers.delete(fn); },
};
