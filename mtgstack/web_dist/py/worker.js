/* mtgstack phone build: runs the Python app (Pyodide) in this Web Worker.
 *
 * Page  <-> worker messages:
 *   page -> {type:"api", id, method, url, headers, body}      worker -> {type:"api", id, status, headers, body}
 *   worker -> {type:"progress", msg} / {type:"ready"} / {type:"error", msg}
 *   worker -> {type:"scanner", state} / {type:"model", state, version}   (scanner libraries, recognition model)
 * Storage: IndexedDB-backed /data (user.db = your collection, catalog.sqlite = card database, scans/ = Scanner Lab).
 */
const V = new URL(self.location.href).searchParams.get("v") || "dev";
const BASE = self.location.pathname.replace(/py\/worker\.js$/, "");          // e.g. "/mtgstack/"
const PY = BASE + "py/";
// Where the card database is published. The installed app (Capacitor) ships without it and downloads it from the
// website, so the page passes ?data=<url>; on the website it sits next to the app.
const DATA = new URL(self.location.href).searchParams.get("data") || `${BASE}data/`;
const PYODIDE = `${PY}pyodide-0.29.5/`;   // keep in sync with scripts/build_pages.py
const post = (m, t) => self.postMessage(m, t || []);
const progress = msg => post({ type: "progress", msg });

let py, B, handle;
let queue = Promise.resolve();                 // one request at a time (single SQLite connection)
let saving = Promise.resolve();
const job = { state: "idle", bytes: 0, total: 0, cards: 0, error: null };
let remoteCatalog = null, remoteTried = 0;

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
let scannerReady = null, scannerFailedAt = 0;
function loadScanner() {
  if (scannerReady) return scannerReady;
  if (Date.now() - scannerFailedAt < 30000) return Promise.reject(new Error("the scanner failed to load - try again in a moment"));
  post({ type: "scanner", state: "loading" });
  scannerReady = (async () => {
    await py.loadPackage(["opencv-python", "numpy"], { messageCallback: () => {} });
    await initOcr();
    B.enable_scanner();
    post({ type: "scanner", state: "ready" });
    loadModel().catch(() => {});                 // optional and large: the scanner works while it downloads
  })();
  scannerReady.catch(e => {
    console.error("scanner load failed", e);
    scannerReady = null;
    scannerFailedAt = Date.now();
    post({ type: "scanner", state: "error", msg: String(e.message || e) });
  });
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

// ---- card recognition model (onnxruntime-web) -----------------------------------------------------------------
// Optional: data/model/ holds a small ONNX model + an index of every printing's picture (docs/scanner-model-plan.md).
// Python rectifies the card and awaits mtgMatch(); without the files the scanner simply reads the text as before.
const ORT = `${PY}ort-1.31.1-512m/`;      // keep in sync with scripts/build_pages.py (version + memory cap)
const MODEL_CACHE = "mtgstack-model";
let model = null, modelLoading = null;
self.mtgModelReady = false;

async function modelFile(cache, name, version) {
  const url = `${BASE}data/model/${name}?v=${encodeURIComponent(version)}`;
  let r = cache && (await cache.match(url));
  if (!r) {
    r = await fetch(url);
    if (!r.ok) throw new Error(`${name}: ${r.status}`);
    if (cache) await cache.put(url, r.clone());
  }
  return r;
}

function loadModel() {
  if (modelLoading) return modelLoading;
  modelLoading = (async () => {
    // Cache Storage only stores http(s) addresses ("Request url is not HTTP/HTTPS" otherwise). An installed app
    // serves itself from capacitor://, where the model files are part of the app and need no caching anyway.
    const cache = self.caches && /^https?:$/.test(self.location.protocol) ? await caches.open(MODEL_CACHE).catch(() => null) : null;
    const metaUrl = `${BASE}data/model/model.json`;
    let meta;
    try {
      const r = await fetch(metaUrl, { cache: "no-store" });
      if (r.status === 404) return null;                          // no model published: OCR only
      if (!r.ok) throw new Error(`model.json: ${r.status}`);
      if (cache) await cache.put(metaUrl, r.clone());
      meta = await r.json();
    } catch (e) {                                                  // offline: use the copy from last time
      const hit = cache && (await cache.match(metaUrl));
      if (!hit) throw e;
      meta = await hit.json();
    }
    post({ type: "model", state: "loading", version: meta.model_version });
    const v = meta.model_version;
    const [onnx, bin, ids] = await Promise.all([
      modelFile(cache, "embedder.onnx", v).then(r => r.arrayBuffer()),
      modelFile(cache, "embeddings.bin", v).then(r => r.arrayBuffer()),
      modelFile(cache, "embeddings-ids.json", v).then(r => r.json()),
    ]);
    for (const req of cache ? await cache.keys() : [])             // drop files of older model versions
      if (req.url.includes("?v=") && !req.url.endsWith(`?v=${encodeURIComponent(v)}`)) await cache.delete(req);
    importScripts(`${ORT}ort.wasm.min.js`);
    self.ort.env.wasm.wasmPaths = ORT;
    self.ort.env.wasm.numThreads = 1;                              // no cross-origin isolation on GitHub Pages
    const session = await self.ort.InferenceSession.create(new Uint8Array(onnx), { executionProviders: ["wasm"] });
    const dim = ids.dim, count = ids.keys.length;
    if (bin.byteLength !== dim * count) throw new Error("model index does not match its id list");
    model = { session, index: new Int8Array(bin), dim, count, keys: ids.keys, groups: Int32Array.from(ids.groups),
              version: ids.model_version || v, input: meta.input || [312, 224] };
    self.mtgModelReady = true;
    post({ type: "model", state: "ready", version: model.version, count });
    return model;
  })();
  modelLoading.catch(e => {
    console.error("recognition model not loaded", e);
    post({ type: "model", state: "error", msg: String(e.message || e) });
  });
  return modelLoading;
}

/** One embedding -> the neighbours Python's match_from_hints expects: every printing of the best artwork
 * (best first), then the best row of each of the next 5 artworks. Mirrors Recognizer.match in recognizer.py. */
function neighbours(vec) {
  const { index, dim, count, keys, groups } = model;
  let norm = 0;
  for (let j = 0; j < dim; j++) norm += vec[j] * vec[j];
  norm = Math.sqrt(norm) || 1;
  const q = new Int32Array(dim);
  for (let j = 0; j < dim; j++) q[j] = Math.max(-127, Math.min(127, Math.round(127 * vec[j] / norm)));
  const sims = new Float32Array(count);
  let best = 0;
  for (let i = 0, o = 0; i < count; i++, o += dim) {
    let s = 0;
    for (let j = 0; j < dim; j++) s += index[o + j] * q[j];
    sims[i] = s / 16129;
    if (sims[i] > sims[best]) best = i;
  }
  const printing = i => keys[i].split(".")[0];
  const g0 = groups[best], same = [];
  for (let i = 0; i < count; i++) if (groups[i] === g0) same.push(i);
  same.sort((a, b) => sims[b] - sims[a]);
  const out = same.slice(0, 60).map(i => ({ printing_id: printing(i), score: sims[i], group: g0 }));
  const seen = new Set([g0]);
  for (let k = 0; k < 5; k++) {
    let top = -1;
    for (let i = 0; i < count; i++) if (!seen.has(groups[i]) && (top < 0 || sims[i] > sims[top])) top = i;
    if (top < 0) break;
    seen.add(groups[top]);
    out.push({ printing_id: printing(top), score: sims[top], group: groups[top] });
  }
  return out;
}

// Awaited from Python (mtgstack.browser._picture_matcher): n model inputs (float32 NCHW, RGB 0..1) -> JSON
self.mtgMatch = async (bytes, n) => {
  const [h, w] = model.input;
  const data = new Float32Array(bytes.slice().buffer, 0, n * 3 * h * w);
  const res = await model.session.run({ image: new self.ort.Tensor("float32", data, [n, 3, h, w]) });
  const emb = res.embedding.data, matches = [];
  for (let i = 0; i < n; i++) matches.push(neighbours(emb.subarray(i * model.dim, (i + 1) * model.dim)));
  return JSON.stringify({ version: model.version, matches });
};

// ---- card database file on disk (OPFS) -------------------------------------------------------------------------
// The catalog is ~250 MB. Holding it in memory (and copying it into IndexedDB) can exhaust an iPhone's memory, so
// when the browser supports synchronous OPFS file handles, SQLite reads and writes the file on disk directly through
// this tiny Emscripten filesystem: /catalog/catalog.sqlite -> whichever OPFS file is current.
const CAT_DIR = "/catalog", CAT_NAME = "catalog.sqlite";
const opfs = { root: null, files: new Map(), current: null };   // files: name in /catalog -> SyncAccessHandle

async function opfsProbe() {
  try {
    const root = await navigator.storage.getDirectory();
    const fh = await root.getFileHandle("mtgstack-probe", { create: true });
    const h = await fh.createSyncAccessHandle();
    const ok = typeof h.getSize() === "number";          // older Safari had an async (promise) variant
    h.close();
    await root.removeEntry("mtgstack-probe");
    return ok ? root : null;
  } catch { return null; }
}

// SQLite reads 4 KB pages; each OPFS read has fixed overhead, so read 1 MB blocks and keep the most recent 48 MB.
const BLOCK = 1 << 20, MAX_BLOCKS = 48;
const blockCache = new WeakMap();                        // SyncAccessHandle -> Map(blockIndex -> Uint8Array)
function blocks(h) { let m = blockCache.get(h); if (!m) blockCache.set(h, (m = new Map())); return m; }
function getBlock(h, i) {
  const m = blocks(h);
  let b = m.get(i);
  if (b) { m.delete(i); m.set(i, b); return b; }        // LRU: move to newest
  const len = Math.max(0, Math.min(BLOCK, h.getSize() - i * BLOCK));
  b = new Uint8Array(len);
  if (len) h.read(b, { at: i * BLOCK });
  m.set(i, b);
  if (m.size > MAX_BLOCKS) m.delete(m.keys().next().value);
  return b;
}
function cachedRead(h, out, position) {
  let done = 0;
  while (done < out.length) {
    const pos = position + done, i = Math.floor(pos / BLOCK), b = getBlock(h, i), off = pos - i * BLOCK;
    if (off >= b.length) break;                          // end of file
    const n = Math.min(out.length - done, b.length - off);
    out.set(b.subarray(off, off + n), done);
    done += n;
  }
  return done;
}
function cachedWrite(h, data, position) {
  const n = h.write(data, { at: position });
  const m = blocks(h);
  for (let i = Math.floor(position / BLOCK); i <= Math.floor((position + data.length) / BLOCK); i++) m.delete(i);
  return n;
}

function OpfsFS(FS) {
  const E = n => new FS.ErrnoError(n);                    // 44 ENOENT, 28 EINVAL, 63 EPERM
  const handle = node => { const h = opfs.files.get(node.name); if (!h) throw E(44); return h; };
  const fs = {
    mount() { return fs.createNode(null, "/", 0o40777, 0); },
    createNode(parent, name, mode, dev) {
      const node = FS.createNode(parent, name, mode, dev);
      node.node_ops = fs.node_ops;
      node.stream_ops = fs.stream_ops;
      node.timestamp = Date.now();
      return node;
    },
    node_ops: {
      getattr(node) {
        const dir = FS.isDir(node.mode), t = new Date(node.timestamp);
        const size = dir ? 4096 : handle(node).getSize();
        return { dev: 1, ino: node.id, mode: node.mode, nlink: 1, uid: 0, gid: 0, rdev: 0, size,
                 atime: t, mtime: t, ctime: t, blksize: 4096, blocks: Math.ceil(size / 4096) };
      },
      setattr(node, attr) {
        if (attr.size !== undefined && !FS.isDir(node.mode)) { const h = handle(node); h.truncate(attr.size); blocks(h).clear(); }
        if (attr.timestamp !== undefined) node.timestamp = attr.timestamp;
        if (attr.mtime !== undefined) node.timestamp = attr.mtime;
      },
      lookup(parent, name) {
        if (!opfs.files.has(name)) throw E(44);
        return fs.createNode(parent, name, 0o100666, 0);
      },
      mknod() { throw E(63); },
      rename() { throw E(63); },
      unlink() { throw E(63); },
      rmdir() { throw E(63); },
      readdir() { return [".", "..", ...opfs.files.keys()]; },
      symlink() { throw E(63); },
    },
    stream_ops: {
      read(stream, buffer, offset, length, position) {
        return cachedRead(handle(stream.node), buffer.subarray(offset, offset + length), position);
      },
      write(stream, buffer, offset, length, position) {
        return cachedWrite(handle(stream.node), buffer.subarray(offset, offset + length), position);
      },
      llseek(stream, offset, whence) {
        let p = offset;
        if (whence === 1) p += stream.position;
        else if (whence === 2) p += handle(stream.node).getSize();
        if (p < 0) throw E(28);
        return p;
      },
      fsync(stream) { handle(stream.node).flush(); },
    },
  };
  return fs;
}

async function readText(name) {
  try { return await (await (await opfs.root.getFileHandle(name)).getFile()).text(); } catch { return null; }
}
async function writeText(name, text) {
  const h = await (await opfs.root.getFileHandle(name, { create: true })).createSyncAccessHandle();
  const b = new TextEncoder().encode(text);
  h.truncate(0); h.write(b, { at: 0 }); h.flush(); h.close();
}
async function openOpfs(name) {
  return (await opfs.root.getFileHandle(name, { create: true })).createSyncAccessHandle();
}

/** Mount /catalog backed by OPFS (returns its path), or null to fall back to /data (IndexedDB, in memory). */
async function setupCatalogStorage() {
  opfs.root = await opfsProbe();
  if (!opfs.root) return null;
  opfs.current = (await readText("catalog-current.txt")) || "catalog-a.sqlite";
  opfs.files.set(CAT_NAME, await openOpfs(opfs.current));
  py.FS.mkdirTree(CAT_DIR);
  py.FS.mount(OpfsFS(py.FS), {}, CAT_DIR);
  return `${CAT_DIR}/${CAT_NAME}`;
}

// ---- card database ------------------------------------------------------------------------------------------------
async function fetchRemoteCatalog() {
  remoteTried = Date.now();
  try {
    const r = await fetch(`${DATA}catalog.json`, { cache: "no-store" });
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
    const r = await fetch(`${DATA}${meta.file}`, { cache: "no-store" });
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
    if (opfs.root) {
      const next = opfs.current === "catalog-a.sqlite" ? "catalog-b.sqlite" : "catalog-a.sqlite";
      const h = await openOpfs(next);
      h.truncate(0);
      let at = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        h.write(value, { at });
        at += value.length;
      }
      h.flush();
      if (meta.size && at !== meta.size) { h.close(); throw new Error(`download incomplete (${at} of ${meta.size} bytes)`); }
      const old = opfs.current;
      await enqueue(async () => {
        B.close();                                     // SQLite lets go of the old file
        opfs.files.get(CAT_NAME).close();
        opfs.files.set(CAT_NAME, h);
        opfs.current = next;
        B.after_catalog_swap(meta.version, meta.printings, null);
      });
      await writeText("catalog-current.txt", next);
      try { await opfs.root.removeEntry(old); } catch { /* leftover is harmless; reused next time */ }
    } else {
      // No OPFS: build the file in one preallocated buffer (no repeated growth/copies), then hand it to the FS.
      const buf = new Uint8Array(meta.size || 0);
      let at = 0, extra = [];
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (at + value.length <= buf.length) buf.set(value, at); else extra.push(value);
        at += value.length;
      }
      const data = extra.length ? new Uint8Array(await new Blob([buf.subarray(0, buf.length), ...extra]).arrayBuffer()) : buf.subarray(0, at);
      const tmp = "/data/catalog.new";
      py.FS.writeFile(tmp, data, { canOwn: true });
      await enqueue(async () => { B.close(); B.after_catalog_swap(meta.version, meta.printings, tmp); });
    }
    await save();
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
    await loadScanner();
    // Scans block this thread, so a model still loading would never finish between them: give it a moment first
    // (it is quick once cached; a first download carries on in the background and the scan reads the text).
    if (!model && modelLoading) await Promise.race([modelLoading.catch(() => {}), new Promise(r => setTimeout(r, 4000))]);
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
    // not known yet (still being asked at start-up, or the device was offline): ask again, at most once a minute
    if (!remoteCatalog && Date.now() - remoteTried > 60000) fetchRemoteCatalog();
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
    const catPath = await setupCatalogStorage();
    if (catPath) {
      try { py.FS.unlink("/data/catalog.sqlite"); await save(); } catch { /* none from an older version */ }
    }
    py.runPython("import sys, os; sys.path.insert(0, '/app')");
    py.globals.get("os").environ.__setitem__("MTGSTACK_CATALOG_DB", catPath || "/data/catalog.sqlite");
    B = py.pyimport("mtgstack.browser");
    B.start();
    handle = B.handle;
    post({ type: "ready" });
    fetchRemoteCatalog();
    setTimeout(() => loadScanner().catch(() => {}), 1500);   // warm up the scanner in the background
  } catch (e) {
    console.error(e);
    post({ type: "error", msg: String(e.message || e) });
    throw e;
  }
})();
