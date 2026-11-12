"""Pieces of the phone (browser) build that run under plain CPython: fuzzy-match shim, catalog builder,
and the in-process request handler."""
import asyncio
import gzip
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from mtgstack import _fuzz

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "cards.json"


def test_fuzz_shim_matches_rapidfuzz():
    from rapidfuzz import fuzz
    from rapidfuzz.distance import Levenshtein
    for a, b in [("Trainmg Cenler", "Training Center"), ("un ay", "Aunt May"), ("Artifact", "Artifact Creature")]:
        assert abs(_fuzz.fuzz.ratio(a, b) - fuzz.ratio(a, b)) < 0.01
        assert abs(_fuzz.Levenshtein.normalized_similarity(a, b) - Levenshtein.normalized_similarity(a, b)) < 1e-9
    names = [f"card number {i}" for i in range(5000)] + ["training center"]
    assert _fuzz.process.extract("trainmg cenler", names, scorer=_fuzz.fuzz.ratio, limit=1)[0][0] == "training center"


def test_build_catalog(tmp_path):
    from mtgstack.catalog_build import build
    meta = build(tmp_path, FIX, cardkingdom=False, log=lambda *_: None)
    assert meta["printings"] > 10 and (tmp_path / "catalog.json").exists()
    raw = tmp_path / "x.sqlite"
    raw.write_bytes(gzip.decompress((tmp_path / meta["file"]).read_bytes()))
    c = sqlite3.connect(raw)
    assert c.execute("SELECT COUNT(*) FROM printings").fetchone()[0] == meta["printings"]
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='lots'").fetchone()   # no user tables


def test_browser_handler_runs_the_api(tmp_path):
    """mtgstack.browser patches threads/http globally, so exercise it in a child process."""
    code = f"""
import asyncio, json
import mtgstack.browser as B
B.DATA = {str(tmp_path)!r}; B.USER_DB = B.DATA + '/user.db'; B.CATALOG_DB = B.DATA + '/catalog.sqlite'
B.start()
from mtgstack.catalog import upsert_cards
c = B.APP.state.conn; upsert_cards(c, json.load(open({str(FIX)!r}))); c.commit()
async def go():
    s, h, b = await B.handle('POST', '/api/import/commit', [['content-type', 'application/json']],
        json.dumps({{'rows': (json.loads((await B.handle('POST', '/api/import/preview', [['content-type','application/json']],
        json.dumps({{'text': '2 Sol Ring'}}).encode()))[2]))['rows'], 'dest': {{'kind': 'collection'}}}}).encode())
    assert s == 200, b
    s, h, b = await B.handle('GET', '/api/summary', [], b'')
    print(json.loads(b)['cards'])
    s, h, b = await B.handle('GET', '/api/nope', [], b'')
    print(s)
asyncio.run(go())
"""
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["2", "404"]


def test_phone_catalog_is_slim(tmp_path):
    from mtgstack.catalog_build import build
    cards = json.loads(FIX.read_text())
    digital = dict(cards[0], id="00000000-0000-0000-0000-00000000d161", games=["arena"], collector_number="A-1")
    src = tmp_path / "cards.json"
    src.write_text(json.dumps(cards + [digital]))
    meta = build(tmp_path / "out", src, cardkingdom=False, log=lambda *_: None)
    raw = tmp_path / "x.sqlite"
    raw.write_bytes(gzip.decompress((tmp_path / "out" / meta["file"]).read_bytes()))
    c = sqlite3.connect(raw)
    assert not c.execute("SELECT 1 FROM printings WHERE id=?", (digital["id"],)).fetchone()       # Arena-only dropped
    assert not c.execute("SELECT 1 FROM current_prices WHERE source='cardmarket'").fetchone()      # USD only
    assert all("not_legal" not in (r[0] or "") for r in c.execute("SELECT legalities FROM printings"))
