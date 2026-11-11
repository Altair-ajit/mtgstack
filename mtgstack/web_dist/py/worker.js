/* mtgstack phone build: runs the Python app (Pyodide) in this Web Worker.
 *
 * Page  <-> worker messages:
 *   page -> {type:"api", id, method, url, headers, body}      worker -> {type:"api", id, status, headers, body}
 *   worker -> {type:"progress", msg} / {type:"ready"} / {type:"error", msg}
 * Storage: IndexedDB-backed /data (user.db = your collection, catalog.sqlite = card database, scans/ = Scanner Lab).
 */
const V = new URL(self.location.href).searchParams.get("v") || "dev";
const BASE = self.location.pathname.replace(/py\/worker\.js$/, "");          // e.g. "/mtgstack/"
const PY = BASE + "py/";
const PYODIDE = `${PY}pyodide-0.29.5/`;   // keep in sync with scripts/build_pages.py
const post = (m, t) => self.postMessage(m, t || []);
const progress = msg => post({ type: "progress", msg });

let py, B, handle;
let queue = Promise.resolve();                 // one request at a time (single SQLite connection)
let saving = Promise.resolve();
const job = { state: "idle", bytes: 0, total: 0, cards: 0, error: null };
let remoteCatalog = null;

// ---- persistence -----------------------------------------------------------------------------------------------
function syncfs(populate) {
  return new Promise((ok, bad) => py.FS.syncfs(populate, e => (e ? bad(e) : ok())));
}
/** Write changed files (user.db, new scans) to IndexedDB. Serialized; callers await it so a change is durable
 * before the UI hears it succeeded (iOS may kill a backgrounded web app at any moment). */
function save() {
  saving = saving.then(() => syncfs(false)).catch(e => console.error("save failed", e));
  return saving;
}

// ---- scanner deps (loaded after the app is usable) ---------------------------------------------------------------
let scannerReady = null;
function loadScanner() {
  if (scannerReady) return scannerReady;
  scannerReady = (async () => {
    await py.loadPackage(["opencv-python", "numpy"], { messageCallback: () => {} });
    await initOcr();
    B.enable_scanner();
  })();
  scannerReady.catch(e => { console.error("scanner load failed", e); scannerReady = null; });
  return scannerReady;
}

let tess = null;
async function initOcr() {
  const simd = WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11]));
  const core = simd ? "tesseract-core-simd-lstm" : "tesseract-core-lstm";
  importScripts(`${PY}tesseract/${core}.js?v=${V}`);
  const M = await self.TesseractCore({ locateFile: f => `${PY}tesseract/${f}?v=${V}` });
  const gz = await fetch(`${PY}tesseract/eng.traineddata.gz?v=${V}`);
  if (!gz.ok) throw new Error("could not load OCR language data");
  const data = new Uint8Array(await new Response(gz.body.pipeThrough(new DecompressionStream("gzip"))).arrayBuffer());
  M.FS.writeFile("/eng.traineddata", data);
  const api = new M.TessBaseAPI();
  if (api.Init(null, "eng", 1) === -1) throw new Error("Tesseract init failed");
  api.SetVariable("user_defined_dpi", "300");
  tess = { M, api };
}
// Called synchronously from Python (mtgstack.browser._ocr): PNG bytes + page-segmentation mode -> TSV text
self.mtgOcr = (png, psm) => {
  const { M, api } = tess;
  M.FS.writeFile("/input", png);
  api.SetVariable("tessedit_pageseg_mode", String(psm));
  if (api.SetImageFile(1, 0) === 1) return "";
  api.Recognize(null);
  return api.GetTSVText() || "";
};

// ---- card database ------------------------------------------------------------------------------------------------
async function fetchRemoteCatalog() {
  try {
    const r = await fetch(`${BASE}data/catalog.json`, { cache: "no-store" });
    remoteCatalog = r.ok ? await r.json() : null;
  } catch { remoteCatalog = null; }
  return remoteCatalog;
}

async function downloadCatalog() {
  if (job.state === "running") return;
  Object.assign(job, { state: "running", bytes: 0, total: 0, cards: 0, error: null });
  try {
    const meta = remoteCatalog || (await fetchRemoteCatalog());
    if (!meta) throw new Error("card database not published yet (check the GitHub Actions run)");
    const r = await fetch(`${BASE}data/${meta.file}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`download failed (${r.status})`);
    job.total = meta.size_gz || Number(r.headers.get("content-length")) || 0;
    const counter = new TransformStream({
      transform(chunk, ctl) {
        job.bytes += chunk.byteLength;
        if (job.total) job.cards = Math.round(meta.printings * Math.min(1, job.bytes / job.total));
        ctl.enqueue(chunk);
      },
    });
    const reader = r.body.pipeThrough(counter).pipeThrough(new DecompressionStream("gzip")).getReader();
    const tmp = "/data/catalog.new";
    const stream = py.FS.open(tmp, "w");
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        py.FS.write(stream, value, 0, value.length);
      }
    } finally { py.FS.close(stream); }
    await enqueue(async () => { B.swap_catalog(tmp, meta.version, meta.printings); });
    await syncfs(false);
    Object.assign(job, { state: "done", cards: meta.printings });
  } catch (e) {
    console.error(e);
    Object.assign(job, { state: "error", error: String(e.message || e) });
  }
}

// ---- requests -------------------------------------------------------------------------------------------------------
function enqueue(fn) {
  const p = queue.then(fn);
  queue = p.catch(() => {});
  return p;
}
const json = (status, obj) => ({ status, headers: [["content-type", "application/json"]], body: new TextEncoder().encode(JSON.stringify(obj)) });
const NEEDS_SCANNER = /^\/api\/(scan|detect|lab\/(upload|rerun))/;

async function api({ method, url, headers, body }) {
  const path = url.split("?")[0];
  // Card database download is handled here (streamed into storage), not in Python.
  if (path === "/api/catalog/sync" && method === "POST") { downloadCatalog(); return json(200, job); }
  if (path === "/api/prices/refresh" && method === "POST") {
    const remote = await fetchRemoteCatalog();
    const local = await enqueue(() => B.catalog_version());
    if (remote && remote.version !== local) { downloadCatalog(); return json(200, { updating: true, version: remote.version }); }
    return json(200, { up_to_date: true, version: local });
  }
  if (NEEDS_SCANNER.test(path)) {
    progress("Loading the scanner (first time only)…");
    await loadScanner();
  }
  const res = await enqueue(async () => {
    const r = await handle(method, url, headers, body ? new Uint8Array(body) : null);
    const [status, hdrs, out] = r.toJs();
    r.destroy();
    return { status, headers: hdrs, body: out };
  });
  if (path === "/api/catalog/status" && res.status === 200) {
    const s = JSON.parse(new TextDecoder().decode(res.body));
    s.job = job;
    s.local_version = await enqueue(() => B.catalog_version());
    s.remote = remoteCatalog;
    s.update_available = !!(remoteCatalog && remoteCatalog.version !== s.local_version);
    return json(200, s);
  }
  if (method !== "GET" && method !== "HEAD") await save();
  return res;
}

self.onmessage = async e => {
  const m = e.data;
  if (m.type === "flush") { if (py) await save(); return; }
  if (m.type !== "api") return;
  try {
    await ready;
    const r = await api(m);
    const buf = r.body.buffer.slice(r.body.byteOffset, r.body.byteOffset + r.body.byteLength);
    post({ type: "api", id: m.id, status: r.status, headers: r.headers, body: buf }, [buf]);
  } catch (err) {
    console.error(err);
    const body = new TextEncoder().encode(JSON.stringify({ detail: String(err.message || err) })).buffer;
    post({ type: "api", id: m.id, status: 500, headers: [["content-type", "application/json"]], body }, [body]);
  }
};

// ---- boot -----------------------------------------------------------------------------------------------------------
const ready = (async () => {
  try {
    progress("Starting Python…");
    importScripts(`${PYODIDE}pyodide.js`);
    py = await self.loadPyodide({ indexURL: PYODIDE });
    progress("Loading libraries…");
    await py.loadPackage(["fastapi", "sqlite3", "httpx", "pydantic"], { messageCallback: () => {} });
    const [mp, app] = await Promise.all([
      fetch(`${PY}wheels/python_multipart.whl?v=${V}`).then(r => r.arrayBuffer()),
      fetch(`${PY}app.zip?v=${V}`).then(r => r.arrayBuffer()),
    ]);
    py.unpackArchive(mp, "wheel");
    py.FS.mkdirTree("/app");
    py.unpackArchive(app, "zip", { extractDir: "/app" });
    progress("Opening your collection…");
    py.FS.mkdirTree("/data");
    py.FS.mount(py.FS.filesystems.IDBFS, {}, "/data");
    await syncfs(true);
    py.runPython("import sys; sys.path.insert(0, '/app')");
    B = py.pyimport("mtgstack.browser");
    B.start();
    handle = B.handle;
    post({ type: "ready" });
    fetchRemoteCatalog();
    setTimeout(() => loadScanner(), 1500);          // warm up the scanner in the background
  } catch (e) {
    console.error(e);
    post({ type: "error", msg: String(e.message || e) });
    throw e;
  }
})();
