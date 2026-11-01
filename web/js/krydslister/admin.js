// Krydslister → Admin: listetyper, artslister, World Bird List, godkendelse af kryds og indstillinger.
import { h, errorBox } from "../core.js";
import { BASE, loadingBox, notice } from "./common.js";

const TABS = [["listetyper", "Listetyper"], ["artslister", "Artslister"], ["wbl", "World Bird List"], ["godkendelse", "Godkendelse"], ["indstillinger", "Indstillinger"]];
const FILES = { listetyper: () => import("./admin-listetyper.js"), artslister: () => import("./admin-artslister.js"), wbl: () => import("./admin-wbl.js"),
  godkendelse: () => import("./admin-godkendelse.js"), indstillinger: () => import("./admin-indstillinger.js") };

export async function page(parts, q = {}) {
  const tab = TABS.some(t => t[0] === parts[1]) ? parts[1] : "listetyper";
  const body = h("div", {}, loadingBox());
  try { body.replaceChildren(await (await FILES[tab]()).page(parts, q)); } catch (e) { console.error(e); body.replaceChildren(e.status === 403 ? notice("Du har ikke adgang til dette.", "err") : errorBox(e)); }
  return h("div", { class: "stack" }, h("nav", { class: "kl-subtabs" }, ...TABS.map(([k, t]) => h("a", { href: `${BASE}/admin/${k}`, class: k === tab ? "on" : "" }, t))), body);
}
