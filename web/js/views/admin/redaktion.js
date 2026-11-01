// Redaktion → Oversigt: alt der venter på en moderator, samlet fra modulerne (fotos, fotokommentarer, artikelkommentarer, kryds).
// Selve behandlingen sker i modulets egen kø – linket fører derhen.
import { api, h, fmtN, errorBox, empty } from "../../core.js";

export async function overview(body) {
  body.append(h("div", { class: "skeleton", style: { height: "120px" } }));
  let d;
  try { d = await api("/api/admin/redaktion"); } catch (err) { body.replaceChildren(errorBox(err)); return; }
  const waiting = d.koeer.reduce((n, k) => n + k.antal, 0);
  body.replaceChildren(
    h("p", { class: "muted" }, waiting ? `${fmtN(waiting)} venter i alt.` : "Intet venter lige nu."),
    d.koeer.length ? h("div", { class: "card" }, h("ul", { class: "mini-list" }, ...d.koeer.map(k => h("li", {},
      h("a", { href: k.href }, h("span", { class: "muted" }, `${k.modul}: `), k.navn),
      h("b", { class: `num ${k.antal ? "" : "muted"}` }, fmtN(k.antal))))))
      : empty("Ingen køer", "Du har ikke moderationsrettigheder i Fotos, Artikler eller Krydslister."));
}
