"""Shared data definitions for the card-recognition model (docs/scanner-model-plan.md).

Used by the training scripts in `scripts/` and by the runtime recognizer, so the image list, the grouping key and
the index file format are defined in exactly one place. Only needs numpy.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import numpy as np

# Network input (width, height): keeps the 63x88 mm card aspect.
INPUT_W, INPUT_H = 224, 312
EMBED_DIM = 128
# Pixel normalisation baked into the exported model; listed here for the training code.
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)

_NO_IMAGE = {"missing", "placeholder"}


def image_rows(card: dict) -> Iterator[dict]:
    """Scryfall card object -> one row per physical card face that has its own picture.

    Paper printings only. `key` names the image file (`<id>` for the front, `<id>.<n>` for other faces);
    `group` is what the embedder treats as one class: the illustration, so reprints of the same art land together
    and printing-level resolution happens afterwards.
    """
    if card.get("object") not in (None, "card") or "paper" not in (card.get("games") or []):
        return
    if card.get("image_status") in _NO_IMAGE:
        return
    base = {"id": card["id"], "name": card.get("name"), "set": (card.get("set") or "").lower(),
            "number": card.get("collector_number"), "lang": card.get("lang", "en"),
            "oracle_id": card.get("oracle_id"), "frame": card.get("frame"),
            "border_color": card.get("border_color"), "full_art": bool(card.get("full_art")),
            "released_at": card.get("released_at"), "layout": card.get("layout")}
    top = card.get("image_uris") or {}
    if top.get("normal"):
        # one picture for the whole card (normal, split, adventure, flip ...): faces share it
        ill = card.get("illustration_id") or next(
            (f.get("illustration_id") for f in card.get("card_faces") or [] if f.get("illustration_id")), None)
        yield {**base, "key": card["id"], "face": 0, "url": top["normal"],
               "illustration_id": ill, "group": ill or "id:" + card["id"]}
        return
    for i, face in enumerate(card.get("card_faces") or []):
        url = (face.get("image_uris") or {}).get("normal")
        if not url or (i and card.get("layout") == "art_series"):
            continue                    # art-card backs are a shared info panel (or a placeholder), not artwork
        key = card["id"] if i == 0 else f"{card['id']}.{i}"
        ill = face.get("illustration_id")
        yield {**base, "key": key, "face": i, "url": url, "name": face.get("name") or base["name"],
               "oracle_id": base["oracle_id"] or face.get("oracle_id"),
               "illustration_id": ill, "group": ill or "id:" + key}


def read_manifest(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_placeholders(data_dir: str | Path) -> set[str]:
    """Keys whose downloaded file is a stock "image not available" picture (found by fetch_images.py)."""
    f = Path(data_dir) / "placeholders.txt"
    return set(f.read_text().split()) if f.exists() else set()


def image_path(data_dir: str | Path, key: str) -> Path:
    return Path(data_dir) / "images" / key[:2] / f"{key}.jpg"


# ---- index file -------------------------------------------------------------------------------------------
# embeddings.bin: N x D int8, row-major, each row = round(127 * unit vector).
# embeddings-ids.json: {"model_version", "dim", "count", "keys": [image key per row], "groups": [int per row]}
# Cosine similarity of two rows ~= dot(a, b) / 127^2.

def quantize(vectors: np.ndarray) -> np.ndarray:
    v = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return np.clip(np.rint(v * 127.0), -127, 127).astype(np.int8)


def write_index(out_dir: str | Path, vectors: np.ndarray, keys: list[str], groups: list[int],
                model_version: str) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    q = quantize(np.asarray(vectors, np.float32))
    if len(keys) != len(q) or len(groups) != len(q):
        raise ValueError("keys/groups must match the number of vectors")
    bin_path, ids_path = out / "embeddings.bin", out / "embeddings-ids.json"
    bin_path.write_bytes(q.tobytes())
    ids_path.write_text(json.dumps({"model_version": model_version, "dim": int(q.shape[1]), "count": len(keys),
                                    "keys": keys, "groups": groups}, separators=(",", ":")))
    return bin_path, ids_path


def read_index(index_dir: str | Path) -> tuple[np.ndarray, dict]:
    d = Path(index_dir)
    meta = json.loads((d / "embeddings-ids.json").read_text())
    vecs = np.fromfile(d / "embeddings.bin", dtype=np.int8).reshape(meta["count"], meta["dim"])
    return vecs, meta


def key_printing(key: str) -> str:
    """Image key -> printing id (drops the face suffix)."""
    return key.split(".", 1)[0]
