"""Bulk catalog sync against a mocked Scryfall (same HTTP/streaming code path as the real thing)."""
import json
import httpx
from mtgstack import db, collection as col
from mtgstack.catalog import Catalog, Scryfall
from conftest import FIX

CARDS = json.loads(FIX.read_text())
BULK = json.dumps(CARDS).encode()


def mock_client():
    def handler(req: httpx.Request):
        if req.url.path == "/bulk-data":
            return httpx.Response(200, json={"data": [{"type": "default_cards", "download_uri": "https://data.example/default.json"}]})
        if req.url.path == "/sets":
            return httpx.Response(200, json={"data": [{"code": "CMM", "name": "Commander Masters", "set_type": "masters",
                                                       "released_at": "2023-08-04", "icon_svg_uri": "https://x/cmm.svg", "card_count": 451}]})
        if req.url.host == "data.example":
            return httpx.Response(200, content=BULK, headers={"content-length": str(len(BULK))})
        return httpx.Response(404)
    return Scryfall(httpx.Client(transport=httpx.MockTransport(handler)))


def test_bulk_sync_loads_catalog_prices_and_sets():
    conn = db.connect(":memory:")
    seen = []
    n = Catalog(conn, mock_client()).sync_bulk(progress=lambda d, t, c: seen.append((d, t, c)))
    assert n == len(CARDS) and seen[-1] == (len(BULK), len(BULK), n)
    assert conn.execute("SELECT COUNT(*) FROM current_prices WHERE source='tcgplayer'").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0        # nothing owned yet -> no history
    assert conn.execute("SELECT name FROM sets WHERE code='cmm'").fetchone()[0] == "Commander Masters"
    assert conn.execute("SELECT value FROM meta WHERE key='catalog_printings'").fetchone()[0] == str(n)
    sol = conn.execute("SELECT id FROM printings WHERE set_code='c21' AND collector_number='263'").fetchone()[0]
    col.add(conn, sol, 1)
    assert conn.execute("SELECT COUNT(*) FROM prices WHERE printing_id=?", (sol,)).fetchone()[0] == 1
    # a second sync (daily refresh) keeps history for the owned card only
    Catalog(conn, mock_client()).sync_bulk()
    assert {r[0] for r in conn.execute("SELECT DISTINCT printing_id FROM prices")} == {sol}
