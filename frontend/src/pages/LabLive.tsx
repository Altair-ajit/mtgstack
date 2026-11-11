import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, lab } from "../api";
import type { Finish } from "../api";
import CardPicker from "../components/CardPicker";
import PhoneDialog from "../components/PhoneDialog";
import { LOCAL } from "../local/boot";
import { useCardCamera } from "../components/useCardCamera";
import { CardImg, FINISH_LABEL, useToast } from "../ui";

interface Cand { printing_id: string; name: string; set_code: string; collector_number: string; finishes: Finish[]; image: string | null; score: number }
interface Result {
  scan_id: string; printing_id: string | null; name: string | null; set_code: string | null; collector_number: string | null;
  language: string; finish: Finish; finish_confidence: number; confidence: number; candidates: Cand[]; evidence: string[];
  debug?: Record<string, string>; markers?: { title_ocr?: string[]; type_ocr?: string[]; collector_ocr?: string[] };
}
type Verdict = "correct" | "wrong" | "unreadable" | "skipped";
interface Done { scan_id: string; verdict: Verdict; guessed: string; actual: string; image: string | null; finishFixed: boolean }
type Pick = { printing_id: string; name: string; set_code: string; number: string; finish: Finish; image?: string | null };

const pref = <T,>(k: string, d: T): T => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } };
const save = (k: string, v: unknown) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } };

export default function LabLive() {
  const [auto, setAuto] = useState(true);
  const [tilt, setTilt] = useState<boolean>(pref("mtg.live.tilt", false));
  const [addCards, setAddCards] = useState<boolean>(pref("mtg.live.add", false));
  const [tags, setTags] = useState<string>(pref("mtg.live.tags", ""));
  const [busy, setBusy] = useState("");
  const [res, setRes] = useState<Result | null>(null);
  const [finish, setFinish] = useState<Finish>("nonfoil");
  const [fixing, setFixing] = useState(false);
  const [fixPick, setFixPick] = useState<Pick | null>(null);
  const [done, setDone] = useState<Done[]>([]);
  const [phone, setPhone] = useState(false);
  const onPhone = typeof navigator !== "undefined" && /iPhone|iPad|Android/i.test(navigator.userAgent);
  const toast = useToast();
  const qc = useQueryClient();
  const answerRef = useRef<HTMLElement>(null);
  // on a phone the answer panel sits under the camera: bring it into view when a result arrives
  useEffect(() => { if (res && window.innerWidth <= 900) answerRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [res]);

  useEffect(() => { save("mtg.live.tilt", tilt); save("mtg.live.add", addCards); save("mtg.live.tags", tags); }, [tilt, addCards, tags]);

  const capture = async () => {
    if (busy) return;
    const frames: Blob[] = [];
    if (tilt) {
      setBusy("Tilt the card slowly…");
      for (let i = 0; i < 6; i++) { frames.push(await cam.grab()); await new Promise(r => setTimeout(r, 250)); }
    } else frames.push(await cam.grab());
    setBusy("Reading…");
    try {
      const r: Result = await api.scan(frames, "live");
      setRes(r); setFinish(r.finish); setFixing(false); setFixPick(null);
    } catch (e) { toast(String((e as Error).message), "err"); }
    setBusy("");
  };
  const cam = useCardCamera({ auto, paused: !!res || !!busy, onAuto: capture });

  const guessCand = res?.candidates.find(c => c.printing_id === res.printing_id) || null;
  const others = (res?.candidates || []).filter(c => c.printing_id !== res?.printing_id).slice(0, 6);

  /** Record the answer, optionally add the (corrected) card, then wait for the next card. */
  const answer = async (verdict: Verdict, pick?: Pick) => {
    if (!res) return;
    const finishFixed = verdict === "correct" && finish !== res.finish;
    const v: Verdict = finishFixed ? "wrong" : verdict;
    const truth = pick || (verdict === "correct" && res.printing_id
      ? { printing_id: res.printing_id, name: res.name!, set_code: res.set_code!, number: res.collector_number!, finish, image: guessCand?.image } : null);
    try {
      if (v !== "skipped") {
        await lab.label(res.scan_id, v === "wrong"
          ? { verdict: "wrong", printing_id: truth!.printing_id, finish: truth!.finish, notes: finishFixed ? "finish corrected in live session" : "" }
          : v === "correct" ? { verdict: "correct", finish } : { verdict: "unreadable" });
      }
      if (addCards && truth && v !== "unreadable" && v !== "skipped") {
        const out = await api.addLot({ printing_id: truth.printing_id, qty: 1, finish: truth.finish, condition: "NM", language: res.language,
          tags: tags.split(/[ ,]+/).filter(Boolean) });
        if (out.allocations.length) toast(`${truth.name} → ${out.allocations[0].deck_name} (${out.allocations[0].deck_progress})`);
        qc.invalidateQueries();
      }
    } catch (e) { toast(String((e as Error).message), "err"); return; }
    setDone(d => [{ scan_id: res.scan_id, verdict: v, guessed: res.name ? `${res.name} (${res.set_code?.toUpperCase()} #${res.collector_number})` : "no guess",
      actual: truth ? `${truth.name} (${truth.set_code.toUpperCase()} #${truth.number}${truth.finish !== "nonfoil" ? ", " + FINISH_LABEL[truth.finish] : ""})` : "",
      image: truth?.image ?? guessCand?.image ?? null, finishFixed }, ...d]);
    setRes(null); setFixing(false); setFixPick(null);
    if (!auto) cam.rearm();
  };
  const pickCand = (c: Cand) => answer("wrong", { printing_id: c.printing_id, name: c.name, set_code: c.set_code, number: c.collector_number,
    finish: c.finishes.includes(finish) ? finish : c.finishes[0], image: c.image });
  const rescan = () => { setRes(null); setFixing(false); cam.rearm(); };

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (fixing && e.key === "Escape") { (document.activeElement as HTMLElement)?.blur(); setFixing(false); setFixPick(null); return; }
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA") return;
      if (!res) { if (e.key === " " && cam.stream && !auto) { e.preventDefault(); capture(); } return; }
      if (fixing) return;
      const key = e.key.toLowerCase();
      if ((key === "y" || key === " " || key === "enter") && res.printing_id) { e.preventDefault(); answer("correct"); }
      else if (key === "n") { e.preventDefault(); setFixing(true); }
      else if (key === "u") answer("unreadable");
      else if (key === "s") answer("skipped");
      else if (key === "r") rescan();
      else if (key === "f" && guessCand) { const fs = guessCand.finishes; setFinish(fs[(fs.indexOf(finish) + 1) % fs.length]); }
      else if (/^[1-6]$/.test(key) && others[+key - 1]) pickCand(others[+key - 1]);
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });

  const judged = done.filter(d => d.verdict === "correct" || d.verdict === "wrong");
  const right = judged.filter(d => d.verdict === "correct").length;
  const finishes: Finish[] = guessCand?.finishes?.length ? guessCand.finishes : ["nonfoil", "foil"];

  return (
    <div className="page">
      <main className="content wide live">
        <div className="page-head">
          <div>
            <h1>Live training session</h1>
            <div className="muted">Show cards one at a time. Each one is captured when it's steady; tell it whether it got it right. Every answer goes into the Scanner Lab dataset.</div>
          </div>
          <div className="actions">
            <div className="tally"><b>{judged.length}</b> cards · <b className="up">{right}</b> right · <b className="down">{judged.length - right}</b> wrong
              {judged.length > 0 && <> · <b>{Math.round(100 * right / judged.length)}%</b></>}</div>
            {!onPhone && !LOCAL && <button onClick={() => setPhone(true)}>📱 Use your phone</button>}
            <Link className="btn" to="/lab">Done → Lab</Link>
          </div>
        </div>

        <div className="live-grid">
          <div>
            <div className={`cam ${cam.stream ? "" : "off"}`}>
              <video ref={cam.video} playsInline autoPlay muted
                onLoadedMetadata={e => { const v = e.currentTarget; v.parentElement!.style.aspectRatio = `${v.videoWidth} / ${v.videoHeight}`; }} />
              <canvas ref={cam.overlay} />
              {cam.stream && (busy || cam.hint) && <div className={`cam-hint ${busy ? "busy" : ""}`}>{busy || cam.hint}</div>}
              {!cam.stream && <div className="cam-off"><button className="primary big" onClick={cam.start}>Start camera</button>
                {cam.error && <div className="bad small">{cam.error}</div>}</div>}
            </div>
            <div className="actions wrap">
              <label className="check"><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> Auto-capture</label>
              {!auto && <button disabled={!cam.stream || !!busy || !!res} onClick={capture}>📷 Capture <kbd>Space</kbd></button>}
              <label className="check" title="Capture 6 frames while you tilt the card — trains foil detection on older cards">
                <input type="checkbox" checked={tilt} onChange={e => setTilt(e.target.checked)} /> Tilt burst (foil)</label>
              <label className="check"><input type="checkbox" checked={addCards} onChange={e => setAddCards(e.target.checked)} /> Add confirmed cards to collection</label>
              {addCards && <input className="tags-in" placeholder="tags, e.g. box:bulk-1" value={tags} onChange={e => setTags(e.target.value)} />}
              {cam.stream && <button className="link" onClick={cam.stop}>Stop camera</button>}
            </div>
          </div>

          <section className="live-answer" ref={answerRef}>
            {!res ? (
              <div className="live-idle">
                <h3>{busy ? busy : cam.stream ? "Show a card" : "Start the camera"}</h3>
                <ol className="muted">
                  <li>Lay a card flat under the camera (any angle). It captures when the outline turns green and holds.</li>
                  <li>Answer: <kbd>Y</kbd>/<kbd>Space</kbd> right · <kbd>N</kbd> wrong · <kbd>1</kbd>–<kbd>6</kbd> pick a suggestion · <kbd>F</kbd> change finish · <kbd>U</kbd> bad photo · <kbd>R</kbd> rescan · <kbd>S</kbd> skip</li>
                  <li>Swap in the next card. Cards it gets wrong are the most useful — include your problem cards.</li>
                </ol>
              </div>
            ) : (
              <>
                <div className="live-guess">
                  <div className="live-img">
                    {guessCand ? <CardImg src={guessCand.image} name={guessCand.name} /> : res.debug?.card ? <img src={res.debug.card} alt="" /> : null}
                  </div>
                  <div>
                    <div className="muted small">Scanner says</div>
                    <h2>{res.name || <span className="muted">I don't know</span>}</h2>
                    {res.name && <div><span className="setcode">{res.set_code?.toUpperCase()}</span> #{res.collector_number} · {res.language}</div>}
                    <div className="mt"><span className={`conf ${res.confidence >= 0.9 ? "hi" : res.confidence >= 0.6 ? "mid" : "lo"}`}>card {Math.round(res.confidence * 100)}%</span>
                      <span className={`conf ${res.finish_confidence >= 0.9 ? "hi" : res.finish_confidence >= 0.6 ? "mid" : "lo"}`}>finish {Math.round(res.finish_confidence * 100)}%</span></div>
                    {res.printing_id && (
                      <div className="seg mt" title="Fix the finish if it's wrong (F)">
                        {finishes.map(f => <button key={f} className={finish === f ? "on" : ""} onClick={() => setFinish(f)}>{FINISH_LABEL[f]}</button>)}
                      </div>
                    )}
                    {finish !== res.finish && <div className="small muted">Finish corrected from {FINISH_LABEL[res.finish]}</div>}
                  </div>
                </div>

                {!fixing ? (
                  <div className="live-btns">
                    <button className="primary big" disabled={!res.printing_id} onClick={() => answer("correct")}>✓ Right <kbd>Y</kbd></button>
                    <button className="danger big" onClick={() => setFixing(true)}>✗ Wrong <kbd>N</kbd></button>
                    <button onClick={() => answer("unreadable")} title="Blurry, cut off, glare — excluded from accuracy">Bad photo <kbd>U</kbd></button>
                    <button onClick={rescan}>Rescan <kbd>R</kbd></button>
                    <button className="link" onClick={() => answer("skipped")}>Skip <kbd>S</kbd></button>
                  </div>
                ) : (
                  <div className="panel fix">
                    <div className="muted small">Which card is it? <kbd>Esc</kbd> to cancel</div>
                    <CardPicker initial={res.markers?.title_ocr?.find(t => t.trim().length > 2) || res.name || ""} onPick={setFixPick} />
                    <div className="actions"><button className="primary" disabled={!fixPick} onClick={() => fixPick && answer("wrong", fixPick)}>Save correction</button>
                      <button className="link" onClick={() => { setFixing(false); setFixPick(null); }}>Cancel</button></div>
                  </div>
                )}

                {others.length > 0 && !fixing && (
                  <div className="live-others">
                    <div className="muted small">Wrong? Maybe it's one of these:</div>
                    <div className="cands">{others.map((c, i) => (
                      <button key={c.printing_id} className="cand" onClick={() => pickCand(c)}>
                        <CardImg src={c.image} name={c.name} /><span>{c.name}</span><span className="muted">{c.set_code.toUpperCase()} #{c.collector_number} <kbd>{i + 1}</kbd></span>
                      </button>))}</div>
                  </div>
                )}

                {res.debug && (
                  <details className="live-read"><summary className="muted small">What it read</summary>
                    <div className="readings compact">
                      {([["title", "Title", res.markers?.title_ocr], ["type_line", "Type line", res.markers?.type_ocr], ["collector", "Bottom-left", res.markers?.collector_ocr]] as const).map(([k, l, t]) =>
                        res.debug![k] ? <div key={k} className="reading"><div className="muted small">{l}</div><img src={res.debug![k]} alt={l} />
                          <div className="ocr">{t?.length ? t.map((x, i) => <code key={i}>{x || "∅"}</code>) : <span className="muted small">nothing legible</span>}</div></div> : null)}
                    </div>
                  </details>
                )}
              </>
            )}
          </section>
        </div>

        {done.length > 0 && (
          <>
            <h3>This session</h3>
            <div className="live-log">
              {done.map(d => (
                <Link key={d.scan_id} to={`/lab?scan=${d.scan_id}`} className={`live-done ${d.verdict}`} title={d.verdict === "wrong" ? `said ${d.guessed}` : d.guessed}>
                  <CardImg src={d.image} name={d.actual || d.guessed} />
                  <span className="mark">{d.verdict === "correct" ? "✓" : d.verdict === "wrong" ? "✗" : d.verdict === "unreadable" ? "?" : "–"}</span>
                  <div className="small">{d.actual || d.guessed}</div>
                  {d.verdict === "wrong" && <div className="small bad">{d.finishFixed ? "finish was wrong" : `said ${d.guessed}`}</div>}
                </Link>
              ))}
            </div>
          </>
        )}
      </main>
      {phone && <PhoneDialog onClose={() => setPhone(false)} />}
    </div>
  );
}
