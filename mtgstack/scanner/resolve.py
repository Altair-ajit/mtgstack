"""Scanner pipeline: photo(s) -> exact printing + finish + language, with evidence and confidence."""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
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
    debug: dict = field(default_factory=dict)              # small JPEG data URIs of what was read
    recognizer: str = "ocr"                                # what identified the card: "ocr" | "model" | "model+ocr"
    model_version: str | None = None                       # recognition model consulted (None = OCR only)

    def to_dict(self):
        return asdict(self)


def _cand(p: dict, score: float, why: str) -> dict:
    return {"printing_id": p["id"], "name": p["name"], "set_code": p["set_code"], "set_name": p["set_name"],
            "collector_number": p["collector_number"], "lang": p["lang"], "border_color": p["border_color"],
            "finishes": json.loads(p["finishes"] or "[]"), "image": p["image_normal"],
            "score": round(score, 3), "why": why}


def _choose(pairs):
    """Pick among (rectified card, picture match) pairs, the detector's own choice first.
    -> (card, match or None, True if a later outline was taken)."""
    it = iter(pairs)
    card, first = next(it)
    if first is None or first.confident:
        return card, first, False
    best = (card, first, False)
    for c, m in it:
        if m is None:
            break
        if (m.confident, m.score) > (best[1].confident, best[1].score) and (m.confident or m.score >= first.score + 0.05):
            best = (c, m, True)
            if m.confident and m.score >= 0.85:
                break
    return best


async def prepare_crops(frame: np.ndarray, matcher) -> list | None:
    """For recognisers that can only be awaited: rectify the card every way the detector proposes and match the
    crops up front. `matcher`: async (list of cards) -> list of Match (or None = no model). The result is
    `Scanner.scan(prepared=...)`; None means no model answered and the scan should go the usual way."""
    props = detect.proposals(frame)
    cards = [detect.warp(frame, props[0])]
    matches = list(await matcher(cards))
    if not matches or matches[0] is None:
        return None
    if not matches[0].confident:
        for i in range(1, len(props), 4):                # a few at a time: stop as soon as one clearly fits
            more = [detect.warp(frame, q) for q in props[i:i + 4]]
            got = list(await matcher(more))
            cards += more
            matches += got
            if any(m is not None and m.confident and m.score >= 0.85 for m in got):
                break
    return list(zip(cards, matches))


class Scanner:
    def __init__(self, catalog: Catalog, fetch_images: bool = True, recognizer="default"):
        """`recognizer`: a recognizer.Recognizer, None for OCR only, or "default" = the installed model if any."""
        self.catalog, self.fetch_images = catalog, fetch_images
        if recognizer == "default":
            from .recognizer import load_default
            recognizer = load_default()
        self.recognizer = recognizer

    # ------------------------------------------------------------------------------------------
    def scan(self, images: list[bytes | np.ndarray | str], debug: bool = True,
             hints: list[dict] | None = None, hints_version: str = "", prepared: list | None = None) -> ScanResult:
        """`images`: one photo, or a short tilt burst of the same card (enables shimmer foil detection).

        Every marker is read several ways, each reading is checked against the catalog, and the readings are
        cross-checked: a collector line (set + number) is only trusted if that printing exists AND its name
        agrees with the title; when they disagree the stronger evidence wins; a weak result says so instead of
        guessing.

        With a recognition model (or `hints`: picture matches computed by the caller, best first, as
        [{"printing_id", "score", "group"}]) the picture is looked up first. A confident match names the card
        without reading the title, and only the collector line is read to pick the printing; an unsure match
        falls back to the OCR pipeline and is offered among the candidates.
        `prepared`: the first photo's crops already matched elsewhere (see `prepare_crops`; the phone build runs
        the model in JavaScript, which Python can only await outside this synchronous call).
        """
        frames = [detect.load(i) for i in images]
        res = ScanResult()
        idx = NameIndex.get(self.catalog)
        switched = False
        if prepared:
            card, match, switched = _choose(prepared)
            cards = [card] + [detect.warp(f) for f in frames[1:]]
        elif self.recognizer is not None and not hints:
            # the detector proposes several outlines; the picture match tells which one really is the card
            card, match, switched = self._best_crop(frames[0])
            cards = [card] + [self._best_crop(f)[0] if switched else detect.warp(f) for f in frames[1:]]
        else:
            cards = [detect.warp(f) for f in frames]
            match = self._picture_match(cards[0], hints, hints_version)
        if switched:
            res.evidence.append("the first outline found matched poorly; used another outline of the card")
        if match is not None:
            res.model_version = match.model_version or None
            if match.confident and self._decide_by_picture(match, cards, res, idx, debug):
                return res

        # Orientation: whichever way up yields the more convincing reading.
        best = None
        for flipped in (False, True):
            cs = [detect.rotate180(c) for c in cards] if flipped else cards
            reading = self._read(cs[0], idx)
            if best is None or reading["strength"] > best[1]["strength"]:
                best = (cs, reading, flipped)
            if reading["strength"] >= 2:            # confident enough; don't spend time on the other way up
                break
        cards, rd, flipped = best
        if flipped:
            res.evidence.append("card was upside down; rotated")
        self._titles_online(rd, idx, res)
        cl = rd["collector"]
        res.markers = {**res.markers, "title_ocr": rd["titles"], "type_ocr": rd["types"],
                       "collector_ocr": [v.raw for v in rd["cl_variants"]],
                       "collector": {k: v for k, v in asdict(cl).items() if k != "evidence"} if cl else None,
                       "name_matches": rd["name_hits"][:5],
                       "collector_hits": [{"set": h["set_code"], "number": h["collector_number"], "name": h["name"]}
                                          for h in rd["cl_hits"]]}
        border, bstats = markers.border_color(cards[0])
        res.markers["border"] = {"color": border, **bstats}
        if cl and cl.lang:
            res.language = PRINTED_LANG.get(cl.lang, "en")

        printing, why = self._decide(rd, res, border, cards[0])
        if match is not None and match.printings:
            printing = self._merge_picture(match, printing, res)
        if debug:
            res.debug = _debug_images(cards[0])
        if printing is None:
            res.evidence.append(why or "could not identify the card; pick from the candidates or search by name")
            return res

        res.printing_id, res.name = printing["id"], printing["name"]
        res.set_code, res.collector_number = printing["set_code"], printing["collector_number"]
        finishes = json.loads(printing["finishes"] or "[]")
        res.finish, res.finish_confidence = self._finish(finishes, cl or markers.CollectorLine(), cards, border, res.evidence)
        res.needs_review = res.confidence < 0.9 or res.finish_confidence < 0.8
        return res

    # ---- picture recognition ----------------------------------------------------------------
    def _picture_match(self, card: np.ndarray, hints, hints_version: str):
        from .recognizer import match_from_hints
        try:
            if hints:
                return match_from_hints(hints, hints_version)
            if self.recognizer is not None:
                return self.recognizer.match(card)
        except Exception as e:                      # a broken model must never break scanning
            import logging
            logging.getLogger("mtgstack.scanner").warning("picture recognition failed: %s: %s", type(e).__name__, e)
        return None

    def _best_crop(self, frame: np.ndarray):
        """Rectify the card, letting the picture match choose among the detector's outlines when its first
        choice matches poorly (typically it locked onto the art box). -> (card, match or None, switched?)"""
        def pairs():
            for q in detect.proposals(frame):
                card = detect.warp(frame, q)
                yield card, self._picture_match(card, None, "")
        return _choose(pairs())

    def _pool(self, match, fetch: bool) -> list[dict]:
        """The catalog's rows for the printings a picture match names. The model's index knows every card, the
        local catalog may not (card database not downloaded yet, or older than the index): with `fetch`, missing
        printings are looked up online in one request."""
        ids = match.printings[:60]
        have = {i: self.catalog.get(i) for i in ids}
        missing = [i for i in ids if have[i] is None]
        if missing and fetch and self.catalog.online:
            try:
                self.catalog.lookup_batch([{"id": i} for i in missing])
                have.update({i: self.catalog.get(i) for i in missing})
            except Exception as e:                      # offline: carry on with what is known locally
                import logging
                logging.getLogger("mtgstack.scanner").info("could not look up matched printings: %s", e)
        return [have[i] for i in ids if have[i]]

    def _decide_by_picture(self, match, cards: list[np.ndarray], res: ScanResult, idx: "NameIndex", debug: bool) -> bool:
        """The picture is a confident match: take the card from it and use the collector line only to choose
        between printings that share the artwork. False = unusable (the catalog doesn't know these printings)."""
        pool = self._pool(match, fetch=True)
        if not pool:
            return False
        res.markers["model"] = match.to_dict()
        res.recognizer = "model"
        res.evidence.append(f"picture matches {pool[0]['name']} ({match.score:.0%} similar, {match.margin:.0%} ahead "
                            f"of the next artwork); {len(pool)} printing(s) share this artwork")

        def agree(v, p) -> int:
            """How well a collector-line reading fits a printing: 2 = set and number, 1 = one of them."""
            num = bool(v.number) and p["collector_number"].lstrip("0") == v.number.lstrip("0")
            st = bool(v.set_code) and p["set_code"] in idx.set_candidates(v.set_code)
            return 2 if num and st else 1 if num or st else 0

        # the model is indifferent to which way up the card is, the collector line is not: read it both ways
        best = None
        for flipped in (False, True):
            cs = [detect.rotate180(c) for c in cards] if flipped else cards
            variants = markers.read_collector_variants(cs[0])
            top = max(((agree(v, p), v, p) for v in variants for p in pool), key=lambda t: t[0], default=(0, None, None))
            if best is None or top[0] > best[0]:
                best = (*top, cs, variants)
            if best[0] == 2 or (len(pool) == 1 and any(v.ok for v in variants)):
                break
        score, cl, hit, cards, variants = best
        if not score:
            cl = next((v for v in variants if v.ok), variants[0] if variants else None)
        res.markers["collector_ocr"] = [v.raw for v in variants]
        res.markers["collector"] = {k: v for k, v in asdict(cl).items() if k != "evidence"} if cl else None
        border, bstats = markers.border_color(cards[0])
        res.markers["border"] = {"color": border, **bstats}
        if cl and cl.lang:
            res.language = PRINTED_LANG.get(cl.lang, "en")

        if score == 2:
            printing, res.confidence = hit, 0.97
            res.evidence.append(f"collector line {hit['set_code'].upper()} #{hit['collector_number']} confirms the printing")
            res.recognizer = "model+ocr"
        elif len(pool) == 1:
            printing, res.confidence = pool[0], 0.93
            res.evidence.append("only one printing has this artwork")
        else:
            narrowed = [p for p in pool if score and agree(cl, p)] or pool
            if len(narrowed) < len(pool):
                res.evidence.append(f"collector line narrows it to {len(narrowed)} printing(s)")
                res.recognizer = "model+ocr"
            lang = [p for p in narrowed if p["lang"] == res.language] or narrowed
            by_border = [p for p in lang if p["border_color"] == border] or lang
            printing = by_border[0]
            res.confidence = 0.85 if len(by_border) == 1 else 0.6
            if len(by_border) > 1:
                res.evidence.append(f"{len(by_border)} printings look the same and the collector line didn't settle "
                                    "which: check the set")
        res.candidates = [_cand(printing, res.confidence, "picture match")] + [
            _cand(p, round(match.score, 3), "same artwork") for p in pool if p["id"] != printing["id"]][:30]
        res.printing_id, res.name = printing["id"], printing["name"]
        res.set_code, res.collector_number = printing["set_code"], printing["collector_number"]
        if not (cl and cl.lang):
            res.language = printing["lang"]
        finishes = json.loads(printing["finishes"] or "[]")
        res.finish, res.finish_confidence = self._finish(finishes, cl or markers.CollectorLine(), cards, border, res.evidence)
        res.needs_review = res.confidence < 0.9 or res.finish_confidence < 0.8
        if debug:
            res.debug = _debug_images(cards[0])
        return True

    def _merge_picture(self, match, printing: dict | None, res: ScanResult) -> dict | None:
        """The picture match was not confident enough to decide alone: let it back up or question the OCR result,
        or stand in for it when the text gave nothing solid. Returns the printing to report."""
        res.markers["model"] = match.to_dict()
        pool = self._pool(match, fetch=match.plausible)
        if not pool:
            return printing
        if printing is not None and printing["oracle_id"] == pool[0]["oracle_id"]:
            res.recognizer = "model+ocr"
            if printing["id"] in match.printings:
                res.confidence = round(min(0.97, res.confidence + 0.15), 2)
                res.evidence.append(f"the picture agrees ({match.score:.0%} similar)")
            else:
                res.evidence.append(f"the picture looks like the {pool[0]['set_code'].upper()} "
                                    f"#{pool[0]['collector_number']} printing ({match.score:.0%} similar)")
            return printing
        if match.plausible and (printing is None or res.confidence < 0.8):
            # nothing readable, or only a shaky title guess: the picture is the better lead, but ask for a check
            if printing is not None:
                res.evidence.append(f"the text read as {printing['name']}, but the picture looks like {pool[0]['name']}")
            pick = pool[0]
            ids = {p["id"] for p in pool}
            res.candidates = ([_cand(pick, round(match.score, 3), "picture match")]
                              + [_cand(p, round(match.score, 3), "same artwork") for p in pool[1:30]]
                              + [c for c in res.candidates if c["printing_id"] not in ids])
            res.evidence.append(f"picture looks like {pick['name']} ({match.score:.0%} similar, {match.margin:.0%} ahead "
                                "of the next artwork): not a sure match, check it")
            res.confidence, res.recognizer = 0.5, "model"
            return pick
        have = {c["printing_id"] for c in res.candidates}
        extra = [_cand(p, round(match.score, 3), f"picture looks {match.score:.0%} like this")
                 for p in pool[:3] if p["id"] not in have]
        if printing is None:
            res.candidates = extra + res.candidates
        else:
            res.candidates = res.candidates[:1] + extra + res.candidates[1:]
            res.evidence.append(f"the picture looks more like {pool[0]['name']} ({match.score:.0%} similar): check it")
            res.confidence = round(min(res.confidence, 0.7), 2)
            res.needs_review = True
        return printing

    def _read(self, card: np.ndarray, idx: "NameIndex") -> dict:
        titles = markers.read_text_variants(card, "name")
        types = markers.read_text_variants(card, "type_line")
        cl_variants = markers.read_collector_variants(card)
        # title -> best catalog names over every OCR variant
        hits: dict[str, float] = {}
        for t in titles:
            for name, score in idx.match(t):
                hits[name] = max(hits.get(name, 0.0), score)
        name_hits = sorted(hits.items(), key=lambda kv: -kv[1])
        # collector line -> printings that really exist (with OCR-confusion fixes on the set code)
        cl_hits, cl_best = [], None
        for v in cl_variants:
            if not v.number:
                continue
            for code in idx.set_candidates(v.set_code):
                p = self.catalog.by_set_cn(code, v.number, PRINTED_LANG.get(v.lang or "EN", "en"),
                                           fetch=idx.small and bool(v.set_code))
                if p and p["id"] not in {h["id"] for h in cl_hits}:
                    p = dict(p)
                    p["_code_fixed"] = code != (v.set_code or "").lower()
                    cl_hits.append(p)
                    cl_best = cl_best or v
        if cl_best is None:
            cl_best = next((v for v in cl_variants if v.ok), cl_variants[0] if cl_variants else None)
        top = name_hits[0][1] if name_hits else 0
        strength = (2 if top >= 80 else 1 if top >= 65 else 0) + (1 if cl_hits else 0)
        return {"titles": titles, "types": types, "cl_variants": cl_variants, "collector": cl_best,
                "name_hits": name_hits, "cl_hits": cl_hits, "strength": strength}

    def _titles_online(self, rd: dict, idx: "NameIndex", res: ScanResult) -> None:
        """Card database not downloaded: the title can only match the few names known locally, so a title read
        perfectly still finds nothing. Ask Scryfall's fuzzy name search about the best readings (two at most)."""
        if not idx.small or (rd["name_hits"] and rd["name_hits"][0][1] >= 80):
            return
        hits, asked = dict(rd["name_hits"]), 0
        for t in rd["titles"]:
            if asked >= 2 or len(NameIndex.norm(t).replace(" ", "")) < 4:
                continue
            asked += 1
            try:
                found = self.catalog.client.named(t)
            except Exception as e:                      # offline: carry on with what is known locally
                import logging
                logging.getLogger("mtgstack.scanner").info("could not look up the title online: %s", e)
                break
            if not found or not found.get("name"):
                continue
            score = NameIndex.score(t, found["name"])
            if score > hits.get(found["name"], 0.0):
                hits[found["name"]] = score
                res.evidence.append(f"title looked up online: {found['name']} ({score:.0f}%)")
            if score >= 80:
                break
        rd["name_hits"] = sorted(hits.items(), key=lambda kv: -kv[1])

    def _decide(self, rd: dict, res: ScanResult, border: str, card: np.ndarray):
        from rapidfuzz import fuzz
        titles, types, name_hits, cl_hits = rd["titles"], rd["types"], rd["name_hits"], rd["cl_hits"]
        top_name, top_score = (name_hits[0] if name_hits else (None, 0.0))
        res.evidence.append(f"title read as {titles[:3] or ['(nothing legible)']}")
        if top_name:
            res.evidence.append(f"best title match: {top_name} ({top_score:.0f}%)")

        def title_sim(name: str) -> float:
            return max((NameIndex.score(t, name) for t in titles), default=0.0)

        def type_sim(type_line: str | None) -> float:
            return max((fuzz.partial_ratio(t.lower(), (type_line or "").lower()) for t in types), default=0.0)

        # 1. collector line that agrees with the title -> exact printing
        for p in cl_hits:
            sim = title_sim(p["name"])
            if sim >= 60:
                res.confidence = 0.97 if sim >= 80 and not p["_code_fixed"] else 0.9
                res.evidence.append(f"collector line {p['set_code'].upper()} #{p['collector_number']} -> {p['name']}, "
                                    f"title agrees ({sim:.0f}%)" + (" [set code OCR-corrected]" if p["_code_fixed"] else ""))
                res.candidates = [_cand(p, res.confidence, "set + number, title agrees")]
                return p, None
        if cl_hits:
            res.evidence.append("collector line read as " + ", ".join(
                f"{p['set_code'].upper()} #{p['collector_number']} ({p['name']})" for p in cl_hits[:3])
                + " but the title doesn't match: ignoring it")

        # 2. title strong enough to name the card (type line breaks near-ties)
        if top_name and top_score >= 65:
            close = [(n, s) for n, s in name_hits if s >= top_score - 8][:5]
            if len(close) > 1 and types:
                scored = []
                for n, s in close:
                    pr = self.catalog.prints_by_name(n, fetch=False)
                    scored.append((s + 0.25 * type_sim(pr[0]["type_line"] if pr else ""), n, s))
                scored.sort(reverse=True)
                if scored[0][1] != top_name:
                    res.evidence.append(f"type line {types[0]!r} favours {scored[0][1]} over {top_name}")
                top_name, top_score = scored[0][1], scored[0][2]
            cands = self.catalog.prints_by_name(top_name, fetch=NameIndex.get(self.catalog).small)
            if not cands:
                return None, f"{top_name} isn't in the card database"
            ts = type_sim(cands[0]["type_line"])
            if types and ts < 50 and top_score < 80:
                res.evidence.append(f"WARNING: type line {types[0]!r} doesn't look like {cands[0]['type_line']!r}")
            # narrow the printing with whatever the collector line got right (set code or number)
            narrowed = cands
            for v in rd["cl_variants"]:
                if v.set_code:
                    same = [c for c in narrowed if c["set_code"] in NameIndex.get(self.catalog).set_candidates(v.set_code)]
                    if same:
                        narrowed = same
                        res.evidence.append(f"set {same[0]['set_code'].upper()} from the collector line")
                        break
            for v in rd["cl_variants"]:
                if v.number:
                    same = [c for c in narrowed if c["collector_number"].lstrip("0") == v.number.lstrip("0")]
                    if same:
                        narrowed = same
                        break
            lang = [c for c in narrowed if c["lang"] == res.language] or narrowed
            by_border = [c for c in lang if c["border_color"] == border]
            pool = by_border if by_border and len(by_border) < len(lang) else lang
            ranked = self._rank_by_art(card, pool)
            res.candidates = ranked + [_cand(c, 0.0, "other printing") for c in cands
                                       if c["id"] not in {r["printing_id"] for r in ranked}][:30]
            base = 0.9 if top_score >= 85 else 0.75 if top_score >= 75 else 0.6
            if len(pool) > 1:
                margin = ranked[0]["score"] - ranked[1]["score"]
                base = min(base, 0.5 + margin) if self.fetch_images else min(base, 0.7)
            res.confidence = round(base, 2)
            res.evidence.append(f"identified by title: {top_name} ({top_score:.0f}%), "
                                f"{len(pool)} candidate printing(s)")
            return self.catalog.get(ranked[0]["printing_id"]), None

        # 3. not convincing: offer what we have, don't guess
        res.confidence = 0.0
        for n, s in name_hits[:5]:
            for c in self.catalog.prints_by_name(n, fetch=False)[:1]:
                res.candidates.append(_cand(c, s / 100, f"title looks {s:.0f}% like this"))
        for p in cl_hits[:3]:
            res.candidates.append(_cand(p, 0.3, "collector line (title disagrees)"))
        return None, ("couldn't read the card confidently: retake with the title and bottom-left text in focus, "
                      "or pick it from the suggestions")

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
            data = fetch_image(url)
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return None
            ref = detect.warp(img, corners=np.array([[0, 0], [img.shape[1] - 1, 0],
                                                     [img.shape[1] - 1, img.shape[0] - 1], [0, img.shape[0] - 1]],
                                                    np.float32))
            return markers.art_hash(ref)
        except Exception:
            return None


class NameIndex:
    """Card names + set codes from the local catalog, cached, for matching noisy OCR."""
    _cache: dict = {}

    def __init__(self, catalog: Catalog):
        conn = catalog.conn
        n = conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0]
        self.small = n < 20000 and catalog.online           # catalog not downloaded: allow online lookups
        names = [r[0] for r in conn.execute("SELECT DISTINCT name FROM printings")]
        # match against each face of double-faced cards too ("A // B" -> "A", "B")
        self.keys, self.names = [], []
        for nm in names:
            for k in {nm, *[f.strip() for f in nm.split("//")]}:
                self.keys.append(self.norm(k))
                self.names.append(nm)
        self.sets = {r[0] for r in conn.execute("SELECT DISTINCT set_code FROM printings")}
        self.n = n

    @classmethod
    def get(cls, catalog: Catalog) -> "NameIndex":
        n = catalog.conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0]
        key = id(catalog.conn)
        if key not in cls._cache or cls._cache[key].n != n:
            cls._cache[key] = cls(catalog)
        return cls._cache[key]

    @staticmethod
    def norm(s: str) -> str:
        import re
        return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("æ", "ae")).strip()

    @staticmethod
    def score(ocr: str, name: str) -> float:
        """0-100: edit-distance similarity over the LONGER string, so short garbage can't match a long name
        ('un ay' vs 'Aunt May' = 62) while OCR typos still score high ('Trainmg Cenler' = 87)."""
        from rapidfuzz.distance import Levenshtein
        a, b = NameIndex.norm(ocr), NameIndex.norm(name)
        if not a or not b:
            return 0.0
        sim = lambda x, y: 100.0 * Levenshtein.normalized_similarity(x, y)
        best = sim(a, b)
        for face in (f.strip() for f in name.split("//")):
            best = max(best, sim(a, NameIndex.norm(face)))
        # OCR often adds junk at the ends (mana symbols, frame art): allow a contained match if long enough
        if len(b) >= 6 and b in a:
            best = max(best, 90.0)
        return float(best)

    def match(self, ocr: str, limit: int = 5) -> list[tuple[str, float]]:
        from rapidfuzz import fuzz, process
        q = self.norm(ocr)
        if len(q.replace(" ", "")) < 3:
            return []
        out: dict[str, float] = {}
        if self.keys:
            for _, _, i in process.extract(q, self.keys, scorer=fuzz.ratio, limit=limit * 3):
                nm = self.names[i]
                out[nm] = max(out.get(nm, 0.0), self.score(ocr, nm))
        return sorted(out.items(), key=lambda kv: -kv[1])[:limit]

    CONFUSABLE = {"0": "O", "O": "0", "1": "I", "I": "1", "L": "1", "5": "S", "S": "5", "8": "B", "B": "8",
                  "2": "Z", "Z": "2", "6": "G", "G": "6", "Q": "O", "D": "O", "U": "V", "V": "U"}

    def set_candidates(self, code: str | None) -> list[str]:
        """The OCR'd set code if real, else real codes one or two OCR-confusions away."""
        if not code:
            return []
        code = code.upper()
        if code.lower() in self.sets or self.small:
            return [code.lower()]
        out = []
        for i, ch in enumerate(code):
            if ch in self.CONFUSABLE:
                c1 = code[:i] + self.CONFUSABLE[ch] + code[i + 1:]
                if c1.lower() in self.sets:
                    out.append(c1.lower())
                for j, ch2 in enumerate(c1):
                    if j > i and ch2 in self.CONFUSABLE:
                        c2 = c1[:j] + self.CONFUSABLE[ch2] + c1[j + 1:]
                        if c2.lower() in self.sets:
                            out.append(c2.lower())
        return list(dict.fromkeys(out))


def _debug_images(card: np.ndarray) -> dict:
    """What the scanner looked at, as small JPEG data URIs (shown in the UI / saved by the Scanner Lab)."""
    import base64
    import cv2

    def uri(img, width):
        h, w = img.shape[:2]
        img = cv2.resize(img, (width, max(1, int(h * width / w))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

    return {"card": uri(card, 300), "title": uri(detect.crop(card, "name"), 420),
            "type_line": uri(detect.crop(card, "type_line"), 420), "collector": uri(detect.crop(card, "collector"), 420)}


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
