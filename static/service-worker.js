const CACHE_NAME = "aldo-tips-v2";
const IS_LOCALHOST = self.location.hostname === "localhost" || self.location.hostname === "127.0.0.1";
const SHELL_ASSETS = [
  "/",
  "/login",
  "/register",
  "/manifest.webmanifest",
  "/offline.html",
  "/static/style.css",
  "/static/aldo.png",
  "/static/icon-192.png",
  "/static/icon-512.png"
];

self.addEventListener("install", (event) => {
  if (IS_LOCALHOST) {
    self.skipWaiting();
    return;
  }
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  if (IS_LOCALHOST) {
    event.waitUntil(
      self.registration.unregister().then(async () => {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      })
    );
    return;
  }
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") {
    return;
  }

  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  if (IS_LOCALHOST) {
    event.respondWith(fetch(event.request));
    return;
  }

  if (url.pathname === "/healthz") {
    event.respondWith(fetch(event.request));
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(async () => {
          const cache = await caches.open(CACHE_NAME);
          return (await cache.match(event.request)) || (await cache.match("/offline.html"));
        })
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) {
        return cached;
      }
      return fetch(event.request).then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      });
    })
  );
});
