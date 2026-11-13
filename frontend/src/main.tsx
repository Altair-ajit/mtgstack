import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ToastProvider } from "./ui";
import "./styles.css";
import { bootLocal, engineEvents, LOCAL, NATIVE } from "./local/boot";

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, refetchOnWindowFocus: false, retry: 1 } } });

const render = () => createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "")}>
        <ToastProvider><App /></ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);

// The CI simulator run drops a selftest.json into the app bundle: tour the app and report (local/selftest.ts).
// A real app has no such file (the web view then answers with the app's own page, which is not JSON).
const selfTest = () => NATIVE && fetch(`${import.meta.env.BASE_URL}selftest.json`).then(r => r.json())
  .then(j => j.selftest && import("./local/selftest").then(m => m.run(engineEvents))).catch(() => {});

if (LOCAL) bootLocal().then(render).then(selfTest).catch(e => console.error(e));
else render();
