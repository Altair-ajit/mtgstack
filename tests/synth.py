"""Synthetic card photos for scanner tests. Not real card art — just the marker layout of a modern frame:
title bar, art box, type line, and the two-line collector block in the bottom-left border."""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 672, 936
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SERIF_B = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"


def star(cx, cy, r, inner=0.42):
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * inner
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def card_image(name="Sol Ring", set_code="CMM", number="410", total="451", rarity="U", lang="EN",
               premium=False, layout="pre-mom", border="black", art_seed=1) -> np.ndarray:
    border_rgb = {"black": (12, 12, 14), "white": (240, 240, 236)}[border]
    text_rgb = (235, 235, 235) if border == "black" else (20, 20, 20)
    im = Image.new("RGB", (W, H), border_rgb)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((26, 26, W - 26, H - 72), radius=10, fill=(150, 130, 105))       # frame
    d.rounded_rectangle((44, 38, W - 44, 92), radius=8, fill=(232, 226, 210))            # title bar
    d.text((60, 46), name, font=ImageFont.truetype(SERIF_B, 30), fill=(10, 10, 10))
    rng = np.random.default_rng(art_seed)                                                # art box
    art = rng.integers(0, 255, (8, 10, 3), dtype=np.uint8)
    art = cv2.resize(art, (W - 108, 410), interpolation=cv2.INTER_CUBIC)
    im.paste(Image.fromarray(art), (54, 100))
    d.rounded_rectangle((44, 518, W - 44, 568), radius=6, fill=(232, 226, 210))          # type line
    d.text((60, 528), "Artifact", font=ImageFont.truetype(SERIF_B, 24), fill=(10, 10, 10))
    d.rectangle((54, 578, W - 54, 840), fill=(240, 234, 220))                            # text box
    d.text((70, 600), "{T}: Add {C}{C}.", font=ImageFont.truetype(SANS, 24), fill=(10, 10, 10))

    f = ImageFont.truetype(BOLD, 15)
    x, y1, y2 = 30, 870, 891
    line1 = f"{number}/{total} {rarity}" if layout == "pre-mom" else f"{rarity} {int(number):04d}"
    d.text((x, y1), line1, font=f, fill=text_rgb)
    d.text((x, y2), set_code, font=f, fill=text_rgb)
    sx = x + d.textlength(set_code + " ", font=f)
    cy = y2 + 9
    if premium:
        d.polygon(star(sx + 6, cy, 6.5), fill=text_rgb)
    else:
        d.ellipse((sx + 3, cy - 3, sx + 9, cy + 3), fill=text_rgb)
    d.text((sx + 17, y2), f"{lang}  Mark Tedin", font=f, fill=text_rgb)
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def photo(card: np.ndarray, angle_deg=6.0, persp=0.06, bg_seed=0, size=(1400, 1800), glare=None) -> np.ndarray:
    """Place the card on a textured table with rotation + perspective, optional glare blob."""
    rng = np.random.default_rng(bg_seed)
    bh, bw = size[1], size[0]
    bg = np.full((bh, bw, 3), (70, 95, 120), np.uint8)
    noise = cv2.resize(rng.integers(0, 40, (bh // 40, bw // 40, 3), dtype=np.uint8), (bw, bh))
    bg = cv2.add(bg, noise)
    scale = 1.6
    cw, ch = W * scale, H * scale
    cx, cy = bw / 2, bh / 2
    pts = np.array([[-cw / 2, -ch / 2], [cw / 2, -ch / 2], [cw / 2, ch / 2], [-cw / 2, ch / 2]])
    a = math.radians(angle_deg)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    pts = pts @ rot.T
    pts[0] += [persp * cw, 0]
    pts[1] -= [persp * cw, 0]
    pts += [cx, cy]
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [W, 0], [W, H], [0, H]]), np.float32(pts))
    warped = cv2.warpPerspective(card, m, (bw, bh))
    mask = cv2.warpPerspective(np.full((H, W), 255, np.uint8), m, (bw, bh))
    out = np.where(mask[..., None] > 0, warped, bg)
    if glare is not None:
        gx, gy, rad, colorful, phase = glare
        yy, xx = np.mgrid[0:bh, 0:bw]
        g = np.exp(-(((xx - gx) ** 2 + (yy - gy) ** 2) / (2 * rad ** 2)))[..., None]
        if colorful:  # rainbow: hue varies across space and with phase (tilt)
            hue = ((xx + yy) / 6 + phase * 60) % 180
            hsv = np.stack([hue, np.full_like(hue, 230), np.full_like(hue, 255)], -1).astype(np.uint8)
            col = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR).astype(float)
        else:
            col = np.full((bh, bw, 3), 255.0)
        out = (out * (1 - 0.8 * g * (mask[..., None] > 0)) + col * 0.8 * g * (mask[..., None] > 0)).astype(np.uint8)
    out = cv2.GaussianBlur(out, (3, 3), 0)
    return out


def burst(card: np.ndarray, colorful: bool, n=5) -> list[np.ndarray]:
    """Simulated tilt burst: same pose, a glare blob sweeping across the art box."""
    frames = []
    for i in range(n):
        gx = 450 + i * 120
        frames.append(photo(card, glare=(gx, 700, 170, colorful, i)))
    return frames


def place(card: np.ndarray, angle: float, bg: str = "table", scale: float = 0.8, off=(0, 0), persp: float = 0.03,
          seed: int = 0, size: int = 1600):
    """Card at any rotation/position/perspective on a background. Returns (photo, true corners tl,tr,br,bl)."""
    rng = np.random.default_rng(seed)
    if bg == "table":
        img = cv2.add(np.full((size, size, 3), (70, 95, 120), np.uint8),
                      cv2.resize(rng.integers(0, 40, (size // 40, size // 40, 3), dtype=np.uint8), (size, size)))
    elif bg == "white":
        img = cv2.add(np.full((size, size, 3), (215, 215, 215), np.uint8),
                      cv2.resize(rng.integers(0, 25, (size // 30, size // 30, 3), dtype=np.uint8), (size, size)))
    elif bg == "dark":
        img = cv2.add(np.full((size, size, 3), (22, 22, 24), np.uint8),
                      cv2.resize(rng.integers(0, 15, (size // 30, size // 30, 3), dtype=np.uint8), (size, size)))
    else:  # "clutter": random colour blocks + lines, a deliberately hostile background
        img = cv2.resize(rng.integers(0, 255, (12, 12, 3), dtype=np.uint8), (size, size), interpolation=cv2.INTER_NEAREST)
        for _ in range(25):
            x, y = rng.integers(0, size, 2)
            cv2.line(img, (int(x), int(y)), (int(x + rng.integers(-400, 400)), int(y + rng.integers(-400, 400))),
                     tuple(int(c) for c in rng.integers(0, 255, 3)), 3)
    cw, ch = W * scale, H * scale
    pts = np.array([[-cw / 2, -ch / 2], [cw / 2, -ch / 2], [cw / 2, ch / 2], [-cw / 2, ch / 2]])
    pts[0] += [persp * cw, 0]
    pts[1] -= [persp * cw, 0]
    a = math.radians(angle)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    pts = pts @ rot.T + [size / 2 + off[0], size / 2 + off[1]]
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [W, 0], [W, H], [0, H]]), np.float32(pts))
    warped = cv2.warpPerspective(card, m, (size, size))
    mask = cv2.warpPerspective(np.full((H, W), 255, np.uint8), m, (size, size))
    return cv2.GaussianBlur(np.where(mask[..., None] > 0, warped, img), (3, 3), 0), pts


def corner_error(found: np.ndarray, truth: np.ndarray) -> float:
    """Max corner distance, ignoring which corner is called 'first' (orientation is settled later by OCR)."""
    return min(float(np.linalg.norm(np.roll(found, k, 0) - truth, axis=1).max()) for k in range(4))
