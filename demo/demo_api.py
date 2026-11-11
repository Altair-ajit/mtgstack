"""In-browser stand-in for server.py: the web UI's /api/* calls are routed here (Pyodide), straight into
the real mtgstack modules. Same handlers, no HTTP server."""
import json
import re
import traceback

from mtgstack import collection as col, decks, prices, tcgplayer
from mtgstack.catalog import Catalog
from mtgstack.query import QueryError, query, query_entries, summarize
from mtgstack import query as qmod

CONN = None
CAT = None
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


class HTTPError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


def setup(conn):
    global CONN, CAT
    CONN, CAT = conn, Catalog(conn, online=False)


def handle(method, path, query_json, body, form_json):
    q = json.loads(query_json or "{}")
    b = json.loads(body) if body else {}
    form = json.loads(form_json) if form_json else {}
    for m, rx, fn in ROUTES:
        mt = rx.match(path)
        if m == method and mt:
            try:
                out = fn(*mt.groups(), q=q, b=b, form=form)
                if isinstance(out, str):
                    return (200, "text/plain", out)
                return (200, "application/json", json.dumps(out, default=str))
            except HTTPError as e:
                return (e.status, "application/json", json.dumps({"detail": e.detail}))
            except (KeyError, ValueError, QueryError) as e:
                return (400, "application/json", json.dumps({"detail": str(e).strip("'")}))
            except Exception as e:
                traceback.print_exc()
                return (500, "application/json", json.dumps({"detail": f"{type(e).__name__}: {e}"}))
    return (404, "application/json", json.dumps({"detail": f"no route {method} {path}"}))


def _int(v):
    return int(v) if v not in (None, "", "null") else None


def _bool(v):
    return str(v).lower() in ("1", "true", "yes", "on")


# ---- collection
@route("GET", "/api/query")
def _(q, b, form):
    lots = query(CONN, q.get("q", ""), _int(q.get("limit")))
    out = {"summary": summarize(lots)}
    if q.get("view", "stacks") == "stacks":
        out["stacks"] = col.stacks(CONN, lots)
    else:
        out["lots"] = lots
    return out


@route("GET", "/api/help/query")
def _(q, b, form):
    return qmod.__doc__


@route("POST", "/api/lots")
def _(q, b, form):
    allocations = []
    lot_id = col.add(CONN, b["printing_id"], b.get("qty", 1), b.get("finish", "nonfoil"), b.get("condition", "NM"),
                     b.get("language", "en"), b.get("tags", []), b.get("acquired_price"), b.get("notes", ""),
                     True, b.get("auto_allocate", True), allocations)
    for a in allocations:
        a["deck_progress"] = decks.reconcile(CONN, a["deck"])["totals"]["progress"]
    return {**col.get_lot(CONN, lot_id), "allocations": allocations}


@route("POST", r"/api/lots/(\d+)/move")
def _(lot, q, b, form):
    new = col.move(CONN, int(lot), b.get("qty"), b.get("add_tags"), b.get("remove_tags"), b.get("set_tags"), b.get("condition"))
    return {"lot": col.get_lot(CONN, new), "source": col.get_lot(CONN, int(lot))}


@route("POST", r"/api/lots/(\d+)/send")
def _(lot, q, b, form):
    new = decks.send(CONN, int(lot), b.get("qty"), b.get("deck"), b.get("board", "main"))
    return {"lot": col.get_lot(CONN, new), "source": col.get_lot(CONN, int(lot))}


@route("POST", r"/api/lots/(\d+)/remove")
def _(lot, q, b, form):
    col.remove(CONN, int(lot), _int(q.get("qty")), q.get("reason", ""))
    return {"ok": True}


@route("POST", "/api/bulk/move")
def _(q, b, form):
    ids = col.move_many(CONN, query(CONN, b["q"]), b.get("qty_per_lot"), add_tags=b.get("add_tags"),
                        remove_tags=b.get("remove_tags"), set_tags=b.get("set_tags"))
    return {"moved_lots": len(ids), "lot_ids": ids}


@route("POST", "/api/bulk/split-dupes")
def _(q, b, form):
    dry = b.get("dry_run", True)
    return {"plan": col.split_dupes(CONN, query(CONN, b.get("q", "")), b.get("keep", 1), b.get("by", "oracle"),
                                    b.get("add_tags"), b.get("remove_tags"), dry), "applied": not dry}


@route("GET", "/api/tags")
def _(q, b, form):
    return col.tag_counts(CONN)


@route("GET", "/api/events")
def _(q, b, form):
    return col.undo_hint(CONN, _int(q.get("n")) or 50)


@route("GET", "/api/entries")
def _(q, b, form):
    return query_entries(CONN, q.get("q", ""))


# ---- catalog
@route("GET", "/api/catalog/prints")
def _(q, b, form):
    return CAT.prints_by_name(q.get("name", ""), fetch=False)


@route("GET", "/api/catalog/suggest")
def _(q, b, form):
    text = q.get("text", "").lower()
    names = CAT.all_names()
    try:
        return [{"name": n, "score": s} for n, s in CAT.fuzzy_name(text)]
    except Exception:  # rapidfuzz unavailable: substring match
        return [{"name": n, "score": 80} for n in names if text in n.lower()][:8]


# ---- scanner: needs OCR + camera, which the browser demo doesn't have
@route("POST", "/api/scan")
def _(q, b, form):
    raise HTTPError(501, "The scanner needs Tesseract OCR, which doesn't run in this browser demo. Run mtgstack locally to scan.")


@route("POST", "/api/detect")
def _(q, b, form):
    raise HTTPError(501, "not available in the demo")


# ---- decks
@route("GET", "/api/decks")
def _(q, b, form):
    return decks.list_decks(CONN)


@route("POST", "/api/decks")
def _(q, b, form):
    return decks.import_decklist(CONN, CAT, b["name"], b["text"], b.get("format", ""), b.get("slug"), True,
                                 b.get("status", "built"))


@route("PATCH", r"/api/decks/([\w-]+)")
def _(slug, q, b, form):
    return decks.update_deck(CONN, slug, b.get("status"), b.get("priority"), b.get("name"), b.get("format"))


@route("GET", r"/api/decks/([\w-]+)")
def _(slug, q, b, form):
    return decks.reconcile(CONN, slug, q.get("q", ""))


@route("POST", r"/api/decks/([\w-]+)/pull")
def _(slug, q, b, form):
    return decks.pull(CONN, slug, q.get("prefer", "cheapest"), _bool(q.get("dry_run")),
                      col.norm_tags(q.get("remove_tags", "")), q.get("q", ""))


@route("POST", r"/api/decks/([\w-]+)/release")
def _(slug, q, b, form):
    return {"lots": decks.release(CONN, slug)}


@route("DELETE", r"/api/decks/([\w-]+)")
def _(slug, q, b, form):
    decks.release(CONN, slug)
    CONN.execute("DELETE FROM decks WHERE slug=?", (slug,))
    CONN.commit()
    return {"ok": True}


@route("GET", r"/api/decks/([\w-]+)/export")
def _(slug, q, b, form):
    return decks.export(CONN, slug, q.get("fmt", "arena"), q.get("source", "list"))


@route("GET", r"/api/decks/([\w-]+)/missing")
def _(slug, q, b, form):
    rep = decks.reconcile(CONN, slug)
    return "\n".join(f"{e['to_buy']} {e['name']}" + (f" [{e['cheapest']['set_code'].upper()}]" if e["cheapest"] else "")
                     for e in rep["entries"] if e["to_buy"] and e["board"] != "maybe")


# ---- import / export / prices
@route("POST", "/api/import/tcgplayer")
def _(q, b, form):
    return tcgplayer.import_csv(CONN, CAT, form.get("file", ""), form.get("tags", ""), _bool(form.get("dry_run")))


@route("GET", "/api/export/tcgplayer")
def _(q, b, form):
    return tcgplayer.export_csv(CONN, query(CONN, q.get("q", "")))


@route("GET", "/api/export/json")
def _(q, b, form):
    return query(CONN, q.get("q", ""))


@route("POST", "/api/prices/refresh")
def _(q, b, form):
    return {"scryfall": "offline demo: sample prices", "cardkingdom": "offline demo"}


@route("GET", "/api/prices/value")
def _(q, b, form):
    return prices.collection_value(CONN)


@route("GET", "/api/prices/movers")
def _(q, b, form):
    return prices.movers(CONN, _int(q.get("days")) or 7)


@route("GET", r"/api/prices/([\w-]+)")
def _(pid, q, b, form):
    return {"quotes": prices.quotes(CONN, pid),
            "history": {f: prices.history(CONN, pid, f) for f in ("nonfoil", "foil", "etched")}}
