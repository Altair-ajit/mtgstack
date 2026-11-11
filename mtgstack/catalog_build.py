"""Build the prebuilt card database for the phone build (run daily by GitHub Actions, in the cloud).

    python -m mtgstack.cli build-catalog site/data [--cards cards.json] [--no-cardkingdom]

Writes  catalog-<version>.sqlite.gz  (every printing + today's TCGplayer/Card Kingdom prices + sets)  and
catalog.json  ({version, printings, size_gz, file, built_at}), which the app checks to offer updates.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import shutil
from pathlib import Path

from . import db
from .catalog import Catalog, upsert_cards


def build(out_dir: str | Path, cards_json: str | Path | None = None, cardkingdom: bool = True, log=print) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "catalog.sqlite"
    raw.unlink(missing_ok=True)
    conn = db.connect(":memory:", catalog=raw)          # catalog tables go to `raw`; user tables stay in memory
    if cards_json:
        upsert_cards(conn, json.loads(Path(cards_json).read_text()))
    else:
        last = [0]

        def progress(done, total, cards):
            if cards - last[0] >= 20000:
                last[0] = cards
                log(f"  {cards:,} printings ({done / 2**20:.0f}/{total / 2**20:.0f} MB)")
        Catalog(conn).sync_bulk(progress=progress)
    if cardkingdom:
        try:
            from . import prices
            log(f"  Card Kingdom: {prices.refresh_cardkingdom(conn):,} prices")
        except Exception as e:                           # nice-to-have; TCGplayer prices come with Scryfall
            log(f"  Card Kingdom prices skipped: {e}")
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM cat.printings").fetchone()[0]
    conn.execute("VACUUM cat")
    conn.close()

    now = dt.datetime.now(dt.timezone.utc)
    version = now.strftime("%Y-%m-%d.%H%M")
    for old in out.glob("catalog-*.sqlite.gz"):
        old.unlink()
    gz = out / f"catalog-{version}.sqlite.gz"
    with raw.open("rb") as src, gzip.open(gz, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    meta = {"version": version, "printings": n, "size": raw.stat().st_size, "size_gz": gz.stat().st_size,
            "file": gz.name, "built_at": now.isoformat(timespec="seconds")}
    raw.unlink()
    (out / "catalog.json").write_text(json.dumps(meta))
    log(f"catalog {version}: {n:,} printings, {meta['size'] / 2**20:.0f} MB -> {meta['size_gz'] / 2**20:.0f} MB gz")
    return meta
