import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import Collection from "./pages/Collection";
import Decks from "./pages/Decks";
import DeckDetail from "./pages/DeckDetail";
import Scan from "./pages/Scan";
import ImportDialog from "./components/ImportDialog";
import AddCard from "./components/AddCard";
import { money, useToast } from "./ui";

function CatalogBanner() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["catalog-status"], queryFn: api.catalogStatus,
    refetchInterval: q => (q.state.data?.job.state === "running" ? 1000 : false),
  });
  const s = status.data;
  const state = s?.job.state;
  useEffect(() => { if (state === "done") qc.invalidateQueries(); }, [state, qc]);
  if (!s) return null;
  const job = s.job;
  if (job.state === "running") {
    const pct = job.total ? Math.round((job.bytes / job.total) * 100) : 0;
    return (
      <div className="banner">
        Downloading the card database… <b>{job.cards.toLocaleString()}</b> printings loaded
        <div className="progress thin"><i className="a" style={{ width: `${pct}%` }} /></div>
        <span className="muted">{pct}%</span>
      </div>
    );
  }
  if (job.state === "error") return <div className="banner err">Card database download failed: {job.error} <button onClick={() => api.syncCatalog().then(() => status.refetch())}>Retry</button></div>;
  if (s.printings >= 20000 || job.state === "done") return null;
  return (
    <div className="banner">
      <span><b>One-time setup:</b> download the full card database (Scryfall, ~500 MB) so search, imports and prices are instant and work offline.
        Until then, cards are looked up online as you go.</span>
      <button className="primary" onClick={() => api.syncCatalog().then(() => status.refetch())}>Download now</button>
    </div>
  );
}

export default function App() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 30000 });
  const [importing, setImporting] = useState(false);
  const [adding, setAdding] = useState(false);
  const [menu, setMenu] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  return (
    <div className="app">
      <header className="top">
        <NavLink to="/" className="logo">mtg<b>stack</b></NavLink>
        <nav>
          <NavLink to="/" end>Collection</NavLink>
          <NavLink to="/decks">Decks</NavLink>
          <NavLink to="/scan">Scan</NavLink>
        </nav>
        <div className="top-right">
          {summary.data && <span className="worth" title="TCGplayer market value of everything you own">{summary.data.cards.toLocaleString()} cards · <b>{money(summary.data.value)}</b></span>}
          <button className="ghost-light hide-sm" onClick={() => setAdding(true)}>+ Add cards</button>
          <button className="primary" onClick={() => setImporting(true)}>Import</button>
          <div className="menu-wrap">
            <button className="icon light" onClick={() => setMenu(!menu)} aria-label="More">⋯</button>
            {menu && (
              <div className="menu" onMouseLeave={() => setMenu(false)}>
                <button className="show-sm" onClick={() => { setMenu(false); setAdding(true); }}>+ Add cards</button>
                <button onClick={async () => { setMenu(false); toast("Refreshing prices…", "info"); try { await fetch("/api/prices/refresh", { method: "POST" }); qc.invalidateQueries(); toast("Prices updated"); } catch { toast("Price refresh failed", "err"); } }}>Refresh prices</button>
                <button onClick={async () => { setMenu(false); await api.syncCatalog(); qc.invalidateQueries({ queryKey: ["catalog-status"] }); }}>Re-download card database</button>
                <a href="/api/export/tcgplayer" target="_blank">Export collection (TCGplayer CSV)</a>
                <a href="/docs" target="_blank">API docs</a>
                <a href="/classic">Classic UI</a>
              </div>
            )}
          </div>
        </div>
      </header>
      <CatalogBanner />
      <Routes>
        <Route path="/" element={<Collection />} />
        <Route path="/decks" element={<Decks />} />
        <Route path="/decks/:slug" element={<DeckDetail />} />
        <Route path="/scan" element={<Scan />} />
        <Route path="*" element={<Collection />} />
      </Routes>
      {importing && <ImportDialog onClose={() => setImporting(false)} />}
      {adding && <AddCard onClose={() => setAdding(false)} />}
    </div>
  );
}
