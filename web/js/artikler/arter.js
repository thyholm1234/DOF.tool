// Arter: alle arter, der omtales i artiklerne (i systematisk rækkefølge), og en side pr. art med artiklerne om den.
import { api, h, seg } from "../core.js";
import { BASE, nowrap } from "./common.js";
import { results, readState } from "./soeg.js";

export async function page(parts) {
  const species = await api("/api/artikler/arter");
  if (parts[1] && /^\d+$/.test(parts[1])) {
    const n = +parts[1], s = species.find(x => x.artnr === n);
    const st = { ...readState({}), art: [n], sort: "hits" };
    const box = h("div", {});
    const draw = () => box.replaceChildren(results(st, { empty: "Ingen artikler om arten endnu." }).el);
    draw();
    return h("div", {}, h("h2", {}, s ? s.navn : "Art"), s?.latin ? h("p", { class: "muted" }, h("i", {}, s.latin), ` · ${nowrap(s.n)} artikler · `, h("a", { href: `#/art/${n}` }, "Artsside med observationer")) : null,
      seg([["hits", "Mest om arten først"], ["nyeste", "Nyeste først"]], st.sort, (v) => { st.sort = v; st.side = 1; draw(); }), box);
  }
  let filter = "";
  const body = h("div", { class: "art-cols" });
  const draw = () => body.replaceChildren(...species.filter(s => !filter || s.navn.toLowerCase().includes(filter) || (s.latin || "").toLowerCase().includes(filter))
    .map(s => h("div", {}, h("a", { href: `${BASE}/arter/${s.artnr}` }, s.navn), " ", h("small", {}, `(${nowrap(s.n)})`))));
  draw();
  return h("div", {}, h("h2", {}, "Arter i artiklerne"), h("p", { class: "muted" }, `${nowrap(species.length)} arter omtales. Arterne findes automatisk ud fra emneord og tekst (også PDF-tekst).`),
    h("input", { type: "search", placeholder: "Find art …", style: { width: "100%", maxWidth: "360px", marginBottom: "12px" }, oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); } }), body);
}
