import { useEffect, useRef, useState } from "react";
import { api } from "../api";

/** Shows a reload bar when the server has a newer build (UI rebuilt or scanner changed). */
export default function UpdateBanner() {
  const first = useRef<string | null>(null);
  const [stale, setStale] = useState(false);
  useEffect(() => {
    let on = true;
    const check = async () => {
      try {
        const v = await api.version();
        if (!on) return;
        if (first.current === null) first.current = v.build;
        else if (v.build !== first.current) setStale(true);
      } catch { /* server restarting */ }
    };
    check();
    const t = setInterval(check, 20000);
    const vis = () => document.visibilityState === "visible" && check();
    document.addEventListener("visibilitychange", vis);
    return () => { on = false; clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, []);
  if (!stale) return null;
  return <div className="banner update">A new version is ready. <button className="primary" onClick={() => location.reload()}>Reload</button></div>;
}
