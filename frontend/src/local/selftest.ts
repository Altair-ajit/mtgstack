/** A scripted tour of the installed app for the iOS simulator on CI (.github/workflows/app.yml).
 *
 * Only runs when the app bundle contains selftest.json, which the CI job adds: it visits the main pages (the job
 * takes screenshots while it does), exercises the scanner with a sample card picture, and then shows everything it
 * learned on screen and in the log. The simulator has no camera, so the live camera itself is not covered. */
type Line = string;
const lines: Line[] = [];
const log = (s: string) => { lines.push(s); console.log(`SELFTEST ${s}`); };
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
const go = (path: string) => { history.pushState({}, "", path); window.dispatchEvent(new PopStateEvent("popstate")); };

async function timed<T>(what: string, f: () => Promise<T>, show: (v: T) => string): Promise<T | null> {
  const t = performance.now();
  try {
    const v = await f();
    log(`${what}: ${show(v)} (${Math.round(performance.now() - t)} ms)`);
    return v;
  } catch (e) {
    log(`${what}: FAILED ${(e as Error).name}: ${(e as Error).message} (${Math.round(performance.now() - t)} ms)`);
    return null;
  }
}

/** A photo of a card on a table, made from a public card image. */
async function sampleCard(): Promise<Blob> {
  const card = await (await fetch("https://api.scryfall.com/cards/named?exact=Morphic+Pool")).json();
  const bmp = await createImageBitmap(await (await fetch(card.image_uris.large)).blob());
  const cv = Object.assign(document.createElement("canvas"), { width: bmp.width + 300, height: bmp.height + 300 });
  const g = cv.getContext("2d")!;
  g.fillStyle = "#7a5a3a"; g.fillRect(0, 0, cv.width, cv.height); g.drawImage(bmp, 150, 150);
  return new Promise(ok => cv.toBlob(b => ok(b!), "image/jpeg", 0.9));
}

export async function run(events: string[]) {
  window.addEventListener("error", e => log(`JS error: ${e.message} @ ${String(e.filename).split("/").pop()}:${e.lineno}`));
  window.addEventListener("unhandledrejection", e => log(`unhandled rejection: ${String((e.reason && e.reason.message) || e.reason)}`));
  const probe = Object.assign(document.createElement("div"), { style: "position:fixed;top:env(safe-area-inset-top);left:0;height:env(safe-area-inset-bottom);width:1px" });
  document.body.appendChild(probe);
  const cs = getComputedStyle(probe);
  log(`screen ${innerWidth}x${innerHeight} @${devicePixelRatio}x, safe area top ${cs.top} bottom ${cs.height}`);
  probe.remove();
  log(`agent ${navigator.userAgent}`);
  let opfs = "no";
  try { await navigator.storage.getDirectory(); opfs = "yes"; } catch (e) { opfs = `no (${(e as Error).name})`; }
  log(`service worker ${"serviceWorker" in navigator ? "yes" : "no"}, cache storage ${"caches" in window ? "yes" : "no"}, OPFS ${opfs}, ` +
      `indexedDB ${"indexedDB" in window ? "yes" : "no"}, camera API ${"mediaDevices" in navigator ? "yes" : "no"}`);
  const header = document.querySelector("header.top");
  if (header) log(`header ${Math.round(header.getBoundingClientRect().height)} px tall, page ${document.documentElement.scrollWidth} px wide`);

  for (const path of ["/", "/scan?mode=batch", "/decks", "/lab"]) { go(path); await sleep(7000); }
  go("/scan?mode=batch");

  await timed("api version", async () => (await fetch("/api/version")).json(), v => JSON.stringify(v));
  await timed("card database", async () => (await fetch("/api/catalog/status")).json(),
    s => `${s.printings} printings here, remote ${s.remote ? s.remote.version : "not reachable"}, job ${s.job.state}`);
  const photo = await timed("sample photo", sampleCard, b => `${Math.round(b.size / 1024)} kB`);
  if (photo) {
    const form = (field: string) => { const fd = new FormData(); fd.append(field, photo, "a.jpg"); return fd; };
    await timed("detect", async () => (await fetch("/api/detect", { method: "POST", body: form("file") })).json(),
      r => (r.found ? `found, ${r.hint}` : `not found: ${JSON.stringify(r).slice(0, 200)}`));
    for (const n of [1, 2]) {
      await timed(`scan ${n}`, async () => (await fetch("/api/scan?save=false", { method: "POST", body: form("files") })).json(),
        r => (r.name ? `${r.name} (${r.set_code}) by ${r.recognizer}, model ${r.model_version || "none"}` : `nothing: ${JSON.stringify(r.evidence || r).slice(0, 300)}`));
      await sleep(6000);                           // the recognition model loads between scans
    }
  }
  await timed("camera", async () => {
    const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    const t = s.getVideoTracks()[0];
    const info = `${t.label} ${JSON.stringify(t.getSettings())}`;
    s.getTracks().forEach(x => x.stop());
    return info;
  }, v => v);
  log(`engine messages: ${events.join(" | ") || "none"}`);
  log("done");

  const box = document.createElement("pre");
  box.style.cssText = "position:fixed;inset:0;z-index:99999;margin:0;padding:calc(8px + env(safe-area-inset-top)) 8px 8px;background:#fff;color:#111;font:10px/1.35 ui-monospace,Menlo,monospace;white-space:pre-wrap;overflow:auto";
  box.textContent = lines.join("\n");
  document.body.appendChild(box);
}
