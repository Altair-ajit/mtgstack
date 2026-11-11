// Boots Pyodide, loads the real mtgstack Python package + demo seed, and routes the UI's /api/* calls into it.
const PYO = window.PYODIDE_BASE || "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
const realFetchOnce = u => fetch(u).then(r => { if (!r.ok) throw new Error("could not load " + u); return r.json(); });
(async () => {
  const msg = t => { const el = document.getElementById("boot-msg"); if (el) el.textContent = t; };
  const PY_FILES = ["mtgstack/__init__.py", "mtgstack/db.py", "mtgstack/catalog.py", "mtgstack/collection.py",
    "mtgstack/query.py", "mtgstack/decks.py", "mtgstack/tcgplayer.py", "mtgstack/prices.py",
    "seed.py", "demo_api.py", "demo_cards.json"];
  try {
    await new Promise((ok, bad) => { const s = document.createElement("script");
      s.src = PYO + "pyodide.js"; s.onload = ok; s.onerror = () => bad(new Error("could not load Pyodide"));
      document.head.appendChild(s); });
    msg("Starting Python (first load ~10s)…");
    const py = await loadPyodide({ indexURL: PYO });
    msg("Loading SQLite…");
    await py.loadPackage(["sqlite3"]);
    msg("Loading mtgstack…");
    py.FS.mkdirTree("/app/mtgstack");
    const bundle = await realFetchOnce("app-bundle.json");
    const texts = PY_FILES.map(f => { if (!(f in bundle)) throw new Error("missing " + f); return bundle[f]; });
    PY_FILES.forEach((f, i) => py.FS.writeFile("/app/" + f, texts[i]));
    msg("Building demo collection…");
    py.runPython(`
import sys, types
httpx = types.ModuleType("httpx")
class HTTPError(Exception): pass
class Client:
    def __init__(self, *a, **k): raise RuntimeError("offline demo")
httpx.HTTPError, httpx.Client = HTTPError, Client
httpx.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline demo"))
sys.modules["httpx"] = httpx
sys.path.insert(0, "/app")
import seed, demo_api
demo_api.setup(seed.build(":memory:", open("/app/demo_cards.json").read()))
`);
    const handle = py.globals.get("demo_api").handle;

    // ---- route /api/* to Python
    const realFetch = window.fetch.bind(window);
    window.fetch = async (url, opts = {}) => {
      const u = new URL(url, location.href);
      const i = u.pathname.indexOf("/api/");
      if (i < 0) return realFetch(url, opts);
      let body = null, form = null;
      if (opts.body instanceof FormData) {
        form = {};
        for (const [k, v] of opts.body.entries()) form[k] = v instanceof Blob ? await v.text() : v;
      } else if (opts.body) body = opts.body;
      const res = handle(opts.method || "GET", decodeURIComponent(u.pathname.slice(i)), JSON.stringify(Object.fromEntries(u.searchParams)),
                         body, form ? JSON.stringify(form) : null);
      const [status, ctype, text] = res.toJs(); res.destroy();
      return new Response(text, { status, headers: { "content-type": ctype } });
    };
    window.confirm = () => true;   // sandboxed frames refuse confirm(); demo resets on reload anyway

    // ---- card images: Scryfall images can't load here, so draw a simple card face
    const cards = Object.fromEntries(JSON.parse(texts[PY_FILES.indexOf("demo_cards.json")]).map(c => [c.id, c]));
    const FRAME = { W: "#e9e2c8", U: "#9cc3e6", B: "#8f8a86", R: "#e6a184", G: "#9fc79a" };
    const escx = s => String(s || "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[ch]));
    window.MTG_IMG = (id) => {
      const c = cards[id]; if (!c) return "";
      const cols = c.colors || [], fill = cols.length > 1 ? "#d9c27a" : cols.length ? FRAME[cols[0]] : (c.type_line.includes("Land") ? "#c9b79a" : "#c4c7cb");
      const name = c.name.length > 22 ? c.name.slice(0, 21) + "…" : c.name;
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 244 340"><rect width="244" height="340" rx="12" fill="#111"/>
        <rect x="10" y="10" width="224" height="300" rx="8" fill="${fill}"/><rect x="16" y="16" width="212" height="26" rx="4" fill="#f4efe3"/>
        <text x="22" y="34" font-family="Georgia,serif" font-size="13" font-weight="bold">${escx(name)}</text>
        <text x="222" y="34" text-anchor="end" font-family="monospace" font-size="10">${escx(c.mana_cost)}</text>
        <rect x="20" y="48" width="204" height="140" fill="#2b2f36" opacity=".85"/>
        <rect x="16" y="194" width="212" height="22" rx="3" fill="#f4efe3"/>
        <text x="22" y="209" font-family="Georgia,serif" font-size="10">${escx(c.type_line)}</text>
        <rect x="20" y="220" width="204" height="84" fill="#f8f5ec"/>
        <text x="16" y="326" fill="#ddd" font-family="sans-serif" font-size="9">${escx(c.set.toUpperCase())} #${escx(c.collector_number)} · sample</text></svg>`;
      return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    };

    // ---- start the real UI
    await new Promise((ok, bad) => { const s = document.createElement("script"); s.src = "app.js"; s.onload = ok; s.onerror = bad; document.body.appendChild(s); });
    // export links can't download in the sandbox: show the text instead
    for (const id of ["ex-tcg", "ex-json"]) document.getElementById(id).addEventListener("click", async e => {
      e.preventDefault(); const r = await fetch(e.currentTarget.getAttribute("href")); exportModal(await r.text());
    });
    document.getElementById("cam-start").disabled = true;
    document.querySelector("#tab-scan .hint").insertAdjacentHTML("beforebegin",
      '<p class="panel">The scanner needs your camera and Tesseract OCR, so it only runs in the local install. To see in-progress decks fill up in this demo, use <b>Add a card</b> on the Collection tab: a card a building deck needs gets tagged straight into it.</p>');
    document.getElementById("boot").remove();
    document.querySelector('nav button[data-tab="decks"]').click();
    setTimeout(() => { const d = document.querySelector('.deck-item[data-slug="atraxa"]'); if (d) d.click(); }, 300);
  } catch (e) {
    console.error(e); msg("Couldn't start the demo: " + e.message);
  }
})();
