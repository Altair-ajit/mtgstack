"""Scanner Lab: every scan is kept with what the scanner saw and guessed, you label it right/wrong
(and what it really was), and the labelled set becomes a regression test for the scanner.

Storage is plain files so the dataset is easy to inspect, zip and share:

    $MTGSTACK_SCANS (default ~/.mtgstack/scans; run.sh uses <repo>/scan-data)
      <scan id>/
        photo_0.jpg ...        the original photo(s) as uploaded
        card.jpg title.jpg type_line.jpg collector.jpg   what the scanner cropped and read
        scan.json              {id, ts, scanner_version, recognizer, model_version,
                                result (guess + OCR readings + evidence), label}

`recognizer` says what identified the card ("ocr", "model" = the picture-recognition model, "model+ocr" = both),
so accuracy can be compared between them on the same photos.
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import re
import secrets
import threading
import zipfile
from collections import Counter
from pathlib import Path

SCANNER_VERSION = "2026.11.12-model5"


def scans_dir() -> Path:
    d = Path(os.environ.get("MTGSTACK_SCANS") or Path.home() / ".mtgstack" / "scans")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def _path(scan_id: str) -> Path:
    if not re.fullmatch(r"[0-9A-Za-z\-]+", scan_id):
        raise KeyError("bad scan id")
    p = scans_dir() / scan_id
    if not p.is_dir():
        raise KeyError(f"no scan {scan_id}")
    return p


def _read(scan_id: str) -> dict:
    return json.loads((_path(scan_id) / "scan.json").read_text())


def _write(scan_id: str, data: dict) -> None:
    (scans_dir() / scan_id / "scan.json").write_text(json.dumps(data, indent=1, default=str))


def save(images: list[bytes], result: dict, source: str = "scan") -> str:
    """Store a scan. `result` is ScanResult.to_dict() (its debug data URIs become JPEG files)."""
    sid = _new_id()
    d = scans_dir() / sid
    d.mkdir(parents=True)
    for i, b in enumerate(images):
        (d / f"photo_{i}.jpg").write_bytes(b)
    res = dict(result)
    for name, uri in (res.pop("debug", None) or {}).items():
        if isinstance(uri, str) and uri.startswith("data:image/jpeg;base64,"):
            (d / f"{name}.jpg").write_bytes(base64.b64decode(uri.split(",", 1)[1]))
    _write(sid, {"id": sid, "ts": dt.datetime.now().isoformat(timespec="seconds"), "source": source,
                 "scanner_version": SCANNER_VERSION, "recognizer": res.get("recognizer") or "ocr",
                 "model_version": res.get("model_version"), "photos": len(images), "result": res, "label": None})
    return sid


def summary(data: dict) -> dict:
    r, lab = data["result"], data.get("label")
    return {"id": data["id"], "ts": data["ts"], "source": data.get("source"),
            "recognizer": data.get("recognizer") or r.get("recognizer") or "ocr",
            "model_version": data.get("model_version") or r.get("model_version"),
            "guess": {"name": r.get("name"), "set_code": r.get("set_code"), "number": r.get("collector_number"),
                      "finish": r.get("finish"), "confidence": r.get("confidence"), "printing_id": r.get("printing_id")},
            "status": "unreviewed" if not lab else lab["verdict"] if lab["verdict"] in ("correct", "unreadable") else "wrong",
            "wrong_fields": (lab or {}).get("wrong_fields", []),
            "truth": (lab or {}).get("truth")}


def list_scans(status: str = "", limit: int = 200, offset: int = 0) -> dict:
    items = []
    for d in sorted(scans_dir().iterdir(), reverse=True):
        f = d / "scan.json"
        if f.is_file():
            try:
                s = summary(json.loads(f.read_text()))
            except (ValueError, KeyError):
                continue
            if not status or s["status"] == status:
                items.append(s)
    return {"total": len(items), "items": items[offset:offset + limit]}


def get(scan_id: str) -> dict:
    data = _read(scan_id)
    files = sorted(p.name for p in _path(scan_id).iterdir() if p.suffix == ".jpg")
    return {**data, "files": files, "summary": summary(data)}


def file_path(scan_id: str, name: str) -> Path:
    if not re.fullmatch(r"[\w\-]+\.jpg", name):
        raise KeyError("bad file name")
    p = _path(scan_id) / name
    if not p.is_file():
        raise KeyError("no such file")
    return p


def label(conn, scan_id: str, verdict: str, printing_id: str | None = None, finish: str | None = None,
          language: str | None = None, notes: str = "") -> dict:
    """verdict: 'correct' (the guess is right) or 'wrong' (give the right printing_id / finish / language).
    Also 'unreadable' = the photo itself is unusable (excluded from accuracy)."""
    data = _read(scan_id)
    r = data["result"]
    if verdict == "correct":
        if not r.get("printing_id"):
            raise ValueError("the scanner made no guess; use 'wrong' and pick the card")
        printing_id, finish, language = r["printing_id"], finish or r.get("finish"), language or r.get("language")
    elif verdict == "wrong":
        if not printing_id:
            raise ValueError("pick the right card")
    elif verdict != "unreadable":
        raise ValueError("verdict must be correct, wrong or unreadable")
    truth = None
    if printing_id:
        p = conn.execute("SELECT id, oracle_id, name, set_code, collector_number, lang FROM printings WHERE id=?",
                         (printing_id,)).fetchone()
        if not p:
            raise KeyError(f"unknown printing {printing_id}")
        truth = {"printing_id": p["id"], "oracle_id": p["oracle_id"], "name": p["name"], "set_code": p["set_code"],
                 "number": p["collector_number"], "finish": finish or "nonfoil", "language": language or p["lang"]}
    wrong = compare(r, truth) if truth else []
    data["label"] = {"verdict": "correct" if truth and not wrong else verdict, "truth": truth,
                     "wrong_fields": wrong, "notes": notes, "labelled_at": dt.datetime.now().isoformat(timespec="seconds")}
    _write(scan_id, data)
    return summary(data)


def compare(guess: dict, truth: dict) -> list[str]:
    """Which fields the guess got wrong: card (name), printing (set/number), finish, language."""
    wrong = []
    if not guess.get("printing_id"):
        return ["card", "printing", "finish"]
    if (guess.get("name") or "").lower() != truth["name"].lower():
        wrong.append("card")
    if guess.get("set_code") != truth["set_code"] or str(guess.get("collector_number")) != str(truth["number"]):
        wrong.append("printing")
    if guess.get("finish") != truth["finish"]:
        wrong.append("finish")
    if (guess.get("language") or "en") != (truth.get("language") or "en"):
        wrong.append("language")
    return wrong


def stats() -> dict:
    items = list_scans(limit=10 ** 6)["items"]
    reviewed = [s for s in items if s["status"] in ("correct", "wrong") and s["truth"]]
    n = len(reviewed)
    acc = {}
    for f in ("card", "printing", "finish", "language"):
        acc[f] = round(100 * sum(1 for s in reviewed if f not in s["wrong_fields"]) / n, 1) if n else None
    confusions = Counter((s["guess"]["name"] or "(no guess)", s["truth"]["name"]) for s in reviewed if "card" in s["wrong_fields"])
    conf_ok = [s["guess"]["confidence"] or 0 for s in reviewed if not s["wrong_fields"]]
    conf_bad = [s["guess"]["confidence"] or 0 for s in reviewed if s["wrong_fields"]]
    by_rec = {}
    for name in sorted({s["recognizer"] for s in reviewed}):
        mine = [s for s in reviewed if s["recognizer"] == name]
        by_rec[name] = {"n": len(mine), **{f: round(100 * sum(f not in s["wrong_fields"] for s in mine) / len(mine), 1)
                                           for f in ("card", "printing", "finish")}}
    return {"total": len(items), "reviewed": n, "unreviewed": sum(1 for s in items if s["status"] == "unreviewed"),
            "by_recognizer": by_rec,
            "fully_correct": round(100 * sum(1 for s in reviewed if not s["wrong_fields"]) / n, 1) if n else None,
            "accuracy": acc,
            "confusions": [{"guessed": g, "actual": a, "count": c} for (g, a), c in confusions.most_common(15)],
            "avg_confidence_when_right": round(sum(conf_ok) / len(conf_ok), 2) if conf_ok else None,
            "avg_confidence_when_wrong": round(sum(conf_bad) / len(conf_bad), 2) if conf_bad else None,
            "scans_dir": str(scans_dir())}


# ---- re-run the current scanner over the labelled set -------------------------------------------
RERUN = {"state": "idle", "done": 0, "total": 0, "report": None, "error": None}


def rerun(make_scanner, conn_lock: threading.Lock | None = None) -> dict:
    """Re-scan every labelled photo with the current scanner and compare with the labels (background thread)."""
    if RERUN["state"] == "running":
        return RERUN
    items = [s for s in list_scans(limit=10 ** 6)["items"] if s["truth"]]

    def run():
        RERUN.update(state="running", done=0, total=len(items), report=None, error=None)
        rows = []
        try:
            for s in items:
                d = _path(s["id"])
                photos = [p.read_bytes() for p in sorted(d.glob("photo_*.jpg"))]
                if conn_lock:
                    with conn_lock:
                        r = make_scanner().scan(photos, debug=False).to_dict()
                else:
                    r = make_scanner().scan(photos, debug=False).to_dict()
                now_wrong = compare(r, s["truth"])
                rows.append({"id": s["id"], "truth": s["truth"]["name"], "was": s["guess"]["name"], "now": r.get("name"),
                             "was_wrong": s["wrong_fields"], "now_wrong": now_wrong,
                             "recognizer": r.get("recognizer") or "ocr", "model_version": r.get("model_version")})
                RERUN["done"] += 1
            n = len(rows) or 1
            fields = ("card", "printing", "finish")
            RERUN["report"] = {
                "n": len(rows),
                "then": {f: round(100 * sum(f not in r["was_wrong"] for r in rows) / n, 1) for f in fields},
                "now": {f: round(100 * sum(f not in r["now_wrong"] for r in rows) / n, 1) for f in fields},
                "fixed": [r for r in rows if r["was_wrong"] and not r["now_wrong"]],
                "broken": [r for r in rows if not r["was_wrong"] and r["now_wrong"]],
                "still_wrong": [r for r in rows if r["was_wrong"] and r["now_wrong"]],
                "scanner_version": SCANNER_VERSION,
                "model_version": next((r["model_version"] for r in rows if r["model_version"]), None),
                "recognizers": dict(Counter(r["recognizer"] for r in rows)),
            }
            RERUN["state"] = "done"
        except Exception as e:
            RERUN.update(state="error", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=run, daemon=True).start()
    return RERUN


def export_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in sorted(scans_dir().iterdir()):
            if (d / "scan.json").is_file():
                for f in d.iterdir():
                    z.write(f, f"scans/{d.name}/{f.name}")
        z.writestr("stats.json", json.dumps(stats(), indent=1))
    return buf.getvalue()


def delete(scan_id: str) -> None:
    d = _path(scan_id)
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
