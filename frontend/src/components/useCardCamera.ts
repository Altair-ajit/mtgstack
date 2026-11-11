import { useEffect, useRef, useState } from "react";
import { api } from "../api";

type Pt = [number, number];
type Track = { found: boolean; corners?: Pt[]; hint?: string };

/**
 * Live camera with card outline + steady-card auto-capture.
 * - `auto`: capture automatically once the card has been steady for 3 frames.
 * - `paused`: keep outlining but don't capture (e.g. while waiting for the user's answer).
 * After a capture it won't fire again until the card is removed or swapped (moved a lot),
 * so the same card is never scanned twice by accident.
 */
export function useCardCamera(o: { auto: boolean; paused: boolean; onAuto: () => void | Promise<void> }) {
  const video = useRef<HTMLVideoElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [hint, setHint] = useState("");
  const [error, setError] = useState("");
  const opts = useRef(o); opts.current = o;
  const st = useRef({ history: [] as (Pt[] | null)[], armed: true, busy: false, last: null as Pt[] | null, firing: false });

  useEffect(() => () => stream?.getTracks().forEach(t => t.stop()), [stream]);

  const start = async () => {
    setError("");
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("The live camera needs HTTPS on a phone — start the app with ./run.sh --phone (or .\\run.ps1 -Phone)."); return;
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1440 } } });
      setStream(s);
      if (video.current) video.current.srcObject = s;
    } catch (e) { setError(String((e as Error).message)); }
  };
  const stop = () => { stream?.getTracks().forEach(t => t.stop()); setStream(null); };

  const grab = (maxSide = 0): Promise<Blob> => new Promise(ok => {
    const v = video.current!, c = document.createElement("canvas");
    const k = maxSide ? Math.min(1, maxSide / Math.max(v.videoWidth, v.videoHeight)) : 1;
    c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
    c.getContext("2d")!.drawImage(v, 0, 0, c.width, c.height);
    c.toBlob(b => ok(b!), "image/jpeg", maxSide ? 0.8 : 0.92);
  });

  /** Allow the card currently in view to be captured again (for "Rescan"). */
  const rearm = () => { st.current.armed = true; st.current.history = []; };

  useEffect(() => {
    if (!stream) return;
    let alive = true;
    (async () => {
      while (alive) {
        const v = video.current;
        if (v?.videoWidth && !st.current.busy) {
          st.current.busy = true;
          try { const r: Track = await api.detect(await grab(640)); draw(r); await step(r); } catch { /* keep going */ }
          st.current.busy = false;
        }
        await new Promise(r => setTimeout(r, 120));
      }
    })();
    return () => { alive = false; };
  }, [stream]); // eslint-disable-line

  const draw = (r: Track) => {
    const cv = overlay.current, v = video.current;
    if (!cv || !v) return;
    const W = v.clientWidth, H = v.clientHeight, k = Math.min(W / v.videoWidth, H / v.videoHeight);
    const ox = (W - v.videoWidth * k) / 2, oy = (H - v.videoHeight * k) / 2;
    cv.width = W * devicePixelRatio; cv.height = H * devicePixelRatio;
    const g = cv.getContext("2d")!;
    g.scale(devicePixelRatio, devicePixelRatio); g.clearRect(0, 0, W, H);
    const s = st.current, { auto, paused } = opts.current;
    if (!r.found || !r.corners) { setHint(paused ? "Answer on the right, then show the next card" : "Show a card…"); return; }
    const ready = r.hint === "ready";
    const waiting = paused || !s.armed;
    g.lineWidth = 3;
    g.strokeStyle = waiting ? "#64748b" : ready ? "#16a34a" : "#f59e0b";
    g.fillStyle = waiting ? "rgba(100,116,139,.10)" : ready ? "rgba(22,163,74,.12)" : "rgba(245,158,11,.1)";
    g.beginPath();
    r.corners.forEach(([x, y], i) => (i ? g.lineTo : g.moveTo).call(g, ox + x * v.videoWidth * k, oy + y * v.videoHeight * k));
    g.closePath(); g.fill(); g.stroke();
    setHint(paused ? "Waiting for your answer" : !s.armed ? "Swap in the next card" : ready ? (auto ? "Hold steady…" : "Ready — tap Capture") : r.hint || "");
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

  const step = async (r: Track) => {
    const s = st.current;
    s.history.push(r.found && r.hint === "ready" ? r.corners! : null);
    if (s.history.length > 8) s.history.shift();
    if (!r.found) s.armed = true;  // card removed: next card may fire
    else if (s.last && r.corners && Math.max(...r.corners.map((p, k) => Math.hypot(p[0] - s.last![k][0], p[1] - s.last![k][1]))) > 0.12) s.armed = true;
    const { auto, paused, onAuto } = opts.current;
    if (!auto || paused || s.firing || !s.armed || steady() < 3) return;
    s.armed = false; s.last = r.corners || null; s.firing = true;
    try { await onAuto(); } finally { s.firing = false; s.history = []; }
  };

  return { video, overlay, stream, start, stop, grab, hint, error, rearm };
}
