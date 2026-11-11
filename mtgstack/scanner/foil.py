"""Foil detection from light behaviour, for when the star/dot glyph can't decide.

Two signals:

* single-frame "border sheen": on a black-bordered card the border should be near-black. Glare on a
  non-foil border is *white* (low saturation); glare on a foil border is *coloured* (rainbow). We measure
  the share of border pixels that are both bright and saturated.

* multi-frame "tilt shimmer" (strongest): capture a short burst while tilting the card. Every frame is
  warped to the same canonical frame, so each pixel can be compared over time. On foil, pixels that
  brighten do so with a strong, *shifting hue*; on non-foil, specular highlights are white and the
  underlying hue stays put. Score = share of art pixels that brighten a lot AND change hue AND are
  saturated at their brightest.

Thresholds are defaults; `calibrate()` fits them from a few known cards of yours.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .detect import border_band, crop

SHEEN_THRESHOLD = 0.06
SHIMMER_THRESHOLD = 0.04


@dataclass
class FoilEstimate:
    finish: str | None              # 'foil' | 'nonfoil' | None (undecided)
    confidence: float               # 0..1
    sheen: float | None = None
    shimmer: float | None = None
    method: str = ""


def border_sheen(card: np.ndarray) -> float:
    px = border_band(card).reshape(-1, 1, 3)
    hsv = cv2.cvtColor(px, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    bright = hsv[:, 2] > 110
    colored = hsv[:, 1] > 80
    return float((bright & colored).mean())


def tilt_shimmer(cards: list[np.ndarray], region: str = "art") -> float:
    if len(cards) < 3:
        raise ValueError("need at least 3 aligned frames")
    stack = np.stack([cv2.cvtColor(crop(c, region), cv2.COLOR_BGR2HSV) for c in cards]).astype(np.float32)
    h, s, v = stack[..., 0] * 2.0, stack[..., 1], stack[..., 2]   # hue in degrees
    i_max, i_min = v.argmax(0), v.argmin(0)
    take = lambda a, idx: np.take_along_axis(a, idx[None], 0)[0]
    dv = take(v, i_max) - take(v, i_min)
    s_hi = take(s, i_max)
    # circular hue spread across frames, only meaningful where there's some saturation
    rad = np.deg2rad(h)
    r = np.hypot(np.cos(rad).mean(0), np.sin(rad).mean(0))       # 1 = constant hue, 0 = spread
    hue_spread = 1 - r
    shimmer = (dv > 40) & (s_hi > 70) & (hue_spread > 0.15)
    return float(shimmer.mean())


def estimate(cards: list[np.ndarray], border: str | None = None) -> FoilEstimate:
    sheen = border_sheen(cards[0]) if border in (None, "black") else None
    shimmer = tilt_shimmer(cards) if len(cards) >= 3 else None
    if shimmer is not None:
        ratio = shimmer / SHIMMER_THRESHOLD
        finish = "foil" if ratio >= 1 else "nonfoil"
        conf = min(0.95, 0.5 + abs(np.log(max(ratio, 1e-3))) / 4)
        return FoilEstimate(finish, round(float(conf), 2), sheen, round(shimmer, 4), "tilt-shimmer")
    if sheen is not None:
        ratio = sheen / SHEEN_THRESHOLD
        if ratio >= 1.5:
            return FoilEstimate("foil", 0.6, round(sheen, 4), None, "border-sheen")
        if ratio <= 0.3:
            return FoilEstimate("nonfoil", 0.55, round(sheen, 4), None, "border-sheen")
        return FoilEstimate(None, 0.3, round(sheen, 4), None, "border-sheen (ambiguous)")
    return FoilEstimate(None, 0.0, None, None, "no signal")


def calibrate(foil_bursts: list[list[np.ndarray]], nonfoil_bursts: list[list[np.ndarray]]) -> float:
    """Pick the shimmer threshold halfway (geometrically) between your foil and non-foil cards."""
    f = min(tilt_shimmer(b) for b in foil_bursts)
    n = max(tilt_shimmer(b) for b in nonfoil_bursts)
    return float(np.sqrt(max(f, 1e-4) * max(n, 1e-4)))
