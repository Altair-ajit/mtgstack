import { useState } from "react";
import type { Facet, Filters } from "../api";
import { COLOR_LABEL, COND_LABEL, ColorDot, FINISH_LABEL, LANG_LABEL, RARITY_LABEL } from "../ui";

type ListKey = "set" | "rarity" | "color" | "type" | "finish" | "condition" | "language" | "tag";

function Section({ title, count, children, open: open0 = true }: { title: string; count?: number; children: React.ReactNode; open?: boolean }) {
  const [open, setOpen] = useState(open0);
  return (
    <section className="fsec">
      <button className="fsec-h" onClick={() => setOpen(!open)}>
        <span>{title}{count ? <b className="fcount">{count}</b> : null}</span><span className="caret">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="fsec-b">{children}</div>}
    </section>
  );
}

function CheckList({ facet, selected, onToggle, label, limit = 8, search }: {
  facet: Facet[]; selected: string[]; onToggle: (v: string) => void; label?: (f: Facet) => React.ReactNode;
  limit?: number; search?: boolean;
}) {
  const [more, setMore] = useState(false);
  const [q, setQ] = useState("");
  let items = facet.filter(f => f.count > 0 || selected.includes(f.value));
  if (q) items = items.filter(f => `${f.label || ""} ${f.value}`.toLowerCase().includes(q.toLowerCase()));
  const shown = more || q ? items : items.slice(0, limit);
  if (!facet.length) return <p className="muted small">Nothing here yet.</p>;
  return (
    <>
      {search && items.length > limit && <input className="fsearch" placeholder="Find…" value={q} onChange={e => setQ(e.target.value)} />}
      <ul className="checks">
        {shown.map(f => (
          <li key={f.value}>
            <label>
              <input type="checkbox" checked={selected.includes(f.value)} onChange={() => onToggle(f.value)} />
              <span className="lbl">{label ? label(f) : f.label || f.value}</span>
              <span className="n">{f.count}</span>
            </label>
          </li>
        ))}
      </ul>
      {!q && items.length > limit && (
        <button className="link small" onClick={() => setMore(!more)}>{more ? "Show less" : `Show all ${items.length}`}</button>
      )}
    </>
  );
}

export default function FilterPanel({ facets, filters, onChange, hideDeck }: {
  facets?: Record<string, Facet[]>; filters: Filters; onChange: (f: Filters) => void; hideDeck?: boolean;
}) {
  const fc = facets || {};
  const toggle = (k: ListKey) => (v: string) => {
    const cur = filters[k];
    onChange({ ...filters, [k]: cur.includes(v) ? cur.filter(x => x !== v) : [...cur, v] });
  };
  const active = (["set", "rarity", "color", "type", "finish", "condition", "language", "tag"] as ListKey[])
    .reduce((n, k) => n + filters[k].length, 0) + (filters.deck ? 1 : 0) + (filters.min_price || filters.max_price ? 1 : 0);
  return (
    <div className="filters">
      <div className="filters-h">
        <h3>Filters</h3>
        {active > 0 && <button className="link" onClick={() => onChange({ set: [], rarity: [], color: [], type: [], finish: [], condition: [], language: [], tag: [], deck: "", min_price: "", max_price: "" })}>Clear all ({active})</button>}
      </div>
      {!hideDeck && (
        <Section title="Decks" count={filters.deck ? 1 : 0}>
          <ul className="checks">
            {[{ value: "", label: "All cards" }, { value: "free", label: "Not in any deck" }, { value: "any", label: "In a deck" }].map(o => (
              <li key={o.value}><label><input type="radio" name="deckf" checked={filters.deck === o.value}
                onChange={() => onChange({ ...filters, deck: o.value })} /><span className="lbl">{o.label}</span></label></li>
            ))}
            {(fc.deck || []).map(d => (
              <li key={d.value}><label><input type="radio" name="deckf" checked={filters.deck === d.value}
                onChange={() => onChange({ ...filters, deck: d.value })} /><span className="lbl">{d.label}</span><span className="n">{d.count}</span></label></li>
            ))}
          </ul>
        </Section>
      )}
      <Section title="Location / tags" count={filters.tag.length} open={(fc.tag || []).length > 0}>
        <CheckList facet={fc.tag || []} selected={filters.tag} onToggle={toggle("tag")} />
      </Section>
      <Section title="Set" count={filters.set.length}>
        <CheckList facet={fc.set || []} selected={filters.set} onToggle={toggle("set")} search
          label={f => <><span className="setcode">{f.value.toUpperCase()}</span> {f.label}</>} />
      </Section>
      <Section title="Color" count={filters.color.length}>
        <CheckList facet={fc.color || []} selected={filters.color} onToggle={toggle("color")} limit={10}
          label={f => <><ColorDot c={f.value} /> {COLOR_LABEL[f.value]}</>} />
      </Section>
      <Section title="Card type" count={filters.type.length}>
        <CheckList facet={fc.type || []} selected={filters.type} onToggle={toggle("type")} limit={10} />
      </Section>
      <Section title="Rarity" count={filters.rarity.length}>
        <CheckList facet={fc.rarity || []} selected={filters.rarity} onToggle={toggle("rarity")}
          label={f => RARITY_LABEL[f.value] || f.value} />
      </Section>
      <Section title="Printing" count={filters.finish.length}>
        <CheckList facet={fc.finish || []} selected={filters.finish} onToggle={toggle("finish")}
          label={f => FINISH_LABEL[f.value] || f.value} />
      </Section>
      <Section title="Condition" count={filters.condition.length}>
        <CheckList facet={fc.condition || []} selected={filters.condition} onToggle={toggle("condition")}
          label={f => COND_LABEL[f.value] || f.value} />
      </Section>
      <Section title="Language" count={filters.language.length} open={false}>
        <CheckList facet={fc.language || []} selected={filters.language} onToggle={toggle("language")}
          label={f => LANG_LABEL[f.value] || f.value} />
      </Section>
      <Section title="Market price" count={filters.min_price || filters.max_price ? 1 : 0}>
        <div className="price-range">
          <input type="number" min="0" step="0.5" placeholder="$ min" value={filters.min_price}
            onChange={e => onChange({ ...filters, min_price: e.target.value })} />
          <span>to</span>
          <input type="number" min="0" step="0.5" placeholder="$ max" value={filters.max_price}
            onChange={e => onChange({ ...filters, max_price: e.target.value })} />
        </div>
      </Section>
    </div>
  );
}
