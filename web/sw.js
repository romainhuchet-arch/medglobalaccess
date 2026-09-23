/* Service worker : l'appli s'ouvre hors ligne une fois installée.
 * - coquille de l'appli : cache d'abord (versionnée) ;
 * - données : réseau d'abord, cache en secours (on voit toujours la dernière version) ;
 * - fond de carte : cache au fil de la navigation, limité en taille.
 */
const VERSION = "medaccess-v2";
const COQUILLE = [
  "./", "index.html", "style.css", "app.js", "calc.js", "manifest.webmanifest",
  "vendor/maplibre-gl.js", "vendor/maplibre-gl.css",
  "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png", "icons/favicon-32.png",
];
const TUILES = "medaccess-tuiles";
const MAX_TUILES = 400;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(COQUILLE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((cles) => Promise.all(cles.filter((k) => k !== VERSION && k !== TUILES).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function reseauDabord(req) {
  const cache = await caches.open(VERSION);
  try {
    const rep = await fetch(req);
    if (rep.ok) cache.put(req, rep.clone());
    return rep;
  } catch (err) {
    const enCache = await cache.match(req);
    if (enCache) return enCache;
    throw err;
  }
}

async function tuile(req) {
  const cache = await caches.open(TUILES);
  const enCache = await cache.match(req);
  if (enCache) return enCache;
  let rep;
  try { rep = await fetch(req); } catch (err) { return new Response("", { status: 504 }); }
  if (rep.ok) {
    cache.put(req, rep.clone());
    cache.keys().then((k) => { if (k.length > MAX_TUILES) cache.delete(k[0]); });
  }
  return rep;
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.hostname.endsWith("basemaps.cartocdn.com")) { e.respondWith(tuile(e.request)); return; }
  if (url.origin !== location.origin) return;
  if (url.pathname.endsWith("/data/communes.json")) { e.respondWith(reseauDabord(e.request)); return; }
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then((r) => r || fetch(e.request)));
});
