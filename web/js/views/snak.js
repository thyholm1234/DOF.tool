// Snak (#/snak/…): feed med opslag i kategorier, kommentarer og reaktioner. Skal og ruter her; hver side er en fil under snak/.
import { h, errorBox, requireLogin, state } from "../core.js";
import { BASE, cleanups, loadCss, loadingBox, notice } from "../snak/common.js";

const MODULES = { feed: () => import("../snak/feed.js"), opslag: () => import("../snak/opslag.js") };

function route(parts) {
  const [a, b] = parts;
  if (a === undefined) return ["feed", "page"];
  if (a === "k" && b) return ["feed", "page"];
  if (a === "ny") return ["opslag", "ny"];
  if (/^\d+$/.test(a)) return ["opslag", b === "rediger" ? "ret" : "page"];
  return null;
}

export async function render(view, [sub]) {
  if (requireLogin(view)) return;
  loadCss("snak");
  cleanups.length = 0;
  if (!state.user.snak?.features?.snak) {
    view.append(h("div", { class: "notice" }, "Snak er ikke slået til.",
      state.user.role === "superadmin" ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå det til under Admin → Funktioner")] : null));
    return;
  }
  const parts = (sub || "").split("/").filter(Boolean);
  const spec = route(parts);
  const content = h("div", {}, loadingBox());
  view.replaceChildren(h("div", { class: "snak" }, content));
  try {
    if (!spec) { content.replaceChildren(notice("Siden findes ikke.", "err")); return; }
    const mod = await MODULES[spec[0]]();
    content.replaceChildren(await mod[spec[1]](parts));
  } catch (err) {
    console.error(err);
    content.replaceChildren(err.status === 404 ? notice("Siden findes ikke, eller du har ikke adgang til den.", "err") : errorBox(err));
  }
  return () => { for (const f of cleanups.splice(0)) { try { f(); } catch { /* ignorér */ } } document.title = "DOF.tool"; };
}
