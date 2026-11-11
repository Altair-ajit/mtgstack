"""Phone build layout: user data and catalog in separate files (catalog attached)."""
import json
from pathlib import Path

from mtgstack import collection as col, db, decks
from mtgstack.catalog import Catalog, upsert_cards

FIX = json.loads((Path(__file__).parent / "fixtures" / "cards.json").read_text())


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
