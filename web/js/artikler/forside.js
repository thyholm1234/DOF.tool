// Forsiden: hovedhistorie med tre sekundære ved siden af, et gitter med de seneste (med billeder) og «på denne dag».
import { api, h } from "../core.js";
import { BASE, cover, meta, dkDate, ctx, listCard, nowrap, tagChips, loadingBox } from "./common.js";

const card = (a, sub) => h("a", { class: "art-card", href: `${BASE}/${a.id}` }, cover(a, { ratio: "3 / 2" }),
  h("div", { class: "art-card-body" }, h("h3", {}, a.title), sub || meta(a)));

export async function page() {
  const f = await api("/api/artikler/forside");
  if (!f.hero) {
    return h("div", { class: "card", style: { textAlign: "center", padding: "40px 16px" } }, h("h2", {}, "Der er ingen artikler endnu"),
      ctx().admin ? h("p", {}, "Indlæs artikelarkivet fra en zip-fil under ", h("a", { href: `${BASE}/admin/import` }, "Admin → Import"), ".") : h("p", { class: "muted" }, "Kig forbi igen senere."));
  }
  const hero = f.hero;
  return h("div", {},
    h("div", { class: "art-front" },
      h("a", { class: "art-hero", href: `${BASE}/${hero.id}` }, cover(hero, { size: "medium", ratio: "16 / 9" }),
        h("div", { class: "art-hero-body" }, h("h2", {}, hero.title), meta(hero), hero.teaser ? h("p", { class: "lead" }, hero.teaser) : null)),
      f.secondary.length ? h("div", { class: "art-secondary" }, ...f.secondary.map(a => h("a", { class: "art-mini", href: `${BASE}/${a.id}` },
        cover(a, { ratio: "1 / 1" }), h("div", {}, h("h3", {}, a.title), meta(a))))) : null),
    f.latest.length ? h("section", { class: "art-sec" }, h("div", { class: "art-sec-head" }, h("h3", {}, "Seneste"), h("a", { href: `${BASE}/soeg?sort=nyeste` }, "Se alle")),
      h("div", { class: "art-cards" }, ...f.latest.map(a => card(a)))) : null,
    f.on_this_day.length ? h("section", { class: "art-sec" }, h("div", { class: "art-sec-head" }, h("h3", {}, "På denne dag")),
      h("div", { class: "art-cards" }, ...f.on_this_day.map(a => card(a, h("div", { class: "art-meta" }, dkDate(a.dato, { long: true })))))) : null,
    h("p", { class: "muted", style: { textAlign: "center", marginTop: "30px" } }, `${nowrap(f.total)} artikler i arkivet · `, h("a", { href: `${BASE}/soeg` }, "Søg i dem alle")));
}
export { listCard, tagChips, loadingBox };
