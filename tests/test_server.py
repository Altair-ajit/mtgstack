import io, json
import cv2
from fastapi.testclient import TestClient
from mtgstack.server import create_app
from mtgstack.catalog import upsert_cards
from synth import card_image, photo
from conftest import FIX

def client(tmp_path):
    app = create_app(str(tmp_path / "t.db"), online=False)
    upsert_cards(app.state.conn, json.loads(FIX.read_text()))
    return TestClient(app)

def test_api_flow(tmp_path):
    c = client(tmp_path)
    assert c.get("/").status_code == 200 and c.get("/static/app.js").status_code == 200
    prints = c.get("/api/catalog/prints", params={"name": "sol ring"}).json()
    cmm = next(p for p in prints if p["set_code"] == "cmm" and p["collector_number"] == "410")
    lot = c.post("/api/lots", json={"printing_id": cmm["id"], "qty": 3, "tags": ["binder"]}).json()
    moved = c.post(f"/api/lots/{lot['lot_id']}/move", json={"qty": 1, "add_tags": ["deck:edh"]}).json()
    assert moved["source"]["qty"] == 2 and moved["lot"]["tags"] == "binder deck:edh"
    st = c.get("/api/query", params={"q": "deck:edh"}).json()
    assert st["summary"]["cards"] == 1 and st["stacks"][0]["name"] == "Sol Ring"
    assert c.get("/api/query", params={"q": "(bad"}).status_code == 400
    plan = c.post("/api/bulk/split-dupes", json={"q": "tag:binder", "keep": 1, "add_tags": ["trade"]}).json()
    assert plan["plan"][0]["move_qty"] == 2 and not plan["applied"]   # deck copy is the one kept
    r = c.post("/api/decks", json={"name": "EDH", "text": "1 Sol Ring\n1 Arcane Signet", "format": "commander"}).json()
    assert c.get(f"/api/decks/{r['slug']}").json()["totals"]["missing"] == 1
    assert c.get(f"/api/decks/{r['slug']}/missing").text == "1 Arcane Signet [ELD]"
    assert "1 Sol Ring" in c.get(f"/api/decks/{r['slug']}/export", params={"fmt": "mtgo"}).text
    assert c.get("/api/tags").json()[0]["tag"] == "binder"
    csv = c.get("/api/export/tcgplayer").text
    assert csv.splitlines()[0].startswith("Quantity,Name")
    up = c.post("/api/import/tcgplayer", files={"file": ("x.csv", csv.encode())}, data={"tags": "reimport", "dry_run": "true"}).json()
    assert up["cards"] == 3 and not up["failed"]

def test_api_scan(tmp_path):
    c = client(tmp_path)
    ok, buf = cv2.imencode(".jpg", photo(card_image(premium=True)))
    r = c.post("/api/scan", files=[("files", ("a.jpg", buf.tobytes(), "image/jpeg"))]).json()
    assert r["name"] == "Sol Ring" and r["finish"] == "foil" and r["set_code"] == "cmm"

def test_api_detect(tmp_path):
    from synth import place
    c = client(tmp_path)
    img, _ = place(card_image(), 250, "white", scale=0.8)
    ok, buf = cv2.imencode(".jpg", cv2.resize(img, (640, 640)))
    r = c.post("/api/detect", files={"file": ("f.jpg", buf.tobytes(), "image/jpeg")}).json()
    assert r["found"] and r["hint"] == "ready"

def test_wsl_network_detection(monkeypatch):
    from mtgstack import cli
    assert cli._wsl_network("Linux version 6.8.0-generic (buildd@lcy02)") is None
    monkeypatch.setattr(cli, "_lan_ips", lambda: ["172.24.10.5"])
    import subprocess
    class R: stdout = "192.168.1.23\r\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    w = cli._wsl_network("Linux version 5.15.153.1-microsoft-standard-WSL2")
    assert w == {"mode": "nat", "wsl_ips": ["172.24.10.5"], "windows_ips": ["192.168.1.23"]}
    help_ = cli._wsl_portproxy_help(w, 8765)
    assert "connectaddress=172.24.10.5" in help_ and "listenport=8765" in help_
    monkeypatch.setattr(cli, "_lan_ips", lambda: ["192.168.1.23"])
    assert cli._wsl_network("5.15.153.1-microsoft-standard-WSL2")["mode"] == "mirrored"

def test_browse_import_and_lot_endpoints(tmp_path):
    c = client(tmp_path)
    csv = "Quantity,Name,Simple Name,Set,Card Number,Set Code,Printing,Condition,Language,Rarity,Product ID,SKU\n" \
          "2,Sol Ring,Sol Ring,Commander Masters,410,CMM,Foil,Near Mint Foil,English,U,500100,1\n" \
          "3,Counterspell,Counterspell,Dominaria Remastered,45,DMR,Normal,Lightly Played,English,C,,2\n"
    pv = c.post("/api/import/preview", json={"text": csv, "filename": "x.csv"}).json()
    assert pv["format"] == "csv" and pv["counts"]["matched"] == 2
    res = c.post("/api/import/commit", json={"rows": pv["rows"], "dest": {"type": "collection", "tags": ["box:1"]}}).json()
    assert res["cards"] == 5
    page = c.get("/api/cards", params={"sort": "price", "size": 1}).json()
    assert page["total_rows"] == 2 and page["pages"] == 2 and page["rows"][0]["name"] == "Sol Ring"
    assert page["rows"][0]["finish"] == "foil" and page["rows"][0]["tags"] == ["box:1"]
    assert {f["value"] for f in page["facets"]["set"]} == {"cmm", "dmr"}
    blue = c.get("/api/cards", params={"color": "U", "condition": "LP"}).json()
    assert [r["name"] for r in blue["rows"]] == ["Counterspell"] and blue["totals"]["cards"] == 3
    lot = blue["rows"][0]["lots"][0]["lot_id"]
    assert c.patch(f"/api/lots/{lot}", json={"qty": 1, "condition": "NM"}).json()["lot"]["condition"] == "NM"
    c.post("/api/decks", json={"name": "Deck A", "text": "1 Counterspell"})
    assert c.post("/api/lots/bulk", json={"lot_ids": [lot], "action": "send", "deck": "deck-a"}).status_code == 200
    assert c.get("/api/cards", params={"deck": "deck-a"}).json()["totals"]["cards"] == 1
    det = c.get(f"/api/card/{blue['rows'][0]['printing_id']}").json()
    assert det["card"]["name"] == "Counterspell" and len(det["printings"]) == 2 and det["decks"][0]["have"] == 1
    assert c.get("/api/catalog/names", params={"text": "count"}).json()[0]["name"] == "Counterspell"
    assert c.get("/api/summary").json()["cards"] == 3
    assert c.get("/api/catalog/status").json()["printings"] == 18
    d = c.get("/api/decks").json()[0]
    assert d["progress"] == "1/1" and "cover_image" in d
    assert c.get("/decks/deck-a").status_code == 200          # SPA route falls through to index.html
    assert c.get("/api/nope").status_code == 404
