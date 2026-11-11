import json, time
import cv2
from fastapi.testclient import TestClient
from mtgstack.server import create_app
from mtgstack.catalog import upsert_cards
from synth import card_image, photo
from conftest import FIX


def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MTGSTACK_SCANS", str(tmp_path / "scans"))
    app = create_app(str(tmp_path / "t.db"), online=False)
    upsert_cards(app.state.conn, json.loads(FIX.read_text()))
    return TestClient(app), app.state.conn


def jpg(img):
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_lab_flow(tmp_path, monkeypatch):
    c, conn = client(tmp_path, monkeypatch)
    # a good scan and a deliberately unreadable one (via bulk upload)
    r = c.post("/api/scan", files=[("files", ("a.jpg", jpg(photo(card_image(set_code="CMM", number="410"))), "image/jpeg"))]).json()
    assert r["name"] == "Sol Ring" and r["scan_id"]
    blank = card_image(name="Qqqq Zzzz", set_code="", number="0"); blank[860:900, 20:400] = (12, 12, 14)
    up = c.post("/api/lab/upload", files=[("files", ("b.jpg", jpg(photo(blank)), "image/jpeg"))]).json()
    bad_id = up["scans"][0]["scan_id"]
    assert c.get("/api/lab/stats").json()["unreviewed"] == 2

    det = c.get(f"/api/lab/scans/{r['scan_id']}").json()
    assert {"photo_0.jpg", "card.jpg", "title.jpg", "collector.jpg"} <= set(det["files"])
    assert c.get(f"/api/lab/file/{r['scan_id']}/title.jpg").headers["content-type"] == "image/jpeg"
    assert c.get(f"/api/lab/file/{r['scan_id']}/../t.db").status_code == 404

    assert c.post(f"/api/lab/scans/{r['scan_id']}/label", json={"verdict": "correct"}).json()["status"] == "correct"
    counter = conn.execute("SELECT id FROM printings WHERE set_code='mh2'").fetchone()[0]
    lab = c.post(f"/api/lab/scans/{bad_id}/label", json={"verdict": "wrong", "printing_id": counter, "finish": "nonfoil"}).json()
    assert lab["status"] == "wrong" and lab["wrong_fields"] == ["card", "printing", "finish"]

    st = c.get("/api/lab/stats").json()
    assert st["reviewed"] == 2 and st["accuracy"]["card"] == 50.0
    assert st["confusions"][0] == {"guessed": "(no guess)", "actual": "Counterspell", "count": 1}

    c.post("/api/lab/rerun")
    for _ in range(100):
        rr = c.get("/api/lab/rerun").json()
        if rr["state"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert rr["state"] == "done", rr
    assert rr["report"]["n"] == 2 and rr["report"]["now"]["card"] == 50.0 and not rr["report"]["broken"]

    z = c.get("/api/lab/export")
    assert z.headers["content-type"] == "application/zip" and len(z.content) > 1000
    assert c.delete(f"/api/lab/scans/{bad_id}").json()["ok"]
    assert c.get("/api/lab/scans").json()["total"] == 1
