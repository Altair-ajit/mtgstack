import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Allocation, Finish } from "../api";
import { CardImg, COND_LABEL, Drawer, FINISH_LABEL, FinishBadge, Mana, money, Rarity, TagChip, useToast } from "../ui";

export function allocationText(a: Allocation[]) {
  return a.map(x => `${x.qty}× ${x.name} → ${x.deck_name}${x.deck_progress ? ` (${x.deck_progress})` : ""}`).join(", ");
}

export default function CardDrawer({ printingId, onClose }: { printingId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading, error } = useQuery({ queryKey: ["card", printingId], queryFn: () => api.card(printingId) });
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const [adding, setAdding] = useState<string | null>(null);
  const [form, setForm] = useState({ qty: 1, finish: "nonfoil" as Finish, condition: "NM" });

  const refresh = () => qc.invalidateQueries();
  const run = async (p: Promise<unknown>, ok: string) => {
    try { await p; toast(ok); refresh(); } catch (e) { toast(String((e as Error).message), "err"); }
  };

  if (isLoading || !data) return <Drawer onClose={onClose}><p className="muted pad">{error ? String(error) : "Loading…"}</p></Drawer>;
  const c = data.card;
  const priceRows = ["nonfoil", "foil", "etched"].filter(f => data.prices.some(p => p.finish === f));
  const quote = (f: string, src: string, kind: string) => data.prices.find(p => p.finish === f && p.source === src && p.kind === kind)?.price;

  return (
    <Drawer onClose={onClose}>
      <div className="cd">
        <div className="cd-top">
          <CardImg src={c.image_normal} name={c.name} size="lg" />
          <div className="cd-info">
            <h2>{c.name} <Mana cost={c.mana_cost} /></h2>
            <div className="muted">{c.type_line}</div>
            <div className="cd-set"><span className="setcode">{c.set_code.toUpperCase()}</span> {c.set_name} · #{c.collector_number} <Rarity r={c.rarity} /></div>
            {c.oracle_text && <p className="oracle">{c.oracle_text}</p>}
            <table className="mini prices">
              <thead><tr><th>Printing</th><th>TCG Market</th><th>CK Retail</th><th>CK Buylist</th></tr></thead>
              <tbody>
                {priceRows.length ? priceRows.map(f => (
                  <tr key={f}><td>{FINISH_LABEL[f]}</td><td className="num strong">{money(quote(f, "tcgplayer", "market"))}</td>
                    <td className="num">{money(quote(f, "cardkingdom", "retail"))}</td><td className="num">{money(quote(f, "cardkingdom", "buylist"))}</td></tr>
                )) : <tr><td colSpan={4} className="muted">No prices yet — refresh prices from the header menu.</td></tr>}
              </tbody>
            </table>
            {c.tcgplayer_id && <a className="small" href={`https://www.tcgplayer.com/product/${c.tcgplayer_id}`} target="_blank" rel="noreferrer">View on TCGplayer ↗</a>}
          </div>
        </div>

        <h3>Your copies <span className="muted">({data.owned.reduce((n, o) => n + o.qty, 0)})</span></h3>
        {data.owned.length ? (
          <table className="mini">
            <thead><tr><th>Printing</th><th>Cond.</th><th>Qty</th><th>Market</th><th>Where</th><th></th></tr></thead>
            <tbody>
              {data.owned.map(o => {
                const tags = (o.tags || "").split(" ").filter(Boolean);
                const inDeck = tags.some(t => t.startsWith("deck:"));
                return (
                  <tr key={o.lot_id}>
                    <td><span className="setcode">{o.set_code.toUpperCase()}</span> #{o.collector_number} <FinishBadge f={o.finish} /></td>
                    <td><select value={o.condition} onChange={e => run(api.patchLot(o.lot_id, { condition: e.target.value }), "Condition updated")}>
                      {Object.keys(COND_LABEL).map(k => <option key={k}>{k}</option>)}</select></td>
                    <td><input className="qty" type="number" min={0} defaultValue={o.qty} key={`${o.lot_id}-${o.qty}`}
                      onBlur={e => { const v = +e.target.value; if (v !== o.qty) run(api.patchLot(o.lot_id, { qty: v }), v ? "Quantity updated" : "Removed"); }} /></td>
                    <td className="num">{money(o.price)}</td>
                    <td>{tags.length ? tags.map(t => <TagChip key={t} t={t} />) : <span className="muted">collection</span>}</td>
                    <td className="right">
                      <select value="" onChange={e => {
                        const v = e.target.value;
                        if (!v) return;
                        run(api.send(o.lot_id, { deck: v === "__none" ? null : v }), v === "__none" ? "Back in collection" : "Moved to deck");
                      }}>
                        <option value="">Move…</option>
                        {inDeck && <option value="__none">↩ Back to collection</option>}
                        {(decks.data || []).map(d => <option key={d.slug} value={d.slug}>→ {d.name}</option>)}
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : <p className="muted">You don't own this card yet.</p>}

        {data.decks.length > 0 && (
          <>
            <h3>In your decks</h3>
            <ul className="deck-wants">
              {data.decks.map(d => (
                <li key={d.deck_slug + d.board}>
                  <Link to={`/decks/${d.deck_slug}`} onClick={onClose}>{d.deck_name}</Link>
                  {d.board !== "main" && <span className="muted"> ({d.board})</span>}
                  <span className={`status ${d.have >= d.wanted ? "ok" : d.to_buy ? "buy" : "move"}`}>
                    {d.have}/{d.wanted}{d.to_move ? ` · ${d.to_move} to move` : ""}{d.to_buy ? ` · ${d.to_buy} to buy` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}

        <h3>All printings <span className="muted">({data.printings.length})</span></h3>
        <table className="mini printings">
          <thead><tr><th></th><th>Set</th><th>Normal</th><th>Foil</th><th>Owned</th><th></th></tr></thead>
          <tbody>
            {data.printings.map(p => (
              <tr key={p.printing_id} className={p.printing_id === c.id ? "current" : ""}>
                <td><CardImg src={p.image_small} name={p.set_code} /></td>
                <td><span className="setcode">{p.set_code.toUpperCase()}</span> {p.set_name}<div className="muted small">#{p.collector_number} · {p.released_at?.slice(0, 4)}{p.lang !== "en" ? ` · ${p.lang}` : ""}{p.border_color === "borderless" ? " · borderless" : ""}{p.frame_effects.includes("showcase") ? " · showcase" : ""}{p.frame_effects.includes("extendedart") ? " · extended" : ""}</div></td>
                <td className="num">{p.finishes.includes("nonfoil") ? money(p.price) : ""}</td>
                <td className="num">{p.finishes.includes("foil") ? money(p.price_foil) : ""}</td>
                <td className="num">{p.owned || ""}</td>
                <td className="right">
                  {adding === p.printing_id ? (
                    <span className="inline-add">
                      <input className="qty" type="number" min={1} value={form.qty} onChange={e => setForm({ ...form, qty: +e.target.value })} />
                      <select value={form.finish} onChange={e => setForm({ ...form, finish: e.target.value as Finish })}>
                        {p.finishes.map(f => <option key={f} value={f}>{FINISH_LABEL[f]}</option>)}</select>
                      <select value={form.condition} onChange={e => setForm({ ...form, condition: e.target.value })}>
                        {Object.keys(COND_LABEL).map(k => <option key={k}>{k}</option>)}</select>
                      <button className="primary small" onClick={async () => {
                        try {
                          const r = await api.addLot({ printing_id: p.printing_id, ...form });
                          toast(r.allocations.length ? allocationText(r.allocations) : `Added ${form.qty}× ${c.name}`);
                          setAdding(null); refresh();
                        } catch (e) { toast(String((e as Error).message), "err"); }
                      }}>Add</button>
                    </span>
                  ) : (
                    <button className="small" onClick={() => { setAdding(p.printing_id); setForm({ qty: 1, finish: p.finishes[0] || "nonfoil", condition: "NM" }); }}>+ Add</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Drawer>
  );
}
