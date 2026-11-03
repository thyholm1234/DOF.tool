// Læring 2.0: Fugl for fugl – dagens art, ledetråde afsløres én ad gangen (docs/laering-spec.md §12.2).
import { api, h, toast, errorBox } from "../core.js";
import { BASE, backLink, mediaEl, loadingBox } from "./common.js";
import { speciesPicker } from "./pickers.js";

export async function render(view) {
  const root = h("div", { class: "lr-daily" });
  view.append(backLink(BASE, "Forside"), h("div", { class: "lr-intro" }, h("h2", {}, "Fugl for fugl"), h("p", { class: "muted" }, "Dagens art er den samme for alle. Du får én ledetråd ad gangen – jo færre du bruger, jo flere point.")), root);
  let step = 0, over = false;
  const names = new Map(), picked = new Set();
  async function draw() {
    root.replaceChildren(loadingBox());
    try {
      const d = await api(`/api/learn/dagens?trin=${step}`);
      const c = d.ledetraad;
      const clue = h("div", { class: "lr-clue" }, h("b", {}, `Ledetråd ${step + 1}: ${c.titel}`));
      if (c.type === "faenologi" && c.uger) { const max = Math.max(...c.uger, 0.0001); clue.append(h("div", { class: "lr-spark big", title: "Forekomst gennem året" }, ...c.uger.map((v, i) => h("i", { class: i === c.nu ? "now" : "", style: { height: `${Math.max(2, 100 * v / max)}%` } }))), h("small", { class: "muted" }, `Den lyse søjle er denne uge.${c.klass && c.klass !== "Alm" ? ` Status: ${c.klass}.` : ""}`)); }
      else if (c.type === "tekst") clue.append(h("p", {}, c.tekst), h("p", { class: "muted" }, `Artens navn har ${c.bogstaver} tegn.`));
      else if (c.type === "lyd") clue.append(c.ingen ? h("p", { class: "muted" }, "Ingen optagelse tilgængelig – prøv næste ledetråd.") : mediaEl(c.media));
      else if (c.type === "foto") clue.append(c.media ? mediaEl(c.media) : h("p", { class: "muted" }, "Intet foto."));
      const sp = speciesPicker(picked, names, () => {});
      const guess = async () => {
        const a = [...picked].pop();
        if (!a) { toast("Vælg en art", "err"); return; }
        const r = await api("/api/learn/dagens/gaet", { method: "POST", body: { trin: step, chosen: a } });
        if (r.correct) return end(r, true);
        if (step >= 3) return end(r, false);
        toast("Ikke den – her er næste ledetråd", "err"); picked.clear(); step++; draw();
      };
      root.replaceChildren(h("p", { class: "lr-score" }, `${d.point} point`), clue, h("div", { class: "lr-actions" }), h("div", { class: "lr-f" }, h("span", {}, "Dit bud"), sp.el),
        h("div", { class: "lr-actions" }, h("button", { class: "btn primary", onclick: guess }, "Gæt"), step < 3 ? h("button", { class: "btn", onclick: () => { step++; picked.clear(); draw(); } }, "Næste ledetråd") : null));
    } catch (e) { root.replaceChildren(errorBox(e)); }
  }
  function end(r, win) {
    over = true;
    root.replaceChildren(h("h3", {}, win ? `Rigtigt! ${r.point} point` : "Dagens fugl var …"), h("p", { class: "lr-score" }, r.art ? h("a", { href: `#/art/${r.art.artnr}` }, r.art.navn) : ""), r.art ? h("p", { class: "muted" }, h("i", {}, r.art.latin)) : null,
      h("p", { class: "muted" }, "Kom tilbage i morgen efter en ny art."), h("a", { class: "btn", href: BASE }, "Til forsiden"));
  }
  await draw();
}
