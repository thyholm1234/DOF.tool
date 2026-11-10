// Admin → Tællinger → Gridceller: tællingerne samlet pr. kvadrat i Det Danske Kvadratnet (UTM 32, 1/2/5/10 km).
// Farven viser det valgte mål (sekventiel skala i én tone, lys → mørk); klik på en celle for arterne og tællingerne i den.
import { api, h, fmtN, fmtDate, errorBox, empty, seg, params, setParams, loading } from "../../core.js";
import { baseMap } from "../../maps.js";
import { BASE, filterBar, tripTable } from "./taellinger.js";

const SIZES = [["1", "1 km"], ["2", "2 km"], ["5", "5 km"], ["10", "10 km"]];
const METRICS = [["taellinger", "Tællinger"], ["arter", "Arter i alt"], ["arter_pr_taelling", "Arter pr. tælling"], ["observatoerer", "Observatører"]];
// Sekventiel skala i portalens grønne tone (lys → mørk); de lyse trin har en mørkere kant, så de ses på kortet
const RAMP = ["#d6ece1", "#a7d3bd", "#72b394", "#3f8f6c", "#1f6246"];
const dec = (x) => String(x).replace(".", ",");

/** Grænser til 5 klasser: kvantiler (afrundet), så både få og mange tællinger kan skelnes. */
function breaks(values) {
  const v = [...values].sort((a, b) => a - b);
  if (!v.length) return [];
  const q = [0.2, 0.4, 0.6, 0.8].map(p => v[Math.min(v.length - 1, Math.floor(p * v.length))]);
  return [...new Set(q)].filter(x => x < v[v.length - 1]);
}

export async function renderGrid(body) {
  const p = params();
  let km = SIZES.some(([k]) => k === p.km) ? p.km : "10";
  let metric = METRICS.some(([k]) => k === p.maal) ? p.maal : "taellinger";
  const bar = filterBar(() => load());
  const legend = h("div", { class: "row wrap", style: { gap: "10px", alignItems: "center", fontSize: ".85rem" } });
  const info = h("small", { class: "muted" });
  const mapEl = h("div", { class: "map", style: { height: "min(70vh, 640px)" } });
  const side = h("div", { class: "card pulse-side stack" });
  const tableEl = h("div", { class: "card mt" });
  body.append(bar.el,
    h("div", { class: "row wrap", style: { gap: "10px", alignItems: "center", marginBottom: "8px" } },
      h("span", { class: "muted" }, "Cellestørrelse"), seg(SIZES, km, (v) => { km = v; setParams({ km: v }); load(); }),
      h("span", { class: "muted" }, "Farve efter"), seg(METRICS, metric, (v) => { metric = v; setParams({ maal: v }); paint(); }), info),
    legend,
    h("div", { class: "pulse-layout mt" }, mapEl, side), tableEl);

  const map = baseMap(mapEl, { center: [56.1, 10.8], zoom: 7 });
  const cellLayer = L.layerGroup().addTo(map);
  let D = { celler: [] }, selected = null, seq = 0, fitted = false;
  const intro = () => side.replaceChildren(h("h3", { style: { margin: 0 } }, "Gridceller"),
    h("p", { class: "muted", style: { margin: 0 } }, "Kvadrater i Det Danske Kvadratnet (UTM zone 32, ETRS89). Kun tællinger med position er med. Klik på en celle for arterne og tællingerne i den."));

  async function load() {
    const my = ++seq;
    info.textContent = "Henter …";
    try { D = await api(`${BASE}/grid?${bar.qs({ km })}`); } catch (err) { if (my === seq) side.replaceChildren(errorBox(err)); return; }
    if (my !== seq) return;
    info.textContent = `${fmtN(D.celler.length)} celler · ${fmtN(D.taellinger)} tællinger med position`;
    paint();
    if (!fitted && D.celler.length) { fitted = true; map.fitBounds(L.latLngBounds(D.celler.flatMap(c => c.ring)), { padding: [20, 20], maxZoom: 11 }); }
    if (selected) openCell(selected, false); else intro();
    drawTable();
  }

  function paint() {
    cellLayer.clearLayers();
    const label = METRICS.find(([k]) => k === metric)[1];
    const vals = D.celler.map(c => c[metric]);
    const b = breaks(vals);
    const tone = (i) => RAMP[b.length ? Math.round(i * (RAMP.length - 1) / b.length) : 3];  // én klasse: en mellemtone
    const cls = (v) => { let i = 0; while (i < b.length && v > b[i]) i++; return tone(i); };
    for (const c of D.celler) {
      const col = cls(c[metric]);
      L.polygon(c.ring, { color: c.code === selected ? "#ffb300" : "#ffffff", weight: c.code === selected ? 3 : 1, fillColor: col, fillOpacity: 0.78 })
        .bindTooltip(`<b>${c.code}</b><br>${fmtN(c.taellinger)} tællinger (${fmtN(c.spontan)} spontan, ${fmtN(c.finn)} FiNN)<br>${fmtN(c.arter)} arter · ${dec(c.arter_pr_taelling)} pr. tælling<br>${fmtN(c.observatoerer)} observatører · seneste ${fmtDate(c.seneste)}`, { sticky: true })
        .on("click", () => openCell(c.code, false)).addTo(cellLayer);
    }
    // Forklaring: klassernes intervaller
    const max = vals.length ? Math.max(...vals) : 0, min = vals.length ? Math.min(...vals) : 0;
    const edges = [min, ...b, max];
    const steps = edges.length > 1 ? edges.slice(0, -1).map((lo, i) => [lo, edges[i + 1], i]) : [[min, max, 0]];
    const fmt = metric === "arter_pr_taelling" ? dec : fmtN;
    legend.replaceChildren(h("b", {}, label),
      ...(vals.length ? steps.map(([lo, hi, i]) => h("span", { class: "row", style: { gap: "4px" } },
        h("span", { style: { width: "16px", height: "12px", borderRadius: "3px", background: tone(i), border: "1px solid var(--line)" } }),
        i === 0 ? `${fmt(lo)}–${fmt(hi)}` : `>${fmt(lo)}–${fmt(hi)}`)) : [h("span", { class: "muted" }, "Ingen tællinger med position i filteret")]));
  }

  async function openCell(code, fit) {
    selected = code;
    paint();
    loading(side, 4);
    let d;
    try { d = await api(`${BASE}/grid/${code}?${bar.qs()}`); } catch (err) { side.replaceChildren(errorBox(err)); return; }
    if (selected !== code) return;
    if (fit) map.fitBounds(L.latLngBounds(d.ring), { maxZoom: 13, padding: [30, 30] });
    const c = D.celler.find(x => x.code === code);
    side.replaceChildren(
      h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, code),
        h("button", { class: "btn sm ghost", type: "button", onclick: () => { selected = null; paint(); intro(); } }, "Luk")),
      c ? h("div", { class: "muted", style: { fontSize: ".9rem" } }, `${fmtN(c.taellinger)} tællinger · ${fmtN(c.observatoerer)} observatører · ${fmtN(c.arter)} arter · ${dec(c.arter_pr_taelling)} arter pr. tælling · ${fmtN(c.individer)} individer`) : null,
      d.arter.length ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", { style: { textAlign: "right" } }, "Frekvens"), h("th", { style: { textAlign: "right" } }, "Individer"))),
        h("tbody", {}, ...d.arter.map(a => h("tr", {}, h("td", {}, a.navn || a.artnr),
          h("td", { style: { textAlign: "right" }, title: `${a.taellinger} af ${d.taellinger} tællinger` }, `${Math.round(a.frekvens * 100)} %`),
          h("td", { style: { textAlign: "right" } }, fmtN(a.individer))))))) : empty("Ingen observationer"),
      h("details", { open: d.rows.length <= 10 }, h("summary", {}, `Tællinger i cellen (${fmtN(d.taellinger)})`), tripTable(d.rows, { compact: true })));
  }

  function drawTable() {
    if (!D.celler.length) { tableEl.replaceChildren(empty("Ingen gridceller", "Ingen tællinger med position i filteret.")); return; }
    const rows = D.celler.slice(0, 200);
    tableEl.replaceChildren(h("h3", {}, `Celler (${fmtN(D.celler.length)})`), h("div", { class: "table-wrap", style: { maxHeight: "420px", overflow: "auto" } }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ...["Celle", "Tællinger", "Spontan", "FiNN", "Observatører", "Arter", "Arter pr. tælling", "Seneste"].map((t, i) => h("th", { style: i ? { textAlign: "right" } : {} }, t)))),
      h("tbody", {}, ...rows.map(c => h("tr", { class: "click", onclick: () => { openCell(c.code, true); mapEl.scrollIntoView({ behavior: "smooth", block: "center" }); } },
        h("td", {}, c.code), ...[fmtN(c.taellinger), fmtN(c.spontan), fmtN(c.finn), fmtN(c.observatoerer), fmtN(c.arter), dec(c.arter_pr_taelling), fmtDate(c.seneste)]
          .map(v => h("td", { style: { textAlign: "right" } }, v))))))),
      D.celler.length > rows.length ? h("small", { class: "muted" }, `De ${rows.length} celler med flest tællinger. Hele listen fås som udtræk (gridcellen står i hver række).`) : null);
  }

  intro();
  await load();
  return () => map.remove();
}
