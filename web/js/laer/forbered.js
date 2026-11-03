// Læring 2.0: Forbered en tur – forventede arter på en lokalitet i en uge (docs/laering-spec.md §7).
import { api, h, toast, errorBox } from "../core.js";
import { BASE, loadingBox } from "./common.js";
import { locationPicker } from "./pickers.js";

export async function render(view) {
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  let place = q.get("loknr") ? { loknr: +q.get("loknr"), navn: `Lokalitet ${q.get("loknr")}` } : null;
  let dato = q.get("dato") || new Date().toISOString().slice(0, 10);
  const out = h("div", {});
  const lp = locationPicker(place, (v) => { place = v; go(); });
  const date = h("input", { type: "date", value: dato, "aria-label": "Dato", onchange: (e) => { dato = e.target.value; go(); } });
  view.append(h("div", { class: "lr-intro" }, h("h2", {}, "Forbered din næste tur"), h("p", { class: "muted" }, "Vælg sted og dato. Du får de arter, du sandsynligvis møder, ud fra DOFbasens observationer – og kan træne dem bagefter.")),
    h("div", { class: "lr-setup" }, h("label", {}, "Lokalitet", lp.el), h("label", {}, "Dato", date), h("button", { class: "btn primary", onclick: go }, "Vis arter")), out);

  async function go() {
    out.replaceChildren(loadingBox());
    try {
      const d = await api(`/api/learn/forbered?dato=${dato}${place ? `&loknr=${place.loknr}` : ""}`);
      const train = () => { sessionStorage.setItem("lr:forbered", JSON.stringify(d.alle)); location.hash = `${BASE}/traen`; };
      out.replaceChildren(
        h("p", { class: "lr-expl" }, d.sted ? [h("b", {}, d.sted.navn), ` · uge ${d.uge}`, d.ture ? ` · ${d.ture} ture i grundlaget` : ""] : `Uge ${d.uge}`, d.note ? h("br") : null, d.note ? h("span", { class: "muted" }, d.note) : null),
        ...d.grupper.filter(g => g.arter.length).map(g => h("section", {}, h("h3", {}, `${g.navn} (${g.arter.length})`),
          h("div", { class: "lr-species" }, ...g.arter.map(a => h("a", { class: "lr-sp", href: `#/art/${a.artnr}` },
            a.foto ? h("img", { src: a.foto, alt: "", loading: "lazy" }) : h("span", { class: "lr-nophoto" }), h("b", {}, a.navn), h("small", { class: "muted" }, `${Math.round(a.andel * 100)} %`)))))),
        d.alle.length ? h("div", { class: "lr-actions" }, h("button", { class: "btn primary", onclick: async () => {
          try { await api("/api/learn/traeninger", { method: "POST", body: { title: `Tur: ${d.sted?.navn || "hele landet"} uge ${d.uge}`, kilde: "arter", arter: d.alle.slice(0, 60), m: "foto", metode: "valg", rtype: "begge", n: 20 } }); toast("Gemt under Træn → Mine træninger"); location.hash = `${BASE}/traen`; } catch (e) { toast(e.message, "err"); }
        } }, "Gem som træning (de 60 mest sandsynlige)")) : null);
    } catch (e) { out.replaceChildren(errorBox(e)); }
  }
  if (place) go();
}
