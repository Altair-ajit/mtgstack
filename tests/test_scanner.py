import cv2
import pytest
from synth import card_image, photo, burst
from mtgstack.scanner import foil, detect
from mtgstack.scanner.resolve import Scanner

@pytest.fixture
def scanner(cat):
    return Scanner(cat, fetch_images=False)

def test_exact_printing_and_dot_means_nonfoil(scanner):
    r = scanner.scan([photo(card_image(set_code="CMM", number="410", premium=False))])
    assert (r.set_code, r.collector_number, r.finish) == ("cmm", "410", "nonfoil")
    assert r.confidence >= 0.9 and not r.needs_review

def test_star_means_foil_post_mom_layout(scanner):
    r = scanner.scan([photo(card_image(set_code="CMM", number="410", premium=True, layout="mom+"))])
    assert r.name == "Sol Ring" and r.finish == "foil" and r.finish_confidence >= 0.9

def test_foil_only_printing_is_decided_by_catalog(scanner):
    r = scanner.scan([photo(card_image(set_code="FDC", number="57", premium=False, layout="mom+"))])
    assert r.set_code == "fdc" and r.finish == "foil" and r.finish_confidence == 1.0

def test_star_on_printing_with_etched_and_foil(scanner):
    r = scanner.scan([photo(card_image(name="Lightning Bolt", set_code="STA", number="42", total="63",
                                       rarity="R", premium=True))])
    assert r.name == "Lightning Bolt" and r.finish == "foil" and r.needs_review   # foil vs etched ambiguous

def test_language_marker(scanner):
    r = scanner.scan([photo(card_image(name="Lightning Bolt", set_code="STA", number="42", total="63",
                                       rarity="R", lang="JP"))])
    assert r.language == "ja" and r.printing_id and scanner.catalog.get(r.printing_id)["lang"] == "ja"

def test_upside_down(scanner):
    img = cv2.rotate(photo(card_image(set_code="CMM", number="703")), cv2.ROTATE_180)
    r = scanner.scan([img])
    assert r.collector_number == "703"

def test_no_collector_line_falls_back_to_name(scanner):
    c = card_image(name="Counterspell", set_code="", number="0")
    c[860:900, 20:400] = (12, 12, 14)                     # erase collector block (pre-2015 style)
    r = scanner.scan([photo(c)])
    assert r.name == "Counterspell" and len(r.candidates) == 2 and r.needs_review

def test_tilt_shimmer_separates_foil_from_glare():
    c = card_image()
    foil_score = foil.tilt_shimmer([detect.warp(f) for f in burst(c, colorful=True)])
    plain_score = foil.tilt_shimmer([detect.warp(f) for f in burst(c, colorful=False)])
    assert foil_score > foil.SHIMMER_THRESHOLD > plain_score, (foil_score, plain_score)

def test_burst_decides_finish_when_no_glyph(scanner):
    c = card_image(name="Counterspell", set_code="", number="0", art_seed=3)
    c[860:900, 20:400] = (12, 12, 14)
    r = scanner.scan(burst(c, colorful=True))
    assert r.name == "Counterspell" and r.finish == "foil"
    r = scanner.scan(burst(c, colorful=False))
    assert r.finish == "nonfoil"
