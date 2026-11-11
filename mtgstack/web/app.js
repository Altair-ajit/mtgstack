// mtgstack web UI — vanilla JS, talks to the JSON API in server.py
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = v => v == null ? "–" : "$" + Number(v).toFixed(2);
const tagsOf = s => (s || "").split(/[\s,]+/).filter(Boolean);

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json) { o.method = o.method || "POST"; o.headers = { "Content-Type": "application/json" }; o.body = JSON.stringify(o.json); }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) { toast(data.detail || data || r.statusText, true); throw new Error(data.detail || r.statusText); }
  return data;
}
function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 3200);
}
function modal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
$("#modal-close").onclick = () => $("#modal").classList.add("hidden");
$("#modal").onclick = e => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); };
const finishBadge = f => f === "foil" ? '<span class="finish-foil">FOIL</span>' : f === "etched" ? '<span class="finish-etched">ETCHED</span>' : "";
const tagChip = t => `<span class="tag ${t.startsWith("deck:") ? "deck" : ""}" data-q="tag:${esc(t)}">${esc(t)}</span>`;
const imgFor = (id, v = "small") => window.MTG_IMG ? window.MTG_IMG(id, v) : `https://api.scryfall.com/cards/${id}?format=image&version=${v}`;

// ---------------------------------------------------------------- tabs
$$("nav button").forEach(b => b.onclick = () => {
  $$("nav button").forEach(x => x.classList.toggle("active", x === b));
  $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  ({ collection: search, decks: loadDecks, prices: loadPrices }[b.dataset.tab] || (() => {}))();
});
document.addEventListener("click", e => {
  const t = e.target.closest(".tag[data-q]");
  if (t) { $("#q").value = t.dataset.q; $$("nav button")[0].click(); }
});

// ---------------------------------------------------------------- collection
const selected = new Set();
async function search() {
  const q = $("#q").value;
  const data = await api("/api/query?view=stacks&q=" + encodeURIComponent(q));
  const s = data.summary;
  $("#summary").textContent = `${s.cards} cards · ${s.unique} unique · ${s.lots} lots · ${money(s.value_usd)} (TCGplayer market)`;
  const open = new Set($$(".stack.open").map(e => e.dataset.oid));
  $("#stacks").innerHTML = data.stacks.map(st => `
    <div class="stack ${open.has(st.oracle_id) ? "open" : ""}" data-oid="${st.oracle_id}">
      <div class="stack-head"><span class="qty">${st.qty}×</span>
        <span><b>${esc(st.name)}</b> <span class="cost">${esc(st.mana_cost || "")} · ${esc(st.type_line || "")}</span><br>
          ${st.tags.map(tagChip).join(" ")}</span>
        <span class="cost">${st.printings.length} printing${st.printings.length > 1 ? "s" : ""}</span>
        <span class="val">${money(st.value)}</span></div>
      <div class="stack-body">${st.printings.map(p => `
        <div class="printing"><img loading="lazy" src="${imgFor(p.printing_id)}" data-pid="${p.printing_id}" alt="">
          <div><div class="ptitle"><b>${esc(p.set_code.toUpperCase())}</b> #${esc(p.collector_number)} · ${esc(p.set_name || "")} · ${esc(p.rarity || "")}
            ${finishBadge(p.finish)} · ${money(p.price)} ea</div>
            <table>${p.lots.map(l => `
              <tr class="lot-row ${selected.has(l.lot_id) ? "sel" : ""}" data-lot="${l.lot_id}">
                <td style="width:22px"><input type="checkbox" ${selected.has(l.lot_id) ? "checked" : ""}></td>
                <td class="num" style="width:36px">${l.qty}×</td><td style="width:44px">${l.condition}</td><td style="width:36px">${l.language}</td>
                <td>${tagsOf(l.tags).map(tagChip).join(" ") || '<span class="muted">untagged</span>'}</td>
                <td class="mono muted" style="width:60px">lot ${l.lot_id}</td></tr>`).join("")}</table></div></div>`).join("")}
      </div></div>`).join("") || '<p class="muted">Nothing matches.</p>';
  loadTags();
}
$("#stacks").addEventListener("click", e => {
  const head = e.target.closest(".stack-head");
  if (head && !e.target.closest(".tag")) return head.parentElement.classList.toggle("open");
  const img = e.target.closest("img[data-pid]");
  if (img) return showPrices(img.dataset.pid);
  const row = e.target.closest(".lot-row");
  if (row) {
    const id = +row.dataset.lot;
    selected.has(id) ? selected.delete(id) : selected.add(id);
    row.classList.toggle("sel", selected.has(id)); $("input", row).checked = selected.has(id);
    updateSel();
  }
});
function updateSel() {
  $("#selbar").classList.toggle("hidden", !selected.size);
  $("#sel-count").textContent = `${selected.size} lot${selected.size > 1 ? "s" : ""} selected`;
}
$("#sel-clear").onclick = () => { selected.clear(); updateSel(); search(); };
$("#sel-move").onclick = async () => {
  const qty = $("#sel-qty").value ? +$("#sel-qty").value : null;
  for (const id of selected) {
    await api(`/api/lots/${id}/move`, { json: { qty, add_tags: tagsOf($("#sel-add").value), remove_tags: tagsOf($("#sel-rm").value) } }).catch(() => {});
  }
  toast(`Moved ${selected.size} lot(s)`); selected.clear(); updateSel(); search();
};
async function sendSelected(deck) {
  const qty = $("#sel-qty").value ? +$("#sel-qty").value : null;
  for (const id of selected)
    await api(`/api/lots/${id}/send`, { json: { qty, deck, board: $("#sel-board").value } }).catch(() => {});
  toast(deck ? `Sent ${selected.size} lot(s) to ${deck}` : `Returned ${selected.size} lot(s) to the collection`);
  selected.clear(); updateSel(); search();
}
$("#sel-send").onclick = () => $("#sel-deck").value ? sendSelected($("#sel-deck").value) : toast("Pick a deck", true);
$("#sel-back").onclick = () => sendSelected(null);
$("#q-go").onclick = search;
$("#q").addEventListener("keydown", e => { if (e.key === "Enter") search(); });
$("#q-help").onclick = async () => {
  const h = $("#help");
  if (!h.textContent) h.textContent = await api("/api/help/query");
  h.classList.toggle("hidden");
};
async function loadTags() {
  const tags = await api("/api/tags");
  $("#tag-list").innerHTML = tags.map(t => `<span class="tag ${t.tag.startsWith("deck:") ? "deck" : ""}" data-q="tag:${esc(t.tag)}">${esc(t.tag)}<small>${t.cards}</small></span>`).join("")
    || '<span class="muted">No tags yet.</span>';
}
async function splitDupes(apply) {
  const body = { q: $("#q").value, keep: +$("#sd-keep").value, by: $("#sd-by").value,
                 add_tags: tagsOf($("#sd-add").value), remove_tags: tagsOf($("#sd-rm").value), dry_run: !apply };
  const r = await api("/api/bulk/split-dupes", { json: body });
  const n = r.plan.reduce((a, s) => a + s.move_qty, 0);
  $("#sd-out").innerHTML = `<p>${apply ? "Moved" : "Would move"} <b>${n}</b> cards from ${r.plan.length} lots.</p>` +
    `<table>${r.plan.slice(0, 200).map(s => `<tr><td>${esc(s.name)}</td><td>${s.set.toUpperCase()}</td><td class="num">move ${s.move_qty}</td><td class="num muted">keep ${s.stay_qty}</td></tr>`).join("")}</table>`;
  if (apply) search();
}
$("#sd-preview").onclick = () => splitDupes(false);
$("#sd-apply").onclick = () => confirm("Move the extra copies?") && splitDupes(true);

// add a card by name -> pick printing
let addTimer;
$("#add-name").addEventListener("input", () => {
  clearTimeout(addTimer);
  addTimer = setTimeout(async () => {
    const name = $("#add-name").value.trim();
    if (name.length < 3) return ($("#add-prints").innerHTML = "");
    let prints = await api("/api/catalog/prints?name=" + encodeURIComponent(name)).catch(() => []);
    if (!prints.length) {
      const sug = await api("/api/catalog/suggest?text=" + encodeURIComponent(name)).catch(() => []);
      $("#add-prints").innerHTML = sug.map(s => `<div class="print-opt" style="cursor:pointer" data-name="${esc(s.name)}">${esc(s.name)}</div>`).join("") || '<span class="muted">No match</span>';
      return;
    }
    $("#add-prints").innerHTML = prints.map(p => {
      const fins = JSON.parse(p.finishes || "[]");
      return `<div class="print-opt" data-pid="${p.id}"><div><b>${p.set_code.toUpperCase()}</b> #${esc(p.collector_number)} ${p.lang !== "en" ? p.lang : ""}<br><span class="muted">${esc(p.set_name)} · ${p.border_color || ""}</span></div>
        <img src="${imgFor(p.id)}" style="width:44px;border-radius:3px" loading="lazy">
        <div class="row" style="grid-column:1/3"><input type="number" value="1" min="1" style="width:3.5em" class="a-qty">
          <select class="a-fin">${fins.map(f => `<option>${f}</option>`).join("")}</select>
          <select class="a-cond"><option>NM</option><option>LP</option><option>MP</option><option>HP</option><option>DMG</option></select>
          <input class="a-tags" placeholder="tags" style="width:8em"><button class="small a-go">Add</button></div></div>`;
    }).join("");
  }, 300);
});
$("#add-prints").addEventListener("click", async e => {
  const sug = e.target.closest("[data-name]");
  if (sug) { $("#add-name").value = sug.dataset.name; return $("#add-name").dispatchEvent(new Event("input")); }
  if (!e.target.classList.contains("a-go")) return;
  const box = e.target.closest(".print-opt");
  const lot = await api("/api/lots", { json: { printing_id: box.dataset.pid, qty: +$(".a-qty", box).value, finish: $(".a-fin", box).value,
    condition: $(".a-cond", box).value, tags: tagsOf($(".a-tags", box).value) } });
  toast(lot.allocations.length ? lot.allocations.map(a => `${a.qty}× → ${a.deck_name} (${a.deck_progress})`).join(", ") : "Added");
  search();
});

async function showPrices(pid) {
  const d = await api("/api/prices/" + pid);
  const rows = d.quotes.map(q => `<tr><td>${q.finish}</td><td>${q.source}</td><td>${q.kind}</td><td class="num">${q.currency === "USD" ? "$" : "€"}${q.price.toFixed(2)}</td><td class="muted">${q.as_of}</td></tr>`).join("");
  const hist = Object.entries(d.history).filter(([, h]) => h.length > 1).map(([f, h]) => `<p><b>${f}</b> ${spark(h.map(x => x.price))} ${money(h[0].price)} → ${money(h.at(-1).price)} <span class="muted">(${h[0].as_of} – ${h.at(-1).as_of})</span></p>`).join("");
  modal(`<div style="display:grid;grid-template-columns:200px 1fr;gap:16px"><img src="${imgFor(pid, "normal")}" style="width:200px;border-radius:8px">
    <div><h3>Prices</h3><table><tr><th>finish</th><th>source</th><th>kind</th><th class="num">price</th><th>as of</th></tr>${rows || '<tr><td colspan=5 class="muted">No prices yet — refresh on the Prices tab.</td></tr>'}</table>${hist}</div></div>`);
}
function spark(vals) {
  const w = 120, h = 24, mn = Math.min(...vals), mx = Math.max(...vals) || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - ((v - mn) / ((mx - mn) || 1)) * h}`).join(" ");
  return `<svg width="${w}" height="${h}" style="vertical-align:middle"><polyline points="${pts}" fill="none" stroke="#e0a64b" stroke-width="1.5"/></svg>`;
}

// ---------------------------------------------------------------- scanner
let stream = null, lastScan = null;
$("#cam-start").onclick = async () => {
  if (!navigator.mediaDevices?.getUserMedia) return toast("Live camera needs HTTPS (run `mtgstack serve --https`) — use upload instead", true);
  stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1440 } } });
  $("#cam").srcObject = stream; $("#cam-wrap").classList.remove("hidden");
  $("#cam-snap").disabled = $("#cam-burst").disabled = false;
  trackLoop();
};
function grab(maxSide = 0) {
  const v = $("#cam"), c = document.createElement("canvas");
  const k = maxSide ? Math.min(1, maxSide / Math.max(v.videoWidth, v.videoHeight)) : 1;
  c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
  c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
  return new Promise(r => c.toBlob(r, "image/jpeg", maxSide ? 0.8 : 0.92));
}

// ---- live edge tracking: small frames -> /api/detect -> outline + coaching; optional auto-capture
const track = { busy: false, history: [], armed: true, scanning: false };
async function trackLoop() {
  while (stream) {
    if ($("#cam").videoWidth && $("#cam-track").checked && !track.busy && !track.scanning && document.visibilityState === "visible"
        && $("#tab-scan").classList.contains("active")) {
      track.busy = true;
      try {
        const fd = new FormData(); fd.append("file", await grab(640), "f.jpg");
        const r = await (await fetch("/api/detect", { method: "POST", body: fd })).json();
        drawTrack(r); autoCapture(r);
      } catch (e) { console.error("track", e); }
      track.busy = false;
    } else if (!$("#cam-track").checked) drawTrack(null);
    await new Promise(r => setTimeout(r, 120));
  }
}
function videoRect() {   // where the video image actually sits inside the element (object-fit: contain)
  const v = $("#cam"), W = v.clientWidth, H = v.clientHeight, k = Math.min(W / v.videoWidth, H / v.videoHeight);
  return { x: (W - v.videoWidth * k) / 2, y: (H - v.videoHeight * k) / 2, w: v.videoWidth * k, h: v.videoHeight * k, W, H };
}
function drawTrack(r) {
  const cv = $("#cam-overlay"), g = cv.getContext("2d"), vr = videoRect();
  cv.width = vr.W * devicePixelRatio; cv.height = vr.H * devicePixelRatio; g.scale(devicePixelRatio, devicePixelRatio);
  g.clearRect(0, 0, vr.W, vr.H);
  const hint = $("#cam-hint");
  if (!r) { hint.textContent = ""; return; }
  if (!r.found) { hint.textContent = "looking for a card…"; hint.className = "cam-hint"; return; }
  const ready = r.hint === "ready";
  const pts = r.corners.map(([x, y]) => [vr.x + x * vr.w, vr.y + y * vr.h]);
  g.lineWidth = 3; g.strokeStyle = ready ? "#5bb37a" : "#e0a64b"; g.fillStyle = ready ? "rgba(91,179,122,.12)" : "rgba(224,166,75,.10)";
  g.beginPath(); pts.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y)); g.closePath(); g.fill(); g.stroke();
  const steady = stableFor() ;
  hint.textContent = ready ? ($("#cam-autocap").checked ? (steady >= 3 ? "capturing…" : "hold steady…") : "ready — tap Capture") : r.hint;
  hint.className = "cam-hint " + (ready ? "ready" : "wait");
}
function stableFor() {   // how many consecutive detections the corners have stayed put (<2.5% of frame)
  const h = track.history; let n = 0;
  for (let i = h.length - 1; i > 0; i--) {
    const a = h[i], b = h[i - 1];
    if (!a || !b) break;
    const d = Math.max(...a.map((p, k) => Math.min(...[0, 1, 2, 3].map(s => Math.hypot(p[0] - b[(k + s) % 4][0], p[1] - b[(k + s) % 4][1])))));
    if (d > 0.025) break; n++;
  }
  return n;
}
async function autoCapture(r) {
  track.history.push(r.found && r.hint === "ready" ? r.corners : null);
  if (track.history.length > 8) track.history.shift();
  if (!r.found) track.armed = true;                       // card removed -> ready for the next one
  else if (track.last && r.hint === "ready" &&             // ...or a card now sits clearly elsewhere
           Math.max(...r.corners.map((p, k) => Math.hypot(p[0] - track.last[k][0], p[1] - track.last[k][1]))) > 0.12) track.armed = true;
  if (!$("#cam-autocap").checked || !track.armed || stableFor() < 3) return;
  track.armed = false; track.scanning = true; track.last = r.corners;
  try {
    if ($("#cam-autoburst").checked) await $("#cam-burst").onclick(); else await sendScan([await grab()]);
  } finally { track.scanning = false; track.history = []; }
}
$("#cam-snap").onclick = async () => sendScan([await grab()]);
$("#cam-burst").onclick = async () => {
  toast("Tilt the card slowly…");
  const frames = [];
  for (let i = 0; i < 6; i++) { frames.push(await grab()); await new Promise(r => setTimeout(r, 300)); }
  await sendScan(frames);
};
$("#scan-file").onchange = e => sendScan([...e.target.files]);

async function sendScan(blobs) {
  const fd = new FormData();
  blobs.forEach((b, i) => fd.append("files", b, `frame${i}.jpg`));
  $("#scan-result").innerHTML = '<p class="muted">Reading markers…</p>';
  const r = await api("/api/scan", { method: "POST", body: fd }).catch(() => null);
  if (!r) return;
  lastScan = r; renderScan(r);
  if ($("#scan-auto").checked && !r.needs_review && r.printing_id) addScanned();
}
function confClass(c) { return c >= 0.9 ? "hi" : c >= 0.6 ? "mid" : "lo"; }
function renderScan(r) {
  const el = $("#scan-result");
  if (!r.printing_id && !r.candidates.length) {
    el.innerHTML = `<div><h2>Couldn't identify</h2><ul class="evidence">${r.evidence.map(e => `<li>${esc(e)}</li>`).join("")}</ul>
      <p class="muted">Try better light, fill the frame, or add it by name on the Collection tab.</p></div>`; return;
  }
  el.innerHTML = `<img src="${r.printing_id ? imgFor(r.printing_id, "normal") : ""}">
    <div><h2>${esc(r.name || "?")}</h2>
      <div>${(r.set_code || "").toUpperCase()} #${esc(r.collector_number || "?")} · lang ${r.language}
        <span class="conf ${confClass(r.confidence)}">id ${Math.round(r.confidence * 100)}%</span></div>
      <div class="row">finish
        <select id="sr-fin">${["nonfoil", "foil", "etched"].map(f => `<option ${f === r.finish ? "selected" : ""}>${f}</option>`).join("")}</select>
        <span class="conf ${confClass(r.finish_confidence)}">${Math.round(r.finish_confidence * 100)}%</span>
        <button id="sr-add">Add to collection</button></div>
      ${r.candidates.length > 1 ? `<div class="muted">Other printings — click to pick:</div><div class="cands">${r.candidates.slice(0, 12).map(c =>
        `<div class="cand ${c.printing_id === r.printing_id ? "on" : ""}" data-pid="${c.printing_id}" data-set="${c.set_code}" data-cn="${esc(c.collector_number)}" data-name="${esc(c.name)}">
          <img src="${imgFor(c.printing_id)}" loading="lazy">${c.set_code.toUpperCase()} #${esc(c.collector_number)}</div>`).join("")}</div>` : ""}
      <details><summary class="muted">How it decided</summary><ul class="evidence">${r.evidence.map(e => `<li>${esc(e)}</li>`).join("")}</ul></details>
    </div>`;
  $("#sr-add").onclick = addScanned;
  $$(".cand", el).forEach(c => c.onclick = () => {
    Object.assign(lastScan, { printing_id: c.dataset.pid, set_code: c.dataset.set, collector_number: c.dataset.cn, name: c.dataset.name });
    renderScan(lastScan);
  });
}
async function addScanned() {
  const r = lastScan;
  const fin = $("#sr-fin")?.value || r.finish;
  const lot = await api("/api/lots", { json: { printing_id: r.printing_id, qty: 1, finish: fin, condition: $("#scan-cond").value,
    language: r.language, tags: tagsOf($("#scan-tags").value) } });
  const al = lot.allocations || [];
  $("#scan-log").insertAdjacentHTML("afterbegin", `<li data-lot="${lot.lot_id}">${esc(lot.name)} <span class="muted">${lot.set_code.toUpperCase()} #${esc(lot.collector_number)}</span> ${finishBadge(lot.finish)}
    ${al.length ? `<span class="alloc">${allocNote(al)}</span> <button class="small ghost" data-keep="${al[0].lot_id}">keep in collection</button>` : `<span class="muted">→ collection</span>`}</li>`);
  toast(al.length ? `${lot.name} → ${al[0].deck_name} (${al[0].deck_progress})` : "Added " + lot.name);
}

$("#scan-log").addEventListener("click", async e => {
  const b = e.target.closest("button[data-keep]");
  if (!b) return;
  await api(`/api/lots/${b.dataset.keep}/send`, { json: { qty: 1, deck: null } });
  b.parentElement.querySelector(".alloc").innerHTML = '<span class="muted">kept in collection (override)</span>'; b.remove();
});

// ---------------------------------------------------------------- decks
let currentDeck = null, deckCache = [];
const dv = { status: "", q: "", sort: "type", group: "type" };
const TYPES = ["Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land"];
const primaryType = t => TYPES.find(k => (t || "").includes(k)) || "Other";
const COLORS = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green" };
const colorName = c => !c ? "Colorless" : c.length > 1 ? "Multicolor" : COLORS[c];

async function loadDecks() {
  deckCache = await api("/api/decks");
  fillDeckSelect();
  $("#deck-list").innerHTML = deckCache.map(d => {
    const w = Math.max(d.wanted, 1);
    return `<div class="deck-item ${d.slug === currentDeck ? "on" : ""}" data-slug="${d.slug}">
      <b>${esc(d.name)}</b> <span class="badge ${d.status === "building" ? "wip" : "built"}">${d.status === "building" ? "IN PROGRESS" : "BUILT"}</span>
      <small><b style="color:var(--text)">${d.progress}</b> · ${d.free} to move · ${d.missing} to buy${d.missing ? ` (${money(d.missing_cost)})` : ""}</small>
      <div class="bar"><i class="a" style="width:${d.in_deck / w * 100}%"></i><i class="f" style="width:${d.free / w * 100}%"></i><i class="m" style="width:${d.missing / w * 100}%"></i></div></div>`;
  }).join("") || '<p class="muted">No decks yet.</p>';
  $$(".deck-item").forEach(e => e.onclick = () => showDeck(e.dataset.slug));
}
function fillDeckSelect() {
  const sel = $("#sel-deck"), cur = sel.value;
  sel.innerHTML = '<option value="">Send to deck…</option>' + deckCache.map(d => `<option value="${d.slug}">${esc(d.name)}</option>`).join("");
  sel.value = cur;
}

async function showDeck(slug) {
  if (slug !== currentDeck) Object.assign(dv, { status: "", q: "" });
  currentDeck = slug; loadDecks();
  const q = [dv.status && `status:${dv.status}`, dv.q, dv.sort && `sort:${dv.sort}`].filter(Boolean).join(" ");
  let r;
  try { r = await api(`/api/decks/${slug}?q=${encodeURIComponent(q)}`); } catch { return; }
  const t = r.totals, d = r.deck, building = d.status === "building";
  const groups = {};
  for (const e of r.entries) {
    const k = { type: primaryType(e.type_line), cmc: e.type_line?.includes("Land") ? "Lands" : `Mana value ${Math.round(e.cmc || 0)}`,
                color: colorName(e.colors), board: e.board, status: { owned: "Have", move: "To move", buy: "To buy" }[e.status], none: "" }[dv.group];
    (groups[k] = groups[k] || []).push(e);
  }
  const order = dv.group === "type" ? [...TYPES, "Other"] : Object.keys(groups);
  const row = e => {
    const st = { owned: '<span class="st-ok">✓ have</span>', move: '<span class="st-pull">move</span>', buy: '<span class="st-miss">buy</span>' }[e.status];
    const acts = [];
    for (const w of e.where.slice(0, 3))
      acts.push(`<button class="small" data-act="in" data-lot="${w.lot_id}" data-qty="${Math.min(w.qty, e.to_move)}" data-board="${e.board}" title="${esc(w.tags || "untagged")}">Move in ${w.set.toUpperCase()}${w.tags ? " · " + esc(w.tags) : ""}</button>`);
    for (const l of e.in_deck_lots.slice(0, 2))
      acts.push(`<button class="small ghost" data-act="out" data-lot="${l.lot_id}" data-qty="${l.qty}" title="Send back to collection">↩ ${l.set.toUpperCase()}</button>`);
    return `<tr><td>${st}</td><td class="num">${e.have}/${e.wanted}</td>
      <td>${esc(e.name)} ${e.finish ? finishBadge(e.finish) : ""} ${e.board !== "main" && dv.group !== "board" ? `<span class="muted">(${e.board})</span>` : ""}</td>
      <td class="mana">${esc(e.mana_cost || "")}</td><td class="muted">${esc(e.type_line || "")}</td>
      <td class="num">${e.price != null ? money(e.price) : ""}</td>
      <td class="muted">${e.to_buy ? `buy ${e.to_buy}${e.cheapest ? ` · ${e.cheapest.set_code.toUpperCase()}` : ""}` : ""}
        ${e.in_other_decks.length ? `also in ${e.in_other_decks.map(tagChip).join(" ")}` : ""}</td>
      <td class="act">${acts.join("")}</td></tr>`;
  };
  const filtered = dv.status || dv.q;
  const shown = filtered && r.shown ? `<span class="muted">${r.entries.length} entries · ${r.shown.wanted} cards match</span>` : "";
  const cols = '<colgroup><col style="width:58px"><col style="width:40px"><col style="width:235px"><col style="width:105px"><col><col style="width:62px"><col style="width:140px"><col style="width:190px"></colgroup>';
  $("#deck-view").innerHTML = `
    <div class="row" style="justify-content:space-between">
      <h2 style="margin:0">${esc(d.name)} <span class="muted" style="font-size:14px">${esc(d.format || "")}</span></h2>
      <div class="row">
        <div class="seg" id="dk-state"><button data-s="building" class="${building ? "on" : ""}">In progress</button><button data-s="built" class="${building ? "" : "on"}">Built</button></div>
        <label class="muted" title="Lower fills first when several in-progress decks need the same card">priority <input id="dk-prio" type="number" value="${d.priority}" style="width:4.5em"></label>
      </div></div>
    <div class="deck-head">
      <div class="progress-big">${t.in_deck}<small>/${t.wanted}</small></div>
      <div class="stat"><b class="st-pull">${t.free}</b><span>to move from collection</span></div>
      <div class="stat"><b class="st-miss">${t.missing}</b><span>to buy · ~${money(t.missing_cost)}</span></div>
      <div class="stat"><b>${t.wanted - t.in_deck}</b><span>left</span></div>
    </div>
    ${building ? '<p class="hint">In progress: when you scan, add or import a card this deck still needs, it\'s tagged straight into the deck. Cards already in your collection are never grabbed automatically — use <b>Move in</b>.</p>' : ""}
    <div class="row">
      <div class="seg" id="dk-status">${[["", "All"], ["owned", "Have"], ["need", "Need"], ["move", "To move"], ["buy", "To buy"]].map(([v, l]) =>
        `<button data-v="${v}" class="${dv.status === v ? "on" : ""}">${l}</button>`).join("")}</div>
      <input id="dk-q" placeholder="filter: t:creature cmc<=3 c:g price>2" value="${esc(dv.q)}" style="flex:1;min-width:180px">
      <label>sort <select id="dk-sort">${["type", "cmc", "name", "price", "color", "rarity", "status"].map(k => `<option ${dv.sort === k ? "selected" : ""}>${k}</option>`).join("")}</select></label>
      <label>group <select id="dk-group">${["type", "cmc", "color", "status", "board", "none"].map(k => `<option ${dv.group === k ? "selected" : ""}>${k}</option>`).join("")}</select></label>
      ${shown}
    </div>
    <div class="row"><button id="dk-pull">Move in all free copies</button><button class="ghost" id="dk-pull-bling">…using priciest printings</button>
      <button class="ghost" id="dk-missing">To-buy list → TCGplayer</button>
      <select id="dk-fmt"><option value="arena">Arena</option><option value="mtgo">MTGO / plain</option><option value="moxfield">Moxfield</option><option value="tcgplayer">TCGplayer mass entry</option></select>
      <select id="dk-src"><option value="list">decklist</option><option value="owned">copies in deck</option></select>
      <button class="ghost" id="dk-export">Export</button>
      <button class="ghost" id="dk-view-cards">Show in collection</button><button class="ghost" id="dk-release">Release all</button><button class="ghost" id="dk-del">Delete</button></div>
    ${order.filter(k => groups[k]).map(k => `${k ? `<div class="group-h">${esc(k)} · ${groups[k].reduce((a, e) => a + e.wanted, 0)}</div>` : ""}
      <table class="entries">${cols}${groups[k].map(row).join("")}</table>`).join("") || '<p class="muted">No entries match.</p>'}`;

  $$("#dk-status button").forEach(b => b.onclick = () => { dv.status = b.dataset.v; showDeck(slug); });
  $$("#dk-state button").forEach(b => b.onclick = async () => { await api(`/api/decks/${slug}`, { method: "PATCH", json: { status: b.dataset.s } }); showDeck(slug); });
  $("#dk-prio").onchange = async e => { await api(`/api/decks/${slug}`, { method: "PATCH", json: { priority: +e.target.value } }); loadDecks(); };
  $("#dk-q").onkeydown = e => { if (e.key === "Enter") { dv.q = e.target.value; showDeck(slug); } };
  $("#dk-sort").onchange = e => { dv.sort = e.target.value; showDeck(slug); };
  $("#dk-group").onchange = e => { dv.group = e.target.value; showDeck(slug); };
  $("#deck-view").querySelectorAll("button[data-act]").forEach(b => b.onclick = async () => {
    const body = b.dataset.act === "in" ? { qty: +b.dataset.qty, deck: slug, board: b.dataset.board } : { qty: +b.dataset.qty, deck: null };
    await api(`/api/lots/${b.dataset.lot}/send`, { json: body });
    toast(b.dataset.act === "in" ? "Moved into deck" : "Sent back to collection"); showDeck(slug);
  });
  $("#dk-pull").onclick = () => pullDeck("cheapest");
  $("#dk-pull-bling").onclick = () => pullDeck("priciest");
  $("#dk-release").onclick = async () => { if (confirm("Untag every card from this deck?")) { await api(`/api/decks/${slug}/release`, { method: "POST" }); showDeck(slug); } };
  $("#dk-export").onclick = async () => exportModal(await api(`/api/decks/${slug}/export?fmt=${$("#dk-fmt").value}&source=${$("#dk-src").value}`));
  $("#dk-missing").onclick = async () => exportModal(await api(`/api/decks/${slug}/missing`), '<p class="hint">Paste into tcgplayer.com/massentry</p>');
  $("#dk-view-cards").onclick = () => { $("#q").value = "deck:" + slug; $$("nav button")[0].click(); };
  $("#dk-del").onclick = async () => { if (confirm("Delete deck (cards stay in your collection, untagged)?")) { await api(`/api/decks/${slug}`, { method: "DELETE" }); currentDeck = null; $("#deck-view").innerHTML = ""; loadDecks(); } };
}
async function pullDeck(prefer) {
  const r = await api(`/api/decks/${currentDeck}/pull?prefer=${prefer}`, { method: "POST" });
  toast(`Moved ${r.pulled.reduce((a, s) => a + s.qty, 0)} cards into the deck`); showDeck(currentDeck);
}
function exportModal(text, extra = "") {
  modal(`${extra}<textarea id="exp-text" rows="20">${esc(text)}</textarea><div class="row"><button id="exp-copy">Copy</button></div>`);
  $("#exp-copy").onclick = () => { navigator.clipboard.writeText($("#exp-text").value); toast("Copied"); };
}
$("#dk-import").onclick = async () => {
  const r = await api("/api/decks", { json: { name: $("#dk-name").value || "Untitled", text: $("#dk-text").value, format: $("#dk-format").value,
                                               status: $("#dk-building").checked ? "building" : "built" } });
  if (r.unresolved.length) toast("Unresolved: " + r.unresolved.join(", "), true); else toast("Imported");
  $("#dk-text").value = ""; showDeck(r.slug);
};
function allocNote(allocs) {
  return allocs.map(a => `→ ${a.qty}× into <b>${esc(a.deck_name)}</b>${a.board !== "main" ? ` (${a.board})` : ""} <span class="muted">${a.deck_progress || ""}</span>`).join(", ");
}

// ---------------------------------------------------------------- import / export
$("#io-go").onclick = async () => {
  const f = $("#io-file").files[0]; if (!f) return toast("Pick a CSV", true);
  const fd = new FormData(); fd.append("file", f); fd.append("tags", $("#io-tags").value); fd.append("dry_run", $("#io-dry").checked);
  const r = await api("/api/import/tcgplayer", { method: "POST", body: fd });
  const review = r.added.filter(a => a.matched_by.includes("review"));
  $("#io-out").innerHTML = `<p>${$("#io-dry").checked ? "Dry run: would import" : "Imported"} <b>${r.cards}</b> cards (${r.added.length} rows). ${r.failed.length} failed, ${review.length} need a printing check.</p>
    ${r.failed.length ? `<p class="st-miss">Failed: ${r.failed.map(x => `row ${x.row} ${esc(x.name)}`).join(", ")}</p>` : ""}
    ${review.length ? `<p class="st-pull">Guessed printing: ${review.map(x => `row ${x.row} ${esc(x.name)}`).join(", ")}</p>` : ""}`;
};
const exLinks = () => { const q = encodeURIComponent($("#ex-q").value); $("#ex-tcg").href = "/api/export/tcgplayer?q=" + q; $("#ex-json").href = "/api/export/json?q=" + q; };
$("#ex-q").addEventListener("input", exLinks); exLinks();

// ---------------------------------------------------------------- prices
async function loadPrices() {
  const v = await api("/api/prices/value");
  $("#pr-value").innerHTML = `<div class="tile"><span>TCGplayer market</span><b>${money(v.tcgplayer_market)}</b></div>
    <div class="tile"><span>Card Kingdom retail</span><b>${money(v.cardkingdom_retail)}</b></div>
    <div class="tile"><span>Card Kingdom buylist (cash)</span><b>${money(v.cardkingdom_buylist)}</b></div>
    <div class="tile"><span>Cards without a price</span><b>${v.unpriced_cards}</b></div>`;
  const m = await api("/api/prices/movers?days=" + $("#pr-days").value);
  $("#pr-movers").innerHTML = m.length ? `<table><tr><th class="num">Δ total</th><th>card</th><th>finish</th><th class="num">was</th><th class="num">now</th></tr>${m.map(r =>
    `<tr><td class="num ${r.delta_total > 0 ? "up" : "down"}">${r.delta_total > 0 ? "+" : ""}${r.delta_total.toFixed(2)}</td><td>${r.qty}× ${esc(r.name)} <span class="muted">${r.set_code.toUpperCase()} #${esc(r.collector_number)}</span></td>
      <td>${r.finish}</td><td class="num">${money(r.was)}</td><td class="num">${money(r.now_price)}</td></tr>`).join("")}</table>`
    : '<p class="muted">No price history yet — refresh daily to build it.</p>';
}
$("#pr-days").onchange = loadPrices;
$("#pr-refresh").onclick = async () => {
  $("#pr-status").textContent = "refreshing…";
  const r = await api("/api/prices/refresh", { method: "POST" });
  $("#pr-status").textContent = Object.entries(r).map(([k, v]) => `${k}: ${v}`).join(" · ");
  loadPrices();
};

search();
api('/api/decks').then(d => { deckCache = d; fillDeckSelect(); });
