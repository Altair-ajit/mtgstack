# Driving mtgstack as an agent

Use `mtgstack --json ...` (CLI) or the REST API (`mtgstack serve`, OpenAPI at `/docs`). Both run on the same SQLite file
(`~/.mtgstack/collection.db`, override with `--db` or `$MTGSTACK_DB`). Add `--offline` to the CLI so it never calls Scryfall.

## Read
| Want | CLI | API |
|---|---|---|
| Lots matching a query | `mtgstack --json q 'deck:atraxa is:foil'` | `GET /api/query?q=...&view=lots` |
| Grouped card → printing → lot | `mtgstack --json stacks '...'` | `GET /api/query?q=...` |
| Query syntax | `mtgstack help query` | `GET /api/help/query` |
| Tags + counts | `mtgstack --json tags` | `GET /api/tags` |
| Deck status | `mtgstack --json deck show SLUG` | `GET /api/decks/SLUG` |
| Prices for a printing | `mtgstack --json prices show PRINTING_ID` | `GET /api/prices/PRINTING_ID` |
| What decks still need (owned or not) | `mtgstack --json wants 'is:building status:need'` | `GET /api/entries?q=...` |
| Collection page rows (grouped, filtered, faceted) | — | `GET /api/cards?q=&set=&color=&type=&finish=&condition=&tag=&deck=&sort=&page=&size=` |
| One card: prices, copies, printings, decks | — | `GET /api/card/{printing_id}` |
| Recent changes | `mtgstack --json events -n 50` | `GET /api/events` |

Lot rows include: `lot_id, qty, finish, condition, language, tags` (space-separated), `printing_id` (Scryfall id), `oracle_id`,
`name, set_code, collector_number, rarity, type_line, mana_cost, cmc, colors, color_identity, price` (TCGplayer market for that finish).

## Write. Preview first, then apply.
- `split-dupes` previews unless `--apply` is given (API: `dry_run` defaults to true).
- `deck pull --dry-run` and `import tcgplayer --dry-run` preview.
- `move LOT --qty N --add T --remove T` splits the lot.
- `send LOT [--deck SLUG --board B]` moves copies out of their deck (to the collection, or another deck). Use it to override an auto-allocation.
- Adding cards (`add`, scan `--add`, CSV import, `POST /api/lots`) can auto-tag them into in-progress decks. The response lists them under `allocations`. Pass `auto_allocate: false` to the API to skip that. `--remove 'deck:*'` removes by prefix.
- Every change is appended to `events` with its parameters. To undo, apply the reverse move.

## Import
- `POST /api/import/preview {text, filename}` parses and matches rows without saving anything. Rows have `status` matched / guessed / unmatched.
- `POST /api/import/commit {rows, dest}`, where dest is `{type:'collection', tags:[]}` or `{type:'deck', name, format, status, owned}`.

## Conventions
- Main board: `deck:<slug>`. Other boards: `deck:<slug>:side`, `deck:<slug>:commander`, `deck:<slug>:companion`, `deck:<slug>:maybe`.
- A card is **free** if no tag on it starts with `deck:` (`is:free`).
- Location tags: `box:<name>`, `binder`, `binder:<name>`. Disposition tags: `trade`, `sell`, `want`.

## Scanner dataset
- `GET /api/lab/stats` gives accuracy and the most common confusions. `GET /api/lab/scans?status=wrong` lists misreads, each with what OCR read.
- `POST /api/lab/scans/{id}/label` with `{"verdict": "correct"|"wrong"|"unreadable", "printing_id", "finish"}` records ground truth.
- `POST /api/lab/rerun` replays the scanner on labelled scans; poll `GET /api/lab/rerun` for fixed/broken counts.
