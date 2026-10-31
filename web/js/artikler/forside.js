// Avisforsiden: hovedhistorie, tre kolonner, spalte med seneste/mest læst/mest kommenteret, «på denne dag», arter i fokus og arkivets PDF'er.
import { api, h } from "../core.js";
import { BASE, cover, meta, dkDate, ctx, listCard, nowrap, tagChips, loadingBox } from "./common.js";

const li = (a, extra) => h("li", {}, h("a", { href: `${BASE}/${a.id}` }, a.title), h("small", {}, dkDate(a.dato), extra || ""));

export async function page() {
  const f = await api("/api/artikler/forside");
  if (!f.hero) {
    return h("div", { class: "card", style: { textAlign: "center", padding: "40px 16px" } }, h("h2", {}, "Der er ingen artikler endnu"),
      ctx().admin ? h("p", {}, "Indlæs artikelarkivet fra en zip-fil under ", h("a", { href: `${BASE}/admin/import` }, "Admin → Import"), ".") : h("p", { class: "muted" }, "Kig forbi igen senere."));
  }
  const hero = f.hero;
  const main = h("div", {},
    h("a", { class: "art-hero", href: `${BASE}/${hero.id}` }, cover(hero, { size: "medium", ratio: "16 / 9" }), h("h2", {}, hero.title), meta(hero),
      hero.teaser ? h("p", { class: "lead" }, hero.teaser) : null),
    f.secondary.length ? h("div", { class: "art-three" }, ...f.secondary.map(a => h("a", { href: `${BASE}/${a.id}` }, cover(a, { ratio: "3 / 2" }), h("h3", {}, a.title), meta(a), h("p", {}, a.teaser)))) : null);
  const side = h("aside", { class: "art-side" },
    f.latest.length ? h("section", {}, h("h4", {}, "Seneste"), h("ol", {}, ...f.latest.map(a => li(a, a.kommentarer ? ` · 💬 ${a.kommentarer}` : "")))) : null,
    f.most_read.length ? h("section", {}, h("h4", {}, "Mest læst"), h("ol", {}, ...f.most_read.map(a => li(a, ` · ${nowrap(a.views)} læsninger`)))) : null,
    f.most_commented.length ? h("section", {}, h("h4", {}, "Mest kommenteret"), h("ol", {}, ...f.most_commented.map(a => li(a, ` · 💬 ${a.kommentarer}`)))) : null);
  return h("div", {},
    h("div", { class: "art-front" }, main, side),
    f.on_this_day.length ? h("section", { class: "art-sec" }, h("h3", {}, "På denne dag"),
      h("div", { class: "art-cards" }, ...f.on_this_day.map(a => h("a", { href: `${BASE}/${a.id}` }, cover(a, { ratio: "3 / 2" }), h("h3", {}, a.title), h("div", { class: "art-meta" }, dkDate(a.dato, { long: true })))))) : null,
    f.species_focus.length ? h("section", { class: "art-sec" }, h("h3", {}, "Arter i fokus"),
      h("div", { class: "art-focus" }, ...f.species_focus.map(s => h("a", { href: `${BASE}/arter/${s.artnr}` }, s.navn, " ", h("small", {}, `(${s.n})`))))) : null,
    f.archive_pdfs.length ? h("section", { class: "art-sec" }, h("h3", {}, "Fra arkivet – tidsskriftsartikler som PDF"),
      h("div", { class: "art-cards" }, ...f.archive_pdfs.map(a => h("a", { href: `${BASE}/${a.id}` }, cover(a, { ratio: "3 / 4" }), h("h3", {}, a.title), h("div", { class: "art-meta" }, dkDate(a.dato)))))) : null,
    h("p", { class: "muted", style: { textAlign: "center", marginTop: "30px" } }, `${nowrap(f.total)} artikler i arkivet · `, h("a", { href: `${BASE}/soeg` }, "Søg i dem alle")));
}
export { listCard, tagChips, loadingBox };
