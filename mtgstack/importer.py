"""Import a card list from anywhere: pasted text, a .txt file, or a .csv export.

    parse(text)                 -> detect the format, return normalized rows
    resolve(conn, catalog, rows) -> match every row to an exact printing (locally, in bulk), mark how sure we are
    commit(conn, catalog, rows, dest) -> add the matched rows to the collection or create a deck from them

Formats recognised
  CSV: TCGplayer app (Quantity, Name, Simple Name, Set, Card Number, Set Code, Printing, Condition, Language,
       Rarity, Product ID, SKU), ManaBox, Moxfield, Deckbox, Archidekt, Dragon Shield, and anything with a
       name column (headers are matched by alias).
  Text, one card per line:
       4 Lightning Bolt                    4x Lightning Bolt
       4 Lightning Bolt [2XM]              4 Lightning Bolt [Double Masters]
       4 Lightning Bolt (2XM) 117          4 Lightning Bolt (2XM) 117 *F*
       1 Sol Ring [CMM] 410 (Foil)         Lightning Bolt            (qty 1)
       section headers (Deck, Sideboard, Commander...) and // comments are skipped

Matching order per row (first hit wins):
  Scryfall ID > TCGplayer product ID > set + collector number > set (code or name) + name > name only.
  "name only" picks the newest regular printing and is flagged `guessed` so the preview can ask.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field

from . import collection as col
from .catalog import Catalog

# ---------------------------------------------------------------------------------------- limits (untrusted input)
# An import is text someone pasted or a file from anywhere, and the rows sent back to commit() come from the browser:
# neither is trusted. Everything is bounded, and commit() re-checks every field instead of believing the preview.
MAX_TEXT = 1_000_000        # characters in one import
MAX_LINES = 5000
MAX_LINE = 400              # characters in one line of a text list
MAX_QTY = 9999              # copies on one line
MAX_FIELD = 200             # a CSV name / set name
_TAG = re.compile(r"[a-z0-9][a-z0-9:_.\-]{0,39}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def safe_tags(tags, limit: int = 12) -> list[str]:
    """Normalized tags, or ValueError: letters, digits and : _ . - only, 40 characters, at most `limit`."""
    out = col.norm_tags(tags if isinstance(tags, (list, tuple, set, str)) or tags is None else [])
    bad = [t for t in out if not _TAG.fullmatch(t)]
    if bad:
        raise ValueError(f"tag {bad[0][:40]!r} is not allowed: use letters, digits and : _ . - (40 characters at most)")
    if len(out) > limit:
        raise ValueError(f"too many tags ({len(out)}): {limit} at most")
    return out


def _qty(raw: str) -> int:
    """A count of copies from a list: 1..MAX_QTY, or ValueError (also for inf, nan and absurd lengths)."""
    raw = (raw or "1").strip()
    if len(raw) > 12:
        raise ValueError(raw[:12])
    try:
        n = float(raw)
    except ValueError:
        raise ValueError(raw) from None
    if n != n or n in (float("inf"), float("-inf")) or n != int(n) or not 1 <= n <= MAX_QTY:
        raise ValueError(raw)
    return int(n)


# ------------------------------------------------------------------------------------------------- parsing

HEADER_ALIASES = {
    "qty": ["quantity", "qty", "count", "total quantity", "add to quantity", "amount"],
    "name": ["name", "card name", "card", "product name", "simple name"],
    "set_name": ["set name", "set", "expansion", "edition name"],
    "set_code": ["set code", "setcode", "edition code", "set abbreviation"],
    "edition": ["edition"],            # Moxfield: code; Deckbox: name. Decided per value.
    "number": ["card number", "collector number", "collector #", "number", "cn", "card #"],
    "printing": ["printing", "finish", "foil", "variant", "foil/variant"],
    "condition": ["condition"],
    "language": ["language", "lang"],
    "product_id": ["product id", "tcgplayer id", "tcgplayer product id", "productid", "tcgplayer_id", "tcgplayer productid"],
    "sku": ["sku", "tcgplayer sku"],
    "scryfall_id": ["scryfall id", "scryfall_id", "scryfallid"],
    "price": ["purchase price", "price each", "acquired price", "my price"],
    "tags": ["tags", "binder name", "binder", "list", "collection", "folder name"],
}
LANG_NAMES = {"english": "en", "japanese": "ja", "german": "de", "french": "fr", "italian": "it", "spanish": "es",
              "portuguese": "pt", "korean": "ko", "russian": "ru", "chinese (s)": "zhs", "simplified chinese": "zhs",
              "chinese simplified": "zhs", "chinese (t)": "zht", "traditional chinese": "zht",
              "chinese traditional": "zht", "jp": "ja", "kr": "ko", "cs": "zhs", "ct": "zht"}
SECTION_WORDS = {"deck", "main", "mainboard", "main deck", "sideboard", "side", "commander", "commanders",
                 "companion", "maybeboard", "maybe", "considering", "tokens", "lands", "creatures", "spells"}

_TEXT_LINE = re.compile(r"""^
    (?:(?P<qty>\d+)\s*[xX]?\s+)?                      # 4 / 4x
    (?P<name>.+?)                                     # card name (lazy)
    (?:\s+[\[(](?P<set>[^\])]{2,60})[\])])?           # [CMM] / (CMM) / [Commander Masters]
    (?:\s+\#?(?P<num>[0-9][0-9A-Za-z★\-]*|[A-Za-z]{1,4}-?[0-9]+[a-z]?))?   # 410 / 410a / STA-42
    (?P<marks>(?:\s+(?:\*[FE]\*|\((?:foil|etched|foil\ etched|holo)\)|\[(?:foil|etched)\]|-?\s?foil|etched))*)
    \s*$""", re.X | re.I)


@dataclass
class Row:
    line: int
    raw: str
    qty: int = 1
    name: str | None = None
    set_code: str | None = None
    set_name: str | None = None
    number: str | None = None
    finish: str | None = None          # None = unspecified (nonfoil unless the printing only exists in foil)
    condition: str = "NM"
    language: str = "en"
    product_id: int | None = None
    sku: str | None = None
    scryfall_id: str | None = None
    price: float | None = None
    tags: list[str] = field(default_factory=list)
    # filled by resolve()
    status: str = "pending"            # matched | guessed | unmatched
    how: str = ""
    printing_id: str | None = None
    match: dict | None = None
    alternatives: list[dict] = field(default_factory=list)


def _clean_name(name: str) -> str:
    n = name.strip().strip('"')
    # TCGplayer product names carry variant suffixes: "Sol Ring (Borderless)", "Island (0276)", "X - Foil Etched"
    n = re.sub(r"\s+\((?:borderless|showcase|extended art|etched|foil etched|retro frame|display commander|"
               r"\d{2,4}|[^)]*\bart\b[^)]*|[^)]*variant[^)]*|[^)]*frame[^)]*|[^)]*promo[^)]*|[^)]*stamped[^)]*)\)\s*$",
               "", n, flags=re.I)
    return re.sub(r"\s{2,}", " ", n).strip()


def _finish_from(*vals: str | None) -> str | None:
    text = " ".join(v for v in vals if v).lower()
    if not text:
        return None
    if "etch" in text:
        return "etched"
    if re.search(r"\bnon[- ]?foil\b|\bnormal\b|\bregular\b", text):
        return "nonfoil"
    if re.search(r"\bfoil\b|\bholo|\btrue\b|\byes\b|^1$|\*f\*", text):
        return "foil"
    return None


def _looks_like_code(v: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9]{2,6}", v.strip()))


def detect(text: str, filename: str | None = None) -> str:
    first = next((l for l in text.lstrip("﻿").splitlines() if l.strip()), "")
    if filename and filename.lower().endswith(".csv"):
        return "csv"
    low = first.lower()
    if ("," in first or "\t" in first or ";" in first) and any(
            a in re.split(r"[,\t;]", low.replace('"', "")) for names in HEADER_ALIASES.values() for a in names):
        return "csv"
    return "text"


def parse(text: str, filename: str | None = None) -> tuple[str, list[Row], list[str]]:
    if not isinstance(text, str):
        raise ValueError("the list must be text")
    if len(text) > MAX_TEXT:
        raise ValueError(f"the list is too long ({len(text):,} characters): {MAX_TEXT:,} at most")
    text = _CONTROL.sub("", text)
    if text.count("\n") + 1 > MAX_LINES:
        raise ValueError(f"the list has too many lines: {MAX_LINES:,} at most. Import it in parts.")
    filename = (filename or "")[:200] if isinstance(filename, str) else None
    kind = detect(text, filename)
    return (kind, *(_parse_csv(text) if kind == "csv" else _parse_text(text)))


def _parse_csv(text: str) -> tuple[list[Row], list[str]]:
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    try:
        records = list(reader)                     # a field over the csv module's size limit, a stray NUL...
    except csv.Error as e:
        return [], [f"this is not a CSV file that can be read: {e}"]
    norm = {(h or "").strip().lower(): h for h in (reader.fieldnames or [])}
    hm = {}
    for key, names in HEADER_ALIASES.items():
        for n in names:
            if n in norm:
                hm[key] = norm[n]
                break
    if "name" not in hm and "product_id" not in hm and "scryfall_id" not in hm:
        return [], [f"no card name / product id / Scryfall id column in: {', '.join(reader.fieldnames or [])}"]
    rows, errors = [], []
    for i, rec in enumerate(records, start=2):
        g = lambda k: (rec.get(hm[k]) or "").strip() if k in hm else ""
        if not any((v or "").strip() for v in rec.values() if isinstance(v, str)):
            continue
        r = Row(line=i, raw=",".join((v or "") for v in rec.values() if isinstance(v, str))[:300])
        try:
            r.qty = _qty(g("qty"))
        except ValueError:
            errors.append(f"line {i}: bad quantity {g('qty')[:20]!r} (a whole number from 1 to {MAX_QTY})")
            continue
        if len(g("name")) > MAX_FIELD or len(g("set_name")) > MAX_FIELD:
            errors.append(f"line {i}: the name or set is too long to be a card")
            continue
        r.name = _clean_name(g("name")) if g("name") else None
        r.set_code = g("set_code").lower() or None
        r.set_name = g("set_name") or None
        if g("edition"):
            if _looks_like_code(g("edition")):
                r.set_code = r.set_code or g("edition").lower()
            else:
                r.set_name = r.set_name or g("edition")
        r.number = g("number").lstrip("#") or None
        cond = g("condition")
        r.finish = _finish_from(g("printing")) or _finish_from(cond) or (
            "foil" if "printing" not in hm and _finish_from(g("name")) == "foil" else None)
        r.condition = col.norm_condition(cond)
        lang = g("language").lower()
        r.language = LANG_NAMES.get(lang, lang if re.fullmatch(r"[a-z]{2,3}", lang) else "en")
        if g("product_id").isdigit():
            r.product_id = int(g("product_id"))
        r.sku = g("sku")[:40] or None
        r.scryfall_id = g("scryfall_id").lower() if _UUID.fullmatch(g("scryfall_id").lower()) else None
        p = g("price").replace("$", "").replace(",", "")
        r.price = float(p) if re.fullmatch(r"\d+(\.\d+)?", p) else None
        if g("tags"):
            # tags from a file are a convenience: the ones that are not plain tag text are dropped, not trusted
            r.tags = [t for t in col.norm_tags(re.sub(r"\s+", "-", t.strip()) for t in re.split(r"[,;|]", g("tags")) if t.strip())
                      if _TAG.fullmatch(t)][:12]
        rows.append(r)
    return rows, errors


def _parse_text(text: str) -> tuple[list[Row], list[str]]:
    rows, errors = [], []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("//", "#")) or line.lower().rstrip(":").strip() in SECTION_WORDS:
            continue
        if re.match(r"^(about|name)\b", line, re.I) and not re.match(r"^\d", line):
            continue
        if len(line) > MAX_LINE:
            errors.append(f"line {i}: too long to be a card ({len(line)} characters)")
            continue
        line = re.sub(r"^SB:\s*", "", line)
        m = _TEXT_LINE.match(line)
        if not m or not m.group("name"):
            errors.append(f"line {i}: couldn't read {raw[:80]!r}")
            continue
        try:
            qty = _qty(m.group("qty") or "1")
        except ValueError:
            errors.append(f"line {i}: bad quantity {(m.group('qty') or '')[:20]!r} (a whole number from 1 to {MAX_QTY})")
            continue
        r = Row(line=i, raw=raw[:300], qty=qty)
        name = m.group("name").strip()
        marks = m.group("marks") or ""
        # "Card Name - Foil" style suffix
        tail = re.search(r"\s+-\s+(foil|etched|foil etched)$", name, re.I)
        if tail:
            marks += " " + tail.group(1)
            name = name[:tail.start()]
        r.name = _clean_name(name)
        s = (m.group("set") or "").strip()
        if s:
            if s.lower() in ("foil", "etched", "foil etched"):
                marks += " " + s
            elif _looks_like_code(s):
                r.set_code = s.lower()
            else:
                r.set_name = s
        r.number = m.group("num")
        r.finish = _finish_from(marks)
        rows.append(r)
    return rows, errors


# ------------------------------------------------------------------------------------------------ resolving

def _card_summary(p: dict, finish: str | None, conn) -> dict:
    finishes = json.loads(p.get("finishes") or "[]")
    fin = finish if finish in finishes else (finishes[0] if finishes and finish is None and "nonfoil" not in finishes
                                             else (finish or "nonfoil"))
    price = conn.execute("SELECT price FROM current_prices WHERE printing_id=? AND finish=? AND source='tcgplayer' "
                         "AND kind='market'", (p["id"], fin)).fetchone()
    return {"printing_id": p["id"], "name": p["name"], "set_code": p["set_code"], "set_name": p["set_name"],
            "number": p["collector_number"], "rarity": p["rarity"], "finish": fin, "finishes": finishes,
            "image": p.get("image_small"), "price": price[0] if price else None, "lang": p["lang"]}


def _rows(conn, sql, params) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def _by_name(conn, name: str) -> list[dict]:
    return _rows(conn, """SELECT * FROM printings WHERE name = ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE
                          ORDER BY lang = 'en' DESC, promo ASC, (set_code IN (SELECT code FROM sets WHERE set_type
                          IN ('memorabilia','token','funny','alchemy'))) ASC, released_at DESC""",
                 (name, name + " // %"))


def _set_matches(p: dict, code: str | None, set_name: str | None) -> bool:
    if code and p["set_code"] == code:
        return True
    if set_name and (p["set_name"] or "").lower() == set_name.lower():
        return True
    return False


def resolve(conn, catalog: Catalog, rows: list[Row], online: bool = True) -> list[Row]:
    pending = [r for r in rows if r.status == "pending"]
    _resolve_local(conn, pending)
    misses = [r for r in pending if r.status == "unmatched"]
    if misses and online and catalog.online:
        _fetch_missing(conn, catalog, misses)
        for r in misses:
            r.status = "pending"
        _resolve_local(conn, misses)
    return rows


def _resolve_local(conn, rows: list[Row]) -> None:
    for r in rows:
        p, how = None, ""
        if r.scryfall_id:
            p = conn.execute("SELECT * FROM printings WHERE id=?", (r.scryfall_id,)).fetchone()
            how = "Scryfall ID"
        if not p and r.product_id:
            p = conn.execute("SELECT * FROM printings WHERE tcgplayer_id=?", (r.product_id,)).fetchone()
            how = "TCGplayer product ID"
            if not p:
                p = conn.execute("SELECT * FROM printings WHERE tcgplayer_etched_id=?", (r.product_id,)).fetchone()
                if p:
                    r.finish = "etched"
        if not p and r.set_code and r.number:
            p = conn.execute("SELECT * FROM printings WHERE set_code=? AND ltrim(collector_number,'0')=? "
                             "ORDER BY lang=? DESC", (r.set_code, r.number.lstrip("0") or "0", r.language)).fetchone()
            how = "set + collector number"
        p = dict(p) if p else None
        cands = []
        if not p and r.name:
            cands = _by_name(conn, r.name)
            in_set = [c for c in cands if _set_matches(c, r.set_code, r.set_name)]
            if in_set and r.number:
                in_set = [c for c in in_set if c["collector_number"].lstrip("0") == r.number.lstrip("0")] or in_set
            if in_set:
                lang = [c for c in in_set if c["lang"] == r.language] or in_set
                p, how = lang[0], "set + name"
                if len({c["id"] for c in lang}) > 1 and not r.number:
                    how += " (several printings in this set - check)"
                    r.status = "guessed"
            elif cands:
                lang = [c for c in cands if c["lang"] == r.language] or cands
                p, how = lang[0], "name only - newest printing picked"
                r.status = "guessed"
        if p:
            r.printing_id = p["id"]
            r.match = _card_summary(p, r.finish, conn)
            r.how = how
            if r.status != "guessed":
                r.status = "matched"
            if r.status == "guessed":
                pool = cands or _by_name(conn, p["name"])
                r.alternatives = [_card_summary(c, r.finish, conn) for c in pool if c["lang"] == p["lang"]][:24]
        else:
            r.status = "unmatched"
            r.how = "not found" + (" (catalog not downloaded yet?)" if not conn.execute(
                "SELECT 1 FROM printings LIMIT 1").fetchone() else "")


def _fetch_missing(conn, catalog: Catalog, rows: list[Row]) -> None:
    """Fill catalog gaps from Scryfall in batches of 75 instead of one request per card."""
    ids = []
    for r in rows:
        if r.scryfall_id:
            ids.append({"id": r.scryfall_id})
        elif r.set_code and r.number:
            ids.append({"set": r.set_code, "collector_number": r.number})
        elif r.name and r.set_code:
            ids.append({"name": r.name, "set": r.set_code})
        elif r.name:
            ids.append({"name": r.name})
    ids = ids[:1500]            # what is asked of Scryfall for one import is bounded, whatever was pasted
    try:
        cards = catalog.lookup_batch(ids)
        # name-only hits: pull every printing so set names / numbers can match next pass
        for name in {c["name"] for c in cards}:
            if any(r.name and r.name.lower() == name.lower() and (r.set_name or not r.set_code) for r in rows):
                catalog.prints_by_name(name)
        for r in rows[:50]:          # product IDs have no batch endpoint; cap single lookups
            if r.product_id and not r.name:
                catalog.by_tcgplayer(r.product_id)
    except Exception:
        pass


def preview(conn, catalog: Catalog, text: str, filename: str | None = None, online: bool = True) -> dict:
    kind, rows, errors = parse(text, filename)
    resolve(conn, catalog, rows, online)
    counts = {s: sum(1 for r in rows if r.status == s) for s in ("matched", "guessed", "unmatched")}
    cards = {s: sum(r.qty for r in rows if r.status == s) for s in ("matched", "guessed", "unmatched")}
    value = round(sum(((r.match or {}).get("price") or 0) * r.qty for r in rows), 2)
    return {"format": kind, "rows": [asdict(r) for r in rows], "errors": errors, "counts": counts, "cards": cards,
            "value": value}


# --------------------------------------------------------------------------------------------- committing

def _checked(conn, rows, dest) -> list[dict]:
    """The rows to import, every field re-validated (they come back from the browser), or ValueError.

    An import is all or nothing: a row that is not a card (never matched, or naming a printing that does not exist)
    fails the whole import unless it was explicitly left out in the review (`include: false`)."""
    if not isinstance(rows, list) or not isinstance(dest, dict):
        raise ValueError("malformed import")
    if len(rows) > MAX_LINES:
        raise ValueError(f"too many rows ({len(rows):,}): {MAX_LINES:,} at most. Import it in parts.")
    from .decks import BOARDS
    out, bad = [], []
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("malformed import row")
        if r.get("include") is False:
            continue
        pid = r.get("printing_id")
        label = str(r.get("name") or r.get("raw") or "?").strip()[:40]
        if r.get("status") == "unmatched" or not isinstance(pid, str) or not 0 < len(pid) <= 64 \
                or not conn.execute("SELECT 1 FROM printings WHERE id=?", (pid,)).fetchone():
            bad.append(label)
            continue
        qty = r.get("qty")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY:
            raise ValueError(f"{label}: the quantity must be a whole number from 1 to {MAX_QTY}")
        match = r.get("match") if isinstance(r.get("match"), dict) else {}
        finish = match.get("finish") or r.get("finish")
        price = r.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price != price or not 0 <= price <= 1_000_000:
            price = None
        lang = r.get("language")
        board = r.get("board")
        out.append({
            "printing_id": pid, "qty": qty,
            "finish": col.norm_finish(finish if isinstance(finish, str) else None),
            "wants_finish": r.get("finish") if r.get("finish") in ("nonfoil", "foil", "etched") else None,
            "condition": col.norm_condition(r.get("condition") if isinstance(r.get("condition"), str) else None),
            "language": lang if isinstance(lang, str) and re.fullmatch(r"[a-z]{2,3}", lang) else "en",
            "tags": safe_tags(r.get("tags")), "price": price,
            "board": board if board in BOARDS else "main",
            "exact": r.get("status") == "matched" and bool(r.get("set_code") or r.get("number") or r.get("product_id")
                                                           or r.get("scryfall_id")),
        })
    if bad:
        more = f" and {len(bad) - 5} more" if len(bad) > 5 else ""
        raise ValueError(f"{len(bad)} line{'s are not cards' if len(bad) > 1 else ' is not a card'}, so nothing was imported: "
                         f"{', '.join(bad[:5])}{more}. Fix or remove them and import again.")
    return out


def commit(conn, catalog: Catalog, rows: list[dict], dest: dict) -> dict:
    """dest = {"type": "collection", "tags": [...]}
           | {"type": "deck", "name": str, "format": str, "status": "building"|"built", "owned": bool}
    All or nothing: every row must be a real card (see `_checked`), and a failure part-way leaves nothing behind.
    Returns counts + any in-progress deck allocations."""
    from . import decks
    rows = _checked(conn, rows, dest)
    allocations: list = []
    added = 0
    try:
        if dest.get("type") == "deck":
            name = _CONTROL.sub("", dest.get("name") if isinstance(dest.get("name"), str) else "").strip()
            if not 0 < len(name) <= 80:
                raise ValueError("a deck needs a name (80 characters at most)")
            fmt = dest.get("format") if isinstance(dest.get("format"), str) else ""
            if not re.fullmatch(r"[A-Za-z0-9 \-]{0,30}", fmt):
                raise ValueError("unknown deck format")
            if conn.execute("SELECT 1 FROM decks WHERE slug=?", (decks.slugify(name),)).fetchone():
                raise ValueError(f"you already have a deck named {name!r}: pick another name")
            status = dest.get("status") if dest.get("status") in ("building", "built") else "building"
            slug = decks.create_deck(conn, name, fmt, status=status)
            for r in rows:
                p = catalog.get(r["printing_id"])
                conn.execute("INSERT INTO deck_entries(deck_slug, board, card_name, oracle_id, printing_id, finish, qty) "
                             "VALUES (?,?,?,?,?,?,?)", (slug, r["board"], p["name"], p["oracle_id"],
                                                         p["id"] if r["exact"] else None,
                                                         r["wants_finish"] if r["exact"] else None, r["qty"]))
                if dest.get("owned"):
                    col.add(conn, p["id"], r["qty"], r["finish"], r["condition"], r["language"],
                            [decks.board_tag(slug, r["board"])] + r["tags"], r["price"],
                            commit=False, auto_allocate=False)
                added += r["qty"]
            conn.commit()
            return {"deck": slug, "cards": added, "rows": len(rows), "allocations": []}
        tags = safe_tags(dest.get("tags"))
        for r in rows:
            col.add(conn, r["printing_id"], r["qty"], r["finish"], r["condition"], r["language"],
                    tags + r["tags"], r["price"], commit=False, allocations=allocations)
            added += r["qty"]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"cards": added, "rows": len(rows), "allocations": allocations}
