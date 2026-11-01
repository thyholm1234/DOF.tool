// Fotos → Admin → Bannere: liste, rækkefølge, aktiv/inaktiv, datointerval og slet. Nye bannere laves fra et fotos side ("Brug som banner").
import { api, h, toast } from "../core.js";
import { BASE } from "./common.js";

export async function page() {
  const rows = await api("/api/admin/fotos/bannere");
  const patch = async (b, body) => { try { await api(`/api/admin/fotos/bannere/${b.id}`, { method: "PATCH", body }); toast("Gemt"); location.reload(); } catch (e) { toast(e.message, "err"); } };
  return h("div", { class: "stack" }, h("p", { class: "muted" }, "Ét banner pr. visning vælges tilfældigt blandt de aktive i datointervallet. Opret et nyt banner fra et publiceret fotos side (Administration → Brug som banner…)."),
    rows.length ? rows.map(b => h("div", { class: "card stack" }, b.url ? h("img", { src: b.url, alt: "Banner", style: { width: "100%", maxWidth: "600px", aspectRatio: "3 / 1", objectFit: "cover", borderRadius: "8px" } }) : null,
      h("div", {}, h("a", { href: `${BASE}/${b.photo_id}` }, `Foto ${b.photo_id}`), ` · ${b.fotograf || ""} · `, b.vises_nu ? h("b", {}, "vises nu") : h("span", { class: "muted" }, b.active ? "uden for datointervallet" : "inaktivt")),
      h("div", { class: "row" },
        h("input", { type: "text", value: b.tekst || "", placeholder: "Tekst", maxlength: 200, onchange: (e) => patch(b, { tekst: e.target.value }) }),
        h("label", { class: "field inline" }, h("span", {}, "Fra"), h("input", { type: "date", value: b.fra || "", onchange: (e) => patch(b, { fra: e.target.value || null }) })),
        h("label", { class: "field inline" }, h("span", {}, "Til"), h("input", { type: "date", value: b.til || "", onchange: (e) => patch(b, { til: e.target.value || null }) })),
        h("label", { class: "field inline" }, h("span", {}, "Rækkefølge"), h("input", { type: "number", value: b.sort, style: { width: "70px" }, onchange: (e) => patch(b, { sort: +e.target.value }) })),
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: b.active, onchange: (e) => patch(b, { active: e.target.checked }) }), h("span", {}, "Aktivt")),
        h("button", { class: "btn sm danger", type: "button", onclick: async () => { if (confirm("Slet banneret?")) { await api(`/api/admin/fotos/bannere/${b.id}`, { method: "DELETE" }); location.reload(); } } }, "Slet")))) : h("p", { class: "muted" }, "Ingen bannere endnu."));
}
