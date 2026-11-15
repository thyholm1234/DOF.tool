// Snak (#/snak/…): feed med opslag i kategorier, kommentarer og reaktioner. Skal, faner og ruter her; hver side er en fil under snak/.
import { h, errorBox, requireLogin, state } from "../core.js";
import { BASE, NAME, cleanups, ctx, loadCss, loadingBox, notice, on } from "../snak/common.js";

const MODULES = { feed: () => import("../snak/feed.js"), opslag: () => import("../snak/opslag.js"), admin: () => import("../snak/admin.js"), soeg: () => import("../snak/soeg.js"), mine: () => import("../snak/mine.js") };

function route(parts) {
  const [a, b] = parts;
  if (a === undefined) return ["feed", "page", ""];
  if (a === "k" && b) return ["feed", "page", ""];
  if (a === "ny") return ["opslag", "ny", ""];
  if (a === "admin") return ["admin", "page", "admin"];
  if (a === "soeg") return ["soeg", "page", "soeg"];
  if (a === "mine") return ["mine", "page", "mine"];
  if (/^\d+$/.test(a)) return ["opslag", b === "rediger" ? "ret" : "page", ""];
  return null;
}

async function bell() {
  const { notificationsDialog } = await import("../snak/notifikationer.js");
  notificationsDialog();
}

export async function render(view, [sub]) {
  if (requireLogin(view)) return;
  loadCss("snak");
  cleanups.length = 0;
  if (!state.user.snak?.features?.snak) {
    view.append(h("div", { class: "notice" }, `${NAME} er ikke slået til.`,
      state.user.role === "superadmin" ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå det til under Admin → Funktioner")] : null));
    return;
  }
  const parts = (sub || "").split("/").filter(Boolean);
  const spec = route(parts);
  const tabs = [["", "Feed"], ["soeg", "Søg"], ["mine", "Mine"], ...(ctx().can_admin ? [["admin", "Admin"]] : [])];
  const content = h("div", {}, loadingBox());
  view.replaceChildren(h("div", { class: "snak" },
    h("nav", { class: "sn-tabs", "aria-label": NAME }, h("div", { class: "seg" }, ...tabs.map(([k, t]) => h("a", { href: k ? `${BASE}/${k}` : BASE, class: spec?.[2] === k ? "on" : "", "aria-current": spec?.[2] === k ? "page" : undefined }, t))),
      h("span", { class: "spacer" }),
      h("form", { class: "sn-search", role: "search", onsubmit: (e) => { e.preventDefault(); const v = e.target.q.value.trim(); location.hash = `${BASE}/soeg${v ? `?q=${encodeURIComponent(v)}` : ""}`; } },
        h("input", { type: "search", name: "q", placeholder: `Søg i ${NAME} …`, "aria-label": `Søg i ${NAME}` })),
      h("a", { class: "btn primary sm", href: `${BASE}/ny` }, "+ Nyt opslag"),
      on("snak_notifikationer") ? h("button", { type: "button", class: "icon-btn", "aria-label": "Notifikationer fra Snak", title: "Notifikationer", onclick: bell }, "🔔") : null),
    content));
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
