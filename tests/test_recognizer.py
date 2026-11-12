"""Picture-recognition model: training-data manifest, index format, and how the scanner uses a picture match."""
import uuid

import numpy as np
import pytest
from synth import card_image, photo

from mtgstack.catalog import Catalog, upsert_cards
from mtgstack.scanner import modeldata, recognizer
from mtgstack.scanner.recognizer import Match, match_from_hints
from mtgstack.scanner.resolve import NameIndex, Scanner

U = lambda s: str(uuid.uuid5(uuid.NAMESPACE_URL, "recog/" + s))


# ---- manifest --------------------------------------------------------------------------------------------
def sf(id_="a", **kw):
    return {"object": "card", "id": id_, "name": "X", "set": "TST", "collector_number": "1", "games": ["paper"],
            "image_status": "highres_scan", "illustration_id": "ill-1", "layout": "normal",
            "image_uris": {"normal": f"https://img/{id_}.jpg"}, **kw}


def test_one_row_per_pictured_face():
    assert [r["key"] for r in modeldata.image_rows(sf())] == ["a"]
    row = next(modeldata.image_rows(sf()))
    assert (row["group"], row["set"], row["number"], row["face"]) == ("ill-1", "tst", "1", 0)

    dfc = sf("d", layout="transform", image_uris=None, illustration_id=None, card_faces=[
        {"name": "Front", "illustration_id": "f", "image_uris": {"normal": "u0"}},
        {"name": "Back", "illustration_id": "b", "image_uris": {"normal": "u1"}}])
    rows = list(modeldata.image_rows(dfc))
    assert [(r["key"], r["group"], r["name"]) for r in rows] == [("d", "f", "Front"), ("d.1", "b", "Back")]
    assert modeldata.key_printing("d.1") == "d"

    # split/adventure cards: one picture, faces share it
    split = sf("s", illustration_id=None, card_faces=[{"illustration_id": "left"}, {"illustration_id": "right"}])
    assert [(r["key"], r["group"]) for r in modeldata.image_rows(split)] == [("s", "left")]


def test_rows_skip_what_cannot_be_scanned():
    assert not list(modeldata.image_rows(sf(games=["arena", "mtgo"])))
    assert not list(modeldata.image_rows(sf(image_status="placeholder")))
    art = sf("r", layout="art_series", image_uris=None, card_faces=[
        {"illustration_id": "f", "image_uris": {"normal": "u0"}}, {"illustration_id": "b", "image_uris": {"normal": "u1"}}])
    assert [r["key"] for r in modeldata.image_rows(art)] == ["r"]          # the back is an info panel
    assert next(modeldata.image_rows(sf(illustration_id=None)))["group"] == "id:a"


# ---- index -------------------------------------------------------------------------------------------------
def test_index_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    vecs = rng.normal(size=(5, 16)).astype(np.float32)
    modeldata.write_index(tmp_path, vecs, list("abcde"), [0, 0, 1, 2, 3], "v-test")
    got, meta = modeldata.read_index(tmp_path)
    assert got.dtype == np.int8 and got.shape == (5, 16)
    assert meta["keys"] == list("abcde") and meta["model_version"] == "v-test"
    unit = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    assert np.abs(got.astype(np.float32) @ got.astype(np.float32).T / 127 ** 2 - unit @ unit.T).max() < 0.03
    with pytest.raises(ValueError):
        modeldata.write_index(tmp_path, vecs, ["a"], [0], "v")


def test_hints_group_by_artwork():
    m = match_from_hints([{"printing_id": "p1", "score": 0.9, "group": 7}, {"printing_id": "p2", "score": 0.88, "group": 7},
                          {"printing_id": "q", "score": 0.5, "group": 3}, {"printing_id": "q2", "score": 0.45, "group": 3}], "v9")
    assert m.printings == ["p1", "p2"] and m.others == [("q", 0.5)]
    assert m.margin == pytest.approx(0.4) and m.confident and m.model_version == "v9"
    assert not match_from_hints([{"printing_id": "p", "score": 0.9}, {"printing_id": "q", "score": 0.88}]).confident
    assert not match_from_hints([]).printings


def test_recognizer_end_to_end(tmp_path, monkeypatch):
    """A real ONNX session + index, with a stand-in model whose embedding is just the mean colour."""
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import TensorProto, helper
    g = helper.make_graph(
        [helper.make_node("GlobalAveragePool", ["image"], ["p"]), helper.make_node("Flatten", ["p"], ["embedding"])],
        "g", [helper.make_tensor_value_info("image", TensorProto.FLOAT, ["n", 3, modeldata.INPUT_H, modeldata.INPUT_W])],
        [helper.make_tensor_value_info("embedding", TensorProto.FLOAT, ["n", 3])])
    model = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.save(model, str(tmp_path / "embedder.onnx"))
    # index rows are RGB directions: red twice (same artwork), green, blue
    modeldata.write_index(tmp_path, np.array([[1, 0, 0], [1, .05, 0], [0, 1, 0], [0, 0, 1]], np.float32),
                          ["red-a", "red-b.1", "green", "blue"], [0, 0, 1, 2], "tiny")
    monkeypatch.setenv("MTGSTACK_MODEL", str(tmp_path))
    recognizer._default.clear()
    rec = recognizer.load_default()
    assert rec is not None and rec.model_version == "tiny"
    reddish = np.zeros((680, 488, 3), np.uint8)
    reddish[..., 2], reddish[..., 1] = 230, 20                      # BGR
    m = rec.match(reddish)
    assert set(m.printings) == {"red-a", "red-b"} and m.score > 0.95 and m.confident
    assert m.others[0][0] == "green"
    recognizer._default.clear()


def test_no_model_installed_means_ocr_only(tmp_path, monkeypatch):
    monkeypatch.setenv("MTGSTACK_MODEL", str(tmp_path / "nothing"))
    recognizer._default.clear()
    assert recognizer.load_default() is None


# ---- scanner integration -----------------------------------------------------------------------------------
def card(name, set_, cn, **kw):
    return {"object": "card", "id": U(f"{set_}/{cn}"), "oracle_id": U(name), "name": name, "set": set_, "lang": "en",
            "set_name": set_.upper(), "collector_number": cn, "rarity": "rare", "type_line": "Artifact", "mana_cost": "",
            "cmc": 0, "colors": [], "finishes": ["nonfoil", "foil"], "released_at": "2020-11-20", "border_color": "black",
            "prices": {"usd": "1.00"}, **kw}


class FakeModel:
    def __init__(self, printings, score=0.9, margin=0.5):
        self.m = Match(printings=printings, score=score, margin=margin, model_version="fake-1")

    def match(self, card_bgr):
        return self.m


@pytest.fixture
def mcat(conn):
    upsert_cards(conn, [card("Brass Orrery", "cmr", "358"), card("Brass Orrery", "roe", "101"), card("Brass Orrery", "spm", "44"),
                        card("Iron Gyre", "dom", "90")])
    NameIndex._cache.clear()
    return Catalog(conn, online=False)


def test_collector_line_picks_the_printing_among_shared_art(mcat):
    shared = [U("roe/101"), U("cmr/358"), U("spm/44")]
    img = photo(card_image("Brass Orrery", "CMR", "358"))
    r = Scanner(mcat, fetch_images=False, recognizer=FakeModel(shared)).scan([img])
    assert (r.printing_id, r.set_code, r.collector_number) == (U("cmr/358"), "cmr", "358")
    assert r.recognizer == "model+ocr" and r.model_version == "fake-1" and r.confidence >= 0.95
    assert r.markers["model"]["confident"] and "title_ocr" not in r.markers      # the title was never read
    assert {c["printing_id"] for c in r.candidates} == set(shared)


def test_confident_picture_names_the_card_when_text_is_useless(mcat):
    # a frame whose title and collector line say nothing the catalog knows (think showcase / Secret Lair)
    img = photo(card_image("Xqzv Wkrrt", "QQQ", "999"))
    r = Scanner(mcat, fetch_images=False, recognizer=FakeModel([U("dom/90")])).scan([img])
    assert r.name == "Iron Gyre" and r.printing_id == U("dom/90") and r.recognizer == "model"
    assert r.confidence >= 0.9

    # same art in several printings and no readable collector line: a guess that asks for review
    r = Scanner(mcat, fetch_images=False, recognizer=FakeModel([U("roe/101"), U("cmr/358")])).scan([img])
    assert r.name == "Brass Orrery" and r.needs_review and r.confidence < 0.9


def test_unsure_picture_falls_back_to_ocr(mcat):
    img = photo(card_image("Brass Orrery", "CMR", "358"))
    plain = Scanner(mcat, fetch_images=False, recognizer=None).scan([img])
    assert plain.recognizer == "ocr" and plain.model_version is None and "model" not in plain.markers

    agrees = Scanner(mcat, fetch_images=False, recognizer=FakeModel([U("cmr/358")], score=0.4, margin=0.02)).scan([img])
    assert agrees.printing_id == plain.printing_id and agrees.recognizer == "model+ocr"
    assert agrees.confidence >= plain.confidence and "title_ocr" in agrees.markers

    differs = Scanner(mcat, fetch_images=False, recognizer=FakeModel([U("dom/90")], score=0.4, margin=0.02)).scan([img])
    assert differs.printing_id == plain.printing_id and differs.recognizer == "ocr"      # OCR result stands...
    assert differs.needs_review and U("dom/90") in {c["printing_id"] for c in differs.candidates}   # ...but is questioned


def test_plausible_picture_is_the_suggestion_when_text_is_useless(mcat):
    img = photo(card_image("Xqzv Wkrrt", "QQQ", "999"))                 # nothing the catalog knows
    r = Scanner(mcat, fetch_images=False, recognizer=FakeModel([U("dom/90")], score=0.56, margin=0.15)).scan([img])
    assert r.name == "Iron Gyre" and r.recognizer == "model" and r.needs_review and r.confidence <= 0.5
    assert r.candidates[0]["printing_id"] == U("dom/90") and not r.markers["model"]["confident"]

    # too weak to be a lead (a card back, a box, a covered card): still no answer, only a candidate
    r = Scanner(mcat, fetch_images=False, recognizer=FakeModel([U("dom/90")], score=0.9, margin=0.0)).scan([img])
    assert r.name is None and r.needs_review and U("dom/90") in {c["printing_id"] for c in r.candidates}

    # a solid OCR reading (set + number confirmed) is not displaced by a merely plausible picture
    img = photo(card_image("Brass Orrery", "CMR", "358"))
    r = Scanner(mcat, fetch_images=False, recognizer=FakeModel([U("dom/90")], score=0.56, margin=0.15)).scan([img])
    assert r.printing_id == U("cmr/358") and r.recognizer == "ocr" and r.needs_review


def test_hints_from_the_caller_and_broken_models(mcat):
    img = photo(card_image("Brass Orrery", "CMR", "358"))
    hints = [{"printing_id": U("roe/101"), "score": 0.91, "group": 1}, {"printing_id": U("cmr/358"), "score": 0.9, "group": 1},
             {"printing_id": U("dom/90"), "score": 0.3, "group": 2}]
    r = Scanner(mcat, fetch_images=False, recognizer=None).scan([img], hints=hints, hints_version="js-1")
    assert r.printing_id == U("cmr/358") and r.model_version == "js-1" and r.recognizer == "model+ocr"

    # printings the catalog doesn't have (index newer than the catalog) -> OCR
    r = Scanner(mcat, fetch_images=False, recognizer=FakeModel(["not-in-catalog"])).scan([img])
    assert r.printing_id == U("cmr/358") and r.recognizer == "ocr"

    class Broken:
        def match(self, card_bgr):
            raise RuntimeError("boom")
    r = Scanner(mcat, fetch_images=False, recognizer=Broken()).scan([img])
    assert r.printing_id == U("cmr/358") and r.recognizer == "ocr"
