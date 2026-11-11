#!/usr/bin/env bash
# mtgstack launcher for WSL / Linux / macOS. The first run sets everything up.
#   ./run.sh            start the website at http://localhost:8765
#   ./run.sh --dev      ...and restart automatically when you edit the code
#   ./run.sh --phone    also reachable from your phone on the same Wi-Fi (HTTPS, so the live camera works)
#   ./run.sh --test     run the test suite
set -euo pipefail
cd "$(dirname "$0")"
DEV=0; PHONE=0; TEST=0; PORT=8765
for a in "$@"; do case "$a" in
  --dev) DEV=1 ;; --phone) PHONE=1 ;; --test) TEST=1 ;; --port=*) PORT="${a#--port=}" ;;
  *) echo "unknown option $a"; exit 2 ;; esac; done

is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

# --- Python ---------------------------------------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  command -v python3 >/dev/null || { echo "Python 3 isn't installed:  sudo apt install -y python3 python3-venv"; exit 1; }
  echo "First run: creating .venv ..."
  python3 -m venv .venv 2>/dev/null || { echo "Need the venv module:  sudo apt install -y python3-venv"; exit 1; }
fi
if [ ! -f .venv/.installed ] || [ pyproject.toml -nt .venv/.installed ]; then
  echo "Installing dependencies (a minute or two the first time) ..."
  .venv/bin/python -m pip install --upgrade pip --quiet
  .venv/bin/python -m pip install -e ".[dev]" --quiet
  touch .venv/.installed
fi

# --- Tesseract (only the scanner needs it) -----------------------------------------------------------
if ! command -v tesseract >/dev/null; then
  echo "WARNING: Tesseract OCR not found - everything works except card scanning."
  if [ "$(uname)" = Darwin ]; then echo "  install: brew install tesseract"; else echo "  install: sudo apt install -y tesseract-ocr"; fi
fi

if [ "$TEST" = 1 ]; then exec .venv/bin/python -m pytest -q; fi

# --- Start ----------------------------------------------------------------------------------------
ARGS=(-m mtgstack.cli serve --port "$PORT")
[ "$PHONE" = 1 ] && ARGS+=(--host 0.0.0.0 --https)
[ "$DEV" = 1 ] && ARGS+=(--reload)
URL="$([ "$PHONE" = 1 ] && echo https || echo http)://localhost:$PORT"
( sleep 3
  if is_wsl; then explorer.exe "$URL" >/dev/null 2>&1 || true      # opens your Windows browser
  elif command -v xdg-open >/dev/null; then xdg-open "$URL" >/dev/null 2>&1 || true
  elif command -v open >/dev/null; then open "$URL" || true; fi ) &
exec .venv/bin/python "${ARGS[@]}"
