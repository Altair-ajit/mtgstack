import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Finish, NameHit, Printing } from "../api";
import { CardImg, COND_LABEL, FINISH_LABEL, LANG_LABEL, Mana, Modal, money, useDebounced, useToast } from "../ui";
import { allocationText } from "./CardDrawer";

/** Search a card by name, pick the exact printing from images, set qty / finish / condition, add. */
export default function AddCard({ onClose, deck: deck0 }: { onClose: () => void; deck?: string }) {
  const [text, setText] = useState("");
  const q = useDebounced(text, 200);
  const [hits, setHits] = useState<NameHit[]>([]);
  const [picked, setPicked] = useState<NameHit | null>(null);
  const [printings, setPrintings] = useState<Printing[]>([]);
  const [sel, setSel] = useState<Printing | null>(null);
  const [form, setForm] = useState({ qty: 1, finish: "nonfoil" as Finish, condition: "NM", language: "en", tags: "", deck: deck0 || "", board: "main" });
  const [addedCount, setAddedCount] = useState(0);
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const toast = useToast();
  const qc = useQueryClient();

  useEffect(() => {
    if (picked || q.length < 2) { setHits([]); return; }
    let live = true;
    api.names(q).then(h => live && setHits(h)).catch(() => {});
    return () => { live = false; };
  }, [q, picked]);

  const pick = async (h: NameHit) => {
    setPicked(h); setHits([]);
    const ps = await api.prints(h.name);
    if (!ps.length) return;
    const det = await api.card(ps[0].id);
    setPrintings(det.printings);
    choose(det.printings[0]);
  };
  const choose = (p: Printing) => {
    setSel(p);
    setForm(f => ({ ...f, finish: p.finishes.includes(f.finish) ? f.finish : p.finishes[0] || "nonfoil" }));
  };

  const add = async (close: boolean) => {
    if (!sel || !picked) return;
    try {
      const tags = form.tags.split(/[ ,]+/).filter(Boolean);
      if (form.deck) tags.push(form.board === "main" ? `deck:${form.deck}` : `deck:${form.deck}:${form.board}`);
      const r = await api.addLot({ printing_id: sel.printing_id, qty: form.qty, finish: form.finish, condition: form.condition, language: form.language, tags });
      toast(r.allocations.length ? allocationText(r.allocations) : `Added ${form.qty}× ${picked.name} (${sel.set_code.toUpperCase()})`);
      qc.invalidateQueries();
      setAddedCount(n => n + form.qty);
      if (close) onClose();
      else { setPicked(null); setSel(null); setPrintings([]); setText(""); }
    } catch (e) { toast(String((e as Error).message), "err"); }
  };

  return (
    <Modal title="Add cards" onClose={onClose} wide footer={<>
      {addedCount > 0 && <span className="muted">{addedCount} added this session</span>}
      <button onClick={onClose}>Done</button>
      <button disabled={!sel} onClick={() => add(false)}>Add &amp; add another</button>
      <button className="primary" disabled={!sel} onClick={() => add(true)}>Add {form.qty} to {form.deck ? "deck" : "collection"}</button>
    </>}>
      {!picked ? (
        <div className="ac-wrap">
          <input className="big-search" autoFocus placeholder="Card name…" value={text} onChange={e => setText(e.target.value)}
            onKeyDown={e => e.key === "Enter" && hits[0] && pick(hits[0])} />
          {hits.length > 0 && (
            <ul className="ac big">
              {hits.map(h => (
                <li key={h.oracle_id} onClick={() => pick(h)}>
                  <CardImg src={h.image_small} name={h.name} />
                  <div><b>{h.name}</b> <Mana cost={h.mana_cost} /><div className="muted small">{h.type_line}</div></div>
                </li>
              ))}
            </ul>
          )}
          {q.length >= 2 && !hits.length && <p className="muted">No card named like “{q}”. If you haven't downloaded the card database yet, do that from the banner at the top.</p>}
        </div>
      ) : (
        <div className="add-pick">
          <div className="add-head">
            <button className="link" onClick={() => { setPicked(null); setSel(null); setPrintings([]); }}>‹ different card</button>
            <h3>{picked.name} <Mana cost={picked.mana_cost} /></h3>
          </div>
          <div className="add-body">
            <div className="printing-grid">
              {printings.map(p => (
                <button key={p.printing_id} className={`pr ${sel?.printing_id === p.printing_id ? "on" : ""}`} onClick={() => choose(p)}>
                  <CardImg src={p.image_small} name={p.set_code} />
                  <span className="pr-set"><span className="setcode">{p.set_code.toUpperCase()}</span> #{p.collector_number}</span>
                  <span className="pr-name">{p.set_name}</span>
                  <span className="price">{money(p.price ?? p.price_foil)}</span>
                  {p.owned > 0 && <span className="owned">own {p.owned}</span>}
                </button>
              ))}
            </div>
            {sel && (
              <div className="add-form">
                <CardImg src={sel.image_small} name={picked.name} size="md" />
                <label>Quantity <input type="number" min={1} value={form.qty} onChange={e => setForm({ ...form, qty: Math.max(1, +e.target.value) })} /></label>
                <label>Printing <select value={form.finish} onChange={e => setForm({ ...form, finish: e.target.value as Finish })}>
                  {sel.finishes.map(f => <option key={f} value={f}>{FINISH_LABEL[f]}</option>)}</select></label>
                <label>Condition <select value={form.condition} onChange={e => setForm({ ...form, condition: e.target.value })}>
                  {Object.entries(COND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
                <label>Language <select value={form.language} onChange={e => setForm({ ...form, language: e.target.value })}>
                  {Object.entries(LANG_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
                <label>Put in deck <select value={form.deck} onChange={e => setForm({ ...form, deck: e.target.value })}>
                  <option value="">— collection (auto-fills in-progress decks) —</option>
                  {(decks.data || []).map(d => <option key={d.slug} value={d.slug}>{d.name}</option>)}</select></label>
                {form.deck && <label>Board <select value={form.board} onChange={e => setForm({ ...form, board: e.target.value })}>
                  {["main", "commander", "side", "companion", "maybe"].map(b => <option key={b}>{b}</option>)}</select></label>}
                <label>Location tag <input placeholder="e.g. binder, box:3" value={form.tags} onChange={e => setForm({ ...form, tags: e.target.value })} /></label>
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
