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
  /** only the listed printing counts (off: any printing of the card does) */
  exact: boolean; listed_printing_id: string | null; listed_finish: Finish | null;
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
  job: { state: "idle" | "running" | "done" | "error"; bytes: number; total: number; cards: number; error: string | null };
  /** phone build only */
  update_available?: boolean; local_version?: string | null;
  remote?: { version: string; printings: number; size_gz: number; file: string } | null }

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

export interface PhoneInfo { urls: { url: string; kind: "tailscale" | "lan" | "current" }[]; secure: boolean }

export const api = {
  summary: () => get<Summary>("/api/summary"),
  phone: () => get<PhoneInfo>("/api/phone"),
  version: () => get<{ build: string; scanner: string }>("/api/version"),
  catalogStatus: () => get<CatalogStatus>("/api/catalog/status"),
  syncCatalog: () => post("/api/catalog/sync"),
  cards: (q: string, f: Filters, sort: string, page: number, size: number, facets = true) =>
    get<CardsPage>(`/api/cards?${qs({ q, ...f, sort, page, size, facets })}`),
  /** filter counts for the same search and filters as `cards` (asked for apart: they cost more than the page) */
  facets: (q: string, f: Filters) => get<Record<string, Facet[]>>(`/api/facets?${qs({ q, ...f })}`),
  card: (id: string) => get<CardDetail>(`/api/card/${id}`),
  names: (text: string) => get<NameHit[]>(`/api/catalog/names?${qs({ text })}`),
  /** `complete`: fetch the card's full printing list if the card database isn't downloaded (for pickers) */
  prints: (name: string, complete = false) => get<any[]>(`/api/catalog/prints?${qs({ name, complete: complete || undefined })}`),
  addLot: (b: { printing_id: string; qty: number; finish: Finish; condition: string; language?: string; tags?: string[] }) =>
    post<{ lot_id: number; name: string; allocations: Allocation[] }>("/api/lots", b),
  patchLot: (id: number, b: Partial<{ qty: number; condition: string; finish: Finish; language: string }>) =>
    req("PATCH", `/api/lots/${id}`, b),
  bulk: (b: { lot_ids: number[]; action: "send" | "tag" | "untag" | "remove"; qty?: number | null; deck?: string | null;
    board?: string; tags?: string[] }) => post("/api/lots/bulk", b),
  /** move copies into a deck/board, or out (deck null); each lot with its own count, all or nothing */
  moveLots: (moves: { lot_id: number; qty?: number }[], deck: string | null, board = "main") =>
    post<{ moved: number }>("/api/lots/move", { moves, deck, board }),
  send: (lot: number, b: { qty?: number | null; deck: string | null; board?: string }) => post(`/api/lots/${lot}/send`, b),
  tags: () => get<{ tag: string; lots: number; cards: number }[]>("/api/tags"),
  decks: () => get<Deck[]>("/api/decks"),
  /** a deck from a decklist; `text` may be empty for a deck to fill by hand */
  createDeck: (b: { name: string; text: string; format: string; status: "building" | "built" }) =>
    post<{ slug: string; entries: number; unresolved: string[] }>("/api/decks", b),
  deck: (slug: string, q = "") => get<DeckDetail>(`/api/decks/${slug}?${qs({ q })}`),
  /** renaming changes the deck's slug (its address and its `deck:` tag): use the one that comes back */
  patchDeck: (slug: string, b: Partial<{ status: string; priority: number; name: string; format: string }>) =>
    req<{ slug: string; name: string }>("PATCH", `/api/decks/${slug}`, b),
  deleteDeck: (slug: string) => req("DELETE", `/api/decks/${slug}`),
  /** grow a deck's list to cover the cards tagged into it (after scanning cards into the deck) */
  deckCover: (slug: string) => post<{ slug: string }>(`/api/decks/${slug}/from-tags?keep=true`),
  pull: (slug: string, prefer = "cheapest") => post<{ pulled: { qty: number }[] }>(`/api/decks/${slug}/pull?${qs({ prefer })}`),
  /** how many copies the deck's list asks for */
  setEntry: (slug: string, entry: number, b: { qty?: number; exact?: boolean }) => req("PATCH", `/api/decks/${slug}/entries/${entry}`, b),
  /** take `qty` copies of a card (all of them when left out) off the deck's list; copies in the deck that the list
   * no longer asks for go back to the collection */
  removeEntry: (slug: string, entry: number, qty?: number) =>
    req("DELETE", `/api/decks/${slug}/entries/${entry}${qty ? `?qty=${qty}` : ""}`) as Promise<{ returned: number }>,
  /** put a card you do not have yet on the deck's list (to buy) */
  addEntry: (slug: string, b: { printing_id: string; qty: number; board: string; exact: boolean }) =>
    post<{ entry_id: number }>(`/api/decks/${slug}/entries`, b),
  release: (slug: string) => post(`/api/decks/${slug}/release`),
  exportDeck: (slug: string, fmt: string, source: string) => get<string>(`/api/decks/${slug}/export?${qs({ fmt, source })}`),
  missing: (slug: string) => get<string>(`/api/decks/${slug}/missing`),
  importPreview: (text: string, filename?: string) => post<ImportPreview>("/api/import/preview", { text, filename }),
  importCommit: (rows: ImportRow[], dest: Record<string, unknown>) =>
    post<{ cards: number; rows: number; deck?: string; allocations: Allocation[] }>("/api/import/commit", { rows, dest }),
  /** `corners`: the outline api.detect just reported for this view, so the card isn't searched for again */
  scan: (files: Blob[], corners?: [number, number][]) => {
    const fd = new FormData();
    files.forEach((f, i) => fd.append("files", f, `frame${i}.jpg`));
    if (corners) fd.append("corners", JSON.stringify(corners));
    return fetch("/api/scan", { method: "POST", body: fd }).then(async r => {
      const d = await r.json(); if (!r.ok) throw new Error(d.detail || r.statusText); return d;
    });
  },
  detect: (frame: Blob) => {
    const fd = new FormData(); fd.append("file", frame, "f.jpg");
    return fetch("/api/detect", { method: "POST", body: fd }).then(async r => {
      const d = await r.json(); if (!r.ok) throw new Error(d.detail || r.statusText); return d;
    });
  },
};
