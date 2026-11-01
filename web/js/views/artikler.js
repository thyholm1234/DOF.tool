// Artikelmodulet (#/artikler/…): netfugl.dk-arkivet og nye artikler med avisforside, søgning på ord og råtekst, emneord, arter,
// kommentarer og en markdown-editor. Skal og faner her; hver side er en fil under artikler/.
import { h, params, errorBox, requireLogin, state, modal, api, toast } from "../core.js";
import { BASE, ctx, on, loadCss, loadingBox, notice, cleanups } from "../artikler/common.js";

const MODULES = {
  forside: () => import("../artikler/forside.js"), soeg: () => import("../artikler/soeg.js"), emneord: () => import("../artikler/emneord.js"),
  arter: () => import("../artikler/arter.js"), forfattere: () => import("../artikler/forfattere.js"), artikel: () => import("../artikler/artikel.js"),
  redigering: () => import("../artikler/redigering.js"), admin: () => import("../artikler/admin.js"),
};
const TABS = [["", "Forside"], ["soeg", "Søg"], ["emneord", "Emneord"], ["arter", "Arter"], ["forfattere", "Forfattere"]];

function route(parts) {
  const [a, b] = parts;
  if (a === undefined) return ["forside"];
  if (/^\d+$/.test(a)) return b === "rediger" ? ["redigering", "ret"] : ["artikel", "side"];
  if (a === "n") return ["artikel", "fraNetfugl"];
  if (a === "ny") return ["redigering", "ny"];
  if (MODULES[a] && !["artikel", "redigering", "forside"].includes(a)) return [a];
  return null;
}

function notifications() {
  const body = h("div", { class: "stack" }, loadingBox());
  const m = modal("Notifikationer fra Artikler", body);
  api("/api/artikler/notifikationer").then(p => {
    const save = async (patch) => { try { Object.assign(p, await api("/api/artikler/notifikationer", { method: "PUT", body: patch })); toast("Gemt"); draw(); } catch (e) { toast(e.message, "err"); } };
    const draw = () => body.replaceChildren(
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: p.alle, onchange: (e) => save({ alle: e.target.checked }) }), h("span", {}, "Giv mig besked, når der kommer en ny artikel")),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: p.kommentarer, onchange: (e) => save({ kommentarer: e.target.checked }) }),
        h("span", {}, "Giv mig besked om nye kommentarer på artikler, jeg har kommenteret (og svar på mine kommentarer)")),
      h("p", { class: "muted small" }, "Beskeder kommer i din indbakke og som push, hvis du har slået push til (stille timer respekteres). Du kan også følge eller holde op med at følge en enkelt artikel nederst på artiklen."),
      p.foelger?.length ? h("div", {}, h("b", {}, "Artikler du følger"), h("ul", {}, ...p.foelger.map(f => h("li", {}, h("a", { href: `${BASE}/${f.id}`, onclick: () => m.close() }, f.title))))) : null);
    draw();
  }).catch(e => body.replaceChildren(errorBox(e)));
}

export async function render(view, [sub]) {
  if (requireLogin(view)) return;
  loadCss("artikler", "editor");
  const parts = (sub || "").split("/").filter(Boolean);
  cleanups.length = 0;
  if (!state.user.artikler?.features?.artikler) {
    view.append(h("div", { class: "notice" }, "Artikelmodulet er ikke slået til.",
      state.user.role === "superadmin" ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå det til under Admin → Funktioner")] : null));
    return;
  }
  const spec = route(parts);
  const active = spec?.[0] === "forside" ? "" : spec?.[0] === "admin" ? "admin" : TABS.find(t => t[0] === parts[0]) ? parts[0] : null;
  const content = h("div", {}, loadingBox());
  const q = h("input", { type: "search", placeholder: "Søg i alle artikler …", "aria-label": "Søg i artikler", value: params().q || "" });
  const c = ctx();
  view.replaceChildren(h("div", { class: "art" },
    h("header", { class: "page-head art-head" },
      h("div", {}, h("h1", {}, h("a", { href: BASE }, "Artikler")), h("p", { class: "lead" }, "Arkiv og nyt fra feltet")),
      h("span", { class: "spacer" }),
      h("form", { class: "art-search", role: "search", onsubmit: (e) => { e.preventDefault(); location.hash = `${BASE}/soeg?q=${encodeURIComponent(q.value.trim())}`; } }, q),
      c.can_edit && on("artikler_editor") ? h("a", { class: "btn primary", href: `${BASE}/ny` }, "+ Ny artikel") : null),
    h("nav", { class: "art-tabs", "aria-label": "Artikler" },
      h("div", { class: "seg" }, ...TABS.map(([k, t]) => h("a", { href: k ? `${BASE}/${k}` : BASE, class: active === k ? "on" : "" }, t)),
        c.admin ? h("a", { href: `${BASE}/admin`, class: active === "admin" ? "on" : "" }, "Admin") : null),
      on("artikler_notifikationer") ? h("button", { class: "icon-btn", type: "button", title: "Notifikationer", "aria-label": "Notifikationer", onclick: notifications }, "🔔") : null),
    content));
  try {
    if (!spec) { content.replaceChildren(notice("Siden findes ikke.", "err")); return; }
    const mod = await MODULES[spec[0]]();
    const page = await mod[spec[1] || "page"](parts, params());
    content.replaceChildren(page);
    if (spec[0] !== "artikel") document.title = "Artikler – DOF.tool";
  } catch (err) {
    console.error(err);
    content.replaceChildren(err.status === 404 ? notice("Siden findes ikke, eller du har ikke adgang til den.", "err") : errorBox(err));
  }
  return () => { for (const f of cleanups.splice(0)) { try { f(); } catch { /* ignorér */ } } document.title = "DOF.tool"; };
}
