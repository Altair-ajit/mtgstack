import json
import os
from pathlib import Path
import pytest
from mtgstack import db
from mtgstack.catalog import Catalog, upsert_cards

os.environ["MTGSTACK_MODEL"] = "/nonexistent-mtgstack-model"   # tests never pick up an installed model

FIX = Path(__file__).parent / "fixtures" / "cards.json"

@pytest.fixture
def conn():
    c = db.connect(":memory:")
    upsert_cards(c, json.loads(FIX.read_text()))
    return c

@pytest.fixture
def cat(conn):
    return Catalog(conn, online=False)

def pid(cat, name, set_code, lang="en"):
    return next(p["id"] for p in cat.prints_by_name(name) if p["set_code"] == set_code and p["lang"] == lang)
