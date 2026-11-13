import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export const normTag = (t: string) => t.trim().toLowerCase().replace(/\s+/g, "-");

/** Quick toggles for the tags given to cards as they are scanned: decks, tags already in use, or a new one. */
export default function TagBar({ active, onChange }: { active: string[]; onChange: (tags: string[]) => void }) {
  const [text, setText] = useState("");
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const tags = useQuery({ queryKey: ["tags"], queryFn: api.tags });
  const deckName = new Map((decks.data || []).map(d => [`deck:${d.slug}`, d.name]));
  const label = (t: string) => deckName.has(t) ? `Deck: ${deckName.get(t)}` : t;
  const used = (tags.data || []).filter(t => !t.tag.startsWith("deck:")).sort((a, b) => b.cards - a.cards).slice(0, 8).map(t => t.tag);
  const deck = active.find(t => t.startsWith("deck:")) || "";       // scanned cards go to one deck at most
  const shown = [...new Set([...active.filter(t => !t.startsWith("deck:")), ...used])];
  const setDeck = (t: string) => onChange([...active.filter(x => !x.startsWith("deck:")), ...(t ? [t] : [])]);
  const toggle = (t: string) => onChange(active.includes(t) ? active.filter(x => x !== t) : [...active, t]);
  const add = () => {
    const t = normTag(text);
    if (t && !active.includes(t)) onChange([...active, t]);
    setText("");
  };
  return (
    <div className="tagbar">
      <label className={`deck-pick ${deck ? "on" : ""}`}>Deck <select value={deck} onChange={e => setDeck(e.target.value)}>
        <option value="">None (collection)</option>
        {deck && !deckName.has(deck) && <option value={deck}>{label(deck)}</option>}
        {(decks.data || []).map(d => <option key={d.slug} value={`deck:${d.slug}`}>{d.name}{d.status === "building" ? ` (${d.progress})` : ""}</option>)}
      </select></label>
      <span className="muted small">Tags</span>
      {shown.map(t => (
        <button key={t} className={`tag-toggle ${active.includes(t) ? "on" : ""} ${t.startsWith("deck:") ? "deck" : ""}`}
          aria-pressed={active.includes(t)} onClick={() => toggle(t)}>{active.includes(t) ? "✓ " : ""}{label(t)}</button>
      ))}
      <input className="tag-new" value={text} placeholder="+ new tag (e.g. box:3)" onChange={e => setText(e.target.value)}
        onKeyDown={e => e.key === "Enter" && add()} onBlur={add} />
    </div>
  );
}
