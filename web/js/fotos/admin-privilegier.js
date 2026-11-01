// Fotos → Admin → Privilegier: hvem må uploade uden godkendelse (Månedlig/Ubegrænset), og hvem er udelukket.
import { api, h, toast } from "../core.js";
import { dkDate } from "./common.js";

export async function page() {
  const d = await api("/api/admin/fotos/privilegier");
  const out = h("div", { class: "stack" });
  const label = Object.fromEntries(d.niveauer.map(n => [n.niveau, n.tekst]));
  async function setLevel(uid, niveau, note) {
    try { await api(`/api/admin/fotos/privilegier/${uid}`, { method: "PUT", body: { niveau, note: note || null } }); toast("Gemt"); location.reload(); } catch (e) { toast(e.message, "err"); }
  }
  const q = h("input", { type: "search", placeholder: "Søg bruger (navn eller obserkode)", style: { width: "280px" } }), found = h("div", { class: "stack" });
  let t;
  q.oninput = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const rows = await api("/api/admin/fotos/brugere?q=" + encodeURIComponent(q.value)).catch(() => []);
      found.replaceChildren(...rows.map(u => {
        const sel = h("select", {}, ...d.niveauer.map(n => h("option", { value: n.niveau, selected: n.niveau === u.niveau }, n.niveau)));
        const note = h("input", { type: "text", placeholder: "Note (valgfri)", maxlength: 300 });
        return h("div", { class: "row" }, h("b", {}, u.navn || u.obserkode), h("span", { class: "muted" }, `(${u.obserkode})`), sel, note, h("button", { class: "btn sm primary", type: "button", onclick: () => setLevel(u.id, sel.value, note.value) }, "Gem"));
      }));
    }, 250);
  };
  out.append(h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Privilegier"),
    h("ul", {}, ...d.niveauer.map(n => h("li", {}, h("b", {}, n.niveau), " – ", n.tekst))),
    h("p", { class: "muted small" }, "Administratorer og fotomoderatorer er altid ubegrænsede."), q, found),
    h("h3", {}, "Brugere med særligt privilegium"),
    d.brugere.length ? h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ...["Bruger", "Niveau", "Note", "Sat", ""].map(x => h("th", {}, x)))),
      h("tbody", {}, ...d.brugere.map(u => h("tr", {}, h("td", {}, u.navn, h("small", { class: "muted" }, ` (${u.obserkode})`)), h("td", {}, label[u.niveau] ? u.niveau : u.niveau), h("td", {}, u.note || ""),
        h("td", {}, dkDate(u.set_at), u.af ? ` · ${u.af}` : ""), h("td", {}, h("button", { class: "btn sm", type: "button", onclick: async () => {
          try { await api(`/api/admin/fotos/privilegier/${u.user_id}`, { method: "DELETE" }); location.reload(); } catch (e) { toast(e.message, "err"); } } }, "Nulstil")))))) : h("p", { class: "muted" }, "Ingen endnu."));
  return out;
}
