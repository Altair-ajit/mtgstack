import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, emptyFilters } from "../api";
import type { CardRow, Filters } from "../api";
import FilterPanel from "../components/Filters";
import CardDrawer from "../components/CardDrawer";
import { CardImg, COND_LABEL, Empty, FinishBadge, HoverCard, LANG_LABEL, Mana, money, Pager, Rarity, TagChip, useDebounced, useToast } from "../ui";

const SORTS: [string, string][] = [
  ["name", "Name A–Z"], ["price", "Price: high to low"], ["-price", "Price: low to high"], ["total", "Total value"],
  ["added", "Recently added"], ["set", "Set (newest)"], ["number", "Set & number"], ["qty", "Quantity"],
  ["cmc", "Mana value"], ["type", "Card type"], ["color", "Color"], ["rarity", "Rarity"],
];

// Filters live in the URL so a view can be bookmarked / shared and Back works.
function readFilters(sp: URLSearchParams): Filters {
  const f = emptyFilters();
  for (const k of ["set", "rarity", "color", "type", "finish", "condition", "language", "tag"] as const)
    f[k] = (sp.get(k) || "").split(",").filter(Boolean);
  f.deck = sp.get("deck") || "";
  f.min_price = sp.get("min") || "";
  f.max_price = sp.get("max") || "";
  return f;
}

export default function Collection() {
  const [sp, setSp] = useSearchParams();
  const filters = useMemo(() => readFilters(sp), [sp]);
  const sort = sp.get("sort") || "name";
  const page = +(sp.get("page") || 1);
  const view = (sp.get("view") || localStorage.getItem("mtg.view") || "list") as "list" | "grid";
  const size = +(sp.get("size") || localStorage.getItem("mtg.size") || 50);
  const [qText, setQText] = useState(sp.get("q") || "");
  const q = useDebounced(qText, 250);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();

  const update = (patch: Record<string, string | null>, resetPage = true) => {
    const n = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) (v ? n.set(k, v) : n.delete(k));
    if (resetPage && !("page" in patch)) n.delete("page");
    setSp(n, { replace: true });
  };
  useEffect(() => { if ((sp.get("q") || "") !== q) update({ q }); }, [q]); // eslint-disable-line

  const setFilters = (f: Filters) => update({
    set: f.set.join(","), rarity: f.rarity.join(","), color: f.color.join(","), type: f.type.join(","),
    finish: f.finish.join(","), condition: f.condition.join(","), language: f.language.join(","), tag: f.tag.join(","),
    deck: f.deck, min: f.min_price, max: f.max_price,
  });

  const { data, isFetching, error } = useQuery({
    queryKey: ["cards", q, filters, sort, page, size],
    queryFn: () => api.cards(q, filters, sort, page, size),
    placeholderData: keepPreviousData,
  });
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });

  const rowKey = (r: CardRow) => `${r.printing_id}|${r.finish}|${r.condition}|${r.language}`;
  const selRows = (data?.rows || []).filter(r => selected.has(rowKey(r)));
  const selLots = selRows.flatMap(r => r.lots.map(l => l.lot_id));
  const toggleSel = (k: string) => setSelected(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });

  const bulk = async (body: Parameters<typeof api.bulk>[0], msg: string) => {
    try { await api.bulk(body); toast(msg); setSelected(new Set()); qc.invalidateQueries(); }
    catch (e) { toast(String((e as Error).message), "err"); }
  };
  const [bulkDeck, setBulkDeck] = useState("");
  const [bulkTag, setBulkTag] = useState("");

  return (
    <div className="page with-sidebar">
      <aside className={`sidebar ${showFilters ? "show" : ""}`}><FilterPanel facets={data?.facets} filters={filters} onChange={setFilters} /></aside>
      <main className="content">
        <div className="page-head">
          <div>
            <h1>My Collection</h1>
            {data && <div className="stats">
              <span><b>{data.totals.cards.toLocaleString()}</b> cards</span>
              <span><b>{data.totals.unique.toLocaleString()}</b> unique</span>
              <span>Market value <b className="price">{money(data.totals.value)}</b></span>
            </div>}
          </div>
        </div>

        <div className="toolbar">
          <div className="search">
            <input value={qText} onChange={e => setQText(e.target.value)} placeholder="Search your collection by card name…" />
            {qText && <button className="icon" onClick={() => setQText("")} aria-label="Clear">×</button>}
          </div>
          <button className="filters-btn" onClick={() => setShowFilters(!showFilters)}>
            Filters{Object.values(filters).flat().filter(Boolean).length ? ` (${Object.values(filters).flat().filter(Boolean).length})` : ""}
          </button>
          <Link className="btn" to="/scan?mode=batch" title="Scan a pile of cards with the camera, check the list, then add them all">📷 Scan cards in</Link>
          <label className="sortsel">Sort <select value={sort} onChange={e => update({ sort: e.target.value })}>
            {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          <div className="seg">
            <button className={view === "list" ? "on" : ""} onClick={() => { localStorage.setItem("mtg.view", "list"); update({ view: "list" }, false); }}>☰ List</button>
            <button className={view === "grid" ? "on" : ""} onClick={() => { localStorage.setItem("mtg.view", "grid"); update({ view: "grid" }, false); }}>▦ Grid</button>
          </div>
        </div>

        {selected.size > 0 && (
          <div className="bulkbar">
            <b>{selRows.reduce((n, r) => n + r.qty, 0)} cards selected</b>
            <select value={bulkDeck} onChange={e => setBulkDeck(e.target.value)}>
              <option value="">Move to deck…</option>
              {(decks.data || []).map(d => <option key={d.slug} value={d.slug}>{d.name}</option>)}
            </select>
            <button disabled={!bulkDeck} onClick={() => bulk({ lot_ids: selLots, action: "send", deck: bulkDeck }, "Moved to deck")}>Move</button>
            <button onClick={() => bulk({ lot_ids: selLots, action: "send", deck: null }, "Removed from decks")}>Remove from decks</button>
            <span className="sep" />
            <input placeholder="tag (e.g. box:3)" value={bulkTag} onChange={e => setBulkTag(e.target.value)} />
            <button disabled={!bulkTag} onClick={() => bulk({ lot_ids: selLots, action: "tag", tags: bulkTag.split(/[ ,]+/) }, "Tagged")}>Add tag</button>
            <button disabled={!bulkTag} onClick={() => bulk({ lot_ids: selLots, action: "untag", tags: bulkTag.split(/[ ,]+/) }, "Untagged")}>Remove tag</button>
            <span className="sep" />
            <button className="danger" onClick={() => { if (confirm(`Delete ${selRows.reduce((n, r) => n + r.qty, 0)} cards from your collection?`)) bulk({ lot_ids: selLots, action: "remove" }, "Deleted"); }}>Delete</button>
            <button className="link" onClick={() => setSelected(new Set())}>Clear</button>
          </div>
        )}

        {error && <div className="error-box">{String((error as Error).message)}</div>}
        {data && data.rows.length === 0 && (
          data.totals.cards === 0 && !q && !Object.values(filters).some(v => (Array.isArray(v) ? v.length : v))
            ? <Empty title="Your collection is empty">Use <b>Import</b> to bring in a TCGplayer export (paste, .txt or .csv), <Link to="/scan?mode=batch">scan a pile of cards</Link> with your camera, or <b>Add cards</b> to add them one at a time.</Empty>
            : <Empty title="No cards match">Try clearing a filter or the search.</Empty>
        )}

        <div className={isFetching ? "fetching" : ""}>
          {data && data.rows.length > 0 && view === "list" && (
            <div className="table-wrap"><table className="cards">
              <thead><tr>
                <th className="cb"><input type="checkbox" checked={data.rows.every(r => selected.has(rowKey(r)))}
                  onChange={e => setSelected(e.target.checked ? new Set(data.rows.map(rowKey)) : new Set())} /></th>
                <th></th><th>Name</th><th className="hide-xs">Set</th><th className="hide-sm">Rarity</th><th className="hide-xs">Printing</th><th className="hide-sm">Condition</th>
                <th className="num">Qty</th><th className="num">Market</th><th className="num hide-sm">Total</th><th className="hide-md">Where</th>
              </tr></thead>
              <tbody>
                {data.rows.map(r => (
                  <tr key={rowKey(r)} className={selected.has(rowKey(r)) ? "sel" : ""} onClick={() => setOpen(r.printing_id)}>
                    <td className="cb" onClick={e => e.stopPropagation()}><input type="checkbox" checked={selected.has(rowKey(r))} onChange={() => toggleSel(rowKey(r))} /></td>
                    <td className="thumb"><HoverCard small={r.image_small} large={r.image_normal} name={r.name} /></td>
                    <td><div className="cname">{r.name}</div><div className="sub"><span className="setcode show-xs">{r.set_code.toUpperCase()}</span><span className="show-xs"><FinishBadge f={r.finish} /></span><Mana cost={r.mana_cost} /> <span className="muted hide-xs">{r.type_line}</span></div></td>
                    <td className="setcol hide-xs"><div className="ell"><span className="setcode">{r.set_code.toUpperCase()}</span> <span className="hide-md">{r.set_name}</span></div><div className="sub muted">#{r.collector_number}</div></td>
                    <td className="hide-sm"><Rarity r={r.rarity} /></td>
                    <td className="hide-xs">{r.finish === "nonfoil" ? <span className="muted">Normal</span> : <FinishBadge f={r.finish} />}{r.language !== "en" && <div className="sub muted">{LANG_LABEL[r.language] || r.language}</div>}</td>
                    <td className="hide-sm" title={COND_LABEL[r.condition]}>{r.condition}</td>
                    <td className="num strong">{r.qty}</td>
                    <td className="num price">{money(r.price)}</td>
                    <td className="num hide-sm">{money(r.total)}</td>
                    <td className="hide-md">{r.decks.map(d => <TagChip key={d} t={`deck:${d}`} />)}{r.tags.map(t => <TagChip key={t} t={t} />)}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
          {data && data.rows.length > 0 && view === "grid" && (
            <div className="grid">
              {data.rows.map(r => (
                <div key={rowKey(r)} className={`tile ${selected.has(rowKey(r)) ? "sel" : ""}`} onClick={() => setOpen(r.printing_id)}>
                  <div className="tile-img">
                    <CardImg src={r.image_normal || r.image_small} name={r.name} size="md" />
                    <span className="qty-badge">×{r.qty}</span>
                    {r.finish !== "nonfoil" && <span className="foil-badge"><FinishBadge f={r.finish} /></span>}
                    <input className="tile-cb" type="checkbox" checked={selected.has(rowKey(r))} onClick={e => e.stopPropagation()} onChange={() => toggleSel(rowKey(r))} />
                  </div>
                  <div className="tile-name">{r.name}</div>
                  <div className="tile-meta"><span className="setcode">{r.set_code.toUpperCase()}</span> {r.condition} <span className="price">{money(r.price)}</span></div>
                </div>
              ))}
            </div>
          )}
        </div>

        {data && (
          <div className="foot">
            <span className="muted">{((data.page - 1) * data.size + 1).toLocaleString()}–{Math.min(data.page * data.size, data.total_rows).toLocaleString()} of {data.total_rows.toLocaleString()} rows</span>
            <Pager page={data.page} pages={data.pages} onPage={p => update({ page: String(p) }, false)} />
            <label className="muted">per page <select value={size} onChange={e => { localStorage.setItem("mtg.size", e.target.value); update({ size: e.target.value }); }}>
              {[25, 50, 100, 200].map(n => <option key={n}>{n}</option>)}</select></label>
          </div>
        )}
      </main>
      {open && <CardDrawer printingId={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
