// DOF.tool service worker: app-shell offline, netværk-først for API, push-notifikationer.
const VERSION = "doftool-v4";
const SHELL = [
  "/", "/index.html", "/css/app.css", "/js/app.js", "/js/core.js", "/js/live.js", "/js/maps.js", "/js/charts.js",
  "/js/views/feed.js", "/js/views/thread.js", "/js/views/map.js", "/js/views/lists.js", "/js/views/pheno.js",
  "/js/views/species.js", "/js/views/location.js", "/js/views/learn.js", "/js/views/alerts.js", "/js/views/profile.js",
  "/js/views/admin.js", "/js/views/login.js", "/vendor/leaflet/leaflet.js", "/vendor/leaflet/leaflet.css",
  "/manifest.webmanifest", "/icons/icon.svg", "/icons/icon-192.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION && k !== "doftool-api").map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.pathname === "/api/stream") return;
  if (url.origin !== location.origin) {
    // Kortfliser: cache-først
    if (url.hostname.endsWith("tile.openstreetmap.org")) {
      e.respondWith(caches.open("doftool-tiles").then(async (c) => (await c.match(e.request)) || fetch(e.request).then((r) => { if (r.ok) c.put(e.request, r.clone()); return r; })));
    }
    return;
  }
  if (url.pathname.startsWith("/api/")) {
    // Netværk først, cache som nødløsning (så feedet kan ses offline)
    e.respondWith(fetch(e.request).then((r) => {
      if (r.ok && !url.pathname.startsWith("/api/admin")) { const copy = r.clone(); caches.open("doftool-api").then((c) => c.put(e.request, copy)); }
      return r;
    }).catch(() => caches.match(e.request).then((r) => r || new Response(JSON.stringify({ detail: "Offline" }), { status: 503, headers: { "Content-Type": "application/json" } }))));
    return;
  }
  // App-shell: stale-while-revalidate
  e.respondWith(caches.open(VERSION).then(async (c) => {
    const hit = await c.match(e.request, { ignoreSearch: true });
    const net = fetch(e.request).then((r) => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
    return hit || net;
  }));
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch { d = { title: "DOF.tool", body: e.data?.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || "DOF.tool", {
    body: d.body || "", tag: d.tag || undefined, renotify: !!d.tag, data: { url: d.url || "/" },
    icon: "/icons/icon-192.png", badge: "/icons/icon-192.png",
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = e.notification.data?.url || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ("focus" in c) { c.postMessage({ type: "navigate", url }); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
