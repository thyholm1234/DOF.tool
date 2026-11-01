// Mine fotos: status (afventer/afvist med grund/publiceret), ret og slet, uploadprivilegium og indstillinger (standard-rettighed og samtykke).
import { api, h, toast } from "../core.js";
import { BASE, STATUS, photoCard, nowrap } from "./common.js";

export async function page() {
  const [d, codes] = await Promise.all([api("/api/fotos/mine"), api("/api/fotos/koder")]);
  const rett = h("select", { onchange: save }, ...codes.rettigheder.map(r => h("option", { value: r.id, selected: r.id === d.indstillinger.standard_rettighed }, r.text)));
  const samtykke = h("input", { type: "checkbox", checked: d.indstillinger.dof_samtykke, onchange: save });
  async function save() {
    try { await api("/api/fotos/mine/indstillinger", { method: "PUT", body: { standard_rettighed: +rett.value, dof_samtykke: samtykke.checked } }); toast("Gemt"); } catch (e) { toast(e.message, "err"); }
  }
  const count = (s) => d.fotos.filter(f => f.status === s).length;
  const card = (f) => h("div", { class: "fot-mine" },
    photoCard({ ...f, arter: f.arter || [], fotograf: null, w: null, h: null, gruppe: 0 }),
    f.status === "afvist" ? h("div", { class: "notice err small" }, "Afvist: ", f.afvist_grund || "ingen begrundelse") : null,
    h("div", { class: "row" }, !f.laast ? h("a", { class: "btn sm", href: `${BASE}/${f.id}/ret` }, "Ret") : h("span", { class: "muted small" }, "Låst af moderator"),
      !f.laast ? h("button", { class: "btn sm danger", type: "button", onclick: async (e) => {
        if (!confirm("Slet fotoet? Det kan ikke fortrydes.")) return;
        try { await api(`/api/fotos/${f.id}`, { method: "DELETE" }); e.target.closest(".fot-mine").remove(); toast("Fotoet er slettet"); } catch (x) { toast(x.message, "err"); }
      } }, "Slet") : null));
  return h("div", { class: "stack" },
    h("div", { class: "card stack" },
      h("div", { class: "row" }, h("b", {}, "Dit uploadprivilegium: "), d.privilegie_tekst),
      d.kvote ? h("div", { class: "muted small" }, `Denne måned: ${d.kvote.brugt} af ${d.kvote.i_alt} fotos publiceret uden godkendelse.`) : null,
      h("label", { class: "field" }, h("span", {}, "Standard-rettighed for mine fotos"), rett),
      h("label", { class: "check" }, samtykke, h("span", {}, "Jeg tillader, at DOF gør brug af mine fotos til brug i DOFs egne medier")),
      h("div", { class: "row" }, d.kan_uploade ? h("a", { class: "btn primary", href: `${BASE}/ny` }, "+ Tilføj nyt foto") : null, h("a", { href: `${BASE}/retningslinjer` }, "Retningslinjer"))),
    h("div", { class: "muted" }, `${nowrap(d.fotos.length)} fotos · ${count("publiceret")} publiceret · ${count("afventer")} afventer · ${count("afvist")} afvist`),
    d.fotos.length ? h("div", { class: "fot-grid" }, ...d.fotos.map(card)) : h("p", { class: "muted" }, "Du har ikke uploadet fotos endnu."));
}
