"""Read the printed identification markers off a canonical (warped) card image.

Markers, in order of how much they pin down:

  1. Collector line (bottom-left, every card since Magic 2015):
       pre-March of the Machine   "123/264 R"   newline  "KLD • EN  Artist"
       March of the Machine on    "R 0123"      newline  "MOM • EN  Artist"
     -> set code + collector number + language identify the exact printing.
     -> the glyph between set code and language is a dot (•) on regular cards and a star (★) on
        premium (foil / etched) cards. We classify it by *shape* (convexity), not OCR, because OCR
        reads both glyphs unreliably.
  2. Card name (title bar) -> OCR + fuzzy match against the catalog. Narrows to all printings.
  3. Border colour (black / white / silver / gold / borderless) -> filters printings.
  4. Art region perceptual hash -> ranks the remaining printings against Scryfall art crops.
  5. Foil sheen (see foil.py) -> finish for cards with no star/dot (pre-2015) or when the glyph is unreadable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

try:
    import pytesseract
except ImportError:          # browser build: OCR comes from set_ocr_backend()
    pytesseract = None

from .detect import border_band, crop


_OCR_BACKEND = None


def set_ocr_backend(fn) -> None:
    """Replace the local tesseract binary: fn(png_bytes, psm) -> Tesseract TSV text (the phone build uses
    Tesseract compiled to WebAssembly)."""
    global _OCR_BACKEND
    _OCR_BACKEND = fn


def _tsv_to_dict(tsv: str) -> dict:
    cols = ["level", "page_num", "block_num", "par_num", "line_num", "word_num", "left", "top", "width", "height",
            "conf", "text"]
    d = {c: [] for c in cols}
    for line in tsv.splitlines():
        parts = line.split("\t")
        if len(parts) < 12 or parts[0] == "level":
            continue
        for c, v in zip(cols, parts[:11] + ["\t".join(parts[11:])]):
            d[c].append(v if c == "text" else (float(v) if c == "conf" else int(v)))
    return d


def _find_tesseract() -> None:
    """Use $MTGSTACK_TESSERACT, else PATH, else the standard Windows/macOS install locations."""
    import os
    import shutil
    from pathlib import Path
    explicit = os.environ.get("MTGSTACK_TESSERACT")
    if explicit:
        pytesseract.pytesseract.tesseract_cmd = explicit
        return
    if shutil.which("tesseract"):
        return
    for cand in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
                 Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
                 Path("/opt/homebrew/bin/tesseract"), Path("/usr/local/bin/tesseract")):
        if cand.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(cand)
            return


if pytesseract is not None:
    _find_tesseract()

LANGS = "EN|DE|FR|IT|ES|SP|PT|JP|JA|KO|KR|RU|CS|CT|PH"
RARITY_LETTERS = "CURMSLPTB"


@dataclass
class CollectorLine:
    raw: str = ""
    set_code: str | None = None
    number: str | None = None
    set_total: str | None = None
    rarity: str | None = None
    lang: str | None = None
    separator: str | None = None        # 'star' | 'dot' | None
    separator_solidity: float | None = None
    style: str | None = None            # 'pre-mom' | 'mom+' | None
    evidence: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.set_code and self.number)


def _prep_text(img: np.ndarray, scale: int = 3, invert_if_dark: bool = True) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if invert_if_dark and th.mean() < 127:  # light text on dark border -> make text black on white
        th = 255 - th
    return th


def ocr_words(img: np.ndarray, psm: int = 6) -> list[dict]:
    if _OCR_BACKEND is not None:
        ok, png = cv2.imencode(".png", img)
        d = _tsv_to_dict(_OCR_BACKEND(png.tobytes(), psm))
    else:
        d = pytesseract.image_to_data(img, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        if t.strip():
            out.append({"text": t.strip(), "x": d["left"][i], "y": d["top"][i], "w": d["width"][i],
                        "h": d["height"][i], "line": (d["block_num"][i], d["par_num"][i], d["line_num"][i]),
                        "conf": float(d["conf"][i])})
    return out


def classify_separator(bin_img: np.ndarray) -> tuple[str | None, float | None]:
    """Given a black-on-white crop containing just the separator glyph, return ('star'|'dot', solidity).

    A filled circle has solidity (area / convex hull area) ~0.95; a five-point star is ~0.5-0.6.
    """
    inv = 255 - bin_img
    cnts, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) > 6]
    if not cnts:
        return None, None
    c = max(cnts, key=cv2.contourArea)
    hull = cv2.contourArea(cv2.convexHull(c))
    if hull <= 0:
        return None, None
    solidity = cv2.contourArea(c) / hull
    return ("star" if solidity < 0.80 else "dot"), round(solidity, 3)


_SETLANG = re.compile(rf"\b([A-Z0-9]{{3,5}})\s*(\S{{0,2}})\s*({LANGS})\b")
_PRE = re.compile(rf"\b(\d{{1,4}})\s*/\s*(\d{{1,4}})\s*([{RARITY_LETTERS}])?\b")
_POST = re.compile(rf"\b([{RARITY_LETTERS}])\s+(\d{{3,5}}[a-z]?)\b")
_BARE = re.compile(r"\b(\d{1,4}[a-z]?)\b")


def binarize_variants(region: np.ndarray, scale: int = 3) -> list[tuple[str, np.ndarray]]:
    """Several black-text-on-white renderings of a region. Real cards (foil glare, textured title bars,
    showcase frames) defeat any single threshold; the matcher picks whichever reading makes sense."""
    g = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    out = []

    def norm(th):  # make text dark on light, whichever polarity the region has
        return 255 - th if th.mean() < 127 else th

    _, otsu = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(("otsu", norm(otsu)))
    out.append(("otsu-inv", 255 - norm(otsu)))
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(g)
    _, c = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(("clahe", norm(c)))
    ad = cv2.adaptiveThreshold(cv2.GaussianBlur(g, (3, 3), 0), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 31, 9)
    out.append(("adaptive", norm(ad)))
    return out


def read_collector_line(card: np.ndarray) -> CollectorLine:
    """Best single reading (first variant that parses). See read_collector_variants for all of them."""
    variants = read_collector_variants(card)
    return next((v for v in variants if v.ok), variants[0] if variants else CollectorLine())


def read_collector_variants(card: np.ndarray, stop=None) -> list[CollectorLine]:
    """Every rendering's reading. OCR is the slowest step of a scan: `stop(reading) -> bool` ends the loop as
    soon as a reading is good enough for the caller, skipping the remaining renderings."""
    region = crop(card, "collector")
    out = []
    for name, th in binarize_variants(region):
        cl = _parse_collector(th)
        cl.evidence.append(f"(collector OCR variant: {name})")
        out.append(cl)
        if stop is not None and stop(cl):
            break
    return out


def _parse_collector(th: np.ndarray) -> CollectorLine:
    words = ocr_words(th)
    text = "\n".join(_join_lines(words))
    cl = CollectorLine(raw=text)

    # --- set code + language (+ separator) -------------------------------------------------
    m = _SETLANG.search(text.upper())
    if m:
        cl.set_code, sep_txt, lang = m.group(1), m.group(2), m.group(3)
        cl.lang = lang
        cl.evidence.append(f"collector line set/lang: {cl.set_code} {lang}")
        sep, sol = _separator_from_boxes(th, words, cl.set_code, lang)
        if sep is None and sep_txt:
            sep = "star" if any(ch in sep_txt for ch in "★*%x+") else "dot" if any(ch in sep_txt for ch in "•.·-«°") else None
            if sep:
                cl.evidence.append(f"separator from OCR text {sep_txt!r}: {sep}")
        else:
            cl.evidence.append(f"separator glyph shape: {sep} (solidity {sol})")
        cl.separator, cl.separator_solidity = sep, sol

    # --- collector number + rarity -------------------------------------------------------
    up = text.upper()
    if m2 := _PRE.search(up):
        cl.number, cl.set_total, cl.rarity, cl.style = m2.group(1), m2.group(2), m2.group(3), "pre-mom"
        cl.evidence.append(f"number {cl.number}/{cl.set_total} (pre-MOM layout)")
    elif m2 := _POST.search(up):
        cl.rarity, cl.number, cl.style = m2.group(1), m2.group(2).lstrip("0") or "0", "mom+"
        cl.evidence.append(f"number {m2.group(2)} rarity {cl.rarity} (MOM+ layout)")
    elif cl.set_code:
        first = text.split("\n")[0]
        if m2 := _BARE.search(first):
            cl.number = m2.group(1).lstrip("0") or "0"
            cl.evidence.append(f"number {cl.number} (bare)")
    return cl


def read_text_variants(card: np.ndarray, region: str, psm: int = 7) -> list[str]:
    """Distinct cleaned OCR strings for a one-line region (title, type line) across binarizations."""
    seen, out = set(), []
    for _, th in binarize_variants(crop(card, region), scale=2):
        words = [w for w in ocr_words(th, psm=psm) if re.search(r"[A-Za-z]", w["text"])]
        text = " ".join(w["text"] for w in sorted(words, key=lambda w: w["x"]))
        text = re.sub(r"[^A-Za-z0-9 ,'\-/]", "", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        if len(re.sub(r"[^A-Za-z]", "", text)) >= 3 and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def _join_lines(words: list[dict]) -> list[str]:
    lines: dict[tuple, list] = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w)
    return [" ".join(x["text"] for x in sorted(ws, key=lambda w: w["x"])) for _, ws in sorted(lines.items())]


def _separator_from_boxes(th: np.ndarray, words: list[dict], set_code: str, lang: str):
    """Crop the gap between the set-code word and the language word and classify the glyph in it."""
    set_w = lang_w = None
    for w in words:
        t = w["text"].upper()
        if set_w is None and t.startswith(set_code):
            set_w = w
        elif set_w is not None and lang_w is None and w["line"] == set_w["line"] and t.endswith(lang):
            lang_w = w
    if not set_w or not lang_w:
        return None, None
    # The set word may have swallowed the glyph ("KLD•"), so start right after the code's letters.
    char_w = set_w["w"] / max(len(set_w["text"]), 1)
    x0 = int(set_w["x"] + char_w * len(set_code) + 1)
    x1 = int(lang_w["x"] + lang_w["w"] - char_w * len(lang) - 1)
    y0 = max(0, min(set_w["y"], lang_w["y"]) - 2)
    y1 = max(set_w["y"] + set_w["h"], lang_w["y"] + lang_w["h"]) + 2
    if x1 - x0 < 3:
        return None, None
    return classify_separator(th[y0:y1, x0:x1])


def read_name(card: np.ndarray) -> tuple[str, float]:
    """OCR the title bar. Title text is dark on a light bar on most frames; try both polarities."""
    region = crop(card, "name")
    best = ("", -1.0)
    for invert in (False, True):
        th = _prep_text(region, scale=2, invert_if_dark=not invert)
        if invert:
            th = 255 - th
        words = ocr_words(th, psm=7)
        words = [w for w in words if w["conf"] > 30 and re.search(r"[A-Za-z]", w["text"])]
        if not words:
            continue
        text = " ".join(w["text"] for w in sorted(words, key=lambda w: w["x"]))
        text = re.sub(r"[^A-Za-z0-9 ,'\-/]", "", text).strip()
        conf = float(np.mean([w["conf"] for w in words]))
        if conf > best[1]:
            best = (text, conf)
    return best


def border_color(card: np.ndarray) -> tuple[str, dict]:
    px = border_band(card)
    hsv = cv2.cvtColor(px.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(float)
    v_med, s_med = float(np.median(hsv[:, 2])), float(np.median(hsv[:, 1]))
    v_std = float(np.std(hsv[:, 2]))
    stats = {"v_median": v_med, "s_median": s_med, "v_std": round(v_std, 1)}
    # Bright glare covers some of the band; medians are robust to that, std is not, so use a high bar.
    if v_std > 70 and 60 < v_med < 200:
        return "borderless", stats
    if v_med < 70:
        return "black", stats
    if s_med > 90 and 15 <= np.median(hsv[:, 0]) <= 35:
        return "gold", stats
    if v_med > 185 and s_med < 40:
        return "white", stats
    if s_med < 40:
        return "silver", stats
    return "borderless", stats


class PHash:
    """Perceptual hash (DCT of a downscaled grey image, bits above the median). `a - b` = Hamming distance."""

    def __init__(self, bits: np.ndarray):
        self.bits = bits.astype(bool).ravel()

    def __sub__(self, other: "PHash") -> int:
        return int(np.count_nonzero(self.bits != other.bits))

    def __str__(self) -> str:
        return np.packbits(self.bits).tobytes().hex()


def phash(img: np.ndarray, hash_size: int = 16, highfreq: int = 4) -> PHash:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    n = hash_size * highfreq
    small = cv2.resize(g, (n, n), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:hash_size, :hash_size]
    return PHash(low > np.median(low))


def art_hash(card: np.ndarray) -> PHash:
    return phash(crop(card, "art"))
