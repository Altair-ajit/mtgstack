#!/usr/bin/env python3
"""Export a trained embedder to ONNX for the phone.

    ~/mtg-data/.venv-train/bin/python scripts/export_onnx.py --run ~/mtg-data/runs/v1

Writes into <run>/export:
    embedder.fp32.onnx   plain float32 export (reference)
    embedder.onnx        the one to ship: same network with weights stored as float16 (half the download,
                         cast back to float32 when the model loads, so it runs everywhere float32 does)
    model.json           version + input description
    embedder.int8.onnx   only with --int8: static QDQ quantization. MobileNetV3 loses most of its accuracy this
                         way (71% vs 99% top-1 on the pilot model), so it is not the default; build_index.py
                         measures whichever file you point it at.
Input: float32 [N, 3, 312, 224], RGB in 0..1 (normalisation is inside the model). Output: [N, 128] unit vectors.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from embedlib import CardDataset, load_checkpoint, load_rows, photo_augment, to_input
from mtgstack.scanner.modeldata import EMBED_DIM, INPUT_H, INPUT_W


def calibration_batches(data: Path, n: int, device: str, batch: int = 32):
    rows = load_rows(data)
    rng = np.random.default_rng(1)
    rows = [rows[i] for i in rng.choice(len(rows), min(n, len(rows)), replace=False)]
    ds = CardDataset(rows, data, [0] * len(rows))
    dl = torch.utils.data.DataLoader(ds, batch_size=batch, num_workers=4)
    g = torch.Generator(device=device).manual_seed(3)
    for clean, src, _, _ in dl:
        yield to_input(clean.to(device)).cpu().numpy()
        yield photo_augment(src.to(device), g).cpu().numpy()


def half_weights(src: Path, dst: Path, min_size: int = 64) -> None:
    """Store float32 weights as float16 with a Cast back to float32 in front of each. The graph still computes
    in float32; onnxruntime folds the casts when it loads the model."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    m = onnx.load(str(src))
    casts = []
    for init in list(m.graph.initializer):
        if init.data_type != TensorProto.FLOAT or np.prod(init.dims) < min_size:
            continue
        arr = numpy_helper.to_array(init)
        half = numpy_helper.from_array(arr.astype(np.float16), init.name + "_f16")
        casts.append(helper.make_node("Cast", [half.name], [init.name], to=TensorProto.FLOAT, name="cast_" + init.name))
        m.graph.initializer.remove(init)
        m.graph.initializer.append(half)
    for i, node in enumerate(casts):
        m.graph.node.insert(i, node)
    onnx.checker.check_model(m)
    onnx.save(m, str(dst))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="run directory containing best.pt / last.pt")
    ap.add_argument("--checkpoint", default="last.pt")
    ap.add_argument("--data", default="~/mtg-data")
    ap.add_argument("--int8", action="store_true", help="also write an int8 model (experimental, see above)")
    ap.add_argument("--calibration", type=int, default=600, help="images used to calibrate the int8 model")
    a = ap.parse_args()
    run, data = Path(a.run).expanduser(), Path(a.data).expanduser()
    out = run / "export"
    out.mkdir(exist_ok=True)

    model, ck = load_checkpoint(run / a.checkpoint)
    version = f"{ck.get('name', run.name)}-e{ck['epoch'] + 1}"
    fp32, ship = out / "embedder.fp32.onnx", out / "embedder.onnx"
    dummy = torch.rand(2, 3, INPUT_H, INPUT_W)
    torch.onnx.export(model, dummy, str(fp32), opset_version=17, input_names=["image"], output_names=["embedding"],
                      dynamic_axes={"image": {0: "n"}, "embedding": {0: "n"}}, do_constant_folding=True)
    half_weights(fp32, ship)

    import onnxruntime as ort
    ref = model(dummy).detach().numpy()
    files = [fp32, ship]
    for f in files:
        got = ort.InferenceSession(str(f), providers=["CPUExecutionProvider"]).run(None, {"image": dummy.numpy()})[0]
        print(f"{f.name}: {f.stat().st_size / 1e6:.1f} MB, cosine to torch {float((ref * got).sum(1).min()):.5f}")

    if a.int8:
        from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_static
        from onnxruntime.quantization.shape_inference import quant_pre_process

        class Reader(CalibrationDataReader):
            def __init__(self):
                dev = "cuda" if torch.cuda.is_available() else "cpu"
                self.it = iter(calibration_batches(data, a.calibration, dev))

            def get_next(self):
                b = next(self.it, None)
                return None if b is None else {"image": b}

        pre, int8 = out / "embedder.pre.onnx", out / "embedder.int8.onnx"
        quant_pre_process(str(fp32), str(pre))
        quantize_static(str(pre), str(int8), Reader(), quant_format=QuantFormat.QDQ, per_channel=True,
                        activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8)
        pre.unlink()
        print(f"{int8.name}: {int8.stat().st_size / 1e6:.1f} MB (check its accuracy with build_index.py --model)")

    (out / "model.json").write_text(json.dumps({
        "model_version": version, "backbone": ck["backbone"], "dim": EMBED_DIM,
        "input": {"name": "image", "shape": [1, 3, INPUT_H, INPUT_W], "format": "RGB float32 0..1, NCHW"},
        "metrics": ck.get("metrics")}, indent=1))
    print(f"model_version {version}")


if __name__ == "__main__":
    main()
