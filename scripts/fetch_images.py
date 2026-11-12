#!/usr/bin/env python3
"""Download every paper printing's Scryfall image for training the card-recognition model.

    python scripts/fetch_images.py --data ~/mtg-data            # everything (~8 GB, a few hours, resumable)
    python scripts/fetch_images.py --data ~/mtg-data --limit 500

Writes  <data>/default_cards.jsonl.gz   the bulk file (reused for a day)
        <data>/manifest.jsonl           one row per card face: key, id, group (illustration), set, number, url ...
        <data>/images/<k[:2]>/<key>.jpg 488x680 "normal" images

Polite by design: one shared rate limit (default 10 requests/second, Scryfall's guideline), a User-Agent, and
files already on disk are never requested again, so re-running only fetches new printings.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mtgstack.catalog import HEADERS, Scryfall, _bulk_cards  # noqa: E402
from mtgstack.scanner.modeldata import image_path, image_rows  # noqa: E402


class RateLimit:
    """At most `rate` acquisitions per second across all threads."""

    def __init__(self, rate: float):
        self.gap, self.next, self.lock = 1.0 / rate, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.gap
        if at > now:
            time.sleep(at - now)


def bulk_file(data: Path, http: httpx.Client, max_age_h: float = 24) -> Path:
    f = data / "default_cards.jsonl.gz"
    if f.exists() and time.time() - f.stat().st_mtime < max_age_h * 3600:
        return f
    url = Scryfall(http).bulk_url("default_cards")
    print(f"downloading bulk data: {url}", flush=True)
    tmp = f.with_suffix(".part")
    with http.stream("GET", url) as r, open(tmp, "wb") as out:
        r.raise_for_status()
        for chunk in r.iter_bytes(1 << 20):
            out.write(chunk)
    os.replace(tmp, f)
    return f


def build_manifest(data: Path, bulk: Path) -> list[dict]:
    def chunks():
        with open(bulk, "rb") as f:
            while True:
                b = f.read(1 << 20)
                if not b:
                    return
                yield b
    rows = [row for card in _bulk_cards(chunks()) for row in image_rows(card)]
    rows.sort(key=lambda r: r["key"])
    tmp = data / "manifest.jsonl.part"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, data / "manifest.jsonl")
    return rows


def fetch_one(http: httpx.Client, limiter: RateLimit, url: str, dest: Path, retries: int = 4) -> bool:
    for attempt in range(retries):
        limiter.wait()
        try:
            r = http.get(url)
            if r.status_code == 404:
                return False
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()
            if r.content[:2] != b"\xff\xd8":
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(r.content)
            os.replace(tmp, dest)
            return True
        except httpx.TransportError:
            time.sleep(2 ** attempt)
    return False


def find_placeholders(data: Path, rows: list[dict], min_groups: int = 3) -> list[str]:
    """Scryfall serves one stock picture for faces it has no scan of. Those files are byte-identical, so any
    content shared by several different illustrations is a placeholder, not a card."""
    import hashlib
    seen: dict[str, list[dict]] = {}
    for r in rows:
        f = image_path(data, r["key"])
        if f.exists():
            seen.setdefault(hashlib.md5(f.read_bytes()).hexdigest(), []).append(r)
    bad = sorted(r["key"] for rs in seen.values() if len({r["group"] for r in rs}) >= min_groups for r in rs)
    (data / "placeholders.txt").write_text("\n".join(bad))
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="~/mtg-data")
    ap.add_argument("--rate", type=float, default=10.0, help="max requests per second (keep <= 10)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="only fetch the first N missing images (for testing)")
    ap.add_argument("--manifest-only", action="store_true")
    a = ap.parse_args()
    data = Path(a.data).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    http = httpx.Client(headers={**HEADERS, "Accept": "*/*"}, timeout=60, follow_redirects=True)

    rows = build_manifest(data, bulk_file(data, http))
    groups = len({r["group"] for r in rows})
    todo = [r for r in rows if not image_path(data, r["key"]).exists()]
    print(f"manifest: {len(rows)} images, {len({r['id'] for r in rows})} printings, {groups} illustrations; "
          f"{len(todo)} to download", flush=True)
    if a.manifest_only:
        print(f"{len(find_placeholders(data, rows))} placeholder pictures ignored", flush=True)
        return
    if a.limit:
        todo = todo[:a.limit]

    limiter, done, failed, t0 = RateLimit(min(a.rate, 10.0)), 0, [], time.monotonic()
    lock = threading.Lock()

    def job(r):
        nonlocal done
        ok = fetch_one(http, limiter, r["url"], image_path(data, r["key"]))
        with lock:
            done += 1
            if not ok:
                failed.append(r["key"])
            if done % 1000 == 0 or done == len(todo):
                el = time.monotonic() - t0
                eta = (len(todo) - done) / max(done / el, 1e-9)
                print(f"{done}/{len(todo)}  {done / el:.1f}/s  eta {eta / 60:.0f} min  failed {len(failed)}", flush=True)

    with ThreadPoolExecutor(a.workers) as pool:
        list(pool.map(job, todo))
    (data / "failed.txt").write_text("\n".join(failed))
    print(f"done: {len(todo) - len(failed)} downloaded, {len(failed)} failed (see failed.txt)", flush=True)


if __name__ == "__main__":
    main()
