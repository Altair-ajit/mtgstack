"""Decks: import any common decklist text, reconcile against the collection, pull cards in, export.

A deck has two sides:
  * the decklist   (deck_entries)  - what the deck wants, by card name, optionally a specific printing
  * the allocation (lot tags)      - which physical copies are actually sleeved up in it
        main board -> tag  deck:<slug>
        other      -> tag  deck:<slug>:<board>     (side, commander, companion, maybe)

`reconcile` compares the two; `pull` tags free copies (not in any deck) to fill the gaps,
splitting lots as needed; `release` takes the deck's tags off so the cards are free again.

In-progress decks (status='building'): whenever a card ENTERS the collection (scan, add, import) and a
building deck still needs it, the new copy is tagged straight into that deck. Cards already in the
collection are never grabbed automatically: they show up as "to move", so a manual override (sending
a copy back to the collection or to another deck) always sticks.
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import collection as col
from .catalog import Catalog
from .db import log_event, rows

BOARDS = ("commander", "companion", "main", "side", "maybe")
_SECTION = {"deck": "main", "main": "main", "mainboard": "main", "main deck": "main",
            "sideboard": "side", "side": "side", "commander": "commander", "commanders": "commander",
            "companion": "companion", "maybeboard": "maybe", "maybe": "maybe", "considering": "maybe"}
_LINE = re.compile(r"""^(?:(?P<sb>SB:)\s*)?(?P<qty>\d+)\s*x?\s+(?P<name>.+?)
                       (?:\s+\((?P<set>[A-Za-z0-9]{2,6})\)(?:\s+(?P<cn>[^\s*]+))?)?
                       (?:\s+\*(?P<fin>[FE])\*)?\s*$""", re.X | re.I)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "deck"


def board_tag(slug: str, board: str) -> str:
    return f"deck:{slug}" if board == "main" else f"deck:{slug}:{board}"


def parse_decklist(text: str) -> list[dict]:
    """Arena / MTGO / Moxfield / plain text. Returns [{qty, name, set, cn, finish, board}]."""
    out, board, blank_after_cards = [], "main", False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if out and board == "main":
                blank_after_cards = True   # MTGO style: blank line separates sideboard
            continue
        low = line.lower().strip("/#: ").strip()
        if low in _SECTION:
            board, blank_after_cards = _SECTION[low], False
            continue
        if low.startswith(("about", "name ")) or line.startswith(("//", "#")):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        b = "side" if m["sb"] or (blank_after_cards and board == "main") else board
        fin = {"F": "foil", "E": "etched"}.get((m["fin"] or "").upper())
        out.append({"qty": int(m["qty"]), "name": m["name"].strip(), "set": (m["set"] or "").lower() or None,
                    "cn": m["cn"], "finish": fin, "board": b})
    return out


def create_deck(conn, name: str, fmt: str = "", notes: str = "", slug: str | None = None,
                status: str | None = None, priority: int | None = None) -> str:
    slug = slug or slugify(name)
    conn.execute("INSERT INTO decks(slug, name, format, notes) VALUES (?,?,?,?) "
                 "ON CONFLICT(slug) DO UPDATE SET name=excluded.name, format=excluded.format", (slug, name, fmt, notes))
    if status or priority is not None:
        update_deck(conn, slug, status=status, priority=priority, commit=False)
    conn.commit()
    return slug


def update_deck(conn, slug: str, status: str | None = None, priority: int | None = None, name: str | None = None,
                fmt: str | None = None, commit: bool = True) -> dict:
    if not conn.execute("SELECT 1 FROM decks WHERE slug=?", (slug,)).fetchone():
        raise KeyError(f"no deck {slug}")
    if status is not None:
        if status not in ("building", "built"):
            raise ValueError("status must be 'building' (in progress, auto-fill) or 'built'")
        conn.execute("UPDATE decks SET status=? WHERE slug=?", (status, slug))
    for col_, val in (("priority", priority), ("name", name), ("format", fmt)):
        if val is not None:
            conn.execute(f"UPDATE decks SET {col_}=? WHERE slug=?", (val, slug))
    log_event(conn, "deck_update", slug=slug, status=status, priority=priority)
    if commit:
        conn.commit()
    return dict(conn.execute("SELECT * FROM decks WHERE slug=?", (slug,)).fetchone())


def import_decklist(conn: sqlite3.Connection, catalog: Catalog, name: str, text: str, fmt: str = "",
                    slug: str | None = None, replace: bool = True, status: str | None = None) -> dict:
    slug = create_deck(conn, name, fmt, slug=slug, status=status)
    if replace:
        conn.execute("DELETE FROM deck_entries WHERE deck_slug=?", (slug,))
    unresolved = []
    for e in parse_decklist(text):
        printing = None
        if e["set"] and e["cn"]:
            printing = catalog.by_set_cn(e["set"], e["cn"])
        prints = [printing] if printing else catalog.prints_by_name(e["name"])
        if not prints:
            unresolved.append(e["name"])
            conn.execute("INSERT INTO deck_entries(deck_slug, board, card_name, qty) VALUES (?,?,?,?)",
                         (slug, e["board"], e["name"], e["qty"]))
            continue
        p = prints[0]
        conn.execute("INSERT INTO deck_entries(deck_slug, board, card_name, oracle_id, printing_id, finish, qty) "
                     "VALUES (?,?,?,?,?,?,?)", (slug, e["board"], p["name"], p["oracle_id"],
                                                printing["id"] if printing else None, e["finish"], e["qty"]))
    log_event(conn, "deck_import", slug=slug, unresolved=unresolved)
    conn.commit()
    return {"slug": slug, "entries": len(parse_decklist(text)), "unresolved": unresolved}


def deck_from_tags(conn, slug: str, name: str | None = None) -> str:
    """Build the decklist from whatever is currently tagged into the deck (e.g. after scanning a deck)."""
    create_deck(conn, name or slug, slug=slug)
    conn.execute("DELETE FROM deck_entries WHERE deck_slug=?", (slug,))
    for board in BOARDS:
        for r in conn.execute(
                "SELECT p.name, p.oracle_id, SUM(l.qty) q FROM lots l JOIN printings p ON p.id=l.printing_id "
                "JOIN lot_tags t ON t.lot_id=l.id WHERE t.tag=? GROUP BY p.oracle_id", (board_tag(slug, board),)):
            conn.execute("INSERT INTO deck_entries(deck_slug, board, card_name, oracle_id, qty) VALUES (?,?,?,?,?)",
                         (slug, board, r["name"], r["oracle_id"], r["q"]))
    conn.commit()
    return slug


def _lots_for(conn, entry: dict, where: str, params: list) -> list[dict]:
    key = ("v.printing_id = ?", entry["printing_id"]) if entry.get("printing_id") else ("v.oracle_id = ?", entry["oracle_id"])
    sql = f"SELECT * FROM lot_view v WHERE {key[0]} AND {where}"
    p = [key[1], *params]
    if entry.get("finish"):
        sql += " AND v.finish = ?"
        p.append(entry["finish"])
    return rows(conn.execute(sql, p))


def _has_tag(tag_sql: str) -> str:
    return f"EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id=v.lot_id AND {tag_sql})"


def entries(conn, slug: str | None = None, q: str = "") -> list[dict]:
    from .query import query_entries
    qq = (f"deck:{slug} " if slug else "") + (q or "")
    return query_entries(conn, qq)


def _totals(rows_: list[dict]) -> dict:
    t = {"wanted": 0, "in_deck": 0, "free": 0, "missing": 0, "missing_cost": 0.0}
    for e in rows_:
        if e["board"] == "maybe":           # the maybeboard isn't part of the deck's count
            continue
        t["wanted"] += e["wanted"]
        t["in_deck"] += e["have"]
        t["free"] += e["to_move"]
        t["missing"] += e["to_buy"]
        t["missing_cost"] += (e["price"] or 0) * e["to_buy"]
    t["missing_cost"] = round(t["missing_cost"], 2)
    t["progress"] = f"{t['in_deck']}/{t['wanted']}"
    return t


def reconcile(conn, slug: str, q: str = "") -> dict:
    """Decklist vs collection. Each entry: have (tagged in) / to_move (free copies exist) / to_buy.
    `q` filters/sorts entries with the query language (e.g. 't:creature sort:cmc', 'status:buy sort:price')."""
    deck = conn.execute("SELECT * FROM decks WHERE slug=?", (slug,)).fetchone()
    if not deck:
        raise KeyError(f"no deck {slug}")
    all_rows = entries(conn, slug)
    shown = entries(conn, slug, q) if q else all_rows
    report = []
    for e in shown:
        row = {"board": e["board"], "name": e["name"], "wanted": e["wanted"], "in_deck": e["in_deck"],
               "have": e["have"], "to_move": e["to_move"], "to_buy": e["to_buy"], "status": e["status"],
               "free_available": e["free_qty"], "missing": e["to_buy"],
               "type_line": e["type_line"], "mana_cost": e["mana_cost"], "cmc": e["cmc"], "colors": e["colors"],
               "rarity": e["rarity"], "price": e["price"], "printing_id": e["printing_id"], "finish": e["finish"],
               "oracle_id": e["oracle_id"], "entry_id": e["entry_id"], "in_other_decks": [], "where": [],
               "card_printing_id": e["card_printing_id"], "image_small": e["image_small"],
               "image_normal": e["image_normal"], "set_code": e["set_code"], "set_name": e["set_name"],
               "collector_number": e["collector_number"],
               "in_deck_lots": [], "board_tag": e["board_tag"],
               "cheapest": None}
        if e["status"] != "owned":
            elsewhere = _lots_for(conn, e, _has_tag("x.tag LIKE 'deck:%' AND x.tag NOT LIKE ?"), [f"deck:{slug}%"])
            row["in_other_decks"] = sorted({t for l in elsewhere for t in (l["tags"] or "").split()
                                            if t.startswith("deck:")})
        if e["in_deck"]:
            row["in_deck_lots"] = [{"lot_id": l["lot_id"], "qty": l["qty"], "set": l["set_code"], "finish": l["finish"],
                                    "tags": l["tags"] or ""} for l in _lots_for(conn, e, _has_tag("x.tag = ?"), [e["board_tag"]])]
        if e["to_move"]:
            free = _lots_for(conn, e, "NOT " + _has_tag("x.tag LIKE 'deck:%'"), [])
            row["where"] = [{"lot_id": l["lot_id"], "qty": l["qty"], "set": l["set_code"], "finish": l["finish"],
                             "tags": l["tags"] or ""} for l in free]
        if e["to_buy"]:
            row["cheapest"] = _cheapest(conn, e)
        report.append(row)
    return {"deck": dict(deck), "totals": _totals(all_rows), "shown": _totals(shown) if q else None,
            "entries": report}


def _cheapest(conn, e: dict) -> dict | None:
    cond = "p.id = ?" if e["printing_id"] else "p.oracle_id = ?"
    r = conn.execute(f"""SELECT p.id, p.set_code, p.collector_number, p.tcgplayer_id, lp.finish, lp.price
        FROM printings p JOIN latest_prices lp ON lp.printing_id=p.id AND lp.source='tcgplayer' AND lp.kind='market'
        WHERE {cond} AND (? IS NULL OR lp.finish = ?) AND p.lang='en' ORDER BY lp.price LIMIT 1""",
                     (e["printing_id"] or e["oracle_id"], e["finish"], e["finish"])).fetchone()
    return dict(r) if r else None


def pull(conn, slug: str, prefer: str = "cheapest", dry_run: bool = False, remove_tags=None, q: str = "") -> dict:
    """Tag free copies into the deck to cover what's not already allocated ("to move" -> "have").
    prefer: 'cheapest' keeps your expensive printings free; 'priciest' blings the deck.
    remove_tags: location tags to drop from pulled copies (e.g. ['binder'] when they leave the binder).
    q: only pull entries matching a query (e.g. 't:land')."""
    plan = []
    for e in entries(conn, slug, q):
        need = min(e["wanted"] - e["in_deck"], e["free_qty"])
        if need <= 0:
            continue
        free = _lots_for(conn, e, "NOT " + _has_tag("x.tag LIKE 'deck:%'"), [])
        free.sort(key=lambda l: (l["price"] or 0) * (-1 if prefer == "priciest" else 1))
        for lot in free:
            if need <= 0:
                break
            take = min(need, lot["qty"])
            plan.append({"lot_id": lot["lot_id"], "name": lot["name"], "set": lot["set_code"],
                         "finish": lot["finish"], "qty": take, "tag": e["board_tag"]})
            need -= take
    if not dry_run:
        for step in plan:
            step["to_lot"] = col.move(conn, step["lot_id"], step["qty"], add_tags=[step["tag"]],
                                     remove_tags=remove_tags, commit=False)
        log_event(conn, "deck_pull", slug=slug, steps=len(plan))
        conn.commit()
    return {"pulled": plan, "after": _totals(entries(conn, slug)) if not dry_run else None}


def auto_allocate(conn, lot_id: int, report: list | None = None) -> int | None:
    """A copy just entered the collection: tag it into building decks that still need it.

    Highest-priority deck first (then oldest), commander > companion > main > side; never the maybeboard.
    Splits the lot if only some copies are needed. Returns the id of the lot holding any leftover copies.
    """
    lot = conn.execute("SELECT l.*, p.oracle_id, p.name FROM lots l JOIN printings p ON p.id=l.printing_id "
                       "WHERE l.id=?", (lot_id,)).fetchone()
    if not lot or any(t.startswith("deck:") for t in col.tags_of(conn, lot_id)):
        return lot_id                     # explicitly placed in a deck by the caller
    wants = rows(conn.execute("""
        SELECT v.* FROM entry_view v JOIN decks d ON d.slug = v.deck_slug
        WHERE v.deck_status = 'building' AND v.board <> 'maybe' AND v.in_deck < v.wanted
          AND (v.printing_id = ? OR (v.printing_id IS NULL AND v.oracle_id = ?))
          AND (v.finish IS NULL OR v.finish = ?)
        ORDER BY d.priority, d.created_at, d.slug,
                 CASE v.board WHEN 'commander' THEN 0 WHEN 'companion' THEN 1 WHEN 'main' THEN 2 ELSE 3 END""",
                           (lot["printing_id"], lot["oracle_id"], lot["finish"])))
    remaining = lot["qty"]
    for e in wants:
        take = min(remaining, e["wanted"] - e["in_deck"])
        if take <= 0:
            continue
        target = col.move(conn, lot_id, take, add_tags=[e["board_tag"]], commit=False)
        remaining -= take
        info = {"deck": e["deck_slug"], "deck_name": e["deck_name"], "board": e["board"], "tag": e["board_tag"],
                "qty": take, "lot_id": target, "name": lot["name"],
                "deck_progress": None}
        log_event(conn, "auto_allocate", **{k: v for k, v in info.items() if k != "deck_progress"})
        if report is not None:
            report.append(info)
        if remaining == 0:
            return None
    return lot_id


def send(conn, lot_id: int, qty: int | None = None, deck: str | None = None, board: str = "main",
         commit: bool = True) -> int:
    """Manual override: move copies out of whatever deck they're in, back to the collection (deck=None)
    or into another deck/board. The copy stays where you put it: auto-allocation only acts on NEW cards."""
    if deck and not conn.execute("SELECT 1 FROM decks WHERE slug=?", (deck,)).fetchone():
        raise KeyError(f"no deck {deck}")
    if board not in BOARDS:
        raise ValueError(f"board must be one of {BOARDS}")
    target = col.move(conn, lot_id, qty, add_tags=[board_tag(deck, board)] if deck else None,
                      remove_tags=["deck:*"], commit=False)
    log_event(conn, "send", lot_id=lot_id, qty=qty, deck=deck, board=board, to_lot=target)
    if commit:
        conn.commit()
    return target


def release(conn, slug: str) -> int:
    """Un-allocate every card in the deck (removes deck:<slug> and deck:<slug>:* tags)."""
    lots = rows(conn.execute("SELECT DISTINCT lot_id FROM lot_tags WHERE tag = ? OR tag LIKE ?",
                             (f"deck:{slug}", f"deck:{slug}:%")))
    for l in lots:
        col.move(conn, l["lot_id"], remove_tags=[f"deck:{slug}", f"deck:{slug}:*"], commit=False)
    log_event(conn, "deck_release", slug=slug, lots=len(lots))
    conn.commit()
    return len(lots)


def export(conn, slug: str, fmt: str = "arena", source: str = "list") -> str:
    """fmt: arena | mtgo | moxfield | tcgplayer (mass entry). source: 'list' (decklist) or 'owned' (allocated copies)."""
    if source == "owned":
        items = rows(conn.execute(
            """SELECT t.tag, p.name, p.set_code, p.collector_number, p.arena_id, l.finish, SUM(l.qty) qty
               FROM lots l JOIN printings p ON p.id=l.printing_id JOIN lot_tags t ON t.lot_id=l.id
               WHERE t.tag = ? OR t.tag LIKE ? GROUP BY t.tag, p.id, l.finish ORDER BY p.name""",
            (f"deck:{slug}", f"deck:{slug}:%")))
        for it in items:
            it["board"] = it["tag"].split(":")[2] if it["tag"].count(":") >= 2 else "main"
    else:
        items = rows(conn.execute(
            """SELECT e.board, e.card_name AS name, e.qty, e.finish, e.oracle_id,
                      p.set_code, p.collector_number, p.arena_id
               FROM deck_entries e LEFT JOIN printings p ON p.id = e.printing_id
               WHERE e.deck_slug=? ORDER BY e.card_name""", (slug,)))
    if fmt == "arena":
        # Arena only accepts set codes for printings that exist on Arena. Use the chosen printing if it
        # does, else the newest Arena printing of the card, else a name-only line (Arena resolves those).
        for it in items:
            if it.get("arena_id"):
                continue
            q = conn.execute("""SELECT set_code, collector_number, arena_id FROM printings
                                WHERE oracle_id = (SELECT oracle_id FROM printings WHERE name=? LIMIT 1)
                                  AND arena_id IS NOT NULL ORDER BY released_at DESC LIMIT 1""",
                             (it["name"],)).fetchone()
            it.update(dict(q) if q else {"set_code": None, "collector_number": None})

    def line(it, with_set=True):
        base = f"{it['qty']} {it['name']}"
        if fmt == "tcgplayer":
            return base + (f" [{it['set_code'].upper()}]" if it.get("set_code") else "")
        if with_set and it.get("set_code") and it.get("collector_number"):
            base += f" ({it['set_code'].upper()}) {it['collector_number']}"
        if fmt == "moxfield" and it.get("finish") in ("foil", "etched"):
            base += " *F*" if it["finish"] == "foil" else " *E*"
        return base

    by_board = {b: [i for i in items if i["board"] == b] for b in BOARDS}
    if fmt == "tcgplayer":
        return "\n".join(line(i) for b in BOARDS if b != "maybe" for i in by_board[b])
    if fmt == "mtgo":
        main = [line(i, False) for b in ("commander", "companion", "main") for i in by_board[b]]
        side = [line(i, False) for i in by_board["side"]]
        return "\n".join(main) + ("\n\n" + "\n".join(side) if side else "")
    # Arena: set codes are only safe when the printing exists on Arena; otherwise name-only lines.
    heads = {"commander": "Commander", "companion": "Companion", "main": "Deck", "side": "Sideboard"}
    if fmt == "moxfield":
        heads["maybe"] = "Maybeboard"
    chunks = []
    for b, head in heads.items():
        if by_board[b]:
            chunks.append(head + "\n" + "\n".join(line(i) for i in by_board[b]))
    return "\n\n".join(chunks)


def list_decks(conn) -> list[dict]:
    """Every deck with progress: have/wanted, to move (free copies you own), to buy (+ cost), value, cover art."""
    out = []
    for d in rows(conn.execute("SELECT * FROM decks ORDER BY status='building' DESC, priority, name")):
        es = entries(conn, d["slug"])
        t = _totals(es)
        cover = next((e for e in es if e["board"] == "commander"), None) or \
            max((e for e in es if e["board"] != "maybe"), key=lambda e: e["price"] or 0, default=None)
        owned_value = conn.execute(
            "SELECT ROUND(COALESCE(SUM(v.qty * COALESCE(v.price,0)),0),2) FROM lot_view v WHERE EXISTS (SELECT 1 FROM "
            "lot_tags x WHERE x.lot_id=v.lot_id AND (x.tag=? OR x.tag LIKE ?))",
            (f"deck:{d['slug']}", f"deck:{d['slug']}:%")).fetchone()[0]
        out.append({**d, **t, "cover_image": cover["image_normal"] if cover else None,
                    "cover_name": cover["name"] if cover else None, "owned_value": owned_value,
                    "commander": next((e["name"] for e in es if e["board"] == "commander"), None)})
    return out
