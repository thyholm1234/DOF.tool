import { api, prefetch, state, h, $, icon, toast, ago, trackView, adminLink, moderationLink, hasTab, backLayer, lightDismiss, hasPopover, trustedHTML, trustedScriptURL } from "./core.js";
import { live } from "./live.js";
import { syncSharing, syncPush, stopAppPush } from "./native.js";
import { initSelectMenu } from "./select-menu.js";

// Build-id fra den versionerede sti (/v/<build>/js/app.js) – vises i brugermenuen
const BUILD = new URL(import.meta.url).pathname.split("/")[2] || "dev";
initSelectMenu();
applyTheme();  // straks, så siden ikke blinker i det forkerte tema

// Navigation i grupper – samme rækkefølge i sidepanelet (desktop) og i "Mere"-arket (mobil)
const SECTIONS = [
  ["Nu og her", [["#/", "now", "Mit overblik"]]],
  ["I felten", [["#/indtast", "edit", "Indtastninger"], ["#/alarmer", "bell", "Alarmer"]]],  // lige under Observationer
  ["Planlæg", [["#/kalender", "calendar", "Kalender"], ["#/ture", "flag", "DOF-ture"]]],
  ["Data", [["#/traek", "trend", "Træk"]]],
  ["Krydslister", [["#/lister", "list", "Krydslister"], ["#/matrikel", "home", "Matrikel"]]],
  ["Viden", [["#/laer", "learn", "Læring"]]],
  ["Værktøjer", [["#/kort", "map", "Kort"], ["#/mobgang", "sound", "Mobgang"]]],
];
const DOFBASEN = ["#/dofbasen", "db", "DOFbasen"];
const NETFUGL = ["#/netfugl", "bird", "Netfugl"];
const ARTIKLER = ["#/artikler", "news", "Artikler"];
const FOTOS = ["#/fotos", "camera", "Fotos"];
const SNAK = ["#/snak", "chat", "Snak"];
const DAGSLISTE = ["#/dagsliste", "list", "Observationer"];
const SEARCH = ["#/soeg", "search", "Søg"];
const ARTER = ["#/arter", "bird", "Arter"];
const LOKALITETER = ["#/lokaliteter", "pin", "Lokaliteter"];  // uden Statistik-modulet står de selv under Data
const STATS = ["#/statistik", "chart", "Statistik"];
const OBSERVERS = ["#/observator", "user", "Observatører"];
const SU = ["#/su", "flag", "SU"];
/** Showcases (DOFbasen og Netfugl) står nederst, efter Admin – kun når superadmin har slået modulerne til (eller forhåndsvisning). */
const showcases = () => [state.user?.dofbasen?.features?.dofbasen ? DOFBASEN : null, state.user?.netfugl?.features?.netfugl ? NETFUGL : null].filter(Boolean);
/** Menuens grupper for den indloggede: modulerne bag funktionsflag vises kun, når de er slået til (eller forhåndsvisning). */
function sections() {
  const extra = [state.user?.artikler?.features?.artikler ? ARTIKLER : null, state.user?.fotos?.features?.fotos ? FOTOS : null, state.user?.snak?.features?.snak ? SNAK : null].filter(Boolean);
  // DKU (med antal sager med ulæste beskeder), når modulet er synligt for brugeren
  const dku = state.user?.dku?.visible ? ["#/dku", "check", `DKU${state.user.dku.unread ? ` <span class="badge-inline">${+state.user.dku.unread}</span>` : ""}`] : null;
  // Statistik-modulet og Søg under Data, når funktionerne er synlige for brugeren
  const stat = [...(state.user?.statistik ? [STATS] : [ARTER, LOKALITETER]), state.user?.search ? SEARCH : null, state.user?.profiler && !state.user?.statistik ? OBSERVERS : null].filter(Boolean);  // Observatører er en fane under Statistik-modulet
  const dl = state.user?.dagsliste ? DAGSLISTE : null;  // Observationer (dagslisten) står lige under Mit overblik
  // SU (med antal ulæste sager) og DKU samles under overskriften Kvalitetssikring, når modulerne er synlige for brugeren
  const su = state.user?.su?.visible ? ["#/su", "flag", `SU${state.user.su.unread ? ` <span class="badge-inline">${+state.user.su.unread}</span>` : ""}`] : null;
  // Moderation øverst i Kvalitetssikring; ulæste support-samtaler vises her, medmindre brugeren har dem på Admin
  const modl = moderationLink();
  const modUnread = adminLink() && hasTab("support") ? 0 : state.adminSupportUnread;
  const mod = modl ? [modl.href, "comment", `${modl.label}${modUnread ? ` <span class="badge-inline">${+modUnread}</span>` : ""}`] : null;
  const qa = [mod, dku, su].filter(Boolean);
  if (!extra.length && !qa.length && !stat.length && !dl) return SECTIONS;
  const out = SECTIONS.map(([t, items]) => [t, t === "Nu og her" && dl ? [...items, dl] : t === "Viden" ? [...items, ...extra]
    : t === "Data" ? [...items, ...stat] : items]);  // Krydslister-modulet (#/lister)
  if (qa.length) out.push(["Kvalitetssikring", qa]);  // nederst
  return out;
}
const NAV = [...SECTIONS.flatMap(([, items]) => items), DOFBASEN, NETFUGL, ARTIKLER, FOTOS, SNAK, STATS, DAGSLISTE, SEARCH, OBSERVERS, ARTER, LOKALITETER, ["#/dku", "check", "DKU"], SU];
const navItem = (href) => NAV.find(n => n[0] === href);
const BOTTOM = ["#/", "#/traek", "#/kalender"];  // mobilens faste knapper (plus Indtast og Mere)

/** Det mest specifikke menupunkt der matcher stien (så #/kalender/mine ikke også markerer Kalender). */
function activeHref(path, hrefs) {
  // en arts side hører under Arter, en lokalitets side under Lokaliteter
  const p = path === "#" ? "#/" : path.startsWith("#/art/") ? (state.user?.statistik ? "#/statistik" : "#/arter") : path.startsWith("#/lok/") ? (state.user?.statistik ? "#/statistik" : "#/lokaliteter")
    : path.startsWith("#/observator") && state.user?.statistik ? "#/statistik" : path;
  return hrefs.filter(href => href === "#/" ? p === "#/" : p === href || p.startsWith(href + "/") || p.startsWith(href + "?"))
    .sort((a, b) => b.length - a.length)[0];
}

const ROUTES = [
  [/^\/?$/, () => import("./views/feed.js")],
  [/^\/traad\/(\d{4}-\d{2}-\d{2})\/(\d+)\/(\d+)$/, () => import("./views/thread.js")],
  [/^\/obs\/(\d+)$/, () => import("./views/obs.js")],
  [/^\/su(?:\/(sag|ny)(?:\/(\d+))?)?$/, () => import("./views/su.js")],  // SU: årsrapporter, arkiv, sager  // én observation, åbnet fra Observationer
  [/^\/lister(?:\/(.+))?$/, () => import("./views/lists.js")],
  [/^\/matrikel$/, () => import("./views/matrikel.js")],
  [/^\/traek$/, () => import("./views/pheno.js")],
  [/^\/(?:kort|puls)$/, () => import("./views/kort.js")],  // #/puls er den gamle adresse
  [/^\/arter$/, () => import("./views/arter.js")],
  [/^\/art\/(\d+)$/, () => import("./views/species.js")],
  [/^\/lokaliteter$/, () => import("./views/lokaliteter.js")],
  [/^\/dagsliste$/, () => import("./views/dagsliste.js")],
  [/^\/soeg$/, () => import("./views/soeg.js")],
  [/^\/statistik$/, () => import("./views/stats.js")],
  [/^\/observator(?:\/([^/]+))?$/, () => import("./views/observer.js")],
  [/^\/lok\/(\d+)$/, () => import("./views/location.js")],
  [/^\/lok\/(\d+)\/art\/(\d+)$/, () => import("./views/locspecies.js")],
  [/^\/laer(?:\/(.+))?$/, () => import("./views/learn.js")],
  [/^\/mobgang$/, () => import("./views/mobgang.js")],
  [/^\/alarmer$/, () => import("./views/alerts.js")],
  [/^\/profil$/, () => import("./views/profile.js")],
  [/^\/admin$/, () => import("./views/admin.js")],
  [/^\/moderation$/, () => import("./views/moderation.js")],
  [/^\/login$/, () => import("./views/login.js")],
  [/^\/indtast\/ny$/, () => import("./views/indtast-start.js")],
  [/^\/indtast\/(spontan|finn)$/, () => import("./views/taelling.js")],
  [/^\/indtast\/tur\/(ny|\d+|lokal\/[\w-]+)(?:\/(ret))?$/, () => import("./views/tur.js")],
  [/^\/indtast(?:\/obs\/(\d+))?$/, () => import("./views/report.js")],
  [/^\/support$/, () => import("./views/support.js")],
  [/^\/dku(?:\/(\d+))?$/, () => import("./views/dku.js")],
  [/^\/ture(?:\/(\d+))?$/, () => import("./views/events.js")],
  [/^\/kalender(?:\/(\d+|mine))?$/, () => import("./views/calendar.js")],
  [/^\/dofbasen(?:\/(.+))?$/, () => import("./views/dofbasen.js")],
  [/^\/netfugl(?:\/(.+))?$/, () => import("./views/netfugl.js")],
  [/^\/artikler(?:\/(.+))?$/, () => import("./views/artikler.js")],
  [/^\/fotos(?:\/(.+))?$/, () => import("./views/fotos.js")],
  [/^\/snak(?:\/(.*))?$/, () => import("./views/snak.js")],
  [/^\/krydslister(?:\/(.+))?$/, () => import("./views/krydslister.js")],
  [/^\/nyheder\/(\d+)$/, () => import("./views/nyhed.js")],
];

let cleanup = null;

function renderNav() {
  const path = "#" + (location.hash.slice(1).split("?")[0] || "/");
  const current = activeHref(path, [...NAV.map(n => n[0]), "#/profil", "#/support", "#/admin", "#/moderation"]);
  const link = ([href, ic, label], cls = "") =>
    h("a", { href, class: `${cls} ${href === current ? "active" : ""}`.trim(), "aria-current": href === current ? "page" : null, html: `${icon(ic)}<span>${label}</span>` });
  document.body.classList.toggle("anon", !state.user);
  // Mobil: Nu · Træk · [Indtast] · Kalender · Mere (arket viser alle moduler i samme grupper som sidepanelet)
  const inMore = current && !BOTTOM.includes(current);
  // En handling (åbner arket), ikke et link
  const more = h("button", { type: "button", class: `nav-more ${inMore ? "active" : ""}`, "aria-haspopup": "dialog", onclick: openMore, html: `${icon("more")}<span>Mere</span>` });
  $("#bottomnav").replaceChildren(...(state.user
    ? [link(navItem("#/")), link(navItem("#/traek")), link(["#/indtast/ny", "plus", "Indtast"], "cta"), link(navItem("#/kalender")), more]
    : []));
  const side = [];
  for (const [title, items] of sections()) side.push(h("div", { class: "nav-h" }, title), ...items.map(n => link(n)));
  if (state.user) {
    side.unshift(link(["#/indtast/ny", "plus", "Indtast"], "cta"));
    side.push(h("div", { class: "sep" }));
    side.push(link(["#/profil", "user", "Profil"]));
    side.push(h("a", { href: "#/support", class: current === "#/support" ? "active" : "", "aria-current": current === "#/support" ? "page" : null, html: `${icon("chat")}<span>Support${state.supportUnread ? ` <span class="badge-inline">${state.supportUnread}</span>` : ""}</span>` }));
    // Ulæste samtaler vises på Admin for dem med support-modulet, ellers på Moderation (under Kvalitetssikring)
    const adm = adminLink(), unread = state.adminSupportUnread, onAdmin = adm && hasTab("support");
    const badge = (n) => n ? ` <span class="badge-inline">${n}</span>` : "";
    if (adm) side.push(h("a", { href: adm.href, class: current === "#/admin" ? "active" : "", "aria-current": current === "#/admin" ? "page" : null, html: `${icon("shield")}<span>${adm.label}${badge(onAdmin ? unread : 0)}</span>` }));
  }
  const demo = showcases();
  if (demo.length) side.push(h("div", { class: "nav-h" }, "Showcases"), ...demo.map(n => link(n)));
  $("#sidenav").replaceChildren(...side);
}

/** «Mere»-arket på mobil: en modal <dialog> (fokus ind og tilbage, Esc og inert side klares af browseren). */
function openMore() {
  document.querySelector(".more-sheet")?.remove();
  const close = () => { if (sheet.open) sheet.close(); sheet.remove(); };
  const tile = (href, ic, label, badge) => h("a", { href, class: "more-tile", onclick: close, html: `${icon(ic)}<span>${label}${badge ? ` <span class="badge-inline">${badge}</span>` : ""}</span>` });
  const u = state.user;
  const sheet = h("dialog", { class: "more-sheet more-panel", "aria-label": "Alle menupunkter" },
      // Samme grupper og moduler som sidepanelet – også dem, der står i bundmenuen
      ...sections()
        .map(([title, items]) => items.length ? [h("div", { class: "more-h" }, title), h("div", { class: "more-grid" }, ...items.map(([href, ic, label]) => tile(href, ic, label)))] : [])
        .flat(),
      h("div", { class: "more-h" }, "Konto"),
      h("div", { class: "more-grid" },
        tile("#/profil", "user", "Profil"), tile("#/support", "chat", "Support", state.supportUnread),
        u && adminLink() ? tile(adminLink().href, "shield", adminLink().label, hasTab("support") ? state.adminSupportUnread : 0) : null),
      // Showcases nederst
      ...(showcases().length ? [h("div", { class: "more-h" }, "Showcases"), h("div", { class: "more-grid" }, ...showcases().map(([href, ic, label]) => tile(href, ic, label)))] : []),
      h("button", { type: "button", class: "btn ghost", style: { width: "100%", marginTop: "8px" }, onclick: close }, "Luk"));
  lightDismiss(sheet);
  sheet.addEventListener("close", () => sheet.remove());
  document.body.append(sheet);
  sheet.showModal();
  backLayer(sheet, close);
}

function renderUser() {
  const u = state.user;
  $("#loginbtn").classList.toggle("hide", !!u);
  $("#userbtn").classList.toggle("hide", !u);
  $("#bell").classList.toggle("hide", !u);
  if (!u) return;
  const initials = (u.navn || u.obserkode).split(/\s+/).map(s => s[0]).join("").slice(0, 2).toUpperCase();
  // Profilbillede i knappen (lille variant, 96 px), ellers initialer
  const btn = $("#userbtn"), src = u.avatar?.s;
  if (src) { if (btn.querySelector("img")?.getAttribute("src") !== src) btn.replaceChildren(h("img", { src, alt: "", width: 34, height: 34, decoding: "async" })); }
  else btn.textContent = initials;
  $("#bell").innerHTML = trustedHTML(icon("bell") + (state.unread ? `<span class="badge">${state.unread > 99 ? "99+" : state.unread}</span>` : ""));
}

let viewAsTimer = null;
/** Rødt fast banner, når en superadmin ser appen som en anden bruger (kun læsning, 15 minutter). */
function renderViewAs() {
  const va = state.user?.view_as, old = document.getElementById("viewas");
  clearInterval(viewAsTimer);
  document.body.classList.toggle("is-viewas", !!va);
  if (!va) { old?.remove(); return; }
  const bar = old || h("div", { id: "viewas", class: "viewas", role: "status" });
  const end = async () => { try { await api("/api/admin/se-som", { method: "DELETE" }); } catch { /* udløbet */ } location.hash = "#/admin?tab=brugere"; location.reload(); };
  const draw = () => {
    const left = Math.round(va.until - Date.now() / 1000);
    if (left <= 0) { clearInterval(viewAsTimer); end(); return; }
    bar.replaceChildren(`Du ser appen som ${state.user.navn || state.user.obserkode} (${state.user.obserkode}) – kun læsning, logget · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} tilbage · `,
      h("button", { type: "button", onclick: end }, "afslut"));
  };
  draw();
  viewAsTimer = setInterval(draw, 1000);
  if (!old) document.body.prepend(bar);
}

export async function refreshUser() {
  try { state.user = (await api("/api/me")).user; } catch { state.user = null; }
  applyTheme();
  renderUser();
  renderViewAs();
  renderNav();
  syncSharing();
  syncPush();
  if (state.user && !state.user.view_as) { refreshUnread(); startOffline(); }
}

let offlineStarted = false;
// Ture, der er gemt på enheden uden forbindelse, sendes, når der er net (og brugeren er logget ind)
function startOffline() {
  if (offlineStarted) return;
  offlineStarted = true;
  import("./offline.js").then(m => m.start());
  import("./tur-data.js").then(m => m.warmOffline());  // opslag, lokaliteter og de seneste ture til indtastning uden net
}

export async function refreshUnread() {
  try {
    state.unread = (await api("/api/notifications?limit=1")).unread; renderUser();
    // Åbnet fra en push-notifikation: siden vises før antallet er hentet
    if (state.unread) markSeen("/#" + ((location.hash.slice(1) || "/").split("?")[0]));
  } catch { /* offline */ }
  try { const s = await api("/api/support/unread"); state.supportUnread = s.user || 0; state.adminSupportUnread = s.admin || 0; renderNav(); } catch { /* ignore */ }
}

/** Udseendet (auto/lys/mørk) gemmes pr. enhed i localStorage – ikke på kontoen. «Automatisk» = ingen gemt værdi.
 *  Valget afspejles i <meta name="color-scheme"> (browserens egne kontroller, rullebjælker, formularfelter) og i
 *  data-theme (appens farvetokens). Det inline script i index.html gør det samme før første visning. */
function applyTheme() {
  let t = null;
  try { t = localStorage.getItem("theme"); } catch { /* lager blokeret: følg systemet */ }
  if (t !== "light" && t !== "dark") t = null;
  document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", t || "light dark");
  if (t) document.documentElement.setAttribute("data-theme", t);
  else document.documentElement.removeAttribute("data-theme");
}
// Valget kan ændres i en anden fane
addEventListener("storage", (e) => { if (e.key === "theme" || e.key === null) applyTheme(); });

/** Brugermenuen er en popover="auto" (#usermenu i index.html) styret af #userbtn via popovertarget: browseren giver
 *  lys afvisning, Esc, aria-expanded på knappen og fokus tilbage. Indholdet bygges, hver gang den åbnes. */
const userMenu = () => $("#usermenu");
const closeMenu = () => { const m = userMenu(); if (hasPopover) { if (m.matches(":popover-open")) m.hidePopover(); } else m.classList.add("hide"); };

function fillMenu() {
  const u = state.user, menu = userMenu();
  if (!u) return;
  const close = closeMenu;
  const mod = moderationLink(), adm = adminLink();
  menu.replaceChildren(
    h("div", { class: "who" }, h("b", {}, u.navn || u.obserkode), h("div", { class: "muted" }, `${u.obserkode} · ${u.role}`)),
    h("a", { href: "#/indtast", onclick: close, html: `${icon("list")} Mine indtastninger` }),
    h("a", { href: "#/alarmer", onclick: close, html: `${icon("bell")} Alarmer og områder` }),
    h("a", { href: "#/profil", onclick: close, html: `${icon("user")} Profil og indstillinger` }),
    h("a", { href: "#/support", onclick: close, html: `${icon("chat")} Feedback og support${state.supportUnread ? ` <span class="badge-inline">${state.supportUnread}</span>` : ""}` }),
    h("a", { href: `#/lister`, onclick: close, html: `${icon("list")} Mine lister` }),
    mod ? h("a", { href: mod.href, onclick: close, html: `${icon("comment")} ${mod.long}` }) : null,
    adm ? h("a", { href: adm.href, onclick: close, html: `${icon("shield")} ${adm.long}` }) : null,
    h("button", { type: "button", onclick: () => { close(); logout(); }, html: `${icon("logout")} Log ud` }),
    h("div", { class: "muted", style: { fontSize: ".72rem", padding: "6px 10px 2px" } }, `Version ${BUILD}`));
}

function initMenu() {
  const menu = userMenu(), btn = $("#userbtn");
  const isOpen = () => hasPopover ? menu.matches(":popover-open") : !menu.classList.contains("hide");
  if (hasPopover) {
    menu.addEventListener("beforetoggle", (e) => { if (e.newState === "open") { fillMenu(); backLayer(menu, closeMenu, isOpen); } });
    return;
  }
  // Uden Popover API: knappen åbner/lukker selv, og et klik udenfor lukker
  menu.removeAttribute("popover");
  menu.classList.add("hide");
  btn.addEventListener("click", () => {
    if (isOpen()) { closeMenu(); return; }
    fillMenu(); menu.classList.remove("hide"); backLayer(menu, closeMenu, isOpen);
  });
  document.addEventListener("click", (e) => { if (isOpen() && !menu.contains(e.target) && !btn.contains(e.target)) closeMenu(); }, true);
}

async function logout() {
  import("./tur-data.js").then(m => m.trackStop()).catch(() => {});  // en rute i gang må ikke fortsætte som ejerløs GPS-optagelse efter logout
  await stopAppPush();
  await api("/api/auth/logout", { method: "POST" });
  state.user = null;
  await (await import("./offline.js")).forgetUser();
  syncSharing(); renderUser(); renderNav(); live.stop();
  location.hash = "#/login";
  toast("Du er logget ud");
}

/** Notifikationer der peger på siden man står på (fx tråden) markeres som læst i indbakken. */
async function markSeen(url, ids) {
  try {
    const r = await api("/api/notifications/read", { method: "POST", body: ids ? { ids } : { url } });
    if (r.changed) { state.unread = r.unread; renderUser(); }
  } catch { /* offline */ }
}

async function openNotifications() {
  const { modal } = await import("./core.js");
  const list = h("div", { class: "notif-list" }, h("div", { class: "skeleton" }));
  const m = modal("Notifikationer", h("div", {},
    h("div", { class: "row between mb" }, h("a", { href: "#/alarmer", onclick: () => m.close() }, "Indstil alarmer →"),
      h("button", { class: "btn sm", onclick: async () => { await api("/api/notifications/read", { method: "POST" }); state.unread = 0; renderUser(); m.close(); } }, "Markér alle som læst")),
    list));
  const data = await api("/api/notifications?limit=80");
  list.replaceChildren(...(data.items.length ? data.items.map(n => h("a", { class: `notif ${n.read ? "" : "unread"}`, href: n.url, onclick: () => { m.close(); if (!n.read) markSeen(null, [n.id]); } },
    h("b", {}, n.title), h("span", {}, n.body), h("small", {}, ago(n.created_at) + (n.pushed ? " · push" : "")))) :
    [h("p", { class: "muted" }, "Ingen notifikationer endnu. Opret en alarm for at få besked om sjældne fugle.")]));
}

// Rullepositionen gemmes i det enkelte historikpunkt (history.state), så tilbage (Androids knap, browseren) fører til
// samme sted på forrige side. En ny side åbnes øverst.
history.scrollRestoration = "manual";
let routing = false, saveTimer = null;
addEventListener("scroll", () => {
  if (routing || saveTimer) return;
  const href = location.href;  // en gemning, der først når frem efter et sideskift, må ikke lande på den nye side
  saveTimer = setTimeout(() => {
    saveTimer = null;
    if (!routing && location.href === href) history.replaceState({ ...history.state, y: Math.round(scrollY) }, "");
  }, 150);
}, { passive: true });

/** Rul til y. Siderne henter ofte indhold efter visning, så der prøves igen, mens siden vokser – højst 3 s, og ikke
 *  længere end til brugeren selv rører skærmen. */
function restoreScroll(y) {
  window.scrollTo(0, y || 0);
  if (!y || Math.abs(scrollY - y) < 2) return;
  const ro = new ResizeObserver(() => { window.scrollTo(0, y); if (Math.abs(scrollY - y) < 2) stop(); });
  const stop = () => { ro.disconnect(); clearTimeout(timer); removeEventListener("touchstart", stop); removeEventListener("wheel", stop); };
  const timer = setTimeout(stop, 3000);
  ro.observe($("#view"));
  addEventListener("touchstart", stop, { passive: true });
  addEventListener("wheel", stop, { passive: true });
}

// Data, en side skal bruge, hentes samtidig med sidens moduler (og allerede ved tryk på et link), så ventetiderne ikke
// lægges oven i hinanden. Siden selv kalder api() med samme sti og får det forhåndshentede svar.
const PREFETCH = [
  [/^\/traad\/(\d{4}-\d{2}-\d{2})\/(\d+)\/(\d+)$/, (m) => `/api/thread/${m[1]}/${m[2]}/${m[3]}`],
];

function prefetchFor(path) {
  for (const [re, url] of PREFETCH) {
    const m = path.match(re);
    if (m) { prefetch(url(m)); return; }
  }
}

// Tryk på et link til en tråd: hent data med det samme (ikke ved mus over – tråden kan sætte opslag i DOFbasens kø i gang)
function prefetchLink(e) {
  const a = e.target.closest?.('a[href^="#/traad/"]');
  if (a && state.user) prefetchFor(a.getAttribute("href").slice(1).split("?")[0]);
}

async function route() {
  routing = true;
  const y = history.state?.y;
  try { await show(y); } finally { routing = false; }
}

/** Hvor kom man fra? Gemmes i det nye historikpunkt (history.state.from), første gang det vises, så tilbage-linket
 *  øverst på fx en tråd kan vise og føre til forrige side – også når man vender tilbage til punktet senere. */
let lastHash = null;
function rememberFrom() {
  const prev = lastHash;
  lastHash = location.hash || "#/";
  if (history.state && "from" in history.state) return;  // besøgt før (tilbage/frem/genindlæs): behold
  let from = null;
  if (prev && prev !== lastHash) {
    const p = prev.split("?")[0] || "#/";
    const exact = navItem(p);
    const h1 = $("#view h1")?.textContent.trim();
    const near = navItem(activeHref(p, NAV.map(n => n[0])) || "");
    const label = p === "#/" || p === "#" ? "Mit overblik" : exact ? exact[2] : h1 && h1.length <= 40 ? h1 : near ? near[2] : "Tilbage";
    from = { hash: prev, label: label.replace(/<[^>]*>/g, "").trim() };
  }
  history.replaceState({ ...history.state, from }, "");
}

/** Efter et sideskift: fanebladets titel følger sidens overskrift (medmindre siden selv har sat en), og fokus flyttes til
 *  <main>, så skærmlæsere og tastatur begynder på den nye side – ikke ved første visning, og ikke hvis siden selv har
 *  sat fokus i et felt. */
let firstView = true;
function announceView(view, titleBefore) {
  if (document.title === titleBefore) {
    const h1 = view.querySelector("h1")?.textContent.trim();
    document.title = h1 ? `${h1} – DOF.tool` : "DOF.tool";
  }
  if (firstView) { firstView = false; return; }
  const a = document.activeElement;
  if (!a || a === document.body || !view.contains(a)) view.focus({ preventScroll: true });
}

async function show(y) {
  const [path] = (location.hash.slice(1) || "/").split("?");
  rememberFrom();
  document.querySelector(".more-sheet")?.remove();
  // Hele portalen kræver login – uden login vises kun forsiden med login
  if (!state.user && path !== "/login") {
    location.replace("#/login" + (path && path !== "/" ? `?next=${encodeURIComponent("#" + location.hash.slice(1))}` : ""));
    return;
  }
  // Artschips i gemt opslags- og artikeltekst (body_html) blev før renderet med #/arter/<artnr>; artens side er #/art/<artnr>
  const oldArt = path.match(/^\/arter\/(\d+)$/);
  if (oldArt) { location.replace(`#/art/${oldArt[1]}`); return; }
  renderNav();
  if (state.user) trackView(path);
  if (state.user && state.unread) markSeen("/#" + path);
  closeMenu();
  for (const [re, loader] of ROUTES) {
    const m = path.match(re);
    if (!m) continue;
    if (typeof cleanup === "function") { try { cleanup(); } catch { /* ignore */ } }
    cleanup = null;
    const view = $("#view");
    prefetchFor(path);
    // Tager det mere end et øjeblik at hente siden første gang, vises et skelet, så man kan se, at der sker noget
    const wait = setTimeout(() => view.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } })), 150);
    try {
      const mod = await loader();
      clearTimeout(wait);
      view.replaceChildren();
      const titleBefore = document.title;
      cleanup = await mod.render(view, m.slice(1));
      announceView(view, titleBefore);
    } catch (err) {
      clearTimeout(wait);
      console.error(err);
      view.replaceChildren(h("div", { class: "notice err" }, "Siden kunne ikke vises: " + err.message));
    }
    restoreScroll(y);
    return;
  }
  location.hash = "#/";
}

async function boot() {
  initMenu();
  $("#bell").addEventListener("click", openNotifications);
  window.addEventListener("hashchange", route);
  document.addEventListener("pointerdown", prefetchLink, { passive: true });
  await refreshUser();
  if (state.user && !state.user.view_as) live.start();  // «se som bruger» opretter ikke SSE-strømmen
  live.on("notification", (d) => { state.unread += 1; renderUser(); toast(`🔔 ${d.title}`); });
  live.onStatus((on) => $("#live").classList.toggle("on", on));
  live.on("resume", () => { if (state.user && !state.user.view_as) refreshUnread(); });  // tilbage fra baggrunden
  await route();
  // Tråde er det, man oftest åbner: hent modulet i baggrunden, når appen er i ro, så første tråd ikke venter på det
  const idle = window.requestIdleCallback || ((f) => setTimeout(f, 2000));
  if (state.user) idle(() => import("./views/thread.js").catch(() => {}));
  const splash = $("#splash");
  if (splash) { splash.classList.add("out"); setTimeout(() => splash.remove(), 300); }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(trustedScriptURL("/sw.js")).then((reg) => reg.update()).catch(() => {});
    // Ny version installeret: genindlæs én gang, så man ikke kører på gammel kode fra cachen
    if (navigator.serviceWorker.controller) {
      let reloaded = false;
      navigator.serviceWorker.addEventListener("controllerchange", () => { if (!reloaded) { reloaded = true; location.reload(); } });
    }
    navigator.serviceWorker.addEventListener("message", (e) => { if (e.data?.type === "navigate") location.hash = e.data.url.replace(/^.*#/, "#"); });
  }
}

/** Skærmtastatur på telefoner: rul feltet fri af bundmenuen og sticky bundknapper (Gem/Annullér), som ellers kan dække
 *  det nederste felt i en formular. Bundmenuen bliver stående. */
function keyboardAware() {
  const touch = matchMedia("(pointer: coarse)");
  const typing = (el) => el?.matches?.("textarea, select, [contenteditable=true], input:not([type=checkbox], [type=radio], [type=button], [type=submit], [type=range], [type=color], [type=file])");
  document.addEventListener("focusin", (e) => {
    if (!touch.matches || !typing(e.target)) return;
    const el = e.target;
    // Når tastaturet er åbent (og viewporten krympet), tjekkes om feltet er dækket af en sticky/fast bjælke
    setTimeout(() => {
      if (document.activeElement !== el) return;
      const vh = window.visualViewport?.height || window.innerHeight;
      const r = el.getBoundingClientRect();
      const foot = [...document.querySelectorAll(".sheet-foot, .tur-bar, .bottomnav")].filter(f => f.offsetParent && !f.contains(el))
        .reduce((m, f) => Math.max(m, vh - f.getBoundingClientRect().top), 0);
      if (r.bottom > vh - foot - 8 || r.top < 0) el.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 350);
  });
}

export { renderUser, renderNav, applyTheme };
keyboardAware();
boot();
