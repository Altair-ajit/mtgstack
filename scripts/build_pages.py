#!/usr/bin/env python3
"""Assemble the phone build (a static site for GitHub Pages) - everything runs in the browser.

    python scripts/build_pages.py --out site --base /mtgstack/ [--pyodide-cache ~/.cache/pyodide]
    python -m mtgstack.cli build-catalog site/data          # card database + prices (separate, daily)

Layout of the output:
    index.html, 404.html, assets/, sw.js, manifest, icons   React app (VITE_LOCAL=1)
    py/worker.js                                             runs Python in a Web Worker
    py/pyodide-<ver>/                                        Pyodide core + only the packages we use
    py/wheels/python_multipart.whl, py/app.zip               pure-Python extras + the mtgstack package
    py/tesseract/                                            Tesseract OCR (WebAssembly) + English model
    version.json                                             build id (the app offers a reload when it changes)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYODIDE_VERSION = "0.29.5"
PACKAGES = ["fastapi", "sqlite3", "httpx", "pydantic", "opencv-python", "numpy"]
CORE = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]


def pyodide_dist(cache: Path) -> Path:
    d = cache / f"pyodide-{PYODIDE_VERSION}"
    if (d / "pyodide-lock.json").exists():
        return d
    cache.mkdir(parents=True, exist_ok=True)
    tar = cache / f"pyodide-{PYODIDE_VERSION}.tar.bz2"
    if not tar.exists():
        url = f"https://github.com/pyodide/pyodide/releases/download/{PYODIDE_VERSION}/pyodide-{PYODIDE_VERSION}.tar.bz2"
        print("downloading", url)
        urllib.request.urlretrieve(url, tar)
    with tarfile.open(tar) as t:
        t.extractall(cache / "x", filter="data")
    (cache / "x" / "pyodide").rename(d)
    return d


def closure(lock: dict, names: list[str]) -> set[str]:
    pk = lock["packages"]
    norm = {k.replace("_", "-").lower(): k for k in pk}
    out: set[str] = set()

    def add(n: str):
        k = norm[n.replace("_", "-").lower()]
        if k not in out:
            out.add(k)
            for dep in pk[k]["depends"]:
                add(dep)
    for n in names:
        add(n)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--base", default="/", help="URL path the site is served from, e.g. /mtgstack/")
    ap.add_argument("--pyodide-cache", default=str(Path.home() / ".cache" / "mtgstack-pyodide"))
    ap.add_argument("--build-id", default=os.environ.get("GITHUB_SHA", "")[:12] or dt.datetime.now().strftime("%Y%m%d%H%M%S"))
    ap.add_argument("--skip-frontend", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    base = "/" + a.base.strip("/") + "/" if a.base.strip("/") else "/"

    if not a.skip_frontend:
        env = dict(os.environ, VITE_LOCAL="1", VITE_BUILD=a.build_id, PAGES_BASE=base, PAGES_OUT=str(out))
        subprocess.run(["npm", "run", "build:pages"], cwd=ROOT / "frontend", env=env, check=True)
    shutil.copy(out / "index.html", out / "404.html")          # deep links (/mtgstack/decks/x) load the app

    py = out / "py"
    dist = pyodide_dist(Path(a.pyodide_cache))
    lock = json.loads((dist / "pyodide-lock.json").read_text())
    dst = py / f"pyodide-{PYODIDE_VERSION}"
    dst.mkdir(parents=True, exist_ok=True)
    for f in CORE:
        shutil.copy(dist / f, dst / f)
    for k in sorted(closure(lock, PACKAGES)):
        shutil.copy(dist / lock["packages"][k]["file_name"], dst / lock["packages"][k]["file_name"])

    wheels = py / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / ".wheels-tmp"
    subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:", "--quiet",
                    "python-multipart", "-d", str(tmp)], check=True)
    shutil.copy(next(tmp.glob("python_multipart-*.whl")), wheels / "python_multipart.whl")
    shutil.rmtree(tmp, ignore_errors=True)

    with zipfile.ZipFile(py / "app.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((ROOT / "mtgstack").rglob("*.py")):
            rel = f.relative_to(ROOT)
            if "__pycache__" not in rel.parts:
                z.write(f, rel.as_posix())

    tess = py / "tesseract"
    tess.mkdir(parents=True, exist_ok=True)
    nm = ROOT / "frontend" / "node_modules"
    for core in ("tesseract-core-simd-lstm", "tesseract-core-lstm"):
        for ext in (".js", ".wasm"):
            shutil.copy(nm / "tesseract.js-core" / f"{core}{ext}", tess / f"{core}{ext}")
    shutil.copy(nm / "@tesseract.js-data" / "eng" / "4.0.0_best_int" / "eng.traineddata.gz", tess / "eng.traineddata.gz")

    (out / "version.json").write_text(json.dumps({"build": a.build_id, "pyodide": PYODIDE_VERSION,
                                                   "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}))
    (out / ".nojekyll").write_text("")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"site ready: {out} ({size / 2**20:.0f} MB, base {base}, build {a.build_id})")


if __name__ == "__main__":
    main()
