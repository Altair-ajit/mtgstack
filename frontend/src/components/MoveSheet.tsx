import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Finish } from "../api";
import { CardImg, FinishBadge, Modal, useToast } from "../ui";

/** One kind of card to move, with the lots (stacks of copies) it can come from. */
export interface MoveItem {
  key: string; name: string; set_code: string; number: string; finish: Finish; image: string | null;
  lots: { lot_id: number; qty: number; tags: string | null }[];
}

const BOARDS: [string, string][] = [["main", "Main"], ["side", "Sideboard"], ["commander", "Commander"], ["maybe", "Maybe"]];
const tagsOf = (l: { tags: string | null }) => (l.tags || "").split(" ").filter(Boolean);
const deckTag = (slug: string, board: string) => (board === "main" ? `deck:${slug}` : `deck:${slug}:${board}`);

/** Move cards into a deck (or back out) in two taps: say how many copies of each, then tap where they go.
 * A stack can be split: 1 of 3 moves one copy and leaves two where they were. */
export default function MoveSheet({ items, onClose, onDone }: { items: MoveItem[]; onClose: () => void; onDone?: () => void }) {
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const qc = useQueryClient();
  const toast = useToast();
  const total = (i: MoveItem) => i.lots.reduce((n, l) => n + l.qty, 0);
  // one copy of each by default: a deck usually wants one, and "All" is a tap away
  const [qty, setQty] = useState<Record<string, number>>(() => Object.fromEntries(items.map(i => [i.key, Math.min(1, total(i))])));
  const [board, setBoard] = useState("main");
  const [naming, setNaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const count = items.reduce((n, i) => n + (qty[i.key] || 0), 0);
  const anyInDeck = items.some(i => i.lots.some(l => tagsOf(l).some(t => t.startsWith("deck:"))));
  const set = (key: string, n: number, max: number) => setQty(q => ({ ...q, [key]: Math.max(0, Math.min(max, n)) }));

  /** Which copies to take: loose ones first, then ones in other decks; never ones already where they are going. */
  const plan = (slug: string | null) => items.flatMap(i => {
    let left = qty[i.key] || 0;
    const there = slug ? deckTag(slug, board) : null;
    const usable = i.lots.filter(l => (slug ? !tagsOf(l).includes(there!) : tagsOf(l).some(t => t.startsWith("deck:"))))
      .sort((a, b) => +tagsOf(a).some(t => t.startsWith("deck:")) - +tagsOf(b).some(t => t.startsWith("deck:")));
    const out: { lot_id: number; qty: number }[] = [];
    for (const l of usable) {
      if (left <= 0) break;
      const n = Math.min(left, l.qty);
      out.push({ lot_id: l.lot_id, qty: n });
      left -= n;
    }
    return out;
  });

  const go = async (slug: string | null, name: string) => {
    const moves = plan(slug);
    if (!moves.length) { toast(slug ? `Those copies are already in ${name}` : "Those copies are not in a deck", "info"); return; }
    setBusy(true);
    try {
      const r = await api.moveLots(moves, slug, board);
      toast(slug ? `Moved ${r.moved} card${r.moved === 1 ? "" : "s"} to ${name}` : `${r.moved} card${r.moved === 1 ? "" : "s"} back in the collection`);
      await qc.invalidateQueries();
      onDone?.();
      onClose();
    } catch (e) { toast(String((e as Error).message), "err"); }
    setBusy(false);
  };
  const slugOf = (n: string) => n.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "deck";
  const taken = !!naming?.trim() && (decks.data || []).some(d => d.slug === slugOf(naming));
  const createAndGo = async () => {
    if (!naming?.trim() || taken || busy) return;
    setBusy(true);
    try {
      const d = await api.createDeck({ name: naming.trim(), text: "", format: "", status: "building" });
      await go(d.slug, naming.trim());
    } catch (e) { toast(String((e as Error).message), "err"); setBusy(false); }
  };
  const list = [...(decks.data || [])].sort((a, b) => +(b.status === "building") - +(a.status === "building") || a.name.localeCompare(b.name));

  return (
    <Modal title={items.length === 1 ? `Move ${items[0].name}` : `Move ${items.length} cards`} onClose={onClose}>
      <ul className="mv-items">
        {items.map(i => {
          const max = total(i), n = qty[i.key] || 0;
          return (
            <li key={i.key}>
              <CardImg src={i.image} name={i.name} />
              <div className="mv-name"><b>{i.name}</b>
                <div className="muted small"><span className="setcode">{i.set_code.toUpperCase()}</span> #{i.number} <FinishBadge f={i.finish} /></div></div>
              {max > 1 ? (
                <div className="mv-qty">
                  <div className="stepper" role="group" aria-label={`How many ${i.name}`}>
                    <button onClick={() => set(i.key, n - 1, max)} disabled={n <= 0} aria-label="One fewer">−</button>
                    <input inputMode="numeric" value={n} aria-label="Copies to move" onChange={e => set(i.key, parseInt(e.target.value, 10) || 0, max)} />
                    <button onClick={() => set(i.key, n + 1, max)} disabled={n >= max} aria-label="One more">+</button>
                  </div>
                  <button className="link small" onClick={() => set(i.key, n === max ? 1 : max, max)}>{n === max ? `of ${max}` : `of ${max} · all`}</button>
                </div>
              ) : <span className="muted small">1 copy</span>}
            </li>
          );
        })}
      </ul>
      {items.length > 1 && items.some(i => total(i) > 1) && (
        <div className="mv-all"><button className="link small" onClick={() => setQty(Object.fromEntries(items.map(i => [i.key, total(i)])))}>Every copy of each</button>
          <button className="link small" onClick={() => setQty(Object.fromEntries(items.map(i => [i.key, Math.min(1, total(i))])))}>One of each</button></div>
      )}
      <div className="mv-to">
        <span className="muted small">{count} card{count === 1 ? "" : "s"} to</span>
        <div className="seg">{BOARDS.map(([k, l]) => <button key={k} className={board === k ? "on" : ""} onClick={() => setBoard(k)}>{l}</button>)}</div>
      </div>
      <div className="mv-decks">
        {list.map(d => (
          <button key={d.slug} className="mv-deck" disabled={busy || !count} onClick={() => go(d.slug, d.name)}>
            <span><b>{d.name}</b><span className="muted small"> {d.format}</span></span>
            <span className={`badge ${d.status === "building" ? "wip" : "done"}`}>{d.progress}</span>
          </button>
        ))}
        {naming === null ? (
          <button className="mv-deck new" disabled={busy || !count} onClick={() => setNaming("")}>+ New deck</button>
        ) : (
          <div className="deck-new">
            <input autoFocus placeholder="New deck name" value={naming} maxLength={80} onChange={e => setNaming(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") createAndGo(); if (e.key === "Escape") setNaming(null); }} />
            <button className="primary" disabled={!naming.trim() || taken || busy || !count} onClick={createAndGo}>Create and move</button>
            {taken && <span className="bad small">You already have a deck with this name.</span>}
          </div>
        )}
        {anyInDeck && <button className="mv-deck out" disabled={busy || !count} onClick={() => go(null, "")}>↩ Out of its deck, back to the collection</button>}
        {!decks.data?.length && naming === null && <p className="muted small">No decks yet: make one with “New deck”.</p>}
      </div>
    </Modal>
  );
}
