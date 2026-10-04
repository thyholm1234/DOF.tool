const CACHE_NAME = "dof-tool-v4";
const CORE_ASSETS = [
  "/",
  "/index.html",
  "/community.html",
  "/hub.js",
  "/module.html",
  "/styles.css",
  "/app.js",
  "/module.js",
  "/notifications.html",
  "/notifications.js",
  "/advanced.html",
  "/advanced.js",
  "/settings.html",
  "/settings.js",
  "/thread.html",
  "/thread.js",
  "/nearby.html",
  "/nearby.js",
  "/admin.html",
  "/admin.js",
  "/samtykke.html",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match("/")))
  );
});

self.addEventListener("push", (event) => {
  const data = event.data?.json() || { title: "Fællesskabet", body: "Du har nyt fra fællesskabet." };
  event.waitUntil(self.registration.showNotification(data.title, { body: data.body, icon: "/icon.svg" }));
});
