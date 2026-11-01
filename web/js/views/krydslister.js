// Krydslistemodulet (#/krydslister/…): krydslister, seneste kryds, hvem har set hvad, blockers, årstophits, mine kryds, artslister, World Bird List og artssider.
import { h, params, errorBox, requireLogin, state } from "../core.js";
import { BASE, ctx, on, loadCss, loadingBox, notice, cleanups } from "../krydslister/common.js";

const MODULES = { oversigt: () => import("../krydslister/oversigt.js"), liste: () => import("../krydslister/liste.js"), alle: () => import("../krydslister/alle.js"),
  mine: () => import("../krydslister/mine.js"), artslister: () => import("../krydslister/artslister.js"), wbl: () => import("../krydslister/wbl.js"),
  art: () => import("../krydslister/art.js"), sider: () => import("../krydslister/sider.js"), admin: () => import("../krydslister/admin.js") };

function route(parts) {
  const [a, b, c] = parts;
  if (a === undefined) return ["oversigt", "page"];
  if (a === "alle") return ["alle", "page"];
  if (a === "regler") return ["sider", "regler"];
  if (a === "om") return ["sider", "om"];
  if (a === "mine") return ["mine", "page"];
  if (a === "artslister") return ["artslister", "page"];
  if (a === "wbl") return ["wbl", "page"];
  if (a === "art" && /^\d+$/.test(b || "")) return ["art", "page"];
  if (a === "admin") return ["admin", "page"];
  if (a === "kryds" && b && c === "art") return ["liste", "hvem"];
  if (a === "kryds" && b) return ["liste", "page"];
  return null;
}

export async function render(view, [sub]) {
  if (requireLogin(view)) return;
  loadCss("krydslister");
  const parts = (sub || "").split("/").filter(Boolean);
  cleanups.length = 0;
  if (!state.user.krydslister?.features?.krydslister) {
    view.append(h("div", { class: "notice" }, "Krydslistemodulet er ikke slået til.", state.user.role === "superadmin" ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå det til under Admin → Funktioner")] : null));
    return;
  }
  const spec = route(parts), c = ctx(), first = parts[0];
  const tab = (k, t, active) => h("a", { href: k, class: active ? "on" : "" }, t);
  const content = h("div", {}, loadingBox());
  view.replaceChildren(h("div", { class: "kl" },
    h("header", { class: "page-head" }, h("div", {}, h("h1", {}, h("a", { href: BASE }, "Krydslister")), h("p", { class: "lead" }, "Krydslister, artslister og World Bird List"))),
    h("nav", { class: "kl-tabs", "aria-label": "Krydslister" }, h("div", { class: "seg" },
      tab(BASE, "Oversigt", first === undefined), tab(`${BASE}/alle`, "Alle lister", first === "alle" || first === "kryds"), tab(`${BASE}/mine`, "Mine kryds", first === "mine"),
      on("krydslister_wbl") ? tab(`${BASE}/artslister`, "Artslister", first === "artslister") : null, on("krydslister_wbl") ? tab(`${BASE}/wbl`, "World Bird List", first === "wbl" || first === "art") : null,
      tab(`${BASE}/regler`, "Krydsregler", first === "regler" || first === "om"), c.can_admin ? tab(`${BASE}/admin`, "Admin", first === "admin") : null)),
    content));
  try {
    if (!spec) { content.replaceChildren(notice("Siden findes ikke.", "err")); return; }
    const mod = await MODULES[spec[0]]();
    content.replaceChildren(await mod[spec[1]](parts, params()));
    document.title = "Krydslister – DOF.tool";
  } catch (err) {
    console.error(err);
    content.replaceChildren(err.status === 404 ? notice("Siden findes ikke, eller du har ikke adgang til den.", "err") : errorBox(err));
  }
  return () => { for (const f of cleanups.splice(0)) { try { f(); } catch { /* ignorér */ } } document.title = "DOF.tool"; };
}
