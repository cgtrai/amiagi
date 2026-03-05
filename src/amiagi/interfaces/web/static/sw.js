/* ================================================================
   Amiagi Service Worker – Web Push & Offline Shell
   ================================================================
   Activated from base.html via navigator.serviceWorker.register().
   Handles:
   - push   → show native Notification
   - notificationclick → open or focus dashboard
   ================================================================ */

const CACHE_NAME = "amiagi-shell-v1";
const SHELL_ASSETS = [
  "/static/css/tokens.css",
  "/static/css/components.css",
  "/static/css/layout.css",
  "/static/css/responsive.css",
];

/* ── Install: pre-cache shell assets ───────────────────────── */
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

/* ── Activate: clean old caches ────────────────────────────── */
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

/* ── Fetch: network first, shell cache fallback ────────────── */
self.addEventListener("fetch", (event) => {
  // Only cache GET requests for shell CSS/JS
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (!url.pathname.startsWith("/static/")) return;

  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});

/* ── Push: display native notification ─────────────────────── */
self.addEventListener("push", (event) => {
  let data = { title: "Amiagi", body: "New notification", url: "/dashboard" };
  try {
    if (event.data) {
      const json = event.data.json();
      data = Object.assign(data, json);
    }
  } catch (_) {
    if (event.data) data.body = event.data.text();
  }

  const options = {
    body: data.body,
    icon: data.icon || "/static/img/icon-192.png",
    badge: data.badge || "/static/img/badge-72.png",
    tag: data.tag || "amiagi-push",
    data: { url: data.url || "/dashboard" },
    vibrate: [100, 50, 100],
  };

  event.waitUntil(self.registration.showNotification(data.title, options));
});

/* ── Notification click: focus or open window ──────────────── */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/dashboard";

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        // Try to focus an existing tab
        for (const client of clientList) {
          if (client.url.includes(url) && "focus" in client) {
            return client.focus();
          }
        }
        // Otherwise open a new window
        return self.clients.openWindow(url);
      })
  );
});
