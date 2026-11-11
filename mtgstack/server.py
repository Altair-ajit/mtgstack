"""HTTP API + web UI. `mtgstack serve` -> http://localhost:8765  (OpenAPI docs at /docs for agents)."""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import collection as col, db, decks, prices, tcgplayer
from .catalog import Catalog
from .query import QueryError, query, summarize

WEB = Path(__file__).parent / "web"


class AddLot(BaseModel):
    printing_id: str
    qty: int = 1
    finish: str = "nonfoil"
    condition: str = "NM"
    language: str = "en"
    tags: list[str] = []
    acquired_price: float | None = None
    notes: str = ""
    auto_allocate: bool = True


class Move(BaseModel):
    qty: int | None = None
    add_tags: list[str] = []
    remove_tags: list[str] = []
    set_tags: list[str] | None = None
    condition: str | None = None


class BulkMove(Move):
    q: str
    qty_per_lot: int | None = None


class SplitDupes(BaseModel):
    q: str = ""
    keep: int = 1
    by: str = "oracle"
    add_tags: list[str] = []
    remove_tags: list[str] = []
    dry_run: bool = True


class DeckImport(BaseModel):
    name: str
    text: str
    format: str = ""
    slug: str | None = None
    status: str = "built"           # "building" = in progress: new cards it needs are auto-tagged in


class DeckPatch(BaseModel):
    status: str | None = None
    priority: int | None = None
    name: str | None = None
    format: str | None = None


class Send(BaseModel):
    qty: int | None = None
    deck: str | None = None         # None = back to the collection (no deck)
    board: str = "main"


def create_app(db_path: str | None = None, online: bool = True) -> FastAPI:
    conn = db.connect(db_path)
    lock = threading.Lock()   # sqlite connection shared across request threads
    catalog = Catalog(conn, online=online)
    app = FastAPI(title="mtgstack", version="0.1.0",
                  description="Tag-based MTG collection. Query syntax: GET /api/help/query")

    def guard(fn, *a, **kw):
        with lock:
            try:
                return fn(*a, **kw)
            except (KeyError, ValueError, QueryError) as e:
                raise HTTPException(400, str(e))

    # ---- collection ------------------------------------------------------------------------
    @app.get("/api/query")
    def api_query(q: str = "", view: str = "stacks", limit: int | None = None):
        lots = guard(query, conn, q, limit)
        out = {"summary": summarize(lots)}
        out["stacks" if view == "stacks" else "lots"] = col.stacks(conn, lots) if view == "stacks" else lots
        return out

    @app.get("/api/help/query", response_class=PlainTextResponse)
    def api_query_help():
        from . import query as qmod
        return qmod.__doc__

    @app.post("/api/lots")
    def api_add(body: AddLot):
        """Add copies. `allocations` lists copies auto-tagged into in-progress decks."""
        allocations: list = []
        lot_id = guard(col.add, conn, body.printing_id, body.qty, body.finish, body.condition, body.language,
                       body.tags, body.acquired_price, body.notes, True, body.auto_allocate, allocations)
        for a in allocations:
            a["deck_progress"] = guard(decks.reconcile, conn, a["deck"])["totals"]["progress"]
        return {**col.get_lot(conn, lot_id), "allocations": allocations}

    @app.post("/api/lots/{lot_id}/send")
    def api_send(lot_id: int, body: Send):
        """Override: move copies out of their deck, back to the collection or into another deck/board."""
        new = guard(decks.send, conn, lot_id, body.qty, body.deck, body.board)
        return {"lot": col.get_lot(conn, new), "source": col.get_lot(conn, lot_id)}

    @app.get("/api/entries")
    def api_entries(q: str = ""):
        """Decklist entries across decks (owned or not), e.g. q='is:building status:buy sort:price'."""
        from .query import query_entries
        return guard(query_entries, conn, q)

    @app.post("/api/lots/{lot_id}/move")
    def api_move(lot_id: int, body: Move):
        new = guard(col.move, conn, lot_id, body.qty, body.add_tags, body.remove_tags, body.set_tags, body.condition)
        return {"lot": col.get_lot(conn, new), "source": col.get_lot(conn, lot_id)}

    @app.post("/api/lots/{lot_id}/remove")
    def api_remove(lot_id: int, qty: int | None = None, reason: str = ""):
        guard(col.remove, conn, lot_id, qty, reason)
        return {"ok": True}

    @app.post("/api/bulk/move")
    def api_bulk_move(body: BulkMove):
        def run():
            lots = query(conn, body.q)
            return col.move_many(conn, lots, body.qty_per_lot, add_tags=body.add_tags,
                                 remove_tags=body.remove_tags, set_tags=body.set_tags)
        ids = guard(run)
        return {"moved_lots": len(ids), "lot_ids": ids}

    @app.post("/api/bulk/split-dupes")
    def api_split(body: SplitDupes):
        return {"plan": guard(lambda: col.split_dupes(conn, query(conn, body.q), body.keep, body.by,
                                                      body.add_tags, body.remove_tags, body.dry_run)),
                "applied": not body.dry_run}

    @app.get("/api/tags")
    def api_tags():
        return guard(col.tag_counts, conn)

    @app.post("/api/tags/rename")
    def api_retag(tag_from: str, tag_to: str):
        return {"lots": guard(col.retag, conn, tag_from, tag_to)}

    @app.get("/api/events")
    def api_events(n: int = 50):
        return guard(col.undo_hint, conn, n)

    # ---- catalog ---------------------------------------------------------------------------
    @app.get("/api/catalog/prints")
    def api_prints(name: str):
        return guard(catalog.prints_by_name, name)

    @app.get("/api/catalog/lookup")
    def api_lookup(set_code: str, number: str, lang: str = "en"):
        p = guard(catalog.by_set_cn, set_code, number, lang)
        if not p:
            raise HTTPException(404, "not found")
        return p

    @app.get("/api/catalog/suggest")
    def api_suggest(text: str):
        return [{"name": n, "score": s} for n, s in guard(catalog.fuzzy_name, text)]

    # ---- scanner ---------------------------------------------------------------------------
    @app.post("/api/scan")
    async def api_scan(files: list[UploadFile] = File(...)):
        from .scanner.resolve import Scanner
        data = [await f.read() for f in files]
        res = guard(lambda: Scanner(catalog, fetch_images=online).scan(data))
        return res.to_dict()

    @app.post("/api/detect")
    async def api_detect(file: UploadFile = File(...)):
        """Live guidance: send a small (~640px) camera frame, get card corners (0-1 coords) + a hint."""
        from .scanner import detect
        img = detect.load(await file.read())
        return detect.track(img)

    # ---- decks -----------------------------------------------------------------------------
    @app.get("/api/decks")
    def api_decks():
        return guard(decks.list_decks, conn)

    @app.post("/api/decks")
    def api_deck_import(body: DeckImport):
        return guard(decks.import_decklist, conn, catalog, body.name, body.text, body.format, body.slug,
                     True, body.status)

    @app.patch("/api/decks/{slug}")
    def api_deck_patch(slug: str, body: DeckPatch):
        return guard(decks.update_deck, conn, slug, body.status, body.priority, body.name, body.format)

    @app.post("/api/decks/{slug}/from-tags")
    def api_deck_from_tags(slug: str, name: str | None = None):
        return {"slug": guard(decks.deck_from_tags, conn, slug, name)}

    @app.get("/api/decks/{slug}")
    def api_deck(slug: str, q: str = ""):
        return guard(decks.reconcile, conn, slug, q)

    @app.post("/api/decks/{slug}/pull")
    def api_pull(slug: str, prefer: str = "cheapest", dry_run: bool = False, remove_tags: str = "", q: str = ""):
        return guard(decks.pull, conn, slug, prefer, dry_run, col.norm_tags(remove_tags), q)

    @app.post("/api/decks/{slug}/release")
    def api_release(slug: str):
        return {"lots": guard(decks.release, conn, slug)}

    @app.delete("/api/decks/{slug}")
    def api_deck_delete(slug: str, release_cards: bool = True):
        def run():
            if release_cards:
                decks.release(conn, slug)
            conn.execute("DELETE FROM decks WHERE slug=?", (slug,))
            conn.commit()
        guard(run)
        return {"ok": True}

    @app.get("/api/decks/{slug}/export", response_class=PlainTextResponse)
    def api_export(slug: str, fmt: str = "arena", source: str = "list"):
        return guard(decks.export, conn, slug, fmt, source)

    @app.get("/api/decks/{slug}/missing", response_class=PlainTextResponse)
    def api_missing(slug: str):
        rep = guard(decks.reconcile, conn, slug)
        return "\n".join(f"{e['to_buy']} {e['name']}" + (f" [{e['cheapest']['set_code'].upper()}]" if e["cheapest"] else "")
                         for e in rep["entries"] if e["to_buy"] and e["board"] != "maybe")

    # ---- import / export -------------------------------------------------------------------
    @app.post("/api/import/tcgplayer")
    async def api_import(file: UploadFile = File(...), tags: str = Form(""), dry_run: bool = Form(False)):
        text = (await file.read()).decode("utf-8-sig", errors="replace")
        return guard(tcgplayer.import_csv, conn, catalog, text, tags, dry_run)

    @app.get("/api/export/tcgplayer", response_class=PlainTextResponse)
    def api_export_tcg(q: str = ""):
        return guard(lambda: tcgplayer.export_csv(conn, query(conn, q)))

    @app.get("/api/export/json")
    def api_export_json(q: str = ""):
        return guard(query, conn, q)

    # ---- prices ----------------------------------------------------------------------------
    @app.post("/api/prices/refresh")
    def api_refresh():
        return guard(prices.refresh_all, conn, catalog)

    @app.get("/api/prices/value")
    def api_value():
        return guard(prices.collection_value, conn)

    @app.get("/api/prices/movers")
    def api_movers(days: int = 7):
        return guard(prices.movers, conn, days)

    @app.get("/api/prices/{printing_id}")
    def api_quotes(printing_id: str):
        return {"quotes": guard(prices.quotes, conn, printing_id),
                "history": {f: guard(prices.history, conn, printing_id, f) for f in ("nonfoil", "foil", "etched")}}

    # ---- web -------------------------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    app.state.conn = conn
    return app


def dev_app() -> FastAPI:
    """App factory for `mtgstack serve --reload` (uvicorn re-imports it on every code change)."""
    import os
    return create_app(os.environ.get("MTGSTACK_DB") or None, online=os.environ.get("MTGSTACK_OFFLINE") != "1")
