import { useEffect, useState } from "react";
import { api } from "../api";
import type { Finish, NameHit, Printing } from "../api";
import { CardImg, FINISH_LABEL, Mana, money, useDebounced } from "../ui";

/** Name search -> exact printing -> finish. Used to correct the scanner. */
export default function CardPicker({ initial = "", onPick, autoFocus = true }: {
  initial?: string; autoFocus?: boolean;
  onPick: (p: { printing_id: string; name: string; set_code: string; number: string; finish: Finish } | null) => void;
}) {
  const [text, setText] = useState(initial);
  const q = useDebounced(text, 200);
  const [hits, setHits] = useState<NameHit[]>([]);
  const [name, setName] = useState<string | null>(null);
  const [prints, setPrints] = useState<Printing[]>([]);
  const [sel, setSel] = useState<Printing | null>(null);
  const [finish, setFinish] = useState<Finish>("nonfoil");

  useEffect(() => {
    if (name || q.length < 2) { setHits([]); return; }
    let live = true;
    api.names(q).then(h => live && setHits(h)).catch(() => {});
    return () => { live = false; };
  }, [q, name]);
  useEffect(() => {
    onPick(sel && name ? { printing_id: sel.printing_id, name, set_code: sel.set_code, number: sel.collector_number, finish } : null);
  }, [sel, finish]); // eslint-disable-line

  const pickName = async (n: string) => {
    setName(n); setHits([]);
    const ps = await api.prints(n);
    if (!ps.length) return;
    const det = await api.card(ps[0].id);
    setPrints(det.printings); setSel(null);
  };

  if (!name) return (
    <div className="picker">
      <input className="big-search" autoFocus={autoFocus} placeholder="What card is it really? Type the name…" value={text}
        onChange={e => setText(e.target.value)} onKeyDown={e => e.key === "Enter" && hits[0] && pickName(hits[0].name)} />
      {hits.length > 0 && <ul className="ac big">{hits.map(h => (
        <li key={h.oracle_id} onClick={() => pickName(h.name)}><CardImg src={h.image_small} name={h.name} />
          <div><b>{h.name}</b> <Mana cost={h.mana_cost} /><div className="muted small">{h.type_line}</div></div></li>))}</ul>}
    </div>
  );
  return (
    <div className="picker">
      <div className="add-head"><button className="link" onClick={() => { setName(null); setSel(null); setPrints([]); }}>‹ different card</button><h3>{name}</h3>
        {sel && <select value={finish} onChange={e => setFinish(e.target.value as Finish)}>{sel.finishes.map(f => <option key={f} value={f}>{FINISH_LABEL[f]}</option>)}</select>}
      </div>
      <p className="muted small">Which printing? (match the set symbol / number on the card)</p>
      <div className="printing-grid">
        {prints.map(p => (
          <button key={p.printing_id} className={`pr ${sel?.printing_id === p.printing_id ? "on" : ""}`}
            onClick={() => { setSel(p); setFinish(p.finishes.includes(finish) ? finish : p.finishes[0]); }}>
            <CardImg src={p.image_small} name={p.set_code} />
            <span className="pr-set"><span className="setcode">{p.set_code.toUpperCase()}</span> #{p.collector_number}</span>
            <span className="pr-name">{p.set_name}</span>
            <span className="price">{money(p.price ?? p.price_foil)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
