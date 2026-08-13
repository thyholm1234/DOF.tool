// Version: 1.13.16 - 2026-05-15 23.55.20
// © Christian Vemmelund Helligsø

const CACHE_NAME = 'boligbirding-v1.13.16';
const PRECACHE_URLS = [
  '/', '/index.html', '/style.css', '/app.js', '/manifest.webmanifest',
  '/icons/icon-192.png', '/icons/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);

    // >>> VIGTIGT: bypass browserens HTTP-cache <<<
    const requests = PRECACHE_URLS.map(
      (url) => new Request(url, { cache: 'reload' })
    );

    try {
      await cache.addAll(requests);
    } catch (e) {
      console.warn('Nogle filer kunne ikke caches ved install:', e);
    }
    self.skipWaiting(); // behold
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)));
    self.clients.claim(); // behold
  })());
});

// Optionelt: network-first for navigationer, så index.html altid kan opdatere hurtigt
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const requestUrl = new URL(request.url);

  // Ignorer ikke-http(s) requests (fx chrome-extension://)
  if (!['http:', 'https:'].includes(requestUrl.protocol)) {
    return;
  }

  // Undgå cache for alle API-kald (alt under /api)
  if (request.url.includes('/api/')) {
    event.respondWith(fetch(request));
    return;
  }

  // HTML navigationer (SPA/MPA) — hent netværk først, fald tilbage til cache ved offline
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/index.html'))
    );
    return;
  }

  // Scripts og styles: network-first for at undgå stale bundles i cache
  const isScriptOrStyle =
    request.destination === 'script' ||
    request.destination === 'style' ||
    requestUrl.pathname.endsWith('.js') ||
    requestUrl.pathname.endsWith('.css');

  if (isScriptOrStyle) {
    event.respondWith(
      fetch(request).then((resp) => {
        if (request.method === 'GET' && resp && resp.status === 200 && resp.type === 'basic') {
          const respClone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, respClone));
        }
        return resp;
      }).catch(() => caches.match(request))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      return cached || fetch(request).then((resp) => {
        if (request.method === 'GET' && resp && resp.status === 200 && resp.type === 'basic') {
          const respClone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, respClone));
        }
        return resp;
      }).catch(() => cached);
    })
  );
});