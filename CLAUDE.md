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
- `mtgstack/scanner/`: detect.py (edges, any rotation), markers.py (OCR), foil.py, resolve.py (title/type/collector arbitration, NameIndex)
- `mtgstack/scanlab.py`: scan dataset in `scan-data/` (MTGSTACK_SCANS): save, label, stats/confusions, re-run harness, export
- `mtgstack/server.py`: REST API; serves web_dist with SPA fallback (`/classic` = the old single-file UI)
- `frontend/src/`: `api.ts` (typed client), `pages/` (Collection, Decks, DeckDetail, Scan, Lab),
  `components/` (Filters, CardDrawer, ImportDialog, AddCard, CardPicker), `ui.tsx`, `styles.css`

## Conventions
- A collection row is printing + finish + condition + language. Lots under it differ only by tags.
- Lots with identical attributes and tags merge automatically, so never assume a lot id survives a move.
- Deck tags: `deck:<slug>` (main), `deck:<slug>:<board>`. A card is "free" when it has no `deck:` tag.
- Auto-allocation only acts on cards entering the collection, never on existing ones (so overrides stick).
- Scanner thresholds were tuned on synthetic images only. Validate changes against real photos: read the labelled
  scans in `scan-data/` (each has `scan.json` + crops), and use Lab → Re-run (`POST /api/lab/rerun`) to compare
  before/after. Bump `scanlab.SCANNER_VERSION` when the scanner changes.

## Not verified yet
- Live Scryfall/Card Kingdom calls (built in a sandbox without access), and the scanner on real photos.
