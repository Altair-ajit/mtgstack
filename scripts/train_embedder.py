#!/usr/bin/env python3
"""Train the card embedder (docs/scanner-model-plan.md).

    ~/mtg-data/.venv-train/bin/python scripts/train_embedder.py --data ~/mtg-data --name v1

Each step pairs a synthetic phone photo of a card with its clean Scryfall image. Two losses:
  * photo -> reference retrieval inside the batch (batches are same-set chunks, so negatives are hard);
  * CosFace over illustration classes, which sees every class as a negative on every step.
3% of illustrations are held out entirely and used to measure how well the model handles art it never saw.

Writes ~/mtg-data/runs/<name>/{train.log, metrics.jsonl, last.pt, best.pt}; --resume continues from last.pt.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import torch

from embedlib import (CardDataset, CosFace, Embedder, SetBatchSampler, embed_rows, is_holdout, load_rows,
                      pair_loss, photo_augment, to_input, top1)
from mtgstack.scanner.modeldata import EMBED_DIM


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="~/mtg-data")
    ap.add_argument("--name", default="v1")
    ap.add_argument("--backbone", default="mobilenetv3_large_100")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=160, help="photo/reference pairs per step (160 ~ 11 GB)")
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="use only the first N images (smoke test)")
    ap.add_argument("--eval-queries", type=int, default=4000)
    ap.add_argument("--eval-gallery", type=int, default=30000, help="references in the per-epoch eval (0 = all)")
    ap.add_argument("--cosface-weight", type=float, default=0.5)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    data = Path(a.data).expanduser()
    run = data / "runs" / a.name
    run.mkdir(parents=True, exist_ok=True)
    logf = open(run / "train.log", "a")

    def log(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    dev = "cuda"
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = torch.backends.cudnn.allow_tf32 = True

    rows = load_rows(data, a.limit)
    groups = sorted({r["group"] for r in rows})
    gid = {g: i for i, g in enumerate(groups)}
    held = {g for g in groups if is_holdout(g)}
    train_rows = [r for r in rows if r["group"] not in held]
    hold_rows = [r for r in rows if r["group"] in held]
    classes = sorted({r["group"] for r in train_rows})
    cid = {g: i for i, g in enumerate(classes)}
    log(f"{len(rows)} images, {len(groups)} illustrations; train {len(train_rows)} images / {len(classes)} classes; "
        f"held out {len(hold_rows)} images / {len(held)} illustrations")

    # fixed eval sets
    rng = np.random.default_rng(0)
    pick = lambda rs, n: [rs[i] for i in sorted(rng.choice(len(rs), min(n, len(rs)), replace=False))]
    q_seen, q_held = pick(train_rows, a.eval_queries), pick(hold_rows, a.eval_queries)
    q_keys = {r["key"] for r in q_seen + q_held}
    others = [r for r in rows if r["key"] not in q_keys]
    gallery = q_seen + q_held + (pick(others, a.eval_gallery) if a.eval_gallery else others)

    def evaluate(model, gal=None):
        gal = gal or gallery
        g_emb = embed_rows(model, gal, data, dev, workers=a.workers)
        gg = [gid[r["group"]] for r in gal]
        out = {}
        for name, qs in (("seen", q_seen), ("held", q_held)):
            q_emb = embed_rows(model, qs, data, dev, photo_seed=7, workers=a.workers)
            out[name] = round(100 * top1(q_emb, g_emb, [gid[r["group"]] for r in qs], gg), 2)
        return out

    model = Embedder(a.backbone, pretrained=True).to(dev).to(memory_format=torch.channels_last)
    head = CosFace(EMBED_DIM, len(classes)).to(dev)
    ds = CardDataset(train_rows, data, [cid[r["group"]] for r in train_rows])
    sampler = SetBatchSampler(train_rows, a.batch)
    dl = torch.utils.data.DataLoader(ds, batch_sampler=sampler, num_workers=a.workers, pin_memory=True,
                                     persistent_workers=True, prefetch_factor=4)
    steps = len(sampler) * a.epochs
    opt = torch.optim.AdamW([{"params": model.parameters(), "lr": a.lr, "weight_decay": 0.01},
                             {"params": head.parameters(), "lr": a.lr * 10, "weight_decay": 0.0}])
    warm = max(1, min(len(sampler), 500))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda t: min(1.0, (t + 1) / warm) * (0.02 + 0.98 * 0.5 * (1 + math.cos(math.pi * min(t, steps) / steps))))
    scaler = torch.amp.GradScaler("cuda")
    start, best = 0, -1.0

    if a.resume and (run / "last.pt").exists():
        ck = torch.load(run / "last.pt", map_location=dev, weights_only=False)
        model.load_state_dict(ck["model"])
        head.load_state_dict(ck["head"])
        opt.load_state_dict(ck["opt"])
        sched.load_state_dict(ck["sched"])
        scaler.load_state_dict(ck["scaler"])
        start, best = ck["epoch"] + 1, ck.get("best", -1.0)
        log(f"resumed after epoch {ck['epoch']}")
    else:
        log(f"baseline (ImageNet weights, untrained head) {evaluate(model)}")

    for epoch in range(start, a.epochs):
        model.train()
        t0, seen, tot = time.time(), 0, np.zeros(2)
        # ease the augmentation in over the first epochs: the pretrained net first learns what a card is
        strength = min(1.0, 0.5 + 0.5 * epoch / max(1, min(4, a.epochs // 4)))
        for step, (clean, src, labels, _) in enumerate(dl):
            clean, src, labels = clean.to(dev, non_blocking=True), src.to(dev, non_blocking=True), labels.to(dev)
            with torch.no_grad():
                x = torch.cat([photo_augment(src, strength=strength), to_input(clean)])
                x = x.contiguous(memory_format=torch.channels_last)
            with torch.autocast("cuda"):
                emb = model(x)
            photo, ref = emb[:len(labels)], emb[len(labels):]
            l_pair = pair_loss(photo, ref, labels)
            l_cls = head(emb, torch.cat([labels, labels]))
            loss = l_pair + a.cosface_weight * l_cls
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            tot += [l_pair.item(), l_cls.item()]
            seen += len(labels)
            if step % 100 == 0:
                log(f"epoch {epoch} step {step}/{len(sampler)} pair {l_pair.item():.3f} cls {l_cls.item():.3f} "
                    f"lr {sched.get_last_lr()[0]:.2e} {seen / (time.time() - t0):.0f} pairs/s")
        m = evaluate(model)
        n = max(step + 1, 1)
        rec = {"epoch": epoch, "pair": round(tot[0] / n, 4), "cls": round(tot[1] / n, 4), "top1_seen": m["seen"],
               "top1_held": m["held"], "minutes": round((time.time() - t0) / 60, 1)}
        log(json.dumps(rec))
        with open(run / "metrics.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        score = (m["seen"] + m["held"]) / 2
        best = max(best, score)
        ck = {"model": model.state_dict(), "head": head.state_dict(), "opt": opt.state_dict(),
              "sched": sched.state_dict(), "scaler": scaler.state_dict(), "epoch": epoch, "best": best,
              "backbone": a.backbone, "metrics": rec, "name": a.name, "args": vars(a)}
        torch.save(ck, run / "last.pt.part")
        (run / "last.pt.part").replace(run / "last.pt")
        if score >= best:
            torch.save({k: ck[k] for k in ("model", "backbone", "epoch", "metrics", "name", "args")}, run / "best.pt")

    final = evaluate(model, rows)
    log(f"final, every image as reference ({len(rows)}): {final}")
    (run / "final.json").write_text(json.dumps({"gallery": len(rows), **final}))


if __name__ == "__main__":
    main()
