"""Bulk catalog sync against a mocked Scryfall (same HTTP/streaming code path as the real thing)."""
import json
import httpx
from mtgstack import db, collection as col
from mtgstack.catalog import Catalog, Scryfall
from conftest import FIX

CARDS = json.loads(FIX.read_text())
BULK = json.dumps(CARDS).encode()


import gzip

import pytest

JSONL_GZ = gzip.compress(b"\n".join(json.dumps(c).encode() for c in CARDS) + b"\n")

# Real cards with layouts the sample fixture lacks (trimmed from Scryfall, Nov 2026): a reversible card has no
# top-level oracle_id / type_line / image_uris; flip & split faces have no images; tokens and emblems.
EDGE = [
    {"id": "dc791c6a-331a-44f6-ab40-7a1ff9673170", "name": "Conjurer's Closet // Conjurer's Closet", "layout": "reversible_card",
     "set": "sld", "set_name": "Secret Lair Drop", "collector_number": "2747", "rarity": "rare", "lang": "en",
     "finishes": ["nonfoil", "foil"], "border_color": "borderless", "frame": "2015", "frame_effects": ["inverted"],
     "promo_types": ["universesbeyond"], "color_identity": [], "tcgplayer_id": 730220,
     "card_faces": [{"oracle_id": "cd1eda60-53e4-44d0-9b2c-7a57395e291f", "name": "Conjurer's Closet",
                     "flavor_name": "The Transporter", "mana_cost": "{5}", "cmc": 5, "type_line": "Artifact",
                     "oracle_text": "At the beginning of your end step, you may exile target creature you control.",
                     "colors": [], "image_uris": {"small": "https://cards.scryfall.io/small/front/d/c/x.jpg"}}] * 2,
     "legalities": {"modern": "legal"}, "prices": {"usd": None, "usd_foil": None, "usd_etched": None, "eur": None}},
    {"id": "6929750f-cf08-4e3e-83b0-076e1f6fa8e0", "oracle_id": "0664dcc8-fa68-4802-936f-ecd2979ebf3c",
     "name": "Wicked // Cursed", "layout": "flip", "set": "plst", "collector_number": "TWOE-17", "mana_cost": "",
     "cmc": 0, "type_line": "Token Enchantment — Aura Role // Token Enchantment — Aura Role", "colors": [],
     "image_uris": {"small": "https://cards.scryfall.io/small/front/6/9/x.jpg"},
     "card_faces": [{"name": "Wicked", "type_line": "Token Enchantment — Aura Role", "oracle_text": "Enchant creature"},
                    {"name": "Cursed", "type_line": "Token Enchantment — Aura Role", "oracle_text": "Enchant creature"}],
     "finishes": ["nonfoil"], "prices": {"usd": None}},
    {"id": "5fdc53ba-e023-4ee4-bb14-fd8c52955ee9", "oracle_id": "1c944fa3-ff6c-4638-a8cd-77d3ca68e599",
     "name": "Chandra, Torch of Defiance Emblem", "layout": "emblem", "set": "tfra", "collector_number": "18",
     "type_line": "Emblem — Chandra", "cmc": 0, "finishes": ["nonfoil", "foil"], "prices": {"usd": None, "usd_foil": None}},
    {"id": "f1083637-7c43-44e0-8029-ad56cca8cc19", "oracle_id": "577725ae-290f-4081-8352-3317521a096f",
     "name": "Vanille, Cheerful l'Cie", "layout": "meld", "set": "fin", "collector_number": "537",
     "finishes": ["foil"], "prices": {"usd": None, "usd_foil": "4.12", "eur_foil": "3.59"}},
]


def mock_client(fmt="jsonl.gz", cards=None):
    cards = CARDS if cards is None else cards
    body = {"json": json.dumps(cards).encode(),
            "jsonl.gz": gzip.compress(b"\n".join(json.dumps(c).encode() for c in cards) + b"\n")}[fmt]

    def handler(req: httpx.Request):
        if req.url.path == "/bulk-data":   # Nov 2026 format: jsonl_download_uri; before: download_uri
            item = ({"jsonl_download_uri": "https://data.example/default.jsonl.gz", "compressed_size": len(body)}
                    if fmt == "jsonl.gz" else {"download_uri": "https://data.example/default.json"})
            return httpx.Response(200, json={"data": [{"type": "default_cards", **item}]})
        if req.url.path == "/sets":
            return httpx.Response(200, json={"data": [{"code": "CMM", "name": "Commander Masters", "set_type": "masters",
                                                       "released_at": "2023-08-04", "icon_svg_uri": "https://x/cmm.svg", "card_count": 451}]})
        if req.url.host == "data.example":
            return httpx.Response(200, content=body, headers={"content-length": str(len(body)),
                                                              "content-type": "application/gzip"})
        return httpx.Response(404)
    return Scryfall(httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("fmt", ["jsonl.gz", "json"])
def test_bulk_formats(fmt):
    conn = db.connect(":memory:")
    assert Catalog(conn, mock_client(fmt, CARDS + EDGE)).sync_bulk() == len(CARDS) + len(EDGE)
    rev = conn.execute("SELECT oracle_id, type_line, image_small FROM printings WHERE set_code='sld' "
                       "AND collector_number='2747'").fetchone()
    assert rev[0] == "cd1eda60-53e4-44d0-9b2c-7a57395e291f" and rev[1] == "Artifact" and rev[2]


def test_bulk_sync_loads_catalog_prices_and_sets():
    conn = db.connect(":memory:")
    seen = []
    n = Catalog(conn, mock_client()).sync_bulk(progress=lambda d, t, c: seen.append((d, t, c)))
    assert n == len(CARDS) and seen[-1] == (len(JSONL_GZ), len(JSONL_GZ), n)
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
