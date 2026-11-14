"""Collection operations: add cards, tag, move subsets, split duplicates, view stacks.

Mental model
------------
  stack   = every copy of a card you own, across all printings (keyed by oracle_id)
  printing= one Scryfall printing inside a stack (set + collector number + language)
  lot     = N identical physical copies: same printing, finish, condition, language AND tag set

Moving "1 of my 3 Sol Rings into deck:atraxa" splits a lot of 3 into a lot of 2 (untouched)
and a lot of 1 (with the new tag). Lots with identical attributes and tags are merged back
automatically, so the table never fragments.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from .db import log_event, rows

CONDITIONS = ("NM", "LP", "MP", "HP", "DMG")
_COND_ALIASES = {"near mint": "NM", "nm": "NM", "lightly played": "LP", "lp": "LP", "excellent": "LP",
                 "moderately played": "MP", "mp": "MP", "heavily played": "HP", "hp": "HP",
                 "damaged": "DMG", "dmg": "DMG", "poor": "DMG"}


def norm_condition(c: str | None) -> str:
    if not c:
        return "NM"
    c = c.strip().lower().replace("_", " ").replace("-", " ")
    for suffix in (" foil", " holofoil", " foil etched"):
        c = c.removesuffix(suffix)
    return _COND_ALIASES.get(c, c.upper() if c.upper() in CONDITIONS else "NM")


def norm_finish(f: str | None) -> str:
    f = (f or "nonfoil").strip().lower()
    if "etch" in f:
        return "etched"
    if f in ("foil", "holofoil", "f", "true", "yes", "1") or (f.endswith("foil") and not f.startswith("non")):
        return "foil"
    return "nonfoil"


def norm_tags(tags: Iterable[str] | str | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = tags.replace(",", " ").split()
    return sorted({t.strip().lower() for t in tags if t and t.strip()})


def tags_of(conn, lot_id: int) -> list[str]:
    return [r[0] for r in conn.execute("SELECT tag FROM lot_tags WHERE lot_id=? ORDER BY tag", (lot_id,))]


def get_lot(conn, lot_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM lot_view WHERE lot_id=?", (lot_id,)).fetchone()
    return dict(r) if r else None


def _set_tags(conn, lot_id: int, tags: list[str]) -> None:
    conn.execute("DELETE FROM lot_tags WHERE lot_id=?", (lot_id,))
    conn.executemany("INSERT INTO lot_tags(lot_id, tag) VALUES (?,?)", [(lot_id, t) for t in tags])


def _merge_into_twin(conn, lot_id: int) -> int:
    """If another lot has identical attributes + tags, fold this one into it. Returns surviving id."""
    me = conn.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
    if not me:
        return lot_id
    my_tags = tags_of(conn, lot_id)
    for other in conn.execute(
            "SELECT * FROM lots WHERE printing_id=? AND finish=? AND condition=? AND language=? AND id<>? ORDER BY id",
            (me["printing_id"], me["finish"], me["condition"], me["language"], lot_id)).fetchall():
        if tags_of(conn, other["id"]) == my_tags:
            # weighted average acquisition price when both known
            ap = other["acquired_price"]
            if me["acquired_price"] is not None and ap is not None:
                ap = (ap * other["qty"] + me["acquired_price"] * me["qty"]) / (other["qty"] + me["qty"])
            elif ap is None:
                ap = me["acquired_price"]
            notes = " | ".join(n for n in (other["notes"], me["notes"]) if n)
            conn.execute("UPDATE lots SET qty=qty+?, acquired_price=?, notes=?, updated_at=datetime('now') WHERE id=?",
                         (me["qty"], ap, notes, other["id"]))
            conn.execute("DELETE FROM lots WHERE id=?", (lot_id,))
            return other["id"]
    return lot_id


def add(conn: sqlite3.Connection, printing_id: str, qty: int = 1, finish: str = "nonfoil", condition: str = "NM",
        language: str = "en", tags: Iterable[str] | str | None = None, acquired_price: float | None = None,
        notes: str = "", commit: bool = True, auto_allocate: bool = True, allocations: list | None = None) -> int:
    """Add copies. If an in-progress ('building') deck still needs this card, the needed copies are tagged
    straight into it (reported in `allocations`). Returns the lot holding the copies; when all of them went
    to decks, that's the (last) deck lot."""
    if qty <= 0:
        raise ValueError("qty must be positive")
    p = conn.execute("SELECT finishes FROM printings WHERE id=?", (printing_id,)).fetchone()
    if not p:
        raise KeyError(f"unknown printing {printing_id}; look it up in the catalog first")
    finish = norm_finish(finish)
    cur = conn.execute(
        "INSERT INTO lots(printing_id, finish, condition, language, qty, acquired_price, notes) VALUES (?,?,?,?,?,?,?)",
        (printing_id, finish, norm_condition(condition), language, qty, acquired_price, notes))
    lot_id = cur.lastrowid
    from . import db
    db.copy_owned(conn, [printing_id])     # split layout: the collection lists it from the user file (db.sync_owned)
    # now owned: start its price history from today's prices
    conn.execute("INSERT OR IGNORE INTO prices SELECT * FROM current_prices WHERE printing_id = ?", (printing_id,))
    _set_tags(conn, lot_id, norm_tags(tags))
    report: list = []
    if auto_allocate:
        from .decks import auto_allocate as _auto
        left = _auto(conn, lot_id, report)
        if allocations is not None:
            allocations.extend(report)
        lot_id = left if left is not None else report[-1]["lot_id"]
    if conn.execute("SELECT 1 FROM lots WHERE id=?", (lot_id,)).fetchone():
        lot_id = _merge_into_twin(conn, lot_id)
    # added straight into a deck: it belongs on that deck's list, or the deck's page does not show it
    if any(t.startswith("deck:") for t in norm_tags(tags)):
        from . import decks as _decks
        oracle = conn.execute("SELECT oracle_id FROM printings WHERE id=?", (printing_id,)).fetchone()[0]
        _decks.lists_follow(conn, oracle, qty, [], norm_tags(tags))
    log_event(conn, "add", printing_id=printing_id, qty=qty, finish=finish, condition=condition,
              language=language, tags=norm_tags(tags), lot_id=lot_id)
    if commit:
        conn.commit()
    return lot_id


def move(conn: sqlite3.Connection, lot_id: int, qty: int | None = None, add_tags: Iterable[str] | str | None = None,
         remove_tags: Iterable[str] | str | None = None, set_tags: Iterable[str] | str | None = None,
         condition: str | None = None, commit: bool = True, lists: bool = True) -> int:
    """Take `qty` copies (default: all) out of a lot and give them a new tag set / condition.

    `lists`: deck lists follow the copies' deck tags (see `decks.lists_follow`), whichever way the tags were changed.

    Returns the id of the lot that now holds the moved copies.
    """
    lot = conn.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
    if not lot:
        raise KeyError(f"no lot {lot_id}")
    qty = lot["qty"] if qty is None else qty
    if not 0 < qty <= lot["qty"]:
        raise ValueError(f"lot {lot_id} has {lot['qty']} copies; cannot move {qty}")
    old_tags = tags_of(conn, lot_id)
    if set_tags is not None:
        new_tags = norm_tags(set_tags)
    else:
        rm = norm_tags(remove_tags)
        new_tags = sorted((set(old_tags) | set(norm_tags(add_tags))) - _expand_removals(old_tags, rm))
    new_cond = norm_condition(condition) if condition else lot["condition"]

    if qty == lot["qty"]:
        target = lot_id
        conn.execute("UPDATE lots SET condition=?, updated_at=datetime('now') WHERE id=?", (new_cond, lot_id))
    else:
        conn.execute("UPDATE lots SET qty=qty-?, updated_at=datetime('now') WHERE id=?", (qty, lot_id))
        target = conn.execute(
            "INSERT INTO lots(printing_id, finish, condition, language, qty, acquired_price, notes) VALUES (?,?,?,?,?,?,?)",
            (lot["printing_id"], lot["finish"], new_cond, lot["language"], qty, lot["acquired_price"],
             lot["notes"])).lastrowid
    _set_tags(conn, target, new_tags)
    target = _merge_into_twin(conn, target)
    if lists and {t for t in old_tags if t.startswith("deck:")} != {t for t in new_tags if t.startswith("deck:")}:
        from . import decks as _decks
        oracle = conn.execute("SELECT oracle_id FROM printings WHERE id=?", (lot["printing_id"],)).fetchone()
        if oracle:
            _decks.lists_follow(conn, oracle[0], qty, old_tags, new_tags)
    log_event(conn, "move", from_lot=lot_id, to_lot=target, qty=qty, old_tags=old_tags, new_tags=new_tags)
    if commit:
        conn.commit()
    return target


def _expand_removals(old: list[str], rm: list[str]) -> set[str]:
    """Support wildcard removal: 'deck:*' removes every deck tag."""
    out = set()
    for r in rm:
        if r.endswith("*"):
            out |= {t for t in old if t.startswith(r[:-1])}
        else:
            out.add(r)
    return out


def remove(conn, lot_id: int, qty: int | None = None, reason: str = "", commit: bool = True) -> None:
    """Remove copies from the collection entirely (sold, traded away, lost)."""
    lot = conn.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
    if not lot:
        raise KeyError(f"no lot {lot_id}")
    qty = lot["qty"] if qty is None else qty
    if qty >= lot["qty"]:
        conn.execute("DELETE FROM lots WHERE id=?", (lot_id,))
    else:
        conn.execute("UPDATE lots SET qty=qty-? WHERE id=?", (qty, lot_id))
    log_event(conn, "remove", lot_id=lot_id, printing_id=lot["printing_id"], qty=qty, reason=reason)
    if commit:
        conn.commit()


def update_lot(conn, lot_id: int, qty: int | None = None, condition: str | None = None, finish: str | None = None,
               language: str | None = None, notes: str | None = None, commit: bool = True) -> int | None:
    """Edit a lot's attributes. qty=0 removes it. Returns the surviving lot id (it may merge into a twin)."""
    lot = conn.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
    if not lot:
        raise KeyError(f"no lot {lot_id}")
    if qty is not None and qty <= 0:
        remove(conn, lot_id, commit=commit)
        return None
    sets, vals = [], []
    for col_, val in (("qty", qty), ("condition", norm_condition(condition) if condition else None),
                      ("finish", norm_finish(finish) if finish else None), ("language", language), ("notes", notes)):
        if val is not None:
            sets.append(f"{col_}=?")
            vals.append(val)
    if sets:
        conn.execute(f"UPDATE lots SET {', '.join(sets)}, updated_at=datetime('now') WHERE id=?", (*vals, lot_id))
    log_event(conn, "update_lot", lot_id=lot_id, qty=qty, condition=condition, finish=finish, language=language)
    new_id = _merge_into_twin(conn, lot_id)
    if commit:
        conn.commit()
    return new_id


def retag(conn, tag_from: str, tag_to: str) -> int:
    """Rename a tag everywhere (e.g. rename a deck)."""
    tag_from, tag_to = tag_from.lower(), tag_to.lower()
    ids = [r[0] for r in conn.execute("SELECT lot_id FROM lot_tags WHERE tag=?", (tag_from,))]
    for i in ids:
        conn.execute("DELETE FROM lot_tags WHERE lot_id=? AND tag=?", (i, tag_from))
        conn.execute("INSERT OR IGNORE INTO lot_tags VALUES (?,?)", (i, tag_to))
        _merge_into_twin(conn, i)
    log_event(conn, "retag", tag_from=tag_from, tag_to=tag_to, lots=ids)
    conn.commit()
    return len(ids)


def move_many(conn, lots: list[dict], qty_per_lot: int | None = None, **kw) -> list[int]:
    out = []
    for lot in lots:
        q = None if qty_per_lot is None else min(qty_per_lot, lot["qty"])
        out.append(move(conn, lot["lot_id"], q, commit=False, **kw))
    conn.commit()
    return out


def _keep_priority(lot: dict) -> tuple:
    """Which copies stay put when splitting duplicates: deck copies first, then best condition,
    then most-tagged, then oldest lot."""
    tags = (lot.get("tags") or "").split()
    in_deck = any(t.startswith("deck:") for t in tags)
    return (not in_deck, CONDITIONS.index(lot["condition"]), -len(tags), lot["lot_id"])


def split_dupes(conn, lots: list[dict], keep: int = 1, by: str = "oracle", add_tags=None, remove_tags=None,
                dry_run: bool = False, protect_decks: bool = True) -> list[dict]:
    """For every stack (by='oracle') or printing+finish (by='printing') among `lots`, keep `keep`
    copies where they are and move the rest (add/remove tags).

    Example: keep 1 of every card in my binder, send extras to 'trade':
        split_dupes(conn, query(conn, 'tag:binder'), keep=1, add_tags=['trade'], remove_tags=['binder'])
    """
    groups: dict[tuple, list[dict]] = {}
    for lot in lots:
        key = (lot["oracle_id"],) if by == "oracle" else (lot["printing_id"], lot["finish"])
        groups.setdefault(key, []).append(lot)
    plan = []
    for key, group in groups.items():
        group.sort(key=_keep_priority)
        remaining_keep = keep
        for lot in group:
            in_deck = protect_decks and any(t.startswith("deck:") for t in (lot.get("tags") or "").split())
            # deck copies are allocated: they count toward `keep` but are never moved out
            stay = lot["qty"] if in_deck else min(max(remaining_keep, 0), lot["qty"])
            remaining_keep -= stay
            if lot["qty"] - stay > 0:
                plan.append({"lot_id": lot["lot_id"], "name": lot["name"], "set": lot["set_code"],
                             "move_qty": lot["qty"] - stay, "stay_qty": stay})
    if not dry_run:
        for step in plan:
            step["to_lot"] = move(conn, step["lot_id"], step["move_qty"], add_tags=add_tags,
                                  remove_tags=remove_tags, commit=False)
        conn.commit()
    return plan


def stacks(conn, lots: list[dict]) -> list[dict]:
    """Group lot rows into stacks -> printings -> lots (the main browse structure)."""
    out: dict[str, dict] = {}
    for lot in lots:
        s = out.setdefault(lot["oracle_id"], {
            "oracle_id": lot["oracle_id"], "name": lot["name"], "type_line": lot["type_line"],
            "mana_cost": lot["mana_cost"], "qty": 0, "value": 0.0, "tags": set(), "printings": {}})
        s["qty"] += lot["qty"]
        s["value"] += (lot["price"] or 0) * lot["qty"]
        s["tags"] |= set((lot["tags"] or "").split())
        pk = (lot["printing_id"], lot["finish"])
        pr = s["printings"].setdefault(pk, {
            "printing_id": lot["printing_id"], "finish": lot["finish"], "set_code": lot["set_code"],
            "set_name": lot["set_name"], "collector_number": lot["collector_number"],
            "rarity": lot["rarity"], "price": lot["price"], "qty": 0, "lots": []})
        pr["qty"] += lot["qty"]
        pr["lots"].append({k: lot[k] for k in ("lot_id", "qty", "condition", "language", "tags", "notes",
                                                "acquired_price")})
    result = []
    for s in sorted(out.values(), key=lambda s: s["name"]):
        s["tags"] = sorted(s["tags"])
        s["value"] = round(s["value"], 2)
        s["printings"] = list(s["printings"].values())
        result.append(s)
    return result


def tag_counts(conn) -> list[dict]:
    return rows(conn.execute(
        "SELECT t.tag, COUNT(*) AS lots, SUM(l.qty) AS cards FROM lot_tags t JOIN lots l ON l.id=t.lot_id "
        "GROUP BY t.tag ORDER BY t.tag"))


def undo_hint(conn, n: int = 20) -> list[dict]:
    return rows(conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,)))
