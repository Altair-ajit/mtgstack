"""SQLite storage. One file holds everything: catalog, collection, decks, prices, event log.

Design:
  printings   - catalog of every known printing (Scryfall card objects, flattened)
  lots        - physical cards you own: (printing, finish, condition, language) x qty
  lot_tags    - free-form tags on lots. Namespaced by convention: "deck:atraxa", "box:binder-1", "trade"
  decks       - deck metadata; deck_entries = the decklist (what the deck *wants*)
  prices      - price history per (printing, finish, source, kind, date)
  events      - append-only log of every mutation (agents can audit / undo by reading it)

A "stack" is every lot sharing an oracle_id (all printings of the same card).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get("MTGSTACK_DB", Path.home() / ".mtgstack" / "collection.db"))

TABLES = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS printings (
    id               TEXT PRIMARY KEY,          -- scryfall id
    oracle_id        TEXT NOT NULL,
    name             TEXT NOT NULL,
    set_code         TEXT NOT NULL,
    set_name         TEXT,
    collector_number TEXT NOT NULL,
    rarity           TEXT,
    lang             TEXT DEFAULT 'en',
    released_at      TEXT,
    finishes         TEXT DEFAULT '[]',          -- json: ["nonfoil","foil","etched"]
    border_color     TEXT,
    frame            TEXT,
    frame_effects    TEXT DEFAULT '[]',
    full_art         INTEGER DEFAULT 0,
    promo            INTEGER DEFAULT 0,
    promo_types      TEXT DEFAULT '[]',
    type_line        TEXT,
    mana_cost        TEXT,
    cmc              REAL,
    colors           TEXT DEFAULT '',            -- "WU" style
    color_identity   TEXT DEFAULT '',
    oracle_text      TEXT,
    tcgplayer_id     INTEGER,
    tcgplayer_etched_id INTEGER,
    cardmarket_id    INTEGER,
    arena_id         INTEGER,
    mtgo_id          INTEGER,
    image_small      TEXT,
    image_normal     TEXT,
    image_art_crop   TEXT,
    legalities       TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_print_oracle ON printings(oracle_id);
CREATE INDEX IF NOT EXISTS ix_print_name ON printings(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_print_setcn ON printings(set_code, collector_number);
CREATE INDEX IF NOT EXISTS ix_print_tcg ON printings(tcgplayer_id);
CREATE INDEX IF NOT EXISTS ix_print_tcg_etched ON printings(tcgplayer_etched_id);

CREATE TABLE IF NOT EXISTS lots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    printing_id    TEXT NOT NULL REFERENCES printings(id),
    finish         TEXT NOT NULL DEFAULT 'nonfoil' CHECK (finish IN ('nonfoil','foil','etched')),
    condition      TEXT NOT NULL DEFAULT 'NM' CHECK (condition IN ('NM','LP','MP','HP','DMG')),
    language       TEXT NOT NULL DEFAULT 'en',
    qty            INTEGER NOT NULL CHECK (qty > 0),
    acquired_price REAL,
    notes          TEXT DEFAULT '',
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_lots_printing ON lots(printing_id);

CREATE TABLE IF NOT EXISTS lot_tags (
    lot_id INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    tag    TEXT NOT NULL,
    PRIMARY KEY (lot_id, tag)
);
CREATE INDEX IF NOT EXISTS ix_tags_tag ON lot_tags(tag);

CREATE TABLE IF NOT EXISTS decks (
    slug       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    format     TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'built',     -- building: new cards it needs are auto-tagged into it
    priority   INTEGER NOT NULL DEFAULT 100,      -- lower = fills first when several building decks want a card
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deck_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_slug   TEXT NOT NULL REFERENCES decks(slug) ON DELETE CASCADE,
    board       TEXT NOT NULL DEFAULT 'main',    -- main | side | commander | companion | maybe
    card_name   TEXT NOT NULL,
    oracle_id   TEXT,
    printing_id TEXT,                            -- optional: a specific printing is wanted
    finish      TEXT,                            -- optional: a specific finish is wanted
    qty         INTEGER NOT NULL CHECK (qty > 0)
);
CREATE INDEX IF NOT EXISTS ix_entries_deck ON deck_entries(deck_slug);
CREATE INDEX IF NOT EXISTS ix_entries_oracle ON deck_entries(oracle_id);

CREATE TABLE IF NOT EXISTS prices (
    printing_id TEXT NOT NULL,
    finish      TEXT NOT NULL,
    source      TEXT NOT NULL,                   -- tcgplayer | cardkingdom | cardmarket
    kind        TEXT NOT NULL,                   -- market | retail | buylist
    price       REAL NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'USD',
    as_of       TEXT NOT NULL,                   -- YYYY-MM-DD
    PRIMARY KEY (printing_id, finish, source, kind, as_of)
);

-- Today's price per printing/finish/source/kind. `prices` keeps daily history (owned/wanted cards only).
CREATE TABLE IF NOT EXISTS current_prices (
    printing_id TEXT NOT NULL,
    finish      TEXT NOT NULL,
    source      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    price       REAL NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'USD',
    as_of       TEXT NOT NULL,
    PRIMARY KEY (printing_id, finish, source, kind)
);

CREATE TABLE IF NOT EXISTS sets (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    set_type    TEXT,
    released_at TEXT,
    icon_svg    TEXT,
    card_count  INTEGER
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT DEFAULT (datetime('now')),
    action  TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""

# Views are dropped and recreated on every start, so their definitions can change freely between versions.
VIEWS = """
CREATE VIEW latest_prices AS SELECT * FROM current_prices;

-- One row per lot, enriched with catalog + tags + TCGplayer market price.
CREATE VIEW lot_view AS
SELECT l.id AS lot_id, l.qty, l.finish, l.condition, l.language, l.acquired_price, l.notes, l.created_at,
       p.id AS printing_id, p.oracle_id, p.name, p.set_code, p.set_name, p.collector_number,
       p.rarity, p.type_line, p.mana_cost, p.cmc, p.colors, p.color_identity, p.oracle_text,
       p.border_color, p.frame, p.frame_effects, p.promo_types, p.full_art, p.tcgplayer_id,
       p.legalities, p.released_at, p.image_small, p.image_normal,
       (SELECT group_concat(tag, ' ') FROM (SELECT tag FROM lot_tags t WHERE t.lot_id = l.id ORDER BY tag)) AS tags,
       cp.price AS price
FROM lots l JOIN printings p ON p.id = l.printing_id
LEFT JOIN current_prices cp ON cp.printing_id = l.printing_id AND cp.finish = l.finish
     AND cp.source = 'tcgplayer' AND cp.kind = 'market';

-- One row per decklist entry with card data (from the pinned printing, else the newest English printing) and
-- how it's covered: in_deck = copies tagged into this board; free_qty = matching copies in no deck.
CREATE VIEW entry_base AS
SELECT e.id AS entry_id, e.deck_slug, d.name AS deck_name, d.status AS deck_status, e.board, e.card_name AS name,
       e.qty AS wanted, e.oracle_id, e.printing_id, e.finish,
       c.id AS card_printing_id, c.type_line, c.oracle_text, c.mana_cost, c.cmc, c.colors, c.color_identity,
       c.rarity, c.legalities, c.set_code, c.set_name, c.collector_number, c.released_at, c.image_small, c.image_normal,
       CASE e.board WHEN 'main' THEN 'deck:' || e.deck_slug ELSE 'deck:' || e.deck_slug || ':' || e.board END AS board_tag,
       (SELECT COALESCE(SUM(l.qty), 0) FROM lots l JOIN printings p ON p.id = l.printing_id
         WHERE (p.id = e.printing_id OR (e.printing_id IS NULL AND p.oracle_id = e.oracle_id))
           AND (e.finish IS NULL OR l.finish = e.finish)
           AND EXISTS (SELECT 1 FROM lot_tags t WHERE t.lot_id = l.id AND t.tag =
                CASE e.board WHEN 'main' THEN 'deck:' || e.deck_slug ELSE 'deck:' || e.deck_slug || ':' || e.board END)) AS in_deck,
       (SELECT COALESCE(SUM(l.qty), 0) FROM lots l JOIN printings p ON p.id = l.printing_id
         WHERE (p.id = e.printing_id OR (e.printing_id IS NULL AND p.oracle_id = e.oracle_id))
           AND (e.finish IS NULL OR l.finish = e.finish)
           AND NOT EXISTS (SELECT 1 FROM lot_tags t WHERE t.lot_id = l.id AND t.tag LIKE 'deck:%')) AS free_qty,
       (SELECT MIN(cp.price) FROM printings q JOIN current_prices cp ON cp.printing_id = q.id
         AND cp.source = 'tcgplayer' AND cp.kind = 'market'
         WHERE (q.id = e.printing_id OR (e.printing_id IS NULL AND q.oracle_id = e.oracle_id))
           AND (e.finish IS NULL OR cp.finish = e.finish) AND q.lang = 'en') AS price
FROM deck_entries e
JOIN decks d ON d.slug = e.deck_slug
LEFT JOIN printings c ON c.id = COALESCE(e.printing_id,
     (SELECT id FROM printings x WHERE x.oracle_id = e.oracle_id ORDER BY x.lang = 'en' DESC, x.released_at DESC LIMIT 1));

-- Split each entry into: have (allocated) / to_move (free copies exist) / to_buy.
CREATE VIEW entry_view AS
SELECT b.*,
       MIN(in_deck, wanted) AS have,
       MIN(wanted - MIN(in_deck, wanted), free_qty) AS to_move,
       wanted - MIN(in_deck, wanted) - MIN(wanted - MIN(in_deck, wanted), free_qty) AS to_buy,
       CASE WHEN in_deck >= wanted THEN 'owned'
            WHEN wanted - MIN(in_deck, wanted) - MIN(wanted - MIN(in_deck, wanted), free_qty) > 0 THEN 'buy'
            ELSE 'move' END AS status
FROM entry_base b;
"""

CATALOG_TABLES = ("printings", "current_prices", "sets")


def _split_ddl(sql: str) -> tuple[str, str]:
    """Split TABLES into (catalog DDL qualified with `cat.`, user DDL) for the two-file layout."""
    import re
    cat, user = [], []
    for stmt in ("\n".join(l for l in s.splitlines() if not l.strip().startswith("--")).strip() for s in sql.split(";")):
        if not stmt or stmt.startswith("PRAGMA"):
            continue
        m = re.match(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\w+)(?: ON (\w+))?", stmt)
        table = (m.group(2) or m.group(1)) if m else ""
        if table in CATALOG_TABLES:
            cat.append(re.sub(r"IF NOT EXISTS (\w+)", r"IF NOT EXISTS cat.\1", stmt, count=1))
        else:   # cross-file foreign keys aren't possible in SQLite
            user.append(stmt.replace("REFERENCES printings(id)", ""))
    return ";\n".join(cat) + ";", ";\n".join(user) + ";"


def connect(path: str | os.PathLike | None = None, catalog: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open the collection. Normally one file holds everything. With `catalog` (or MTGSTACK_CATALOG_DB) the card
    catalog lives in a second, attached file, so the small user file can be saved cheaply (the phone build)."""
    path = Path(path) if path else DEFAULT_PATH
    catalog = catalog or os.environ.get("MTGSTACK_CATALOG_DB")
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if os.environ.get("MTGSTACK_BROWSER"):
        conn.execute("PRAGMA journal_mode=MEMORY")   # browser filesystem: no shared-memory WAL
    else:
        conn.execute("PRAGMA journal_mode=WAL")      # readers don't block the writer (UI stays responsive)
        conn.execute("PRAGMA synchronous=NORMAL")
    if catalog:
        conn.execute("ATTACH DATABASE ? AS cat", (str(catalog),))
        if os.environ.get("MTGSTACK_BROWSER"):
            conn.execute("PRAGMA cat.journal_mode=MEMORY")
        cat_ddl, user_ddl = _split_ddl(TABLES)
        conn.executescript("PRAGMA foreign_keys = ON;" + cat_ddl + user_ddl)
        conn.execute("ALTER TABLE cat.printings ADD COLUMN image_small TEXT") if not any(
            r[1] == "image_small" for r in conn.execute("PRAGMA cat.table_info(printings)")) else None
        add_user_cols(conn)
        views = VIEWS.replace("CREATE VIEW", "CREATE TEMP VIEW")   # temp views may span attached files
    else:
        _migrate(conn)
        conn.executescript(TABLES)
        views = VIEWS
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall():
        conn.execute(f"DROP VIEW IF EXISTS {name}")
    for (name,) in conn.execute("SELECT name FROM sqlite_temp_master WHERE type='view'").fetchall():
        conn.execute(f"DROP VIEW IF EXISTS temp.{name}")
    conn.executescript(views)
    return conn


def add_user_cols(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA main.table_info(decks)")}
    for col, ddl in (("status", "TEXT NOT NULL DEFAULT 'built'"), ("priority", "INTEGER NOT NULL DEFAULT 100")):
        if have and col not in have:
            conn.execute(f"ALTER TABLE main.decks ADD COLUMN {col} {ddl}")


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by older versions up to date."""
    def add_cols(table, cols_ddl):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if have:
            for col, ddl in cols_ddl:
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    add_cols("decks", [("status", "TEXT NOT NULL DEFAULT 'built'"), ("priority", "INTEGER NOT NULL DEFAULT 100")])
    add_cols("printings", [("image_small", "TEXT")])
    # current_prices was introduced after prices; seed it from the newest history row
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='prices'").fetchone() and \
       not conn.execute("SELECT 1 FROM sqlite_master WHERE name='current_prices'").fetchone():
        conn.executescript(TABLES)
        conn.execute("""INSERT OR REPLACE INTO current_prices
            SELECT p.* FROM prices p JOIN (SELECT printing_id, finish, source, kind, MAX(as_of) d FROM prices
            GROUP BY printing_id, finish, source, kind) m ON m.printing_id=p.printing_id AND m.finish=p.finish
            AND m.source=p.source AND m.kind=p.kind AND m.d=p.as_of""")
    conn.commit()


def log_event(conn: sqlite3.Connection, action: str, **payload) -> None:
    conn.execute("INSERT INTO events(action, payload) VALUES (?, ?)", (action, json.dumps(payload, default=str)))


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
