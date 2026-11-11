import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import QRCode from "qrcode";
import { api } from "../api";
import { Modal } from "../ui";

const KIND: Record<string, { title: string; how: string }> = {
  tailscale: { title: "Tailscale (any network)", how: "Phone needs the Tailscale app, signed in to the same account. Real HTTPS — no warnings." },
  lan: { title: "Same Wi-Fi", how: "Self-signed certificate: on iPhone tap “Show Details” → “visit this website” once, then allow the camera." },
  current: { title: "This address", how: "" },
};

function QR({ text }: { text: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => { QRCode.toDataURL(text, { margin: 1, width: 220 }).then(setSrc).catch(() => setSrc("")); }, [text]);
  return src ? <img className="qr" src={src} alt={text} /> : null;
}

/** QR codes to open the live scanner (or any page) on a phone. */
export default function PhoneDialog({ path = "/lab/live", onClose }: { path?: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["phone"], queryFn: api.phone });
  const urls = q.data?.urls || [];
  const best = urls.find(u => u.kind === "tailscale") ? urls.filter(u => u.kind !== "current") : urls;
  return (
    <Modal title="Scan with your phone" wide onClose={onClose} footer={<button onClick={onClose}>Close</button>}>
      {q.isLoading ? <p className="muted">Looking up addresses…</p> : best.length === 0 ? (
        <div className="phone-setup">
          <p>The app is only listening on this computer right now. Restart it in phone mode, then come back here:</p>
          <pre>./run.sh --tailscale --dev     # recommended: works anywhere, no certificate warning{"\n"}./run.sh --phone --dev         # same Wi-Fi only</pre>
          <p className="muted small">Tailscale: install it on this PC and your iPhone, sign in to the same account. The first run prints a link to turn on HTTPS for your tailnet (one click).</p>
        </div>
      ) : (
        <div className="phone-grid">
          {best.map(u => (
            <div key={u.url} className="phone-opt">
              <b>{KIND[u.kind]?.title || u.kind}</b>
              <QR text={u.url + path} />
              <code>{u.url + path}</code>
              {u.url.startsWith("http:") ? <p className="bad small">Plain http: the page opens but iPhone won't allow the camera. Restart with --tailscale or --phone.</p>
                : KIND[u.kind]?.how && <p className="muted small">{KIND[u.kind].how}</p>}
            </div>
          ))}
        </div>
      )}
      <p className="muted small">Point the iPhone camera at the code. Then in Safari: Share → <b>Add to Home Screen</b> to get a full-screen app icon.
        It updates by itself — when a new version is out you'll see a “Reload” bar.</p>
    </Modal>
  );
}
