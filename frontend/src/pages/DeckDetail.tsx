import { useMemo, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import type { Entry } from "../api";
import CardDrawer from "../components/CardDrawer";
import AddCard from "../components/AddCard";
import { FinishBadge, HoverCard, Mana, Modal, money, Rarity, TagChip, useDebounced, useToast } from "../ui";

const TYPES = ["Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land"];
const ptype = (t: string) => TYPES.find(k => (t || "").includes(k)) || "Other";
const BOARD_ORDER = ["commander", "companion", "main", "side", "maybe"];
const BOARD_LABEL: Record<string, string> = { commander: "Commander", companion: "Companion", main: "Main deck", side: "Sideboard", maybe: "Maybeboard" };

export default function DeckDetail() {
  const { slug = "" } = useParams();
  const [tab, setTab] = useState<"all" | "owned" | "move" | "buy">("all");
  const [text, setText] = useState("");
  const q = useDebounced(text, 250);
  const [sort, setSort] = useState("type");
  const [group, setGroup] = useState<"type" | "board" | "cmc" | "status" | "none">("type");
  const [open, setOpen] = useState<string | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Entry | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const [renaming, setRenaming] = useState<string | null>(null);
  // A deck's cards carry the tag deck:<slug> and the slug comes from the name, so a rename moves the tag (the server
  // does it) and this page's address. The scanner's remembered deck choice follows too.
  const rename = async () => {
    if (renaming === null || !renaming.trim()) return;
    try {
      const r = await api.patchDeck(slug, { name: renaming.trim() });
      if (r.slug !== slug) {
        const saved = (localStorage.getItem("mtg.scan.tags") || "").split(/[ ,]+/).filter(Boolean)
          .map(t => (t === `deck:${slug}` || t.startsWith(`deck:${slug}:`) ? `deck:${r.slug}${t.slice(`deck:${slug}`.length)}` : t));
        localStorage.setItem("mtg.scan.tags", saved.join(" "));
      }
      setRenaming(null);
      await qc.invalidateQueries();
      toast(`Renamed to ${r.name}`);
      if (r.slug !== slug) nav(`/decks/${r.slug}`, { replace: true });
    } catch (e) { toast(String((e as Error).message), "err"); }
  };

  const query = [tab !== "all" && `status:${tab}`, q, `sort:${sort === "type" ? "type" : sort}`].filter(Boolean).join(" ");
  const { data, error } = useQuery({ queryKey: ["deck", slug, query], queryFn: () => api.deck(slug, query), placeholderData: keepPreviousData });
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const run = async (p: Promise<unknown>, msg: string) => {
    try { await p; toast(msg); qc.invalidateQueries(); } catch (e) { toast(String((e as Error).message), "err"); }
  };

  const groups = useMemo(() => {
    const g = new Map<string, Entry[]>();
    for (const e of data?.entries || []) {
      const k = group === "type" ? (e.board === "commander" ? "Commander" : e.board === "maybe" ? "Maybeboard" : e.board === "side" ? "Sideboard" : ptype(e.type_line))
        : group === "board" ? BOARD_LABEL[e.board] || e.board
        : group === "cmc" ? (e.type_line?.includes("Land") ? "Lands" : `${Math.round(e.cmc || 0)} mana`)
        : group === "status" ? { owned: "In deck", move: "To move from collection", buy: "To buy" }[e.status]
        : "Cards";
      if (!g.has(k)) g.set(k, []);
      g.get(k)!.push(e);
    }
    const order = group === "type" ? ["Commander", ...TYPES, "Other", "Sideboard", "Maybeboard"]
      : group === "board" ? BOARD_ORDER.map(b => BOARD_LABEL[b]) : [...g.keys()];
    return [...g.entries()].sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  }, [data, group]);

  if (error) return <div className="page"><main className="content"><div className="error-box">{String((error as Error).message)}</div></main></div>;
  if (!data) return <div className="page"><main className="content"><p className="muted">Loading…</p></main></div>;
  const d = data.deck, t = data.totals, building = d.status === "building";
  const cover = data.entries.find(e => e.board === "commander") || data.entries[0];

  return (
    <div className="page">
      <main className="content wide">
        <div className="crumbs"><Link to="/decks">Decks</Link> › {d.name}</div>
        <div className="deck-hero">
          {cover?.image_normal && <div className="hero-art" style={{ backgroundImage: `url(${cover.image_normal})` }} />}
          <div className="hero-main">
            {renaming === null ? (
              <h1>{d.name} <button className="icon rename" onClick={() => setRenaming(d.name)} aria-label="Rename deck" title="Rename deck">✎</button></h1>
            ) : (
              <div className="deck-rename">
                <input autoFocus value={renaming} maxLength={80} onChange={e => setRenaming(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") rename(); if (e.key === "Escape") setRenaming(null); }} />
                <button className="primary" disabled={!renaming.trim() || renaming.trim() === d.name} onClick={rename}>Save</button>
                <button className="link" onClick={() => setRenaming(null)}>Cancel</button>
              </div>
            )}
            <div className="muted">{d.format}</div>
            <div className="seg mt">
              <button className={building ? "on" : ""} onClick={() => run(api.patchDeck(slug, { status: "building" }), "In progress: new cards it needs go straight in")}>In progress</button>
              <button className={!building ? "on" : ""} onClick={() => run(api.patchDeck(slug, { status: "built" }), "Marked built")}>Built</button>
            </div>
            {building && <p className="hint">Cards you add or import that this deck still needs are put straight into it. Cards already in your collection show under <b>To move</b>.</p>}
          </div>
          <div className="hero-stats">
            <div className="big-progress"><b>{t.in_deck}</b><span>/{t.wanted}</span></div>
            <div className="progress"><i className="a" style={{ width: `${(t.in_deck / Math.max(t.wanted, 1)) * 100}%` }} /><i className="f" style={{ width: `${(t.free / Math.max(t.wanted, 1)) * 100}%` }} /></div>
            <div className="hero-row"><span className="move"><b>{t.free}</b> to move</span><span className="buy"><b>{t.missing}</b> to buy</span><span>{money(t.missing_cost)} to finish</span></div>
          </div>
        </div>

        <div className="toolbar">
          <div className="tabs inline">
            {([["all", "All", t.wanted], ["owned", "In deck", t.in_deck], ["move", "To move", t.free], ["buy", "To buy", t.missing]] as const).map(([k, l, n]) => (
              <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l} <span className="n">{n}</span></button>
            ))}
          </div>
          <div className="search"><input placeholder="Filter: name, or t:creature cmc<=3 c:g price>2" value={text} onChange={e => setText(e.target.value)} /></div>
          <label className="sortsel">Sort <select value={sort} onChange={e => setSort(e.target.value)}>
            {[["type", "Type"], ["cmc", "Mana value"], ["name", "Name"], ["price", "Price"], ["color", "Color"], ["rarity", "Rarity"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          <label className="sortsel">Group <select value={group} onChange={e => setGroup(e.target.value as typeof group)}>
            {[["type", "Card type"], ["board", "Board"], ["cmc", "Mana value"], ["status", "Have / move / buy"], ["none", "None"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        </div>
        <div className="actions">
          {t.free > 0 && <button className="primary" onClick={() => run(api.pull(slug), `Moved ${t.free} cards in from your collection`)}>Move all {t.free} in from collection</button>}
          <button onClick={() => setAdding(true)}>+ Add card</button>
          {t.missing > 0 && <button onClick={() => setExporting("missing")}>Buy list ({t.missing})</button>}
          <button onClick={() => setExporting("arena")}>Export</button>
          <Link className="btn" to={`/?deck=${slug}`}>Show copies in collection</Link>
          <span className="spacer" />
          <button className="link" onClick={() => { if (confirm("Take every card out of this deck (they stay in your collection)?")) run(api.release(slug), "Cards returned to collection"); }}>Return all cards</button>
          <button className="link danger" onClick={async () => { if (confirm("Delete this deck? Its cards stay in your collection.")) { await api.deleteDeck(slug); qc.invalidateQueries(); nav("/decks"); } }}>Delete deck</button>
        </div>

        {groups.map(([g, es]) => (
          <section key={g} className="group">
            <h3 className="group-h">{g} <span className="muted">{es.reduce((n, e) => n + e.wanted, 0)}</span></h3>
            <table className="cards deck">
              <tbody>
                {es.map(e => (
                  <tr key={e.entry_id} onClick={() => setOpen(e.card_printing_id)}>
                    <td className="thumb"><HoverCard small={e.image_small} large={e.image_normal} name={e.name} /></td>
                    <td className="num qtycol">{e.wanted}</td>
                    <td><div className="cname">{e.name} <FinishBadge f={e.finish} /></div><div className="sub muted">{e.type_line}</div></td>
                    <td className="hide-sm"><Mana cost={e.mana_cost} /></td>
                    <td className="hide-sm"><Rarity r={e.rarity} /></td>
                    <td><span className={`status ${e.status === "owned" ? "ok" : e.status}`}>
                      {e.status === "owned" ? `✓ ${e.have}/${e.wanted}` : e.status === "move" ? `${e.have}/${e.wanted} · move ${e.to_move}` : `${e.have}/${e.wanted} · buy ${e.to_buy}`}
                    </span></td>
                    <td className="num price">{money(e.price)}</td>
                    <td className="right act" onClick={ev => ev.stopPropagation()}>
                      {e.where.slice(0, 2).map(w => (
                        <button key={w.lot_id} className="small" title={`From: ${w.tags || "collection (untagged)"}`}
                          onClick={() => run(api.send(w.lot_id, { qty: Math.min(w.qty, e.to_move), deck: slug, board: e.board }), `Moved ${e.name} in`)}>
                          Move in <span className="muted">{w.set.toUpperCase()}{w.tags ? ` · ${w.tags.split(" ")[0]}` : ""}</span>
                        </button>
                      ))}
                      {e.in_deck_lots.slice(0, 1).map(l => (
                        <button key={l.lot_id} className="small ghost" title="Send this copy back to your collection"
                          onClick={() => run(api.send(l.lot_id, { qty: 1, deck: null }), `${e.name} returned to collection`)}>↩</button>
                      ))}
                      {e.in_other_decks.slice(0, 1).map(od => <TagChip key={od} t={od} />)}
                      <button className="small ghost" title="Change the count, or take it off this deck" aria-label={`Edit ${e.name} in this deck`}
                        onClick={() => setEditing(e)}>Edit</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ))}
        {!data.entries.length && <p className="muted pad">Nothing in this view.</p>}
      </main>
      {open && <CardDrawer printingId={open} ids={[...new Set(groups.flatMap(([, es]) => es.map(e => e.card_printing_id)))]} onNavigate={setOpen}
        preview={Object.fromEntries((data.entries || []).map(e => [e.card_printing_id, { name: e.name, image: e.image_normal || e.image_small }]))} onClose={() => setOpen(null)} />}
      {editing && <EntryEdit slug={slug} e={editing} onClose={() => setEditing(null)} />}
      {adding && <AddCard deck={slug} onClose={() => setAdding(false)} />}
      {exporting && <ExportModal slug={slug} decks={(decks.data || []).length} initial={exporting} onClose={() => setExporting(null)} />}
    </div>
  );
}

/** One card on the deck's list: how many the deck wants, or off the list altogether. */
function EntryEdit({ slug, e, onClose }: { slug: string; e: Entry; onClose: () => void }) {
  const min = Math.max(1, e.in_deck);
  const [n, setN] = useState(e.wanted);
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  const run = async (p: Promise<unknown>, msg: string) => {
    setBusy(true);
    try { await p; toast(msg); await qc.invalidateQueries(); onClose(); }
    catch (err) { toast(String((err as Error).message), "err"); setBusy(false); }
  };
  return (
    <Modal title={e.name} onClose={onClose} footer={<>
      <button onClick={onClose}>Cancel</button>
      <button className="primary" disabled={busy || n === e.wanted} onClick={() => run(api.setEntry(slug, e.entry_id, n), `${e.name}: ${n} wanted`)}>Save</button>
    </>}>
      <div className="entry-edit">
        <div><b>Copies this deck wants</b>
          <div className="muted small">{e.in_deck} in the deck now{e.to_move ? `, ${e.to_move} to move in` : ""}{e.to_buy ? `, ${e.to_buy} to buy` : ""}</div></div>
        <div className="stepper" role="group" aria-label="Copies wanted">
          <button onClick={() => setN(Math.max(min, n - 1))} disabled={n <= min} aria-label="One fewer">−</button>
          <input inputMode="numeric" value={n} aria-label="Copies wanted" onChange={ev => setN(Math.max(min, Math.min(999, parseInt(ev.target.value, 10) || min)))} />
          <button onClick={() => setN(Math.min(999, n + 1))} aria-label="One more">+</button>
        </div>
      </div>
      {e.in_deck > 1 && n <= min && <p className="muted small">{e.in_deck} copies are in the deck. To want fewer, move a copy out first (↩).</p>}
      <button className="danger wide-btn" disabled={busy} onClick={() => run(api.removeEntry(slug, e.entry_id),
        e.in_deck ? `${e.name} is off the deck; ${e.in_deck} cop${e.in_deck === 1 ? "y" : "ies"} back in the collection` : `${e.name} is off the deck`)}>
        Remove from this deck</button>
      {e.in_deck > 0 && <p className="muted small">The {e.in_deck === 1 ? "copy" : `${e.in_deck} copies`} in the deck stay{e.in_deck === 1 ? "s" : ""} in your collection.</p>}
    </Modal>
  );
}

function ExportModal({ slug, initial, onClose }: { slug: string; decks: number; initial: string; onClose: () => void }) {
  const [fmt, setFmt] = useState(initial);
  const [source, setSource] = useState("list");
  const { data } = useQuery({
    queryKey: ["export", slug, fmt, source],
    queryFn: () => (fmt === "missing" ? api.missing(slug) : api.exportDeck(slug, fmt, source)),
  });
  const toast = useToast();
  return (
    <Modal title="Export deck" onClose={onClose} footer={<>
      <button onClick={onClose}>Close</button>
      <button className="primary" onClick={() => { navigator.clipboard.writeText(data || ""); toast("Copied"); }}>Copy</button>
    </>}>
      <div className="tabs">
        {[["arena", "MTG Arena"], ["mtgo", "MTGO / plain"], ["moxfield", "Moxfield"], ["tcgplayer", "TCGplayer mass entry"], ["missing", "Buy list (missing)"]].map(([k, l]) =>
          <button key={k} className={fmt === k ? "on" : ""} onClick={() => setFmt(k)}>{l}</button>)}
      </div>
      {fmt !== "missing" && <label className="check"><input type="checkbox" checked={source === "owned"} onChange={e => setSource(e.target.checked ? "owned" : "list")} /> Only the copies actually in this deck (with their printings)</label>}
      {(fmt === "missing" || fmt === "tcgplayer") && <p className="hint">Paste into <a href="https://www.tcgplayer.com/massentry" target="_blank" rel="noreferrer">tcgplayer.com/massentry</a> to price or buy.</p>}
      <textarea className="paste" readOnly rows={16} value={data || ""} />
    </Modal>
  );
}
