// Administration og Moderation: skallen med faner. Hver fane ligger i views/admin/<fane>.js og hentes først, når den
// åbnes – så man ikke henter hele admin-panelet for at se én fane.
import { h, seg, params, setParams, errorBox, hasTab, ADMIN_TABS, MOD_TABS } from "../core.js";

/** Før lå moderationsfanerne under Admin – gamle links (fx i notifikationer) sendes videre. */
const MOVED_TO_MODERATION = new Set(MOD_TABS.map(t => t[0]).concat("kategorier"));

// fane → (body, every) => tegn fanen. every(fn, ms) kører fn nu og derefter med intervallet, indtil fanen forlades.
const TABS = {
  drift: () => import("./admin/drift.js").then(m => (body, every) => m.renderDrift(body, every)),
  data: () => import("./admin/data.js").then(m => (body, every) => m.dataPanel(body, "dof", (f) => every(f, 20000))),
  vejr: () => import("./admin/data.js").then(m => (body, every) => m.dataPanel(body, "weather", (f) => every(f, 5000))),
  aktivitet: () => import("./admin/aktivitet.js").then(m => (body) => m.activityPanel(body)),
  support: () => import("./admin/support.js").then(m => (body) => m.supportPanel(body)),
  moderation: () => import("./admin/moderation.js").then(m => (body) => m.moderation(body)),
  ture: () => import("./admin/ture.js").then(m => (body) => m.portalEvents(body)),
  brugere: () => import("./admin/brugere.js").then(m => (body) => m.users(body)),
  roller: () => import("./admin/roller.js").then(m => (body) => m.rolesPanel(body)),
  arter: () => import("./admin/arter.js").then(m => (body) => m.species(body)),
  indhold: () => import("./admin/indhold.js").then(m => (body) => m.content(body)),
  funktioner: () => import("./admin/funktioner.js").then(m => (body) => m.featuresPanel(body)),
  lokaliteter: () => import("./admin/lokaliteter.js").then(m => (body, every) => m.renderLocations(body, every)),
};

export const render = (view) => shell(view, ADMIN_TABS, "Administration");
export const renderModeration = (view) => shell(view, MOD_TABS, "Moderation");

async function shell(view, allTabs, title) {
  const isMod = allTabs === MOD_TABS;
  if (!isMod && MOVED_TO_MODERATION.has(params().tab)) {
    const q = new URLSearchParams(location.hash.split("?")[1] || "");
    if (q.get("tab") === "kategorier") { q.set("tab", "arter"); q.set("vis", "kategorier"); }
    location.replace(`#/moderation?${q}`); return;
  }
  // Fanerne følger brugerens moduler (rollens standard eller individuelle rettigheder)
  const tabs = allTabs.filter(([m]) => hasTab(m));
  if (!tabs.length) { view.append(errorBox(new Error("Du har ikke adgang til nogen moduler her"))); return; }
  let tab = params().tab || tabs[0][0];
  if (!tabs.some(t => t[0] === tab)) tab = tabs[0][0];
  const body = h("div", {});
  view.append(h("div", { class: "page-head" }, h("h1", {}, title), h("span", { class: "spacer" }),
    seg(tabs, tab, (v) => { setParams({ tab: v, t: "" }); body.replaceChildren(); stop(); draw(v); })), body);
  let timers = [], shown = null;
  const stop = () => { timers.forEach(clearInterval); timers = []; };
  const every = (fn, ms) => { fn(); timers.push(setInterval(fn, ms)); };
  const draw = async (t) => {
    shown = t;
    let fn;
    try { fn = await (TABS[t] || TABS.drift)(); } catch (err) { if (shown === t) body.append(errorBox(err)); return; }
    if (shown === t) fn(body, every);  // ikke hvis der er skiftet fane, mens modulet blev hentet
  };
  await draw(tab);
  return stop;
}
