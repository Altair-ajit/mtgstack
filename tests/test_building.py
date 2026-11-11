"""In-progress ('building') decks: new cards are auto-tagged in; overrides stick; entries are queryable."""
import pytest
from mtgstack import collection as col, decks, tcgplayer
from mtgstack.query import query, query_entries, QueryError
from conftest import pid

LIST = """Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
1 Arcane Signet
2 Counterspell
1 Lightning Bolt (STA) 42 *E*
3 Island

Maybeboard
1 Swords to Plowshares
"""

@pytest.fixture
def deck(conn, cat):
    col.add(conn, pid(cat, "Counterspell", "dmr"), 1, tags="binder")        # owned BEFORE the deck exists
    return decks.import_decklist(conn, cat, "Atraxa WIP", LIST, "commander", status="building")["slug"]

def test_progress_split_have_move_buy(conn, cat, deck):
    t = decks.reconcile(conn, deck)["totals"]
    assert t["wanted"] == 9 and t["progress"] == "0/9" and t["free"] == 1 and t["missing"] == 8
    # existing copies are NOT auto-grabbed: they show as "to move"
    cs = next(e for e in decks.reconcile(conn, deck)["entries"] if e["name"] == "Counterspell")
    assert (cs["have"], cs["to_move"], cs["to_buy"], cs["status"]) == (0, 1, 1, "buy")
    assert cs["where"][0]["tags"] == "binder"

def test_new_card_is_auto_tagged(conn, cat, deck):
    got = []
    lot = col.add(conn, pid(cat, "Sol Ring", "c21"), 1, tags="box:new", allocations=got)
    assert got[0]["deck"] == deck and got[0]["board"] == "main"
    assert col.get_lot(conn, lot)["tags"] == f"box:new deck:{deck}"
    assert decks.reconcile(conn, deck)["totals"]["progress"] == "1/9"

def test_partial_need_splits_and_extra_stays_free(conn, cat, deck):
    got = []
    col.add(conn, pid(cat, "Island", "fdn"), 5, allocations=got)
    assert got[0]["qty"] == 3
    assert sum(l["qty"] for l in query(conn, "t:island is:free")) == 2
    assert sum(l["qty"] for l in query(conn, f"t:island deck:{deck}")) == 3

def test_commander_board_and_pinned_finish(conn, cat, deck):
    got = []
    col.add(conn, pid(cat, "Atraxa, Praetors' Voice", "2x2"), 1, finish="foil", allocations=got)
    assert got[0]["tag"] == f"deck:{deck}:commander"
    nope = []
    col.add(conn, pid(cat, "Lightning Bolt", "sta"), 1, finish="foil", allocations=nope)   # deck wants etched
    assert nope == []
    col.add(conn, pid(cat, "Lightning Bolt", "sta"), 1, finish="etched", allocations=nope)
    assert nope[0]["name"] == "Lightning Bolt"

def test_maybeboard_never_auto(conn, cat, deck):
    got = []
    col.add(conn, pid(cat, "Swords to Plowshares", "sta"), 1, allocations=got)
    assert got == []

def test_override_back_to_collection_sticks(conn, cat, deck):
    lot = col.add(conn, pid(cat, "Sol Ring", "c21"), 1)
    back = decks.send(conn, lot)                                      # back to collection
    assert "deck:" not in (col.get_lot(conn, back)["tags"] or "")
    e = next(e for e in decks.reconcile(conn, deck)["entries"] if e["name"] == "Sol Ring")
    assert e["status"] == "move"                                      # not re-grabbed; shown as movable
    got = []
    col.add(conn, pid(cat, "Sol Ring", "cmm"), 1, allocations=got)    # the NEXT new copy fills it
    assert got and got[0]["deck"] == deck

def test_send_to_another_deck(conn, cat, deck):
    decks.create_deck(conn, "Other", slug="other")
    lot = col.add(conn, pid(cat, "Sol Ring", "c21"), 1)
    moved = decks.send(conn, lot, deck="other", board="side")
    assert col.get_lot(conn, moved)["tags"] == "deck:other:side"

def test_priority_between_building_decks(conn, cat, deck):
    decks.import_decklist(conn, cat, "Urgent", "1 Sol Ring", status="building", slug="urgent")
    decks.update_deck(conn, "urgent", priority=1)
    got = []
    col.add(conn, pid(cat, "Sol Ring", "c21"), 2, allocations=got)
    assert [g["deck"] for g in got] == ["urgent", deck]

def test_built_decks_and_explicit_tags_are_left_alone(conn, cat, deck):
    decks.update_deck(conn, deck, status="built")
    got = []
    col.add(conn, pid(cat, "Sol Ring", "c21"), 1, allocations=got)
    assert got == []
    decks.update_deck(conn, deck, status="building")
    col.add(conn, pid(cat, "Arcane Signet", "eld"), 1, tags="deck:somewhere-else", allocations=got)
    assert got == []                                                   # caller placed it explicitly

def test_tcgplayer_import_auto_allocates(conn, cat, deck):
    csv = "Quantity,Name,Set Code,Card Number,Printing,Condition\n2,Counterspell,MH2,267,Normal,Near Mint\n"
    tcgplayer.import_csv(conn, cat, csv)
    assert decks.reconcile(conn, deck)["totals"]["in_deck"] == 2

def test_entry_queries_and_sorts(conn, cat, deck):
    names = lambda q: [e["name"] for e in query_entries(conn, f"deck:{deck} " + q)]
    assert names("t:instant sort:name") == ["Counterspell", "Lightning Bolt", "Swords to Plowshares"]
    assert names("status:buy cmc<=1 -t:land sort:-price -board:maybe") == ["Lightning Bolt", "Sol Ring"]
    assert names("sort:type")[0].startswith("Atraxa") and names("sort:type")[-1] == "Island"
    assert names("sort:cmc")[0] == "Island"
    assert names("status:move") == ["Counterspell"] or names("status:move") == []
    assert names("board:commander") == ["Atraxa, Praetors' Voice"]
    assert {e["deck_slug"] for e in query_entries(conn, "is:building status:need")} == {deck}
    rep = decks.reconcile(conn, deck, q="t:instant")
    assert rep["shown"]["wanted"] == 3 and rep["totals"]["wanted"] == 9   # maybeboard excluded from counts
    with pytest.raises(QueryError):
        query_entries(conn, "tag:binder")
    with pytest.raises(QueryError):
        query(conn, "status:buy")

def test_list_decks_progress(conn, cat, deck):
    d = next(x for x in decks.list_decks(conn) if x["slug"] == deck)
    assert d["status"] == "building" and d["progress"] == "0/9" and d["free"] == 1 and d["missing"] == 8
