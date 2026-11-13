import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { LOCAL, NATIVE } from "./local/boot";
import Collection from "./pages/Collection";
import Decks from "./pages/Decks";
import DeckDetail from "./pages/DeckDetail";
import Scan from "./pages/Scan";
import Lab from "./pages/Lab";
import LabLive from "./pages/LabLive";
import ImportDialog from "./components/ImportDialog";
import AddCard from "./components/AddCard";
import PhoneDialog from "./components/PhoneDialog";
import UpdateBanner from "./components/UpdateBanner";
import { money, useToast } from "./ui";

function CatalogBanner() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["catalog-status"], queryFn: api.catalogStatus,
    // the app looks again while it does not yet know what the site offers (just started, or offline)
    refetchInterval: q => (q.state.data?.job.state === "running" ? 1000 : NATIVE && !q.state.data?.remote ? 15000 : false),
  });
  const s = status.data;
  const state = s?.job.state;
  useEffect(() => { if (state === "done") qc.invalidateQueries(); }, [state, qc]);
  // The installed app keeps its card database current by itself: once per start, when it has none or the site has
  // a newer one. The old one stays in use until the new one is complete, and when offline nothing changes.
  const auto = useRef(false);
  const wanted = NATIVE && !!s && state === "idle" && !!s.remote && (s.printings < 20000 || !!s.update_available);
  useEffect(() => {
    if (!wanted || auto.current) return;
    auto.current = true;
    api.syncCatalog().then(() => status.refetch()).catch(() => {});
  }, [wanted]); // eslint-disable-line
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
  if (LOCAL && s.update_available && s.printings >= 20000)
    return (
      <div className="banner">
        <span>New card data &amp; prices are available ({s.remote?.version}).</span>
        <button className="primary" onClick={() => api.syncCatalog().then(() => status.refetch())}>Update</button>
      </div>
    );
  if (s.printings >= 20000 || job.state === "done") return null;
  const mb = s.remote?.size_gz ? ` (~${Math.max(1, Math.round(s.remote.size_gz / 1e6))} MB)` : LOCAL ? "" : " (Scryfall, ~500 MB)";
  return (
    <div className="banner">
      <span><b>One-time setup:</b> download the full card database{mb} so search, imports and the scanner work
        offline{LOCAL ? " on this device" : " and prices are instant"}. Until then, cards are looked up online as you go.</span>
      <button className="primary" onClick={() => api.syncCatalog().then(() => status.refetch())}>Download now</button>
    </div>
  );
}

export default function App() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 30000 });
  const [importing, setImporting] = useState(false);
  const [adding, setAdding] = useState(false);
  const [menu, setMenu] = useState(false);
  const [phone, setPhone] = useState(false);
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
          <NavLink to="/lab"><span className="hide-xs">Scanner&nbsp;</span>Lab</NavLink>
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
                {!LOCAL && <button onClick={() => { setMenu(false); setPhone(true); }}>📱 Open on your phone</button>}
                <button onClick={async () => { setMenu(false); toast("Refreshing prices…", "info"); try { await fetch("/api/prices/refresh", { method: "POST" }); qc.invalidateQueries(); toast("Prices updated"); } catch { toast("Price refresh failed", "err"); } }}>Refresh prices</button>
                <button onClick={async () => { setMenu(false); await api.syncCatalog(); qc.invalidateQueries({ queryKey: ["catalog-status"] }); }}>Re-download card database</button>
                <a href="/api/export/tcgplayer" target="_blank">Export collection (TCGplayer CSV)</a>
                {!LOCAL && <a href="/docs" target="_blank">API docs</a>}
                {!LOCAL && <a href="/classic">Classic UI</a>}
              </div>
            )}
          </div>
        </div>
      </header>
      <UpdateBanner />
      <CatalogBanner />
      <Routes>
        <Route path="/" element={<Collection />} />
        <Route path="/decks" element={<Decks />} />
        <Route path="/decks/:slug" element={<DeckDetail />} />
        <Route path="/scan" element={<Scan />} />
        <Route path="/lab" element={<Lab />} />
        <Route path="/lab/live" element={<LabLive />} />
        <Route path="*" element={<Collection />} />
      </Routes>
      {/* phones: the main sections sit under the thumb, like a store app; hidden on wider screens (styles.css) */}
      <nav className="tabbar" aria-label="Sections">
        <NavLink to="/" end><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="3" width="12" height="16" rx="2" /><path d="M8 21h10a2 2 0 0 0 2-2V8" /></svg><span>Collection</span></NavLink>
        <NavLink to="/decks"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3 3 8l9 5 9-5-9-5Z" /><path d="m3 13 9 5 9-5" /></svg><span>Decks</span></NavLink>
        <NavLink to="/scan" className={({ isActive }) => `scan-tab ${isActive ? "active" : ""}`}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" /><circle cx="12" cy="12" r="3.2" /></svg><span>Scan</span></NavLink>
        <NavLink to="/lab"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3" /><path d="M7.5 15h9" /></svg><span>Lab</span></NavLink>
      </nav>
      {importing && <ImportDialog onClose={() => setImporting(false)} />}
      {adding && <AddCard onClose={() => setAdding(false)} />}
      {phone && <PhoneDialog onClose={() => setPhone(false)} />}
    </div>
  );
}
