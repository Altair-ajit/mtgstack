"""TCGplayer interop: import the app's collection CSV export, export lots back out.

The TCGplayer app lets you choose which CSV columns to export, and other tools (ManaBox, Moxfield,
Deckbox, Dragon Shield) use similar but different headers. So the importer maps headers by alias and
resolves each row with the strongest identifier present:

    TCGplayer Product ID  >  set code + collector number  >  set name/code + card name  >  card name
"""
from __future__ import annotations

import csv
import io

from . import collection as col
from .catalog import Catalog

ALIASES = {
    "qty": ["quantity", "qty", "count", "total quantity", "add to quantity"],
    "name": ["name", "card name", "product name", "simple name", "card"],
    "set_name": ["set", "set name", "edition", "expansion"],
    "set_code": ["set code", "setcode", "edition code", "set abbreviation"],
    "number": ["card number", "collector number", "number", "collector #", "cn"],
    "printing": ["printing", "finish", "foil", "variant"],
    "condition": ["condition"],
    "language": ["language", "lang"],
    "product_id": ["product id", "tcgplayer id", "tcgplayer product id", "productid", "tcgplayer_id"],
    "price": ["price", "purchase price", "price each", "acquired price", "my price"],
    "tags": ["tags", "list", "binder name", "binder", "collection"],
}
LANG_NAMES = {"english": "en", "japanese": "ja", "german": "de", "french": "fr", "italian": "it",
              "spanish": "es", "portuguese": "pt", "korean": "ko", "russian": "ru",
              "chinese (s)": "zhs", "chinese simplified": "zhs", "chinese (t)": "zht", "chinese traditional": "zht"}


def _map_headers(headers: list[str]) -> dict[str, str]:
    norm = {h.strip().lower(): h for h in headers}
    out = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in norm:
                out[key] = norm[n]
                break
    return out


def _clean_name(name: str) -> str:
    # TCGplayer product names carry variant suffixes: "Sol Ring (Borderless)", "Island (0276)"
    return name.split(" (")[0].strip() if name else name


def import_csv(conn, catalog: Catalog, text: str, tags=None, dry_run: bool = False) -> dict:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    hm = _map_headers(reader.fieldnames or [])
    if "name" not in hm and "product_id" not in hm:
        raise ValueError(f"CSV needs a name or product id column; got {reader.fieldnames}")
    get = lambda row, k: (row.get(hm[k]) or "").strip() if k in hm else ""
    added, failed, allocated = [], [], []
    for i, row in enumerate(reader, start=2):
        name, printing = get(row, "name"), None
        how = ""
        if (pid := get(row, "product_id")).isdigit():
            printing, how = catalog.by_tcgplayer(int(pid)), "product id"
        if not printing and get(row, "set_code") and get(row, "number"):
            printing, how = catalog.by_set_cn(get(row, "set_code"), get(row, "number")), "set+number"
        if not printing and name:
            setv = get(row, "set_code") or get(row, "set_name")
            cands = catalog.by_set_name(setv, _clean_name(name)) if setv else []
            if get(row, "number"):
                cands = [c for c in cands if c["collector_number"].lstrip("0") == get(row, "number").lstrip("0")] or cands
            if not cands:
                cands = catalog.prints_by_name(_clean_name(name))
                how = "name only (printing guessed - review)"
            else:
                how = how or "set+name"
            printing = cands[0] if cands else None
        if not printing:
            failed.append({"row": i, "name": name, "reason": "not found"})
            continue
        finish_raw = get(row, "printing")
        cond_raw = get(row, "condition")
        finish = col.norm_finish(finish_raw or ("foil" if "foil" in cond_raw.lower() else "nonfoil"))
        lang = LANG_NAMES.get(get(row, "language").lower(), get(row, "language").lower() or "en")[:3]
        qty = int(float(get(row, "qty") or 1))
        row_tags = col.norm_tags(tags) + col.norm_tags(get(row, "tags").replace(" ", "-") if get(row, "tags") else None)
        price = get(row, "price").lstrip("$")
        item = {"row": i, "name": printing["name"], "set": printing["set_code"], "cn": printing["collector_number"],
                "qty": qty, "finish": finish, "condition": col.norm_condition(cond_raw), "matched_by": how}
        if not dry_run:
            item["lot_id"] = col.add(conn, printing["id"], qty, finish, cond_raw, lang, row_tags,
                                     float(price) if price.replace(".", "", 1).isdigit() else None, commit=False,
                                     allocations=allocated)
        added.append(item)
    conn.commit()
    return {"added": added, "failed": failed, "cards": sum(a["qty"] for a in added), "allocated": allocated}


EXPORT_COLS = ["Quantity", "Name", "Set", "Set Code", "Card Number", "Printing", "Condition", "Language",
               "Product ID", "Rarity", "Market Price", "Tags", "Scryfall ID"]
_COND_LONG = {"NM": "Near Mint", "LP": "Lightly Played", "MP": "Moderately Played", "HP": "Heavily Played",
              "DMG": "Damaged"}
_LANG_LONG = {v: k.title() for k, v in LANG_NAMES.items()}


def export_csv(conn, lots: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLS)
    for l in lots:
        p = conn.execute("SELECT tcgplayer_id, tcgplayer_etched_id FROM printings WHERE id=?",
                         (l["printing_id"],)).fetchone()
        pid = (p["tcgplayer_etched_id"] if l["finish"] == "etched" and p["tcgplayer_etched_id"] else p["tcgplayer_id"]) or ""
        w.writerow([l["qty"], l["name"], l["set_name"], l["set_code"].upper(), l["collector_number"],
                    {"nonfoil": "Normal", "foil": "Foil", "etched": "Foil Etched"}[l["finish"]],
                    _COND_LONG[l["condition"]], _LANG_LONG.get(l["language"], l["language"]), pid,
                    (l["rarity"] or "")[:1].upper(), l["price"] if l["price"] is not None else "",
                    l["tags"] or "", l["printing_id"]])
    return buf.getvalue()


def mass_entry(lots: list[dict]) -> str:
    """TCGplayer 'Mass Entry' text: paste into tcgplayer.com/massentry to price/buy a list."""
    return "\n".join(f"{l['qty']} {l['name']} [{l['set_code'].upper()}]" for l in lots)
