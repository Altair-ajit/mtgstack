"""Price tracking per printing *and finish*, with history.

Sources (all public, no keys):
  * tcgplayer  market   - via Scryfall card objects (usd / usd_foil / usd_etched), updated daily by Scryfall
  * cardmarket market   - via Scryfall (eur / eur_foil / eur_etched)
  * cardkingdom retail + buylist - Card Kingdom's public pricelist JSON, which carries scryfall_id per SKU

Every refresh writes one row per (printing, finish, source, kind, date), so `history` and `movers`
come for free. Run `mtgstack prices refresh` daily (cron / Task Scheduler) to build history.
"""
from __future__ import annotations

import datetime as dt
import os

import httpx

from .catalog import HEADERS, Catalog, tracked_printings, upsert_cards
from .db import log_event, rows

CK_PRICELIST = os.environ.get("MTGSTACK_CK_URL", "https://api.cardkingdom.com/api/v2/pricelist")


def refresh_scryfall(conn, catalog: Catalog, scope: str = "owned") -> int:
    """Re-fetch Scryfall card objects for owned printings (or deck-wanted too) and record prices."""
    sql = "SELECT DISTINCT printing_id FROM lots"
    if scope == "all":
        sql += " UNION SELECT DISTINCT printing_id FROM deck_entries WHERE printing_id IS NOT NULL"
    ids = [r[0] for r in conn.execute(sql)]
    cards = catalog.client.collection([{"id": i} for i in ids]) if ids else []
    n = upsert_cards(conn, cards, record_prices=True)
    log_event(conn, "prices_scryfall", printings=n)
    conn.commit()
    return n


def ingest_cardkingdom(conn, payload: dict, only_known: bool = True) -> int:
    """Parse Card Kingdom's pricelist payload: {"meta":..., "data":[{scryfall_id, is_foil, variation,
    price_retail, price_buy, ...}]}. Field access is defensive since the feed isn't formally documented."""
    today = dt.date.today().isoformat()
    known = {r[0] for r in conn.execute("SELECT id FROM printings")} if only_known else None
    tracked = tracked_printings(conn)
    n = 0
    for item in payload.get("data", payload if isinstance(payload, list) else []):
        sid = item.get("scryfall_id")
        if not sid or (known is not None and sid not in known):
            continue
        variation = f"{item.get('variation') or ''} {item.get('edition') or ''}".lower()
        is_foil = str(item.get("is_foil", "false")).lower() in ("true", "1", "yes")
        finish = "etched" if "etched" in variation else ("foil" if is_foil else "nonfoil")
        for key, kind in (("price_retail", "retail"), ("price_buy", "buylist")):
            try:
                price = float(item.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if price > 0:
                rec = (sid, finish, "cardkingdom", kind, price, "USD", today)
                conn.execute("INSERT OR REPLACE INTO current_prices VALUES (?,?,?,?,?,?,?)", rec)
                if sid in tracked:
                    conn.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rec)
                n += 1
    log_event(conn, "prices_cardkingdom", points=n)
    conn.commit()
    return n


def refresh_cardkingdom(conn, url: str = CK_PRICELIST) -> int:
    r = httpx.get(url, headers=HEADERS, timeout=120, follow_redirects=True)
    r.raise_for_status()
    return ingest_cardkingdom(conn, r.json())


def refresh_all(conn, catalog: Catalog) -> dict:
    out = {}
    for name, fn in (("scryfall", lambda: refresh_scryfall(conn, catalog, "all")),
                     ("cardkingdom", lambda: refresh_cardkingdom(conn))):
        try:
            out[name] = fn()
        except Exception as e:  # one source down shouldn't block the other
            out[name] = f"error: {e}"
    return out


def quotes(conn, printing_id: str, finish: str | None = None) -> list[dict]:
    sql = "SELECT * FROM latest_prices WHERE printing_id=?"
    params = [printing_id]
    if finish:
        sql += " AND finish=?"
        params.append(finish)
    return rows(conn.execute(sql + " ORDER BY finish, source, kind", params))


def history(conn, printing_id: str, finish: str = "nonfoil", source: str = "tcgplayer",
            kind: str = "market") -> list[dict]:
    return rows(conn.execute(
        "SELECT as_of, price FROM prices WHERE printing_id=? AND finish=? AND source=? AND kind=? ORDER BY as_of",
        (printing_id, finish, source, kind)))


def movers(conn, days: int = 7, limit: int = 20) -> list[dict]:
    """Owned printings with the largest TCGplayer market change over `days`, weighted by copies owned."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return rows(conn.execute("""
        WITH owned AS (SELECT printing_id, finish, SUM(qty) qty FROM lots GROUP BY printing_id, finish),
        cur AS (SELECT printing_id, finish, price FROM latest_prices WHERE source='tcgplayer' AND kind='market'),
        prev AS (SELECT p.printing_id, p.finish, p.price FROM prices p
                 JOIN (SELECT printing_id, finish, MIN(as_of) d FROM prices
                       WHERE source='tcgplayer' AND kind='market' AND as_of >= ? GROUP BY printing_id, finish) m
                   ON m.printing_id=p.printing_id AND m.finish=p.finish AND m.d=p.as_of
                 WHERE p.source='tcgplayer' AND p.kind='market')
        SELECT pr.name, pr.set_code, pr.collector_number, o.finish, o.qty, prev.price AS was, cur.price AS now_price,
               ROUND(cur.price - prev.price, 2) AS delta, ROUND((cur.price - prev.price) * o.qty, 2) AS delta_total
        FROM owned o JOIN cur USING (printing_id, finish) JOIN prev USING (printing_id, finish)
        JOIN printings pr ON pr.id = o.printing_id
        WHERE cur.price <> prev.price ORDER BY ABS(delta_total) DESC LIMIT ?""", (since, limit)))


def collection_value(conn) -> dict:
    r = conn.execute("""SELECT SUM(l.qty * COALESCE(tp.price,0)) tcg,
                               SUM(l.qty * COALESCE(ckr.price,0)) ck_retail,
                               SUM(l.qty * COALESCE(ckb.price,0)) ck_buylist,
                               SUM(CASE WHEN tp.price IS NULL THEN l.qty ELSE 0 END) unpriced
        FROM lots l
        LEFT JOIN latest_prices tp ON tp.printing_id=l.printing_id AND tp.finish=l.finish AND tp.source='tcgplayer' AND tp.kind='market'
        LEFT JOIN latest_prices ckr ON ckr.printing_id=l.printing_id AND ckr.finish=l.finish AND ckr.source='cardkingdom' AND ckr.kind='retail'
        LEFT JOIN latest_prices ckb ON ckb.printing_id=l.printing_id AND ckb.finish=l.finish AND ckb.source='cardkingdom' AND ckb.kind='buylist'
    """).fetchone()
    return {"tcgplayer_market": round(r["tcg"] or 0, 2), "cardkingdom_retail": round(r["ck_retail"] or 0, 2),
            "cardkingdom_buylist": round(r["ck_buylist"] or 0, 2), "unpriced_cards": r["unpriced"] or 0}
