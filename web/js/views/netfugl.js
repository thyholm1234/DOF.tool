// Netfugl-modulet (#/netfugl/…): en showcase af en ny udgave af netfugl.dk, integreret med DOFbasen-data. Siderne vises i
// Netfugls eget layout (portalens topbar og menuer skjules), og hele modulet kan slås til/fra af superadmin under Admin → Funktioner.
import { h, params, errorBox } from "../core.js";
import { loadCtx, frame, ctx, on, notice } from "../netfugl/shell.js";

const PAGES = {
  "": ["forside", "page"], artikler: ["artikler", "page", "netfugl_artikler"], fotos: ["fotos", "page", "netfugl_fotos"],
  kryds: ["kryds", "page", "netfugl_kryds"], "mine-kryds": ["kryds", "mine", "netfugl_kryds"], arter: ["arter", "page"], art: ["arter", "page"],
  rss: ["sider", "rss", "netfugl_rss"], vejret: ["sider", "vejret"], brugere: ["sider", "brugere"], bruger: ["sider", "brugere"],
  side: ["sider", "side"], soeg: ["sider", "soeg"], api: ["apidocs", "page", "netfugl_api"], admin: ["admin", "page"],
};
const MODULES = {
  forside: () => import("../netfugl/forside.js"), artikler: () => import("../netfugl/artikler.js"), fotos: () => import("../netfugl/fotos.js"),
  kryds: () => import("../netfugl/kryds.js"), arter: () => import("../netfugl/arter.js"), sider: () => import("../netfugl/sider.js"),
  apidocs: () => import("../netfugl/apidocs.js"), admin: () => import("../netfugl/admin.js"),
};

export async function render(view, [sub]) {
  const parts = (sub || "").split("/").filter(Boolean);
  let data;
  try { data = await loadCtx(true); } catch (err) { view.append(errorBox(err)); return; }
  if (!data.enabled) {
    view.append(h("div", { class: "notice" }, "Netfugl-modulet er ikke slået til.",
      data.superadmin ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå det til under Admin → Funktioner")] : null));
    return;
  }
  const spec = PAGES[parts[0] || ""];
  let content;
  try {
    if (!spec) content = h("div", { class: "wrap" }, notice("Siden findes ikke.", "err"));
    else if (spec[2] && !on(spec[2])) content = h("div", { class: "wrap" }, notice("Funktionen er ikke slået til.", "err"));
    else content = await (await MODULES[spec[0]]())[spec[1]](parts, params());
  } catch (err) {
    console.error(err);
    content = h("div", { class: "wrap" }, notice("Siden kunne ikke vises: " + err.message, "err"));
  }
  view.replaceChildren(frame(content));
  document.title = "Netfugl – showcase";
  return () => {
    if (!location.hash.startsWith("#/netfugl")) { document.body.classList.remove("nfu-mode"); document.title = "DOF.tool"; ctx.data = null; }
  };
}
