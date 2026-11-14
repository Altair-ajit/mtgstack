# mtgstack — notes for Claude Code

MTG collection manager styled like a TCGplayer collection: a searchable collection with filters, decks that are
mini collections (in-progress decks auto-claim new cards), import from TCGplayer/ManaBox/Moxfield/text, a camera
scanner, and prices. Backend: Python 3.10+, FastAPI, SQLite, OpenCV, Tesseract. UI: React + TypeScript + Vite
in `frontend/`, built into `mtgstack/web_dist` (committed, so users only need Python).
README.md covers features; AGENTS.md covers the CLI and API contract.

## Environment
- Do all dev work in **WSL (Ubuntu)** on Rajit's Windows machine, not native Windows.
- System deps: `sudo apt install -y python3-venv tesseract-ocr`. Node 20+ only for UI work.
- `./run.sh` sets up `.venv` on first run. Flags: `--dev` (auto-reload), `--phone` (LAN + HTTPS), `--test`.
- Site: http://localhost:8765. UI dev server: `cd frontend && npm run dev` serves :5173 and proxies /api to :8765.
- First run: the in-app banner (or `mtgstack catalog sync`) downloads Scryfall's bulk data, ~500 MB, once.

## Commands
- Tests: `./run.sh --test`. Frontend: `cd frontend && npx tsc -b && npm run build` (outputs to mtgstack/web_dist).
- Keep tests green and add a test for new behaviour. After UI changes, rebuild web_dist and commit it.

## Layout
- `mtgstack/db.py`: tables plus views (`lot_view`, `entry_view`). Views are dropped and recreated on every start;
  column migrations go in `_migrate()`. Prices: `current_prices` holds today's price for every printing;
  `prices` holds daily history for owned/wanted printings only.
- `mtgstack/catalog.py`: Scryfall client, bulk sync (with progress), batch lookups
- `mtgstack/importer.py`: parse (CSV aliases / text lines), resolve (local, bulk), commit (collection or deck)
- `mtgstack/browse.py`: collection page rows, facets, card detail, name autocomplete
- `mtgstack/collection.py`: lots, tags, move/split/merge/update; `add()` triggers deck auto-allocation
- `mtgstack/decks.py`: decklists, reconcile (have / to_move / to_buy), pull, send, auto_allocate
- `mtgstack/query.py`: Scryfall-style query language over lots and deck entries
- `mtgstack/scanner/`: detect.py (edges, any rotation; `proposals` = alternative outlines the model chooses among), markers.py (OCR), foil.py, resolve.py (title/type/collector arbitration, NameIndex),
  recognizer.py (optional ONNX picture match, tried before OCR), modeldata.py (manifest rows + index file format)
- `mtgstack/server.py`: REST API; serves web_dist with SPA fallback (`/classic` = the old single-file UI)
- `frontend/src/`: `api.ts` (typed client), `pages/` (Collection, Decks, DeckDetail, Scan),
  `components/` (Filters, CardDrawer, ImportDialog, AddCard, CardPicker), `ui.tsx`, `styles.css`

## Conventions
- A collection row is printing + finish + condition + language. Lots under it differ only by tags.
- Lots with identical attributes and tags merge automatically, so never assume a lot id survives a move.
- Deck tags: `deck:<slug>` (main), `deck:<slug>:<board>`. A card is "free" when it has no `deck:` tag.
- A deck's list follows its cards' tags: `collection.move`/`add` call `decks.lists_follow`, so a card tagged into a deck
  by any route is listed (`_cover`), and one that leaves takes off what was only listed for being there (`placed`).
  What a decklist asked for stays. Change deck tags through `collection.move`, not by writing `lot_tags`.
- Auto-allocation only acts on cards entering the collection, never on existing ones (so overrides stick).
- Scanner thresholds were tuned on synthetic images only. Validate changes against real photos: read the labelled
  photos you have by eye (the Scanner Lab that saved and replayed scans was removed: scans are no longer kept
  anywhere, and `/api/scan` drops the photos once read). Bump `mtgstack.scanner.VERSION` when the scanner changes.

## Not verified yet
- Live Scryfall/Card Kingdom calls (built in a sandbox without access), and the scanner on real photos.

## Phone build (GitHub Pages, runs in the browser)
- `mtgstack/browser.py` runs the real FastAPI app in Pyodide (worker: `frontend/public/py/worker.js`; service worker
  `frontend/public/sw.js` routes `/api/*`). Constraints: no threads (inlined), no rapidfuzz (`mtgstack/_fuzz.py`),
  HTTP via sync XHR, OCR via `markers.set_ocr_backend` (Tesseract WASM). Keep server code free of other native deps.
- Split DB when `MTGSTACK_CATALOG_DB` is set: catalog tables in attached `cat`, user tables in main, TEMP views.
- Build: `python scripts/build_pages.py --out site --base /mtgstack/` + `python -m mtgstack.cli build-catalog site/data`.
  Test locally: serve the parent dir so the site is at /mtgstack/, then drive it with Playwright (see tests notes).
- Pyodide version is pinned in both `scripts/build_pages.py` and `worker.js` (PYODIDE path).

## Card-recognition model
Plan and current status are in `docs/scanner-model-plan.md`. Pipeline (training venv `~/mtg-data/.venv-train`, data in
`~/mtg-data`): `scripts/fetch_images.py` -> `train_embedder.py` -> `export_onnx.py` -> `build_index.py` (writes
`<run>/model`, the directory `MTGSTACK_MODEL` points at; default `~/.mtgstack/model`) (the Lab-based `eval_lab.py` is gone with the Lab).
The scanner uses the model when it is installed and `onnxruntime` is importable, otherwise OCR only. Tests never load
an installed model (conftest points `MTGSTACK_MODEL` at nothing); they use `recognizer=None` or a fake.
Phone build: worker.js runs the model with onnxruntime-web (`py/ort-<ver>-512m/`, version pinned in worker.js `ORT` and
checked by build_pages.py, which also patches its 4 GB shared-memory maximum down to 512 MB: iPhones refuse 4 GB) when `data/model/` is published (`build_pages.py --model DIR`; CI uses the committed
`model/`, which `run.sh` also uses by default). JS can only be awaited, so the server awaits `app.state.picture_matcher` in `prepare()` and
passes `Scanner.scan(prepared=...)`. Not done yet: a test on a real phone, new printings in the index.

## Installed app (Capacitor, iOS + Android)
Setup, status and what is not done: `docs/app.md`. Projects in `frontend/android` and `frontend/ios`, CI in
`.github/workflows/app.yml` (debug APK + iOS compile on push; TestFlight upload is a manual run that needs Apple secrets).
The app packages the phone build made with `build_pages.py --native`: iOS app web views have no service worker, so
`frontend/src/local/boot.ts` catches the page's own `/api` fetches instead (and loads `/api` images itself). Anything
new that reaches `/api` other than through `fetch` or an `<img>` will work on the website and fail in the app.
