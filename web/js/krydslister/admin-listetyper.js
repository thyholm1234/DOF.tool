// Admin → Listetyper: opret, ret og deaktivér listetyper (navn med {p}, referenceliste, auto/manuel, parameter, regler). En ændring genberegner de automatiske lister.
import { api, h, toast, modal } from "../core.js";

const PARAMS = [["", "Ingen"], ["year", "År"], ["month", "Måned"], ["afdeling", "Lokalafdeling"], ["kommune", "Kommune"]];

export async function page() {
  const d = await api("/api/admin/krydslister/listetyper");
  const body = h("div", {});
  function edit(def) {
    const f = def ? { ...def, regler: { ...def.regler } } : { key: "", navn: "", ref_list_id: d.artslister[0]?.id, auto: false, manuel: true, param: null, regler: { kategorier: ["A", "B", "C"] }, sort: 100, aktiv: true, beskrivelse_md: "" };
    const t = (k, a = {}) => h("input", { type: "text", value: f[k] ?? "", oninput: (e) => { f[k] = e.target.value; }, ...a });
    const cb = (k, label) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!f[k], onchange: (e) => { f[k] = e.target.checked; } }), h("span", {}, label));
    const regler = h("textarea", { rows: 4, style: { width: "100%", fontFamily: "monospace" } }); regler.value = JSON.stringify(f.regler, null, 1);
    const m = modal(def ? `Ret ${def.navn}` : "Ny listetype", h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      try { f.regler = JSON.parse(regler.value || "{}"); } catch { toast("Reglerne er ikke gyldig JSON", "err"); return; }
      try {
        await api(def ? `/api/admin/krydslister/listetyper/${def.id}` : "/api/admin/krydslister/listetyper", { method: def ? "PUT" : "POST", body: { ...f, param: f.param || null, sort: +f.sort || 0 } });
        toast("Gemt – de automatiske lister genberegnes"); m.close(); location.reload();
      } catch (x) { toast(x.message, "err"); }
    } }, h("label", { class: "field" }, h("span", {}, "Nøgle (små bogstaver, kan ikke ændres senere)"), t("key", { disabled: !!def })),
      h("label", { class: "field" }, h("span", {}, "Navn (brug {p} for år/måned/område)"), t("navn")),
      h("label", { class: "field" }, h("span", {}, "Referenceliste"), h("select", { onchange: (e) => { f.ref_list_id = +e.target.value; } }, ...d.artslister.map(a => h("option", { value: a.id, selected: a.id === f.ref_list_id }, a.navn)))),
      h("label", { class: "field" }, h("span", {}, "Parameter"), h("select", { onchange: (e) => { f.param = e.target.value || null; } }, ...PARAMS.map(([v, tx]) => h("option", { value: v, selected: (f.param || "") === v }, tx)))),
      cb("auto", "Beregnes automatisk fra DOFbasen"), cb("manuel", "Deltagerne kan selv krydse arter af"), cb("aktiv", "Aktiv"),
      h("label", { class: "field" }, h("span", {}, "Regler (JSON): kategorier, godkendelse, auto_fra (liste hvis kryds tæller med), kilde_liste …"), regler),
      h("label", { class: "field" }, h("span", {}, "Sortering"), t("sort", { type: "number" })), h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem"))));
  }
  const draw = () => body.replaceChildren(h("table", { class: "kl-table" }, h("thead", {}, h("tr", {}, ...["Nøgle", "Navn", "Reference", "Auto", "Manuel", "Parameter", "Regler", ""].map(x => h("th", {}, x)))),
    h("tbody", {}, ...d.defs.map(x => h("tr", { class: x.aktiv ? "" : "kl-none" }, h("td", {}, x.key), h("td", {}, x.navn), h("td", {}, d.artslister.find(a => a.id === x.ref_list_id)?.key || ""), h("td", {}, x.auto ? "ja" : ""),
      h("td", {}, x.manuel ? "ja" : ""), h("td", {}, x.param || ""), h("td", {}, h("code", {}, JSON.stringify(x.regler))),
      h("td", {}, h("button", { class: "btn sm", type: "button", onclick: () => edit(x) }, "Ret"), " ", h("button", { class: "btn sm danger", type: "button", onclick: async () => {
        if (!confirm(`Slet listetypen «${x.navn}»? De automatiske kryds på listen fjernes (manuelle kryds beholdes).`)) return;
        try { await api(`/api/admin/krydslister/listetyper/${x.id}`, { method: "DELETE" }); location.reload(); } catch (e) { toast(e.message, "err"); } } }, "Slet")))))));
  draw();
  return h("div", { class: "stack" }, h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onclick: () => edit(null) }, "+ Ny listetype"),
    h("button", { class: "btn", type: "button", onclick: async () => { await api("/api/admin/krydslister/beregn", { method: "POST" }); toast("Genberegning bestilt hos workeren"); } }, "Genberegn automatiske lister nu")), body);
}
