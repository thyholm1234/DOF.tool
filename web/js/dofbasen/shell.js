// DOFbasen-modulet: fælles skal (header, fanemenu, højrekolonne, sidefod), let markup og hjælpere.
// Layoutet følger dofbasen.dk; links i det kopierede indhold, der peger på dofbasen.dk, oversættes til modulets egne sider.
import { api, h, esc, fmtN, toast, backLayer } from "../core.js";

export const BASE = "#/dofbasen";
export const ctx = { data: null };

/** Modulets kontekst (rettigheder, funktioner, menu-links) – hentes én gang pr. visning. */
export async function loadCtx(force = false) {
  if (!ctx.data || force) ctx.data = await api("/api/dofbasen/ctx");
  return ctx.data;
}
export const on = (f) => !!ctx.data?.features?.[f];
export const can = (p) => !!ctx.data?.perms?.includes(p);
/** Må redigere direkte på siden (rettighed + funktionen "redigering på siderne"). */
export const canEdit = (p) => can(p) && on("dofbasen_edit");

// ---------------------------------------------------------------- formattering

export const dmy = (iso) => { if (!iso) return ""; const [y, m, d] = String(iso).slice(0, 10).split("-"); return `${d}-${m}-${y}`; };
export const dmySlash = (iso) => dmy(iso).replaceAll("-", "/");
const DAYS = ["SØNDAG", "MANDAG", "TIRSDAG", "ONSDAG", "TORSDAG", "FREDAG", "LØRDAG"];
export const weekday = (iso) => DAYS[new Date(iso + "T12:00:00").getDay()];
export const MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];
export const artnr5 = (n) => String(n).padStart(5, "0");

/** Artsnavn i DOFbasens farver: SU rød, SUB grøn, fænologisk bemærkelsesværdig blå. */
export function artClass(kat) {
  return { SU: "db-su", SUB: "db-subart", bemaerk: "db-default" }[kat] || "";
}
export function artName(name, kat, latin) {
  const el = h("span", {}, h("span", { class: artClass(kat) || "db-default" }, name));
  if (latin) el.append(" (", h("i", {}, latin), ")");
  return el;
}

// ---------------------------------------------------------------- let markup → HTML

// dofbasen.dk-adresser → modulets egne sider. Ukendte adresser åbnes på originalen i en ny fane.
const ROUTES = [
  [/^\/(index\.php)?$/, () => ""],
  [/^\/start\.php/, () => "/side/start"], [/^\/rights\.php/, () => "/side/rettigheder"], [/^\/kvalitet/, () => "/side/kvalitet"],
  [/^\/help\.php/, () => "/side/hjaelp"], [/^\/viden/, () => "/side/viden"], [/^\/kontakt/, () => "/side/kontakt"],
  [/^\/features\.php/, () => "/side/funktioner"], [/^\/english/, () => "/side/english"], [/^\/opretmig\.php/, () => "/side/brugeroprettelse"],
  [/^\/reminder\.php/, () => "/side/glemt"], [/^\/logind\.php/, () => "/bruger"], [/^\/mine/, () => "/bruger"],
  [/^\/observationer/, (u) => "/observationer" + u.search],
  [/^\/search\/(search1|result)\.php/, (u) => "/soeg" + u.search],
  [/^\/search\/lokalitet\.php/, () => "/opslag/lokaliteter"], [/^\/search\/storlokalitet\.php/, () => "/opslag/omraader/stor"],
  [/^\/search\/ibalokalitet\.php/, () => "/opslag/omraader/iba"], [/^\/search\/?$/, () => "/soeg"], [/^\/kort\.php/, () => "/opslag/kort"],
  [/^\/opslag\/artdata\.php/, () => "/opslag/arter"], [/^\/opslag\/behaviour\.php/, () => "/opslag/liste/adfaerd"],
  [/^\/opslag\/alder\.php/, () => "/opslag/liste/alder"], [/^\/opslag\/dragt\.php/, () => "/opslag/liste/dragt"],
  [/^\/opslag\/vindstyrke\.php/, () => "/opslag/liste/vindstyrke"], [/^\/opslag\/sudata\.php/, () => "/opslag/liste/su"],
  [/^\/opslag\/subdata\.php/, () => "/opslag/liste/sub"], [/^\/opslag\/sensitive\.php/, () => "/opslag/liste/foelsomme"],
  [/^\/opslag\/bemaerk\.php/, () => "/opslag/bemaerk"], [/^\/opslag\/wsdata\.php/, () => "/opslag/liste/faenologi"],
  [/^\/opslag\/visningstyper_soegning\.php/, () => "/side/visningstyper_soegning"], [/^\/opslag\/visningstyper\.php/, () => "/side/visningstyper"],
  [/^\/opslag\/obserdata\.php/, () => "/opslag/observatorer"], [/^\/opslag\/?$/, () => "/opslag"],
  [/^\/faeno\/art\.php/, (u) => u.searchParams.get("artnmr") ? `/faeno/art/${+u.searchParams.get("artnmr")}` : "/faeno"],
  [/^\/faeno\/firstobs/, () => "/faeno/firstobs"], [/^\/faeno\/samedate/, () => "/faeno/samedate"], [/^\/faeno\/art_kort/, () => "/faeno/artkort"],
  [/^\/(ART\/art\.php|danmarksfugle\/art\/)/, (u) => { const n = u.searchParams.get("art") || u.pathname.split("/").pop(); return /^\d+$/.test(n) ? `/faeno/art/${+n}` : "/faeno"; }],
  [/^\/popobs\.php/, (u) => u.searchParams.get("obsid") ? `/obs/${+u.searchParams.get("obsid")}` : "/observationer"],
  [/^\/poplok\.php/, (u) => u.searchParams.get("loknr") ? `/lok/${+u.searchParams.get("loknr")}` : "/opslag/lokaliteter"],
  [/^\/popobser\.php/, (u) => u.searchParams.get("obserkode") ? `/obser/${encodeURIComponent(u.searchParams.get("obserkode"))}` : "/opslag/observatorer"],
  [/^\/news/, () => "/nyheder"], [/^\/atlas/, () => "/side/atlas"], [/^\/pkt/, () => "/side/punkt"], [/^\/fotogalleri/, () => "/fotogalleri"],
];
export function mapHref(href) {
  let u;
  try { u = new URL(href, "https://dofbasen.dk/"); } catch { return { href: "#", ext: false }; }
  if (u.hostname === "dofbasen.dk" || u.hostname === "www.dofbasen.dk") {
    for (const [re, fn] of ROUTES) if (re.test(u.pathname)) return { href: BASE + fn(u), ext: false };
  }
  if (!/^https?:$/.test(u.protocol) && u.protocol !== "mailto:") return { href: "#", ext: false };
  return { href: u.href, ext: true };
}

function inline(text) {
  // Links først (pladsholdere), så understregninger i adresser ikke tolkes som kursiv
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
export function md(text, { values } = {}) {
  let src = String(text ?? "");
  if (values) src = src.replace(/\{(\w+)\}/g, (m, k) => values[k] ?? m);
  const out = [];
  for (const block of src.split(/\n{2,}/)) {
    const lines = block.split("\n").filter(l => l.trim() !== "");
    if (!lines.length) continue;
    if (lines.every(l => l.trim().startsWith("|"))) {
      out.push("<table>" + lines.map(l => "<tr>" + l.trim().replace(/^\||\|$/g, "").split("|").map(c => `<td>${inline(c.trim())}</td>`).join("") + "</tr>").join("") + "</table>");
    } else if (lines.every(l => l.trim().startsWith("• "))) {
      out.push("<ul>" + lines.map(l => `<li>${inline(l.trim().slice(2))}</li>`).join("") + "</ul>");
    } else {
      out.push("<p>" + lines.map(l => l.trim().startsWith("• ") ? "<b>•</b> " + inline(l.trim().slice(2)) : inline(l)).join("<br>") + "</p>");
    }
  }
  return h("div", { class: "db-md", html: out.join("") });
}

// ---------------------------------------------------------------- byggeklodser

/** Sektionsbjælke (grå/blå/tema) med evt. redigér-knap. */
export function head(title, { kind = "", edit, center } = {}) {
  return h("div", { class: `db-h ${kind} ${center ? "center" : ""}` }, h("span", {}, title),
    edit ? h("button", { class: "db-edit", type: "button", onclick: edit }, "Redigér") : null);
}

/** Faneblade som på dofbasen.dk (#tabnav). tabs = [[værdi, tekst]] */
export function tabs(list, value, onPick, { tema = false } = {}) {
  const bar = h("div", { class: `db-tabs ${tema ? "tema" : ""}` });
  const draw = (v) => bar.replaceChildren(...list.map(([k, t]) => h("a", { href: "javascript:void 0", class: k === v ? "on" : "", onclick: (e) => { e.preventDefault(); draw(k); onPick(k); } }, t)));
  draw(value);
  return bar;
}

export function link(path, text, attrs = {}) {
  return h("a", { href: BASE + path, ...attrs }, text);
}

export function notice(text, err = false) {
  return h("div", { class: `db-notice ${err ? "err" : ""}` }, text);
}

export function loadingBox(text = "Henter …") {
  return h("div", { class: "muted", style: { padding: "10px 0" } }, text);
}

/** Modal i DOFbasens stil (popup-vinduerne popobs/poplok/popobser). */
export function dbModal(title, body, { wide = false } = {}) {
  const close = () => { bg.remove(); document.removeEventListener("keydown", key); removeEventListener("hashchange", close); };
  const key = (e) => { if (e.key === "Escape") close(); };
  // Et klik på et link i popuppen skifter side – så skal popuppen lukke
  addEventListener("hashchange", close);
  const bg = h("div", { class: "db-modal-bg", onclick: (e) => { if (e.target === bg) close(); } },
    h("div", { class: `db-modal ${wide ? "wide" : ""}`, role: "dialog", "aria-modal": "true" },
      h("div", { class: "db-modal-head" }, h("b", {}, title), h("button", { type: "button", "aria-label": "Luk", onclick: close }, "×")),
      h("div", { class: "db-modal-body dofb" }, body)));
  document.body.append(bg);
  document.addEventListener("keydown", key);
  backLayer(bg, close);
  return { close, el: bg };
}

/** Tekstfelt med forslag (art eller lokalitet) som DOFbasens autosuggest. */
export function autosuggest({ url, placeholder = "", width = "180px", label = (r) => r.navn, initial = "", onPick }) {
  const input = h("input", { type: "text", placeholder, value: initial, style: { width }, autocomplete: "off" });
  const list = h("div", { class: "db-ac-list", hidden: true });
  let items = [], timer = null, seq = 0, active = -1;
  const pick = (r) => { input.value = label(r); list.hidden = true; onPick(r); };
  const draw = () => {
    list.replaceChildren(...items.map((r, i) => h("div", { class: i === active ? "on" : "", onmousedown: (e) => { e.preventDefault(); pick(r); } }, label(r),
      r.afdeling || r.kommune ? h("span", { class: "muted" }, ` – ${[r.kommune, r.afdeling].filter(Boolean).join(", ")}`) : null)));
    list.hidden = !items.length;
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { items = []; draw(); if (!q) onPick(null); return; }
    timer = setTimeout(async () => {
      const my = ++seq;
      try { const r = await api(url + encodeURIComponent(q)); if (my === seq) { items = r; active = -1; draw(); } } catch { /* ignore */ }
    }, 180);
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); draw(); e.preventDefault(); }
    else if (e.key === "Enter" && items[Math.max(0, active)]) { pick(items[Math.max(0, active)]); e.preventDefault(); }
    else if (e.key === "Escape") list.hidden = true;
  });
  input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 150));
  return h("span", { class: "db-ac" }, input, list);
}

// ---------------------------------------------------------------- redigering (sider og tekstbokse)

/** Redigér en tekstside: titel + sektioner (overskrift og brødtekst i let markup). */
export function editPage(page, onSaved) {
  const title = h("input", { type: "text", value: page.title, class: "full", maxlength: 160 });
  const list = h("div", {});
  const secs = page.sections.map(s => ({ ...s }));
  const draw = () => list.replaceChildren(...secs.map((s, i) => h("div", { class: "sec" },
    h("div", { class: "bar" },
      h("input", { type: "text", value: s.title, placeholder: "Overskrift (vises med store bogstaver)", oninput: (e) => { s.title = e.target.value; } }),
      h("button", { type: "button", title: "Flyt op", disabled: i === 0, onclick: () => { [secs[i - 1], secs[i]] = [secs[i], secs[i - 1]]; draw(); } }, "↑"),
      h("button", { type: "button", title: "Flyt ned", disabled: i === secs.length - 1, onclick: () => { [secs[i + 1], secs[i]] = [secs[i], secs[i + 1]]; draw(); } }, "↓"),
      h("button", { type: "button", class: "db-danger", onclick: () => { if (confirm("Fjern sektionen?")) { secs.splice(i, 1); draw(); } } }, "Fjern")),
    h("textarea", { rows: Math.min(18, Math.max(4, s.body.split("\n").length + 1)), oninput: (e) => { s.body = e.target.value; } }, s.body))));
  draw();
  const m = dbModal(`Redigér: ${page.title}`, h("div", { class: "db-admin" },
    h("div", { class: "field" }, h("b", {}, "Sidens titel"), title),
    markupHelp(),
    list,
    h("div", { class: "db-form-row" },
      h("button", { type: "button", onclick: () => { secs.push({ title: "NY SEKTION", body: "" }); draw(); } }, "+ Tilføj sektion"),
      h("span", { style: { flex: 1 } }),
      h("button", { type: "button", onclick: () => m.close() }, "Annullér"),
      h("button", { type: "button", class: "db-primary", onclick: async () => {
        try {
          const saved = await api(`/api/dofbasen/sider/${page.slug}`, { method: "PUT", body: { title: title.value.trim() || page.title, sections: secs } });
          toast("Siden er gemt"); m.close(); onSaved?.(saved);
        } catch (err) { toast(err.message, "err"); }
      } }, "Gem"))), { wide: true });
}

export function markupHelp() {
  return h("details", { class: "muted", style: { margin: "4px 0 8px" } }, h("summary", {}, "Sådan formaterer du teksten"),
    h("div", { class: "db-code" }, "Tom linje = nyt afsnit\n• Punkt (start linjen med • og mellemrum)\n**fed**   _kursiv_\n[linktekst](https://adresse) – links til dofbasen.dk vises som modulets egne sider\n| celle | celle |  (linjer der starter med | bliver en tabel)\n{lokaliteter} og {observationer} indsættes som tal på forsiden"));
}

// ---------------------------------------------------------------- skal

const BIRD = `<svg class="db-bird" viewBox="0 0 560 85" preserveAspectRatio="xMaxYMid slice" aria-hidden="true">
  <defs><linearGradient id="dbg" x1="0" x2="1"><stop offset="0" stop-color="#6392b0"/><stop offset=".55" stop-color="#9dbfd6"/><stop offset="1" stop-color="#cfe0eb"/></linearGradient></defs>
  <rect width="560" height="85" fill="url(#dbg)"/>
  <g fill="none" stroke="#5c88a6" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" opacity=".55">
    <path d="M300 85c40-30 70-38 110-36 22 1 40-6 54-22 10-12 26-18 44-14 22 5 34 22 52 18"/>
    <path d="M410 49c-4 14-20 22-46 26M460 22c-8-10-6-20 4-24"/>
  </g>
  <g fill="#4f7d9b" opacity=".55"><path d="M462 30c14-10 32-12 50-4-12 2-20 8-26 16-8-2-16-6-24-12z"/><circle cx="478" cy="30" r="3.4" fill="#e8f0f5"/></g>
</svg>`;

function menuTree() {
  const d = ctx.data || {};
  const ext = (rows) => (rows || []).map(([t, url]) => { const m = mapHref(url); return m.ext ? [t, m.href, true] : [t, m.href, null, true]; });
  const user = [["Min side", "/bruger"], ...(on("dofbasen_api") ? [["API-nøgler", "/api"]] : []),
    ...(d.perms?.length ? [["Administration", "/admin"]] : []), ["Tilbage til DOF.tool", "#/", false, true]];
  return [
    ["DOFbasen", "", [["Forsiden", ""], ["Hvad er det?", "/side/start"], ["Rettigheder", "/side/rettigheder"],
      ["Statistik", null, ext(d.menu?.statistik)], ["Kvalitetssikring", "/side/kvalitet"], ["Hjælp", "/side/hjaelp"], ["Anvendelse af data", "/side/viden"],
      ...(on("dofbasen_news") ? [["Nyheder", "/nyheder"]] : [])]],
    ["Observationer", "/observationer", [["Observationer i dag", "/observationer"], ...(on("dofbasen_search") ? [["Søg observationer", "/soeg"]] : []),
      ["Fotogalleri", "/fotogalleri"],
      ["Fænologi", "/faeno", [["Artsliste", "/faeno"], ["Første iagttagelse", "/faeno/firstobs"], ["Samme dato", "/faeno/samedate"], ["Artskort", "/faeno/artkort"]]],
      ["Atlas III", "/side/atlas"], ["Punkttællinger", "/side/punkt"]]],
    ["Lister", "/opslag", [
      ["Indtastning", "/opslag", [["Lokalitetsliste", "/opslag/lokaliteter"], ["Lokalitetskort", "/opslag/kort"], ["Artsliste", "/opslag/arter"],
        ["Adfærdskoder", "/opslag/liste/adfaerd"], ["Alderskoder", "/opslag/liste/alder"], ["Dragtkoder", "/opslag/liste/dragt"], ["Vindstyrker", "/opslag/liste/vindstyrke"]]],
      ["Observationsvisning", "/opslag", [["SU-arter", "/opslag/liste/su"], ["SUB-arter", "/opslag/liste/sub"], ["Følsomme arter", "/opslag/liste/foelsomme"],
        ["Bemærkelsesværdige", "/opslag/bemaerk"], ["Fænologiliste", "/opslag/liste/faenologi"], ["Storlokaliteter", "/opslag/omraader/stor"], ["IBA-lokaliteter", "/opslag/omraader/iba"]]],
      ["Links på egen side", "/opslag", [["Til observationssiden", "/side/visningstyper"], ["Til søgesiden", "/side/visningstyper_soegning"]]],
      ["Observatørliste", "/opslag/observatorer"]]],
    ["Kontakt", "/side/kontakt"],
    ["Links", null, ext(d.menu?.links)],
    ["Støt DOF", null, ext(d.menu?.stoet)],
    ["Bruger", "/bruger", user, "db-user"],
  ];
}

function menuLink([text, target, sub, abs]) {
  const ext = sub === true;
  const href = target === null ? "javascript:void 0" : abs || ext ? target : BASE + target;
  const a = h("a", { href, class: ext ? "db-ext" : "", ...(ext ? { target: "_blank", rel: "noopener" } : {}) }, text);
  if (Array.isArray(sub) && sub.length) {
    return h("div", { class: "db-sub" }, a, h("div", { class: "db-drop" }, ...sub.map(menuLink)));
  }
  return a;
}

function header() {
  const menu = h("nav", { class: "db-menu", "aria-label": "DOFbasen-menu" });
  for (const [text, target, items, cls] of menuTree()) {
    const mi = h("div", { class: `db-mi ${cls || ""}` });
    const top = h("a", { href: target === null ? "javascript:void 0" : BASE + target, onclick: (e) => {
      // På telefon (og ved tastatur) åbner første tryk undermenuen
      if (items?.length && (matchMedia("(max-width: 760px)").matches || target === null)) {
        e.preventDefault();
        const open = mi.classList.contains("open");
        menu.querySelectorAll(".db-mi.open").forEach(x => x.classList.remove("open"));
        mi.classList.toggle("open", !open);
      }
    } }, text);
    mi.append(top);
    if (items?.length) mi.append(h("div", { class: "db-drop" }, ...items.map(menuLink)));
    menu.append(mi);
  }
  const hd = h("header", { class: "db-header" },
    h("span", { html: BIRD }),
    h("a", { class: "db-logo", href: BASE }, "DOFbasen"),
    h("button", { class: "db-burger", type: "button", "aria-label": "Menu", onclick: () => hd.classList.toggle("menu-open") }, "☰ Menu"),
    menu);
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) menu.querySelectorAll(".db-mi.open").forEach(x => x.classList.remove("open")); });
  return hd;
}

/** Højrekolonnens genvejsknapper (som GENVEJE på dofbasen.dk). */
export function shortcuts() {
  return [head("Genveje", { kind: "blue" }),
    h("div", { class: "db-shortcuts" },
      link("/bruger", "Min side", { class: "green" }),
      link("/side/brugeroprettelse", "Brugeroprettelse", { class: "green" }),
      on("dofbasen_search") ? link("/soeg", "Søg observationer") : null,
      link("/observationer", "Dagens observationer"),
      h("a", { href: "#/alarmer" }, "Notifikationer"))];
}

let cssLoaded = false;
function ensureCss() {
  if (cssLoaded || document.querySelector("link[data-dofb]")) return;
  const path = new URL(import.meta.url).pathname;
  const href = path.startsWith("/v/") ? `/v/${path.split("/")[2]}/css/dofbasen.css` : "/css/dofbasen.css";
  document.head.append(h("link", { rel: "stylesheet", href, "data-dofb": "" }));
  cssLoaded = true;
}

/** Hele siden: strimmel, header med menu, indhold og sidefod. */
export function frame(content, started) {
  ensureCss();
  document.body.classList.add("dofb-mode");
  const secs = ((performance.now() - started) / 1000).toFixed(3);
  return h("div", { class: "dofb" },
    h("div", { class: "db-strip" },
      h("span", { class: "db-pill" }, "SHOWCASE"),
      h("span", { class: "db-long" }, "Ny udgave af DOFbasen på DOF.tool's moderne backend. Tekster og lister er kopieret fra dofbasen.dk som eksempel – indholdet tilhører Dansk Ornitologisk Forening."),
      h("span", { class: "spacer" }),
      ctx.data?.perms?.length ? link("/admin", "Admin") : null,
      h("a", { href: "#/" }, "← DOF.tool")),
    header(),
    content,
    h("div", { class: "db-footer" }, h("a", { href: "https://www.dof.dk", target: "_blank", rel: "noopener" }, "DOF"),
      " Vesterbrogade 140 1620 København V. - tlf.: 3328 3800 e-mail: dof@dof.dk © 2002-2026 · Showcase på DOF.tool"),
    h("div", { class: "db-timing" }, `(${secs} sekunder)`));
}

export function fmtInt(n) { return n == null ? "–" : fmtN(n); }
