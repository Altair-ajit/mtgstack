import { useEffect, useState } from "react";
import { api } from "../api";
import type { Finish, Printing } from "../api";
import CardPicker from "./CardPicker";
import { CardImg, COND_LABEL, FINISH_LABEL, Modal, money, TagChip } from "../ui";

/** A scanned card waiting in the batch: nothing is in the collection until the batch is added. */
export interface BatchItem {
  id: number; printing_id: string; name: string; set_code: string; set_name: string; number: string; image: string | null;
  finish: Finish; finishes: Finish[]; condition: string; language: string; qty: number; tags: string[];
  prices: Partial<Record<Finish, number | null>>; review: boolean;
  /** Scanner Lab: the saved scan and what the scanner said, so a correction made here teaches it */
  scan_id?: string; orig?: { printing_id: string; finish: Finish };
}

export const itemPrice = (i: BatchItem) => i.prices[i.finish] ?? null;
export const sameStack = (a: BatchItem, b: BatchItem) => a.printing_id === b.printing_id && a.finish === b.finish
  && a.condition === b.condition && a.language === b.language && [...a.tags].sort().join(" ") === [...b.tags].sort().join(" ");
/** The same card going to the same place, whatever the printing and finish. */
export const sameCard = (a: BatchItem, b: BatchItem) => a.name === b.name && a.condition === b.condition
  && [...a.tags].sort().join(" ") === [...b.tags].sort().join(" ");

const printingFields = (p: Printing, finish: Finish) => ({
  printing_id: p.printing_id, set_code: p.set_code, set_name: p.set_name, number: p.collector_number, image: p.image_small,
  finishes: p.finishes, finish: p.finishes.includes(finish) ? finish : p.finishes[0] || "nonfoil",
  prices: { nonfoil: p.price, foil: p.price_foil } as BatchItem["prices"], review: false,
});

/** Everything a batch row needs for a printing picked by hand (search by name, or a different card). */
export async function itemFor(printing_id: string, finish: Finish, base: Pick<BatchItem, "condition" | "tags">): Promise<BatchItem> {
  const det = await api.card(printing_id);
  const p = det.printings.find(x => x.printing_id === printing_id);
  return {
    id: Date.now(), name: det.card.name, language: "en", qty: 1, ...base,
    ...(p ? printingFields(p, finish) : { printing_id, set_code: det.card.set_code, set_name: det.card.set_name, number: det.card.collector_number,
      image: null, finishes: det.card.finishes, finish, prices: {}, review: false }),
  };
}

/** Pick the right printing for a row (the usual correction), or replace the card altogether. */
function EditPrinting({ item, onChange, onClose }: { item: BatchItem; onChange: (patch: Partial<BatchItem>) => void; onClose: () => void }) {
  const [prints, setPrints] = useState<Printing[] | null>(null);
  const [other, setOther] = useState(false);
  const [pick, setPick] = useState<{ printing_id: string; finish: Finish } | null>(null);
  useEffect(() => {
    let live = true;
    api.prints(item.name, true).catch(() => []).then(() => api.card(item.printing_id))
      .then(d => live && setPrints(d.printings)).catch(() => live && setPrints([]));
    return () => { live = false; };
  }, [item.printing_id]);
  return (
    <Modal title={other ? "What card is it?" : `${item.name}: which printing?`} wide onClose={onClose} footer={<>
      <button className="link" onClick={() => { setOther(!other); setPick(null); }}>{other ? "‹ Back to printings" : "Not this card? Search by name"}</button>
      {other && <button className="primary" disabled={!pick} onClick={async () => {
        if (!pick) return;
        const it = await itemFor(pick.printing_id, pick.finish, item);
        onChange({ ...it, id: item.id, qty: item.qty });
        onClose();
      }}>Use this card</button>}
    </>}>
      {other ? <CardPicker onPick={setPick} /> : prints === null ? <p className="muted">Loading printings…</p> : (
        <div className="printing-grid">
          {prints.map(p => (
            <button key={p.printing_id} className={`pr ${p.printing_id === item.printing_id ? "on" : ""}`}
              onClick={() => { onChange(printingFields(p, item.finish)); onClose(); }}>
              <CardImg src={p.image_small} name={p.set_code} />
              <span className="pr-set"><span className="setcode">{p.set_code.toUpperCase()}</span> #{p.collector_number}</span>
              <span className="pr-name">{p.set_name}</span>
              <span className="price">{money(p.price ?? p.price_foil)}</span>
            </button>
          ))}
        </div>
      )}
    </Modal>
  );
}

export function BatchRow({ item, fresh, onChange, onRemove }: {
  item: BatchItem; fresh: boolean; onChange: (patch: Partial<BatchItem>) => void; onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const price = itemPrice(item);
  const finishes = item.finishes.includes(item.finish) ? item.finishes : [item.finish, ...item.finishes];
  return (
    <li className={`batch-row ${fresh ? "fresh" : ""} ${item.review ? "review" : ""}`}>
      <CardImg src={item.image} name={item.name} />
      <div className="br-main">
        <b>{item.name}</b>
        <button className="br-set" onClick={() => setEditing(true)} title="Change the printing">
          <span className="setcode">{item.set_code.toUpperCase()}</span> #{item.number} <span className="muted">{item.set_name}</span> ✎
        </button>
        {item.review && <button className="br-check" onClick={() => setEditing(true)}>Check the printing</button>}
        {item.tags.length > 0 && <div className="br-tags">{item.tags.map(t =>
          <TagChip key={t} t={`${t} ×`} onClick={() => onChange({ tags: item.tags.filter(x => x !== t) })} />)}</div>}
      </div>
      <div className="br-opts">
        <select value={item.finish} aria-label="Finish" onChange={e => onChange({ finish: e.target.value as Finish })}>
          {finishes.map(f => <option key={f} value={f}>{FINISH_LABEL[f]}</option>)}</select>
        <select value={item.condition} aria-label="Condition" onChange={e => onChange({ condition: e.target.value })}>
          {Object.keys(COND_LABEL).map(k => <option key={k} value={k}>{k}</option>)}</select>
      </div>
      <div className="stepper" role="group" aria-label="Quantity">
        <button onClick={() => (item.qty > 1 ? onChange({ qty: item.qty - 1 }) : onRemove())} aria-label="One fewer">−</button>
        <input inputMode="numeric" value={item.qty} aria-label="Quantity"
          onChange={e => { const n = parseInt(e.target.value, 10); if (n > 0 && n < 1000) onChange({ qty: n }); }} />
        <button onClick={() => onChange({ qty: item.qty + 1 })} aria-label="One more">+</button>
      </div>
      <div className="br-price"><b className="price">{money(price === null ? null : price * item.qty)}</b>
        {item.qty > 1 && price !== null && <span className="muted small">{money(price)} each</span>}</div>
      <button className="icon" onClick={onRemove} aria-label={`Remove ${item.name}`}>×</button>
      {editing && <EditPrinting item={item} onChange={onChange} onClose={() => setEditing(false)} />}
    </li>
  );
}
