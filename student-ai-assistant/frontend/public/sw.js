// Service worker — offline shell for the installed PWA.
//
// Bump CACHE on every change to this file. The `activate` handler deletes every
// cache whose name differs, so a bump is what evicts stale bundles from users
// who already have the old worker installed.
//
// v2: v1 served JS chunks cache-first from a cache that never expired. Combined
// with Next.js dev's stable chunk filenames, that pinned browsers to
// pre-deploy JavaScript — code calling API routes that had since been removed,
// with no way for a reload to escape it.
const CACHE = "studai-v2";
const OFFLINE_PAGE = "/";

// Only genuinely static things are pre-cached. HTML is deliberately excluded:
// pre-caching "/dashboard" is what made a stale shell survive deploys.
const STATIC_ASSETS = ["/manifest.json", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(STATIC_ASSETS).catch(() => {})),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// Lets a freshly installed worker take over without waiting for every tab to
// close (see ServiceWorkerRegistrar).
self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

/** Content-hashed build output is immutable, so it is safe to cache forever. */
function isImmutableAsset(url) {
  return /\/_next\/static\/(chunks|css|media)\/.*\.[0-9a-f]{8,}\.(js|css|woff2?|png|svg)$/.test(
    url.pathname,
  );
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== "GET") return;
  if (url.origin !== self.location.origin) return;
  // API calls must never be cached — an answer about deadlines has to be live.
  if (url.pathname.startsWith("/api/")) return;

  // Navigation: network-first, cache only as an offline fallback.
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_PAGE)));
    return;
  }

  // Immutable hashed assets: cache-first is correct — the filename changes when
  // the content does, so a cached copy can never be stale.
  if (isImmutableAsset(url)) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            if (response.ok) {
              const clone = response.clone();
              void caches.open(CACHE).then((cache) => cache.put(request, clone));
            }
            return response;
          }),
      ),
    );
    return;
  }

  // Everything else (including dev chunks, whose names are NOT hashed):
  // network-first, falling back to cache only when offline.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const clone = response.clone();
          void caches.open(CACHE).then((cache) => cache.put(request, clone));
        }
        return response;
      })
      .catch(() => caches.match(request)),
  );
});
