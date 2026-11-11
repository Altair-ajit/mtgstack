import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lab } from "../api";
import type { Finish } from "../api";
import CardPicker from "../components/CardPicker";
import { CardImg, Empty, FINISH_LABEL, useToast } from "../ui";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v}%`);

export default function Lab() {
  const [filter, setFilter] = useState<"unreviewed" | "wrong" | "correct" | "unreadable" | "">("unreviewed");
  const [sel, setSel] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const stats = useQuery({ queryKey: ["lab-stats"], queryFn: lab.stats });
  const list = useQuery({ queryKey: ["lab-list", filter], queryFn: () => lab.list(filter) });
  const rerun = useQuery({ queryKey: ["lab-rerun"], queryFn: lab.rerunStatus,
    refetchInterval: q => (q.state.data?.state === "running" ? 800 : false) });

  const items = list.data?.items || [];
  useEffect(() => { if (!sel && items.length) setSel(items[0].id); }, [items, sel]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["lab-stats"] }); qc.invalidateQueries({ queryKey: ["lab-list"] }); };
  const next = () => {
    const i = items.findIndex(s => s.id === sel);
    const n = items.slice(i + 1).find(s => s.status === "unreviewed") || items.find((s, j) => j !== i && s.status === "unreviewed");
    setSel(n ? n.id : null);
  };

  const upload = async (files: FileList) => {
    setUploading(true);
    try {
      const r = await lab.upload([...files]);
      toast(`Scanned ${r.scans.length} photo(s) — review them below`);
      setFilter("unreviewed"); setSel(r.scans[0]?.scan_id || null); refresh();
    } catch (e) { toast(String((e as Error).message), "err"); }
    setUploading(false);
  };

  const s = stats.data, rr = rerun.data;
  return (
    <div className="page">
      <main className="content wide">
        <div className="page-head">
          <div>
            <h1>Scanner Lab</h1>
            <div className="muted">Every scan is saved with what the scanner read. Mark it right or wrong (and what it really was) — the labelled set measures every scanner change.</div>
          </div>
          <div className="actions">
            <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={e => e.target.files && upload(e.target.files)} />
            <button onClick={() => fileRef.current?.click()} disabled={uploading}>{uploading ? "Scanning…" : "Upload photos"}</button>
            <button disabled={rr?.state === "running" || !s?.reviewed} onClick={async () => { await lab.rerun(); rerun.refetch(); }}
              title="Re-scan every labelled photo with the current scanner and compare">
              {rr?.state === "running" ? `Re-running ${rr.done}/${rr.total}…` : "Re-run scanner on labelled scans"}</button>
            <a className="btn" href="/api/lab/export">Export dataset (.zip)</a>
          </div>
        </div>

        {s && (
          <div className="lab-stats">
            <div className="stat-card"><span>Reviewed</span><b>{s.reviewed}<small>/{s.total}</small></b><em>{s.unreviewed} to review</em></div>
            <div className="stat-card"><span>Right card</span><b>{pct(s.accuracy.card)}</b></div>
            <div className="stat-card"><span>Right printing</span><b>{pct(s.accuracy.printing)}</b></div>
            <div className="stat-card"><span>Right finish</span><b>{pct(s.accuracy.finish)}</b></div>
            <div className="stat-card"><span>Fully correct</span><b>{pct(s.fully_correct)}</b></div>
            <div className="stat-card"><span>Confidence right / wrong</span><b className="sm">{s.avg_confidence_when_right ?? "—"} / {s.avg_confidence_when_wrong ?? "—"}</b><em>should be far apart</em></div>
          </div>
        )}
        {rr?.state === "done" && rr.report && (
          <div className="panel rerun">
            <b>Re-run with scanner {rr.report.scanner_version} on {rr.report.n} labelled scans:</b>{" "}
            {(["card", "printing", "finish"] as const).map(f => (
              <span key={f} className="rr">{f} {rr.report!.then[f]}% → <b className={rr.report!.now[f] > rr.report!.then[f] ? "up" : rr.report!.now[f] < rr.report!.then[f] ? "down" : ""}>{rr.report!.now[f]}%</b></span>
            ))}
            <span className="muted"> · fixed {rr.report.fixed.length} · broke {rr.report.broken.length} · still wrong {rr.report.still_wrong.length}</span>
          </div>
        )}
        {rr?.state === "error" && <div className="error-box">Re-run failed: {rr.error}</div>}
        {s && s.confusions.length > 0 && (
          <details className="panel"><summary><b>Most common mistakes</b></summary>
            <table className="mini"><thead><tr><th>Scanner said</th><th>Actually</th><th className="num">Times</th></tr></thead>
              <tbody>{s.confusions.map((c, i) => <tr key={i}><td>{c.guessed}</td><td>{c.actual}</td><td className="num">{c.count}</td></tr>)}</tbody></table>
          </details>
        )}

        <div className="lab-grid">
          <aside className="lab-list">
            <div className="tabs">
              {([["unreviewed", "To review"], ["wrong", "Wrong"], ["correct", "Right"], ["unreadable", "Bad photo"], ["", "All"]] as const).map(([k, l]) => (
                <button key={k} className={filter === k ? "on" : ""} onClick={() => { setFilter(k); setSel(null); }}>{l}</button>
              ))}
            </div>
            {!items.length && <p className="muted pad">{filter === "unreviewed" ? "Nothing to review. Scan cards on the Scan page, or upload photos." : "Nothing here."}</p>}
            {items.map(it => (
              <button key={it.id} className={`lab-item ${sel === it.id ? "on" : ""}`} onClick={() => setSel(it.id)}>
                <img src={lab.file(it.id, "card.jpg")} alt="" onError={e => (e.currentTarget.style.visibility = "hidden")} />
                <div>
                  <div className="cname">{it.guess.name || <i className="muted">no guess</i>}</div>
                  <div className="muted small">{it.guess.set_code?.toUpperCase()} {it.guess.number && `#${it.guess.number}`} · {Math.round((it.guess.confidence || 0) * 100)}%</div>
                  {it.truth && it.status === "wrong" && <div className="small bad">really: {it.truth.name}</div>}
                </div>
                <span className={`dot ${it.status}`} />
              </button>
            ))}
          </aside>
          <section className="lab-detail">
            {sel ? <Review key={sel} id={sel} onDone={() => { refresh(); next(); }} onDeleted={() => { refresh(); setSel(null); }} />
                 : <Empty title="Pick a scan to review" />}
          </section>
        </div>
      </main>
    </div>
  );
}

function Review({ id, onDone, onDeleted }: { id: string; onDone: () => void; onDeleted: () => void }) {
  const { data } = useQuery({ queryKey: ["lab-scan", id], queryFn: () => lab.get(id) });
  const [fixing, setFixing] = useState(false);
  const [pick, setPick] = useState<{ printing_id: string; finish: Finish } | null>(null);
  const [notes, setNotes] = useState("");
  const toast = useToast();

  const save = async (verdict: "correct" | "wrong" | "unreadable") => {
    try {
      if (verdict === "wrong" && !pick) { setFixing(true); return; }
      await lab.label(id, verdict === "wrong" ? { verdict, printing_id: pick!.printing_id, finish: pick!.finish, notes } : { verdict, notes });
      toast(verdict === "correct" ? "Marked right" : verdict === "unreadable" ? "Marked unreadable" : "Correction saved");
      onDone();
    } catch (e) { toast(String((e as Error).message), "err"); }
  };
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.key === "y") save("correct");
      if (e.key === "n") setFixing(true);
      if (e.key === "u") save("unreadable");
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }); // eslint-disable-line

  if (!data) return <p className="muted pad">Loading…</p>;
  const r = data.result, m = r.markers || {};
  const has = (f: string) => data.files.includes(f);
  return (
    <div className="review">
      <div className="review-top">
        <div className="shots">
          {has("photo_0.jpg") && <figure><img src={lab.file(id, "photo_0.jpg")} alt="photo" /><figcaption>Your photo</figcaption></figure>}
          {has("card.jpg") && <figure><img src={lab.file(id, "card.jpg")} alt="card" /><figcaption>Card as found &amp; straightened</figcaption></figure>}
        </div>
        <div className="guess">
          <div className="muted small">Scanner guessed</div>
          <h2>{r.name || <span className="muted">nothing</span>}</h2>
          {r.name && <div><span className="setcode">{r.set_code?.toUpperCase()}</span> #{r.collector_number} · {FINISH_LABEL[r.finish]} · {r.language}</div>}
          <div className="mt"><span className={`conf ${r.confidence >= 0.9 ? "hi" : r.confidence >= 0.6 ? "mid" : "lo"}`}>card {Math.round(r.confidence * 100)}%</span>
            <span className={`conf ${r.finish_confidence >= 0.9 ? "hi" : r.finish_confidence >= 0.6 ? "mid" : "lo"}`}>finish {Math.round(r.finish_confidence * 100)}%</span></div>
          {data.summary.status !== "unreviewed" && (
            <div className={`verdict ${data.summary.status}`}>Labelled {data.summary.status}{data.summary.truth && data.summary.status === "wrong" ? `: really ${data.summary.truth.name} (${data.summary.truth.set_code.toUpperCase()} #${data.summary.truth.number}, ${FINISH_LABEL[data.summary.truth.finish]})` : ""}</div>
          )}
          <div className="actions">
            <button className="primary" disabled={!r.printing_id} onClick={() => save("correct")}>✓ Right <kbd>Y</kbd></button>
            <button className="danger" onClick={() => setFixing(true)}>✗ Wrong <kbd>N</kbd></button>
            <button onClick={() => save("unreadable")} title="The photo itself is unusable (blurry, cut off): excluded from accuracy">Bad photo <kbd>U</kbd></button>
            <button className="link danger" onClick={async () => { await lab.remove(id); onDeleted(); }}>Delete</button>
          </div>
          <input className="notes" placeholder="Notes (optional): e.g. glare on title, showcase frame, foil…" value={notes} onChange={e => setNotes(e.target.value)} />
        </div>
      </div>

      {fixing && (
        <div className="panel fix">
          <CardPicker initial={r.name || m.title_ocr?.[0] || ""} onPick={p => setPick(p)} />
          <div className="actions"><button className="primary" disabled={!pick} onClick={() => save("wrong")}>Save correction</button>
            <button className="link" onClick={() => { setFixing(false); setPick(null); }}>Cancel</button></div>
        </div>
      )}

      <h3>What it read</h3>
      <div className="readings">
        <Reading img={has("title.jpg") ? lab.file(id, "title.jpg") : null} label="Title" texts={m.title_ocr}
          extra={m.name_matches?.length ? `closest names: ${m.name_matches.slice(0, 3).map(([n, s]) => `${n} ${Math.round(s)}%`).join(", ")}` : undefined} />
        <Reading img={has("type_line.jpg") ? lab.file(id, "type_line.jpg") : null} label="Type line" texts={m.type_ocr} />
        <Reading img={has("collector.jpg") ? lab.file(id, "collector.jpg") : null} label="Bottom-left (set · number · language · foil)" texts={m.collector_ocr}
          extra={m.collector_hits?.length ? `matches a real printing: ${m.collector_hits.map(h => `${h.set.toUpperCase()} #${h.number} = ${h.name}`).join(", ")}` : "no real printing matched"} />
      </div>
      {r.candidates?.length > 1 && (
        <>
          <h3>Other candidates</h3>
          <div className="cands">{r.candidates.slice(0, 10).map(c => (
            <div key={c.printing_id} className="cand"><CardImg src={c.image} name={c.name} /><span>{c.name}</span><span className="muted">{c.set_code.toUpperCase()} #{c.collector_number}</span></div>
          ))}</div>
        </>
      )}
      <details><summary className="muted">Reasoning ({r.evidence.length} steps)</summary><ul className="evidence">{r.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul></details>
    </div>
  );
}

function Reading({ img, label, texts, extra }: { img: string | null; label: string; texts?: string[]; extra?: string }) {
  return (
    <div className="reading">
      <div className="muted small">{label}</div>
      {img ? <img src={img} alt={label} /> : <div className="muted small">(no image)</div>}
      <div className="ocr">{texts?.length ? texts.map((t, i) => <code key={i}>{t || "∅"}</code>) : <span className="muted small">nothing legible</span>}</div>
      {extra && <div className="muted small">{extra}</div>}
    </div>
  );
}
