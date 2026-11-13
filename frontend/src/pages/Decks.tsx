import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import ImportDialog from "../components/ImportDialog";
import { Empty, Modal, money } from "../ui";

const FORMATS = ["commander", "standard", "pioneer", "modern", "legacy", "vintage", "pauper", "brawl", "other"];
const slug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "deck";

export default function Decks() {
  const { data, isLoading } = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const [importing, setImporting] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [deck, setDeck] = useState({ name: "", format: "commander" });
  const nav = useNavigate();
  // a deck of the same name would be replaced by the new list: make the name unique first
  const taken = (data || []).some(d => d.slug === slug(deck.name));
  const startScan = () => nav(`/scan?deck=${encodeURIComponent(deck.name.trim())}&format=${encodeURIComponent(deck.format)}`);
  const building = (data || []).filter(d => d.status === "building");
  const built = (data || []).filter(d => d.status !== "building");

  const Tile = ({ d }: { d: NonNullable<typeof data>[number] }) => {
    const w = Math.max(d.wanted, 1);
    return (
      <Link to={`/decks/${d.slug}`} className="deck-tile">
        <div className="deck-art" style={d.cover_image ? { backgroundImage: `url(${d.cover_image})` } : undefined}>
          <span className={`badge ${d.status === "building" ? "wip" : "done"}`}>{d.status === "building" ? "In progress" : "Built"}</span>
        </div>
        <div className="deck-body">
          <div className="deck-name">{d.name}</div>
          <div className="muted small">{d.format || "—"}{d.commander ? ` · ${d.commander}` : ""}</div>
          <div className="progress"><i className="a" style={{ width: `${(d.in_deck / w) * 100}%` }} /><i className="f" style={{ width: `${(d.free / w) * 100}%` }} /></div>
          <div className="deck-stats">
            <span><b>{d.in_deck}</b>/{d.wanted}</span>
            {d.free > 0 && <span className="move">{d.free} to move</span>}
            {d.missing > 0 && <span className="buy">{d.missing} to buy · {money(d.missing_cost)}</span>}
            {d.missing === 0 && d.free === 0 && <span className="okc">complete</span>}
          </div>
          <div className="muted small">Value of cards in deck {money(d.owned_value)}</div>
        </div>
      </Link>
    );
  };

  return (
    <div className="page">
      <main className="content wide">
        <div className="page-head">
          <div><h1>Decks</h1><div className="stats"><span><b>{data?.length || 0}</b> decks</span>{building.length > 0 && <span><b>{building.length}</b> in progress</span>}</div></div>
          <div className="actions">
            <button onClick={() => setScanning(true)}>📷 Scan a deck in</button>
            <button className="primary" onClick={() => setImporting(true)}>+ New deck from list</button>
          </div>
        </div>
        {isLoading && <p className="muted">Loading…</p>}
        {data && !data.length && (
          <Empty title="No decks yet">
            Paste a decklist (Arena, Moxfield, MTGO, TCGplayer) to create one. Mark it <b>in progress</b> and every card it needs
            that you add later goes straight into it.
            <div className="mt actions" style={{ justifyContent: "center" }}><button className="primary" onClick={() => setImporting(true)}>+ New deck from list</button>
              <button onClick={() => setScanning(true)}>📷 Scan a deck in</button></div>
          </Empty>
        )}
        {building.length > 0 && <><h2 className="section-h">In progress</h2><div className="deck-grid">{building.map(d => <Tile key={d.slug} d={d} />)}</div></>}
        {built.length > 0 && <><h2 className="section-h">Built</h2><div className="deck-grid">{built.map(d => <Tile key={d.slug} d={d} />)}</div></>}
      </main>
      {importing && <ImportDialog dest="deck" onClose={() => setImporting(false)} />}
      {scanning && (
        <Modal title="Scan a deck in" onClose={() => setScanning(false)} footer={<>
          <button onClick={() => setScanning(false)}>Cancel</button>
          <button className="primary" disabled={!deck.name.trim() || taken} onClick={startScan}>Start scanning</button>
        </>}>
          <p className="muted">Name the deck, then scan its cards with the camera. You can fix printings and counts before the deck is created; the cards go into your collection, in this deck.</p>
          <div className="deck-form">
            <input autoFocus placeholder="Deck name" value={deck.name} onChange={e => setDeck({ ...deck, name: e.target.value })}
              onKeyDown={e => e.key === "Enter" && deck.name.trim() && !taken && startScan()} />
            <select value={deck.format} onChange={e => setDeck({ ...deck, format: e.target.value })}>{FORMATS.map(f => <option key={f}>{f}</option>)}</select>
          </div>
          {taken && <p className="bad small">You already have a deck with this name. Pick another name.</p>}
        </Modal>
      )}
    </div>
  );
}
