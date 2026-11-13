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
  const building = (decks.data || []).filter(d => d.status === "building").map(d => `deck:${d.slug}`);
  const used = (tags.data || []).filter(t => !t.tag.startsWith("deck:")).sort((a, b) => b.cards - a.cards).slice(0, 8).map(t => t.tag);
  const shown = [...new Set([...active, ...building, ...used])];
  const others = (decks.data || []).filter(d => !shown.includes(`deck:${d.slug}`));
  const toggle = (t: string) => onChange(active.includes(t) ? active.filter(x => x !== t) : [...active, t]);
  const add = () => {
    const t = normTag(text);
    if (t && !active.includes(t)) onChange([...active, t]);
    setText("");
  };
  return (
    <div className="tagbar">
      <span className="muted small">Tag what you scan</span>
      {shown.map(t => (
        <button key={t} className={`tag-toggle ${active.includes(t) ? "on" : ""} ${t.startsWith("deck:") ? "deck" : ""}`}
          aria-pressed={active.includes(t)} onClick={() => toggle(t)}>{active.includes(t) ? "✓ " : ""}{label(t)}</button>
      ))}
      {others.length > 0 && (
        <select value="" onChange={e => e.target.value && toggle(e.target.value)} aria-label="Put scanned cards in a deck">
          <option value="">Deck…</option>
          {others.map(d => <option key={d.slug} value={`deck:${d.slug}`}>{d.name}</option>)}
        </select>
      )}
      <input className="tag-new" value={text} placeholder="+ new tag (e.g. box:3)" onChange={e => setText(e.target.value)}
        onKeyDown={e => e.key === "Enter" && add()} onBlur={add} />
    </div>
  );
}
