// Fotos → Admin → Indstillinger: kvoter, grænse pr. art, afvisningsgrunde og retningslinjerne (tekstsiden).
import { api, h, toast } from "../core.js";

export async function page() {
  const d = await api("/api/admin/fotos/indstillinger");
  const num = (key, label, hint) => { const i = h("input", { type: "number", min: 0, max: 1000, value: d[key] }); return [h("label", { class: "field" }, h("span", {}, label), i, hint ? h("small", { class: "muted" }, hint) : null), () => ({ [key]: +i.value })]; };
  const fields = [num("maanedlig_kvote", "Månedlig kvote", "Fotos pr. kalendermåned der publiceres uden godkendelse for privilegiet Månedlig"),
    num("max_pr_art", "Grænse pr. art", "Vises i køen; gælder ikke sjældne arter"), num("upload_max_mb", "Største billede (MB)")];
  const per = h("select", {}, ...[24, 48, 96].map(n => h("option", { value: n, selected: n === d.default_per_side }, n)));
  const grunde = h("textarea", { rows: 6, style: { width: "100%" } }); grunde.value = d.afvis_grunde.join("\n");
  const txt = h("textarea", { rows: 18, style: { width: "100%" } }); txt.value = d.tekster.retningslinjer || "";
  return h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      await api("/api/admin/fotos/indstillinger", { method: "PUT", body: Object.assign({}, ...fields.map(f => f[1]()), { default_per_side: +per.value, afvis_grunde: grunde.value.split("\n"), tekster: { retningslinjer: txt.value } }) });
      toast("Gemt");
    } catch (x) { toast(x.message, "err"); }
  } }, ...fields.map(f => f[0]), h("label", { class: "field" }, h("span", {}, "Fotos pr. side (standard)"), per),
    h("label", { class: "field" }, h("span", {}, "Standardgrunde ved afvisning (én pr. linje)"), grunde),
    h("label", { class: "field" }, h("span", {}, "Retningslinjer (markdown)"), txt), h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem")));
}
