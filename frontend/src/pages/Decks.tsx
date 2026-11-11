import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import ImportDialog from "../components/ImportDialog";
import { Empty, money } from "../ui";

export default function Decks() {
  const { data, isLoading } = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const [importing, setImporting] = useState(false);
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
          <button className="primary" onClick={() => setImporting(true)}>+ New deck from list</button>
        </div>
        {isLoading && <p className="muted">Loading…</p>}
        {data && !data.length && (
          <Empty title="No decks yet">
            Paste a decklist (Arena, Moxfield, MTGO, TCGplayer) to create one. Mark it <b>in progress</b> and every card it needs
            that you add later goes straight into it.
            <div className="mt"><button className="primary" onClick={() => setImporting(true)}>+ New deck from list</button></div>
          </Empty>
        )}
        {building.length > 0 && <><h2 className="section-h">In progress</h2><div className="deck-grid">{building.map(d => <Tile key={d.slug} d={d} />)}</div></>}
        {built.length > 0 && <><h2 className="section-h">Built</h2><div className="deck-grid">{built.map(d => <Tile key={d.slug} d={d} />)}</div></>}
      </main>
      {importing && <ImportDialog dest="deck" onClose={() => setImporting(false)} />}
    </div>
  );
}
