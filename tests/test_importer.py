from mtgstack import importer, decks
from mtgstack.importer import parse
from mtgstack.query import query
from conftest import pid

TCG_CSV = """Quantity,Name,Simple Name,Set,Card Number,Set Code,Printing,Condition,Language,Rarity,Product ID,SKU
2,Sol Ring,Sol Ring,Commander Masters,410,CMM,Foil,Near Mint Foil,English,U,500100,111
1,Lightning Bolt,Lightning Bolt,Strixhaven Mystical Archive,42,STA,Foil Etched,Lightly Played,English,R,234001,222
1,Island (0276),Island,Foundations,276,FDN,Normal,Near Mint,English,L,,333
3,Counterspell,Counterspell,Dominaria Remastered,45,DMR,Normal,Near Mint,English,C,,444
1,Atraxa Praetors' Voice (Showcase),,Double Masters 2022,,,Normal,Near Mint,English,M,,555
"""

MANABOX = """Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,Purchase price currency
Sol Ring,c21,Commander 2021,263,normal,uncommon,2,1,,0.99,false,false,near_mint,en,USD
Counterspell,mh2,Modern Horizons 2,267,etched,uncommon,1,2,,,false,false,lightly_played,en,USD
"""

MOXFIELD = '''"Count","Tradelist Count","Name","Edition","Condition","Language","Foil","Tags","Last Modified","Collector Number","Alter","Proxy","Purchase Price"
"1","0","Llanowar Elves","fdn","Near Mint","English","foil","binder","2024-01-01","227","False","False",""
'''

TEXT = """Deck
4 Lightning Bolt [2XM]
1x Sol Ring (CMM) 703 *F*
2 Counterspell [Dominaria Remastered]
Arcane Signet
1 Island - Foil
// a comment
1 Totally Fake Card
SB: 1 Swords to Plowshares (STA) 10
"""

def test_detects_formats():
    assert parse(TCG_CSV)[0] == "csv" and parse(MANABOX)[0] == "csv" and parse(MOXFIELD)[0] == "csv"
    assert parse(TEXT)[0] == "text"
    assert parse("Sol Ring\nCounterspell", "list.txt")[0] == "text"

def test_text_lines():
    _, rows, errs = parse(TEXT)
    got = [(r.qty, r.name, r.set_code, r.set_name, r.number, r.finish) for r in rows]
    assert got[0] == (4, "Lightning Bolt", "2xm", None, None, None)
    assert got[1] == (1, "Sol Ring", "cmm", None, "703", "foil")
    assert got[2] == (2, "Counterspell", None, "Dominaria Remastered", None, None)
    assert got[3] == (1, "Arcane Signet", None, None, None, None)
    assert got[4] == (1, "Island", None, None, None, "foil")
    assert got[6][1:4] == ("Swords to Plowshares", "sta", None) and not errs

def test_tcgplayer_csv_resolves(conn, cat):
    p = importer.preview(conn, cat, TCG_CSV, online=False)
    rows = {r["raw"].split(",")[1]: r for r in p["rows"]}
    sol = rows["Sol Ring"]
    assert sol["status"] == "matched" and sol["how"] == "TCGplayer product ID" and sol["match"]["finish"] == "foil"
    assert sol["condition"] == "NM" and sol["qty"] == 2
    bolt = rows["Lightning Bolt"]
    assert bolt["match"]["finish"] == "etched" and bolt["condition"] == "LP"
    assert rows["Island (0276)"]["match"]["number"] == "276" and rows["Island (0276)"]["name"] == "Island"
    assert rows["Counterspell"]["how"] == "set + collector number"
    atraxa = rows["Atraxa Praetors' Voice (Showcase)"]
    assert atraxa["status"] in ("matched", "guessed") or atraxa["status"] == "unmatched"
    assert p["counts"]["matched"] >= 4

def test_manabox_and_moxfield(conn, cat):
    p = importer.preview(conn, cat, MANABOX, online=False)
    assert [(r["match"]["set_code"], r["match"]["finish"], r["condition"]) for r in p["rows"]] == \
        [("c21", "nonfoil", "NM"), ("mh2", "etched", "LP")]
    p = importer.preview(conn, cat, MOXFIELD, online=False)
    r = p["rows"][0]
    assert r["match"]["set_code"] == "fdn" and r["match"]["finish"] == "foil" and r["tags"] == ["binder"]

def test_text_resolution_statuses(conn, cat):
    p = importer.preview(conn, cat, TEXT, online=False)
    by = {r["name"]: r for r in p["rows"]}
    assert by["Lightning Bolt"]["status"] == "matched" and by["Lightning Bolt"]["match"]["set_code"] == "2xm"
    assert by["Sol Ring"]["match"]["number"] == "703" and by["Sol Ring"]["match"]["finish"] == "foil"
    assert by["Counterspell"]["match"]["set_code"] == "dmr"          # set given by NAME
    assert by["Arcane Signet"]["status"] == "guessed"                # name only
    assert by["Totally Fake Card"]["status"] == "unmatched"
    assert p["counts"] == {"matched": 4, "guessed": 2, "unmatched": 1}   # Arcane Signet + Island: no set given

def test_guessed_rows_offer_alternatives(conn, cat):
    p = importer.preview(conn, cat, "Sol Ring", online=False)
    r = p["rows"][0]
    assert r["status"] == "guessed" and len(r["alternatives"]) == 4
    assert {a["set_code"] for a in r["alternatives"]} == {"c21", "cmm", "fdc"}

def test_commit_to_collection_with_tags_and_auto_allocation(conn, cat):
    decks.import_decklist(conn, cat, "WIP", "1 Sol Ring", status="building", slug="wip")
    p = importer.preview(conn, cat, TCG_CSV, online=False)
    res = importer.commit(conn, cat, p["rows"], {"type": "collection", "tags": ["from-tcg"]})
    assert res["cards"] == sum(r["qty"] for r in p["rows"] if r["printing_id"])
    assert res["allocations"][0]["deck"] == "wip"                     # the in-progress deck took a Sol Ring
    assert sum(l["qty"] for l in query(conn, "tag:from-tcg")) == res["cards"]
    assert decks.reconcile(conn, "wip")["totals"]["progress"] == "1/1"

def test_scanned_batch_becomes_an_owned_deck(conn, cat):
    """The Scan page's "scan a deck in" sends bare rows: what was scanned, with its printing and finish."""
    sol = pid(cat, "Sol Ring", "cmm")
    rows = [{"printing_id": sol, "qty": 2, "finish": "foil", "condition": "LP", "language": "en", "tags": ["box:3"],
             "include": True, "status": "matched", "set_code": "cmm", "number": "410"}]
    res = importer.commit(conn, cat, rows, {"type": "deck", "name": "Scanned", "format": "commander", "status": "built", "owned": True})
    rep = decks.reconcile(conn, res["deck"])
    assert (res["cards"], rep["totals"]["wanted"], rep["totals"]["in_deck"]) == (2, 2, 2)
    lot = query(conn, "deck:scanned sol")[0]
    assert (lot["finish"], lot["condition"], lot["qty"]) == ("foil", "LP", 2) and "box:3" in lot["tags"]
    assert rep["entries"][0]["card_printing_id"] == sol                # the list remembers the printing scanned


def test_commit_as_owned_deck(conn, cat):
    p = importer.preview(conn, cat, TEXT, online=False)
    res = importer.commit(conn, cat, p["rows"], {"type": "deck", "name": "Burn", "format": "modern", "status": "built", "owned": True})
    rep = decks.reconcile(conn, res["deck"])
    assert rep["totals"]["wanted"] == res["cards"] == rep["totals"]["in_deck"]
    assert query(conn, "deck:burn sol")[0]["finish"] == "foil"
