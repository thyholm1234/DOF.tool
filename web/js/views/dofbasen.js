// DOFbasen-modulet (#/dofbasen/…): en showcase af en ny udgave af dofbasen.dk. Siderne vises i DOFbasens eget layout
// (portalens topbar og menuer skjules), og hele modulet kan slås til/fra af superadmin under Admin → Funktioner.
import { h, params, errorBox } from "../core.js";
import { loadCtx, frame, ctx, on, notice } from "../dofbasen/shell.js";
import { obsView, lokView, obserView, layout, popupLinks } from "../dofbasen/common.js";

const PAGES = {
  "": () => import("../dofbasen/forside.js"),
  observationer: () => import("../dofbasen/observationer.js"),
  soeg: () => import("../dofbasen/soeg.js"),
  faeno: () => import("../dofbasen/faeno.js"),
  opslag: () => import("../dofbasen/opslag.js"),
  side: () => import("../dofbasen/tekst.js"),
  api: () => import("../dofbasen/apidocs.js"),
  admin: () => import("../dofbasen/admin.js"),
};
const FEATURE = { soeg: "dofbasen_search", nyheder: "dofbasen_news", api: "dofbasen_api" };

export async function render(view, [sub]) {
  const started = performance.now();
  const parts = (sub || "").split("/").filter(Boolean);
  const key = parts[0] || "";
  let data;
  try { data = await loadCtx(true); } catch (err) { view.append(errorBox(err)); return; }
  if (!data.enabled) {
    view.append(h("div", { class: "notice" }, "DOFbasen-modulet er ikke slået til.",
      data.superadmin ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå det til under Admin → Funktioner")] : null));
    return;
  }
  const q = params();
  let content;
  try {
    if (FEATURE[key] && !on(FEATURE[key])) content = layout(notice("Funktionen er ikke slået til.", true));
    else if (key === "obs" || key === "lok" || key === "obser") {
      const box = h("div", {});
      ({ obs: obsView, lok: lokView, obser: obserView })[key](key === "obser" ? parts[1] : +parts[1], box);
      popupLinks(box);
      content = layout(box);
    } else if (key === "nyheder") content = await (await import("../dofbasen/tekst.js")).nyheder(parts);
    else if (key === "bruger") content = await (await import("../dofbasen/tekst.js")).bruger();
    else if (key === "fotogalleri") content = await (await import("../dofbasen/tekst.js")).fotogalleri();
    else if (PAGES[key]) content = await (await PAGES[key]()).page(parts, q);
    else content = layout(notice("Siden findes ikke.", true));
  } catch (err) {
    console.error(err);
    content = layout(notice("Siden kunne ikke vises: " + err.message, true));
  }
  view.replaceChildren(frame(content, started));
  document.title = "DOFbasen – showcase";
  return () => {
    // Inden for modulet beholdes layoutet (ingen blink af portalens menuer mellem siderne)
    if (!location.hash.startsWith("#/dofbasen")) { document.body.classList.remove("dofb-mode"); document.title = "DOF.tool"; ctx.data = null; }
  };
}
