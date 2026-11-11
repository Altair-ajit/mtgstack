"""Scryfall-flavoured query language over your collection.

    sol ring                         name contains "sol" and "ring"
    "sol ring"                       name contains the phrase
    t:creature c:g cmc<=3            type / colors / mana value
    tag:binder -tag:trade            tags (and negation)
    tag:deck:*                       tag prefix wildcard
    deck:atraxa                      in any board of deck atraxa (deck:atraxa, deck:atraxa:side, ...)
    set:mh3 cn:12 r:mythic lang:ja   printing details
    is:foil is:etched is:nonfoil     finish
    cond:nm  cond<=lp                condition (NM < LP < MP < HP < DMG; <=lp means LP or better)
    price>5  value>=20  qty>1        TCGplayer market price, price*qty, copies in the lot
    is:dupe                          you own more than one copy of the card (any printing)
    is:free                          not allocated to any deck
    is:untagged is:borderless is:showcase is:extendedart is:fullart is:promo
    f:commander                      legal in a format
    (tag:a or tag:b) -is:foil        grouping + OR
    sort:price  sort:-name           ordering (prefix - for descending; price defaults to descending)
                                     keys: name price value cmc type color rarity set qty released added
                                     (aliases: mana/mv=cmc, cost/usd=price)

Deck entries (cards a decklist wants, owned or not) use the same syntax plus:
    deck:atraxa          entries of that deck          board:side       board (main/side/commander/...)
    status:owned         already tagged into the deck  status:move      a free copy you own can be moved in
    status:buy           not owned anywhere free       status:need      move or buy
    is:building          deck is in progress           want>1           copies the list wants
"""
from __future__ import annotations

import re
import sqlite3

from .db import rows

FIELD_ALIASES = {
    "n": "name", "name": "name", "t": "type", "type": "type", "o": "oracle", "oracle": "oracle",
    "c": "color", "color": "color", "colors": "color", "id": "identity", "ci": "identity", "identity": "identity",
    "cmc": "cmc", "mv": "cmc", "s": "set", "e": "set", "set": "set", "cn": "cn", "number": "cn",
    "r": "rarity", "rarity": "rarity", "lang": "lang", "language": "lang", "finish": "finish",
    "cond": "cond", "condition": "cond", "tag": "tag", "tags": "tag", "deck": "deck", "qty": "qty",
    "price": "price", "usd": "price", "value": "value", "f": "format", "format": "format", "legal": "format",
    "is": "is", "not": "not", "sort": "sort", "order": "sort",
    "board": "board", "status": "status", "want": "want", "wanted": "want",
}
RARITY = {"c": "common", "u": "uncommon", "r": "rare", "m": "mythic", "s": "special", "b": "bonus"}
COND_RANK = {"NM": 0, "LP": 1, "MP": 2, "HP": 3, "DMG": 4}
OPS = (">=", "<=", "!=", ":", "=", ">", "<")

TOKEN_RE = re.compile(r'''
    \s*(?:
      (?P<lp>\() | (?P<rp>\)) |
      (?P<neg>-)?(?P<key>[a-zA-Z]+)(?P<op>>=|<=|!=|:|=|>|<)(?P<val>"[^"]*"|[^\s()]+) |
      (?P<nneg>-)?(?P<phrase>"[^"]*") |
      (?P<nneg2>-)?(?P<word>[^\s()]+)
    )''', re.X)


class QueryError(ValueError):
    pass


def tokenize(q: str) -> list[tuple]:
    toks, pos = [], 0
    q = q.strip()
    while pos < len(q):
        m = TOKEN_RE.match(q, pos)
        if not m or m.end() == pos:
            raise QueryError(f"cannot parse near: {q[pos:]!r}")
        pos = m.end()
        if m["lp"]:
            toks.append(("(",))
        elif m["rp"]:
            toks.append((")",))
        elif m["key"] and m["key"].lower() in FIELD_ALIASES:
            toks.append(("term", bool(m["neg"]), FIELD_ALIASES[m["key"].lower()], m["op"], m["val"].strip('"')))
        elif m["phrase"]:
            toks.append(("term", bool(m["nneg"]), "name", ":", m["phrase"].strip('"')))
        else:
            raw = m.group(0).strip()
            if raw.lower() == "or":
                toks.append(("or",))
            elif raw.lower() == "and":
                continue
            else:
                neg = raw.startswith("-") and len(raw) > 1
                toks.append(("term", neg, "name", ":", raw[1:] if neg else raw))
    return toks


def _num(v: str) -> float:
    try:
        return float(v.lstrip("$"))
    except ValueError as e:
        raise QueryError(f"expected a number, got {v!r}") from e


def _cmp(op: str) -> str:
    return {":": "=", "=": "=", "!=": "<>"}.get(op, op)


DECK_TAG = "EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id=v.lot_id AND x.tag LIKE 'deck:%')"


LOT_ONLY = {"tag", "cond", "lang", "qty", "value"}
ENTRY_ONLY = {"board", "status", "want"}


def term_sql(field: str, op: str, val: str, target: str = "lots") -> tuple[str, list]:
    v = val.lower()
    if target == "entries":
        if field in LOT_ONLY:
            raise QueryError(f"{field}: applies to owned copies, not deck entries")
        if field == "deck":
            return ("v.deck_slug = ?", [v]) if v != "*" else ("1", [])
        if field == "board":
            return "v.board = ?", [v]
        if field == "status":
            if v == "need":
                return "v.status IN ('move','buy')", []
            if v not in ("owned", "move", "buy"):
                raise QueryError("status: owned | move | buy | need")
            return "v.status = ?", [v]
        if field == "want":
            return f"v.wanted {_cmp(op)} ?", [_num(val)]
        if field == "finish":
            return "v.finish = ?", [v]
        if field in ("is", "not") and v in ("building", "built"):
            sql = "v.deck_status = 'building'"
            return (sql if (v == "building") == (field == "is") else f"NOT ({sql})"), []
        if field in ("is", "not") and v in ("foil", "nonfoil", "etched", "dupe", "free", "indeck", "deck", "untagged"):
            raise QueryError(f"is:{v} applies to owned copies, not deck entries")
    elif field in ENTRY_ONLY:
        raise QueryError(f"{field}: applies to deck entries (use the deck view / `mtgstack wants`)")
    if field == "name":
        return "v.name LIKE ?", [f"%{val}%"]
    if field == "type":
        return "v.type_line LIKE ?", [f"%{val}%"]
    if field == "oracle":
        return "v.oracle_text LIKE ?", [f"%{val}%"]
    if field in ("color", "identity"):
        col = "v.colors" if field == "color" else "v.color_identity"
        letters = sorted(set(v.upper()) & set("WUBRG"))
        if v in ("c", "colorless"):
            return f"{col} = ''", []
        if v in ("m", "multi", "multicolor"):
            return f"length({col}) > 1", []
        has_all = " AND ".join(f"instr({col}, '{c}') > 0" for c in letters) or "1"
        only = " AND ".join(f"instr({col}, '{c}') = 0" for c in "WUBRG" if c not in letters) or "1"
        if field == "identity" and op == ":":
            op = "<="  # Scryfall semantics: id: means fits within that identity
        if op in (":", ">="):
            return f"({has_all})", []
        if op == "=":
            return f"({has_all} AND {only})", []
        if op == "<=":
            return f"({only})", []
        if op == ">":
            return f"({has_all} AND length({col}) > {len(letters)})", []
        if op == "<":
            return f"({only} AND length({col}) < {len(letters)})", []
        raise QueryError(f"bad color operator {op}")
    if field == "cmc":
        return f"v.cmc {_cmp(op)} ?", [_num(val)]
    if field == "set":
        return "v.set_code = ?", [v]
    if field == "cn":
        return "ltrim(v.collector_number,'0') = ?", [val.lstrip("0") or "0"]
    if field == "rarity":
        r = RARITY.get(v, v)
        if op in (":", "="):
            return "v.rarity = ?", [r]
        order = "CASE v.rarity WHEN 'common' THEN 0 WHEN 'uncommon' THEN 1 WHEN 'rare' THEN 2 WHEN 'mythic' THEN 3 ELSE 4 END"
        rank = ["common", "uncommon", "rare", "mythic"].index(r) if r in ("common", "uncommon", "rare", "mythic") else 4
        return f"{order} {_cmp(op)} ?", [rank]
    if field == "lang":
        return "v.language = ?", [v]
    if field == "finish":
        return "v.finish = ?", [{"foil": "foil", "etched": "etched"}.get(v, "nonfoil")]
    if field == "cond":
        rank = COND_RANK.get(val.upper())
        if rank is None:
            raise QueryError(f"unknown condition {val}")
        case = "CASE v.condition WHEN 'NM' THEN 0 WHEN 'LP' THEN 1 WHEN 'MP' THEN 2 WHEN 'HP' THEN 3 ELSE 4 END"
        return f"{case} {_cmp(op)} ?", [rank]
    if field == "tag":
        if v in ("none", "untagged"):
            return "NOT EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id=v.lot_id)", []
        if v.endswith("*"):
            return "EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id=v.lot_id AND x.tag LIKE ?)", [v[:-1] + "%"]
        return "EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id=v.lot_id AND x.tag = ?)", [v]
    if field == "deck":
        if v == "*":
            return DECK_TAG, []
        return ("EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id=v.lot_id AND (x.tag = ? OR x.tag LIKE ?))",
                [f"deck:{v}", f"deck:{v}:%"])
    if field == "qty":
        return f"v.qty {_cmp(op)} ?", [_num(val)]
    if field == "price":
        return f"COALESCE(v.price, 0) {_cmp(op)} ?", [_num(val)]
    if field == "value":
        return f"COALESCE(v.price, 0) * v.qty {_cmp(op)} ?", [_num(val)]
    if field == "format":
        return "json_extract(v.legalities, '$.' || ?) IN ('legal','restricted')", [v]
    if field in ("is", "not"):
        sql, params = is_sql(v)
        return (f"NOT ({sql})" if field == "not" else sql), params
    raise QueryError(f"unknown field {field}")


def is_sql(v: str) -> tuple[str, list]:
    if v in ("foil", "nonfoil", "etched"):
        return "v.finish = ?", [v]
    if v == "dupe":
        return ("(SELECT SUM(l2.qty) FROM lots l2 JOIN printings p2 ON p2.id=l2.printing_id "
                "WHERE p2.oracle_id = v.oracle_id) > 1"), []
    if v == "free":
        return f"NOT {DECK_TAG}", []
    if v in ("indeck", "deck"):
        return DECK_TAG, []
    if v == "untagged":
        return "NOT EXISTS (SELECT 1 FROM lot_tags x WHERE x.lot_id=v.lot_id)", []
    if v == "borderless":
        return "v.border_color = 'borderless'", []
    if v in ("showcase", "extendedart", "etched_frame", "inverted", "retro"):
        if v == "retro":
            return "v.frame IN ('1993','1997')", []
        return "v.frame_effects LIKE ?", [f'%"{v}"%']
    if v in ("fullart", "full_art"):
        return "v.full_art = 1", []
    if v == "promo":
        return "v.promo_types <> '[]'", []
    if v in ("commander", "legendary"):
        return "v.type_line LIKE '%Legendary%' AND (v.type_line LIKE '%Creature%' OR v.oracle_text LIKE '%can be your commander%')", []
    raise QueryError(f"unknown is: value {v}")


class _Parser:
    def __init__(self, toks, target="lots"):
        self.toks, self.i, self.sort, self.target = toks, 0, [], target

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def expr(self):
        parts = [self.conj()]
        while self.peek() and self.peek()[0] == "or":
            self.i += 1
            parts.append(self.conj())
        sqls = [p for p in parts if p[0]]
        if not sqls:
            return "", []
        return "(" + " OR ".join(s for s, _ in sqls) + ")", [x for _, ps in sqls for x in ps]

    def conj(self):
        sqls, params = [], []
        while self.peek() and self.peek()[0] not in ("or", ")"):
            t = self.peek()
            if t[0] == "(":
                self.i += 1
                s, p = self.expr()
                if not self.peek() or self.peek()[0] != ")":
                    raise QueryError("unbalanced parentheses")
                self.i += 1
                if s:
                    sqls.append(s)
                    params += p
                continue
            self.i += 1
            _, neg, field, op, val = t
            if field == "sort":
                self.sort.append(val)
                continue
            s, p = term_sql(field, op, val, self.target)
            sqls.append(f"NOT ({s})" if neg else s)
            params += p
        return (" AND ".join(sqls), params) if sqls else ("", [])


# primary card type in conventional deck order, for sort:type / grouping
TYPE_ORDER = ("CASE WHEN v.type_line LIKE '%Creature%' THEN 1 WHEN v.type_line LIKE '%Planeswalker%' THEN 2 "
              "WHEN v.type_line LIKE '%Battle%' THEN 3 WHEN v.type_line LIKE '%Instant%' THEN 4 "
              "WHEN v.type_line LIKE '%Sorcery%' THEN 5 WHEN v.type_line LIKE '%Artifact%' THEN 6 "
              "WHEN v.type_line LIKE '%Enchantment%' THEN 7 WHEN v.type_line LIKE '%Land%' THEN 9 ELSE 8 END")
COLOR_ORDER = ("CASE WHEN length(COALESCE(v.colors,'')) > 1 THEN 6 WHEN v.colors = 'W' THEN 1 WHEN v.colors = 'U' THEN 2 "
               "WHEN v.colors = 'B' THEN 3 WHEN v.colors = 'R' THEN 4 WHEN v.colors = 'G' THEN 5 ELSE 7 END")
RARITY_ORDER = ("CASE v.rarity WHEN 'common' THEN 0 WHEN 'uncommon' THEN 1 WHEN 'rare' THEN 2 "
                "WHEN 'mythic' THEN 3 ELSE 4 END")
SORTS = {"name": "v.name", "price": "COALESCE(v.price,0)", "value": "COALESCE(v.price,0)*v.qty",
         "set": "v.set_code", "cmc": "v.cmc", "qty": "v.qty", "released": "v.released_at",
         "added": "v.lot_id", "rarity": RARITY_ORDER, "type": TYPE_ORDER, "color": COLOR_ORDER}
SORT_ALIASES = {"mana": "cmc", "mv": "cmc", "cost": "price", "usd": "price", "colors": "color", "colour": "color"}
ENTRY_SORTS = {**SORTS, "value": "COALESCE(v.price,0)*v.to_buy", "qty": "v.wanted", "added": "v.entry_id",
               "status": "CASE v.status WHEN 'owned' THEN 0 WHEN 'move' THEN 1 ELSE 2 END", "board": "v.board"}


def compile_query(q: str, target: str = "lots") -> tuple[str, list]:
    p = _Parser(tokenize(q or ""), target)
    where, params = p.expr()
    if p.i != len(p.toks):
        raise QueryError("unbalanced parentheses")
    sorts = ENTRY_SORTS if target == "entries" else SORTS
    order = []
    for s in p.sort:
        key = SORT_ALIASES.get(s.lstrip("+-"), s.lstrip("+-"))
        desc = s.startswith("-") or (key in ("price", "value") and not s.startswith("+"))
        if key not in sorts:
            raise QueryError(f"unknown sort {key}; one of {', '.join(sorts)}")
        order.append(f"{sorts[key]} {'DESC' if desc else 'ASC'}")
    if target == "entries":
        order.append("v.deck_slug, v.board, v.name")
        table = "entry_view"
    else:
        order.append("v.name ASC, v.set_code ASC, v.lot_id ASC")
        table = "lot_view"
    sql = f"SELECT * FROM {table} v" + (f" WHERE {where}" if where else "") + " ORDER BY " + ", ".join(order)
    return sql, params


def query(conn: sqlite3.Connection, q: str = "", limit: int | None = None) -> list[dict]:
    sql, params = compile_query(q)
    if limit:
        sql += f" LIMIT {int(limit)}"
    return rows(conn.execute(sql, params))


def query_entries(conn: sqlite3.Connection, q: str = "", limit: int | None = None) -> list[dict]:
    """Decklist entries (owned or not) across decks, with have / to_move / to_buy and card data."""
    sql, params = compile_query(q, "entries")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return rows(conn.execute(sql, params))


def primary_type(type_line: str | None) -> str:
    t = type_line or ""
    for k in ("Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land"):
        if k in t:
            return k
    return "Other"


def summarize(lots: list[dict]) -> dict:
    return {"lots": len(lots), "cards": sum(l["qty"] for l in lots),
            "unique": len({l["oracle_id"] for l in lots}),
            "value_usd": round(sum((l["price"] or 0) * l["qty"] for l in lots), 2)}
