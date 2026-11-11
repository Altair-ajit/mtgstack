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

SCHEMA = """
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
    image_normal     TEXT,
    image_art_crop   TEXT,
    legalities       TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_print_oracle ON printings(oracle_id);
CREATE INDEX IF NOT EXISTS ix_print_name ON printings(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_print_setcn ON printings(set_code, collector_number);
CREATE INDEX IF NOT EXISTS ix_print_tcg ON printings(tcgplayer_id);

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

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT DEFAULT (datetime('now')),
    action  TEXT NOT NULL,
    payload TEXT NOT NULL
);

-- Latest price per printing/finish/source/kind.
CREATE VIEW IF NOT EXISTS latest_prices AS
SELECT p.* FROM prices p
JOIN (SELECT printing_id, finish, source, kind, MAX(as_of) AS as_of
        FROM prices GROUP BY printing_id, finish, source, kind) m
  USING (printing_id, finish, source, kind, as_of);

-- One row per lot, enriched with catalog + tags + default price (TCGplayer market).
CREATE VIEW IF NOT EXISTS lot_view AS
SELECT l.id AS lot_id, l.qty, l.finish, l.condition, l.language, l.acquired_price, l.notes,
       p.id AS printing_id, p.oracle_id, p.name, p.set_code, p.set_name, p.collector_number,
       p.rarity, p.type_line, p.mana_cost, p.cmc, p.colors, p.color_identity, p.oracle_text,
       p.border_color, p.frame, p.frame_effects, p.promo_types, p.full_art, p.tcgplayer_id,
       p.legalities, p.released_at,
       (SELECT group_concat(tag, ' ') FROM (SELECT tag FROM lot_tags t WHERE t.lot_id = l.id ORDER BY tag)) AS tags,
       (SELECT price FROM latest_prices lp WHERE lp.printing_id = p.id AND lp.finish = l.finish
          AND lp.source = 'tcgplayer' AND lp.kind = 'market') AS price
FROM lots l JOIN printings p ON p.id = l.printing_id;

-- One row per decklist entry with card data (from the pinned printing, else the newest printing) and
-- how it's covered: in_deck = copies tagged into this board; free_qty = matching copies in no deck.
CREATE VIEW IF NOT EXISTS entry_base AS
SELECT e.id AS entry_id, e.deck_slug, d.name AS deck_name, d.status AS deck_status, e.board, e.card_name AS name,
       e.qty AS wanted, e.oracle_id, e.printing_id, e.finish,
       c.type_line, c.oracle_text, c.mana_cost, c.cmc, c.colors, c.color_identity, c.rarity, c.legalities,
       c.set_code, c.collector_number, c.released_at,
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
       (SELECT MIN(lp.price) FROM printings q JOIN latest_prices lp ON lp.printing_id = q.id
         AND lp.source = 'tcgplayer' AND lp.kind = 'market'
         WHERE (q.id = e.printing_id OR (e.printing_id IS NULL AND q.oracle_id = e.oracle_id))
           AND (e.finish IS NULL OR lp.finish = e.finish) AND q.lang = 'en') AS price
FROM deck_entries e
JOIN decks d ON d.slug = e.deck_slug
LEFT JOIN printings c ON c.id = COALESCE(e.printing_id,
     (SELECT id FROM printings x WHERE x.oracle_id = e.oracle_id ORDER BY x.lang = 'en' DESC, x.released_at DESC LIMIT 1));

-- Split each entry into: have (allocated) / to_move (free copies exist) / to_buy.
CREATE VIEW IF NOT EXISTS entry_view AS
SELECT b.*,
       MIN(in_deck, wanted) AS have,
       MIN(wanted - MIN(in_deck, wanted), free_qty) AS to_move,
       wanted - MIN(in_deck, wanted) - MIN(wanted - MIN(in_deck, wanted), free_qty) AS to_buy,
       CASE WHEN in_deck >= wanted THEN 'owned'
            WHEN wanted - MIN(in_deck, wanted) - MIN(wanted - MIN(in_deck, wanted), free_qty) > 0 THEN 'buy'
            ELSE 'move' END AS status
FROM entry_base b;
"""


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    path = Path(path) if path else DEFAULT_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _migrate(conn)
    conn.executescript(SCHEMA)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by older versions up to date."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decks)")}
    if cols:
        for col, ddl in (("status", "TEXT NOT NULL DEFAULT 'built'"), ("priority", "INTEGER NOT NULL DEFAULT 100")):
            if col not in cols:
                conn.execute(f"ALTER TABLE decks ADD COLUMN {col} {ddl}")
    conn.commit()


def log_event(conn: sqlite3.Connection, action: str, **payload) -> None:
    conn.execute("INSERT INTO events(action, payload) VALUES (?, ?)", (action, json.dumps(payload, default=str)))


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
