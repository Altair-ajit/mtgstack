#!/usr/bin/env python3
"""Embed every downloaded card image with an exported ONNX model and write the search index.

    ~/mtg-data/.venv-train/bin/python scripts/build_index.py --run ~/mtg-data/runs/v1

Writes <run>/model: embedder.onnx + model.json (copied from <run>/export), embeddings.bin (N x 128 int8) and
embeddings-ids.json (row order, illustration group per row, model version). That directory is what the app loads
(MTGSTACK_MODEL) and what gets published. Also reports synthetic-photo accuracy with exactly what ships: this ONNX
model searched against the int8 index (report.json).
Runs on the GPU when onnxruntime-gpu is installed, otherwise on all CPU cores.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import numpy as np
import torch

from embedlib import CardDataset, is_holdout, load_rows, photo_augment, to_input
from mtgstack.scanner.modeldata import quantize, write_index


def session(path: Path):
    import onnxruntime as ort
    opts = ort.SessionOptions()
    providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
    return ort.InferenceSession(str(path), opts, providers=providers)


def embed(sess, rows, data, photo_seed=None, batch=128, workers=12, progress=""):
    """ONNX embeddings of clean images, or of one fixed synthetic photo per row."""
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    ds = CardDataset(rows, data, [0] * len(rows), jpeg_prob=0.3 if photo_seed is not None else 0.0)
    dl = torch.utils.data.DataLoader(ds, batch_size=batch, num_workers=workers,
                                     worker_init_fn=lambda i: np.random.seed(4321 + i))
    g = torch.Generator(device=dev).manual_seed(photo_seed) if photo_seed is not None else None
    out, t0 = [], time.time()
    for i, (clean, src, _, _) in enumerate(dl):
        x = photo_augment(src.to(dev), g) if g is not None else to_input(clean.to(dev))
        out.append(sess.run(None, {"image": x.cpu().numpy()})[0])
        if progress and i % 50 == 0:
            n = (i + 1) * batch
            print(f"{progress} {min(n, len(rows))}/{len(rows)}  {n / (time.time() - t0):.0f}/s", flush=True)
    return np.concatenate(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--model", default="embedder.onnx", help="file in <run>/export")
    ap.add_argument("--data", default="~/mtg-data")
    ap.add_argument("--out", default="", help="default <run>/model")
    ap.add_argument("--eval-queries", type=int, default=4000)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    run, data = Path(a.run).expanduser(), Path(a.data).expanduser()
    out = Path(a.out).expanduser() if a.out else run / "model"
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((run / "export" / "model.json").read_text())
    version = meta["model_version"] + ("-int8" if "int8" in a.model else "")
    shutil.copyfile(run / "export" / a.model, out / "embedder.onnx")
    (out / "model.json").write_text(json.dumps({**meta, "model_version": version}, indent=1))
    sess = session(run / "export" / a.model)
    print(f"{a.model} on {sess.get_providers()[0]}")

    rows = load_rows(data, a.limit)
    groups = sorted({r["group"] for r in rows})
    gid = {g: i for i, g in enumerate(groups)}
    vecs = embed(sess, rows, data, progress="index")
    g_ids = [gid[r["group"]] for r in rows]
    bin_path, ids_path = write_index(out, vecs, [r["key"] for r in rows], g_ids, version)
    print(f"{bin_path} {bin_path.stat().st_size / 1e6:.1f} MB, {ids_path.name} {ids_path.stat().st_size / 1e6:.1f} MB")

    # accuracy of the shipped pair (this model + int8 index) on synthetic photos
    index = torch.from_numpy(quantize(vecs)).float()
    gg = torch.tensor(g_ids)
    rng = np.random.default_rng(0)
    report = {"model": a.model, "model_version": version, "index_rows": len(rows)}
    for name, pool in (("seen", [r for r in rows if not is_holdout(r["group"])]),
                       ("held", [r for r in rows if is_holdout(r["group"])])):
        if not pool:
            continue
        qs = [pool[i] for i in sorted(rng.choice(len(pool), min(a.eval_queries, len(pool)), replace=False))]
        q = torch.from_numpy(embed(sess, qs, data, photo_seed=11))
        sims = q @ index.t() / 127.0
        top = sims.topk(2, dim=1)
        hit = gg[top.indices[:, 0]] == torch.tensor([gid[r["group"]] for r in qs])
        report[f"top1_{name}"] = round(100 * hit.float().mean().item(), 2)
        report[f"sim_right_{name}"] = round(top.values[:, 0][hit].mean().item(), 3)
        if (~hit).any():
            report[f"sim_wrong_{name}"] = round(top.values[:, 0][~hit].mean().item(), 3)
    print(json.dumps(report))
    (out / "report.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
