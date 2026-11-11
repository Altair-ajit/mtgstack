import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ToastProvider } from "./ui";
import "./styles.css";
import { bootLocal, LOCAL } from "./local/boot";

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

if (LOCAL) bootLocal().then(render).catch(e => console.error(e));
else render();
