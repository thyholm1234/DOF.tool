// Fotomodulet (#/fotos/…): fotogalleri med netfugl-arkivet og nye fotos – søgning, fotoside, kommentarer, upload og moderation.
// Skal og faner her; hver side er en fil under fotos/.
import { h, params, errorBox, requireLogin, state } from "../core.js";
import { BASE, ctx, on, loadCss, loadingBox, notice, cleanups } from "../fotos/common.js";

const MODULES = {
  galleri: () => import("../fotos/galleri.js"), foto: () => import("../fotos/foto.js"), mine: () => import("../fotos/mine.js"),
  upload: () => import("../fotos/upload.js"), sider: () => import("../fotos/sider.js"), admin: () => import("../fotos/admin.js"),
};

/** Rute → [modul, funktion]. Fotos med tal i stien er fotosider; faste ord kommer først. */
function route(parts) {
  const [a, b] = parts;
  if (a === undefined) return ["galleri", "page"];
  if (a === "fotograf" && /^\d+$/.test(b || "")) return ["galleri", "photographer"];
  if (a === "mine") return ["mine", "page"];
  if (a === "ny") return ["upload", "ny"];
  if (a === "retningslinjer") return ["sider", "retningslinjer"];
  if (a === "n" && /^\d+$/.test(b || "")) return ["foto", "fraNetfugl"];
  if (a === "admin") return ["admin", "page"];
  if (/^\d+$/.test(a)) return b === "ret" ? ["upload", "ret"] : ["foto", "page"];
  return null;
}


export async function render(view, [sub]) {
  if (requireLogin(view)) return;
  loadCss("fotos");
  const parts = (sub || "").split("/").filter(Boolean);
  cleanups.length = 0;
  if (!state.user.fotos?.features?.fotos) {
    view.append(h("div", { class: "notice" }, "Fotomodulet er ikke slået til.",
      state.user.role === "superadmin" ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå det til under Admin → Funktioner")] : null));
    return;
  }
  const spec = route(parts);
  const c = ctx();
  const tab = (k, t, active) => h("a", { href: k, class: active ? "on" : "" }, t);
  const first = parts[0];
  const content = h("div", {}, loadingBox());
  view.replaceChildren(h("div", { class: "fot" },
    h("header", { class: "page-head fot-head" },
      h("div", {}, h("h1", {}, h("a", { href: BASE }, "Fotos")), h("p", { class: "lead" }, "Fotogalleri med netfugl-arkivet")),
      h("span", { class: "spacer" }),
      on("fotos_upload") && c.privilegie !== "udelukket" ? h("a", { class: "btn primary", href: `${BASE}/ny` }, "+ Nyt foto") : null),
    h("nav", { class: "fot-tabs", "aria-label": "Fotos" },
      h("div", { class: "seg" }, tab(BASE, "Galleri", first === undefined || first === "fotograf" || /^\d+$/.test(first || "")),
        tab(`${BASE}/mine`, "Mine fotos", first === "mine"), tab(`${BASE}/retningslinjer`, "Retningslinjer", first === "retningslinjer"),
        c.can_admin ? tab(`${BASE}/admin`, "Admin", first === "admin") : null),
      h("a", { class: "icon-btn", href: "#/alarmer?tab=notifikationer", title: "Notifikationer", "aria-label": "Notifikationer" }, "🔔")),
    content));
  try {
    if (!spec) { content.replaceChildren(notice("Siden findes ikke.", "err")); return; }
    const mod = await MODULES[spec[0]]();
    content.replaceChildren(await mod[spec[1]](parts, params()));
    if (spec[0] !== "foto") document.title = "Fotos – DOF.tool";
  } catch (err) {
    console.error(err);
    content.replaceChildren(err.status === 404 ? notice("Siden findes ikke, eller du har ikke adgang til den.", "err") : errorBox(err));
  }
  return () => { for (const f of cleanups.splice(0)) { try { f(); } catch { /* ignorér */ } } document.title = "DOF.tool"; };
}
