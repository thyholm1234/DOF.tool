// Fælles hjælpere: API, tilstand, DOM, formattering, ikoner, modaler og toasts.

export const state = { user: null, afdelinger: [], kommuner: [], unread: 0 };
/** Har den indloggede adgang til et admin-modul (rolle eller individuel rettighed, se /api/me). */
export const can = (module) => !!state.user?.modules?.includes(module);
// Admin-sidens faner i rækkefølge: [modul, fanetekst]
export const ADMIN_TABS = [["drift", "Drift"], ["data", "Data"], ["vejr", "Vejr"], ["aktivitet", "Aktivitet"], ["support", "Support"],
  ["moderation", "Moderation"], ["ture", "Ture"], ["kategorier", "Kategorier"], ["brugere", "Brugere"], ["arter", "Arter"], ["indhold", "Nyheder"]];
/** Admin-menupunktet: "Moderation" for dem med kun moderatoropgaver, ellers "Admin". null = ingen faner. */
export function adminLink() {
  const tabs = ADMIN_TABS.filter(([m]) => can(m)).map(([m]) => m);
  if (!tabs.length) return null;
  const mod = tabs.every(m => ["moderation", "ture", "kategorier"].includes(m));
  return { href: "#/admin", label: mod ? "Moderation" : "Admin", long: mod ? "Moderation" : "Administration" };
}

// append/replaceChildren ignorerer null/undefined/false, så betingede børn kan skrives som `x ? el : null`
for (const name of ["append", "replaceChildren", "prepend"]) {
  const orig = Element.prototype[name];
  Element.prototype[name] = function (...nodes) {
    return orig.apply(this, nodes.flat(Infinity).filter(n => n !== null && n !== undefined && n !== false && n !== ""));
  };
}

export async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, opts);
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = (data && data.detail) ? (typeof data.detail === "string" ? data.detail : data.detail.map?.(d => d.msg).join(", ")) : `Fejl ${res.status}`;
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return data;
}

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") for (const [sk, sv] of Object.entries(v)) { if (sk.startsWith("--")) el.style.setProperty(sk, sv); else el.style[sk] = sv; }
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const $ = (sel, root = document) => root.querySelector(sel);
export const nf = new Intl.NumberFormat("da-DK");
export const fmtN = (n) => n === null || n === undefined ? "–" : nf.format(n);
const MONTHS = ["jan.", "feb.", "mar.", "apr.", "maj", "jun.", "jul.", "aug.", "sep.", "okt.", "nov.", "dec."];
export function fmtDate(iso, withYear = true) {
  if (!iso) return "–";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return `${d.getDate()}. ${MONTHS[d.getMonth()]}${withYear ? " " + d.getFullYear() : ""}`;
}
export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit" });
}
export function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "lige nu";
  if (s < 3600) return `${Math.floor(s / 60)} min. siden`;
  if (s < 86400) return `${Math.floor(s / 3600)} t. siden`;
  return fmtDate(iso, false);
}
export const todayISO = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
export function doyToDate(doy, year = 2025) { const d = new Date(year, 0, 1); d.setDate(Math.round(doy)); return d; }
export function doyLabel(doy) { if (doy == null) return "–"; const d = doyToDate(doy); return `${d.getDate()}. ${MONTHS[d.getMonth()]}`; }

export const KAT = { SU: "SU", SUB: "SUB", bemaerk: "Bemærk", local: "Lokalt hit", alm: "Alm." };
export const tag = (k, text) => h("span", { class: `tag ${k}` }, text ?? KAT[k] ?? k);

export function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}`, onclick: () => t.remove() }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 7000 : 4000);
}

export function modal(title, content, { wide = false, onClose } = {}) {
  const close = () => { bg.remove(); document.removeEventListener("keydown", key); onClose?.(); };
  const key = (e) => { if (e.key === "Escape") close(); };
  const bg = h("div", { class: "modal-bg", onclick: (e) => { if (e.target === bg) close(); } },
    h("div", { class: `modal ${wide ? "wide" : ""}`, role: "dialog", "aria-modal": "true" },
      h("div", { class: "modal-head" }, h("h2", {}, title), h("button", { class: "icon-btn", onclick: close, "aria-label": "Luk", html: icon("x") })),
      content));
  document.body.append(bg);
  document.addEventListener("keydown", key);
  return { close, el: bg };
}

export function loading(el, rows = 4) {
  el.replaceChildren(...Array.from({ length: rows }, () => h("div", { class: "skeleton", style: { marginBottom: "8px" } })));
}

/** Indlæsningsblok: skeleton-rækker og en besked med spinner. Beskeden vises først efter lidt ventetid, så hurtige
 *  opslag ikke blinker – tager det længere (tunge beregninger på serveren), ved brugeren at siden arbejder. */
export function busyBlock(text, rows = 4) {
  return h("div", { class: "stack", "aria-busy": "true", "aria-live": "polite" },
    h("div", { class: "loadnote" }, h("span", { class: "spinner" }), h("span", {}, text)),
    ...Array.from({ length: rows }, () => h("div", { class: "skeleton", style: { height: "44px" } })));
}

export function empty(text, sub) {
  return h("div", { class: "empty" }, h("div", { html: icon("bino") }), h("p", {}, text), sub ? h("small", {}, sub) : null);
}

export function errorBox(err) {
  return h("div", { class: "notice err" }, err.message || String(err));
}

export function seg(options, value, onChange) {
  const wrap = h("div", { class: "seg", role: "tablist" });
  for (const [v, label] of options) {
    wrap.append(h("button", { class: v === value ? "on" : "", type: "button", onclick: () => {
      wrap.querySelectorAll("button").forEach(b => b.classList.remove("on"));
      wrap.children[options.findIndex(o => o[0] === v)].classList.add("on");
      onChange(v);
    } }, label));
  }
  return wrap;
}

export function requireLogin(view) {
  if (state.user) return false;
  view.replaceChildren(h("div", { class: "card", style: { maxWidth: "520px", margin: "8vh auto", textAlign: "center" } },
    h("div", { html: icon("lock"), style: { fontSize: "34px", lineHeight: 1, margin: "0 auto 8px", color: "var(--brand)" } }),
    h("h2", {}, "Log ind med din DOFbasen-obserkode"),
    h("p", { class: "muted" }, "Denne side bruger dine egne data, områder eller notifikationer."),
    h("a", { class: "btn primary", href: "#/login?next=" + encodeURIComponent(location.hash) }, "Log ind")));
  return true;
}

export function params() {
  const q = location.hash.split("?")[1] || "";
  return Object.fromEntries(new URLSearchParams(q));
}

export function setParams(obj) {
  const [path] = location.hash.split("?");
  const q = new URLSearchParams({ ...params(), ...obj });
  for (const [k, v] of [...q]) if (v === "" || v === "null" || v === "undefined") q.delete(k);
  history.replaceState(null, "", `${path}?${q}`);
}

export async function loadRef() {
  if (state.afdelinger.length) return;
  const [a, k] = await Promise.all([api("/api/afdelinger"), api("/api/kommuner")]);
  state.afdelinger = a; state.kommuner = k;
}

export function store(key, value) {
  try { if (value === undefined) return JSON.parse(localStorage.getItem(key) || "null"); localStorage.setItem(key, JSON.stringify(value)); } catch { return null; }
}

// Ikoner (stroke, 24x24)
const P = {
  now: '<path d="M12 8v4l3 2"/><circle cx="12" cy="12" r="9"/>',
  map: '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z"/><path d="M9 4v14M15 6v14"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01" stroke-width="3"/>',
  trend: '<path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/>',
  learn: '<path d="M2 9l10-5 10 5-10 5z"/><path d="M6 11v5c3 2.5 9 2.5 12 0v-5"/>',
  bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  bino: '<circle cx="6.5" cy="15.5" r="3.5"/><circle cx="17.5" cy="15.5" r="3.5"/><path d="M10 15h4M5 12l2-7h3l1 7M19 12l-2-7h-3l-1 7"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  pin: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  wind: '<path d="M3 8h11a3 3 0 1 0-3-3M3 12h16a3 3 0 1 1-3 3M3 16h8"/>',
  logout: '<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  edit: '<path d="M4 20h4L20 8l-4-4L4 16z"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  sound: '<path d="M4 9.5h3.5L12 5v14l-4.5-4.5H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
  check: '<path d="M4 12l5 5L20 6"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c1-3.5 3.5-5 6.5-5s5.5 1.5 6.5 5"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14.5c2.6 0 4.6 1.4 5.5 4.5"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
  phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h5v-6h4v6h5V9.5"/>',
  bird: '<path d="M3 14c3.5-1 6-3.6 7.6-7.2.4-.9 1.6-1 2.1-.1l1 1.8 4.6-1.3c.6-.2 1 .5.6 1l-3.2 3.4c-1.4 4.3-5 6.9-9.6 6.9H3.6c-.9 0-1.2-1.2-.6-1.5z"/>',
  prev: '<path d="M15 5l-7 7 7 7"/>',
  next: '<path d="M9 5l7 7-7 7"/>',
  thumb: '<path d="M7 10.5V20H4v-9.5h3z"/><path d="M7 10.5 11 3.6c1.3 0 2.3 1.1 2 2.4l-.8 3.5h5.6c1.3 0 2.3 1.2 2 2.5l-1.3 6.2c-.2.9-1 1.6-2 1.6H7"/>',
};
export function icon(name) {
  return `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ""}</svg>`;
}

export function windArrow(deg, size = 18) {
  // pil der peger i den retning vinden blæser HEN
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" style="transform: rotate(${(deg + 180) % 360}deg)" aria-hidden="true"><path d="M12 3v15M6 12l6 7 6-7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}
export function compass(deg) {
  const d = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ", "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"];
  return d[Math.round(deg / 22.5) % 16];
}

// Artssøgning i browseren: ord i det danske navn matches fra ordstart (alle ord skal passe; fra 4 tegn også
// inde i sammensatte navne),
// latinske navne kun fra starten af slægts- eller artsnavnet. Danske navnetræf sorteres først.
const fold = (s) => (s || "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
export function searchSpecies(list, query, n = 8) {
  const q = fold(query.trim());
  if (q.length < 2) return [];
  const words = q.split(/\s+/);
  const scored = [];
  for (const s of list) {
    const parts = fold(s.navn).split(/[\s,()/-]+/).filter(Boolean);
    const nameHit = words.every(w => parts.some(p => p.startsWith(w)));
    // Sammensatte navne (Skovsneppe, Dobbeltbekkasin): fra 4 tegn må ordet også stå inde i navnet
    const innerHit = !nameHit && words.every(w => w.length >= 4 && parts.some(p => p.includes(w)));
    const latinHit = !nameHit && !innerHit && s.latin && fold(s.latin).split(/\s+/).some(p => p.startsWith(q));
    if (!nameHit && !innerHit && !latinHit) continue;
    const score = (fold(s.navn).startsWith(q) ? 0 : nameHit ? 1 : innerHit ? 2 : 3) + (s.art_type === "art" ? 0 : 1.5);
    scored.push([score, s]);
  }
  return scored.sort((a, b) => a[0] - b[0] || a[1].navn.localeCompare(b[1].navn, "da")).slice(0, n).map(x => x[1]);
}

// Store billeder skaleres i browseren før upload: længste side højst 2048 px, JPEG 60 %.
// GIF (animation) og små filer sendes uændret; lyd røres ikke.
export async function compressImage(file, { max = 2048, quality = 0.6, minBytes = 400_000 } = {}) {
  if (!file.type?.startsWith("image/") || file.type === "image/gif") return file;
  let bmp;
  try { bmp = await createImageBitmap(file, { imageOrientation: "from-image" }); } catch { return file; }
  const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
  if (scale === 1 && file.size <= minBytes && file.type === "image/jpeg") { bmp.close?.(); return file; }
  const w = Math.round(bmp.width * scale), h2 = Math.round(bmp.height * scale);
  const canvas = Object.assign(document.createElement("canvas"), { width: w, height: h2 });
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, w, h2);  // PNG med gennemsigtighed → hvid baggrund i JPEG
  ctx.drawImage(bmp, 0, 0, w, h2);
  bmp.close?.();
  const blob = await new Promise(res => canvas.toBlob(res, "image/jpeg", quality));
  if (!blob || (blob.size >= file.size && scale === 1)) return file;
  return new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg", lastModified: Date.now() });
}

// Sidevisning til aktivitetsloggen (fejl ignoreres – må aldrig forstyrre brugeren)
export function trackView(path) {
  fetch("/api/activity", { method: "POST", credentials: "same-origin", keepalive: true,
    headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" }, body: JSON.stringify({ path }) }).catch(() => {});
}

/** Sorteringsbjælke: knapper for felter + retning. st = {key, dir: 1|-1}; onChange kaldes ved ændring. */
export function sortBar(options, st, onChange, { label = "Sortér" } = {}) {
  const dirBtn = h("button", { type: "button", class: "btn sm", "aria-label": "Skift retning" });
  const drawDir = () => { dirBtn.textContent = st.dir === 1 ? "↑ Stigende" : "↓ Faldende"; };
  dirBtn.onclick = () => { st.dir = -st.dir; drawDir(); onChange(st); };
  drawDir();
  return h("div", { class: "row sort-bar", style: { gap: "8px" } }, h("small", { class: "muted" }, label),
    seg(options, st.key, (v) => { st.key = v; onChange(st); }), dirBtn);
}
