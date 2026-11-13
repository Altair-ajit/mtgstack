/** Phone build (GitHub Pages) and the installed app (Capacitor): no server. The Python app runs in a Web Worker
 * (public/py/worker.js) and this page forwards every /api/* request to it.
 * On the website a service worker catches the requests (fetch, <img>, downloads) and hands them to this page.
 * An installed iOS app has no service workers, so there (NATIVE) the page catches its own fetch() calls and loads
 * /api/ images itself. */
export const LOCAL = import.meta.env.VITE_LOCAL === "1";
export const NATIVE = import.meta.env.VITE_NATIVE === "1";
export const BUILD = import.meta.env.VITE_BUILD || "dev";
const BASE = import.meta.env.BASE_URL;

/** What the engine reported while starting (scanner and model state): read by the simulator self-test. */
export const engineEvents: string[] = [];

type Reply = { status: number; headers: [string, string][]; body: ArrayBuffer };
type Pending = { ok: (r: Reply) => void; timer: number };

function show(msg: string, err = false) {
  const el = document.getElementById("boot");
  if (!el) return;
  el.innerHTML = `<div class="boot-card"><div class="logo">mtg<b>stack</b></div>
    ${err ? "" : '<div class="spin"></div>'}<div class="${err ? "bad" : "muted"}">${msg}</div>
    ${err ? '<button onclick="location.reload()">Try again</button>' : ""}</div>`;
}

export async function bootLocal(): Promise<void> {
  const boot = document.createElement("div");
  boot.id = "boot";
  document.body.appendChild(boot);
  show("Starting…");
  const viaSw = !NATIVE && "serviceWorker" in navigator;

  try { await navigator.storage?.persist?.(); } catch { /* best effort: keep data from being evicted */ }

  if (viaSw) {
    await navigator.serviceWorker.register(`${BASE}sw.js?v=${BUILD}`, { scope: BASE });
    if (!navigator.serviceWorker.controller) {
      await new Promise<void>(ok => {
        navigator.serviceWorker.addEventListener("controllerchange", () => ok(), { once: true });
        setTimeout(ok, 4000);
      });
      if (!navigator.serviceWorker.controller) { location.reload(); await new Promise(() => {}); }
    }
  }

  const data = import.meta.env.VITE_DATA_URL ? `&data=${encodeURIComponent(import.meta.env.VITE_DATA_URL)}` : "";
  const worker = new Worker(`${BASE}py/worker.js?v=${BUILD}${data}`);
  const pending = new Map<number, Pending>();
  let n = 0;
  const ready = new Promise<void>((ok, bad) => {
    worker.onmessage = e => {
      const m = e.data;
      if (m.type === "scanner" || m.type === "model" || m.type === "error") engineEvents.push(`${m.type} ${m.state || ""} ${m.msg || m.version || ""}`.trim());
      if (m.type === "progress") { if (!document.body.classList.contains("booted")) show(m.msg); }
      else if (m.type === "scanner") {
        // shown once while it loads, and only where the camera is used
        if (m.state === "loading" && /\/(scan|lab)/.test(location.pathname)) hint("Loading the scanner (first time only)…");
        else if (m.state === "error") hint(`Scanner couldn't load: ${m.msg}`, 8000);
        else hint("");
      }
      else if (m.type === "model") {
        // until it is ready scans only read the text, which is much weaker: say so instead of failing silently
        if (m.state === "loading" && /\/(scan|lab)/.test(location.pathname)) hint("Loading picture recognition (27 MB, first time only)…");
        else if (m.state === "error") hint(`Picture recognition couldn't load: ${m.msg}. Scans will only read the text.`, 20000);
        else hint("");
      }
      else if (m.type === "ready") ok();
      else if (m.type === "error") { show(`Couldn't start: ${m.msg}`, true); bad(new Error(m.msg)); }
      else if (m.type === "api") {
        const p = pending.get(m.id);
        pending.delete(m.id);
        if (p) { clearTimeout(p.timer); p.ok({ status: m.status, headers: m.headers, body: m.body }); }
      }
    };
  });
  /** One API request to the Python app. `url` starts at /api/. */
  const call = (method: string, url: string, headers: [string, string][], body: ArrayBuffer | null) => new Promise<Reply>(ok => {
    const id = ++n;
    // never leave the UI hanging: if Python doesn't answer (e.g. the worker was killed), fail the request
    const timer = window.setTimeout(() => {
      pending.delete(id);
      const out = new TextEncoder().encode(JSON.stringify({ detail: "The app didn't respond. Reload to restart it." })).buffer as ArrayBuffer;
      ok({ status: 504, headers: [["content-type", "application/json"]], body: out });
    }, 120000);
    pending.set(id, { ok, timer });
    worker.postMessage({ type: "api", id, method, url, headers, body }, body ? [body] : []);
  });
  if (viaSw) {
    navigator.serviceWorker.addEventListener("message", async e => {
      if (e.data?.type !== "api") return;
      const { method, url, headers, body } = e.data;
      const r = await call(method, url, headers, body);
      e.ports[0].postMessage(r, [r.body]);
    });
  } else {
    const real = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const u = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.href);
      const at = u.pathname.indexOf("/api/");
      if (u.origin !== location.origin || at < 0) return real(input, init);
      const req = input instanceof Request ? input : new Request(u.href, init);
      const body = ["GET", "HEAD"].includes(req.method) ? null : await req.arrayBuffer();
      const r = await call(req.method, u.pathname.slice(at) + u.search, [...req.headers.entries()], body);
      return new Response([204, 304].includes(r.status) ? null : r.body, { status: r.status, headers: r.headers });
    };
    apiImages();
  }

  // Links to /api/* downloads (CSV/zip export) would open outside the app: fetch and save them instead.
  document.addEventListener("click", async ev => {
    const a = (ev.target as HTMLElement).closest?.("a[href]") as HTMLAnchorElement | null;
    if (!a || !a.getAttribute("href")!.startsWith("/api/")) return;
    ev.preventDefault();
    const r = await fetch(a.getAttribute("href")!);
    const name = /filename="?([^";]+)/.exec(r.headers.get("content-disposition") || "")?.[1] || "mtgstack-export";
    const url = URL.createObjectURL(await r.blob());
    const dl = Object.assign(document.createElement("a"), { href: url, download: name });
    dl.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  });

  // Make sure everything is on disk when the app is backgrounded or closed.
  const flush = () => worker.postMessage({ type: "flush" });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
  window.addEventListener("pagehide", flush);

  worker.onerror = ev => {
    console.error("worker crashed", ev);
    crashed(String((ev as ErrorEvent).message || "out of memory?"));
  };

  await ready;
  document.body.classList.add("booted");
  boot.remove();
}

/** Without a service worker an <img src="/api/..."> (Scanner Lab photos) has nobody to answer it: fetch the picture
 * through the page (see the fetch above) and show it from memory instead. */
function apiImages() {
  const fix = (img: HTMLImageElement) => {
    const src = img.getAttribute("src") || "";
    if (!src.startsWith("/api/")) return;
    img.removeAttribute("src");
    fetch(src).then(r => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then(b => { img.src = URL.createObjectURL(b); }).catch(() => {});
  };
  new MutationObserver(changes => {
    for (const m of changes) {
      if (m.type === "attributes") { if (m.target instanceof HTMLImageElement) fix(m.target); continue; }
      m.addedNodes.forEach(node => {
        if (node instanceof HTMLImageElement) fix(node);
        else if (node instanceof Element) node.querySelectorAll("img").forEach(fix);
      });
    }
  }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["src"] });
}

function crashed(msg: string) {
  const el = document.createElement("div");
  el.className = "banner crash";
  el.innerHTML = `<span>The app's engine stopped (${msg}). Your data is saved.</span>`;
  const b = Object.assign(document.createElement("button"), { className: "primary", textContent: "Reload" });
  b.onclick = () => location.reload();
  el.appendChild(b);
  document.body.prepend(el);
}

let hintEl: HTMLDivElement | null = null, hintTimer = 0;
function hint(msg: string, ms = 0) {
  if (!hintEl) {
    hintEl = document.createElement("div");
    hintEl.className = "boot-hint";
    document.body.appendChild(hintEl);
  }
  hintEl.textContent = msg;
  hintEl.style.display = msg ? "block" : "none";
  clearTimeout(hintTimer);
  if (msg && ms) hintTimer = window.setTimeout(() => hint(""), ms);
}
