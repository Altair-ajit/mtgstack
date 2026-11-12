#!/usr/bin/env bash
# Whole model build in one go: (wait for a running download,) refresh manifest, train, export, index.
#   scripts/train_all.sh v1 [extra train_embedder.py args]
# Logs: ~/mtg-data/runs/<name>/{train.log,pipeline.log}. Result: ~/mtg-data/runs/<name>/model
set -euo pipefail
NAME=${1:?run name}; shift || true
DATA=${MTG_DATA:-$HOME/mtg-data}
PY=$DATA/.venv-train/bin/python
cd "$(dirname "$0")"
mkdir -p "$DATA/runs/$NAME"
exec > >(tee -a "$DATA/runs/$NAME/pipeline.log") 2>&1

while pgrep -f "fetch_images.py --data" > /dev/null; do sleep 30; done
$PY fetch_images.py --data "$DATA" --manifest-only
$PY train_embedder.py --data "$DATA" --name "$NAME" "$@" 2>&1 | grep -v "Warning\|warn"
$PY export_onnx.py --data "$DATA" --run "$DATA/runs/$NAME" 2>&1 | grep -v "Warning\|warn"
$PY build_index.py --data "$DATA" --run "$DATA/runs/$NAME"
echo "pipeline done: $DATA/runs/$NAME/model"
