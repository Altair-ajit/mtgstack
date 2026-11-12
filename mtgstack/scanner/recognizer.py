"""Picture-based card recognition: embed the rectified card with a small ONNX model and look it up in an index
of every printing's Scryfall image (docs/scanner-model-plan.md).

The model answers "which artwork is this"; printings that share the artwork are told apart afterwards by the
collector line (see resolve.py). It is optional: without the model files or onnxruntime the scanner uses OCR only.

Model files (directory from MTGSTACK_MODEL, default ~/.mtgstack/model):
    embedder.onnx, embeddings.bin, embeddings-ids.json   (scripts/build_index.py writes this directory)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .modeldata import INPUT_H, INPUT_W, key_printing, quantize, read_index

log = logging.getLogger("mtgstack.recognizer")

# Cosine similarity of the best match and its lead over the best *other* artwork. Set from synthetic photos
# (scripts/build_index.py prints the similarity of right and wrong matches); re-tune on Lab photos.
CONFIDENT_SIM = 0.65
CONFIDENT_MARGIN = 0.10
# Below "confident" but still a likely answer: offered as the suggestion when reading the text found nothing
# better. On 76 real photos (docs/scanner-model-plan.md) every wrong match was under one of these, every right one over.
PLAUSIBLE_SIM = 0.50
PLAUSIBLE_MARGIN = 0.11


@dataclass
class Match:
    """Result of one lookup. `printings` are all printings showing the best-matching artwork."""
    printings: list[str] = field(default_factory=list)
    score: float = 0.0                  # cosine similarity of the best match
    margin: float = 0.0                 # lead over the best different artwork
    others: list[tuple[str, float]] = field(default_factory=list)   # runner-up artworks: (a printing id, score)
    model_version: str = ""

    @property
    def confident(self) -> bool:
        return bool(self.printings) and self.score >= CONFIDENT_SIM and self.margin >= CONFIDENT_MARGIN

    @property
    def plausible(self) -> bool:
        return bool(self.printings) and self.score >= PLAUSIBLE_SIM and self.margin >= PLAUSIBLE_MARGIN

    def to_dict(self) -> dict:
        return {"printings": self.printings[:40], "score": round(self.score, 3), "margin": round(self.margin, 3),
                "confident": self.confident, "model_version": self.model_version,
                "others": [{"printing_id": p, "score": round(s, 3)} for p, s in self.others]}


def match_from_hints(hints: list[dict], model_version: str = "") -> Match:
    """Build a Match from neighbours computed elsewhere (the phone runs the model in JS and sends
    [{"printing_id", "score", "group"}], best first). Entries without a group are their own artwork."""
    if not hints:
        return Match(model_version=model_version)
    top = hints[0]
    g0 = top.get("group", top["printing_id"])
    m = Match(score=float(top["score"]), margin=float(top["score"]), model_version=model_version)
    seen_other = set()
    for h in hints:
        g = h.get("group", h["printing_id"])
        if g == g0:
            if h["printing_id"] not in m.printings:
                m.printings.append(h["printing_id"])
        elif g not in seen_other:
            seen_other.add(g)
            if len(m.others) < 5:
                m.others.append((h["printing_id"], float(h["score"])))
    if m.others:
        m.margin = m.score - m.others[0][1]
    return m


def preprocess(card_bgr: np.ndarray) -> np.ndarray:
    """Rectified card (BGR, any size, portrait) -> model input [1, 3, H, W] float32 RGB 0..1."""
    import cv2
    img = cv2.resize(card_bgr, (INPUT_W, INPUT_H), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255.0


class Recognizer:
    def __init__(self, model_dir: str | Path):
        import onnxruntime as ort
        d = Path(model_dir)
        self.session = ort.InferenceSession(str(d / "embedder.onnx"), providers=["CPUExecutionProvider"])
        vecs, meta = read_index(d)
        self.index = vecs.astype(np.float32)
        self.keys: list[str] = meta["keys"]
        self.groups = np.asarray(meta["groups"], np.int32)
        self.model_version: str = meta.get("model_version", "")
        self._by_group: dict[int, list[int]] | None = None

    def embed(self, card_bgr: np.ndarray) -> np.ndarray:
        return self.session.run(None, {"image": preprocess(card_bgr)})[0][0]

    def match(self, card_bgr: np.ndarray, others: int = 5) -> Match:
        q = quantize(self.embed(card_bgr)[None])[0].astype(np.float32)
        sims = self.index @ q / (127.0 * 127.0)
        best = int(sims.argmax())
        g0 = int(self.groups[best])
        if self._by_group is None:
            self._by_group = {}
            for i, g in enumerate(self.groups.tolist()):
                self._by_group.setdefault(g, []).append(i)
        rows = sorted(self._by_group[g0], key=lambda i: -sims[i])
        m = Match(printings=list(dict.fromkeys(key_printing(self.keys[i]) for i in rows)),
                  score=float(sims[best]), margin=float(sims[best]), model_version=self.model_version)
        rest = np.where(self.groups == g0, -2.0, sims)
        seen = set()
        for i in np.argsort(-rest)[:others * 8]:
            g = int(self.groups[i])
            if g in seen or rest[i] <= -2.0:
                continue
            seen.add(g)
            m.others.append((key_printing(self.keys[i]), float(rest[i])))
            if len(m.others) >= others:
                break
        if m.others:
            m.margin = m.score - m.others[0][1]
        return m


_default: dict = {}


def model_dir() -> Path:
    return Path(os.environ.get("MTGSTACK_MODEL") or Path.home() / ".mtgstack" / "model")


def load_default() -> Recognizer | None:
    """The installed model, or None when there isn't one (or onnxruntime is missing). Cached per directory."""
    d = model_dir()
    if str(d) not in _default:
        rec = None
        if (d / "embeddings-ids.json").exists():
            try:
                rec = Recognizer(d)
                log.info("card recognition model %s: %d images", rec.model_version, len(rec.keys))
            except Exception as e:                       # missing onnxruntime, bad files: fall back to OCR
                log.warning("card recognition model in %s not usable (%s: %s); using OCR only", d, type(e).__name__, e)
        _default[str(d)] = rec
    return _default[str(d)]
