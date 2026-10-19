import { api, state, h, $, icon, toast, ago, trackView } from "./core.js";
import { live } from "./live.js";

// Build-id fra den versionerede sti (/v/<build>/js/app.js) – vises i brugermenuen
const BUILD = new URL(import.meta.url).pathname.split("/")[2] || "dev";

const NAV = [
  ["#/", "now", "Nu"],
  ["#/kort", "map", "Kort"],
  ["#/kalender", "calendar", "Kalender"],
  ["#/ture", "flag", "Ture"],
  ["#/lister", "list", "Lister"],
  ["#/traek", "trend", "Træk"],
  ["#/laer", "learn", "Læring"],
  ["#/alarmer", "bell", "Alarmer"],
];
const navItem = (href) => NAV.find(n => n[0] === href);

const ROUTES = [
  [/^\/?$/, () => import("./views/feed.js")],
  [/^\/kort$/, () => import("./views/map.js")],
  [/^\/traad\/(\d{4}-\d{2}-\d{2})\/(\d+)\/(\d+)$/, () => import("./views/thread.js")],
  [/^\/lister(?:\/(.+))?$/, () => import("./views/lists.js")],
  [/^\/traek$/, () => import("./views/pheno.js")],
  [/^\/art\/(\d+)$/, () => import("./views/species.js")],
  [/^\/lok\/(\d+)$/, () => import("./views/location.js")],
  [/^\/laer(?:\/(.+))?$/, () => import("./views/learn.js")],
  [/^\/alarmer$/, () => import("./views/alerts.js")],
  [/^\/profil$/, () => import("./views/profile.js")],
  [/^\/admin$/, () => import("./views/admin.js")],
  [/^\/login$/, () => import("./views/login.js")],
  [/^\/indtast(?:\/tur\/(\d+))?$/, () => import("./views/report.js")],
  [/^\/support$/, () => import("./views/support.js")],
  [/^\/ture(?:\/(\d+))?$/, () => import("./views/events.js")],
  [/^\/kalender(?:\/(\d+|mine))?$/, () => import("./views/calendar.js")],
];

let cleanup = null;

function renderNav() {
  const path = "#" + (location.hash.slice(1).split("?")[0] || "/");
  const link = ([href, ic, label], cls = "") => {
    const base = href.split("?")[0];
    const active = base === "#/" ? path === "#/" || path === "#" : path.startsWith(base);
    return h("a", { href, class: `${cls} ${active ? "active" : ""}`.trim(), html: `${icon(ic)}<span>${label}</span>` });
  };
  const items = NAV.map(n => link(n));
  document.body.classList.toggle("anon", !state.user);
  // Mobil: Nu · Kort · [Indtast] · Kalender · Mere (resten samlet i et ark)
  const inMore = ["#/ture", "#/lister", "#/traek", "#/laer", "#/alarmer", "#/profil", "#/support", "#/admin"].some(p => path.startsWith(p));
  const more = h("a", { href: "javascript:void 0", class: inMore ? "active" : "", onclick: (e) => { e.preventDefault(); openMore(); }, html: `${icon("more")}<span>Mere</span>` });
  $("#bottomnav").replaceChildren(...(state.user
    ? [link(navItem("#/")), link(navItem("#/kort")), link(["#/indtast?ny=1", "plus", "Indtast"], "cta"), link(navItem("#/kalender")), more]
    : []));
  const side = [...items];
  if (state.user) {
    side.unshift(link(["#/indtast?ny=1", "plus", "Indtast"], "cta"));
    side.push(h("div", { class: "sep" }));
    side.push(h("a", { href: "#/profil", class: path.startsWith("#/profil") ? "active" : "", html: `${icon("user")}<span>Profil</span>` }));
    side.push(h("a", { href: "#/support", class: path.startsWith("#/support") ? "active" : "", html: `${icon("chat")}<span>Support${state.supportUnread ? ` <span class="badge-inline">${state.supportUnread}</span>` : ""}</span>` }));
    if (["admin", "superadmin"].includes(state.user.role))
      side.push(h("a", { href: "#/admin", class: path.startsWith("#/admin") ? "active" : "", html: `${icon("shield")}<span>Admin${state.adminSupportUnread ? ` <span class="badge-inline">${state.adminSupportUnread}</span>` : ""}</span>` }));
    else if (["moderator", "editor"].includes(state.user.role))
      side.push(h("a", { href: "#/admin?tab=moderation", class: path.startsWith("#/admin") ? "active" : "", html: `${icon("shield")}<span>Moderation</span>` }));
  }
  $("#sidenav").replaceChildren(...side);
}

function openMore() {
  document.querySelector(".more-sheet")?.remove();
  const close = () => sheet.remove();
  const tile = (href, ic, label, badge) => h("a", { href, class: "more-tile", onclick: close, html: `${icon(ic)}<span>${label}${badge ? ` <span class="badge-inline">${badge}</span>` : ""}</span>` });
  const u = state.user;
  const sheet = h("div", { class: "more-sheet", onclick: (e) => { if (e.target === sheet) close(); } },
    h("div", { class: "more-panel" },
      h("div", { class: "more-grid" },
        tile("#/ture", "flag", "DOF-ture"), tile("#/lister", "list", "Lister"), tile("#/traek", "trend", "Træk"), tile("#/laer", "learn", "Læring"),
        tile("#/indtast", "edit", "Mine indtastninger"), tile("#/kalender/mine", "calendar", "Mine planer"), tile("#/alarmer", "bell", "Alarmer"),
        tile("#/profil", "user", "Profil"), tile("#/support", "chat", "Support", state.supportUnread),
        u && ["admin", "superadmin"].includes(u.role) ? tile("#/admin", "shield", "Admin", state.adminSupportUnread)
          : u && ["moderator", "editor"].includes(u.role) ? tile("#/admin?tab=moderation", "shield", "Moderation") : null),
      h("button", { class: "btn ghost", style: { width: "100%", marginTop: "8px" }, onclick: close }, "Luk")));
  document.body.append(sheet);
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

export async function refreshUser() {
  try { state.user = (await api("/api/me")).user; } catch { state.user = null; }
  applyTheme();
  renderUser();
  renderNav();
  if (state.user) refreshUnread();
}

export async function refreshUnread() {
  try { state.unread = (await api("/api/notifications?limit=1")).unread; renderUser(); } catch { /* offline */ }
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
  const menu = h("div", { class: "menu" },
    h("div", { class: "who" }, h("b", {}, u.navn || u.obserkode), h("div", { class: "muted" }, `${u.obserkode} · ${u.role}`)),
    h("a", { href: "#/indtast", onclick: close, html: `${icon("list")} Mine indtastninger` }),
    h("a", { href: "#/alarmer", onclick: close, html: `${icon("bell")} Alarmer og områder` }),
    h("a", { href: "#/profil", onclick: close, html: `${icon("user")} Profil, matrikler og indstillinger` }),
    h("a", { href: "#/support", onclick: close, html: `${icon("chat")} Feedback og support${state.supportUnread ? ` <span class="badge-inline">${state.supportUnread}</span>` : ""}` }),
    h("a", { href: `#/lister`, onclick: close, html: `${icon("list")} Mine lister` }),
    ["moderator", "editor"].includes(u.role) ? h("a", { href: "#/admin?tab=moderation", onclick: close, html: `${icon("shield")} Moderation` }) : null,
    ["admin", "superadmin"].includes(u.role) ? h("a", { href: "#/admin", onclick: close, html: `${icon("shield")} Administration` }) : null,
    h("button", { onclick: async () => { close(); await api("/api/auth/logout", { method: "POST" }); state.user = null; renderUser(); renderNav(); live.stop(); location.hash = "#/login"; toast("Du er logget ud"); }, html: `${icon("logout")} Log ud` }),
    h("div", { class: "muted", style: { fontSize: ".72rem", padding: "6px 10px 2px" } }, `Version ${BUILD}`));
  document.body.append(menu);
  setTimeout(() => document.addEventListener("click", outside, true));
}

async function openNotifications() {
  const { modal } = await import("./core.js");
  const list = h("div", { class: "notif-list" }, h("div", { class: "skeleton" }));
  const m = modal("Notifikationer", h("div", {},
    h("div", { class: "row between mb" }, h("a", { href: "#/alarmer", onclick: () => m.close() }, "Indstil alarmer →"),
      h("button", { class: "btn sm", onclick: async () => { await api("/api/notifications/read", { method: "POST" }); state.unread = 0; renderUser(); m.close(); } }, "Markér alle som læst")),
    list));
  const data = await api("/api/notifications?limit=80");
  list.replaceChildren(...(data.items.length ? data.items.map(n => h("a", { class: `notif ${n.read ? "" : "unread"}`, href: n.url, onclick: () => m.close() },
    h("b", {}, n.title), h("span", {}, n.body), h("small", {}, ago(n.created_at) + (n.pushed ? " · push" : "")))) :
    [h("p", { class: "muted" }, "Ingen notifikationer endnu. Opret en alarm for at få besked om sjældne fugle.")]));
}

async function route() {
  const [path] = (location.hash.slice(1) || "/").split("?");
  document.querySelector(".more-sheet")?.remove();
  // Hele portalen kræver login – uden login vises kun forsiden med login
  if (!state.user && path !== "/login") {
    location.replace("#/login" + (path && path !== "/" ? `?next=${encodeURIComponent("#" + location.hash.slice(1))}` : ""));
    return;
  }
  renderNav();
  if (state.user) trackView(path);
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
    window.scrollTo(0, 0);
    return;
  }
  location.hash = "#/";
}

async function boot() {
  $("#userbtn").addEventListener("click", openMenu);
  $("#bell").addEventListener("click", openNotifications);
  window.addEventListener("hashchange", route);
  await refreshUser();
  if (state.user) live.start();
  live.on("notification", (d) => { state.unread += 1; renderUser(); toast(`🔔 ${d.title}`); });
  live.onStatus((on) => $("#live").classList.toggle("on", on));
  await route();
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

export { renderUser, renderNav, applyTheme };
boot();
