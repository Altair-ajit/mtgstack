import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export const normTag = (t: string) => t.trim().toLowerCase().replace(/\s+/g, "-");

/** Quick toggles for the tags given to cards as they are scanned: decks, tags already in use, or a new one. */
export default function TagBar({ active, onChange, noDeck = false }: { active: string[]; onChange: (tags: string[]) => void; noDeck?: boolean }) {
  const [text, setText] = useState("");
  const [naming, setNaming] = useState<string | null>(null);        // quick-add a deck: only a name is asked for
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const tags = useQuery({ queryKey: ["tags"], queryFn: api.tags });
  const deckName = new Map((decks.data || []).map(d => [`deck:${d.slug}`, d.name]));
  const label = (t: string) => deckName.has(t) ? `Deck: ${deckName.get(t)}` : t;
  const used = (tags.data || []).filter(t => !t.tag.startsWith("deck:")).sort((a, b) => b.cards - a.cards).slice(0, 8).map(t => t.tag);
  const deck = active.find(t => t.startsWith("deck:")) || "";       // scanned cards go to one deck at most
  const shown = [...new Set([...active.filter(t => !t.startsWith("deck:")), ...used])];
  const setDeck = (t: string) => onChange([...active.filter(x => !x.startsWith("deck:")), ...(t ? [t] : [])]);
  const slugOf = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "deck";
  const taken = !!naming?.trim() && (decks.data || []).some(d => d.slug === slugOf(naming));
  const createDeck = async () => {
    if (!naming?.trim() || taken || busy) return;
    setBusy(true);
    try {
      const d = await api.createDeck({ name: naming.trim(), text: "", format: "", status: "building" });
      await qc.invalidateQueries({ queryKey: ["decks"] });
      setDeck(`deck:${d.slug}`);
      setNaming(null);
    } catch { /* stays open to try again */ }
    setBusy(false);
  };
  const toggle = (t: string) => onChange(active.includes(t) ? active.filter(x => x !== t) : [...active, t]);
  const add = () => {
    const t = normTag(text);
    if (t && !active.includes(t)) onChange([...active, t]);
    setText("");
  };
  return (
    <div className="tagbar">
      {!noDeck && <label className={`deck-pick ${deck ? "on" : ""}`}>Deck <select value={deck} onChange={e => (e.target.value === "__new" ? setNaming("") : setDeck(e.target.value))}>
        <option value="">None (collection)</option>
        <option value="__new">+ New deck…</option>
        {deck && !deckName.has(deck) && <option value={deck}>{label(deck)}</option>}
        {(decks.data || []).map(d => <option key={d.slug} value={`deck:${d.slug}`}>{d.name}{d.status === "building" ? ` (${d.progress})` : ""}</option>)}
      </select></label>}
      {!noDeck && naming !== null && (
        <div className="deck-new">
          <input autoFocus placeholder="New deck name" value={naming} onChange={e => setNaming(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") createDeck(); if (e.key === "Escape") setNaming(null); }} />
          <button className="primary" disabled={!naming.trim() || taken || busy} onClick={createDeck}>{busy ? "Creating…" : "Create"}</button>
          <button className="link" onClick={() => setNaming(null)}>Cancel</button>
          {taken && <span className="bad small">You already have a deck with this name.</span>}
        </div>
      )}
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
