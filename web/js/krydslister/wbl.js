// World Bird List: orden → familie → art, med søgning. Arter linker til artssiden.
import { api, h } from "../core.js";
import { BASE, link, loadingBox } from "./common.js";

export async function page(parts, q) {
  const box = h("div", {}, loadingBox());
  let t;
  const filter = h("input", { type: "search", placeholder: "Søg art, familie eller orden …", value: q.q || "", style: { width: "300px", maxWidth: "100%" } });
  async function load() {
    const d = await api("/api/krydslister/wbl?q=" + encodeURIComponent(filter.value.trim()));
    const groups = [];
    for (const r of d.rows) {
      const o = r.orden || "Uden orden", f = r.familie || "";
      let g = groups.at(-1);
      if (!g || g.orden !== o) { g = { orden: o, fam: [] }; groups.push(g); }
      let fm = g.fam.at(-1);
      if (!fm || fm.familie !== f) { fm = { familie: f, rows: [] }; g.fam.push(fm); }
      fm.rows.push(r);
    }
    box.replaceChildren(h("p", { class: "muted small" }, `${d.total.toLocaleString("da-DK")} arter${d.version ? ` · ${d.version}` : ""}${d.rows.length < d.total ? ` · viser ${d.rows.length}` : ""}`),
      ...groups.map(g => h("details", { class: "kl-orden", open: groups.length < 4 || !!filter.value }, h("summary", {}, h("b", {}, g.orden)),
        ...g.fam.map(fm => h("div", { class: "kl-fam" }, fm.familie ? h("h5", {}, fm.familie) : null,
          h("div", { class: "kl-arter" }, ...fm.rows.map(r => link(`/art/${r.id}`, r.dansk || r.engelsk || r.latin, { title: r.latin }))))))));
  }
  filter.oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  await load();
  return h("div", { class: "stack" }, h("nav", { class: "kl-subtabs" }, h("a", { href: `${BASE}/artslister` }, "Artslister"), h("a", { href: `${BASE}/wbl`, class: "on" }, "World Bird List")),
    h("p", {}, "World Bird List (AviList) som artsregister. Familie og orden vises, når AviList er indlæst under Admin → World Bird List."), filter, box);
}
