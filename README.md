# mtgstack

A Magic: The Gathering collection manager in the style of a TCGplayer collection, plus decks that work like
mini collections, a camera scanner, and imports from TCGplayer/ManaBox/Moxfield/Deckbox or a pasted list.

- **Collection.** Every card you own on one searchable page: a filter sidebar (deck, location, set, color, type,
  rarity, printing, condition, language, price) with counts, sorting, and list or grid view. Click a card for its
  prices, your copies (edit quantity and condition, move to a deck) and every printing.
- **Decks.** Each deck is a mini collection: *have / to move / to buy*, grouped by type, with Arena, MTGO,
  Moxfield and TCGplayer export. Mark a deck **in progress** and cards it needs go straight into it as you add them.
- **Import.** Paste a list, or upload the `.txt` or `.csv` the TCGplayer app exports. Every row is matched to an
  exact printing (TCGplayer product ID > set + number > set + name > name) and shown for review before anything is saved.
- **Scan.** Phone camera, any angle; set, number, language and foil are read from the card's printed markers.
- **Prices.** TCGplayer market (via Scryfall) and Card Kingdom retail and buylist, per printing and finish, with history.

Everything is one SQLite file (`~/.mtgstack/collection.db`). The UI is React + TypeScript (`frontend/`). The
built copy is committed in `mtgstack/web_dist`, so running the app only needs Python.

## Quick start

**WSL + VS Code (recommended on Windows):**

```bash
# in your WSL (Ubuntu) terminal, one time:
sudo apt update && sudo apt install -y python3-venv tesseract-ocr unzip
cd ~ && unzip /mnt/c/Users/<you>/Downloads/mtgstack.zip      # keep code in the Linux filesystem, not /mnt/c (much faster)
cd ~/mtgstack && code .                                      # opens VS Code connected to WSL
```

In VS Code, press **Ctrl+Shift+B**. The first run creates `.venv`, installs everything, and opens
http://localhost:8765 in your Windows browser. Or use the terminal:

| | |
|---|---|
| `./run.sh` | start the website |
| `./run.sh --dev` | restart automatically when you edit code |
| `./run.sh --phone` | reachable from your phone on the same Wi-Fi, over HTTPS so the live camera works |
| `./run.sh --test` | run the tests |

**F5** starts the site under the debugger (breakpoints work).

*Phone + WSL:* WSL2's default (NAT) networking only forwards `localhost` to Windows, so your phone can't reach a
server inside WSL directly. `./run.sh --phone` detects this, prints the phone URL (your Windows Wi-Fi address),
and prints the one-time `netsh portproxy` and firewall commands to paste into an admin PowerShell. With
`networkingMode=mirrored` in `%UserProfile%\.wslconfig` that step isn't needed.

*Native Windows* also works with `.\run.ps1` (same flags: `-Dev`, `-Phone`, `-Test`).

**Any OS, by hand:**
```
python -m venv .venv && .venv/bin/pip install -e .        # Windows: .venv\Scripts\pip
mtgstack serve [--reload] [--host 0.0.0.0 --https]
```

**First run:** click **Download now** on the banner, or run `mtgstack catalog sync`. That downloads Scryfall's
card database once (~500 MB, a few minutes), and after that search, imports and prices are local and instant.
Re-run it, or use *Refresh prices* in the ⋯ menu, to update prices. Without it the app still works, but looks
cards up online one at a time.

**Changing the UI:** install Node 20+ in WSL (`curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
&& sudo apt install -y nodejs`), run the task *frontend dev (hot reload)*, and open http://localhost:5173 (API
calls go to the Python server on :8765, so keep that running too). Run *build frontend* to update `mtgstack/web_dist`.

---

## 1. Tag-based storage

| Concept | What it is |
|---|---|
| **stack** | Every copy you own of one card, across all printings (Scryfall `oracle_id`). |
| **printing** | Set + collector number + language, with Scryfall's frame data (border, showcase, extended art...). |
| **lot** | N identical copies: same printing, finish (nonfoil/foil/etched), condition, language **and tag set**. |
| **tag** | A free-form label on a lot. Namespaces are a naming convention: `deck:atraxa`, `deck:atraxa:side`, `box:bulk-3`, `binder`, `trade`. |

When you move part of a lot, it splits. Lots that end up identical merge back automatically.

```bash
mtgstack add "Sol Ring" --set cmm --cn 410 --foil --qty 2 --tags binder
mtgstack move 12 --qty 1 --add deck:atraxa --remove binder      # 1 of lot 12 goes to the deck, the rest stays
mtgstack move-query 'tag:binder set:mh3' --add box:mh3 --remove binder
mtgstack split-dupes 'tag:binder' --keep 1 --add trade --remove binder          # dry run
mtgstack split-dupes 'tag:binder' --keep 1 --add trade --remove binder --apply
mtgstack stacks 'is:dupe -tag:trade'                             # card -> printing -> lot tree
mtgstack retag binder box:binder-1
```

`split-dupes` never moves cards that are allocated to a deck. Deck copies count toward `--keep`.
Use `--by printing` to keep one of each *printing* instead of one of each card.

### Query language (`mtgstack help query`)

```
sol ring  "sol ring"  t:creature  o:proliferate  c:wu  id<=wubg  cmc<=3  r:mythic
set:mh3  cn:12  lang:ja  is:foil  is:etched  cond<=lp  price>5  value>=20  qty>1
tag:binder  -tag:trade  tag:deck:*  tag:none  deck:atraxa  is:free  is:dupe  f:commander
is:borderless  is:showcase  is:extendedart  is:fullart  is:promo
(tag:a or tag:b) -is:foil  sort:price  sort:-name
```

## 2. Scanner: which printed marker tells you what

**Finding the card (any rotation).** The card can sit at any angle, anywhere in the frame, with the camera tilted.
`scanner/detect.py` builds candidate four-sided outlines from several edge maps, then:
- keeps candidates with card proportions (63×88 mm) whose four sides all lie on real edges;
- prefers outlines just inside which the colour is uniform, like a card border;
- steps outward from the frame line to the card's outer edge (the frame is card-shaped too);
- grows from the frame by border colour when the outline merges into a busy background;
- snaps each side to the outermost straight edge with a line fit, so rounded corners don't skew it.

On synthetic photos it's exact (median corner error ~1.6px) at every rotation on plain, white and dark surfaces.
On a deliberately hostile random-block background, about 60% are exact and the rest stay on the card.
Which end is up is decided by reading the collector line, or the title bar on older cards.

In the web UI the camera view outlines the card live (`POST /api/detect`, ~120 ms per 640px frame) and tells you
"move closer" or "hold still". **Auto-capture** scans once the card has been steady for 3 frames, then waits for the
next card, so you can feed cards one after another. It can also capture each card as a tilt burst for the foil check.

Once found, the card is flattened to a 672×936 image and these markers are read in order:

| Marker | Where | Decides |
|---|---|---|
| Collector line | bottom-left, every card since Magic 2015 | **Set code + collector number + language**, which is the exact printing. Handles both layouts: `123/264 R` / `KLD • EN` (before March of the Machine) and `R 0123` / `MOM • EN` (March of the Machine onward). |
| Separator glyph | between set code and language | **• = non-foil, ★ = foil/etched.** It's classified by **shape** (filled area ÷ convex hull area: a dot is about 0.99, a star about 0.64), not by OCR. Tesseract reads the star as "X" and the dot as "¢e". |
| Catalog finishes | Scryfall `finishes` | If a printing only exists in one finish, that settles it (e.g. foil-only Starter Collection cards). |
| Title bar | top | OCR plus fuzzy match, giving every printing of the card. Also cross-checks the collector line. |
| Border colour | outer band | black / white / silver / gold / borderless, used to narrow down printings. |
| Art perceptual hash | art box | Ranks the remaining printings against Scryfall images (cached in `~/.mtgstack/images`). |
| **Tilt shimmer** | art box, 3+ frames | For pre-2015 cards with no ★/•. Each frame of a tilt burst is aligned to the same card image, so each pixel can be compared over time. Glare on foil is **coloured and shifts hue**; glare on non-foil is white. |
| Border sheen | border band, 1 frame | Weaker single-photo fallback: is the glare on the black border rainbow-coloured or white? |
| Orientation | — | Upright vs upside-down: whichever way the collector line reads, else whichever way the title OCRs more confidently. |

Every result includes a confidence score, ranked candidate printings, and an `evidence` list explaining each decision.

**How the readings are combined.** The title, type line and collector line are read independently and must agree:
- A set+number from the collector line is accepted only if that printing's name matches the OCR'd title (≥ 60
  similarity). A misread number (e.g. landing on another card in the same set) is therefore rejected, not trusted.
- Otherwise the card is identified by title (≥ 65 Levenshtein similarity); the type line breaks near-ties. Then set,
  number, language, border and art narrow it down to the printing.
- If neither is good enough, the scanner returns **no guess** (confidence 0) plus candidates, rather than a wrong card.
- Scryfall's online fuzzy match is only accepted if it really resembles what was read, and lookups fall back to the
  local catalog when Scryfall can't be reached.

### Scanner Lab (`/lab`)
Every scan (camera, upload or CLI-free API) is saved to the **scan dataset**: `scan-data/` in the repo by default
(`MTGSTACK_SCANS` to move it; gitignored). Each scan folder holds the photos, the straightened card, the title / type-line /
collector crops, and `scan.json` with everything the scanner read and decided.

- **▶ Live session** (`/lab/live`): camera on, show cards one at a time. Each is captured once it's steady and fully
  in frame. Answer with **Y/Space** right, **N** wrong (card picker), **1–6** pick a suggestion, **F** fix the finish, **U** bad
  photo, **R** rescan, **S** skip; then swap in the next card. Optionally adds confirmed cards to the collection as you go.
  Big buttons on a phone (needs `--phone` for HTTPS).
- On the **Scan** page, mark a result **✓ Right** or **✗ Wrong — fix it** (pick the real card). Adding a card you
  corrected also records the correction.
- In **Scanner Lab**, go through *To review* with the keyboard: **Y** right, **N** wrong (opens a card picker),
  **U** bad photo. Each scan shows exactly what OCR read from each region, so you can see *where* it went wrong.
- The stats show accuracy for card / printing / finish, the most common confusions (e.g. *Training Center → Aunt May*),
  and whether confidence separates right from wrong answers.
- **Re-run scanner on labelled scans** replays the current scanner over every labelled photo and reports what got
  fixed and what broke, so scanner changes are measured against your real cards. **Export** zips the dataset.
`needs_review` is set when either the identification or the finish is uncertain. In the UI, **Tilt burst** captures 6 frames while you tilt the card.

```bash
mtgstack scan photo.jpg --add --tags box:bulk-1
mtgstack scan f1.jpg f2.jpg f3.jpg f4.jpg f5.jpg     # tilt burst -> foil check
```

## In-progress decks

Mark a deck **in progress** (`--building`, or the toggle in the Decks tab). From then on:

- **New cards fill it automatically.** When a card enters the collection (scan, `add`, TCGplayer import) and an
  in-progress deck still needs it, the needed copies are tagged straight into the deck, and extras stay free.
  Pinned printings and finishes are respected (`1 Lightning Bolt (STA) 42 *E*` only takes etched copies).
  The maybeboard never auto-fills. Order: deck priority (lower first), then commander > companion > main > side.
- **Cards you already own are never grabbed.** They show as **to move**, with the binder or box they're in, and
  you pull them in with **Move in** (one card) or `deck pull` (all of them).
- **Overrides stick.** `mtgstack send LOT` (or ↩ in the UI) puts a copy back in the collection, and
  `send LOT --deck other --board side` moves it to another deck. Only *new* cards are auto-placed, so a card you
  moved stays where you put it.
- **Progress is have/total** (e.g. `72/100`), split into **have**, **to move** (you own a free copy) and
  **to buy** (with the cheapest printing and total cost). The maybeboard doesn't count toward the total.

Deck entries, owned or not, use the same query language as the collection plus `status:owned|move|buy|need`,
`board:` and `deck:`. Sort by `type`, `cmc` (aliases `mana`, `mv`), `price` (`cost`), `color`, `rarity`:

```bash
mtgstack deck import atraxa.txt --name "Atraxa" --format commander --building
mtgstack deck show atraxa 'status:buy t:creature sort:-price'
mtgstack wants 'is:building status:need sort:cmc'        # everything any in-progress deck still needs
mtgstack deck status atraxa built                        # stop auto-filling
```

## 3. Decks, TCGplayer, Arena, prices

```bash
mtgstack deck import atraxa.txt --name "Atraxa" --format commander   # Arena / Moxfield / MTGO / plain text, stdin with -
mtgstack deck show atraxa          # per card: allocated / can pull from free copies / missing (+ cheapest printing) / used in other decks
mtgstack deck pull atraxa --remove binder     # tags free copies into the deck (cheapest first; --prefer priciest to use your best printings)
mtgstack deck export atraxa --fmt arena       # also mtgo | moxfield | tcgplayer (mass entry); --owned exports your allocated copies
mtgstack deck missing atraxa                  # buy list
mtgstack deck release atraxa                  # untag everything; the cards become free again
mtgstack deck from-tags atraxa                # build the decklist from whatever you tagged (e.g. after scanning a deck)

mtgstack import tcgplayer export.csv --tags from-tcg --dry-run
mtgstack export tcgplayer 'tag:trade' > trade.csv       # also: json, massentry

mtgstack prices refresh            # Scryfall (TCGplayer + Cardmarket market) + Card Kingdom retail & buylist
mtgstack prices value              # collection value: TCGplayer market / CK retail / CK cash buylist
mtgstack prices movers --days 7
```

- **TCGplayer import:** set the app's export to CSV and enable *Product ID, Set Code, Card Number, Printing, Condition, Language*. Rows are matched by Product ID first, then set + number, then set + name, then name alone. The result flags any row whose printing had to be guessed. ManaBox, Moxfield and Deckbox CSV headers are recognised too.
- **Arena export:** set codes are only written for printings that exist on Arena (Scryfall `arena_id`). Otherwise the line is name-only, which Arena resolves itself, so a paper-only printing can't break the import.
- **Prices** are stored per printing **and finish**, per source, per day, so history and movers build up automatically. To refresh daily, add a cron job or scheduled task that runs `mtgstack prices refresh`.

## Browser demo

`demo/` runs the real Python modules in the browser with Pyodide, routing the UI's `/api` calls through `demo/demo_api.py`. It uses a sample catalog with sample prices. `python3 demo/build.py` builds it into `demo/dist`. There's no scanner in the demo (no camera or Tesseract in the browser).

## For agents

See `AGENTS.md`. In short: `mtgstack --json <cmd>` or the REST API (`/docs`). Every change is recorded in the `events` table.

## Status / known limits

- **Not run against live data yet.** It was built in a sandbox that couldn't reach Scryfall or Card Kingdom. All 24 tests run against Scryfall-format fixture data and synthetic card photos. Check on first real use:
  - Card Kingdom's pricelist URL and fields (`scryfall_id`, `is_foil`, `price_retail`, `price_buy`). The URL can be overridden with `MTGSTACK_CK_URL`; the parser is defensive.
  - Scanner regions and foil thresholds on **real photos**. Region coordinates are in `scanner/detect.py:REGIONS`. Use `scanner.foil.calibrate()` with a few of your own foil and non-foil cards to set the shimmer threshold.
- Pre-2003 frames have no collector line, so they fall back to name + border + art hash. They are flagged for review more often.
- TCGplayer has no public collection API, so interop is CSV in and out, not live sync. TCGplayer prices come from Scryfall's daily TCGplayer market feed.
- Telling foil from etched on a printing that has both, from a single photo, is flagged for review (both show ★).
