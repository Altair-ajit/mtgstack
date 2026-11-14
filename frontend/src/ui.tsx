import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

export const money = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v.toLocaleString("en-US", { style: "currency", currency: "USD" });

export const COND_LABEL: Record<string, string> = {
  NM: "Near Mint", LP: "Lightly Played", MP: "Moderately Played", HP: "Heavily Played", DMG: "Damaged",
};
export const RARITY_LABEL: Record<string, string> = {
  common: "Common", uncommon: "Uncommon", rare: "Rare", mythic: "Mythic", special: "Special", bonus: "Bonus",
};
export const FINISH_LABEL: Record<string, string> = { nonfoil: "Normal", foil: "Foil", etched: "Foil Etched" };
export const LANG_LABEL: Record<string, string> = {
  en: "English", ja: "Japanese", de: "German", fr: "French", it: "Italian", es: "Spanish", pt: "Portuguese",
  ko: "Korean", ru: "Russian", zhs: "Chinese (S)", zht: "Chinese (T)",
};
export const COLOR_LABEL: Record<string, string> = {
  W: "White", U: "Blue", B: "Black", R: "Red", G: "Green", C: "Colorless", M: "Multicolor",
};

/** {2}{G}{U/P} -> small coloured pips (simple circles, not the official symbol font). */
export function Mana({ cost }: { cost?: string | null }) {
  if (!cost) return null;
  const syms = cost.match(/\{[^}]+\}/g) || [];
  return (
    <span className="mana">
      {syms.map((s, i) => {
        const t = s.slice(1, -1);
        const cls = /^[WUBRG]$/.test(t) ? `pip pip-${t}` : t.includes("/") ? `pip pip-hybrid pip-${t[0]}` : "pip pip-generic";
        return <span key={i} className={cls} title={t}>{/^[WUBRG]$/.test(t) ? "" : t.replace("/P", "")}</span>;
      })}
    </span>
  );
}

export function ColorDot({ c }: { c: string }) {
  return <span className={`pip pip-${c === "M" ? "multi" : c === "C" ? "generic" : c}`} title={COLOR_LABEL[c]} />;
}

export function Rarity({ r }: { r: string }) {
  return <span className={`rarity rarity-${r}`} title={RARITY_LABEL[r] || r}>{(r || "?")[0].toUpperCase()}</span>;
}

export function FinishBadge({ f }: { f: string | null | undefined }) {
  if (f === "foil") return <span className="badge foil">Foil</span>;
  if (f === "etched") return <span className="badge etched">Etched</span>;
  return null;
}

/** `eager`: load now even when off screen (a card waiting beside the one on show must not arrive blank). */
export function CardImg({ src, name, size = "sm", onClick, eager }: { src?: string | null; name: string; size?: "sm" | "md" | "lg"; onClick?: () => void; eager?: boolean }) {
  const [broken, setBroken] = useState(false);
  if (!src || broken)
    return <div className={`card-img ph ${size}`} onClick={onClick}><span>{name}</span></div>;
  return <img className={`card-img ${size}`} src={src} alt={name} loading={eager ? "eager" : "lazy"} decoding={eager ? "sync" : "async"} onError={() => setBroken(true)} onClick={onClick} />;
}

/** Thumbnail that shows the large image on hover, like a marketplace listing. */
export function HoverCard({ small, large, name }: { small?: string | null; large?: string | null; name: string }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  return (
    // touch screens fire a mouse move on tap and never a mouse leave: the big picture would stay on top of
    // whatever opens next. Only devices that can hover get it.
    <span className="hovercard" onMouseMove={e => window.matchMedia("(hover: hover)").matches && setPos({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setPos(null)} onClick={() => setPos(null)}>
      <CardImg src={small} name={name} />
      {pos && (large || small) && (
        <img className="hover-big" src={large || small || ""} alt=""
             style={{ left: Math.min(pos.x + 24, window.innerWidth - 270), top: Math.max(8, Math.min(pos.y - 170, window.innerHeight - 360)) }} />
      )}
    </span>
  );
}

export function TagChip({ t, onClick }: { t: string; onClick?: () => void }) {
  const kind = t.startsWith("deck:") ? "deck" : t.startsWith("box:") || t.startsWith("binder") ? "loc" : "";
  return <span className={`chip ${kind}`} onClick={onClick}>{t.replace(/^deck:/, "")}</span>;
}

// Phones: how much of the screen the on-screen keyboard leaves. A `tall` dialog is sized to it (see styles.css).
if (typeof window !== "undefined" && window.visualViewport) {
  const vv = window.visualViewport;
  const fit = () => {
    document.documentElement.style.setProperty("--vvh", `${Math.round(vv.height)}px`);
    document.documentElement.style.setProperty("--vvtop", `${Math.round(vv.offsetTop)}px`);
  };
  vv.addEventListener("resize", fit); vv.addEventListener("scroll", fit); fit();
}

/** `tall`: for a dialog you type a search into. On a phone it is pinned to the top of the screen at a fixed height,
 * so the box you are typing in stays where it is while the results under it come and go, and it ends above the
 * keyboard. (A dialog that grows up from the bottom pushed the box off the top of the screen.) */
export function Modal({ title, onClose, children, wide, tall, footer }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean; tall?: boolean; footer?: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  // Drawn on the page itself, not where it is used: a dialog opened from the card view would otherwise be clipped
  // and moved with that view. React still passes its events up to where it was used, so touches and keys stop here.
  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();
  return createPortal(
    <div className="modal-bg" onMouseDown={e => e.target === e.currentTarget && onClose()}
      onTouchStart={stop} onTouchMove={stop} onTouchEnd={stop} onKeyDown={stop}>
      <div className={`modal ${wide ? "wide" : ""} ${tall ? "tall" : ""}`} role="dialog" aria-label={title}>
        <div className="modal-head"><h2>{title}</h2><button className="icon" onClick={onClose} aria-label="Close">×</button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="drawer-bg" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <aside className="drawer"><button className="icon drawer-x" onClick={onClose} aria-label="Close">×</button>{children}</aside>
    </div>
  );
}

export function useDebounced<T>(v: T, ms = 250): T {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

// ---- toasts ------------------------------------------------------------------------------------
type Toast = { id: number; text: ReactNode; kind: "ok" | "err" | "info" };
const ToastCtx = createContext<(text: ReactNode, kind?: Toast["kind"]) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [list, setList] = useState<Toast[]>([]);
  const n = useRef(0);
  const push = useCallback((text: ReactNode, kind: Toast["kind"] = "ok") => {
    const id = ++n.current;
    setList(l => [...l, { id, text, kind }]);
    setTimeout(() => setList(l => l.filter(t => t.id !== id)), kind === "err" ? 6000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts">{list.map(t => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}</div>
    </ToastCtx.Provider>
  );
}

export function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null;
  const nums = new Set([1, pages, page - 1, page, page + 1, page - 2, page + 2].filter(p => p >= 1 && p <= pages));
  const sorted = [...nums].sort((a, b) => a - b);
  return (
    <nav className="pager">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Prev</button>
      {sorted.map((p, i) => (
        <span key={p}>
          {i > 0 && p - sorted[i - 1] > 1 && <span className="gap">…</span>}
          <button className={p === page ? "on" : ""} onClick={() => onPage(p)}>{p}</button>
        </span>
      ))}
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next ›</button>
    </nav>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children}</div>;
}
