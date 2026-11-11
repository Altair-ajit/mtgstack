import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, lab } from "../api";
import CardPicker from "../components/CardPicker";
import type { Allocation, Finish } from "../api";
import { CardImg, COND_LABEL, FINISH_LABEL, Modal, money, useToast } from "../ui";

interface Candidate { printing_id: string; name: string; set_code: string; set_name: string; collector_number: string; finishes: Finish[]; image: string | null; score: number }
interface ScanResult {
  scan_id?: string; debug?: Record<string, string>; markers?: { title_ocr?: string[]; type_ocr?: string[]; collector_ocr?: string[] };
  printing_id: string | null; name: string | null; set_code: string | null; collector_number: string | null; language: string;
  finish: Finish; finish_confidence: number; confidence: number; candidates: Candidate[]; evidence: string[]; needs_review: boolean;
}
interface LogItem { id: number; name: string; set: string; finish: Finish; lot: number; allocations: Allocation[]; kept?: boolean }
type Track = { found: boolean; corners?: [number, number][]; hint?: string };

export default function Scan() {
  const video = useRef<HTMLVideoElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [trackOn, setTrackOn] = useState(true);
  const [auto, setAuto] = useState(false);
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ScanResult | null>(null);
  const [finish, setFinish] = useState<Finish>("nonfoil");
  const [opts, setOpts] = useState({ condition: "NM", tags: localStorage.getItem("mtg.scan.tags") || "", autoAdd: false });
  const [log, setLog] = useState<LogItem[]>([]);
  const [orig, setOrig] = useState<{ printing_id: string | null; finish: Finish } | null>(null);
  const [labelled, setLabelled] = useState<string | null>(null);
  const [fixing, setFixing] = useState(false);
  const [fixPick, setFixPick] = useState<{ printing_id: string; name: string; set_code: string; number: string; finish: Finish } | null>(null);
  const decks = useQuery({ queryKey: ["decks"], queryFn: api.decks });
  const toast = useToast();
  const qc = useQueryClient();
  const st = useRef({ history: [] as ([number, number][] | null)[], armed: true, busy: false, last: null as [number, number][] | null });

  useEffect(() => () => stream?.getTracks().forEach(t => t.stop()), [stream]);

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

  const grab = (maxSide = 0): Promise<Blob> => new Promise(ok => {
    const v = video.current!, c = document.createElement("canvas");
    const k = maxSide ? Math.min(1, maxSide / Math.max(v.videoWidth, v.videoHeight)) : 1;
    c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
    c.getContext("2d")!.drawImage(v, 0, 0, c.width, c.height);
    c.toBlob(b => ok(b!), "image/jpeg", maxSide ? 0.8 : 0.92);
  });

  // live outline + auto-capture
  useEffect(() => {
    if (!stream) return;
    let alive = true;
    const loop = async () => {
      while (alive) {
        const v = video.current;
        if (trackOn && v?.videoWidth && !st.current.busy) {
          st.current.busy = true;
          try {
            const r: Track = await api.detect(await grab(640));
            draw(r);
            await maybeAuto(r);
          } catch { /* keep going */ }
          st.current.busy = false;
        }
        await new Promise(r => setTimeout(r, 120));
      }
    };
    loop();
    return () => { alive = false; };
  }, [stream, trackOn, auto]); // eslint-disable-line

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
    setHint(ready ? (auto ? "Hold steady…" : "Ready — tap Capture") : r.hint || "");
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
  const maybeAuto = async (r: Track) => {
    const s = st.current;
    s.history.push(r.found && r.hint === "ready" ? r.corners! : null);
    if (s.history.length > 8) s.history.shift();
    if (!r.found) s.armed = true;
    else if (s.last && r.corners && Math.max(...r.corners.map((p, k) => Math.hypot(p[0] - s.last![k][0], p[1] - s.last![k][1]))) > 0.12) s.armed = true;
    if (!auto || !s.armed || steady() < 3) return;
    s.armed = false; s.last = r.corners || null;
    await send([await grab()]);
    s.history = [];
  };

  const send = async (frames: Blob[]) => {
    setBusy(true);
    try {
      const r: ScanResult = await api.scan(frames);
      setRes(r); setFinish(r.finish); setOrig({ printing_id: r.printing_id, finish: r.finish }); setLabelled(null);
      if (opts.autoAdd && r.printing_id && !r.needs_review) await add(r, r.finish);
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
    localStorage.setItem("mtg.scan.tags", opts.tags);
    const out = await api.addLot({ printing_id: r.printing_id, qty: 1, finish: fin, condition: opts.condition, language: r.language,
      tags: opts.tags.split(/[ ,]+/).filter(Boolean) });
    setLog(l => [{ id: Date.now(), name: r.name || out.name, set: r.set_code || "", finish: fin, lot: out.allocations[0]?.deck ? -1 : out.lot_id,
      allocations: out.allocations }, ...l]);
    toast(out.allocations.length ? `${r.name} → ${out.allocations[0].deck_name} (${out.allocations[0].deck_progress})` : `Added ${r.name}`);
    qc.invalidateQueries();
  };

  return (
    <div className="page">
      <main className="content wide scan">
        <div className="page-head"><div><h1>Scan cards</h1><div className="muted">Any angle works. Set code, number, language and foil come from the card's printed markers.</div></div></div>
        <div className="scan-grid">
          <div>
            <div className={`cam ${stream ? "" : "off"}`}>
              <video ref={video} playsInline autoPlay muted />
              <canvas ref={overlay} />
              {stream && hint && <div className="cam-hint">{hint}</div>}
              {!stream && <div className="cam-off"><button className="primary" onClick={start}>Start camera</button><div className="muted small">or upload a photo below</div></div>}
            </div>
            <div className="actions">
              <button disabled={!stream || busy} onClick={async () => send([await grab()])}>📷 Capture</button>
              <button disabled={!stream || busy} onClick={burst} title="Captures 6 frames while you tilt the card: detects foil shimmer on older cards">Tilt burst (foil check)</button>
              <label className="btn">Upload photo<input type="file" accept="image/*" capture="environment" multiple hidden onChange={e => e.target.files && send([...e.target.files])} /></label>
            </div>
            <div className="actions">
              <label className="check"><input type="checkbox" checked={trackOn} onChange={e => setTrackOn(e.target.checked)} /> Outline the card</label>
              <label className="check"><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> Auto-capture when steady</label>
              <label className="check"><input type="checkbox" checked={opts.autoAdd} onChange={e => setOpts({ ...opts, autoAdd: e.target.checked })} /> Auto-add confident scans</label>
            </div>
            <div className="actions">
              <label>Condition <select value={opts.condition} onChange={e => setOpts({ ...opts, condition: e.target.value })}>
                {Object.entries(COND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
              <label>Location tag <input value={opts.tags} placeholder="e.g. box:bulk-1" onChange={e => setOpts({ ...opts, tags: e.target.value })} /></label>
            </div>
          </div>

          <div>
            <div className="scan-result">
              {busy && <p className="muted">Reading markers…</p>}
              {!busy && !res && <p className="muted">Scan a card to see what was read.</p>}
              {!busy && res && (res.printing_id ? (
                <div className="sr">
                  <CardImg src={res.candidates.find(c => c.printing_id === res.printing_id)?.image} name={res.name || ""} size="md" />
                  <div>
                    <h2>{res.name}</h2>
                    <div><span className="setcode">{res.set_code?.toUpperCase()}</span> #{res.collector_number} · {res.language}
                      <span className={`conf ${res.confidence >= 0.9 ? "hi" : res.confidence >= 0.6 ? "mid" : "lo"}`}>{Math.round(res.confidence * 100)}% sure</span></div>
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
                          <button key={c.printing_id} className={`cand ${c.printing_id === res.printing_id ? "on" : ""}`}
                            onClick={() => setRes({ ...res, printing_id: c.printing_id, set_code: c.set_code, collector_number: c.collector_number, name: c.name })}>
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
            <h3>This session <span className="muted">({log.length})</span></h3>
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
            {(decks.data || []).some(d => d.status === "building") && <p className="hint">In-progress decks: {(decks.data || []).filter(d => d.status === "building").map(d => `${d.name} ${d.progress}`).join(" · ")} · total {money((decks.data || []).filter(d => d.status === "building").reduce((n, d) => n + d.missing_cost, 0))} left to buy</p>}
          </div>
        </div>
      </main>
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
