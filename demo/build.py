"""Assemble the browser demo (Pyodide) into demo/dist from the real app files."""
import re, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "demo" / "dist"
shutil.rmtree(DIST, ignore_errors=True)
(DIST / "py" / "mtgstack").mkdir(parents=True)
for f in ("__init__", "db", "catalog", "collection", "query", "decks", "tcgplayer", "prices"):
    shutil.copy(ROOT / "mtgstack" / f"{f}.py", DIST / "py" / "mtgstack" / f"{f}.py")
for f in ("seed.py", "demo_api.py", "demo_cards.json"):
    shutil.copy(ROOT / "demo" / f, DIST / "py" / f)
shutil.copy(ROOT / "mtgstack" / "web" / "style.css", DIST / "style.css")
shutil.copy(ROOT / "mtgstack" / "web" / "app.js", DIST / "app.js")
shutil.copy(ROOT / "demo" / "boot.js", DIST / "boot.js")
# artifact hosting doesn't serve .py: bundle every Python/data file into one JSON
import json as _json
bundle = {str(f.relative_to(DIST / "py")): f.read_text() for f in sorted((DIST / "py").rglob("*")) if f.is_file()}
shutil.rmtree(DIST / "py")
(DIST / "app-bundle.json").write_text(_json.dumps(bundle))

html = (ROOT / "mtgstack" / "web" / "index.html").read_text()
html = html.replace('href="/static/style.css"', 'href="style.css"')
html = html.replace('<script src="/static/app.js"></script>', '<script src="boot.js"></script>')
html = html.replace("<title>mtgstack</title>", "<title>mtgstack demo</title>")
html = html.replace("<body>", """<body>
<div id="demo-banner">Live demo: the real mtgstack Python code running in your browser. Sample prices, resets on reload.
  The scanner and price refresh need the local install.</div>
<div id="boot"><div class="boot-box"><div class="brand">mtg<b>stack</b></div><p id="boot-msg">Starting Python…</p></div></div>""", 1)
(DIST / "index.html").write_text(html)
print("built", sorted(str(p.relative_to(DIST)) for p in DIST.rglob("*") if p.is_file()))
