"""Collection browsing for the UI: TCGplayer-style rows, filters with counts, sorting, paging.

One row = one printing + finish + condition + language (what TCGplayer shows as a line in a collection),
with the total quantity across lots and the lots themselves (their tags / decks) attached.
"""
from __future__ import annotations

import json
import sqlite3

from .db import rows
from .query import TYPE_ORDER, where_clause

TYPES = ["Creature", "Planeswalker", "Instant", "Sorcery", "Artifact", "Enchantment", "Battle", "Land"]
COLORS = ["W", "U", "B", "R", "G", "C", "M"]
SORTS = {
    "name": "name COLLATE NOCASE ASC, set_code ASC",
    "price": "price IS NULL, price DESC, name",
    "-price": "price IS NULL, price ASC, name",
    "total": "total DESC, name",
    "qty": "qty DESC, name",
    "added": "added DESC, name",
    "set": "released_at DESC, set_code, CAST(collector_number AS INTEGER), collector_number",
    "number": "set_code, CAST(collector_number AS INTEGER), collector_number",
    "cmc": "cmc ASC, name",
    "rarity": "CASE rarity WHEN 'mythic' THEN 0 WHEN 'rare' THEN 1 WHEN 'uncommon' THEN 2 WHEN 'common' THEN 3 ELSE 4 END, name",
    "type": "type_rank, name",
    "color": "CASE WHEN length(colors) > 1 THEN 6 WHEN colors = 'W' THEN 1 WHEN colors = 'U' THEN 2 WHEN colors = 'B' "
             "THEN 3 WHEN colors = 'R' THEN 4 WHEN colors = 'G' THEN 5 ELSE 7 END, name",
}


def _in(col: str, vals) -> tuple[str, list]:
    vals = [v for v in vals if v not in (None, "")]
    return (f"{col} IN ({','.join('?' * len(vals))})", list(vals)) if vals else ("", [])


def filter_sql(f: dict, skip: str | None = None) -> tuple[list[str], list]:
    """Sidebar filters -> SQL conditions on lot_view v. `skip` leaves one dimension out (for its own facet)."""
    conds, params = [], []

    def add(sql, ps):
        if sql:
            conds.append(sql)
            params.extend(ps)

    if skip != "set":
        add(*_in("v.set_code", f.get("set") or []))
    if skip != "rarity":
        add(*_in("v.rarity", f.get("rarity") or []))
    if skip != "finish":
        add(*_in("v.finish", f.get("finish") or []))
    if skip != "condition":
        add(*_in("v.condition", f.get("condition") or []))
    if skip != "language":
        add(*_in("v.language", f.get("language") or []))
    if skip != "color" and f.get("color"):
        parts = []
        for c in f["color"]:
            if c == "C":
                parts.append("COALESCE(v.colors,'') = ''")
            elif c == "M":
                parts.append("length(v.colors) > 1")
            elif c in "WUBRG":
                parts.append(f"instr(v.colors, '{c}') > 0")
        if parts:
            conds.append("(" + " OR ".join(parts) + ")")
    if skip != "type" and f.get("type"):
        parts = [f"v.type_line LIKE ?" for _ in f["type"]]
        conds.append("(" + " OR ".join(parts) + ")")
        params += [f"%{t}%" for t in f["type"]]
    if skip != "tag" and f.get("tag"):
        q, ps = _in("x.tag", f["tag"])
        add(f"EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id = v.lot_id AND {q})", ps)
    if skip != "deck" and f.get("deck"):
        d = f["deck"]
        if d == "free":
            conds.append("NOT EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id = v.lot_id AND x.tag LIKE 'deck:%')")
        elif d == "any":
            conds.append("EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id = v.lot_id AND x.tag LIKE 'deck:%')")
        else:
            add("EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id = v.lot_id AND (x.tag = ? OR x.tag LIKE ?))",
                [f"deck:{d}", f"deck:{d}:%"])
    if f.get("min_price") not in (None, ""):
        add("COALESCE(v.price, 0) >= ?", [float(f["min_price"])])
    if f.get("max_price") not in (None, ""):
        add("COALESCE(v.price, 0) <= ?", [float(f["max_price"])])
    return conds, params


def _where(q: str, f: dict, skip: str | None = None) -> tuple[str, list]:
    w, params, _ = where_clause(q)
    conds = [w] if w else []
    fc, fp = filter_sql(f, skip)
    conds += fc
    return (" WHERE " + " AND ".join(conds)) if conds else "", params + fp


def cards(conn: sqlite3.Connection, q: str = "", filters: dict | None = None, sort: str = "name", page: int = 1,
          size: int = 60, facets: bool = True) -> dict:
    f = filters or {}
    where, params = _where(q, f)
    order = SORTS.get(sort, SORTS["name"])
    grouped = f"""
        SELECT v.printing_id, v.finish, v.condition, v.language,
               MIN(v.oracle_id) oracle_id, MIN(v.name) name, MIN(v.set_code) set_code, MIN(v.set_name) set_name,
               MIN(v.collector_number) collector_number, MIN(v.rarity) rarity, MIN(v.type_line) type_line,
               MIN(v.mana_cost) mana_cost, MIN(v.cmc) cmc, MIN(v.colors) colors, MIN(v.released_at) released_at,
               MIN(v.image_small) image_small, MIN(v.image_normal) image_normal, MIN(v.tcgplayer_id) tcgplayer_id,
               SUM(v.qty) qty, MAX(v.price) price, SUM(v.qty) * COALESCE(MAX(v.price), 0) total,
               MAX(v.created_at) added, group_concat(v.lot_id) lot_ids,
               MIN({TYPE_ORDER}) type_rank
        FROM lot_view v{where}
        GROUP BY v.printing_id, v.finish, v.condition, v.language"""
    # counted without the display columns: the full grouped select costs about as much as fetching the page
    total_rows = conn.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM lot_view v{where} "
                              "GROUP BY v.printing_id, v.finish, v.condition, v.language)", params).fetchone()[0]
    page = max(1, int(page))
    size = max(1, min(int(size), 500))
    page_rows = rows(conn.execute(f"SELECT * FROM ({grouped}) ORDER BY {order} LIMIT ? OFFSET ?",
                                  params + [size, (page - 1) * size]))
    # lots (with tags) only for the rows on this page
    ids = [int(i) for r in page_rows for i in (r["lot_ids"] or "").split(",") if i]
    lots = {}
    if ids:
        for l in conn.execute(f"""SELECT l.id lot_id, l.qty, (SELECT group_concat(tag, ' ') FROM
                                  (SELECT tag FROM lot_tags t WHERE t.lot_id = l.id ORDER BY tag)) tags
                                  FROM lots l WHERE l.id IN ({','.join('?' * len(ids))})""", ids):
            lots[l["lot_id"]] = dict(l)
    for r in page_rows:
        r["lots"] = [lots[int(i)] for i in (r.pop("lot_ids") or "").split(",") if i and int(i) in lots]
        tags = sorted({t for l in r["lots"] for t in (l["tags"] or "").split()})
        r["decks"] = sorted({t.split(":")[1] for t in tags if t.startswith("deck:")})
        r["tags"] = [t for t in tags if not t.startswith("deck:")]
        r["total"] = round(r["total"], 2)
        r.pop("type_rank", None)
    t = conn.execute(f"SELECT COALESCE(SUM(v.qty),0), COUNT(DISTINCT v.oracle_id), "
                     f"ROUND(COALESCE(SUM(v.qty * COALESCE(v.price,0)),0), 2) FROM lot_view v{where}", params).fetchone()
    out = {"rows": page_rows, "total_rows": total_rows, "page": page, "size": size,
           "pages": max(1, -(-total_rows // size)),
           "totals": {"cards": t[0], "unique": t[1], "value": t[2]}}
    if facets:
        out["facets"] = facet_counts(conn, q, f)
    return out


def facet_counts(conn, q: str, f: dict) -> dict:
    """Card counts per filter value, each computed with the other filters applied (standard faceting)."""
    def simple(dim, expr, extra_join="", order="n DESC", limit=200):
        where, params = _where(q, f, skip=dim)
        sql = (f"SELECT {expr} k, SUM(v.qty) n FROM lot_view v {extra_join}{where} GROUP BY k "
               f"HAVING k IS NOT NULL ORDER BY {order} LIMIT {limit}")
        return [{"value": r["k"], "count": r["n"]} for r in conn.execute(sql, params)]

    out = {
        "rarity": simple("rarity", "v.rarity"),
        "finish": simple("finish", "v.finish"),
        "condition": simple("condition", "v.condition",
                            order="CASE k WHEN 'NM' THEN 0 WHEN 'LP' THEN 1 WHEN 'MP' THEN 2 WHEN 'HP' THEN 3 ELSE 4 END"),
        "language": simple("language", "v.language"),
    }
    where, params = _where(q, f, skip="set")
    out["set"] = [{"value": r["set_code"], "label": r["set_name"], "count": r["n"]} for r in conn.execute(
        f"SELECT v.set_code, MIN(v.set_name) set_name, SUM(v.qty) n FROM lot_view v{where} GROUP BY v.set_code "
        f"ORDER BY MIN(v.released_at) DESC", params)]
    where, params = _where(q, f, skip="color")
    r = conn.execute(f"""SELECT {', '.join(f"SUM(CASE WHEN instr(v.colors,'{c}')>0 THEN v.qty ELSE 0 END)" for c in "WUBRG")},
                         SUM(CASE WHEN COALESCE(v.colors,'')='' THEN v.qty ELSE 0 END),
                         SUM(CASE WHEN length(v.colors)>1 THEN v.qty ELSE 0 END) FROM lot_view v{where}""", params).fetchone()
    out["color"] = [{"value": c, "count": r[i] or 0} for i, c in enumerate(COLORS)]
    where, params = _where(q, f, skip="type")
    r = conn.execute("SELECT " + ", ".join(f"SUM(CASE WHEN v.type_line LIKE '%{t}%' THEN v.qty ELSE 0 END)" for t in TYPES)
                     + f" FROM lot_view v{where}", params).fetchone()
    out["type"] = [{"value": t, "count": r[i] or 0} for i, t in enumerate(TYPES)]
    where, params = _where(q, f, skip="tag")
    out["tag"] = [{"value": r["tag"], "count": r["n"]} for r in conn.execute(
        f"SELECT x.tag, SUM(v.qty) n FROM lot_view v JOIN lot_tags x ON x.lot_id = v.lot_id{where}"
        f"{' AND' if where else ' WHERE'} x.tag NOT LIKE 'deck:%' GROUP BY x.tag ORDER BY x.tag", params)]
    where, params = _where(q, f, skip="deck")
    out["deck"] = [{"value": r["slug"], "label": r["name"], "count": r["n"]} for r in conn.execute(
        f"""SELECT d.slug, d.name, SUM(v.qty) n FROM lot_view v JOIN lot_tags x ON x.lot_id = v.lot_id
            JOIN decks d ON x.tag = 'deck:' || d.slug OR x.tag LIKE 'deck:' || d.slug || ':%'{where}
            GROUP BY d.slug ORDER BY d.name""", params)]
    return out


def card_detail(conn, printing_id: str) -> dict:
    p = conn.execute("SELECT * FROM printings WHERE id=?", (printing_id,)).fetchone()
    if not p:
        raise KeyError(f"no printing {printing_id}")
    p = dict(p)
    prices = rows(conn.execute("SELECT finish, source, kind, price, currency, as_of FROM current_prices "
                               "WHERE printing_id=? ORDER BY finish, source, kind", (printing_id,)))
    history = {}
    for h in conn.execute("SELECT finish, as_of, price FROM prices WHERE printing_id=? AND source='tcgplayer' "
                          "AND kind='market' ORDER BY as_of", (printing_id,)):
        history.setdefault(h["finish"], []).append({"date": h["as_of"], "price": h["price"]})
    owned = rows(conn.execute("""SELECT v.lot_id, v.printing_id, v.set_code, v.set_name, v.collector_number, v.finish,
                                        v.condition, v.language, v.qty, v.price, v.tags, v.image_small
                                 FROM lot_view v WHERE v.oracle_id = ? ORDER BY v.released_at DESC, v.finish""",
                              (p["oracle_id"],)))
    printings = rows(conn.execute("""
        SELECT q.id printing_id, q.set_code, q.set_name, q.collector_number, q.rarity, q.lang, q.released_at,
               q.finishes, q.image_small, q.border_color, q.frame_effects, q.promo,
               (SELECT price FROM current_prices c WHERE c.printing_id=q.id AND c.finish='nonfoil' AND c.source='tcgplayer' AND c.kind='market') price,
               (SELECT price FROM current_prices c WHERE c.printing_id=q.id AND c.finish='foil' AND c.source='tcgplayer' AND c.kind='market') price_foil,
               (SELECT COALESCE(SUM(qty),0) FROM lots l WHERE l.printing_id=q.id) owned
        FROM printings q WHERE q.oracle_id = ? ORDER BY q.lang='en' DESC, q.released_at DESC""", (p["oracle_id"],)))
    for x in printings:
        x["finishes"] = json.loads(x["finishes"] or "[]")
        x["frame_effects"] = json.loads(x["frame_effects"] or "[]")
    wanted = rows(conn.execute("""SELECT deck_slug, deck_name, board, wanted, have, to_move, to_buy, deck_status
                                  FROM entry_view WHERE oracle_id = ?""", (p["oracle_id"],)))
    for k in ("finishes", "frame_effects", "promo_types", "legalities"):
        p[k] = json.loads(p[k] or ("{}" if k == "legalities" else "[]"))
    return {"card": p, "prices": prices, "history": history, "owned": owned, "printings": printings,
            "decks": wanted}


def search_names(conn, text: str, limit: int = 12) -> list[dict]:
    """Autocomplete: card names starting with (then containing) the text, newest printing's image."""
    text = text.strip()
    if len(text) < 2:
        return []
    out, seen = [], set()
    for pattern in (text + "%", "%" + text + "%"):
        for r in conn.execute("""SELECT name, oracle_id, MAX(released_at) rel, type_line, mana_cost,
                                        (SELECT image_small FROM printings z WHERE z.oracle_id=p.oracle_id
                                         ORDER BY lang='en' DESC, released_at DESC LIMIT 1) image_small
                                 FROM printings p WHERE name LIKE ? COLLATE NOCASE GROUP BY oracle_id
                                 ORDER BY length(name), name LIMIT ?""", (pattern, limit)):
            if r["oracle_id"] not in seen:
                seen.add(r["oracle_id"])
                out.append(dict(r))
        if len(out) >= limit:
            break
    return out[:limit]
