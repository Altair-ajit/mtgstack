"""Regression tests for the 'Training Center scanned as Aunt May' class of bugs (fake ids/numbers)."""
import uuid
import httpx
import pytest
from synth import card_image, photo
from mtgstack.catalog import Catalog, Scryfall, upsert_cards
from mtgstack.scanner.resolve import NameIndex, Scanner

U = lambda s: str(uuid.uuid5(uuid.NAMESPACE_URL, "xcheck/" + s))


def card(name, set_, cn, type_line):
    return {"object": "card", "id": U(f"{set_}/{cn}"), "oracle_id": U(name), "name": name, "set": set_, "lang": "en",
            "set_name": set_.upper(), "collector_number": cn, "rarity": "rare", "type_line": type_line, "mana_cost": "",
            "cmc": 0, "colors": [], "finishes": ["nonfoil", "foil"], "released_at": "2020-11-20", "border_color": "black",
            "prices": {"usd": "1.00"}}


@pytest.fixture
def xcat(conn):
    upsert_cards(conn, [card("Training Center", "cmr", "358", "Land"),
                        card("Aunt May", "spm", "4", "Legendary Creature — Human Citizen"),
                        card("Training Grounds", "roe", "101", "Enchantment")])
    NameIndex._cache.clear()
    return Catalog(conn, online=False)


def scan(cat, img):
    return Scanner(cat, fetch_images=False).scan([img])


def test_title_beats_a_collector_line_that_reads_as_another_real_card(xcat):
    # Title says Training Center, but the collector line reads "SPM 4" = Aunt May (a real printing).
    img = photo(card_image(name="Training Center", set_code="SPM", number="4", total="200", rarity="R", layout="pre-mom"))
    r = scan(xcat, img)
    assert r.name == "Training Center", r.evidence
    assert any("doesn't match" in e for e in r.evidence)


def test_collector_line_agreeing_with_title_gives_exact_printing(xcat):
    r = scan(xcat, photo(card_image(name="Training Center", set_code="CMR", number="358", total="361", rarity="R")))
    assert (r.name, r.set_code, r.collector_number) == ("Training Center", "cmr", "358") and r.confidence >= 0.9


def test_unreadable_card_is_not_guessed(xcat):
    c = card_image(name="Xqzv Wrtp Lmnb", set_code="", number="0")
    c[860:900, 20:400] = (12, 12, 14)
    r = scan(xcat, photo(c))
    assert r.printing_id is None and r.confidence == 0.0


def test_debug_crops_are_returned(xcat):
    r = scan(xcat, photo(card_image(name="Training Center", set_code="CMR", number="358", total="361", rarity="R")))
    assert set(r.debug) == {"card", "title", "type_line", "collector"} and r.debug["title"].startswith("data:image/jpeg")
    assert "Training Center" in r.markers["title_ocr"]


def test_online_fuzzy_name_must_resemble_the_text(conn):
    # Scryfall's fuzzy endpoint answers *something* for gibberish; that answer must not be trusted.
    def handler(req):
        return httpx.Response(200, json={"object": "card", "name": "Aunt May"})
    cat = Catalog(conn, Scryfall(httpx.Client(transport=httpx.MockTransport(handler))), online=True)
    assert all(n != "Aunt May" for n, _ in cat.fuzzy_name("un ay"))


def test_name_score_punishes_garbage():
    assert NameIndex.score("Training Center", "Training Center") == 100
    assert NameIndex.score("Trainmg Cenler", "Training Center") >= 80
    assert NameIndex.score("un ay", "Aunt May") < 65
    assert NameIndex.score("Training Center 4 R", "Training Center") >= 90     # junk after the name


def test_set_code_ocr_confusions(xcat):
    idx = NameIndex.get(xcat)
    assert idx.set_candidates("CMR") == ["cmr"]
    assert idx.set_candidates("CNR") == []                     # not a confusable letter: no invention
    assert "spm" in idx.set_candidates("5PM")                 # 5 <-> S
