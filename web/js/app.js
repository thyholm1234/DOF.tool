import { api, state, h, $, icon, toast, ago, trackView, adminLink, moderationLink, hasTab, backLayer } from "./core.js";
import { live } from "./live.js";
import { syncSharing, syncPush, stopAppPush } from "./native.js";
import { initSelectMenu } from "./select-menu.js";

// Build-id fra den versionerede sti (/v/<build>/js/app.js) – vises i brugermenuen
const BUILD = new URL(import.meta.url).pathname.split("/")[2] || "dev";
initSelectMenu();

// Navigation i grupper efter hvad brugeren vil – samme rækkefølge i sidepanelet (desktop) og i "Mere"-arket (mobil).
// Hvert punkt: [adresse, ikon, tekst, synlig?]; uden synlig? vises punktet altid.
const usr = () => state.user || {};
const SECTIONS = [
  ["Nu", [["#/", "now", "Nu"], ["#/kort", "map", "Kort"]]],
  ["Opslag", [["#/arter", "bird", "Arter"], ["#/lokaliteter", "pin", "Lokaliteter"],
    ["#/observator", "user", "Observatører", () => usr().profiler], ["#/soeg", "search", "Søg i observationer", () => usr().search]]],
  ["Analyse", [["#/traek", "trend", "Træk & fænologi"], ["#/statistik", "chart", "Statistik", () => usr().statistik]]],
  ["Lister", [["#/lister", "list", "Lister"], ["#/matrikel", "home", "Matrikel"],
    ["#/krydslister", "list", "Krydslister", () => usr().krydslister?.features?.krydslister]]],
  ["Fotos & artikler", [["#/fotos", "camera", "Fotos", () => usr().fotos?.features?.fotos],
    ["#/artikler", "news", "Artikler", () => usr().artikler?.features?.artikler]]],
  ["Felt", [["#/kalender", "calendar", "Kalender"], ["#/ture", "flag", "DOF-ture"]]],
  ["Lær", [["#/laer", "learn", "Læring"], ["#/mobgang", "sound", "Mobgang"]]],
  ["Mit", [["#/indtast", "edit", "Indtastninger"], ["#/fotos/mine", "camera", "Mine fotos", () => usr().fotos?.features?.fotos],
    ["#/kalender/mine", "calendar", "Mine planer"], ["#/alarmer", "bell", "Alarmer"],
    // DKU med antal sager med ulæste beskeder, når modulet er synligt for brugeren
    ["#/dku", "check", () => `DKU${usr().dku?.unread ? ` <span class="badge-inline">${+usr().dku.unread}</span>` : ""}`, () => usr().dku?.visible]]],
];
// Showcases (nye udgaver af dofbasen.dk og netfugl.dk) står nederst, efter Admin
const SHOWCASES = [["#/dofbasen", "db", "DOFbasen", () => usr().dofbasen?.features?.dofbasen], ["#/netfugl", "bird", "Netfugl", () => usr().netfugl?.features?.netfugl]];
const visible = (items) => items.filter(n => !n[3] || n[3]()).map(([href, ic, label]) => [href, ic, typeof label === "function" ? label() : label]);
/** Menuens grupper for den indloggede – punkter bag funktionsflag vises kun, når de er synlige for brugeren. */
const sections = () => SECTIONS.map(([t, items]) => [t, visible(items)]).filter(([, items]) => items.length);
const showcases = () => visible(SHOWCASES);
const NAV = [...SECTIONS, ["", SHOWCASES]].flatMap(([, items]) => items).map(([href, ic, label]) => [href, ic, typeof label === "function" ? "DKU" : label]);
const navItem = (href) => NAV.find(n => n[0] === href);
const BOTTOM = ["#/", "#/traek", "#/lister"];  // mobilens faste knapper (plus Indtast og Mere)

/** Det mest specifikke menupunkt der matcher stien (så #/kalender/mine ikke også markerer Kalender). */
function activeHref(path, hrefs) {
  // en arts side hører under Arter, en lokalitets side under Lokaliteter
  const p = path === "#" ? "#/" : path.startsWith("#/art/") ? "#/arter" : path.startsWith("#/lok/") ? "#/lokaliteter" : path;
  return hrefs.filter(href => href === "#/" ? p === "#/" : p === href || p.startsWith(href + "/") || p.startsWith(href + "?"))
    .sort((a, b) => b.length - a.length)[0];
}

const ROUTES = [
  [/^\/?$/, () => import("./views/feed.js")],
  [/^\/traad\/(\d{4}-\d{2}-\d{2})\/(\d+)\/(\d+)$/, () => import("./views/thread.js")],
  [/^\/lister(?:\/(.+))?$/, () => import("./views/lists.js")],
  [/^\/matrikel$/, () => import("./views/matrikel.js")],
  [/^\/traek$/, () => import("./views/pheno.js")],
  [/^\/(?:kort|puls)$/, () => import("./views/kort.js")],  // #/puls er den gamle adresse
  [/^\/arter$/, () => import("./views/arter.js")],
  [/^\/art\/(\d+)$/, () => import("./views/species.js")],
  [/^\/lokaliteter$/, () => import("./views/lokaliteter.js")],
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
  [/^\/krydslister(?:\/(.+))?$/, () => import("./views/krydslister.js")],
  [/^\/nyheder\/(\d+)$/, () => import("./views/nyhed.js")],
];

let cleanup = null;

function renderNav() {
  const path = "#" + (location.hash.slice(1).split("?")[0] || "/");
  const current = activeHref(path, [...NAV.map(n => n[0]), "#/profil", "#/support", "#/admin", "#/moderation"]);
  const link = ([href, ic, label], cls = "") =>
    h("a", { href, class: `${cls} ${href === current ? "active" : ""}`.trim(), html: `${icon(ic)}<span>${label}</span>` });
  document.body.classList.toggle("anon", !state.user);
  // Mobil: Nu · Træk · [Indtast] · Lister · Mere (resten i arket, i samme grupper som sidepanelet)
  const inMore = current && !BOTTOM.includes(current);
  const more = h("a", { href: "javascript:void 0", class: inMore ? "active" : "", onclick: (e) => { e.preventDefault(); openMore(); }, html: `${icon("more")}<span>Mere</span>` });
  $("#bottomnav").replaceChildren(...(state.user
    ? [link(navItem("#/")), link(["#/traek", "trend", "Træk"]), link(["#/indtast/tur/ny", "plus", "Indtast"], "cta"), link(navItem("#/lister")), more]
    : []));
  const side = [];
  for (const [title, items] of sections()) side.push(h("div", { class: "nav-h" }, title), ...items.map(n => link(n)));
  if (state.user) {
    side.unshift(link(["#/indtast/tur/ny", "plus", "Indtast"], "cta"));
    side.push(h("div", { class: "sep" }));
    side.push(h("a", { href: "#/profil", class: current === "#/profil" ? "active" : "", html: `${icon("user")}<span>Profil</span>` }));
    side.push(h("a", { href: "#/support", class: current === "#/support" ? "active" : "", html: `${icon("chat")}<span>Support${state.supportUnread ? ` <span class="badge-inline">${state.supportUnread}</span>` : ""}</span>` }));
    // Ulæste samtaler vises på Admin for dem med support-modulet, ellers på Moderation
    const adm = adminLink(), mod = moderationLink(), unread = state.adminSupportUnread, onAdmin = adm && hasTab("support");
    const badge = (n) => n ? ` <span class="badge-inline">${n}</span>` : "";
    if (mod) side.push(h("a", { href: mod.href, class: current === "#/moderation" ? "active" : "", html: `${icon("check")}<span>${mod.label}${badge(onAdmin ? 0 : unread)}</span>` }));
    if (adm) side.push(h("a", { href: adm.href, class: current === "#/admin" ? "active" : "", html: `${icon("shield")}<span>${adm.label}${badge(onAdmin ? unread : 0)}</span>` }));
  }
  const demo = showcases();
  if (demo.length) side.push(h("div", { class: "nav-h" }, "Showcases"), ...demo.map(n => link(n)));
  $("#sidenav").replaceChildren(...side);
}

function openMore() {
  document.querySelector(".more-sheet")?.remove();
  const close = () => sheet.remove();
  const tile = (href, ic, label, badge) => h("a", { href, class: "more-tile", onclick: close, html: `${icon(ic)}<span>${label}${badge ? ` <span class="badge-inline">${badge}</span>` : ""}</span>` });
  const u = state.user;
  const sheet = h("div", { class: "more-sheet", onclick: (e) => { if (e.target === sheet) close(); } },
    h("div", { class: "more-panel" },
      // Samme grupper som sidepanelet; punkterne fra bundmenuen er udeladt
      ...sections().map(([title, items]) => [title, items.filter(n => !BOTTOM.includes(n[0]))])
        .map(([title, items]) => items.length ? [h("div", { class: "more-h" }, title), h("div", { class: "more-grid" }, ...items.map(([href, ic, label]) => tile(href, ic, label)))] : [])
        .flat(),
      h("div", { class: "more-h" }, "Konto"),
      h("div", { class: "more-grid" },
        tile("#/profil", "user", "Profil"), tile("#/support", "chat", "Support", state.supportUnread),
        u && moderationLink() ? tile(moderationLink().href, "check", moderationLink().label, hasTab("support") ? 0 : state.adminSupportUnread) : null,
        u && adminLink() ? tile(adminLink().href, "shield", adminLink().label, hasTab("support") ? state.adminSupportUnread : 0) : null),
      // Showcases nederst
      ...(showcases().length ? [h("div", { class: "more-h" }, "Showcases"), h("div", { class: "more-grid" }, ...showcases().map(([href, ic, label]) => tile(href, ic, label)))] : []),
      h("button", { class: "btn ghost", style: { width: "100%", marginTop: "8px" }, onclick: close }, "Luk")));
  document.body.append(sheet);
  backLayer(sheet, close);
}

function renderUser() {
  const u = state.user;
  $("#loginbtn").classList.toggle("hide", !!u);
  $("#userbtn").classList.toggle("hide", !u);
  $("#bell").classList.toggle("hide", !u);
  if (!u) return;
  const initials = (u.navn || u.obserkode).split(/\s+/).map(s => s[0]).join("").slice(0, 2).toUpperCase();
  $("#userbtn").textContent = initials;
  $("#bell").innerHTML = icon("bell") + (state.unread ? `<span class="badge">${state.unread > 99 ? "99+" : state.unread}</span>` : "");
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

function applyTheme() {
  const t = state.user?.prefs?.theme || localStorage.getItem("theme") || "auto";
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}

function openMenu() {
  document.querySelector(".menu")?.remove();
  const u = state.user;
  const close = () => { menu.remove(); document.removeEventListener("click", outside, true); };
  const outside = (e) => { if (!menu.contains(e.target) && e.target !== $("#userbtn")) close(); };
  const mod = moderationLink(), adm = adminLink();
  const menu = h("div", { class: "menu" },
    h("div", { class: "who" }, h("b", {}, u.navn || u.obserkode), h("div", { class: "muted" }, `${u.obserkode} · ${u.role}`)),
    h("a", { href: "#/indtast", onclick: close, html: `${icon("list")} Mine indtastninger` }),
    h("a", { href: "#/alarmer", onclick: close, html: `${icon("bell")} Alarmer og områder` }),
    h("a", { href: "#/profil", onclick: close, html: `${icon("user")} Profil, matrikler og indstillinger` }),
    h("a", { href: "#/support", onclick: close, html: `${icon("chat")} Feedback og support${state.supportUnread ? ` <span class="badge-inline">${state.supportUnread}</span>` : ""}` }),
    h("a", { href: `#/lister`, onclick: close, html: `${icon("list")} Mine lister` }),
    mod ? h("a", { href: mod.href, onclick: close, html: `${icon("check")} ${mod.long}` }) : null,
    adm ? h("a", { href: adm.href, onclick: close, html: `${icon("shield")} ${adm.long}` }) : null,
    h("button", { onclick: () => { close(); logout(); }, html: `${icon("logout")} Log ud` }),
    h("div", { class: "muted", style: { fontSize: ".72rem", padding: "6px 10px 2px" } }, `Version ${BUILD}`));
  document.body.append(menu);
  backLayer(menu, close);
  setTimeout(() => document.addEventListener("click", outside, true));
}

async function logout() {
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

async function route() {
  routing = true;
  const y = history.state?.y;
  try { await show(y); } finally { routing = false; }
}

async function show(y) {
  const [path] = (location.hash.slice(1) || "/").split("?");
  document.querySelector(".more-sheet")?.remove();
  // Hele portalen kræver login – uden login vises kun forsiden med login
  if (!state.user && path !== "/login") {
    location.replace("#/login" + (path && path !== "/" ? `?next=${encodeURIComponent("#" + location.hash.slice(1))}` : ""));
    return;
  }
  renderNav();
  if (state.user) trackView(path);
  if (state.user && state.unread) markSeen("/#" + path);
  document.querySelector(".menu")?.remove();
  for (const [re, loader] of ROUTES) {
    const m = path.match(re);
    if (!m) continue;
    if (typeof cleanup === "function") { try { cleanup(); } catch { /* ignore */ } }
    cleanup = null;
    const view = $("#view");
    try {
      const mod = await loader();
      view.replaceChildren();
      cleanup = await mod.render(view, m.slice(1));
    } catch (err) {
      console.error(err);
      view.replaceChildren(h("div", { class: "notice err" }, "Siden kunne ikke vises: " + err.message));
    }
    restoreScroll(y);
    return;
  }
  location.hash = "#/";
}

async function boot() {
  $("#userbtn").addEventListener("click", openMenu);
  $("#bell").addEventListener("click", openNotifications);
  window.addEventListener("hashchange", route);
  await refreshUser();
  if (state.user && !state.user.view_as) live.start();  // «se som bruger» opretter ikke SSE-strømmen
  live.on("notification", (d) => { state.unread += 1; renderUser(); toast(`🔔 ${d.title}`); });
  live.onStatus((on) => $("#live").classList.toggle("on", on));
  await route();
  const splash = $("#splash");
  if (splash) { splash.classList.add("out"); setTimeout(() => splash.remove(), 300); }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").then((reg) => reg.update()).catch(() => {});
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
