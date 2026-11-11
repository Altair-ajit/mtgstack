"""Card catalog backed by Scryfall.

Two ways to fill it:
  * on demand  - every lookup that misses locally hits the Scryfall API and caches all printings
                 of that card (cheap; good for getting started)
  * bulk       - `mtgstack catalog sync` streams Scryfall's `default_cards` bulk file (~every printing,
                 English + any non-English-only printings) and also records today's prices.

Scryfall asks for a User-Agent/Accept header and <=10 requests/second; the client enforces 100ms spacing.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from typing import Iterable

import httpx

from .db import rows

API = "https://api.scryfall.com"
HEADERS = {"User-Agent": "mtgstack/0.1 (personal collection manager)", "Accept": "application/json"}

# Printed language codes on the card -> Scryfall lang codes
PRINTED_LANG = {"EN": "en", "DE": "de", "FR": "fr", "IT": "it", "ES": "es", "SP": "es", "PT": "pt",
                "JP": "ja", "JA": "ja", "KO": "ko", "KR": "ko", "RU": "ru", "CS": "zhs", "CT": "zht",
                "PH": "ph"}

_COLS = ["id", "oracle_id", "name", "set_code", "set_name", "collector_number", "rarity", "lang",
         "released_at", "finishes", "border_color", "frame", "frame_effects", "full_art", "promo",
         "promo_types", "type_line", "mana_cost", "cmc", "colors", "color_identity", "oracle_text",
         "tcgplayer_id", "tcgplayer_etched_id", "cardmarket_id", "arena_id", "mtgo_id",
         "image_normal", "image_art_crop", "legalities"]


def flatten(card: dict) -> dict:
    """Scryfall card object -> printings row."""
    faces = card.get("card_faces") or []
    face0 = faces[0] if faces else {}
    imgs = card.get("image_uris") or face0.get("image_uris") or {}
    oracle_text = card.get("oracle_text")
    if oracle_text is None and faces:
        oracle_text = "\n//\n".join(f.get("oracle_text", "") for f in faces)
    colors = card.get("colors")
    if colors is None and faces:
        colors = sorted({c for f in faces for c in f.get("colors", [])})
    return {
        "id": card["id"],
        "oracle_id": card.get("oracle_id") or face0.get("oracle_id") or card["id"],
        "name": card["name"],
        "set_code": card["set"].lower(),
        "set_name": card.get("set_name"),
        "collector_number": card["collector_number"],
        "rarity": card.get("rarity"),
        "lang": card.get("lang", "en"),
        "released_at": card.get("released_at"),
        "finishes": json.dumps(card.get("finishes") or []),
        "border_color": card.get("border_color"),
        "frame": card.get("frame"),
        "frame_effects": json.dumps(card.get("frame_effects") or []),
        "full_art": int(bool(card.get("full_art"))),
        "promo": int(bool(card.get("promo"))),
        "promo_types": json.dumps(card.get("promo_types") or []),
        "type_line": card.get("type_line") or face0.get("type_line"),
        "mana_cost": card.get("mana_cost") or face0.get("mana_cost"),
        "cmc": card.get("cmc"),
        "colors": "".join(colors or []),
        "color_identity": "".join(card.get("color_identity") or []),
        "oracle_text": oracle_text,
        "tcgplayer_id": card.get("tcgplayer_id"),
        "tcgplayer_etched_id": card.get("tcgplayer_etched_id"),
        "cardmarket_id": card.get("cardmarket_id"),
        "arena_id": card.get("arena_id"),
        "mtgo_id": card.get("mtgo_id"),
        "image_normal": imgs.get("normal"),
        "image_art_crop": imgs.get("art_crop"),
        "legalities": json.dumps(card.get("legalities") or {}),
    }


def upsert_cards(conn: sqlite3.Connection, cards: Iterable[dict], record_prices: bool = True) -> int:
    today = dt.date.today().isoformat()
    n = 0
    placeholders = ",".join("?" for _ in _COLS)
    updates = ",".join(f"{c}=excluded.{c}" for c in _COLS[1:])
    for card in cards:
        if card.get("object") not in (None, "card") or "oracle_id" not in card and not card.get("card_faces"):
            continue
        row = flatten(card)
        conn.execute(f"INSERT INTO printings({','.join(_COLS)}) VALUES ({placeholders}) "
                     f"ON CONFLICT(id) DO UPDATE SET {updates}", [row[c] for c in _COLS])
        if record_prices:
            # Scryfall's usd/usd_foil/usd_etched are TCGplayer market prices.
            p = card.get("prices") or {}
            for key, finish in (("usd", "nonfoil"), ("usd_foil", "foil"), ("usd_etched", "etched")):
                if p.get(key):
                    conn.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
                                 (card["id"], finish, "tcgplayer", "market", float(p[key]), "USD", today))
            for key, finish in (("eur", "nonfoil"), ("eur_foil", "foil"), ("eur_etched", "etched")):
                if p.get(key):
                    conn.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
                                 (card["id"], finish, "cardmarket", "market", float(p[key]), "EUR", today))
        n += 1
    conn.commit()
    return n


class Scryfall:
    """Thin rate-limited Scryfall client. Swap `http` for a fake in tests."""

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True)
        self._last = 0.0

    def get(self, path: str, **params):
        wait = 0.1 - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.http.get(path if path.startswith("http") else API + path, params=params or None)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def search_all(self, q: str, **params) -> list[dict]:
        out, page = [], self.get("/cards/search", q=q, **params)
        while page:
            out.extend(page.get("data", []))
            page = self.get(page["next_page"]) if page.get("has_more") else None
        return out

    def prints_of(self, name: str) -> list[dict]:
        return self.search_all(f'!"{name}"', unique="prints", include_extras="true", include_variations="true")

    def named(self, name: str, fuzzy: bool = True) -> dict | None:
        return self.get("/cards/named", **({"fuzzy": name} if fuzzy else {"exact": name}))

    def by_set_cn(self, set_code: str, cn: str, lang: str | None = None) -> dict | None:
        path = f"/cards/{set_code.lower()}/{cn}" + (f"/{lang}" if lang and lang != "en" else "")
        return self.get(path)

    def by_tcgplayer(self, product_id: int) -> dict | None:
        return self.get(f"/cards/tcgplayer/{product_id}")

    def collection(self, identifiers: list[dict]) -> list[dict]:
        out = []
        for i in range(0, len(identifiers), 75):  # API max 75 per request
            wait = 0.1 - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.http.post(API + "/cards/collection", json={"identifiers": identifiers[i:i + 75]})
            r.raise_for_status()
            out.extend(r.json().get("data", []))
        return out

    def bulk_url(self, kind: str = "default_cards") -> str:
        meta = self.get("/bulk-data")
        for item in meta["data"]:
            if item["type"] == kind:
                return item["download_uri"]
        raise RuntimeError(f"bulk type {kind} not found")


class Catalog:
    """Local-first catalog lookups; falls back to Scryfall when `online`."""

    def __init__(self, conn: sqlite3.Connection, client: Scryfall | None = None, online: bool = True):
        self.conn, self.online = conn, online
        self._client = client

    @property
    def client(self) -> Scryfall:
        if self._client is None:
            self._client = Scryfall()
        return self._client

    # ---- lookups -------------------------------------------------------------------------
    def get(self, printing_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM printings WHERE id=?", (printing_id,)).fetchone()
        return dict(r) if r else None

    def prints_by_name(self, name: str, fetch: bool = True) -> list[dict]:
        name = name.strip()
        q = "SELECT * FROM printings WHERE name = ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE ORDER BY released_at DESC"
        found = rows(self.conn.execute(q, (name, name + " // %")))
        if found or not (fetch and self.online):
            return found
        card = self.client.named(name)
        if not card:
            return []
        upsert_cards(self.conn, self.client.prints_of(card["name"]))
        return rows(self.conn.execute(q, (card["name"], card["name"] + " // %")))

    def by_set_cn(self, set_code: str, cn: str, lang: str = "en", fetch: bool = True) -> dict | None:
        cn = cn.lstrip("0") or "0"
        r = self.conn.execute(
            "SELECT * FROM printings WHERE set_code=? AND ltrim(collector_number,'0')=? ORDER BY lang=? DESC",
            (set_code.lower(), cn, lang)).fetchone()
        if r or not (fetch and self.online):
            return dict(r) if r else None
        card = self.client.by_set_cn(set_code, cn, lang) or self.client.by_set_cn(set_code, cn)
        if not card:
            return None
        upsert_cards(self.conn, [card])
        return self.get(card["id"])

    def by_tcgplayer(self, product_id: int, fetch: bool = True) -> dict | None:
        r = self.conn.execute("SELECT * FROM printings WHERE tcgplayer_id=? OR tcgplayer_etched_id=?",
                              (product_id, product_id)).fetchone()
        if r or not (fetch and self.online):
            return dict(r) if r else None
        card = self.client.by_tcgplayer(product_id)
        if not card:
            return None
        upsert_cards(self.conn, [card])
        return self.get(card["id"])

    def by_set_name(self, set_name_or_code: str, name: str, fetch: bool = True) -> list[dict]:
        cands = self.prints_by_name(name, fetch=fetch)
        s = set_name_or_code.strip().lower()
        return [c for c in cands if c["set_code"] == s or (c["set_name"] or "").lower() == s]

    def all_names(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT name FROM printings")]

    def fuzzy_name(self, text: str, limit: int = 5) -> list[tuple[str, float]]:
        """Best local name matches for noisy OCR text. Falls back to Scryfall fuzzy if online."""
        from rapidfuzz import fuzz, process
        names = self.all_names()
        hits = process.extract(text, names, scorer=fuzz.WRatio, limit=limit) if names else []
        out = [(n, float(s)) for n, s, _ in hits]
        if (not out or out[0][1] < 85) and self.online and len(text) >= 3:
            try:
                card = self.client.named(text)
                if card:
                    out.insert(0, (card["name"], 90.0))
            except httpx.HTTPError:
                pass
        return out

    # ---- bulk ------------------------------------------------------------------------------
    def sync_bulk(self, kind: str = "default_cards", progress=None) -> int:
        import ijson
        url = self.client.bulk_url(kind)
        n = 0
        with self.client.http.stream("GET", url) as resp:
            resp.raise_for_status()
            batch = []
            for card in ijson.items(_IterStream(resp.iter_bytes()), "item", use_float=True):
                batch.append(card)
                if len(batch) >= 2000:
                    n += upsert_cards(self.conn, batch)
                    batch.clear()
                    if progress:
                        progress(n)
            n += upsert_cards(self.conn, batch)
        return n


class _IterStream:
    """Adapts an iterator of bytes to a file-like object for ijson."""

    def __init__(self, it):
        self.it, self.buf = it, b""

    def read(self, n=-1):
        while n < 0 or len(self.buf) < n:
            try:
                self.buf += next(self.it)
            except StopIteration:
                break
        if n < 0:
            out, self.buf = self.buf, b""
        else:
            out, self.buf = self.buf[:n], self.buf[n:]
        return out
