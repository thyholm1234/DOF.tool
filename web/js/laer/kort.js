// Læring 2.0: kort til kurser og øvelser.
import { h } from "../core.js";
import { BASE, art, chip, bar, KINDS, LEVELS } from "./common.js";

export function courseCard(c) {
  const p = c.progress;
  const href = c.kind === "kursus" ? `${BASE}/kursus/${c.slug}` : `${BASE}/ovelse/${c.slug}`;
  return h("a", { class: "lr-card", href },
    art(c),
    h("div", { class: "lr-card-body" },
      h("div", { class: "lr-kicker" }, c.kind === "kursus" ? [c.number ? `Kursus ${c.number}` : "Kursus", c.stage ? ` · ${c.stage}` : ""] : [KINDS[c.kind] || "Øvelse", c.category ? ` · ${c.category}` : ""]),
      h("h3", {}, c.title),
      h("p", { class: "muted" }, c.card || c.summary),
      h("div", { class: "lr-meta" }, c.hours ? chip(c.hours) : null, c.n_acts ? chip(`${c.n_acts} aktiviteter`) : null, c.published ? null : chip("kladde", "warn"),
        c.level && c.kind === "kursus" ? chip(LEVELS[c.level] || c.level) : null, ...(c.skills || []).slice(0, 3).map(s => chip(s, "soft"))),
      p && p.total ? [bar(p.done, p.total, `${p.done} af ${p.total} lektioner`), h("small", { class: "muted" }, p.done >= p.total ? "Gennemført" : `${p.done} af ${p.total} lektioner`)] : null));
}
