"""Card edge detection at any rotation, position, perspective and background."""
import cv2
import numpy as np
import pytest
from synth import card_image, place, corner_error
from mtgstack.scanner import detect
from mtgstack.scanner.resolve import Scanner

CARD = card_image()

@pytest.mark.parametrize("bg", ["table", "white", "dark"])
def test_full_rotation_sweep(bg):
    for angle in range(0, 360, 20):
        img, truth = place(CARD, angle, bg, scale=0.75, persp=0.04, seed=angle)
        found = detect.find_card(img)
        assert found is not None, (bg, angle)
        assert corner_error(found, truth) < 6, (bg, angle, corner_error(found, truth))

def _random_errs(bgs, n, seed):
    rng = np.random.default_rng(seed)
    errs = []
    for i in range(n):
        img, truth = place(CARD, rng.uniform(0, 360), bgs[i % len(bgs)], scale=rng.uniform(0.55, 0.9),
                           off=rng.uniform(-120, 120, 2), persp=rng.uniform(0, 0.08), seed=i)
        found = detect.find_card(img)
        errs.append(corner_error(found, truth) if found is not None else 1e9)
    return np.array(errs)

def test_random_poses_plain_backgrounds_exact():
    errs = _random_errs(["table", "white", "dark"], 18, seed=7)
    assert (errs < 6).all(), errs.round(1)

def test_random_poses_hostile_clutter_never_lost():
    # random colour-block grid + lines: much harsher than a playmat. Must stay on the card.
    errs = _random_errs(["clutter"], 10, seed=11)
    assert (errs < 6).mean() >= 0.5 and errs.max() < 120, errs.round(1)

def test_order_is_portrait_for_any_rotation():
    for angle in range(0, 360, 15):
        _, truth = place(CARD, angle)
        q = detect._order(np.roll(truth, angle // 15 % 4, 0).astype(np.float32))
        top, side = np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[1])
        assert top < side, angle

@pytest.mark.parametrize("angle", [35, 90, 165, 200, 270, 320])
def test_scan_any_rotation(cat, angle):
    img, _ = place(CARD, angle, "table", scale=0.85, persp=0.03, seed=angle)
    r = Scanner(cat, fetch_images=False).scan([img])
    assert (r.name, r.set_code, r.collector_number) == ("Sol Ring", "cmm", "410"), r.evidence

def test_scan_rotated_card_without_collector_line_uses_title_for_orientation(cat):
    c = card_image(name="Counterspell", set_code="", number="0")
    c[860:900, 20:400] = (12, 12, 14)
    for angle in (180, 95):
        img, _ = place(c, angle, "table", scale=0.85, seed=angle)
        r = Scanner(cat, fetch_images=False).scan([img])
        assert r.name == "Counterspell", (angle, r.evidence)

def test_track_report():
    img, _ = place(CARD, 130, "table", scale=0.85)
    small = cv2.resize(img, (640, 640))
    t = detect.track(small)
    assert t["found"] and t["hint"] == "ready" and len(t["corners"]) == 4
    assert all(0 <= v <= 1 for p in t["corners"] for v in p)
    far, _ = place(CARD, 10, "table", scale=0.42)
    assert detect.track(cv2.resize(far, (640, 640)))["hint"] == "move closer"
    assert detect.track(np.full((480, 640, 3), 90, np.uint8))["found"] is False
