// Fotos → Admin: faner (kø, privilegier, fotografer, import, bannere, koder, indstillinger). Hver fane er en fil, der hentes først, når den åbnes.
import { h, errorBox } from "../core.js";
import { BASE, loadingBox, notice } from "./common.js";

const TABS = [["koe", "Kø"], ["privilegier", "Privilegier"], ["fotografer", "Fotografer"], ["import", "Import"], ["kommentarer", "Kommentarer"], ["bannere", "Bannere"], ["koder", "Koder"], ["indstillinger", "Indstillinger"]];
const FILES = { koe: () => import("./admin-koe.js"), privilegier: () => import("./admin-privilegier.js"), fotografer: () => import("./admin-fotografer.js"),
  import: () => import("./admin-import.js"), kommentarer: () => import("./admin-kommentarer.js"), bannere: () => import("./admin-bannere.js"), koder: () => import("./admin-koder.js"), indstillinger: () => import("./admin-indstillinger.js") };

export async function page(parts, q = {}) {
  const tab = TABS.some(t => t[0] === parts[1]) ? parts[1] : "koe";
  const body = h("div", {}, loadingBox());
  try {
    const mod = await FILES[tab]();
    body.replaceChildren(await mod.page(parts, q));
  } catch (e) { console.error(e); body.replaceChildren(e.status === 403 ? notice("Du har ikke adgang til dette.", "err") : errorBox(e)); }
  return h("div", { class: "fot-adm stack" },
    h("nav", { class: "fot-subtabs" }, ...TABS.map(([k, t]) => h("a", { href: `${BASE}/admin/${k}`, class: k === tab ? "on" : "" }, t))), body);
}
