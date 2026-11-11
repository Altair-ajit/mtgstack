"""mtgstack command line. Every command takes --json for machine-readable output (for agents/scripts).

Examples
  mtgstack q 't:creature deck:atraxa'                    # search (query syntax: mtgstack help query)
  mtgstack stacks 'is:dupe -tag:trade'                   # grouped by card -> printing -> lot
  mtgstack add "Sol Ring" --set cmm --cn 410 --foil --tags binder
  mtgstack move 12 --qty 1 --add deck:atraxa --remove binder
  mtgstack move-query 'tag:binder set:mh3' --add box:mh3 --remove binder
  mtgstack split-dupes 'tag:binder' --keep 1 --add trade --remove binder --apply
  mtgstack deck import atraxa.txt --name "Atraxa" --format commander
  mtgstack deck import atraxa.txt --name "Atraxa" --building    # in progress: new cards it needs auto-tag in
  mtgstack deck show atraxa 'status:buy sort:price' ; mtgstack deck pull atraxa ; mtgstack deck export atraxa --fmt arena
  mtgstack wants 'is:building status:need t:creature sort:cmc'   # decklist entries across decks
  mtgstack send 12 --deck other --board side                    # override: move a copy to another deck
  mtgstack send 12                                               # ...or back to the collection
  mtgstack scan photo.jpg [more burst frames...] --add --tags binder
  mtgstack import tcgplayer export.csv --tags from-tcg
  mtgstack prices refresh ; mtgstack prices movers --days 7
  mtgstack serve --port 8765
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import collection as col, db, decks, prices, tcgplayer
from .catalog import Catalog
from .query import query, summarize


def _out(args, data, human=None):
    if args.json or human is None:
        print(json.dumps(data, indent=2, default=str))
    else:
        human(data)


def _table(lots):
    if not lots:
        print("(nothing)")
        return
    print(f"{'lot':>5} {'qty':>3}  {'name':<34} {'set':<5} {'#':<5} {'fin':<7} {'cond':<4} {'$':>7}  tags")
    for l in lots:
        price = f"{l['price']:.2f}" if l["price"] is not None else "-"
        print(f"{l['lot_id']:>5} {l['qty']:>3}  {l['name'][:34]:<34} {l['set_code']:<5} {l['collector_number']:<5} "
              f"{l['finish']:<7} {l['condition']:<4} {price:>7}  {l['tags'] or ''}")
    s = summarize(lots)
    print(f"-- {s['cards']} cards / {s['unique']} unique / {s['lots']} lots / ${s['value_usd']}")


def _stacks(st):
    for s in st:
        print(f"{s['qty']:>3}x {s['name']}  (${s['value']})  {' '.join(s['tags'])}")
        for p in s["printings"]:
            price = f"${p['price']:.2f}" if p["price"] is not None else ""
            print(f"      {p['qty']:>2} {p['set_code'].upper()} #{p['collector_number']} {p['finish']} {price}")
            for l in p["lots"]:
                print(f"           lot {l['lot_id']}: {l['qty']} {l['condition']} {l['language']}  {l['tags'] or ''}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mtgstack", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="database file (default ~/.mtgstack/collection.db or $MTGSTACK_DB)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--offline", action="store_true", help="never call Scryfall; local catalog only")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("q", help="search lots");  p.add_argument("query", nargs="?", default=""); p.add_argument("--limit", type=int)
    p = sub.add_parser("stacks", help="search, grouped by card"); p.add_argument("query", nargs="?", default="")
    sub.add_parser("tags", help="list tags with counts")
    p = sub.add_parser("retag", help="rename a tag everywhere"); p.add_argument("old"); p.add_argument("new")

    p = sub.add_parser("add", help="add cards")
    p.add_argument("name", nargs="?"); p.add_argument("--set"); p.add_argument("--cn"); p.add_argument("--id", help="scryfall id")
    p.add_argument("--qty", type=int, default=1); p.add_argument("--foil", action="store_true"); p.add_argument("--etched", action="store_true")
    p.add_argument("--cond", default="NM"); p.add_argument("--lang", default="en"); p.add_argument("--tags", default="")
    p.add_argument("--price", type=float)

    p = sub.add_parser("move", help="move qty copies of a lot to new tags (splits the lot)")
    p.add_argument("lot", type=int); p.add_argument("--qty", type=int); p.add_argument("--add", default="")
    p.add_argument("--remove", default=""); p.add_argument("--set-tags"); p.add_argument("--cond")
    p = sub.add_parser("send", help="move copies out of their deck: back to the collection or into another deck")
    p.add_argument("lot", type=int); p.add_argument("--qty", type=int); p.add_argument("--deck"); p.add_argument("--board", default="main")
    p = sub.add_parser("wants", help="decklist entries across decks (owned or not); same query syntax + status:/board:/deck:")
    p.add_argument("query", nargs="?", default="")
    p = sub.add_parser("move-query", help="move every lot matching a query")
    p.add_argument("query"); p.add_argument("--qty-per-lot", type=int); p.add_argument("--add", default=""); p.add_argument("--remove", default="")
    p = sub.add_parser("remove", help="remove copies from the collection"); p.add_argument("lot", type=int); p.add_argument("--qty", type=int); p.add_argument("--reason", default="")
    p = sub.add_parser("split-dupes", help="keep N of each card, move the rest")
    p.add_argument("query", nargs="?", default=""); p.add_argument("--keep", type=int, default=1)
    p.add_argument("--by", choices=["oracle", "printing"], default="oracle")
    p.add_argument("--add", default=""); p.add_argument("--remove", default=""); p.add_argument("--apply", action="store_true")

    p = sub.add_parser("deck", help="decks"); ds = p.add_subparsers(dest="deck_cmd", required=True)
    d = ds.add_parser("import"); d.add_argument("file", help="decklist file or - for stdin"); d.add_argument("--name"); d.add_argument("--format", default=""); d.add_argument("--slug")
    d.add_argument("--building", action="store_true", help="in progress: auto-tag new cards it needs")
    ds.add_parser("list")
    d = ds.add_parser("status", help="building (in progress, auto-fill) | built"); d.add_argument("slug"); d.add_argument("status", choices=["building", "built"]); d.add_argument("--priority", type=int)
    d = ds.add_parser("show"); d.add_argument("slug"); d.add_argument("query", nargs="?", default="", help="filter/sort entries, e.g. 't:creature sort:cmc'")
    d = ds.add_parser("pull"); d.add_argument("slug"); d.add_argument("--prefer", choices=["cheapest", "priciest"], default="cheapest"); d.add_argument("--dry-run", action="store_true")
    d.add_argument("--remove", default="", help="tags to drop from pulled copies, e.g. binder")
    d = ds.add_parser("release"); d.add_argument("slug")
    d = ds.add_parser("from-tags"); d.add_argument("slug"); d.add_argument("--name")
    d = ds.add_parser("export"); d.add_argument("slug"); d.add_argument("--fmt", choices=["arena", "mtgo", "moxfield", "tcgplayer"], default="arena"); d.add_argument("--owned", action="store_true")
    d = ds.add_parser("missing"); d.add_argument("slug")

    p = sub.add_parser("scan", help="identify a card from photo(s); several photos = tilt burst")
    p.add_argument("images", nargs="+"); p.add_argument("--add", action="store_true"); p.add_argument("--tags", default="")
    p.add_argument("--cond", default="NM"); p.add_argument("--no-images", action="store_true", help="skip art-hash downloads")

    p = sub.add_parser("import", help="import a collection"); p.add_argument("kind", choices=["tcgplayer"]); p.add_argument("file")
    p.add_argument("--tags", default=""); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("export", help="export lots"); p.add_argument("kind", choices=["tcgplayer", "json", "massentry"]); p.add_argument("query", nargs="?", default="")

    p = sub.add_parser("prices", help="prices"); ps = p.add_subparsers(dest="price_cmd", required=True)
    ps.add_parser("refresh"); ps.add_parser("value")
    x = ps.add_parser("movers"); x.add_argument("--days", type=int, default=7)
    x = ps.add_parser("show"); x.add_argument("printing_id")

    p = sub.add_parser("catalog", help="card catalog"); cs = p.add_subparsers(dest="cat_cmd", required=True)
    cs.add_parser("sync", help="download Scryfall bulk data (all printings + prices)")
    x = cs.add_parser("prints"); x.add_argument("name")
    p = sub.add_parser("events", help="recent changes"); p.add_argument("-n", type=int, default=20)
    p = sub.add_parser("help", help="extra docs"); p.add_argument("topic", choices=["query"])
    p = sub.add_parser("serve", help="run web UI + API"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8765)
    p.add_argument("--https", action="store_true", help="self-signed HTTPS so phone browsers allow live camera")
    p.add_argument("--tailscale", action="store_true",
                   help="publish on your tailnet with a real HTTPS certificate (tailscale serve) - best way to use a phone")
    p.add_argument("--reload", action="store_true", help="dev mode: restart when the code changes")

    args = ap.parse_args(argv)
    if args.cmd == "help":
        from . import query as qmod
        print(qmod.__doc__)
        return
    if args.cmd == "serve":
        return _serve(args)

    conn = db.connect(args.db)
    catalog = Catalog(conn, online=not args.offline)

    if args.cmd == "q":
        _out(args, query(conn, args.query, args.limit), _table)
    elif args.cmd == "stacks":
        _out(args, col.stacks(conn, query(conn, args.query)), _stacks)
    elif args.cmd == "tags":
        _out(args, col.tag_counts(conn), lambda t: [print(f"{r['cards']:>5}  {r['tag']}") for r in t])
    elif args.cmd == "retag":
        _out(args, {"lots": col.retag(conn, args.old, args.new)})
    elif args.cmd == "add":
        if args.id:
            pr = catalog.get(args.id)
        elif args.set and args.cn:
            pr = catalog.by_set_cn(args.set, args.cn, args.lang)
        else:
            cands = catalog.prints_by_name(args.name or "")
            if args.set:
                cands = [c for c in cands if c["set_code"] == args.set.lower()]
            pr = cands[0] if cands else None
        if not pr:
            sys.exit("card not found (try --set and --cn, or `mtgstack catalog prints NAME`)")
        fin = "etched" if args.etched else "foil" if args.foil else "nonfoil"
        allocs: list = []
        lot = col.add(conn, pr["id"], args.qty, fin, args.cond, args.lang, args.tags, args.price, allocations=allocs)
        _out(args, {**col.get_lot(conn, lot), "allocations": allocs}, lambda l: (_table([l]), _allocs(allocs)))
    elif args.cmd == "move":
        new = col.move(conn, args.lot, args.qty, args.add, args.remove, args.set_tags, args.cond)
        _out(args, [x for x in (col.get_lot(conn, new), col.get_lot(conn, args.lot)) if x], _table)
    elif args.cmd == "send":
        new = decks.send(conn, args.lot, args.qty, args.deck, args.board)
        _out(args, col.get_lot(conn, new), lambda l: _table([l]))
    elif args.cmd == "wants":
        from .query import query_entries
        _out(args, query_entries(conn, args.query), _entries_table)
    elif args.cmd == "move-query":
        ids = col.move_many(conn, query(conn, args.query), args.qty_per_lot, add_tags=args.add, remove_tags=args.remove)
        _out(args, {"moved_lots": len(ids)})
    elif args.cmd == "remove":
        col.remove(conn, args.lot, args.qty, args.reason)
        _out(args, {"ok": True})
    elif args.cmd == "split-dupes":
        plan = col.split_dupes(conn, query(conn, args.query), args.keep, args.by, args.add, args.remove, not args.apply)
        def show(pl):
            for s in pl:
                print(f"lot {s['lot_id']:>5}: move {s['move_qty']} of {s['name']} [{s['set']}], keep {s['stay_qty']}")
            print(("applied" if args.apply else "dry run -- add --apply to do it") + f": {sum(s['move_qty'] for s in pl)} cards")
        _out(args, plan, show)
    elif args.cmd == "deck":
        _deck(args, conn, catalog)
    elif args.cmd == "scan":
        from .scanner.resolve import Scanner
        res = Scanner(catalog, fetch_images=not (args.no_images or args.offline)).scan([str(Path(i)) for i in args.images])
        out = res.to_dict()
        if args.add and res.printing_id:
            allocs: list = []
            out["lot_id"] = col.add(conn, res.printing_id, 1, res.finish, args.cond, res.language, args.tags,
                                    allocations=allocs)
            out["allocations"] = allocs
        def show(r):
            print(f"{r['name']}  [{(r['set_code'] or '?').upper()} #{r['collector_number']}]  {r['finish']} "
                  f"({r['finish_confidence']:.0%})  lang={r['language']}  confidence={r['confidence']:.0%}"
                  + ("  ** REVIEW **" if r["needs_review"] else ""))
            for e in r["evidence"]:
                print("   -", e)
            _allocs(r.get("allocations") or [])
            if len(r["candidates"]) > 1:
                print("   candidates:", ", ".join(f"{c['set_code'].upper()} #{c['collector_number']} ({c['score']})" for c in r["candidates"][:5]))
        _out(args, out, show)
    elif args.cmd == "import":
        res = tcgplayer.import_csv(conn, catalog, Path(args.file).read_text(encoding="utf-8-sig"), args.tags, args.dry_run)
        _out(args, res, lambda r: (print(f"imported {r['cards']} cards in {len(r['added'])} rows; {len(r['failed'])} failed"),
                                    [print("  FAILED row", f["row"], f["name"]) for f in r["failed"]],
                                    [print("  check row", a["row"], a["name"], "-", a["matched_by"]) for a in r["added"] if "review" in a["matched_by"]]))
    elif args.cmd == "export":
        lots = query(conn, args.query)
        if args.kind == "json":
            print(json.dumps(lots, indent=2, default=str))
        else:
            print(tcgplayer.export_csv(conn, lots) if args.kind == "tcgplayer" else tcgplayer.mass_entry(lots))
    elif args.cmd == "prices":
        if args.price_cmd == "refresh":
            _out(args, prices.refresh_all(conn, catalog))
        elif args.price_cmd == "value":
            _out(args, prices.collection_value(conn))
        elif args.price_cmd == "movers":
            _out(args, prices.movers(conn, args.days), lambda m: [print(
                f"{r['delta_total']:>+8.2f}  {r['qty']}x {r['name']} [{r['set_code']}] {r['finish']}: {r['was']} -> {r['now_price']}") for r in m])
        else:
            _out(args, prices.quotes(conn, args.printing_id))
    elif args.cmd == "catalog":
        if args.cat_cmd == "sync":
            n = catalog.sync_bulk(progress=lambda n: print(f"  {n} printings...", file=sys.stderr))
            _out(args, {"printings": n})
        else:
            _out(args, catalog.prints_by_name(args.name), lambda ps: [print(
                f"{p['set_code'].upper():<6}#{p['collector_number']:<6} {p['lang']} {p['released_at']} {p['border_color']:<10} "
                f"{json.loads(p['finishes'])} {p['id']}") for p in ps])
    elif args.cmd == "events":
        _out(args, col.undo_hint(conn, args.n))


def _allocs(allocs):
    for a in allocs:
        print(f"  -> {a['qty']}x {a['name']} auto-tagged into {a['tag']} (in-progress deck {a['deck_name']})")


def _entries_table(es):
    print(f"{'deck':<18} {'board':<9} {'have':>6} {'move':>4} {'buy':>3}  {'name':<30} {'mv':>3} {'type':<12} {'$':>6}")
    for e in es:
        from .query import primary_type
        price = f"{e['price']:.2f}" if e["price"] is not None else "-"
        print(f"{e['deck_slug'][:18]:<18} {e['board']:<9} {e['have']:>2}/{e['wanted']:<3} {e['to_move']:>4} {e['to_buy']:>3}  "
              f"{e['name'][:30]:<30} {int(e['cmc'] or 0):>3} {primary_type(e['type_line']):<12} {price:>6}")


def _deck(args, conn, catalog):
    c = args.deck_cmd
    if c == "import":
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
        name = args.name or (Path(args.file).stem if args.file != "-" else "deck")
        _out(args, decks.import_decklist(conn, catalog, name, text, args.format, args.slug,
                                         status="building" if args.building else None))
    elif c == "status":
        _out(args, decks.update_deck(conn, args.slug, status=args.status, priority=args.priority))
    elif c == "list":
        _out(args, decks.list_decks(conn), lambda ds: [print(
            f"{d['slug']:<26} {'IN PROGRESS' if d['status'] == 'building' else 'built':<11} {d['progress']:>8}  "
            f"{d['free']:>3} to move  {d['missing']:>3} to buy (${d['missing_cost']})") for d in ds])
    elif c == "show":
        def show(r):
            t = r["totals"]
            state = "IN PROGRESS" if r["deck"]["status"] == "building" else "built"
            print(f"{r['deck']['name']}  [{r['deck']['format']}] {state}  {t['progress']}  "
                  f"{t['free']} to move, {t['missing']} to buy (~${t['missing_cost']})")
            for e in r["entries"]:
                flag = {"owned": "ok", "move": "MOVE", "buy": "BUY"}[e["status"]]
                extra = ""
                if e["to_move"]:
                    extra += f" move {e['to_move']} from " + ", ".join(f"lot {w['lot_id']} [{w['tags'] or 'untagged'}]" for w in e["where"])
                if e["to_buy"]:
                    extra += f" buy {e['to_buy']}" + (f" (cheapest {e['cheapest']['set_code'].upper()} ${e['cheapest']['price']})" if e["cheapest"] else "")
                if e["in_other_decks"]:
                    extra += f"  also in: {', '.join(e['in_other_decks'])}"
                print(f"  {flag:<4} {e['board']:<9} {e['have']}/{e['wanted']} {e['name']}{extra}")
        _out(args, decks.reconcile(conn, args.slug, args.query), show)
    elif c == "pull":
        _out(args, decks.pull(conn, args.slug, args.prefer, args.dry_run, col.norm_tags(args.remove)), lambda r: (
            [print(f"  {s['qty']}x {s['name']} [{s['set']}] {s['finish']} -> {s['tag']}") for s in r["pulled"]],
            print("dry run" if args.dry_run else f"after: {r['after']}")))
    elif c == "release":
        _out(args, {"lots": decks.release(conn, args.slug)})
    elif c == "from-tags":
        _out(args, {"slug": decks.deck_from_tags(conn, args.slug, args.name)})
    elif c == "export":
        print(decks.export(conn, args.slug, args.fmt, "owned" if args.owned else "list"))
    elif c == "missing":
        r = decks.reconcile(conn, args.slug)
        print("\n".join(f"{e['to_buy']} {e['name']}" for e in r["entries"] if e["to_buy"] and e["board"] != "maybe"))


def _serve(args):
    import os
    import uvicorn
    kw = {}
    if args.https:
        kw = dict(zip(("ssl_certfile", "ssl_keyfile"), _self_signed()))
    scheme = "https" if args.https else "http"
    print(f"mtgstack running: {scheme}://localhost:{args.port}   (API docs: /docs, stop: Ctrl+C)")
    phone: list[dict] = []
    if getattr(args, "tailscale", False):
        ts = _tailscale_serve(args.port, scheme)
        if ts.get("url"):
            phone.append({"url": ts["url"], "kind": "tailscale"})
            print(f"  on your phone (Tailscale, anywhere): {ts['url']}/lab/live")
        else:
            print("  Tailscale: " + ts.get("error", "not available"))
    if args.host in ("0.0.0.0", "::"):
        wsl = _wsl_network()
        for ip in (wsl["windows_ips"] if wsl else _lan_ips()):
            phone.append({"url": f"{scheme}://{ip}:{args.port}", "kind": "lan"})
            print(f"  on your phone (same Wi-Fi): {scheme}://{ip}:{args.port}")
        if wsl and wsl["mode"] == "nat":
            print(_wsl_portproxy_help(wsl, args.port))
        if args.https:
            print("  the certificate is self-signed: accept the browser warning once per device")
    import json as _json
    os.environ["MTGSTACK_PHONE_URLS"] = _json.dumps(phone)   # shown as QR codes in the app (Phone button)
    if args.reload:   # dev mode: restart when code changes
        if args.db:
            os.environ["MTGSTACK_DB"] = args.db
        if args.offline:
            os.environ["MTGSTACK_OFFLINE"] = "1"
        from pathlib import Path as _P
        uvicorn.run("mtgstack.server:dev_app", factory=True, reload=True, host=args.host, port=args.port,
                    reload_dirs=[str(_P(__file__).parent)], log_level="info", **kw)
        return
    from .server import create_app
    uvicorn.run(create_app(args.db, online=not args.offline), host=args.host, port=args.port,
                log_level="warning", **kw)


def _tailscale_exe() -> str | None:
    import shutil
    for c in ("tailscale", "tailscale.exe", "/mnt/c/Program Files/Tailscale/tailscale.exe"):
        p = shutil.which(c) or (c if Path(c).is_file() else None)
        if p:
            return p
    return None


def _tailscale_serve(port: int, scheme: str = "http") -> dict:
    """Publish localhost:port on this machine's tailnet name with a real (Let's Encrypt) certificate, so a phone on
    the same tailnet gets https://<pc>.<tailnet>.ts.net with no certificate warning, from any network.
    Under WSL this drives the Windows Tailscale app; Windows forwards its localhost to WSL."""
    import json
    import subprocess
    exe = _tailscale_exe()
    if not exe:
        return {"error": "tailscale not found - install it on this PC and on your phone (tailscale.com/download)"}
    target = f"{'https+insecure' if scheme == 'https' else 'http'}://127.0.0.1:{port}"
    try:
        r = subprocess.run([exe, "serve", "--bg", target], capture_output=True, text=True, timeout=30)
        out = (r.stdout + r.stderr).strip()
        if "not enabled" in out.lower() or r.returncode != 0:
            link = next((w for w in out.split() if w.startswith("https://login.tailscale.com")), "")
            return {"error": ("HTTPS/Serve isn't enabled on your tailnet yet - open " + link + " once, then restart.")
                    if link else out or "tailscale serve failed"}
        st = json.loads(subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=15).stdout)
        name = (st.get("Self") or {}).get("DNSName", "").rstrip(".")
        return {"url": f"https://{name}"} if name else {"error": "couldn't read this machine's tailnet name"}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"error": f"tailscale: {e}"}


def _lan_ips() -> list[str]:
    import socket
    ips = set()
    try:  # the address the OS would use to reach the internet = the Wi-Fi/Ethernet one
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        ips |= {i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def _wsl_network(proc_version: str | None = None) -> dict | None:
    """Under WSL, the phone must connect to the *Windows* machine's address. Returns None outside WSL, else
    {"mode": "nat"|"mirrored", "wsl_ips": [...], "windows_ips": [...]}."""
    import ipaddress
    import subprocess
    if proc_version is None:
        try:
            proc_version = Path("/proc/version").read_text()
        except OSError:
            return None
    if "microsoft" not in proc_version.lower():
        return None
    wsl_ips = _lan_ips()
    win_ips: list[str] = []
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null }).IPv4Address.IPAddress"],
            capture_output=True, text=True, timeout=15).stdout
        win_ips = [l.strip() for l in out.splitlines() if l.strip() and not l.strip().startswith("127.")]
    except (OSError, subprocess.SubprocessError):
        pass
    # WSL2's default NAT puts WSL on a private 172.16/12 network that the LAN can't reach.
    nat = bool(wsl_ips) and all(ipaddress.ip_address(i) in ipaddress.ip_network("172.16.0.0/12") for i in wsl_ips)
    if not win_ips and not nat:
        win_ips = wsl_ips          # mirrored mode: WSL shares the Windows addresses
    return {"mode": "nat" if nat else "mirrored", "wsl_ips": wsl_ips, "windows_ips": win_ips}


def _wsl_portproxy_help(wsl: dict, port: int) -> str:
    target = wsl["wsl_ips"][0] if wsl["wsl_ips"] else "<WSL address from: hostname -I>"
    return f"""
  WSL is in NAT mode, so your phone can't reach this server yet. One-time fix, in an ADMIN PowerShell on Windows:
    netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport={port} connectaddress={target} connectport={port}
    New-NetFirewallRule -DisplayName "mtgstack {port}" -Direction Inbound -Protocol TCP -LocalPort {port} -Action Allow -Profile Private
  WSL's address can change after a reboot; if the phone stops connecting, re-run the netsh line with the new
  address printed here (or switch WSL to mirrored networking: networkingMode=mirrored in %UserProfile%\\.wslconfig).
"""


def _self_signed():
    """Phone browsers only allow the live camera over HTTPS (or on localhost). Make a cert once, in Python
    (no openssl needed), valid for localhost and this machine's LAN addresses."""
    import datetime as dt
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    d = Path.home() / ".mtgstack"
    crt, key = d / "cert.pem", d / "key.pem"
    wsl = _wsl_network()
    ips = sorted(set(_lan_ips()) | set(wsl["windows_ips"] if wsl else []))
    if crt.exists() and key.exists():
        cert = x509.load_pem_x509_certificate(crt.read_bytes())
        try:
            have = {str(i) for i in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                    .get_values_for_type(x509.IPAddress)}
        except x509.ExtensionNotFound:
            have = set()
        if set(ips) <= have:
            return str(crt), str(key)          # still covers this network; reuse
    d.mkdir(parents=True, exist_ok=True)
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mtgstack")])
    san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    san += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False).sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(crt), str(key)


if __name__ == "__main__":
    main()
