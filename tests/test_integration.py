import datetime as dt
from mtgstack import collection as col, decks, tcgplayer, prices
from mtgstack.query import query
from conftest import pid

DECK = """About
Name Atraxa Superfriends

Commander
1 Atraxa, Praetors' Voice (C16) 28

Deck
1 Sol Ring
1 Arcane Signet
2 Counterspell
1 Lightning Bolt (STA) 42 *E*
10 Island

Sideboard
1 Swords to Plowshares
"""

def own(conn, cat):
    col.add(conn, pid(cat, "Atraxa, Praetors' Voice", "c16"), 1)
    col.add(conn, pid(cat, "Sol Ring", "c21"), 2)
    col.add(conn, pid(cat, "Sol Ring", "cmm"), 1, tags="deck:other")
    col.add(conn, pid(cat, "Counterspell", "mh2"), 1, finish="foil")
    col.add(conn, pid(cat, "Counterspell", "dmr"), 3)
    col.add(conn, pid(cat, "Lightning Bolt", "sta"), 1, finish="foil")   # wrong finish for the deck
    col.add(conn, pid(cat, "Island", "fdn"), 4)

def test_parse_formats():
    e = decks.parse_decklist(DECK)
    assert e[0] == {"qty": 1, "name": "Atraxa, Praetors' Voice", "set": "c16", "cn": "28", "finish": None, "board": "commander"}
    bolt = next(x for x in e if x["name"] == "Lightning Bolt")
    assert bolt["finish"] == "etched" and bolt["set"] == "sta"
    assert e[-1]["board"] == "side"
    mtgo = decks.parse_decklist("4 Lightning Bolt\n2x Counterspell\n\n3 Swords to Plowshares\nSB: 1 Island")
    assert [x["board"] for x in mtgo] == ["main", "main", "side", "side"]

def test_import_reconcile_pull_release_export(conn, cat):
    own(conn, cat)
    r = decks.import_decklist(conn, cat, "Atraxa Superfriends", DECK, fmt="commander")
    assert r["slug"] == "atraxa-superfriends" and not r["unresolved"]
    rep = {e["name"]: e for e in decks.reconcile(conn, r["slug"])["entries"]}
    assert rep["Sol Ring"]["free_available"] == 2 and rep["Sol Ring"]["in_other_decks"] == ["deck:other"]
    assert rep["Lightning Bolt"]["missing"] == 1          # only own a foil, deck wants etched
    assert rep["Arcane Signet"]["missing"] == 1 and rep["Arcane Signet"]["cheapest"]["price"] == 0.60
    assert rep["Island"]["missing"] == 6

    out = decks.pull(conn, r["slug"], prefer="cheapest")
    cs = [p for p in out["pulled"] if p["name"] == "Counterspell"]
    assert [p["set"] for p in cs] == ["dmr"] and cs[0]["qty"] == 2     # cheap copies, foil stays free
    assert sum(l["qty"] for l in query(conn, "deck:atraxa-superfriends")) == 1 + 1 + 2 + 4
    assert query(conn, "tag:deck:atraxa-superfriends:commander")[0]["name"].startswith("Atraxa")
    assert out["after"]["missing"] == 1 + 1 + 6 + 1

    arena = decks.export(conn, r["slug"], "arena")
    assert arena.startswith("Commander\n1 Atraxa, Praetors' Voice") and "\nSideboard\n" in arena
    assert "1 Lightning Bolt (STA) 42" in arena       # has arena_id in fixtures
    assert "1 Sol Ring\n" in arena                     # no Arena printing -> name only
    assert "10 Island (FDN) 275" in arena              # deck didn't pin a printing -> newest Arena printing
    assert "*E*" in decks.export(conn, r["slug"], "moxfield")
    owned = decks.export(conn, r["slug"], "mtgo", source="owned")
    assert "4 Island" in owned

    assert decks.release(conn, r["slug"]) >= 4
    assert query(conn, "deck:atraxa-superfriends") == []
    assert sum(l["qty"] for l in query(conn, '"Counterspell"')) == 4   # lots merged back, nothing lost

def test_deck_from_tags(conn, cat):
    own(conn, cat)
    decks.deck_from_tags(conn, "other")
    assert decks.reconcile(conn, "other")["totals"] == {"wanted": 1, "in_deck": 1, "free": 0, "missing": 0, "missing_cost": 0.0, "progress": "1/1"}

TCG_CSV = """Quantity,Name,Simple Name,Set,Card Number,Set Code,Printing,Condition,Language,Rarity,Product ID,SKU
2,Sol Ring,Sol Ring,Commander Masters,410,CMM,Foil,Near Mint Foil,English,U,500100,1
1,Lightning Bolt,Lightning Bolt,Strixhaven Mystical Archive,42,STA,Normal,Lightly Played,English,R,,2
1,Atraxa Praetors Voice (Showcase),,Double Masters 2022,,,Normal,Near Mint,English,M,,3
1,Island,,Unknown Set,999,ZZZ,Normal,Near Mint,English,L,,4
1,Not A Real Card,,,,,Normal,Near Mint,English,C,,5
"""

def test_tcgplayer_roundtrip(conn, cat):
    res = tcgplayer.import_csv(conn, cat, TCG_CSV, tags="from-tcg")
    names = [(a["name"], a["finish"], a["matched_by"]) for a in res["added"]]
    assert names[0] == ("Sol Ring", "foil", "product id")
    assert names[1][2] == "set+number" and res["added"][1]["condition"] == "LP"
    assert res["failed"][-1]["name"] == "Not A Real Card"
    out = tcgplayer.export_csv(conn, query(conn, "tag:from-tcg"))
    assert "2,Sol Ring,Commander Masters,CMM,410,Foil,Near Mint,English,500100" in out
    assert tcgplayer.mass_entry(query(conn, "set:cmm")) == "2 Sol Ring [CMM]"

def test_prices_cardkingdom_history_value(conn, cat):
    own(conn, cat)
    sol = pid(cat, "Sol Ring", "c21")
    n = prices.ingest_cardkingdom(conn, {"data": [
        {"scryfall_id": sol, "is_foil": "false", "variation": "", "price_retail": "2.49", "price_buy": "1.10"},
        {"scryfall_id": pid(cat, "Lightning Bolt", "sta"), "is_foil": "true", "variation": "Foil Etched", "price_retail": "9.99", "price_buy": "4.00"},
        {"scryfall_id": "not-in-catalog", "is_foil": "false", "price_retail": "1"}]})
    assert n == 4
    q = {(x["source"], x["kind"], x["finish"]): x["price"] for x in prices.quotes(conn, sol)}
    assert q[("cardkingdom", "buylist", "nonfoil")] == 1.10 and q[("tcgplayer", "market", "nonfoil")] == 1.50
    # fake an older tcgplayer price to exercise history/movers
    old = (dt.date.today() - dt.timedelta(days=5)).isoformat()
    conn.execute("INSERT INTO prices VALUES (?,?,?,?,?,?,?)", (sol, "nonfoil", "tcgplayer", "market", 1.00, "USD", old))
    assert [h["price"] for h in prices.history(conn, sol)] == [1.00, 1.50]
    m = prices.movers(conn, days=7)
    assert m[0]["name"] == "Sol Ring" and m[0]["delta_total"] == 1.0
    v = prices.collection_value(conn)
    assert v["cardkingdom_buylist"] == 2.20 and v["tcgplayer_market"] > 0
