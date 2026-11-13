import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Finish, ImportPreview, ImportRow } from "../api";
import { CardImg, COND_LABEL, FINISH_LABEL, Modal, money, useDebounced, useToast } from "../ui";
import { allocationText } from "./CardDrawer";

type Dest = "collection" | "deck";

const EXAMPLE = `Paste a list, one card per line. Any of these work:

4 Lightning Bolt
1 Sol Ring [CMM]
1 Sol Ring (CMM) 410 *F*
2x Counterspell [Dominaria Remastered]

...or paste a CSV export (TCGplayer, ManaBox, Moxfield, Deckbox) including its header row.`;

export default function ImportDialog({ onClose, dest: dest0 = "collection" }: { onClose: () => void; dest?: Dest }) {
  const [tab, setTab] = useState<"paste" | "file">("paste");
  const [text, setText] = useState("");
  const [file, setFile] = useState<{ name: string; text: string } | null>(null);
  const [dest, setDest] = useState<Dest>(dest0);
  const [tags, setTags] = useState("");
  const [deck, setDeck] = useState({ name: "", format: "commander", building: true, owned: false });
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [show, setShow] = useState<"all" | "guessed" | "unmatched">("all");
  const [pg, setPg] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const qc = useQueryClient();
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const nav = useNavigate();

  const input = tab === "paste" ? text : file?.text || "";
  const readFile = async (f: File) => setFile({ name: f.name, text: await f.text() });

  const runPreview = async () => {
    setBusy(true);
    try {
      const p = await api.importPreview(input, tab === "file" ? file?.name : undefined);
      setPreview(p);
      setRows(p.rows.map(r => ({ ...r, include: r.status !== "unmatched" })));
      setShow(p.counts.guessed + p.counts.unmatched ? (p.counts.guessed ? "guessed" : "unmatched") : "all");
      setPg(0);
      if (!deck.name && tab === "file" && file) setDeck(d => ({ ...d, name: file.name.replace(/\.(csv|txt)$/i, "") }));
    } catch (e) { toast(String((e as Error).message), "err"); }
    setBusy(false);
  };

  const patch = (i: number, p: Partial<ImportRow>) => setRows(rs => rs.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const included = rows.filter(r => r.include && r.printing_id);
  const nCards = included.reduce((n, r) => n + r.qty, 0);
  const value = included.reduce((n, r) => n + (r.match?.price || 0) * r.qty, 0);
  const visible = useMemo(() => rows.map((r, i) => [r, i] as const).filter(([r]) => show === "all" || r.status === show), [rows, show]);
  const PAGE = 100;

  const commit = async () => {
    setBusy(true);
    try {
      const d = dest === "collection"
        ? { type: "collection", tags: tags.split(/[ ,]+/).filter(Boolean) }
        : { type: "deck", name: deck.name || "Imported deck", format: deck.format, status: deck.building ? "building" : "built", owned: deck.owned };
      const res = await api.importCommit(included, d);
      qc.invalidateQueries();
      toast(`Imported ${res.cards} cards` + (res.allocations?.length ? ` · ${allocationText(res.allocations)}` : ""));
      onClose();
      nav(res.deck ? `/decks/${res.deck}` : "/");
    } catch (e) { toast(String((e as Error).message), "err"); }
    setBusy(false);
  };

  if (!preview) {
    // Opened from the Decks page this is "New deck": the name and format come first and the destination is settled.
    const forDeck = dest0 === "deck";
    // A new deck only needs a name. Without a list it is created empty (in progress, so cards you add later that it
    // wants go straight in) and filled by hand from the deck page or the collection. A name already in use would
    // replace that deck's list, so it is refused.
    const slug = deck.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "deck";
    const taken = dest === "deck" && !!deck.name.trim() && (decks.data || []).some(d => d.slug === slug);
    const createEmpty = async () => {
      setBusy(true);
      try {
        const d = await api.createDeck({ name: deck.name.trim(), text: "", format: deck.format, status: "building" });
        qc.invalidateQueries();
        toast(`Created ${deck.name.trim()}`);
        onClose();
        nav(`/decks/${d.slug}`);
      } catch (e) { toast(String((e as Error).message), "err"); }
      setBusy(false);
    };
    const phone = window.matchMedia("(max-width: 760px)").matches;
    const deckForm = (
              <div className="deck-form">
                <input placeholder="Deck name" value={deck.name} onChange={e => setDeck({ ...deck, name: e.target.value })} />
                <select value={deck.format} onChange={e => setDeck({ ...deck, format: e.target.value })}>
                  {["commander", "standard", "pioneer", "modern", "legacy", "vintage", "pauper", "brawl", "other"].map(f => <option key={f}>{f}</option>)}
                </select>
                <label className="check"><input type="checkbox" checked={deck.owned} onChange={e => setDeck({ ...deck, owned: e.target.checked })} /> I already own these cards (add them to my collection, in this deck)</label>
                {!deck.owned && <label className="check"><input type="checkbox" checked={deck.building} onChange={e => setDeck({ ...deck, building: e.target.checked })} /> In progress: cards I add later go straight into this deck</label>}
              </div>
    );
    return (
      <Modal title={forDeck ? "New deck" : "Import cards"} onClose={onClose} wide footer={<>
        <button onClick={onClose}>Cancel</button>
        {forDeck && !input.trim()
          ? <button className="primary" disabled={!deck.name.trim() || taken || busy} onClick={createEmpty}>{busy ? "Creating…" : "Create empty deck"}</button>
          : <button className="primary" disabled={!input.trim() || busy || (forDeck && !deck.name.trim()) || taken} onClick={runPreview}>{busy ? "Reading…" : forDeck ? "Preview decklist" : "Preview import"}</button>}
      </>}>
        {forDeck && deckForm}
        {taken && <p className="bad small">You already have a deck with this name. Pick another name.</p>}
        {forDeck && <h3>The decklist <span className="muted small">(optional: leave it empty to add cards by hand)</span></h3>}
        <div className="tabs">
          <button className={tab === "paste" ? "on" : ""} onClick={() => setTab("paste")}>Paste list</button>
          <button className={tab === "file" ? "on" : ""} onClick={() => setTab("file")}>Upload .txt / .csv</button>
        </div>
        {tab === "paste" ? (
          <textarea className="paste" value={text} onChange={e => setText(e.target.value)} placeholder={EXAMPLE} rows={phone ? 7 : 12} autoFocus={!phone} />
        ) : (
          <div className="dropzone" onClick={() => fileRef.current?.click()}
               onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) readFile(f); }}>
            <input ref={fileRef} type="file" accept=".csv,.txt,text/csv,text/plain" hidden onChange={e => e.target.files?.[0] && readFile(e.target.files[0])} />
            {file ? <><b>{file.name}</b><div className="muted">{file.text.split(/\r?\n/).filter(Boolean).length} lines · click to choose another</div></>
                  : <><b>Drop a file here or click to browse</b><div className="muted">TCGplayer app export (CSV or text), ManaBox, Moxfield, Deckbox, or any list</div></>}
          </div>
        )}
        <p className="hint">From the TCGplayer app: open a collection → ⋮ → <b>Export</b>. For CSV, turn on <i>Product ID</i>, <i>Set Code</i> and <i>Card Number</i> in the app's CSV settings for exact printings.</p>

        {!forDeck && <><h3>Add to</h3>
        <div className="dest">
          <label className={`dest-opt ${dest === "collection" ? "on" : ""}`}>
            <input type="radio" checked={dest === "collection"} onChange={() => setDest("collection")} />
            <div><b>My collection</b><div className="muted small">Cards you own. Any in-progress deck that needs them gets them automatically.</div>
              {dest === "collection" && <input className="mt" placeholder="Optional location tag, e.g. binder or box:3" value={tags} onChange={e => setTags(e.target.value)} />}
            </div>
          </label>
          <label className={`dest-opt ${dest === "deck" ? "on" : ""}`}>
            <input type="radio" checked={dest === "deck"} onChange={() => setDest("deck")} />
            <div><b>A new deck</b><div className="muted small">The list becomes a decklist.</div>
              {dest === "deck" && deckForm}
            </div>
          </label>
        </div></>}
      </Modal>
    );
  }

  return (
    <Modal title="Review import" onClose={onClose} wide footer={<>
      <button onClick={() => setPreview(null)}>‹ Back</button>
      <span className="muted">{nCards} cards · {money(value)}</span>
      <button className="primary" disabled={!nCards || busy} onClick={commit}>
        {busy ? "Importing…" : dest === "deck" ? `Create deck (${nCards} cards)` : `Add ${nCards} cards to collection`}
      </button>
    </>}>
      <div className="import-summary">
        <span className="muted">Read as {preview.format === "csv" ? "CSV" : "text list"} · {rows.length} rows</span>
        <div className="seg">
          <button className={show === "all" ? "on" : ""} onClick={() => { setShow("all"); setPg(0); }}>All {rows.length}</button>
          <button className={`ok ${show === "all" ? "" : ""}`} disabled>✓ Matched {preview.counts.matched}</button>
          <button className={show === "guessed" ? "on warn" : "warn"} onClick={() => { setShow("guessed"); setPg(0); }}>? Check printing {rows.filter(r => r.status === "guessed").length}</button>
          <button className={show === "unmatched" ? "on bad" : "bad"} onClick={() => { setShow("unmatched"); setPg(0); }}>✕ Not found {rows.filter(r => r.status === "unmatched").length}</button>
        </div>
      </div>
      {preview.errors.length > 0 && <div className="error-box small">{preview.errors.slice(0, 5).join(" · ")}{preview.errors.length > 5 && ` · +${preview.errors.length - 5} more`}</div>}
      <table className="mini import">
        <thead><tr><th></th><th></th><th>Qty</th><th>Card</th><th>Printing</th><th>Finish</th><th>Cond.</th><th className="num">Price</th></tr></thead>
        <tbody>
          {visible.slice(pg * PAGE, pg * PAGE + PAGE).map(([r, i]) => (
            <tr key={i} className={`st-${r.status} ${r.include ? "" : "off"}`}>
              <td><input type="checkbox" checked={!!r.include} disabled={!r.printing_id} onChange={e => patch(i, { include: e.target.checked })} /></td>
              <td className="st">{r.status === "matched" ? "✓" : r.status === "guessed" ? "?" : "✕"}</td>
              <td><input className="qty" type="number" min={1} value={r.qty} onChange={e => patch(i, { qty: Math.max(1, +e.target.value) })} /></td>
              <td>
                <div className="imp-card">
                  {r.match && <CardImg src={r.match.image} name={r.match.name} />}
                  <div>
                    <div className="cname">{r.match?.name || r.name || "?"}</div>
                    {r.status !== "matched" && <div className="muted small" title={r.raw}>{preview.format === "text" ? `“${r.raw.trim().slice(0, 60)}” — ` : r.set_name || r.set_code ? `${r.set_name || r.set_code?.toUpperCase()} · ` : ""}{r.how}</div>}
                    {r.status === "unmatched" && <FixName onPick={(alt) => patch(i, alt)} />}
                  </div>
                </div>
              </td>
              <td>
                {r.match && (r.alternatives.length > 1 ? (
                  <select value={r.printing_id || ""} onChange={e => {
                    const alt = r.alternatives.find(a => a.printing_id === e.target.value)!;
                    patch(i, { printing_id: alt.printing_id, match: { ...alt, finish: alt.finishes.includes(r.match!.finish) ? r.match!.finish : alt.finishes[0] } });
                  }}>
                    {r.alternatives.map(a => <option key={a.printing_id} value={a.printing_id}>{a.set_code.toUpperCase()} #{a.number} · {a.set_name}{a.price ? ` · ${money(a.price)}` : ""}</option>)}
                  </select>
                ) : <span><span className="setcode">{r.match.set_code.toUpperCase()}</span> #{r.match.number} <span className="muted hide-sm">{r.match.set_name}</span></span>)}
              </td>
              <td>{r.match && <select value={r.match.finish} onChange={e => patch(i, { match: { ...r.match!, finish: e.target.value as Finish } })}>
                {(r.match.finishes.length ? r.match.finishes : ["nonfoil"]).map(f => <option key={f} value={f}>{FINISH_LABEL[f]}</option>)}</select>}</td>
              <td><select value={r.condition} onChange={e => patch(i, { condition: e.target.value })}>{Object.keys(COND_LABEL).map(k => <option key={k}>{k}</option>)}</select></td>
              <td className="num">{money(r.match?.price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {visible.length > PAGE && (
        <div className="pager small">
          <button disabled={pg === 0} onClick={() => setPg(pg - 1)}>‹</button>
          <span>{pg * PAGE + 1}–{Math.min((pg + 1) * PAGE, visible.length)} of {visible.length}</span>
          <button disabled={(pg + 1) * PAGE >= visible.length} onClick={() => setPg(pg + 1)}>›</button>
        </div>
      )}
      {!visible.length && <p className="muted pad">Nothing in this view.</p>}
    </Modal>
  );
}

/** For rows we couldn't match: search a name, pick it, then choose the printing. */
function FixName({ onPick }: { onPick: (p: Partial<ImportRow>) => void }) {
  const [t, setT] = useState("");
  const q = useDebounced(t, 250);
  const [hits, setHits] = useState<{ name: string }[]>([]);
  useEffect(() => { if (q.length >= 2) api.names(q).then(setHits).catch(() => setHits([])); else setHits([]); }, [q]);
  return (
    <div className="fixname">
      <input placeholder="Search the right card…" value={t} onChange={e => setT(e.target.value)} />
      {hits.length > 0 && <ul className="ac">{hits.slice(0, 6).map(h => (
        <li key={h.name} onClick={async () => {
          const ps = await api.prints(h.name);
          const det = await api.card(ps[0].id);
          const alts = det.printings.map(p => ({ printing_id: p.printing_id, name: h.name, set_code: p.set_code, set_name: p.set_name,
            number: p.collector_number, rarity: p.rarity, finish: p.finishes[0], finishes: p.finishes, image: p.image_small, price: p.price }));
          onPick({ status: "guessed", how: "picked by hand", printing_id: alts[0].printing_id, match: alts[0], alternatives: alts, include: true, name: h.name });
          setT(""); setHits([]);
        }}>{h.name}</li>))}</ul>}
    </div>
  );
}
