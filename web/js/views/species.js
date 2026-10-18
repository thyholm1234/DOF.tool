import { api, h, fmtN, fmtDate, doyLabel, tag, errorBox, empty, KAT } from "../core.js";
import { lineChart, seriesLegend, yearDots, PALETTE } from "../charts.js";
import { baseMap, katColor } from "../maps.js";

const WEEK_TICKS = [0, 4, 8, 13, 17, 21, 26, 30, 34, 39, 43, 47];
const MON = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

export async function render(view, [artnr]) {
  view.append(h("div", { class: "skeleton", style: { height: "160px" } }));
  let sp, ph;
  try { [sp, ph] = await Promise.all([api(`/api/species/${artnr}`), api(`/api/pheno/species/${artnr}`)]); }
  catch (err) { view.replaceChildren(errorBox(err)); return; }
  const mapEl = h("div", { class: "map small" });
  view.replaceChildren(
    h("p", {}, h("a", { href: "javascript:history.back()" }, "← Tilbage")),
    h("div", { class: "page-head" }, h("div", {},
      h("div", { class: "row" }, sp.klass !== "Alm" ? tag(sp.klass) : null, h("span", { class: "muted" }, ({ sommer: "sommergæst", vinter: "vintergæst", passage: "gennemtrækker/standfugl" })[ph.summary?.profile] || ph.type || "")),
      h("h1", {}, sp.navn), sp.latin ? h("p", { class: "lead" }, h("i", {}, sp.latin)) : null)),
    h("div", { class: "stats", style: { gridTemplateColumns: "repeat(3, 1fr)" } },
      stat(fmtN(sp.total?.n_obs), "observationer i alt"), stat(fmtN(sp.total?.n_days), "dage med fund"), stat(sp.total?.last_date ? fmtDate(sp.total.last_date) : "–", "senest før i dag")),
    h("div", { class: "card mt" }, h("h2", {}, "Forekomst gennem året"), phenoChart(ph), summaryBox(ph.summary)),
    h("div", { class: "grid-2 mt" },
      h("div", { class: "card" }, h("h3", {}, "Fund de seneste 30 dage"), mapEl, recentList(sp.recent)),
      h("div", { class: "card" }, h("h3", {}, "Mediandato pr. år"), yearChart(ph), h("h3", { class: "mt2" }, "Observationer pr. lokalafdeling"),
        sp.by_afdeling.length ? h("ul", { class: "mini-list" }, ...sp.by_afdeling.map(a => h("li", {}, a.navn, h("b", { class: "num" }, fmtN(a.n_obs))))) : h("p", { class: "muted" }, "–"))));
  const map = baseMap(mapEl, { zoom: 6, center: [56.1, 11] });
  for (const r of sp.recent) L.circleMarker([r.lat, r.lon], { radius: 5, color: "#fff", weight: 1, fillColor: katColor(r.kategori), fillOpacity: .9 }).addTo(map)
    .bindPopup(`${fmtN(r.antal)} · ${r.lok}<br>${fmtDate(r.dato)}<br><a href="#/traad/${r.dato}/${artnr}/${r.loknr}">Åbn</a>`);
  return () => map.remove();
}

const stat = (v, l) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, l));

function phenoChart(ph) {
  if (!ph.weeks || !ph.baseline_years?.length) return empty("Ikke nok historiske data til en fænologikurve");
  const w = ph.weeks;
  const pts = (arr) => (arr || []).map((v, i) => [i, v]);
  const series = [
    { name: `Gns. ${ph.baseline_years[0]}–${ph.baseline_years.at(-1)}`, points: pts(w.mean), color: PALETTE[0], width: 2.5 },
    ...(w.last_year ? [{ name: "Sidste år", points: pts(w.last_year), color: PALETTE[2], dash: "5 4", width: 1.6 }] : []),
    { name: "I år", points: pts(w.current), color: PALETTE[3], width: 2.5 },
  ];
  return h("div", {},
    lineChart({ series, band: { lower: pts(w.p10), upper: pts(w.p90) }, height: 260, xTicks: WEEK_TICKS,
      xFormat: (x) => MON[WEEK_TICKS.indexOf(x)] ?? `uge ${x + 1}`, yFormat: (v) => `${v.toFixed(v < 1 ? 2 : 1).replace(".", ",")} %` }),
    seriesLegend(series),
    h("p", { class: "muted", style: { fontSize: ".82rem" } }, "Andel af alle ture (lister) i DOFbasen, hvor arten er registreret, pr. uge. Skraveret: 10–90 % af årene."));
}

function summaryBox(s) {
  if (!s) return null;
  const parts = [];
  for (const [key, label] of [["spring", "Forår"], ["autumn", "Efterår"]]) {
    const x = s.seasons[key];
    if (!x) continue;
    const st = x.status;
    const tr = x.trend_q50;
    parts.push(h("div", { class: "card", style: { boxShadow: "none", background: "var(--surface-2)" } },
      h("h3", {}, label),
      h("p", {}, `${({ ankomst: "Ankomst", afrejse: "Afrejse" })[x.event] || "Træktop"} normalt `, h("b", {}, doyLabel(x.normal.q50)),
        h("span", { class: "muted" }, ` (forløb 10–90 %: ${doyLabel(x.normal.q10)} – ${doyLabel(x.normal.q90)})`), "."),
      st ? h("p", {}, "I år: ", h("b", {}, st.lag_days === 0 ? "som normalt" : `${Math.abs(st.lag_days)} dage ${st.lag_days < 0 ? "tidligere" : "senere"}`), ` end normalt (${Math.round(st.season_progress * 100)} % af sæsonen er gået).`)
        : x.this_year ? h("p", {}, "I år var medianen ", h("b", {}, doyLabel(x.this_year.q50)), ".") : null,
      tr ? h("p", {}, "Trend: ", h("b", {}, `${tr.slope_per_decade > 0 ? "+" : ""}${String(tr.slope_per_decade).replace(".", ",")} dage pr. 10 år`),
        ` over ${tr.n_years} år `, h("span", { class: "muted" }, tr.p < 0.05 ? "(statistisk sikker)" : "(ikke statistisk sikker)")) : null,
      x.climate ? h("p", { class: "muted" }, `Klima: ${String(x.climate.days_per_degree).replace(".", ",")} dage pr. °C varmere forår (r = ${String(x.climate.r).replace(".", ",")}).`) : null));
  }
  return parts.length ? h("div", { class: "grid-2 mt" }, ...parts) : null;
}

function yearChart(ph) {
  const rows = (ph.metrics || []).filter(m => m.season === (ph.summary?.seasons?.spring ? "spring" : "autumn"));
  if (rows.length < 3) return h("p", { class: "muted" }, "For få år med data.");
  const season = ph.summary?.seasons?.spring ? "spring" : "autumn";
  const tr = ph.summary?.seasons?.[season]?.trend_q50;
  const trend = tr ? { slope: tr.slope_per_decade / 10, intercept: tr.intercept } : null;
  return h("div", {}, h("small", { class: "muted" }, season === "spring" ? "Forår" : "Efterår", " – nøgledato (50 %) pr. år med trendlinje"),
    yearDots({ points: rows.map(r => [r.year, r.q50]), trend, yFormat: doyLabel }));
}

function recentList(rows) {
  if (!rows.length) return h("p", { class: "muted" }, "Ingen fund de seneste 30 dage.");
  return h("ul", { class: "mini-list", style: { maxHeight: "260px", overflow: "auto", marginTop: "8px" } }, ...rows.slice(0, 60).map(r => h("li", {},
    h("span", {}, fmtDate(r.dato, false), " · ", h("a", { href: `#/lok/${r.loknr}` }, r.lok)), h("span", {}, r.kategori !== "alm" ? tag(r.kategori, KAT[r.kategori]) : null, " ", h("b", { class: "num" }, fmtN(r.antal))))));
}
