// Sidepanelet i feedet (desktop): kategorier med nyt siden sidste besøg, uløste bestemmelser og mest aktive tråde (docs/snak-modul-spec.md §2.1).
import { api, ago, h, store } from "../core.js";
import { BASE, cleanups } from "./common.js";

/** Sidste besøg huskes på enheden (store("snak").seen); tælleren nulstilles først, når man forlader feedet. */
export function panel(slug) {
  const since = store("snak")?.seen;
  const aside = h("aside", { class: "sn-panel", "aria-label": "Oversigt" });
  cleanups.push(() => store("snak", { ...(store("snak") || {}), seen: new Date().toISOString() }));
  api(`/api/snak/panel${since ? `?since=${encodeURIComponent(since)}` : ""}`).then((d) => {
    const list = (title, rows, href, meta) => rows.length ? h("section", { class: "sn-card" }, h("h2", { class: "sn-panel-h" }, title),
      h("ul", { class: "sn-panel-list" }, ...rows.map(r => h("li", {}, h("a", { href: href(r) }, r.titel), h("small", { class: "muted" }, meta(r)))))) : null;
    aside.append(
      h("section", { class: "sn-card" }, h("h2", { class: "sn-panel-h" }, "Kategorier"),
        h("ul", { class: "sn-panel-list" }, ...d.kategorier.map(c => h("li", {}, h("a", { href: `${BASE}/k/${c.slug}`, class: c.slug === slug ? "on" : "" }, h("span", { class: "sn-dot", style: { background: c.farve }, "aria-hidden": "true" }), c.navn),
          c.nye ? h("small", { class: "sn-new-count" }, `${c.nye} nye`) : null)))),
      list("Uløste bestemmelser", d.uloeste, r => `${BASE}/${r.id}`, r => `${r.n_comments} svar · ${ago(r.created_at)}`),
      list("Mest aktive i ugen", d.aktive, r => `${BASE}/${r.id}`, r => `${r.n_comments} kommentarer`));
    aside.dispatchEvent(new Event("snak:panel"));  // feedet markerer den valgte kategori igen
  }).catch(() => { /* sidepanelet er pynt: feedet virker uden */ });
  return aside;
}
