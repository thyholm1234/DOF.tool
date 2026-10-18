import { api, h, fmtN, fmtDate, tag, errorBox, KAT } from "../core.js";
import { baseMap } from "../maps.js";

export async function render(view, [loknr]) {
  let d;
  try { d = await api(`/api/locations/${loknr}`); } catch (err) { view.append(errorBox(err)); return; }
  const mapEl = h("div", { class: "map small" });
  let q = "";
  const tbody = h("tbody");
  const drawSpecies = () => tbody.replaceChildren(...d.species.filter(s => !q || s.navn.toLowerCase().includes(q)).map(s => h("tr", {},
    h("td", {}, s.navn), h("td", { class: "right num" }, fmtN(s.n_obs)), h("td", { class: "nowrap" }, fmtDate(s.last_date)))));
  view.append(
    h("p", {}, h("a", { href: "javascript:history.back()" }, "← Tilbage")),
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, d.navn), h("p", { class: "lead" }, [d.kommune && `${d.kommune} Kommune`, d.afdeling].filter(Boolean).join(" · ")))),
    h("div", { class: "stats", style: { gridTemplateColumns: "repeat(3, 1fr)" } },
      h("div", { class: "stat", title: "Én pr. art pr. dag – flere turlister samme dag tæller ikke dobbelt" }, h("b", {}, fmtN(d.stats.n_obs)), h("span", {}, "observationer")),
      h("div", { class: "stat" }, h("b", {}, fmtN(d.stats.n_taxa)), h("span", {}, "arter/taxa")),
      h("div", { class: "stat" }, h("b", {}, d.stats.first ? new Date(d.stats.first).getFullYear() : "–"), h("span", {}, "første år"))),
    h("div", { class: "grid-2 mt lok-grid" },
      h("div", { class: "lok-col" }, h("div", { class: "card", style: { padding: "8px" } }, mapEl),
        h("div", { class: "card lok-fill" }, h("h3", { style: { marginTop: 0 } }, "Seneste 30 dage"),
          h("small", { class: "muted" }, "Største antal pr. art pr. dag"),
          d.recent.length ? h("div", { class: "lok-scroll" }, h("ul", { class: "mini-list" }, ...d.recent.map(r => h("li", {},
          h("span", {}, fmtDate(r.dato, false), " · ", h("a", { href: `#/traad/${r.dato}/${r.artnr}/${loknr}` }, r.art)),
          h("span", {}, r.kategori !== "alm" ? tag(r.kategori, KAT[r.kategori]) : null, " ", h("b", { class: "num" }, fmtN(r.antal))))))) : h("p", { class: "muted" }, "Ingen observationer."))),
      h("div", { class: "card lok-fill" }, h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Arter på lokaliteten"),
        h("input", { type: "search", placeholder: "Søg…", style: { width: "160px" }, oninput: (e) => { q = e.target.value.toLowerCase(); drawSpecies(); } })),
        h("small", { class: "muted" }, "Obs. = antal dage med arten"),
        d.species.length ? h("div", { class: "table-wrap lok-scroll" }, h("table", { class: "data" },
          h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", { class: "right" }, "Obs."), h("th", {}, "Senest"))), tbody))
          : h("p", { class: "muted mt" }, "Ingen observationer i databasen."))));
  drawSpecies();
  const map = baseMap(mapEl, { center: d.lat ? [d.lat, d.lon] : [56, 10.5], zoom: d.lat ? 12 : 6 });
  if (d.lat) L.marker([d.lat, d.lon]).addTo(map);
  return () => map.remove();
}
