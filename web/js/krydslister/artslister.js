// Artslister (DK, VP …) med kategorier og sjældenhed; søg, CSV og Print. Administratorer redigerer under Admin → Artslister.
import { api, h } from "../core.js";
import { BASE, link, table, loadingBox } from "./common.js";

const SJ = { almindelig: "Almindelig", relativ_almindelig: "Relativ almindelig", ualmindelig: "Ualmindelig", sjaelden: "Sjælden", su: "SU" };

export async function page(parts, q) {
  const lists = await api("/api/krydslister/artslister");
  const key = q.liste && lists.some(l => l.key === q.liste) ? q.liste : lists[0]?.key;
  const body = h("div", {}, loadingBox());
  const tabs = h("div", { class: "seg" }, ...lists.map(l => h("a", { href: `${BASE}/artslister?liste=${l.key}`, class: l.key === key ? "on" : "" }, `${l.navn} (${l.antal})`)));
  if (key) api(`/api/krydslister/artslister/${key}`).then(d => {
    body.replaceChildren(d.liste.beskrivelse ? h("p", {}, d.liste.beskrivelse) : null, table(["#", "SU", "Navn", "Latinsk navn", "Kategori", "Sjældenhed"], d.rows.map((r, i) => ({ text: `${r.navn} ${r.latin} ${r.engelsk || ""}`,
      cells: [i + 1, r.su ? h("b", { class: "kl-su" }, "*") : "", link(`/art/${r.id}`, r.navn), h("i", {}, r.latin), r.kategorier.join(", "), SJ[r.sjaeldenhed] || r.sjaeldenhed] })), { name: key }));
  });
  return h("div", { class: "stack" }, tabs, body);
}
