/* Service worker mínimo: habilita la instalación como app y un arranque rápido.
   Cachea el armazón estático; los datos (API) siempre van a la red. */
"use strict";
const CACHE = "recepcion-v4";
const SHELL = [
  "/panel/", "/panel/index.html", "/panel/agenda.html", "/panel/espera.html",
  "/panel/calendario.html", "/panel/clientes.html", "/panel/servicios.html",
  "/panel/facturacion.html", "/panel/ajustes.html", "/panel/css/panel.css",
  "/panel/js/api.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Nunca cachear la API ni peticiones que no sean GET: datos siempre frescos.
  if (e.request.method !== "GET" || url.pathname.startsWith("/admin")) return;
  e.respondWith(
    fetch(e.request).then((r) => {
      if (url.pathname.startsWith("/panel/")) {
        const copy = r.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      }
      return r;
    }).catch(() => caches.match(e.request))
  );
});
