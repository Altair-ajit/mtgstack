import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { BUILD, LOCAL, NATIVE } from "../local/boot";

/** Phone build: compare the build baked into this page with the one currently published on GitHub Pages. */
async function latestBuild(): Promise<string> {
  if (!LOCAL) return (await api.version()).build;
  const r = await fetch(`${import.meta.env.BASE_URL}version.json`, { cache: "no-store" });
  if (!r.ok) throw new Error("offline");
  return (await r.json()).build;
}

/** Shows a reload bar when the server has a newer build (UI rebuilt or scanner changed). */
export default function UpdateBanner() {
  const first = useRef<string | null>(null);
  const [stale, setStale] = useState(false);
  useEffect(() => {
    let on = true;
    const check = async () => {
      try {
        const b = await latestBuild();
        if (!on) return;
        if (first.current === null) first.current = LOCAL ? BUILD : b;
        if (b !== first.current) setStale(true);
      } catch { /* server restarting */ }
    };
    check();
    const t = setInterval(check, LOCAL ? 300000 : 20000);
    const vis = () => document.visibilityState === "visible" && check();
    document.addEventListener("visibilitychange", vis);
    return () => { on = false; clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, []);
  if (!stale || NATIVE) return null;          // an installed app is updated by its store, not by a reload
  return <div className="banner update">A new version is ready. <button className="primary" onClick={() => location.reload()}>Reload</button></div>;
}
