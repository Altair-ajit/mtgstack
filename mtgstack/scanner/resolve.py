"""Scanner pipeline: photo(s) -> exact printing + finish + language, with evidence and confidence."""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..catalog import PRINTED_LANG, Catalog
from . import detect, foil, markers

IMG_CACHE = Path.home() / ".mtgstack" / "images"


@dataclass
class ScanResult:
    printing_id: str | None = None
    name: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    language: str = "en"
    finish: str = "nonfoil"
    finish_confidence: float = 0.0
    confidence: float = 0.0
    candidates: list[dict] = field(default_factory=list)   # ranked alternatives for the user to pick from
    markers: dict = field(default_factory=dict)            # raw marker readings
    evidence: list[str] = field(default_factory=list)      # human-readable reasoning trail
    needs_review: bool = True

    def to_dict(self):
        return asdict(self)


def _cand(p: dict, score: float, why: str) -> dict:
    return {"printing_id": p["id"], "name": p["name"], "set_code": p["set_code"], "set_name": p["set_name"],
            "collector_number": p["collector_number"], "lang": p["lang"], "border_color": p["border_color"],
            "finishes": json.loads(p["finishes"] or "[]"), "image": p["image_normal"],
            "score": round(score, 3), "why": why}


class Scanner:
    def __init__(self, catalog: Catalog, fetch_images: bool = True):
        self.catalog, self.fetch_images = catalog, fetch_images

    # ------------------------------------------------------------------------------------------
    def scan(self, images: list[bytes | np.ndarray | str]) -> ScanResult:
        """`images`: one photo, or a short tilt burst of the same card (enables shimmer foil detection)."""
        frames = [detect.load(i) for i in images]
        cards = [detect.warp(f) for f in frames]
        res = ScanResult()

        # Orientation: edge detection gives the card upright-or-upside-down. Decide with the collector line
        # (cards since 2015), else with whichever orientation reads a more confident title.
        cl = markers.read_collector_line(cards[0])
        name, name_conf = None, None
        if not cl.ok:
            flipped = [detect.rotate180(c) for c in cards]
            cl2 = markers.read_collector_line(flipped[0])
            if cl2.ok:
                cards, cl = flipped, cl2
                res.evidence.append("card was upside down; rotated (collector line)")
            else:
                n1, n2 = markers.read_name(cards[0]), markers.read_name(flipped[0])
                if n2[1] > n1[1] + 5:
                    cards, (name, name_conf) = flipped, n2
                    res.evidence.append("card was upside down; rotated (title)")
                else:
                    name, name_conf = n1
        res.markers["collector"] = {k: v for k, v in asdict(cl).items() if k != "evidence"}
        res.evidence += cl.evidence

        if name is None:
            name, name_conf = markers.read_name(cards[0])
        res.markers["name_ocr"] = {"text": name, "conf": round(name_conf, 1)}
        border, bstats = markers.border_color(cards[0])
        res.markers["border"] = {"color": border, **bstats}
        res.evidence.append(f"border looks {border}")
        if cl.lang:
            res.language = PRINTED_LANG.get(cl.lang, "en")

        printing = None
        # ---- 1. exact: set code + collector number -------------------------------------------
        if cl.ok:
            printing = self.catalog.by_set_cn(cl.set_code, cl.number, res.language)
            if printing:
                res.evidence.append(f"collector line -> {printing['name']} [{printing['set_code']} #{printing['collector_number']}]")
                if name and not self._name_agrees(name, printing["name"]):
                    res.evidence.append(f"WARNING: title OCR {name!r} disagrees with {printing['name']!r}")
                    res.confidence = 0.6
                else:
                    res.confidence = 0.95
                res.candidates = [_cand(printing, res.confidence, "set+number")]
            else:
                res.evidence.append(f"no printing {cl.set_code}#{cl.number} in catalog")

        # ---- 2. name -> candidates -> filter by markers -> rank by art ----------------------
        if printing is None and name:
            matches = self.catalog.fuzzy_name(name)
            res.markers["name_matches"] = matches[:3]
            if matches and matches[0][1] >= 70:
                best_name = matches[0][0]
                res.evidence.append(f"title OCR {name!r} -> {best_name} ({matches[0][1]:.0f})")
                cands = self.catalog.prints_by_name(best_name)
                if cl.set_code:
                    same_set = [c for c in cands if c["set_code"] == cl.set_code.lower()]
                    if same_set:
                        cands = same_set
                        res.evidence.append(f"filtered to set {cl.set_code}")
                lang_match = [c for c in cands if c["lang"] == res.language]
                cands = lang_match or cands
                by_border = [c for c in cands if c["border_color"] == border]
                if by_border and len(by_border) < len(cands):
                    res.evidence.append(f"border {border} narrows {len(cands)} -> {len(by_border)} printings")
                    cands = by_border
                ranked = self._rank_by_art(cards[0], cands)
                res.candidates = ranked
                if ranked:
                    printing = self.catalog.get(ranked[0]["printing_id"])
                    margin = ranked[0]["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0)
                    res.confidence = 0.9 if len(ranked) == 1 else min(0.85, 0.4 + margin)

        if printing is None:
            res.evidence.append("could not identify the card; pick from candidates or search manually")
            return res

        res.printing_id, res.name = printing["id"], printing["name"]
        res.set_code, res.collector_number = printing["set_code"], printing["collector_number"]

        # ---- 3. finish -------------------------------------------------------------------------
        finishes = json.loads(printing["finishes"] or "[]")
        res.finish, res.finish_confidence = self._finish(finishes, cl, cards, border, res.evidence)
        res.needs_review = res.confidence < 0.9 or res.finish_confidence < 0.8
        return res

    # ------------------------------------------------------------------------------------------
    @staticmethod
    def _name_agrees(ocr: str, real: str) -> bool:
        from rapidfuzz import fuzz
        return fuzz.partial_ratio(ocr.lower(), real.lower()) >= 70

    def _finish(self, finishes: list[str], cl, cards, border, evidence) -> tuple[str, float]:
        premium = [f for f in finishes if f in ("foil", "etched")]
        if len(finishes) == 1:
            evidence.append(f"printing only exists as {finishes[0]}")
            return finishes[0], 1.0
        if cl.separator == "star":
            fin = "foil" if "foil" in premium else (premium[0] if premium else "foil")
            evidence.append(f"★ separator -> premium finish ({fin})")
            return fin, 0.95 if len(premium) <= 1 else 0.75
        if cl.separator == "dot":
            evidence.append("• separator -> nonfoil")
            return "nonfoil", 0.95
        est = foil.estimate(cards, border)
        evidence.append(f"foil estimate via {est.method}: {est.finish} (conf {est.confidence}, "
                        f"sheen={est.sheen}, shimmer={est.shimmer})")
        if est.finish == "foil" and premium:
            return ("foil" if "foil" in premium else premium[0]), est.confidence
        if est.finish == "nonfoil" and "nonfoil" in finishes:
            return "nonfoil", est.confidence
        return ("nonfoil" if "nonfoil" in finishes else finishes[0] if finishes else "nonfoil"), 0.3

    def _rank_by_art(self, card: np.ndarray, cands: list[dict]) -> list[dict]:
        if len(cands) <= 1 or not self.fetch_images:
            return [_cand(c, 1.0 / max(len(cands), 1), "name match") for c in cands]
        mine = markers.art_hash(card)
        scored = []
        for c in cands:
            ref = self._ref_hash(c)
            if ref is None:
                scored.append(_cand(c, 0.0, "no reference image"))
                continue
            dist = mine - ref                      # hamming distance over 256 bits
            scored.append(_cand(c, max(0.0, 1 - dist / 128), f"art hash distance {dist}"))
        return sorted(scored, key=lambda c: -c["score"])

    def _ref_hash(self, p: dict):
        url = p.get("image_normal")
        if not url:
            return None
        try:
            import imagehash
            from PIL import Image
            data = fetch_image(url)
            img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))[:, :, ::-1]
            ref = detect.warp(img, corners=np.array([[0, 0], [img.shape[1] - 1, 0],
                                                     [img.shape[1] - 1, img.shape[0] - 1], [0, img.shape[0] - 1]],
                                                    np.float32))
            return markers.art_hash(ref)
        except Exception:
            return None


def fetch_image(url: str) -> bytes:
    IMG_CACHE.mkdir(parents=True, exist_ok=True)
    f = IMG_CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")
    if f.exists():
        return f.read_bytes()
    import httpx
    from ..catalog import HEADERS
    r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    f.write_bytes(r.content)
    return r.content
