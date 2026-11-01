// Artsside: fund fra DOFbasen (pr. måned og lokalafdeling; alle fund for sjældne arter), kryds på listerne og fotos fra galleriet.
import { api, h, fmtN } from "../core.js";
import { BASE, link, title, notice } from "./common.js";

const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

export async function page(parts) {
  const d = await api(`/api/krydslister/art/${parts[1]}`);
  const s = d.dofb;
  const mx = s ? Math.max(1, ...s.months.map(m => m.n)) : 1;
  const box = h("div", { class: "stack" },
    h("h2", {}, d.su ? h("b", { class: "kl-su" }, "* ") : null, d.navn, " ", h("i", { class: "muted" }, d.latin)),
    h("p", { class: "muted" }, [d.engelsk, d.familie, d.orden].filter(Boolean).join(" · ")),
    h("p", { class: "muted small" }, d.lister.map(l => `${l.navn}${l.kategorier?.length ? ` (kategori ${l.kategorier.join(", ")})` : ""}`).join(" · ")),
    h("div", { class: "row" }, link("/artslister", "← Artslister"), d.artnr ? h("a", { href: `#/art/${d.artnr}` }, "Artsside i DOF.tool →") : null,
    ));
  if (d.kryds.length) box.append(title("Kryds"), h("div", { class: "stack" }, ...d.kryds.map(k => link(`/kryds/${encodeURIComponent(k.liste)}/art/${encodeURIComponent(k.key)}`, `${k.navn}: krydset af ${k.antal} af ${k.deltagere} deltagere`))));
  if (s) {
    box.append(title("Fund i DOFbasen"), h("p", {}, `${fmtN(s.fund)} fund (dato og lokalitet) med ${fmtN(s.obs)} observationer`, s.first ? `, fra ${s.first} til ${s.last}` : "", "."),
      h("div", { class: "kl-cols" },
        h("div", {}, h("b", {}, "Fund pr. måned"), h("div", { class: "kl-bars" }, ...Array.from({ length: 12 }, (_, i) => { const n = s.months.find(m => m.m === i + 1)?.n || 0; return h("div", { title: `${n}`, style: { height: `${(n / mx) * 100}%` } }); })),
          h("div", { class: "kl-barlabels" }, ...MONTHS.map(m => h("span", {}, m)))),
        h("div", {}, h("b", {}, "Fund pr. lokalafdeling"), h("table", { class: "kl-table small" }, h("tbody", {}, ...s.afdelinger.map(a => h("tr", {}, h("td", {}, a.navn), h("td", { class: "num" }, fmtN(a.n)))))))));
    if (s.fundliste.length) box.append(title(`Samtlige fund (${s.fundliste.length})`), h("div", { class: "kl-scroll", style: { maxHeight: "460px" } }, h("table", { class: "kl-table small" },
      h("thead", {}, h("tr", {}, ...["Dato", "Lokalitet", "Antal", "Observatør"].map(t => h("th", {}, t)))),
      h("tbody", {}, ...s.fundliste.map(r => h("tr", {}, h("td", {}, r.dato), h("td", {}, h("a", { href: `#/dofbasen/obs/${r.obsid}` }, r.lokalitet || r.loknr)), h("td", { class: "num" }, fmtN(r.antal)), h("td", {}, r.observator || "")))))));
  } else if (!d.artnr) box.append(notice("Arten er ikke koblet til en art i DOFbasen (kobles under Admin → World Bird List).", "info"));
  if (d.fotos) {
    const q = new URLSearchParams({ taxon: d.id });
    if (d.fotos.artnr) q.set("art", d.fotos.artnr);
    box.append(title("Fotos"), d.fotos.rows.length ? h("div", { class: "kl-photos" }, ...d.fotos.rows.map(p => h("a", { href: `#/fotos/${p.id}` }, h("img", { src: p.thumb, alt: d.navn, loading: "lazy" }))),
      h("a", { href: `#/fotos?${q}` }, `Se alle ${d.fotos.antal} fotos →`)) : h("p", { class: "muted" }, "Ingen fotos af arten i galleriet endnu."));
  }
  return box;
}
