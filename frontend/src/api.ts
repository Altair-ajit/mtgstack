// Typed client for the mtgstack Python API.

export type Finish = "nonfoil" | "foil" | "etched";

export interface Lot { lot_id: number; qty: number; tags: string | null }

export interface CardRow {
  printing_id: string; oracle_id: string; name: string; set_code: string; set_name: string;
  collector_number: string; rarity: string; type_line: string; mana_cost: string; cmc: number; colors: string;
  image_small: string | null; image_normal: string | null; finish: Finish; condition: string; language: string;
  qty: number; price: number | null; total: number; added: string; lots: Lot[]; tags: string[]; decks: string[];
  tcgplayer_id: number | null;
}

export interface Facet { value: string; label?: string; count: number }

export interface CardsPage {
  rows: CardRow[]; total_rows: number; page: number; size: number; pages: number;
  totals: { cards: number; unique: number; value: number };
  facets?: Record<string, Facet[]>;
}

export interface Filters {
  set: string[]; rarity: string[]; color: string[]; type: string[]; finish: string[]; condition: string[];
  language: string[]; tag: string[]; deck: string; min_price: string; max_price: string;
}
export const emptyFilters = (): Filters => ({
  set: [], rarity: [], color: [], type: [], finish: [], condition: [], language: [], tag: [], deck: "",
  min_price: "", max_price: "",
});

export interface Deck {
  slug: string; name: string; format: string; status: "building" | "built"; priority: number;
  wanted: number; in_deck: number; free: number; missing: number; missing_cost: number; progress: string;
  cover_image: string | null; cover_name: string | null; owned_value: number; commander: string | null;
}

export interface Entry {
  entry_id: number; board: string; name: string; wanted: number; in_deck: number; have: number; to_move: number;
  to_buy: number; status: "owned" | "move" | "buy"; type_line: string; mana_cost: string; cmc: number;
  colors: string; rarity: string; price: number | null; finish: Finish | null; oracle_id: string;
  card_printing_id: string; image_small: string | null; image_normal: string | null; set_code: string;
  set_name: string; collector_number: string; in_other_decks: string[];
  where: { lot_id: number; qty: number; set: string; finish: string; tags: string }[];
  in_deck_lots: { lot_id: number; qty: number; set: string; finish: string; tags: string }[];
  cheapest: { set_code: string; price: number } | null;
}

export interface Totals { wanted: number; in_deck: number; free: number; missing: number; missing_cost: number; progress: string }
export interface DeckDetail { deck: Deck; totals: Totals; shown: Totals | null; entries: Entry[] }

export interface Allocation { deck: string; deck_name: string; board: string; qty: number; name: string; deck_progress?: string }

export interface Printing {
  printing_id: string; set_code: string; set_name: string; collector_number: string; rarity: string; lang: string;
  released_at: string; finishes: Finish[]; image_small: string | null; border_color: string;
  frame_effects: string[]; promo: number; price: number | null; price_foil: number | null; owned: number;
}

export interface CardDetail {
  card: Record<string, any> & { id: string; name: string; type_line: string; oracle_text: string; mana_cost: string;
    image_normal: string | null; set_code: string; set_name: string; collector_number: string; rarity: string;
    finishes: Finish[]; oracle_id: string };
  prices: { finish: string; source: string; kind: string; price: number; currency: string; as_of: string }[];
  history: Record<string, { date: string; price: number }[]>;
  owned: { lot_id: number; printing_id: string; set_code: string; set_name: string; collector_number: string;
    finish: Finish; condition: string; language: string; qty: number; price: number | null; tags: string | null;
    image_small: string | null }[];
  printings: Printing[];
  decks: { deck_slug: string; deck_name: string; board: string; wanted: number; have: number; to_move: number;
    to_buy: number; deck_status: string }[];
}

export interface NameHit { name: string; oracle_id: string; type_line: string; mana_cost: string; image_small: string | null }

export interface ImportRow {
  line: number; raw: string; qty: number; name: string | null; set_code: string | null; set_name: string | null;
  number: string | null; finish: Finish | null; condition: string; language: string; product_id: number | null;
  status: "matched" | "guessed" | "unmatched"; how: string; printing_id: string | null;
  match: { printing_id: string; name: string; set_code: string; set_name: string; number: string; rarity: string;
    finish: Finish; finishes: Finish[]; image: string | null; price: number | null } | null;
  alternatives: NonNullable<ImportRow["match"]>[]; tags: string[]; include?: boolean;
}
export interface ImportPreview {
  format: string; rows: ImportRow[]; errors: string[];
  counts: Record<"matched" | "guessed" | "unmatched", number>; cards: Record<string, number>; value: number;
}

export interface Summary { cards: number; printings_owned: number; value: number; catalog_printings: number;
  decks: number; building: number; online: boolean }
export interface CatalogStatus { printings: number; synced_at: string | null;
  job: { state: "idle" | "running" | "done" | "error"; bytes: number; total: number; cards: number; error: string | null } }

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method, headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(typeof data === "string" ? data : data.detail || r.statusText);
  return data as T;
}
const get = <T,>(p: string) => req<T>("GET", p);
const post = <T,>(p: string, b?: unknown) => req<T>("POST", p, b ?? {});
const qs = (o: Record<string, unknown>) =>
  new URLSearchParams(Object.entries(o).flatMap(([k, v]) =>
    v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length) ? [] : [[k, Array.isArray(v) ? v.join(",") : String(v)]])).toString();

export const api = {
  summary: () => get<Summary>("/api/summary"),
  catalogStatus: () => get<CatalogStatus>("/api/catalog/status"),
  syncCatalog: () => post("/api/catalog/sync"),
  cards: (q: string, f: Filters, sort: string, page: number, size: number, facets = true) =>
    get<CardsPage>(`/api/cards?${qs({ q, ...f, sort, page, size, facets })}`),
  card: (id: string) => get<CardDetail>(`/api/card/${id}`),
  names: (text: string) => get<NameHit[]>(`/api/catalog/names?${qs({ text })}`),
  prints: (name: string) => get<any[]>(`/api/catalog/prints?${qs({ name })}`),
  addLot: (b: { printing_id: string; qty: number; finish: Finish; condition: string; language?: string; tags?: string[] }) =>
    post<{ lot_id: number; name: string; allocations: Allocation[] }>("/api/lots", b),
  patchLot: (id: number, b: Partial<{ qty: number; condition: string; finish: Finish; language: string }>) =>
    req("PATCH", `/api/lots/${id}`, b),
  bulk: (b: { lot_ids: number[]; action: "send" | "tag" | "untag" | "remove"; qty?: number | null; deck?: string | null;
    board?: string; tags?: string[] }) => post("/api/lots/bulk", b),
  send: (lot: number, b: { qty?: number | null; deck: string | null; board?: string }) => post(`/api/lots/${lot}/send`, b),
  decks: () => get<Deck[]>("/api/decks"),
  deck: (slug: string, q = "") => get<DeckDetail>(`/api/decks/${slug}?${qs({ q })}`),
  patchDeck: (slug: string, b: Partial<{ status: string; priority: number; name: string; format: string }>) =>
    req("PATCH", `/api/decks/${slug}`, b),
  deleteDeck: (slug: string) => req("DELETE", `/api/decks/${slug}`),
  pull: (slug: string, prefer = "cheapest") => post<{ pulled: { qty: number }[] }>(`/api/decks/${slug}/pull?${qs({ prefer })}`),
  release: (slug: string) => post(`/api/decks/${slug}/release`),
  exportDeck: (slug: string, fmt: string, source: string) => get<string>(`/api/decks/${slug}/export?${qs({ fmt, source })}`),
  missing: (slug: string) => get<string>(`/api/decks/${slug}/missing`),
  importPreview: (text: string, filename?: string) => post<ImportPreview>("/api/import/preview", { text, filename }),
  importCommit: (rows: ImportRow[], dest: Record<string, unknown>) =>
    post<{ cards: number; rows: number; deck?: string; allocations: Allocation[] }>("/api/import/commit", { rows, dest }),
  scan: (files: Blob[]) => {
    const fd = new FormData();
    files.forEach((f, i) => fd.append("files", f, `frame${i}.jpg`));
    return fetch("/api/scan", { method: "POST", body: fd }).then(async r => {
      const d = await r.json(); if (!r.ok) throw new Error(d.detail || r.statusText); return d;
    });
  },
  detect: (frame: Blob) => {
    const fd = new FormData(); fd.append("file", frame, "f.jpg");
    return fetch("/api/detect", { method: "POST", body: fd }).then(r => r.json());
  },
};

// ---- Scanner Lab ---------------------------------------------------------------------------------
export interface LabSummary {
  id: string; ts: string; source: string; status: "unreviewed" | "correct" | "wrong";
  guess: { name: string | null; set_code: string | null; number: string | null; finish: Finish; confidence: number; printing_id: string | null };
  wrong_fields: string[]; truth: { name: string; set_code: string; number: string; finish: Finish; printing_id: string } | null;
}
export interface LabScan {
  id: string; ts: string; files: string[]; summary: LabSummary;
  result: {
    name: string | null; set_code: string | null; collector_number: string | null; finish: Finish; language: string;
    confidence: number; finish_confidence: number; printing_id: string | null; evidence: string[];
    candidates: { printing_id: string; name: string; set_code: string; collector_number: string; image: string | null; score: number; why: string }[];
    markers: { title_ocr?: string[]; type_ocr?: string[]; collector_ocr?: string[]; name_matches?: [string, number][];
      collector_hits?: { set: string; number: string; name: string }[]; border?: { color: string } };
  };
  label: { verdict: string; notes: string } | null;
}
export interface LabStats {
  total: number; reviewed: number; unreviewed: number; fully_correct: number | null;
  accuracy: Record<"card" | "printing" | "finish" | "language", number | null>;
  confusions: { guessed: string; actual: string; count: number }[];
  avg_confidence_when_right: number | null; avg_confidence_when_wrong: number | null; scans_dir: string;
}
export interface Rerun {
  state: "idle" | "running" | "done" | "error"; done: number; total: number; error: string | null;
  report: null | { n: number; then: Record<string, number>; now: Record<string, number>; scanner_version: string;
    fixed: { id: string; truth: string; was: string | null; now: string | null }[];
    broken: { id: string; truth: string; was: string | null; now: string | null }[];
    still_wrong: { id: string; truth: string; was: string | null; now: string | null; now_wrong: string[] }[] };
}
export const lab = {
  list: (status = "") => get<{ total: number; items: LabSummary[] }>(`/api/lab/scans?${qs({ status })}`),
  get: (id: string) => get<LabScan>(`/api/lab/scans/${id}`),
  file: (id: string, name: string) => `/api/lab/file/${id}/${name}`,
  label: (id: string, b: { verdict: "correct" | "wrong" | "unreadable"; printing_id?: string; finish?: Finish; notes?: string }) =>
    post<LabSummary>(`/api/lab/scans/${id}/label`, b),
  remove: (id: string) => req("DELETE", `/api/lab/scans/${id}`),
  stats: () => get<LabStats>("/api/lab/stats"),
  rerun: () => post<Rerun>("/api/lab/rerun"),
  rerunStatus: () => get<Rerun>("/api/lab/rerun"),
  upload: (files: File[]) => {
    const fd = new FormData(); files.forEach(f => fd.append("files", f, f.name));
    return fetch("/api/lab/upload", { method: "POST", body: fd }).then(r => r.json() as Promise<{ scans: { scan_id: string; name: string | null }[] }>);
  },
};
