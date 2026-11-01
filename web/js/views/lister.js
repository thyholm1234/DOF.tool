// Lister (#/lister/…): ét modul for lister, krydslister og matrikel. Fanerne kommer fra tre kilder:
//   lists.js       – Mine lister, Huller, Kapløb & grupper (beregnes direkte fra observationerne, også egne områder og grupper)
//   matrikel.js    – Matrikel (Boligbirding)
//   krydslister/*  – Oversigt, Ranglister (alle listetyper med manuelle kryds), Mine kryds, Artslister, World Bird List, Regler, Admin
// Krydslisterne ligger bag funktionsflaget krydslister; uden det er Mine lister forsiden.
import { h, params, errorBox, requireLogin, state } from "../core.js";
import { ctx, on, loadCss, loadingBox, notice, cleanups } from "../krydslister/common.js";

export const BASE = "#/lister";

const KL = { oversigt: () => import("../krydslister/oversigt.js"), liste: () => import("../krydslister/liste.js"), alle: () => import("../krydslister/alle.js"),
  mine: () => import("../krydslister/mine.js"), artslister: () => import("../krydslister/artslister.js"), wbl: () => import("../krydslister/wbl.js"),
  art: () => import("../krydslister/art.js"), sider: () => import("../krydslister/sider.js"), admin: () => import("../krydslister/admin.js") };

// Faner fra lists.js og matrikel.js: (body) => tegn fanen
const LISTS = {
  mine: async (body) => (await import("./lists.js")).mine(body),
  huller: async (body) => (await import("./lists.js")).gaps(body),
  kaploeb: async (body) => {
    const m = await import("./lists.js");
    const groupsBox = h("div", { class: "mt" });
    await m.scoreboard(body);
    body.append(h("h3", { class: "mt2" }, "Grupper"), groupsBox);
    await m.groups(groupsBox, `${BASE}/kaploeb`);
  },
  matrikel: async (body) => (await import("./matrikel.js")).render(body),
  "arter-pr-aar": async (body) => (await import("./stats.js")).regionsTab(body, "regioner"),
};

/** Sti → [kilde, modul, funktion]. */
function route(parts, kl) {
  const [a, b, c] = parts;
  if (a === undefined) return kl ? ["kl", "oversigt", "page"] : ["lists", "mine"];
  if (a === "mine" && b === "kryds") return kl ? ["kl", "mine", "page"] : null;
  if (LISTS[a] && !b) return ["lists", a];
  if (!kl) return null;
  if (a === "alle") return ["kl", "alle", "page"];
  if (a === "regler") return ["kl", "sider", "regler"];
  if (a === "om") return ["kl", "sider", "om"];
  if (a === "artslister") return ["kl", "artslister", "page"];
  if (a === "wbl") return ["kl", "wbl", "page"];
  if (a === "art" && /^\d+$/.test(b || "")) return ["kl", "art", "page"];
  if (a === "admin") return ["kl", "admin", "page"];
  if (a === "kryds" && b && c === "art") return ["kl", "liste", "hvem"];
  if (a === "kryds" && b) return ["kl", "liste", "page"];
  return null;
}

/** Gamle adresser: #/lister?tab=…, #/krydslister/… og #/matrikel?… */
const OLD_TABS = { mine: "mine", huller: "huller", score: "kaploeb", grupper: "kaploeb", regioner: "arter-pr-aar" };
export function legacy(path, sub) {
  const q = location.hash.split("?")[1] || "";
  if (path === "krydslister") {
    const s = (sub || "").replace(/^mine(?=$|[/?])/, "mine/kryds");
    return `${BASE}${s ? "/" + s : ""}${q ? "?" + q : ""}`;
  }
  if (path === "matrikel") return `${BASE}/matrikel${q ? "?" + q : ""}`;
  return null;
}

export async function render(view, [sub]) {
  if (requireLogin(view)) return;
  loadCss("krydslister");
  const parts = (sub || "").split("/").filter(Boolean);
  const p = params();
  if (!parts.length && OLD_TABS[p.tab]) {
    const q = new URLSearchParams(p); q.delete("tab");
    location.replace(`${BASE}/${OLD_TABS[p.tab]}${[...q].length ? "?" + q : ""}`); return;
  }
  cleanups.length = 0;
  const kl = !!state.user.krydslister?.features?.krydslister, wbl = kl && on("krydslister_wbl"), stats = !!state.user.statistik;
  const spec = route(parts, kl), c = ctx(), first = parts[0];
  const tab = (k, t, active) => h("a", { href: k, class: active ? "on" : "" }, t);
  const content = h("div", {}, loadingBox());
  view.replaceChildren(h("div", { class: "kl" },
    h("header", { class: "page-head" }, h("div", {}, h("h1", {}, h("a", { href: BASE }, "Lister")),
      h("p", { class: "lead" }, kl ? "Dine lister, krydslister, ranglister og matrikel – beregnet fra DOFbasen og suppleret med manuelle kryds" : "Dine lister, ranglister og matrikel – beregnet fra DOFbasen"))),
    h("nav", { class: "kl-tabs", "aria-label": "Lister" }, h("div", { class: "seg" },
      kl ? tab(BASE, "Oversigt", first === undefined) : null,
      tab(`${BASE}/mine`, "Mine lister", first === "mine" ? parts[1] !== "kryds" : !kl && first === undefined),
      kl ? tab(`${BASE}/mine/kryds`, "Mine kryds", first === "mine" && parts[1] === "kryds") : null,
      kl ? tab(`${BASE}/alle`, "Ranglister", first === "alle" || first === "kryds") : null,
      tab(`${BASE}/kaploeb`, "Kapløb & grupper", first === "kaploeb"),
      tab(`${BASE}/huller`, "Huller", first === "huller"),
      tab(`${BASE}/matrikel`, "Matrikel", first === "matrikel"),
      stats ? null : tab(`${BASE}/arter-pr-aar`, "Arter pr. år", first === "arter-pr-aar"),
      wbl ? tab(`${BASE}/artslister`, "Artslister", first === "artslister") : null,
      wbl ? tab(`${BASE}/wbl`, "World Bird List", first === "wbl" || first === "art") : null,
      kl ? tab(`${BASE}/regler`, "Regler", first === "regler" || first === "om") : null,
      kl && c.can_admin ? tab(`${BASE}/admin`, "Admin", first === "admin") : null)),
    content));
  try {
    if (!spec) { content.replaceChildren(notice("Siden findes ikke.", "err")); return; }
    if (spec[0] === "lists") {
      const body = h("div", {});
      content.replaceChildren(body);
      const off = await LISTS[spec[1]](body);
      if (typeof off === "function") cleanups.push(off);
    } else {
      const mod = await KL[spec[1]]();
      content.replaceChildren(await mod[spec[2]](parts, p));
    }
    document.title = "Lister – DOF.tool";
  } catch (err) {
    console.error(err);
    content.replaceChildren(err.status === 404 ? notice("Siden findes ikke, eller du har ikke adgang til den.", "err") : errorBox(err));
  }
  return () => { for (const f of cleanups.splice(0)) { try { f(); } catch { /* ignorér */ } } document.title = "DOF.tool"; };
}
