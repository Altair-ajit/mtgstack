/* mtgstack phone build service worker.
 * 1. Routes every /api/* request (fetch, <img>, downloads) to the page, which hands it to the Python worker.
 * 2. Caches the app and its runtime so it opens offline; card images are cached as you browse.
 */
const V = new URL(self.location.href).searchParams.get("v") || "dev";
const SCOPE = new URL(self.registration.scope).pathname;                 // e.g. "/mtgstack/"
const APP_CACHE = `mtgstack-app-${V}`, RUNTIME = "mtgstack-runtime", IMAGES = "mtgstack-images";

self.addEventListener("install", e => {
  e.waitUntil(caches.open(APP_CACHE).then(c => c.addAll([SCOPE, `${SCOPE}manifest.webmanifest`]).catch(() => {})));
  self.skipWaiting();
});
self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys())
      if (k.startsWith("mtgstack-app-") && k !== APP_CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

let seq = 0;
async function viaPage(event, req) {
  let client = event.clientId && (await self.clients.get(event.clientId));
  if (!client) client = (await self.clients.matchAll({ type: "window" })).find(c => new URL(c.url).pathname.startsWith(SCOPE));
  if (!client) return new Response(JSON.stringify({ detail: "open the app first" }), { status: 503 });
  const u = new URL(req.url);
  const body = ["GET", "HEAD"].includes(req.method) ? null : await req.arrayBuffer();
  const ch = new MessageChannel();
  const reply = new Promise(ok => { ch.port1.onmessage = m => ok(m.data); });
  client.postMessage({ type: "api", id: ++seq, method: req.method, url: u.pathname.slice(u.pathname.indexOf("/api/")) + u.search,
    headers: [...req.headers.entries()], body }, [ch.port2, ...(body ? [body] : [])]);
  const r = await reply;
  return new Response(r.body, { status: r.status, headers: r.headers });
}

async function cacheFirst(cacheName, req) {
  const c = await caches.open(cacheName);
  const hit = await c.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok || res.type === "opaque") c.put(req, res.clone());
  return res;
}
async function networkFirst(req) {
  const c = await caches.open(APP_CACHE);
  try {
    const res = await fetch(req, { cache: "no-store" });
    if (res.ok) c.put(req, res.clone());
    return res;
  } catch {
    return (await c.match(req)) || (await c.match(SCOPE)) || Response.error();
  }
}

self.addEventListener("fetch", event => {
  const req = event.request, u = new URL(req.url);
  if (u.origin === location.origin && u.pathname.includes("/api/")) return event.respondWith(viaPage(event, req));
  if (u.hostname === "cards.scryfall.io" || u.hostname === "svgs.scryfall.io") return event.respondWith(cacheFirst(IMAGES, req));
  if (u.origin !== location.origin || req.method !== "GET" || !u.pathname.startsWith(SCOPE)) return;
  const rel = u.pathname.slice(SCOPE.length);
  if (rel.startsWith("data/")) return;                                    // card database: streamed, stored by the app
  if (rel.startsWith("assets/") || rel.startsWith("py/")) return event.respondWith(cacheFirst(rel.startsWith("py/") && !u.search ? RUNTIME : APP_CACHE, req));
  if (req.mode === "navigate") return event.respondWith(networkFirst(new Request(SCOPE)));
  return event.respondWith(networkFirst(req));
});
