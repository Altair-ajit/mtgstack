from mtgstack import collection as col
from mtgstack.query import query, summarize, QueryError
from conftest import pid
import pytest

def seed(conn, cat):
    a = col.add(conn, pid(cat, "Sol Ring", "c21"), 3, tags="binder")
    b = col.add(conn, pid(cat, "Sol Ring", "cmm"), 1, finish="foil", tags=["binder"])
    c = col.add(conn, pid(cat, "Lightning Bolt", "sta"), 2, finish="etched", condition="lightly played")
    d = col.add(conn, pid(cat, "Atraxa, Praetors' Voice", "c16"), 1, tags="deck:atraxa:commander")
    e = col.add(conn, pid(cat, "Counterspell", "mh2"), 4, tags="binder")
    return a, b, c, d, e

def test_add_merges_identical_lots(conn, cat):
    a = col.add(conn, pid(cat, "Sol Ring", "c21"), 2, tags="binder")
    b = col.add(conn, pid(cat, "Sol Ring", "c21"), 1, tags="binder")
    assert a == b and col.get_lot(conn, a)["qty"] == 3
    c = col.add(conn, pid(cat, "Sol Ring", "c21"), 1, tags="trade")
    assert c != a

def test_move_subset_splits_and_remerges(conn, cat):
    a, *_ = seed(conn, cat)
    moved = col.move(conn, a, 1, add_tags="deck:atraxa", remove_tags="binder")
    assert col.get_lot(conn, a)["qty"] == 2
    assert col.get_lot(conn, moved)["tags"] == "deck:atraxa"
    back = col.move(conn, moved, add_tags="binder", remove_tags="deck:*")
    assert back == a and col.get_lot(conn, a)["qty"] == 3   # merged back

def test_move_too_many_fails(conn, cat):
    a, *_ = seed(conn, cat)
    with pytest.raises(ValueError):
        col.move(conn, a, 5, add_tags="x")

def test_stacks_group_printings(conn, cat):
    seed(conn, cat)
    st = {s["name"]: s for s in col.stacks(conn, query(conn, ""))}
    sol = st["Sol Ring"]
    assert sol["qty"] == 4 and len(sol["printings"]) == 2
    assert sol["value"] == round(3 * 1.50 + 6.00, 2)

def test_split_dupes_keeps_best_and_moves_rest(conn, cat):
    seed(conn, cat)
    plan = col.split_dupes(conn, query(conn, "tag:binder"), keep=1, add_tags="trade", remove_tags="binder")
    by_name = {p["name"]: p for p in plan}
    assert by_name["Counterspell"]["move_qty"] == 3
    assert sum(l["qty"] for l in query(conn, 'tag:trade "sol ring"')) == 3
    assert sum(l["qty"] for l in query(conn, "tag:binder")) == 2  # one sol ring + one counterspell

def test_query_language(conn, cat):
    seed(conn, cat)
    names = lambda q: sorted({l["name"] for l in query(conn, q)})
    assert names("t:instant") == ["Counterspell", "Lightning Bolt"]
    assert names("c:r") == ["Lightning Bolt"]
    assert names("is:etched") == ["Lightning Bolt"]
    assert names("cond<=nm -tag:binder") == ["Atraxa, Praetors' Voice"]
    assert names("deck:atraxa") == ["Atraxa, Praetors' Voice"]
    assert names("is:free is:dupe") == ["Counterspell", "Lightning Bolt", "Sol Ring"]
    assert names("(set:c16 or set:mh2) price>=1") == ["Atraxa, Praetors' Voice", "Counterspell"]
    assert names("id:wubg t:legendary") == ["Atraxa, Praetors' Voice"]
    assert names("tag:none") == ["Lightning Bolt"]
    assert names("tag:deck:*") == ["Atraxa, Praetors' Voice"]
    assert names("f:modern") == ["Lightning Bolt"]
    assert names('"sol ring" value>5') == ["Sol Ring"]
    assert query(conn, "sort:price")[0]["name"] == "Atraxa, Praetors' Voice"
    s = summarize(query(conn, ""))
    assert s["cards"] == 11 and s["unique"] == 4
    with pytest.raises(QueryError):
        query(conn, "(t:instant")

def test_retag_and_events(conn, cat):
    seed(conn, cat)
    assert col.retag(conn, "binder", "box:binder-1") == 3
    assert {l["tags"] for l in query(conn, "tag:box:binder-1")} == {"box:binder-1"}
    assert col.undo_hint(conn, 1)[0]["action"] == "retag"

def test_split_dupes_never_moves_deck_copies(conn, cat):
    sol = pid(cat, "Sol Ring", "c21")
    col.add(conn, sol, 2, tags=["binder", "deck:edh"])
    col.add(conn, sol, 3, tags="binder")
    plan = col.split_dupes(conn, query(conn, "tag:binder"), keep=1, add_tags="trade", remove_tags="binder")
    assert [(p["move_qty"], p["stay_qty"]) for p in plan] == [(3, 0)]   # deck copies already satisfy keep=1
    assert sum(l["qty"] for l in query(conn, "deck:edh")) == 2
