"""Phone build layout: user data and catalog in separate files (catalog attached)."""
import json
from pathlib import Path

from mtgstack import collection as col, db, decks
from mtgstack.catalog import Catalog, upsert_cards

FIX = json.loads((Path(__file__).parent / "fixtures" / "cards.json").read_text())


def test_collection_lists_from_the_user_file_not_the_catalog(tmp_path):
    """In the two-file layout the collection's card details come from a copy in the user file: listing, sorting
    and filtering must not look every owned card up in the (large, slow to reach) catalog file."""
    conn = db.connect(tmp_path / "user.db", catalog=tmp_path / "catalog.db")
    upsert_cards(conn, FIX); conn.commit()
    sol = conn.execute("SELECT id FROM printings WHERE name='Sol Ring' AND set_code='cmm'").fetchone()[0]
    col.add(conn, sol, qty=2, finish="foil")
    assert conn.execute("SELECT name, price FROM lot_view").fetchone()[:] == ("Sol Ring", conn.execute(
        "SELECT price FROM cat.current_prices WHERE printing_id=? AND finish='foil' AND source='tcgplayer'", (sol,)).fetchone()[0])
    plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN SELECT name, price, SUM(qty) FROM lot_view GROUP BY printing_id"))
    assert "own_printings" in plan and "own_prices" in plan and " printings" not in plan and "current_prices" not in plan
    # a newer catalog (new price): the copy follows on refresh, and keeps only what is owned
    conn.execute("UPDATE cat.current_prices SET price = 99.5 WHERE printing_id=? AND finish='foil'", (sol,))
    assert db.sync_owned(conn) == 0 and conn.execute("SELECT price FROM lot_view").fetchone()[0] != 99.5
    assert db.sync_owned(conn, refresh=True) == 1 and conn.execute("SELECT price FROM lot_view").fetchone()[0] == 99.5
    conn.commit(); conn.close()
    # a collection from before the copy existed is filled in when it is opened
    import sqlite3
    raw = sqlite3.connect(tmp_path / "user.db")
    raw.execute("DELETE FROM own_printings"); raw.execute("DELETE FROM own_prices"); raw.commit(); raw.close()
    conn = db.connect(tmp_path / "user.db", catalog=tmp_path / "catalog.db")
    assert conn.execute("SELECT name, qty, price FROM lot_view").fetchone()[:] == ("Sol Ring", 2, 99.5)
    # the one-file layout has no copy and needs none
    one = db.connect(tmp_path / "one.db")
    assert not db.is_split(one) and db.sync_owned(one) == 0


def test_split_layout_keeps_user_data_small_and_queries_work(tmp_path):
    conn = db.connect(tmp_path / "user.db", catalog=tmp_path / "catalog.db")
    cat_tables = {r[0] for r in conn.execute("SELECT name FROM cat.sqlite_master WHERE type='table'")}
    user_tables = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
    assert {"printings", "current_prices", "sets"} <= cat_tables
    assert not ({"printings", "current_prices", "sets"} & user_tables) and {"lots", "decks"} <= user_tables

    upsert_cards(conn, FIX); conn.commit()
    pid = conn.execute("SELECT id FROM printings WHERE name='Sol Ring' LIMIT 1").fetchone()[0]
    col.add(conn, pid, qty=2, tags=["box:a"])
    decks.import_decklist(conn, Catalog(conn, online=False), "Test", "1 Sol Ring\n1 Lightning Bolt", status="building")
    conn.commit()
    assert conn.execute("SELECT SUM(qty) FROM lot_view").fetchone()[0] == 2
    conn.close()

    # reopening (e.g. after the catalog file is replaced by a newer download) keeps the collection
    conn = db.connect(tmp_path / "user.db", catalog=tmp_path / "catalog.db")
    assert conn.execute("SELECT name FROM lot_view").fetchone()[0] == "Sol Ring"
    import sqlite3
    user_only = sqlite3.connect(tmp_path / "user.db")
    assert not user_only.execute("SELECT 1 FROM sqlite_master WHERE name='printings'").fetchone()
