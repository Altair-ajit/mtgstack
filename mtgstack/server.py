"""HTTP API + web UI. `mtgstack serve` -> http://localhost:8765  (OpenAPI docs at /docs for agents)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import os

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import collection as col, db, decks, prices, tcgplayer
from .catalog import Catalog
from .query import QueryError, query, summarize

WEB = Path(__file__).parent / "web"            # legacy single-file UI (still served at /classic)
DIST = Path(__file__).parent / "web_dist"       # React UI build (frontend/ -> npm run build)


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


class LotPatch(BaseModel):
    qty: int | None = None
    condition: str | None = None
    finish: str | None = None
    language: str | None = None
    notes: str | None = None


class Bulk(BaseModel):
    lot_ids: list[int]
    action: str                     # send | tag | untag | remove
    qty: int | None = None          # per lot; None = all copies
    deck: str | None = None
    board: str = "main"
    tags: list[str] = []


class Label(BaseModel):
    verdict: str                    # correct | wrong | unreadable
    printing_id: str | None = None
    finish: str | None = None
    language: str | None = None
    notes: str = ""


class ImportPreview(BaseModel):
    text: str
    filename: str | None = None


class ImportCommit(BaseModel):
    rows: list[dict]
    dest: dict


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
    decks.cover_all(conn)     # cards put in a deck by older versions show on its page (see decks.cover_all)
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
    def api_prints(name: str, complete: bool = False):
        """`complete=true`: make sure every printing is known (card database not downloaded yet), for a picker."""
        return guard(catalog.prints_by_name, name, True, complete)

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
    # The phone build runs the recognition model in JavaScript, which can only be awaited: it sets this to an
    # async (list of rectified cards) -> list of Match. The desktop server leaves it None (onnxruntime, in-process).
    app.state.picture_matcher = None

    async def prepare(data: list[bytes], corners=None):
        if app.state.picture_matcher is None:
            return None
        from .scanner import detect
        from .scanner.resolve import prepare_crops
        try:
            return await prepare_crops(detect.load(data[0]), app.state.picture_matcher, corners)
        except Exception as e:                           # a broken model must never break scanning
            import logging
            logging.getLogger("mtgstack.scanner").warning("picture recognition failed: %s: %s", type(e).__name__, e)
            return None

    @app.post("/api/scan")
    async def api_scan(files: list[UploadFile] = File(...), save: bool = True, source: str = "scan",
                       hints: str = Form(""), model_version: str = Form(""), corners: str = Form("")):
        """Identify a card. The scan is also saved to the Scanner Lab (scan_id) so it can be labelled.
        `hints`: optional JSON list of picture matches the client computed with the recognition model,
        best first: [{"printing_id", "score", "group"}].
        `corners`: optional JSON [[x, y] x 4] (0-1 coords), the outline /api/detect just reported for this
        view: the card is then not searched for again."""
        from . import scanlab
        from .scanner.resolve import Scanner
        data = [await f.read() for f in files]
        try:
            hint_list = [h for h in json.loads(hints) if isinstance(h, dict) and h.get("printing_id")
                         and isinstance(h.get("score"), (int, float))][:50] if hints else None
        except ValueError:
            raise HTTPException(400, "hints must be a JSON list")
        try:
            outline = json.loads(corners) if corners else None
        except ValueError:
            outline = None                                # only a shortcut: a bad one is ignored
        prepared = None if hint_list else await prepare(data, outline)
        res = guard(lambda: Scanner(catalog, fetch_images=online).scan(
            data, hints=hint_list, hints_version=model_version[:60], prepared=prepared, corners=outline)).to_dict()
        # market price per finish of the printing found, so a scan can be valued without another request
        res["prices"] = {r[0]: r[1] for r in conn.execute(
            "SELECT finish, price FROM current_prices WHERE printing_id=? AND source='tcgplayer' AND kind='market'",
            (res["printing_id"],))} if res.get("printing_id") else {}
        if save:
            res["scan_id"] = scanlab.save(data, res, source=source[:20] or "scan")
        return res

    @app.post("/api/detect")
    async def api_detect(file: UploadFile = File(...)):
        """Live guidance: send a small (~640px) camera frame, get card corners (0-1 coords) + a hint."""
        from .scanner import detect
        img = detect.load(await file.read())
        return detect.track(img)

    # ---- scanner lab -----------------------------------------------------------------------
    from . import scanlab

    def make_scanner(ocr_only: bool = False):
        from .scanner.resolve import Scanner
        return Scanner(catalog, fetch_images=online, recognizer=None if ocr_only else "default")

    @app.post("/api/lab/upload")
    async def api_lab_upload(files: list[UploadFile] = File(...)):
        """Bulk: each photo is scanned separately and saved for review."""
        out = []
        for f in files:
            b = await f.read()
            try:
                prepared = await prepare([b])
                r = guard(lambda: make_scanner().scan([b], prepared=prepared)).to_dict()
            except HTTPException as e:
                r = {"name": None, "printing_id": None, "confidence": 0, "evidence": [str(e.detail)]}
            out.append({"scan_id": scanlab.save([b], r, source="upload"), "name": r.get("name"), "file": f.filename})
        return {"scans": out}

    @app.get("/api/lab/scans")
    def api_lab_scans(status: str = "", limit: int = 200, offset: int = 0):
        return scanlab.list_scans(status, limit, offset)

    @app.get("/api/lab/scans/{scan_id}")
    def api_lab_scan(scan_id: str):
        try:
            return scanlab.get(scan_id)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/lab/file/{scan_id}/{name}")
    def api_lab_file(scan_id: str, name: str):
        try:
            return FileResponse(scanlab.file_path(scan_id, name), media_type="image/jpeg")
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/lab/scans/{scan_id}/label")
    def api_lab_label(scan_id: str, body: Label):
        return guard(scanlab.label, conn, scan_id, body.verdict, body.printing_id, body.finish, body.language, body.notes)

    @app.delete("/api/lab/scans/{scan_id}")
    def api_lab_delete(scan_id: str):
        guard(scanlab.delete, scan_id)
        return {"ok": True}

    @app.get("/api/lab/stats")
    def api_lab_stats():
        return scanlab.stats()

    @app.post("/api/lab/rerun")
    def api_lab_rerun(ocr_only: bool = False):
        """Re-scan the labelled photos. `ocr_only=true` leaves the recognition model out, to compare the two."""
        return scanlab.rerun(lambda: make_scanner(ocr_only), lock)

    @app.get("/api/lab/rerun")
    def api_lab_rerun_status():
        return scanlab.RERUN

    @app.get("/api/lab/export")
    def api_lab_export():
        from fastapi.responses import Response
        return Response(scanlab.export_zip(), media_type="application/zip",
                        headers={"Content-Disposition": "attachment; filename=mtgstack-scans.zip"})

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
    def api_deck_from_tags(slug: str, name: str | None = None, keep: bool = False):
        """Rebuild the decklist from the cards tagged into the deck. `keep=true` only grows the list to cover them
        (what the scanner does after putting cards in a deck), leaving the rest of the list alone."""
        return {"slug": guard(decks.deck_from_tags, conn, slug, name, keep)}

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

    # ---- browsing (React UI) ------------------------------------------------------------------
    from . import browse, importer

    def _csv(v: str | None) -> list[str]:
        return [x for x in (v or "").split(",") if x]

    @app.get("/api/cards")
    def api_cards(q: str = "", set: str = "", rarity: str = "", color: str = "", type: str = "", finish: str = "",
                  condition: str = "", language: str = "", tag: str = "", deck: str = "", min_price: str = "",
                  max_price: str = "", sort: str = "name", page: int = 1, size: int = 60, facets: bool = True):
        """Collection rows (printing + finish + condition + language), filtered, sorted, paged, with facet counts."""
        f = {"set": _csv(set), "rarity": _csv(rarity), "color": _csv(color), "type": _csv(type),
             "finish": _csv(finish), "condition": _csv(condition), "language": _csv(language), "tag": _csv(tag),
             "deck": deck or None, "min_price": min_price, "max_price": max_price}
        return guard(browse.cards, conn, q, f, sort, page, size, facets)

    @app.get("/api/facets")
    def api_facets(q: str = "", set: str = "", rarity: str = "", color: str = "", type: str = "", finish: str = "",
                   condition: str = "", language: str = "", tag: str = "", deck: str = "", min_price: str = "",
                   max_price: str = ""):
        """The counts next to each filter value, for the same search and filters as /api/cards. They cost several
        times the page itself, so the UI asks for them apart (`/api/cards?facets=false`) and only when they show."""
        f = {"set": _csv(set), "rarity": _csv(rarity), "color": _csv(color), "type": _csv(type),
             "finish": _csv(finish), "condition": _csv(condition), "language": _csv(language), "tag": _csv(tag),
             "deck": deck or None, "min_price": min_price, "max_price": max_price}
        return guard(browse.facet_counts, conn, q, f)

    @app.get("/api/card/{printing_id}")
    def api_card(printing_id: str):
        return guard(browse.card_detail, conn, printing_id)

    @app.get("/api/catalog/names")
    def api_names(text: str):
        found = guard(browse.search_names, conn, text)
        if not found and online and len(text) >= 3:      # catalog not downloaded / brand-new card
            try:
                with lock:
                    catalog.prints_by_name(text)
                found = guard(browse.search_names, conn, text)
            except Exception:
                pass
        return found

    sync_job = {"state": "idle", "bytes": 0, "total": 0, "cards": 0, "error": None}

    @app.get("/api/catalog/status")
    def api_catalog_status():
        with lock:
            n = conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0]
            synced = conn.execute("SELECT value FROM meta WHERE key='catalog_synced_at'").fetchone()
        return {"printings": n, "synced_at": synced[0] if synced else None, "job": sync_job}

    @app.post("/api/catalog/sync")
    def api_catalog_sync():
        """Download Scryfall's full card database (~500 MB, once; then daily for prices) in the background."""
        if sync_job["state"] == "running":
            return sync_job
        if not online:
            raise HTTPException(400, "offline mode")
        path = db_path

        def run():
            sync_job.update(state="running", bytes=0, total=0, cards=0, error=None)
            try:
                c2 = db.connect(path)
                Catalog(c2).sync_bulk(progress=lambda d, t, n: sync_job.update(bytes=d, total=t, cards=n))
                c2.close()
                sync_job["state"] = "done"
            except Exception as e:  # surfaced to the UI
                sync_job.update(state="error", error=str(e))

        threading.Thread(target=run, daemon=True).start()
        return sync_job

    @app.get("/api/phone")
    def api_phone(request: Request):
        """Addresses a phone can open (set by `serve`), for the QR codes in the Phone dialog."""
        import json as _json
        try:
            urls = _json.loads(os.environ.get("MTGSTACK_PHONE_URLS") or "[]")
        except ValueError:
            urls = []
        from urllib.parse import urlparse
        host = request.url.hostname or ""
        # behind `tailscale serve` the request arrives as plain http with the ts.net host: don't list it twice
        if host not in ("localhost", "127.0.0.1") and not any(urlparse(u["url"]).hostname == host for u in urls):
            urls.insert(0, {"url": str(request.base_url).rstrip("/"), "kind": "current"})
        return {"urls": urls, "secure": request.url.scheme == "https" or any(u["kind"] == "tailscale" for u in urls)}

    @app.get("/api/version")
    def api_version():
        """Changes whenever the UI is rebuilt or the scanner changes, so open pages can offer a reload."""
        idx = DIST / "index.html"
        stamp = int(idx.stat().st_mtime) if idx.exists() else 0
        return {"build": f"{scanlab.SCANNER_VERSION}-{stamp}", "scanner": scanlab.SCANNER_VERSION}

    @app.get("/api/summary")
    def api_summary():
        with lock:
            t = conn.execute("SELECT COALESCE(SUM(qty),0), COUNT(DISTINCT printing_id) FROM lots").fetchone()
            v = conn.execute("SELECT ROUND(COALESCE(SUM(qty*COALESCE(price,0)),0),2) FROM lot_view").fetchone()[0]
            n = conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0]
            d = conn.execute("SELECT COUNT(*), SUM(status='building') FROM decks").fetchone()
        return {"cards": t[0], "printings_owned": t[1], "value": v, "catalog_printings": n,
                "decks": d[0], "building": d[1] or 0, "online": online}

    @app.patch("/api/lots/{lot_id}")
    def api_lot_patch(lot_id: int, body: LotPatch):
        new = guard(col.update_lot, conn, lot_id, body.qty, body.condition, body.finish, body.language, body.notes)
        return {"lot": col.get_lot(conn, new) if new else None}

    @app.post("/api/lots/bulk")
    def api_lots_bulk(body: Bulk):
        def run():
            done = []
            for lid in body.lot_ids:
                if not conn.execute("SELECT 1 FROM lots WHERE id=?", (lid,)).fetchone():
                    continue          # merged away by an earlier step in this batch
                if body.action == "send":
                    done.append(decks.send(conn, lid, body.qty, body.deck, body.board, commit=False))
                elif body.action == "tag":
                    done.append(col.move(conn, lid, body.qty, add_tags=body.tags, commit=False))
                elif body.action == "untag":
                    done.append(col.move(conn, lid, body.qty, remove_tags=body.tags, commit=False))
                elif body.action == "remove":
                    col.remove(conn, lid, body.qty, "bulk remove", commit=False)
                else:
                    raise ValueError(f"unknown action {body.action}")
            conn.commit()
            return {"lots": len(body.lot_ids)}
        return guard(run)

    @app.post("/api/import/preview")
    def api_import_preview(body: ImportPreview):
        """Paste / .txt / .csv -> every row matched to a printing (or flagged), nothing saved yet."""
        return guard(importer.preview, conn, catalog, body.text, body.filename, online)

    @app.post("/api/import/commit")
    def api_import_commit(body: ImportCommit):
        res = guard(importer.commit, conn, catalog, body.rows, body.dest)
        for a in res.get("allocations", []):
            a["deck_progress"] = guard(decks.reconcile, conn, a["deck"])["totals"]["progress"]
        return res

    # ---- web -------------------------------------------------------------------------------
    app.state.conn = conn
    if os.environ.get("MTGSTACK_BROWSER"):      # phone build: the page itself is served by GitHub Pages
        return app
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/classic")
    def classic():
        return FileResponse(WEB / "index.html")

    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    import mimetypes
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    NOCACHE = {"Cache-Control": "no-cache"}   # index.html must be re-checked so phones pick up new builds

    def _index():
        return FileResponse(DIST / "index.html" if (DIST / "index.html").exists() else WEB / "index.html", headers=NOCACHE)

    @app.get("/")
    def index():
        return _index()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """Client-side routes (/decks/foo, /card/...) all load the React app."""
        if path.startswith("api/"):
            raise HTTPException(404, "no such endpoint")
        f = (DIST / path).resolve()
        if DIST.exists() and f.is_file() and f.is_relative_to(DIST.resolve()):
            return FileResponse(f, headers=NOCACHE if f.suffix in (".webmanifest", ".html") else None)
        return _index()

    app.state.conn = conn
    return app


def dev_app() -> FastAPI:
    """App factory for `mtgstack serve --reload` (uvicorn re-imports it on every code change)."""
    import os
    return create_app(os.environ.get("MTGSTACK_DB") or None, online=os.environ.get("MTGSTACK_OFFLINE") != "1")
