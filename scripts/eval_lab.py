#!/usr/bin/env python3
"""Measure the recognition model on real photos from the Scanner Lab.

    python scripts/eval_lab.py --model ~/mtg-data/runs/v1/model --scans scan-data
    python scripts/eval_lab.py --model ... --scans ~/Downloads/mtgstack-scans.zip      # Lab -> Export from the phone

Uses every scan whose label has a confirmed `truth.printing_id`: finds and rectifies the card with the app's own
detector, embeds it, and reports top-1 by artwork (did the model find the right picture) and by printing id
(before any collector-line disambiguation), plus where the confidence thresholds fall.
These photos are for evaluation only: don't train on them without a held-out split.

Needs only the app's environment plus onnxruntime (no torch).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mtgstack.scanner import detect, recognizer  # noqa: E402
from mtgstack.scanner.modeldata import key_printing  # noqa: E402


def labelled_scans(src: Path):
    """Yield (scan id, [photo paths], truth dict) from a scans directory or an exported zip."""
    if src.is_file():
        tmp = Path(tempfile.mkdtemp(prefix="mtgstack-lab-"))
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        src = tmp / "scans" if (tmp / "scans").is_dir() else tmp
    for d in sorted(p for p in src.iterdir() if (p / "scan.json").is_file()):
        data = json.loads((d / "scan.json").read_text())
        truth = (data.get("label") or {}).get("truth") or {}
        photos = sorted(d.glob("photo_*.jpg"))
        if truth.get("printing_id") and photos:
            yield data, photos, truth


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True, help="directory with embedder.onnx + embeddings.bin + embeddings-ids.json (<run>/model)")
    ap.add_argument("--scans", default="scan-data", help="scans directory or Lab export zip")
    ap.add_argument("--out", default="", help="write per-scan results as JSON")
    a = ap.parse_args()

    rec = recognizer.Recognizer(Path(a.model).expanduser())
    group_of = {}
    for key, g in zip(rec.keys, rec.groups.tolist()):
        group_of.setdefault(key_printing(key), set()).add(g)

    rows = []
    for data, photos, truth in labelled_scans(Path(a.scans).expanduser()):
        tid = truth["printing_id"]
        try:
            card = detect.warp(detect.load(photos[0].read_bytes()))
            m = rec.match(card)
        except Exception as e:
            rows.append({"id": data["id"], "truth": truth.get("name"), "error": f"{type(e).__name__}: {e}"})
            continue
        top_groups = set().union(*(group_of.get(p, set()) for p in m.printings[:1]))
        rows.append({"id": data["id"], "truth": truth.get("name"), "in_index": tid in group_of,
                     "art_ok": bool(group_of.get(tid, set()) & top_groups), "printing_ok": m.printings[:1] == [tid],
                     "printing_in_pool": tid in m.printings, "pool": len(m.printings),
                     "score": round(m.score, 3), "margin": round(m.margin, 3), "confident": m.confident,
                     "was_recognizer": data.get("recognizer", "ocr"),
                     "ocr_was_right": "card" not in ((data.get("label") or {}).get("wrong_fields") or [])})

    ok = [r for r in rows if "error" not in r]
    if not ok:
        print(f"no labelled scans with a confirmed printing in {a.scans} ({len(rows)} unreadable). "
              "Scan some cards, label them in the Lab, then run this again.")
        return
    pct = lambda xs: f"{100 * sum(xs) / max(len(xs), 1):.1f}%"
    conf = [r for r in ok if r["confident"]]
    print(f"model {rec.model_version}: {len(ok)} labelled photos ({len(rows) - len(ok)} failed to load, "
          f"{sum(not r['in_index'] for r in ok)} of a printing that isn't in the index)")
    print(f"  right artwork (top-1)            {pct([r['art_ok'] for r in ok])}")
    print(f"  right printing before OCR        {pct([r['printing_ok'] for r in ok])}")
    print(f"  right printing among same-art    {pct([r['printing_in_pool'] for r in ok])}")
    print(f"  the OCR scanner had the card     {pct([r['ocr_was_right'] for r in ok])}")
    print(f"  confident matches                {len(conf)}/{len(ok)}, of which right {pct([r['art_ok'] for r in conf])}")
    right, wrong = [r["score"] for r in ok if r["art_ok"]], [r["score"] for r in ok if not r["art_ok"]]
    if right:
        print(f"  similarity when right            median {np.median(right):.2f}, 5th percentile {np.percentile(right, 5):.2f}")
    if wrong:
        print(f"  similarity when wrong            median {np.median(wrong):.2f}, 95th percentile {np.percentile(wrong, 95):.2f}")
        for r in sorted((r for r in ok if not r["art_ok"]), key=lambda r: -r["score"])[:15]:
            print(f"    missed {r['id']}  {r['truth']}  score {r['score']} margin {r['margin']}")
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
