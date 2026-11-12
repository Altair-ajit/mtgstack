"""Run the real mtgstack API inside a browser (Pyodide in a Web Worker) - the phone build.

The JavaScript worker (frontend/public/py/worker.js) loads Pyodide, mounts persistent storage at /data, then:
    import mtgstack.browser as B; B.start()
    status, headers, body = await B.handle(method, path_and_query, headers, body)
Every /api/* request from the page is routed here by the service worker, so the React app is identical to the
server version.

Differences from the desktop server, all handled here:
  - no threads: anyio's thread offloading runs inline; background jobs run to completion within the request
  - no rapidfuzz wheel: mtgstack._fuzz stands in
  - HTTP (Scryfall lookups, reference images) goes through synchronous XMLHttpRequest, which workers allow
  - OCR uses Tesseract compiled to WebAssembly (worker.js exposes mtgOcr)
  - the catalog is a separate file (/data/catalog.sqlite) downloaded prebuilt; the small user file is what gets saved
"""
from __future__ import annotations

import os
import sys
import types
from urllib.parse import unquote, urlsplit

DATA = "/data"
USER_DB = f"{DATA}/user.db"
CATALOG_DB = f"{DATA}/catalog.sqlite"
APP = None


# ---- shims ----------------------------------------------------------------------------------------------------
def install_shims() -> None:
    from . import _fuzz

    rf = types.ModuleType("rapidfuzz")
    dist = types.ModuleType("rapidfuzz.distance")
    dist.Levenshtein = _fuzz.Levenshtein
    rf.fuzz, rf.process, rf.distance = _fuzz.fuzz, _fuzz.process, dist
    sys.modules.update({"rapidfuzz": rf, "rapidfuzz.distance": dist})

    import anyio.to_thread

    async def run_sync(func, *args, **_):          # no threads in the browser: run inline
        return func(*args)
    anyio.to_thread.run_sync = run_sync

    import threading

    class InlineThread(threading.Thread):           # background jobs finish within the request
        def start(self):
            self.run()
    threading.Thread = InlineThread

    _patch_httpx()


_FORBIDDEN = {"user-agent", "accept-encoding", "connection", "host", "content-length", "cookie"}


def _patch_httpx() -> None:
    import httpx

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        from js import Uint8Array, XMLHttpRequest
        from pyodide.ffi import to_js
        xhr = XMLHttpRequest.new()
        xhr.open(request.method, str(request.url), False)        # synchronous: allowed inside workers
        xhr.responseType = "arraybuffer"
        for k, v in request.headers.items():
            if k.lower() not in _FORBIDDEN:
                try:
                    xhr.setRequestHeader(k, v)
                except Exception:
                    pass
        body = request.read()
        try:
            xhr.send(to_js(body) if body else None)
        except Exception as e:                                 # CORS/network failure
            raise httpx.ConnectError(str(e), request=request) from None
        if xhr.status == 0:
            raise httpx.ConnectError("network error (offline or blocked)", request=request)
        headers = []
        for line in str(xhr.getAllResponseHeaders() or "").split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip().lower() not in ("content-encoding", "content-length"):   # already decoded
                    headers.append((k.strip(), v.strip()))
        content = bytes(Uint8Array.new(xhr.response).to_py()) if xhr.response else b""
        return httpx.Response(xhr.status, headers=headers, content=content, request=request)

    httpx.HTTPTransport.handle_request = handle_request


def _ocr(png: bytes, psm: int) -> str:
    import js
    from pyodide.ffi import to_js
    return str(js.mtgOcr(to_js(png), psm))


# ---- app lifecycle ------------------------------------------------------------------------------------------
def start() -> None:
    """Configure the environment and build the FastAPI app (call once after /data is mounted)."""
    global APP
    os.makedirs(DATA, exist_ok=True)
    global CATALOG_DB
    CATALOG_DB = os.environ.get("MTGSTACK_CATALOG_DB") or CATALOG_DB    # worker.js: OPFS file or /data copy
    os.environ.update(MTGSTACK_BROWSER="1", MTGSTACK_DB=USER_DB, MTGSTACK_CATALOG_DB=CATALOG_DB,
                      MTGSTACK_SCANS=f"{DATA}/scans")
    install_shims()
    from .server import create_app
    APP = create_app(USER_DB, online=True)


def enable_scanner() -> None:
    """Once OpenCV and Tesseract are loaded: route the scanner's OCR to Tesseract-in-WebAssembly."""
    from .scanner import markers
    markers.set_ocr_backend(_ocr)


def close() -> None:
    global APP
    if APP is not None:
        try:
            APP.state.conn.close()
        except Exception:
            pass
        APP = None


def swap_catalog(new_path: str, version: str, printings: int) -> dict:
    close()
    return after_catalog_swap(version, printings, new_path)


def after_catalog_swap(version: str, printings: int, new_path: str | None = None) -> dict:
    """The worker has put a freshly downloaded card catalog in place (call close() first). Reopen, keeping the
    collection, and record today's prices in the history of every card you own or want."""
    if new_path:
        os.replace(new_path, CATALOG_DB)
    start()
    conn = APP.state.conn
    conn.execute("""INSERT OR IGNORE INTO prices (printing_id, finish, source, kind, price, currency, as_of)
        SELECT cp.printing_id, cp.finish, cp.source, cp.kind, cp.price, cp.currency, cp.as_of FROM current_prices cp
        WHERE cp.printing_id IN (SELECT printing_id FROM lots
                                 UNION SELECT printing_id FROM deck_entries WHERE printing_id IS NOT NULL)""")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('catalog_version', ?)", (version,))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('catalog_synced_at', datetime('now'))")
    conn.commit()
    return {"printings": conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0], "version": version}


def catalog_version() -> str | None:
    r = APP.state.conn.execute("SELECT value FROM meta WHERE key='catalog_version'").fetchone()
    return r[0] if r else None


# ---- requests -----------------------------------------------------------------------------------------------
async def handle(method: str, url: str, headers, body):
    """Run one HTTP request through the ASGI app. Returns (status, [[name, value], ...], body bytes)."""
    if hasattr(headers, "to_py"):
        headers = headers.to_py()
    if hasattr(body, "to_py"):
        body = bytes(body.to_py())
    body = body or b""
    u = urlsplit(url)
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method.upper(),
        "scheme": "https", "path": unquote(u.path), "raw_path": u.path.encode(), "query_string": u.query.encode(),
        "root_path": "", "headers": [(str(k).lower().encode(), str(v).encode()) for k, v in headers],
        "client": ("127.0.0.1", 0), "server": ("localhost", 443),
    }
    pending = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    out = {"status": 500, "headers": [], "body": bytearray()}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = [[k.decode(), v.decode()] for k, v in msg.get("headers", [])]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    try:
        await APP(scope, receive, send)
    except Exception as e:  # surface Python errors to the UI instead of a silent hang
        import traceback
        traceback.print_exc()
        import json
        return 500, [["content-type", "application/json"]], json.dumps({"detail": f"{type(e).__name__}: {e}"}).encode()
    return out["status"], out["headers"], bytes(out["body"])
