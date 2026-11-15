// Mine (#/snak/mine): mine opslag og tråde, jeg følger, med antal nye kommentarer siden sidst (docs/snak-modul-spec.md §2.6).
import { api, ago, empty, errorBox, fmtN, h, toast } from "../core.js";
import { BASE, categoryTag, loadCss, loadingBox } from "./common.js";

export async function page() {
  loadCss("snak");
  const box = h("div", { class: "sn-feed stack" }, h("h1", {}, "Mine"), loadingBox());
  const row = (r, extra) => h("article", { class: "sn-card sn-hit" },
    h("div", { class: "row" }, categoryTag({ slug: r.slug, navn: r.kategori, farve: r.farve }), r.status === "skjult" ? h("span", { class: "sn-chip warn" }, "Skjult") : null, extra),
    h("a", { class: "sn-title", href: `${BASE}/${r.id}` }, r.titel || r.uddrag || `Opslag ${r.id}`),
    r.titel && r.uddrag ? h("p", { class: "sn-snip muted" }, r.uddrag) : null,
    h("div", { class: "muted small" }, `💬 ${fmtN(r.n_comments)}${r.n_reactions !== undefined ? ` · ${fmtN(r.n_reactions)} reaktioner` : ""} · ${ago(r.created_at || r.last_activity_at)}`));
  async function load() {
    try {
      const d = await api("/api/snak/mine");
      const unfollow = async (r, card) => { try { await api(`/api/snak/${r.id}/foelg`, { method: "DELETE" }); card.remove(); toast("Du følger ikke tråden mere"); } catch (e) { toast(e.message, "err"); } };
      box.replaceChildren(h("h1", {}, "Mine"),
        h("section", { class: "stack", "aria-labelledby": "sn-mine-o" }, h("h2", { id: "sn-mine-o" }, "Mine opslag"), ...(d.opslag.length ? d.opslag.map(r => row(r)) : [empty("Du har ikke lavet nogen opslag endnu", "Skriv det første fra feedet.")])),
        h("section", { class: "stack", "aria-labelledby": "sn-mine-f" }, h("h2", { id: "sn-mine-f" }, "Tråde jeg følger"),
          ...(d.foelger.length ? d.foelger.map(r => { const card = row(r, r.nye ? h("span", { class: "sn-chip ok" }, `${r.nye} ${r.nye === 1 ? "ny" : "nye"}`) : null);
            card.append(h("button", { type: "button", class: "btn sm", onclick: () => unfollow(r, card) }, "Følg ikke")); return card; }) : [empty("Du følger ingen tråde", "Du følger automatisk tråde, du kommenterer.")])));
    } catch (e) { box.replaceChildren(errorBox(e)); }
  }
  document.title = "Mine – Snak";
  await load();
  return box;
}
