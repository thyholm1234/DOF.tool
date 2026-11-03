// Læring 2.0: kursusoversigt, grupperet efter trin.
import { api, h, empty } from "../core.js";
import { courseCard } from "./kort.js";

export async function render(view) {
  const d = await api("/api/learn/katalog");
  const stages = [];
  for (const c of d.kurser) { const s = c.stage || "Kurser"; let g = stages.find(x => x[0] === s); if (!g) stages.push(g = [s, []]); g[1].push(c); }
  view.append(h("div", { class: "lr-intro" }, h("h2", {}, "Kurser"), h("p", { class: "muted" }, "Hvert kursus har korte lektioner med læsetekst og aktiviteter. Du kan se alle kurser gratis – fremgangen gemmes på din bruger.")),
    ...(stages.length ? stages.map(([s, list]) => [h("h3", { class: "lr-stage" }, s), h("div", { class: "lr-grid" }, ...list.map(courseCard))]) : [empty("Ingen kurser endnu")]));
}
