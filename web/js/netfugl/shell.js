// Netfugl-modulet: fælles skal (navigation med undermenuer, bannere, sidefod), let markup og hjælpere.
// Links til netfugl.dk i det kopierede indhold vises som modulets egne sider; links til dofbasen.dk som DOFbasen-modulet.
import { api, h, esc, toast } from "../core.js";
import { mapHref as dofbMap } from "../dofbasen/shell.js";

export const BASE = "#/netfugl";
export const ctx = { data: null };

export async function loadCtx(force = false) {
  if (!ctx.data || force) ctx.data = await api("/api/netfugl/ctx");
  return ctx.data;
}
export const on = (f) => !!ctx.data?.features?.[f];
export const can = (p) => !!ctx.data?.perms?.includes(p);
export const canEdit = (p) => can(p) && on("netfugl_edit");

export const MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];
const MS = ["jan.", "feb.", "mar.", "apr.", "maj", "jun.", "jul.", "aug.", "sep.", "okt.", "nov.", "dec."];
/** "18. okt. 2026 kl. 10:02" som på netfugl.dk */
export function dkDate(iso, withTime = false) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  const s = `${d.getDate()}. ${MS[d.getMonth()]} ${d.getFullYear()}`;
  return withTime && iso.length > 10 ? `${s} kl. ${d.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit" })}` : s;
}

// ---------------------------------------------------------------- markup

const NF_ROUTES = [
  [/^\/?$/, () => ""], [/^\/artikler\/(\d+)/, (m) => `/artikler/${m[1]}`], [/^\/artikler/, () => "/artikler"], [/^\/rss/, () => "/rss"],
  [/^\/vejret/, () => "/vejret"], [/^\/ranking\/regler/, () => "/kryds/regler"], [/^\/ranking\/alle/, () => "/kryds/alle"],
  [/^\/ranking\/?$/, () => "/kryds"], [/^\/ranking/, () => "/kryds/dk/ranking"], [/^\/artslister\/dk/, () => "/arter/dk"],
  [/^\/artslister\/vp/, () => "/arter/vp"], [/^\/arter/, () => "/arter"], [/^\/fotos/, () => "/fotos"], [/^\/om/, () => "/side/om"],
  [/^\/faq/, () => "/side/faq"], [/^\/ordforklaringer/, () => "/side/ordforklaringer"], [/^\/stoet/, () => "/side/stoet"],
  [/^\/bruger\/alle/, () => "/brugere"], [/^\/bruger\/(opret|login)/, () => "/side/om"],
];
export function mapHref(href) {
  let u;
  try { u = new URL(href, "https://www.netfugl.dk/"); } catch { return { href: "#", ext: false }; }
  if (/(^|\.)netfugl\.dk$/.test(u.hostname)) {
    for (const [re, fn] of NF_ROUTES) { const m = u.pathname.match(re); if (m) return { href: BASE + fn(m), ext: false }; }
  }
  if (/(^|\.)dofbasen\.dk$/.test(u.hostname) && ctx.data?.features?.netfugl_dofbasen) return dofbMap(href);
  if (!/^(https?|mailto):$/.test(u.protocol)) return { href: "#", ext: false };
  return { href: u.href, ext: true };
}

function inline(text) {
  const links = [];
  let s = esc(text).replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, url) => {
    const m = mapHref(url.replaceAll("&amp;", "&"));
    links.push(`<a href="${esc(m.href)}"${m.ext ? ' target="_blank" rel="noopener"' : ""}>${t}</a>`);
    return `\u0000${links.length - 1}\u0000`;
  });
  s = s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])_(\S(?:.*?\S)?)_(?=[\s).,:;!?]|$)/g, "$1<i>$2</i>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => links[+i]);
}

/** Let markup: afsnit (tom linje), • punkter, | tabeller |, [tekst](adresse), **fed**, _kursiv_. */
export function md(text) {
  const out = [];
  for (const block of String(text ?? "").split(/\n{2,}/)) {
    const lines = block.split("\n").filter(l => l.trim() !== "");
    if (!lines.length) continue;
    if (lines.every(l => l.trim().startsWith("|"))) {
      out.push("<table>" + lines.map(l => "<tr>" + l.trim().replace(/^\||\|$/g, "").split("|").map(c => `<td>${inline(c.trim())}</td>`).join("") + "</tr>").join("") + "</table>");
    } else if (lines.every(l => l.trim().startsWith("• "))) {
      out.push("<ul>" + lines.map(l => `<li>${inline(l.trim().slice(2))}</li>`).join("") + "</ul>");
    } else {
      out.push("<p>" + lines.map(l => l.trim().startsWith("• ") ? "• " + inline(l.trim().slice(2)) : inline(l)).join("<br>") + "</p>");
    }
  }
  return h("div", { class: "nf-md", html: out.join("") });
}

// ---------------------------------------------------------------- byggeklodser

export function title(text, { edit, black } = {}) {
  return h("div", { class: `nf-title ${black ? "black" : ""}` }, text, edit ? h("button", { type: "button", class: "btn btn-edit", onclick: edit }, "Redigér") : null);
}
export const link = (path, text, attrs = {}) => h("a", { href: BASE + path, ...attrs }, text);
export const notice = (text, kind = "") => h("div", { class: `nf-notice ${kind}` }, text);
export const loadingBox = (text = "Henter …") => h("div", { class: "muted", style: { padding: "12px 0" } }, text);

export function tabs(list, value, onPick) {
  const bar = h("div", { class: "nf-tabs" });
  const draw = (v) => bar.replaceChildren(...list.map(([k, t]) => h("a", { href: "javascript:void 0", class: k === v ? "on" : "", onclick: (e) => { e.preventDefault(); draw(k); onPick(k); } }, t)));
  draw(value);
  return bar;
}

export function pager(page, pages, go) {
  if (pages <= 1) return null;
  const items = [];
  const from = Math.max(1, page - 2), to = Math.min(pages, from + 4);
  if (page > 1) items.push(h("a", { href: "javascript:void 0", onclick: () => go(page - 1) }, "«"));
  for (let p = from; p <= to; p++) items.push(p === page ? h("span", { class: "on" }, p) : h("a", { href: "javascript:void 0", onclick: () => go(p) }, p));
  if (page < pages) items.push(h("a", { href: "javascript:void 0", onclick: () => go(page + 1) }, "»"));
  return h("div", { class: "nf-pager" }, ...items);
}

export function nfModal(heading, body, { wide = false } = {}) {
  const close = () => { bg.remove(); document.removeEventListener("keydown", key); removeEventListener("hashchange", close); };
  const key = (e) => { if (e.key === "Escape") close(); };
  const bg = h("div", { class: "nf-modal-bg", onclick: (e) => { if (e.target === bg) close(); } },
    h("div", { class: `nf-modal ${wide ? "wide" : ""}`, role: "dialog", "aria-modal": "true" },
      h("div", { class: "nf-modal-head" }, h("h3", {}, heading), h("button", { type: "button", "aria-label": "Luk", onclick: close }, "×")),
      h("div", { class: "nf-modal-body nfu", style: { minHeight: 0 } }, body)));
  document.body.append(bg);
  document.addEventListener("keydown", key);
  addEventListener("hashchange", close);
  return { close };
}

export function autosuggest({ url, placeholder = "", width = "260px", label = (r) => r.navn, initial = "", onPick }) {
  const input = h("input", { type: "text", placeholder, value: initial, style: { width }, autocomplete: "off" });
  const list = h("div", { class: "nf-ac-list", hidden: true });
  let items = [], timer = null, seq = 0, active = -1;
  const pick = (r) => { input.value = label(r); list.hidden = true; onPick(r); };
  const draw = () => { list.replaceChildren(...items.map((r, i) => h("div", { class: i === active ? "on" : "", onmousedown: (e) => { e.preventDefault(); pick(r); } }, label(r)))); list.hidden = !items.length; };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { items = []; draw(); if (!q) onPick(null); return; }
    timer = setTimeout(async () => { const my = ++seq; try { const r = await api(url + encodeURIComponent(q)); if (my === seq) { items = r.rows || r; active = -1; draw(); } } catch { /* ignore */ } }, 180);
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); draw(); e.preventDefault(); }
    else if (e.key === "Enter" && items[Math.max(0, active)]) { pick(items[Math.max(0, active)]); e.preventDefault(); }
  });
  input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 150));
  return h("span", { class: "nf-ac" }, input, list);
}

export function markupHelp() {
  return h("details", { class: "muted small", style: { margin: "4px 0 8px" } }, h("summary", {}, "Sådan formaterer du teksten"),
    h("div", { class: "nf-code" }, "Tom linje = nyt afsnit\n• Punkt (start linjen med • og mellemrum)\n**fed**   _kursiv_\n[linktekst](https://adresse) – links til netfugl.dk og dofbasen.dk vises som modulernes egne sider\n| celle | celle |  (linjer der starter med | bliver en tabel)"));
}

/** Redigér en tekstside (én tekst i let markup). */
export function editPage(page, onSaved) {
  const t = h("input", { type: "text", class: "full", value: page.title, style: { width: "100%" } });
  const b = h("textarea", { rows: 22 }, page.body);
  const m = nfModal(`Redigér: ${page.title}`, h("div", {},
    h("label", { class: "nf-field" }, h("span", {}, "Titel"), t), markupHelp(), h("label", { class: "nf-field" }, h("span", {}, "Tekst"), b),
    h("div", { class: "nf-row" }, h("span", { style: { flex: 1 } }), h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Annullér"),
      h("button", { type: "button", class: "btn primary", onclick: async () => {
        try { await api(`/api/netfugl/sider/${page.slug}`, { method: "PUT", body: { title: t.value.trim() || page.title, body: b.value } }); toast("Siden er gemt"); m.close(); onSaved?.(); }
        catch (err) { toast(err.message, "err"); }
      } }, "Gem"))), { wide: true });
}

// ---------------------------------------------------------------- skal

const LOGO = `<svg width="44" height="50" viewBox="0 0 44 50" aria-hidden="true"><path d="M4 14c6-4 13-5 19-2 4-6 10-9 17-8-5 2-8 5-10 9 3 2 5 6 5 10-4-3-8-4-12-3-2 9-6 18-12 26 1-8 2-15 1-21-3 0-6-2-8-11z" fill="#555"/><path d="M23 12c4 1 7 4 9 8" stroke="#a70e00" stroke-width="2" fill="none"/><circle cx="30" cy="9" r="1.3" fill="#fff"/></svg>`;
const BANNER = `<svg viewBox="0 0 300 150" aria-hidden="true"><g fill="#3d4a33" opacity=".75"><path d="M40 150c10-40 40-70 90-80 30-6 55 2 75-10 14-9 30-10 44-2-12 0-20 6-26 14 8 6 12 16 10 27-10-10-24-14-38-12-30 4-50 30-70 63z"/><circle cx="236" cy="67" r="3" fill="#f2f2f2"/></g><path d="M0 150 40 70M30 150 60 40M260 150 230 30" stroke="#5b6b44" stroke-width="3" opacity=".5"/></svg>`;

function menuTree() {
  const kryds = (k) => [["Krydsliste", `/kryds/${k}/ranking`], ["Seneste kryds", `/kryds/${k}/seneste`], ["Hvem har set hvad?", `/kryds/${k}/hvem`],
    ["Blockers", `/kryds/${k}/blockers`], ["Blockers statistik", `/kryds/${k}/blockerstat`], ["Årstophits", `/kryds/${k}/tophits`]];
  const y = new Date().getFullYear();
  return [
    ["Nyheder & Artikler", [...(on("netfugl_artikler") ? [["Nyheder & Artikler", "/artikler"]] : []), ...(on("netfugl_rss") ? [["RSS Nyheder", "/rss"]] : []), ["Vejret", "/vejret"]]],
    ...(on("netfugl_kryds") ? [["Krydslister", [["Om krydslister", "/kryds"], ["VP krydslister", null, kryds("vp")], ["Falske VP krydslister", "/kryds/falskvp/ranking"],
      ["DK krydslister", null, kryds("dk")], ["DK årsarter", `/kryds/aar:${y}/ranking`], ["DK månedsarter", `/kryds/md:${new Date().getMonth() + 1}/ranking`],
      ["DK ringmærkningsarter", "/kryds/ring/ranking"], ["DK ædelarter", "/kryds/aedel/ranking"], ["DK ædelårsarter", `/kryds/aedelaar:${y}/ranking`],
      ["DK lokalarter", "/kryds/afd:574/ranking"], ["Alle krydslister", "/kryds/alle"], ["Krydsregler", "/kryds/regler"], ["—"], ["Mine kryds", "/mine-kryds"]]]] : []),
    ["Arter", [["World Bird List", "/arter"], ["DK listen", "/arter/dk"], ["VP listen", "/arter/vp"]]],
    ...(on("netfugl_fotos") ? [["Fotogalleri", "/fotos"]] : []),
    ["Om Netfugl", [["Om Netfugl", "/side/om"], ["FAQ", "/side/faq"], ["Ordforklaringer", "/side/ordforklaringer"], ["Alle brugere", "/brugere"], ["Søg på Netfugl", "/soeg"], ["Støt Netfugl", "/side/stoet"]]],
  ];
}

function item([text, target, sub]) {
  if (text === "—") return h("hr");
  const a = h("a", { href: target ? BASE + target : "javascript:void 0" }, text);
  return sub ? h("div", { class: "nf-sub" }, a, h("div", { class: "nf-drop" }, ...sub.map(item))) : a;
}

function nav() {
  const menu = h("div", { class: "nf-menu" });
  for (const [text, target] of menuTree()) {
    const mi = h("div", { class: `nf-mi ${Array.isArray(target) ? "dd" : ""}` });
    if (Array.isArray(target)) {
      mi.append(h("a", { href: "javascript:void 0", onclick: (e) => { e.preventDefault(); const o = mi.classList.contains("open"); menu.querySelectorAll(".open").forEach(x => x.classList.remove("open")); mi.classList.toggle("open", !o); } }, text),
        h("div", { class: "nf-drop" }, ...target.map(item)));
    } else mi.append(h("a", { href: BASE + target }, text));
    menu.append(mi);
  }
  const me = ctx.data?.me;
  const user = h("div", { class: "nf-mi dd nf-user" },
    h("a", { href: "javascript:void 0", onclick: (e) => { e.preventDefault(); user.classList.toggle("open"); } }, me ? (me.navn || me.obserkode).split(" ")[0] : "Profil"),
    h("div", { class: "nf-drop", style: { left: "auto", right: 0 } },
      me ? link(`/bruger/${me.obserkode}`, "Min profil") : null,
      on("netfugl_kryds") ? link("/mine-kryds", "Mine kryds") : null,
      on("netfugl_fotos") ? link("/fotos/upload", "Upload foto") : null,
      on("netfugl_api") ? link("/api", "API-nøgler") : null,
      ctx.data?.perms?.length ? link("/admin", "Administration") : null,
      h("hr"), h("a", { href: "#/" }, "Tilbage til DOF.tool")));
  menu.append(user);
  const navEl = h("nav", { class: "nf-nav" }, h("div", { class: "wrap" },
    h("a", { class: "nf-logo", href: BASE, html: `${LOGO}<b>net<i>fugl</i>.dk</b>` }),
    h("button", { type: "button", class: "btn nf-burger", onclick: () => navEl.classList.toggle("open") }, "☰ Menu"),
    menu));
  document.addEventListener("click", (e) => { if (!navEl.contains(e.target)) navEl.querySelectorAll(".open").forEach(x => x.classList.remove("open")); });
  return navEl;
}

/** Forsidebanner (rød kant forneden) */
export const banner = () => h("div", { class: "nf-banner", html: BANNER });
/** Overskrift over et svagt banner (som på undersiderne) */
export const headline = (text) => h("div", { class: "nf-headline" }, h("div", { class: "bg" }), h("div", { class: "wrap" }, h("h1", {}, text)));

let cssLoaded = false;
function ensureCss() {
  if (cssLoaded || document.querySelector("link[data-nfu]")) return;
  const path = new URL(import.meta.url).pathname;
  document.head.append(h("link", { rel: "stylesheet", href: path.startsWith("/v/") ? `/v/${path.split("/")[2]}/css/netfugl.css` : "/css/netfugl.css", "data-nfu": "" }));
  cssLoaded = true;
}

export function frame(content) {
  ensureCss();
  document.body.classList.add("nfu-mode");
  return h("div", { class: "nfu" },
    h("div", { class: "nf-strip" }, h("span", { class: "pill" }, "SHOWCASE"),
      h("span", { class: "long" }, "Ny udgave af Netfugl på DOF.tool – integreret med DOFbasen. Tekster og lister er kopieret fra netfugl.dk som eksempel; indholdet tilhører Netfugl/DOF."),
      h("span", { class: "spacer" }), on("netfugl_dofbasen") ? h("a", { href: "#/dofbasen" }, "DOFbasen") : null,
      ctx.data?.perms?.length ? link("/admin", "Admin") : null, h("a", { href: "#/" }, "← DOF.tool")),
    nav(), content,
    h("footer", { class: "nf-footer" }, h("div", { class: "wrap" },
      h("div", {}, h("a", { class: "nf-logo", href: BASE, html: `${LOGO}<b>net<i>fugl</i>.dk</b>` }), h("p", { class: "small" }, "Showcase på DOF.tool · indhold kopieret fra netfugl.dk som eksempel")),
      h("div", { class: "dof" }, "Dansk", h("br"), "Ornitologisk", h("br"), "Forening"))));
}
