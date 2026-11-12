# Card recognition model — plan & handoff

Goal: replace OCR-first scanning with a small **image-embedding model** trained locally (RTX 4070 Ti Super, 16 GB,
in WSL), running **on the phone** in the browser. No paid APIs, no LLMs. Works offline.

## Why
Current scanner (OpenCV edges + Tesseract OCR in Pyodide) is slow on iPhone (~30 MB engine load, seconds per scan)
and fails on special frames (borderless, full art, showcase/comic, Secret Lair) where text isn't where OCR expects.
Matching the *whole picture* is robust to frames; it's how commercial scanner apps work.

## Architecture
1. **Detect + rectify** (existing `mtgstack/scanner/detect.py` logic; port to JS/OpenCV.js or keep in worker):
   find the card quad, warp to 488x680.
2. **Embed**: small CNN (start: MobileNetV3-Large or EfficientNet-B0, ImageNet weights) -> 128-d L2-normalized vector.
3. **Search**: cosine nearest-neighbour over the **index** of every printing's Scryfall image
   (~120k x 128 int8 ≈ 15 MB; brute force in JS ≈ tens of ms). Ship index with the daily catalog build.
4. **Disambiguate reprints with identical art** (same illustration, different set): the top-k neighbours share
   `illustration_id`. Resolve with a tiny second stage on the bottom-left collector line (set code + number) — either
   targeted OCR on that crop only, or a small classifier over the candidate sets. Also use the Lab's foil signal.
5. **Fallback**: keep the existing OCR scanner for when the model is unsure (distance gap below threshold).

## Data
- Source: Scryfall bulk `default_cards` (gzipped JSONL, `jsonl_download_uri` — see `catalog.py::_bulk_cards`),
  `image_uris.normal` (488x680 JPG) per printing; for DFCs use `card_faces[i].image_uris` (index both faces).
  Paper printings only (`games` contains `paper`). Keep `illustration_id` for grouping.
- Download politely: ≤10 req/s, User-Agent set, resumable, cache to `~/mtg-data/images/<id>.jpg` (~120k files,
  ~8 GB). Skip already-downloaded. Re-run incrementally for new sets.
- Labels: printing `id`. Group key for metric learning: `illustration_id` (same art = same class for the embedder;
  printing-level resolution happens in step 4).
- Real-world eval: Scanner Lab dataset (`scan-data/` from desktop, or *Lab -> Export* zip from the phone):
  photos + `scan.json` with user-confirmed `truth.printing_id`. Never train on it until we have a holdout split.

## Training
- PyTorch + timm. Metric learning: ArcFace / CosFace head over illustration classes (~35-40k classes), or
  supervised-contrastive with large batches. Mixed precision, batch 256, 224x312 input (keep card aspect).
- **Augmentations that mimic phone photos** (the key to real-world accuracy): perspective warp + re-rectify error
  (small residual skew, corner jitter like `tests/synth.py::corner_error`), random crop/zoom 90-100%, rotation 0/180,
  motion & defocus blur, JPEG artifacts, noise, colour temperature/white balance, brightness/contrast/gamma,
  specular glare blobs and holo/foil rainbow overlays, sleeve sheen, shadows, low light.
- Hard-negative mining: same-set siblings, same-name different-art printings.
- Expect: a few hours for a first good model on the 4070 Ti Super.

## Export & on-device
- Export to ONNX (opset 17), quantize to int8 (onnxruntime quantization) -> target ≤ 10 MB.
- Run with **onnxruntime-web** (WASM SIMD; WebGPU where available) in the page or a worker — not Pyodide.
- Index file: `site/data/embeddings-<catalog_version>.bin` (int8 vectors, row order = `embeddings-ids.json`)
  produced in the daily GitHub Action by running the model over images... **but** GitHub runners have no GPU and
  downloading 120k images daily is wasteful. Plan: build the full index locally once per model version, publish as
  a release asset; the daily Action only embeds *new* printings (CPU is fine for a few hundred images).

## Integration points in this repo
- `frontend/public/py/worker.js` / `frontend/src/local/boot.ts`: add a JS recognizer (ONNX) used by `/api/scan`
  before falling back to Python OCR; or a new page-side module that calls the API with `printing_id` hints.
- `mtgstack/scanner/resolve.py`: accept an external candidate list (printing ids + distances) and merge with OCR
  evidence; keep `ScanResult` shape so the Scan page, live session and Lab work unchanged.
- `mtgstack/scanlab.py`: add `recognizer` + `model_version` to each scan; Lab stats per recognizer so we can compare
  OCR vs model on the same photos. `RERUN` should be able to replay the model too.
- `scripts/` new: `fetch_images.py`, `train_embedder.py`, `export_onnx.py`, `build_index.py`, `eval_lab.py`.

## Milestones & acceptance
1. Data pipeline: images downloaded, manifest with id / illustration_id / set / number / faces.
2. Baseline: ImageNet-pretrained backbone, no training, index + kNN -> measure top-1 on Lab scans (sanity).
3. Trained embedder: top-1 illustration accuracy ≥ 98% on synthetic hold-out, ≥ 95% on Lab real photos.
4. Printing resolution: top-1 printing accuracy ≥ 95% on Lab photos incl. special frames.
5. On-device: ≤ 300 ms per scan on iPhone (embed + search), model ≤ 10 MB, index ≤ 20 MB.
6. Ship: Pages build serves model + index; Scanner Lab shows model vs OCR accuracy.

## Status (2026-11-12)
Done, on branch `scanner-model`:
- **Data** (`scripts/fetch_images.py`, `mtgstack/scanner/modeldata.py`): 111,690 images / 110,225 paper printings /
  53,978 illustrations in `~/mtg-data` (`manifest.jsonl`, `images/<k[:2]>/<key>.jpg`; back faces are `<id>.1`).
  Art-series backs are skipped, and byte-identical "not available" pictures are detected (`placeholders.txt`).
- **Training** (`scripts/train_embedder.py`, `scripts/embedlib.py`): MobileNetV3-Large -> 128-d. Each step pairs a
  synthetic phone photo (GPU augmentation: glare, foil rainbow, sheen, shadow, corner jitter/zoom/180 turn with table
  showing through, blur, low-res, white balance, exposure, noise, JPEG) with the clean image. Loss = in-batch
  photo->reference retrieval (batches are same-set chunks = hard negatives) + 0.5 x CosFace over illustrations.
  3% of illustrations are held out completely (`is_holdout`) to measure unseen art. Batch 160 pairs ~ 11 GB VRAM.
- **Export** (`scripts/export_onnx.py`): ONNX opset 17 with float16-stored weights: 8.8 MB, identical output.
  Static int8 quantization of MobileNetV3 wrecks accuracy (71% vs 99% on the pilot; SE/hard-swish ranges), so int8 is
  opt-in (`--int8`). To get a real int8 model: quantization-aware training or a ReLU backbone.
- **Index** (`scripts/build_index.py`): int8 vectors (no measurable loss), writes the deployable `<run>/model`
  directory and `report.json` (accuracy of the shipped model + int8 index on synthetic photos).
- **Runtime** (`mtgstack/scanner/recognizer.py`, `resolve.py`): picture match first; a confident match names the card
  and only the collector line is read to pick the printing; otherwise OCR runs and the match backs it up or
  questions it. `/api/scan` accepts client-computed `hints`. `ScanResult` gained `recognizer` + `model_version`.
- **Lab** (`scanlab.py`): each scan stores recognizer/model version; `stats().by_recognizer`;
  `POST /api/lab/rerun?ocr_only=true` to compare. `scripts/eval_lab.py` scores the model on labelled photos.

Not done: milestone 2/3/4 numbers on **real photos** (`scan-data/` is empty: scan and label cards in the Lab first,
then run `eval_lab.py` and tune `recognizer.CONFIDENT_SIM/MARGIN`), on-device inference (onnxruntime-web),
publishing model + index with the Pages build, embedding new printings in the daily Action, Lab UI for model vs OCR.

## Environment notes
- Dev happens in WSL (Ubuntu) on Windows; repo at `/mnt/c/Users/Rajit/Desktop/Projects/mtgstack`.
  For training speed keep image data on the Linux filesystem (`~/mtg-data`), not `/mnt/c`.
- GPU: check `nvidia-smi` inside WSL; install PyTorch with CUDA wheels (`pip install torch --index-url
  https://download.pytorch.org/whl/cu124` or current). Use a separate venv for training (`.venv-train`) so the app's
  deps stay light.
- Long jobs: run under `tmux` or `nohup` with logs in `~/mtg-data/runs/<name>/`; checkpoint every epoch.
- See `CLAUDE.md` for the app layout, phone build, and test commands (`python -m pytest -q`).
