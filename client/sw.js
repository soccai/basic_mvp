const CACHE_NAME = "lifeos-v6";
const URLS_TO_CACHE = [
    "/",
    "/styles.css",
    "/app.js",
    "/ws.js",
    "/voice.js",
    "/pcm-processor.js",
    "/session-surface.js",
    "/timeline-surface.js",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(URLS_TO_CACHE))
    );
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((names) =>
            Promise.all(
                names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))
            )
        )
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    // Don't cache API or WebSocket
    if (
        event.request.url.includes("/api/") ||
        event.request.url.includes("/ws") ||
        event.request.url.includes("/health")
    ) {
        return;
    }
    event.respondWith(
        caches.match(event.request).then((r) => r || fetch(event.request))
    );
});
