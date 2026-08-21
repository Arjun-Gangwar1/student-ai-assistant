const CACHE = "studai-v1";
const OFFLINE_PAGE = "/";

// App shell files to pre-cache
const STATIC_ASSETS = ["/", "/dashboard", "/manifest.json", "/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(STATIC_ASSETS).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const { request } = e;
  const url = new URL(request.url);

  // Skip non-GET and API/backend requests
  if (request.method !== "GET") return;
  if (url.pathname.startsWith("/api/")) return;
  if (!url.origin.startsWith(self.location.origin)) return;

  // Network-first for navigation, cache-first for static assets
  if (request.mode === "navigate") {
    e.respondWith(
      fetch(request).catch(() => caches.match(OFFLINE_PAGE))
    );
  } else {
    e.respondWith(
      caches.match(request).then(
        (cached) => cached || fetch(request).then((res) => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(CACHE).then((c) => c.put(request, clone));
          }
          return res;
        })
      )
    );
  }
});
