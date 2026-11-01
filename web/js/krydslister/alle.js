// Alle krydslister: oversigt over listetyperne og deres parametre (år, måneder, lokalafdelinger, kommuner).
import { api, h } from "../core.js";
import { link } from "./common.js";

const VIEWS = [["ranking", "Liste"], ["seneste", "Seneste kryds"], ["hvem", "Hvem har set hvad?"], ["blockers", "Blockers"], ["blockerstat", "Blockers statistik"], ["tophits", "Årstophits"]];

export async function page() {
  const m = await api("/api/krydslister/lister");
  const views = (k, top) => h("span", { class: "small" }, ...VIEWS.filter(([v]) => top || v !== "tophits").map(([v, t], i) => [i ? " | " : "", link(`/kryds/${encodeURIComponent(k)}/${v}`, t)]));
  const row = (k, name, top = true) => h("div", { style: { margin: "8px 0" } }, h("b", {}, link(`/kryds/${encodeURIComponent(k)}/ranking`, name)), h("br"), views(k, top));
  const sections = m.lister.map(l => {
    if (!l.param) return [row(l.key, l.name)];
    if (l.param === "year") return (l.key === "aedelaar" ? m.aedel_years : m.years).map(y => row(`${l.key}:${y}`, l.name.replace("{p}", y), false));
    if (l.param === "month") return [h("h3", { class: "kl-title" }, l.name.replace("{p}", "")), ...m.months.map((n, i) => row(`${l.key}:${i + 1}`, l.name.replace("{p}", n), false))];
    if (l.param === "afdeling") return [h("h3", { class: "kl-title" }, "Lokalarter – lokalafdelinger"), ...m.afdelinger.map(a => row(`${l.key}:${a.id}`, a.navn, false))];
    return [h("h3", { class: "kl-title" }, "Lokalarter – kommuner"), h("div", { style: { columns: "3 220px" } }, ...m.kommuner.map(k => h("div", {}, link(`/kryds/${l.key}:${k.kode}/ranking`, k.navn))))];
  });
  return h("div", {}, h("p", {}, "Komplet oversigt over krydslisterne. Automatiske lister beregnes ud fra DOFbasen; øvrige består af deltagernes egne kryds."), ...sections.flat());
}
