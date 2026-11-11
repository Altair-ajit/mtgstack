# mtgstack — notes for Claude Code

MTG collection manager: tag-based storage, a marker-based card scanner, decks (incl. in-progress decks that
auto-tag new cards), TCGplayer/Arena interop, price tracking. Python 3.10+, FastAPI, SQLite, OpenCV, Tesseract.
Read README.md for features and AGENTS.md for the CLI/API contract.

## Environment
- Do all dev work in **WSL (Ubuntu)** on Rajit's Windows machine, not native Windows.
- System deps: `sudo apt install -y python3-venv tesseract-ocr`
- `./run.sh` sets up `.venv` on first run. Flags: `--dev` (auto-reload), `--phone` (LAN + HTTPS), `--test`.
- The site is at http://localhost:8765 (WSL forwards localhost to Windows). For phone testing in WSL NAT mode,
  `./run.sh --phone` prints the netsh portproxy commands.

## Commands
- Tests: `./run.sh --test` (or `.venv/bin/python -m pytest -q`). Keep them green; add a test for new behavior.
- CLI: `.venv/bin/mtgstack --help`. Every command takes `--json`. Use `--offline` to avoid Scryfall calls.
- Browser demo (Pyodide): `python3 demo/build.py` builds it into `demo/dist`.

## Layout
- `mtgstack/db.py`: schema, views (`lot_view`, `entry_view`), migrations in `_migrate()`
- `mtgstack/collection.py`: lots, tags, move/split/merge; `add()` triggers deck auto-allocation
- `mtgstack/decks.py`: decklists, reconcile (have / to_move / to_buy), pull, send, auto_allocate
- `mtgstack/query.py`: query language over lots and deck entries
- `mtgstack/scanner/`: detect.py (card edges, any rotation), markers.py (OCR), foil.py, resolve.py
- `mtgstack/server.py` (REST + web UI in `mtgstack/web/`), `mtgstack/cli.py`
- `tests/`: synthetic card photos come from `tests/synth.py`; the fixture catalog is in `tests/fixtures/`

## Conventions
- Lots with identical attributes and tags merge automatically, so never assume a lot id survives a move.
- Deck tags: `deck:<slug>` (main), `deck:<slug>:<board>`. A card is "free" when it has no `deck:` tag.
- Auto-allocation only acts on cards entering the collection, never on existing ones (so overrides stick).
- Scanner thresholds and regions are tuned on synthetic images only; validate changes against real photos.

## Not verified yet
- Live Scryfall and Card Kingdom calls (built in a sandbox without access), and the scanner on real photos.
