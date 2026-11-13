import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, lab } from "../api";
import CardPicker from "../components/CardPicker";
import { BatchRow, itemFor, itemPrice, sameCard, sameStack } from "../components/ScanBatch";
import type { BatchItem } from "../components/ScanBatch";
import TagBar from "../components/TagBar";
import type { Allocation, Finish, ImportRow } from "../api";
import { CardImg, COND_LABEL, FINISH_LABEL, Modal, money, useToast } from "../ui";

interface Candidate { printing_id: string; name: string; set_code: string; set_name: string; collector_number: string; finishes: Finish[]; image: string | null; score: number }
interface ScanResult {
  scan_id?: string; debug?: Record<string, string>; markers?: { title_ocr?: string[]; type_ocr?: string[]; collector_ocr?: string[] };
  printing_id: string | null; name: string | null; set_code: string | null; collector_number: string | null; language: string;
  finish: Finish; finish_confidence: number; confidence: number; candidates: Candidate[]; evidence: string[]; needs_review: boolean;
  prices?: Partial<Record<Finish, number | null>>;
}
interface LogItem { id: number; name: string; set: string; finish: Finish; lot: number; allocations: Allocation[]; kept?: boolean }
type Track = { found: boolean; corners?: [number, number][]; hint?: string; sig?: number[][] };
type Mode = "single" | "batch";

const loadBatch = (): BatchItem[] => { try { return JSON.parse(localStorage.getItem("mtg.scan.batch") || "[]"); } catch { return []; } };

export default function Scan() {
  const video = useRef<HTMLVideoElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const [sp, setSp] = useSearchParams();
  // Batch: scans pile up in an editable list and go into the collection together. Single: look a card up.
  const mode: Mode = (sp.get("mode") || localStorage.getItem("mtg.scan.mode") || "batch") === "single" ? "single" : "batch";
  const setMode = (m: Mode) => { localStorage.setItem("mtg.scan.mode", m); setSp({ mode: m }, { replace: true }); setRes(null); };
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [trackOn, setTrackOn] = useState(true);
  const [auto, setAutoState] = useState(localStorage.getItem("mtg.scan.auto") !== "0");
  const setAuto = (on: boolean) => { localStorage.setItem("mtg.scan.auto", on ? "1" : "0"); setAutoState(on); };
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ScanResult | null>(null);
  const [finish, setFinish] = useState<Finish>("nonfoil");
  const [condition, setCondition] = useState("NM");
  const [tags, setTagsState] = useState<string[]>((localStorage.getItem("mtg.scan.tags") || "").split(/[ ,]+/).filter(Boolean));
  const setTags = (t: string[]) => { localStorage.setItem("mtg.scan.tags", t.join(" ")); setTagsState(t); };
  const [items, setItems] = useState<BatchItem[]>(loadBatch);
  const [fresh, setFresh] = useState(0);
  const [saving, setSaving] = useState(false);
  const [added, setAdded] = useState<{ cards: number; allocations: Allocation[] } | null>(null);
  const [byName, setByName] = useState(false);
  const [namePick, setNamePick] = useState<{ printing_id: string; finish: Finish } | null>(null);
  const [log, setLog] = useState<LogItem[]>([]);
  const [orig, setOrig] = useState<{ printing_id: string | null; finish: Finish } | null>(null);
  const [labelled, setLabelled] = useState<string | null>(null);
  const [fixing, setFixing] = useState(false);
  const [fixPick, setFixPick] = useState<{ printing_id: string; name: string; set_code: string; number: string; finish: Finish } | null>(null);
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const toast = useToast();
  const qc = useQueryClient();
  const st = useRef({ history: [] as ([number, number][] | null)[], armed: true, busy: false, away: 0,
    sig: null as number[][] | null });

  useEffect(() => () => stream?.getTracks().forEach(t => t.stop()), [stream]);
  useEffect(() => { localStorage.setItem("mtg.scan.batch", JSON.stringify(items)); }, [items]);

  const start = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      toast("The live camera needs HTTPS on a phone — run ./run.sh --phone, or use Upload photo.", "err"); return;
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1440 } } });
      setStream(s);
      if (video.current) video.current.srcObject = s;
    } catch (e) { toast(String((e as Error).message), "err"); }
  };

  // One canvas per size, reused: the tracker grabs a frame several times a second, and phones (iOS above all) cap
  // the memory of all live canvases. Past the cap new ones silently draw blank, i.e. "no card" for ever.
  const canvases = useRef<Record<string, HTMLCanvasElement>>({});
  const grab = (maxSide = 0): Promise<Blob> => new Promise(ok => {
    const v = video.current!, key = maxSide ? "small" : "full";
    const c = canvases.current[key] || (canvases.current[key] = document.createElement("canvas"));
    const k = maxSide ? Math.min(1, maxSide / Math.max(v.videoWidth, v.videoHeight)) : 1;
    const w = Math.round(v.videoWidth * k), h = Math.round(v.videoHeight * k);
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    c.getContext("2d")!.drawImage(v, 0, 0, c.width, c.height);
    c.toBlob(b => ok(b!), "image/jpeg", maxSide ? 0.8 : 0.92);
  });

  const draw = (r: Track) => {
    const cv = overlay.current, v = video.current;
    if (!cv || !v) return;
    const W = v.clientWidth, H = v.clientHeight, k = Math.min(W / v.videoWidth, H / v.videoHeight);
    const ox = (W - v.videoWidth * k) / 2, oy = (H - v.videoHeight * k) / 2;
    cv.width = W * devicePixelRatio; cv.height = H * devicePixelRatio;
    const g = cv.getContext("2d")!;
    g.scale(devicePixelRatio, devicePixelRatio); g.clearRect(0, 0, W, H);
    if (!r.found || !r.corners) { setHint("Looking for a card…"); return; }
    const ready = r.hint === "ready";
    g.lineWidth = 3; g.strokeStyle = ready ? "#16a34a" : "#f59e0b"; g.fillStyle = ready ? "rgba(22,163,74,.12)" : "rgba(245,158,11,.1)";
    g.beginPath();
    r.corners.forEach(([x, y], i) => (i ? g.lineTo : g.moveTo).call(g, ox + x * v.videoWidth * k, oy + y * v.videoHeight * k));
    g.closePath(); g.fill(); g.stroke();
    setHint(ready ? (auto ? (st.current.armed ? "Hold steady…" : "Scanned — next card") : "Ready — tap Capture") : r.hint || "");
  };

  const steady = () => {
    const h = st.current.history; let n = 0;
    for (let i = h.length - 1; i > 0; i--) {
      const a = h[i], b = h[i - 1];
      if (!a || !b) break;
      const d = Math.max(...a.map((p, j) => Math.min(...[0, 1, 2, 3].map(s => Math.hypot(p[0] - b[(j + s) % 4][0], p[1] - b[(j + s) % 4][1])))));
      if (d > 0.025) break;
      n++;
    }
    return n;
  };
  /** How alike two tracker signatures are (-1..1): the same card scores about 0.8, another card about 0.2.
   * See detect.signature: the picture box of the straightened card in colour, one version per way up. */
  const alike = (a: number[][], b: number[][]) => Math.max(...b.map(y => {
    let xy = 0, xx = 0, yy = 0;
    for (let i = 0; i < y.length; i++) { xy += a[0][i] * y[i]; xx += a[0][i] * a[0][i]; yy += y[i] * y[i]; }
    const den = Math.sqrt(xx * yy);
    return den > 1e3 ? xy / den : 1;               // a featureless picture cannot tell: assume the same card
  }));
  const maybeAuto = async (r: Track) => {
    const s = st.current;
    s.history.push(r.found && r.hint === "ready" ? r.corners! : null);
    if (s.history.length > 8) s.history.shift();
    // Scan each card once. After a capture the next one waits until the card is gone: no card in view, or the
    // card in view looks different from the one captured (another card, or a hand over it, which is how an
    // identical copy gets counted). The comparison follows the card, so a hand-held phone drifting, or the card
    // sliding, is not a new card; and it must hold for two frames, so one blurred frame is not either.
    if (!s.armed) {
      const differs = !!(r.found && r.sig && s.sig) && alike(s.sig!, r.sig!) < 0.6;
      s.away = !r.found || differs ? s.away + 1 : 0;
      // whatever re-armed it (often a hand on the card) may still be in the picture: wait for a fresh steady view
      if (s.away >= 2) { s.armed = true; s.away = 0; s.history = []; }
    }
    // two ready (sharp, uncut) frames in a row that agree on where the card is: it has stopped moving
    if (!auto || !s.armed || !r.corners || steady() < 1) return;
    s.armed = false; s.away = 0; s.sig = r.sig || null;
    await send([await grab()], r.corners);
    s.history = [];
  };

  // live outline + auto-capture. The loop outlives renders, so it calls the latest handlers through a ref.
  const handlers = useRef({ draw, maybeAuto });
  handlers.current = { draw, maybeAuto };
  useEffect(() => {
    if (!stream) return;
    let alive = true;
    const loop = async () => {
      while (alive) {
        const v = video.current, cam = stream.getVideoTracks()[0];
        // A camera the phone took away (call, app switch, another tab) leaves a frozen picture: say so, don't
        // keep "looking" at it.
        if (!cam || cam.readyState === "ended") { setHint("The camera stopped"); setStream(null); return; }
        if (v && v.paused) v.play().catch(() => {});
        if (cam.muted) setHint("The camera is paused by the device. Bring this page to the front.");
        else if (trackOn && v?.videoWidth && !st.current.busy) {
          st.current.busy = true;
          try {
            const r: Track = await api.detect(await grab(640));
            handlers.current.draw(r);
            await handlers.current.maybeAuto(r);
          } catch (e) { setHint(`The scanner isn't responding: ${(e as Error).message}`); }
          st.current.busy = false;
        }
        await new Promise(r => setTimeout(r, 30));
      }
    };
    loop();
    return () => { alive = false; };
  }, [stream, trackOn]);

  /** Put a card on the batch list: another copy of a row already there raises its count. */
  const stage = (it: BatchItem) => {
    setAdded(null);
    setItems(list => {
      // Copies of one card rarely scan identically: when the collector line is unreadable the printing or the
      // finish is a guess that changes from copy to copy. So a scan also joins a row of the same card when
      // either of the two was unsure, and the row keeps the surer details (a corrected row counts as sure).
      const same = list.find(x => sameStack(x, it)) || list.find(x => sameCard(x, it) && (x.review || it.review));
      if (!same) { setFresh(it.id); return [it, ...list]; }
      const base = same.review && !it.review ? { ...it, id: same.id, scan_id: same.scan_id, orig: same.orig } : same;
      const merged = { ...base, qty: same.qty + it.qty };
      const rest = list.filter(x => x !== same);
      const dup = rest.find(x => sameStack(x, merged));           // the surer details may match another row
      setFresh(merged.id);
      return dup ? [{ ...merged, qty: merged.qty + dup.qty }, ...rest.filter(x => x !== dup)] : [merged, ...rest];
    });
  };
  const fromScan = (r: ScanResult): BatchItem => {
    const c = r.candidates.find(x => x.printing_id === r.printing_id);
    return {
      id: Date.now(), printing_id: r.printing_id!, name: r.name || "", set_code: r.set_code || "", set_name: c?.set_name || "",
      number: r.collector_number || "", image: c?.image ?? null, finish: r.finish, finishes: c?.finishes?.length ? c.finishes : [r.finish],
      condition, language: r.language, qty: 1, tags: [...tags], prices: r.prices || {}, review: r.needs_review,
      scan_id: r.scan_id, orig: { printing_id: r.printing_id!, finish: r.finish },
    };
  };

  const send = async (frames: Blob[], corners?: [number, number][]) => {
    setBusy(true);
    try {
      const r: ScanResult = await api.scan(frames, "scan", corners);
      if (corners && r.printing_id) navigator.vibrate?.(40);
      setRes(r); setFinish(r.finish); setOrig({ printing_id: r.printing_id, finish: r.finish }); setLabelled(null);
      if (mode === "batch" && r.printing_id) stage(fromScan(r));
    } catch (e) { toast(String((e as Error).message), "err"); }
    setBusy(false);
  };
  const burst = async () => {
    toast("Tilt the card slowly…", "info");
    const frames: Blob[] = [];
    for (let i = 0; i < 6; i++) { frames.push(await grab()); await new Promise(r => setTimeout(r, 300)); }
    await send(frames);
  };

  /** Teach the Scanner Lab: right if the user kept the guess, otherwise a correction. */
  const teach = async (r: ScanResult, fin: Finish, verdict?: "correct" | "wrong") => {
    if (!r.scan_id || labelled === r.scan_id || !r.printing_id) return;
    const changed = !orig || orig.printing_id !== r.printing_id || orig.finish !== fin;
    const v = verdict || (changed ? "wrong" : "correct");
    try {
      await lab.label(r.scan_id, v === "wrong" ? { verdict: "wrong", printing_id: r.printing_id, finish: fin } : { verdict: "correct", finish: fin });
      setLabelled(r.scan_id);
    } catch { /* labelling is best-effort */ }
  };

  const add = async (r: ScanResult, fin: Finish) => {
    if (!r.printing_id) return;
    teach(r, fin);
    const out = await api.addLot({ printing_id: r.printing_id, qty: 1, finish: fin, condition, language: r.language, tags });
    setLog(l => [{ id: Date.now(), name: r.name || out.name, set: r.set_code || "", finish: fin, lot: out.allocations[0]?.deck ? -1 : out.lot_id,
      allocations: out.allocations }, ...l]);
    toast(out.allocations.length ? `${r.name} → ${out.allocations[0].deck_name} (${out.allocations[0].deck_progress})` : `Added ${r.name}`);
    qc.invalidateQueries();
  };

  /** Single mode: another printing of the scanned card was picked, so its prices are needed too. */
  const pickCandidate = async (r: ScanResult, c: Candidate) => {
    setRes({ ...r, printing_id: c.printing_id, set_code: c.set_code, collector_number: c.collector_number, name: c.name, prices: {} });
    try {
      const p = (await api.card(c.printing_id)).printings.find(x => x.printing_id === c.printing_id);
      if (p) setRes(cur => (cur && cur.printing_id === c.printing_id ? { ...cur, prices: { nonfoil: p.price, foil: p.price_foil } } : cur));
    } catch { /* price stays unknown */ }
  };

  const count = items.reduce((n, i) => n + i.qty, 0);
  const value = items.reduce((n, i) => n + (itemPrice(i) || 0) * i.qty, 0);
  const patch = (id: number, p: Partial<BatchItem>) => setItems(list => {
    const next = list.map(i => (i.id === id ? { ...i, ...p, id } : i));
    const me = next.find(i => i.id === id)!;
    const twin = next.find(i => i.id !== id && sameStack(i, me));      // an edit made two rows identical: one row
    return twin ? next.filter(i => i !== twin).map(i => (i === me ? { ...me, qty: me.qty + twin.qty } : i)) : next;
  });
  const commit = async () => {
    setSaving(true);
    try {
      const rows = items.map(i => ({ printing_id: i.printing_id, qty: i.qty, finish: i.finish, condition: i.condition,
        language: i.language, tags: i.tags, include: true }));
      const out = await api.importCommit(rows as unknown as ImportRow[], { type: "collection", tags: [] });
      for (const i of items) {                     // what was kept or corrected teaches the Scanner Lab
        if (!i.scan_id || !i.orig) continue;
        const wrong = i.orig.printing_id !== i.printing_id || i.orig.finish !== i.finish;
        lab.label(i.scan_id, wrong ? { verdict: "wrong", printing_id: i.printing_id, finish: i.finish } : { verdict: "correct", finish: i.finish }).catch(() => {});
      }
      setItems([]); setRes(null); setAdded({ cards: out.cards, allocations: out.allocations || [] });
      toast(`Added ${out.cards} card${out.cards === 1 ? "" : "s"} to your collection`);
      qc.invalidateQueries();
    } catch (e) { toast(String((e as Error).message), "err"); }
    setSaving(false);
  };

  const price = res?.prices?.[finish];
  return (
    <div className="page">
      <main className="content wide scan">
        <div className="page-head">
          <div><h1>Scan cards</h1><div className="muted">{mode === "batch"
            ? "Scan a pile: cards collect in a list you can correct, then add them all at once."
            : "Check a card's printing and price. Add it to your collection only if you want to."}</div></div>
          <div className="seg" role="tablist">
            <button role="tab" aria-selected={mode === "batch"} className={mode === "batch" ? "on" : ""} onClick={() => setMode("batch")}>Scan a batch</button>
            <button role="tab" aria-selected={mode === "single"} className={mode === "single" ? "on" : ""} onClick={() => setMode("single")}>Price check</button>
          </div>
        </div>
        <div className="scan-grid">
          <div>
            <div className={`cam ${stream ? "" : "off"}`}>
              <video ref={video} playsInline autoPlay muted />
              <canvas ref={overlay} />
              {stream && mode === "batch" && items[0] && <div className="cam-last">
                <span>✓ <b>{items[0].name}</b> {items[0].set_code.toUpperCase()} · {money(itemPrice(items[0]))}</span>
                <span className="n">{count} cards · {money(value)}</span></div>}
              {stream && (busy || hint) && <div className={`cam-hint ${busy ? "busy" : ""}`}>{busy ? "Reading the card…" : hint}</div>}
              {!stream && <div className="cam-off"><button className="primary" onClick={start}>Start camera</button><div className="muted small">or upload a photo below</div></div>}
            </div>
            <div className="actions">
              <button disabled={!stream || busy} onClick={async () => send([await grab()])}>📷 Capture</button>
              <button disabled={!stream || busy} onClick={burst} title="Captures 6 frames while you tilt the card: detects foil shimmer on older cards">Tilt burst (foil check)</button>
              <label className="btn">Upload photo<input type="file" accept="image/*" capture="environment" multiple hidden onChange={e => e.target.files && send([...e.target.files])} /></label>
            </div>
            <div className="actions">
              <label className="check"><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> Auto-capture when steady</label>
              <label className="check"><input type="checkbox" checked={trackOn} onChange={e => setTrackOn(e.target.checked)} /> Outline the card</label>
              <label>Condition <select value={condition} onChange={e => setCondition(e.target.value)}>
                {Object.entries(COND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            </div>
            <TagBar active={tags} onChange={setTags} />
          </div>

          <div>
            {mode === "batch" && (
              <div className="batch">
                <div className="batch-head">
                  <div className="batch-total"><b>{count}</b> card{count === 1 ? "" : "s"} · {items.length} unique · <b className="price">{money(value)}</b></div>
                  <div className="actions">
                    <button onClick={() => setByName(true)}>+ Add by name</button>
                    <button className="link danger" disabled={!items.length} onClick={() => confirm(`Discard the ${count} scanned cards?`) && setItems([])}>Clear</button>
                    <button className="primary" disabled={!items.length || saving} onClick={commit}>{saving ? "Adding…" : `Add ${count || ""} to collection`}</button>
                  </div>
                </div>
                {res && !res.printing_id && !busy && (
                  <div className="batch-miss">
                    <span><b>Couldn't identify that card.</b> Try again with better light, or</span>
                    <button className="link" onClick={() => setByName(true)}>add it by name</button>
                    <button className="icon" onClick={() => setRes(null)} aria-label="Dismiss">×</button>
                  </div>
                )}
                {added && (
                  <div className="batch-done">✓ Added {added.cards} card{added.cards === 1 ? "" : "s"}.
                    {added.allocations.length > 0 && ` ${added.allocations.reduce((n, a) => n + a.qty, 0)} went to in-progress decks.`}
                    {" "}<Link to="/?sort=added">View in collection</Link></div>
                )}
                {items.length === 0 && !added && <p className="muted batch-empty">Scanned cards collect here. Nothing goes into your collection until you press Add.</p>}
                <ul className="batch-list">
                  {items.map(i => <BatchRow key={i.id} item={i} fresh={i.id === fresh} onChange={p => patch(i.id, p)}
                    onRemove={() => setItems(list => list.filter(x => x.id !== i.id))} />)}
                </ul>
              </div>
            )}

            {mode === "single" && <>
              <div className="scan-result">
                {busy && <p className="muted">Reading the card…</p>}
                {!busy && !res && <p className="muted">Scan a card to see its printing and price.</p>}
                {!busy && res && (res.printing_id ? (
                  <div className="sr">
                    <CardImg src={res.candidates.find(c => c.printing_id === res.printing_id)?.image} name={res.name || ""} size="md" />
                    <div>
                      <h2>{res.name}</h2>
                      <div><span className="setcode">{res.set_code?.toUpperCase()}</span> #{res.collector_number} · {res.language}
                        <span className={`conf ${res.confidence >= 0.9 ? "hi" : res.confidence >= 0.6 ? "mid" : "lo"}`}>{Math.round(res.confidence * 100)}% sure</span></div>
                      <div className="sr-price"><b className="price">{money(price)}</b> <span className="muted small">market, {FINISH_LABEL[finish]}</span></div>
                      <div className="actions">
                        <select value={finish} onChange={e => setFinish(e.target.value as Finish)}>
                          {["nonfoil", "foil", "etched"].map(f => <option key={f} value={f}>{FINISH_LABEL[f]}</option>)}
                        </select>
                        <span className={`conf ${res.finish_confidence >= 0.9 ? "hi" : res.finish_confidence >= 0.6 ? "mid" : "lo"}`}>finish {Math.round(res.finish_confidence * 100)}%</span>
                        <button className="primary" onClick={() => add(res, finish)}>Add to collection</button>
                      </div>
                      {res.scan_id && (
                        <div className="actions teach">
                          {labelled === res.scan_id ? <span className="muted small">✓ saved to Scanner Lab</span> : <>
                            <span className="muted small">Was it right?</span>
                            <button className="small" onClick={() => { teach(res, finish); toast("Thanks — recorded as right"); }}>✓ Right</button>
                            <button className="small danger" onClick={() => setFixing(true)}>✗ Wrong — fix it</button>
                          </>}
                        </div>
                      )}
                      {res.candidates.length > 1 && (
                        <div className="cands">
                          {res.candidates.slice(0, 10).map(c => (
                            <button key={c.printing_id} className={`cand ${c.printing_id === res.printing_id ? "on" : ""}`} onClick={() => pickCandidate(res, c)}>
                              <CardImg src={c.image} name={c.set_code} /><span>{c.set_code.toUpperCase()} #{c.collector_number}</span>
                            </button>
                          ))}
                        </div>
                      )}
                      <details><summary className="muted small">How it decided</summary>
                        {res.debug && <div className="readings compact">
                          {([["title", "Title", res.markers?.title_ocr], ["type_line", "Type line", res.markers?.type_ocr], ["collector", "Bottom-left", res.markers?.collector_ocr]] as const).map(([k, l, t]) =>
                            <div key={k} className="reading"><div className="muted small">{l}</div><img src={res.debug![k]} alt={l} />
                              <div className="ocr">{t?.length ? t.map((x, i) => <code key={i}>{x}</code>) : <span className="muted small">nothing legible</span>}</div></div>)}
                        </div>}
                        <ul className="evidence">{res.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul></details>
                    </div>
                  </div>
                ) : (
                  <div><h3>Couldn't identify this card</h3>
                    {res.scan_id && <button className="small" onClick={() => setFixing(true)}>Tell it what this card is</button>}<ul className="evidence">{res.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
                    <p className="muted">Try better light and fill more of the frame, or use Add cards.</p></div>
                ))}
              </div>
              {log.length > 0 && <>
                <h3>Added this session <span className="muted">({log.length})</span></h3>
                <ol className="scan-log">
                  {log.map(l => (
                    <li key={l.id}>
                      <b>{l.name}</b> <span className="setcode">{l.set.toUpperCase()}</span> {l.finish !== "nonfoil" && <span className="badge foil">{FINISH_LABEL[l.finish]}</span>}
                      {l.allocations.length > 0 && !l.kept ? <>
                        <span className="alloc"> → {l.allocations[0].deck_name} ({l.allocations[0].deck_progress})</span>
                        <button className="small link" onClick={async () => {
                          const lots = await api.cards(l.name, { set: [], rarity: [], color: [], type: [], finish: [l.finish], condition: [], language: [], tag: [], deck: l.allocations[0].deck, min_price: "", max_price: "" }, "added", 1, 5, false);
                          const lot = lots.rows[0]?.lots[0];
                          if (lot) { await api.send(lot.lot_id, { qty: 1, deck: null }); setLog(x => x.map(y => (y.id === l.id ? { ...y, kept: true } : y))); qc.invalidateQueries(); toast("Kept in collection"); }
                        }}>keep in collection instead</button>
                      </> : <span className="muted"> → collection</span>}
                    </li>
                  ))}
                </ol>
              </>}
            </>}
            {(decks.data || []).some(d => d.status === "building") && <p className="hint">In-progress decks claim new cards they need: {(decks.data || []).filter(d => d.status === "building").map(d => `${d.name} ${d.progress}`).join(" · ")} · total {money((decks.data || []).filter(d => d.status === "building").reduce((n, d) => n + d.missing_cost, 0))} left to buy</p>}
          </div>
        </div>
      </main>
      {byName && (
        <Modal title="Add a card by name" wide onClose={() => { setByName(false); setNamePick(null); }} footer={<>
          <button onClick={() => { setByName(false); setNamePick(null); }}>Cancel</button>
          <button className="primary" disabled={!namePick} onClick={async () => {
            if (!namePick) return;
            try { stage(await itemFor(namePick.printing_id, namePick.finish, { condition, tags: [...tags] })); setRes(null); }
            catch (e) { toast(String((e as Error).message), "err"); }
            setByName(false); setNamePick(null);
          }}>Add to batch</button>
        </>}>
          <CardPicker initial={res && !res.printing_id ? res.markers?.title_ocr?.[0] || "" : ""} onPick={setNamePick} />
        </Modal>
      )}
      {fixing && res && (
        <Modal title="What card is it really?" wide onClose={() => { setFixing(false); setFixPick(null); }} footer={<>
          <button onClick={() => { setFixing(false); setFixPick(null); }}>Cancel</button>
          <button className="primary" disabled={!fixPick} onClick={async () => {
            if (!fixPick || !res.scan_id) return;
            await lab.label(res.scan_id, { verdict: "wrong", printing_id: fixPick.printing_id, finish: fixPick.finish });
            setLabelled(res.scan_id);
            setRes({ ...res, printing_id: fixPick.printing_id, name: fixPick.name, set_code: fixPick.set_code, collector_number: fixPick.number, needs_review: false });
            setFinish(fixPick.finish); setFixing(false); setFixPick(null);
            toast("Correction saved to Scanner Lab — you can add the right card now");
          }}>Save correction</button>
        </>}>
          <CardPicker initial={res.markers?.title_ocr?.[0] || res.name || ""} onPick={setFixPick} />
        </Modal>
      )}
    </div>
  );
}
