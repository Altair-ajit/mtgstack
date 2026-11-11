/** Phone build (GitHub Pages): no server. A service worker routes /api/* here, and this page forwards each
 * request to the Python app running in a Web Worker (public/py/worker.js). */
export const LOCAL = import.meta.env.VITE_LOCAL === "1";
export const BUILD = import.meta.env.VITE_BUILD || "dev";
const BASE = import.meta.env.BASE_URL;

type Pending = { port: MessagePort };

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
  if (!("serviceWorker" in navigator)) { show("This browser can't run the offline app (no service workers).", true); throw new Error("no sw"); }

  try { await navigator.storage?.persist?.(); } catch { /* best effort: keep data from being evicted */ }

  const reg = await navigator.serviceWorker.register(`${BASE}sw.js?v=${BUILD}`, { scope: BASE });
  if (!navigator.serviceWorker.controller) {
    await new Promise<void>(ok => {
      navigator.serviceWorker.addEventListener("controllerchange", () => ok(), { once: true });
      setTimeout(ok, 4000);
    });
    if (!navigator.serviceWorker.controller) { location.reload(); await new Promise(() => {}); }
  }
  void reg;

  const worker = new Worker(`${BASE}py/worker.js?v=${BUILD}`);
  const pending = new Map<number, Pending>();
  let n = 0;
  const ready = new Promise<void>((ok, bad) => {
    worker.onmessage = e => {
      const m = e.data;
      if (m.type === "progress") { if (!document.body.classList.contains("booted")) show(m.msg); else hint(m.msg); }
      else if (m.type === "ready") ok();
      else if (m.type === "error") { show(`Couldn't start: ${m.msg}`, true); bad(new Error(m.msg)); }
      else if (m.type === "api") {
        const p = pending.get(m.id);
        pending.delete(m.id);
        p?.port.postMessage({ status: m.status, headers: m.headers, body: m.body }, [m.body]);
        hint("");
      }
    };
  });
  navigator.serviceWorker.addEventListener("message", e => {
    if (e.data?.type !== "api") return;
    const id = ++n;
    pending.set(id, { port: e.ports[0] });
    const { method, url, headers, body } = e.data;
    worker.postMessage({ type: "api", id, method, url, headers, body }, body ? [body] : []);
  });

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

  await ready;
  document.body.classList.add("booted");
  boot.remove();
}

let hintEl: HTMLDivElement | null = null;
function hint(msg: string) {
  if (!hintEl) {
    hintEl = document.createElement("div");
    hintEl.className = "boot-hint";
    document.body.appendChild(hintEl);
  }
  hintEl.textContent = msg;
  hintEl.style.display = msg ? "block" : "none";
}
