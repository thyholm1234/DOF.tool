import { api, h, fmtN, fmtDate, doyLabel, tag, errorBox, empty, seg, KAT, esc } from "../core.js";
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
  const pttEl = h("div", {});
  const atlasEl = h("div", {});
  const cleanups = [];
  view.replaceChildren(
    h("p", {}, h("a", { href: "#/arter", onclick: (e) => { if (history.length > 1) { e.preventDefault(); history.back(); } } }, "← Tilbage")),
    h("div", { class: "page-head" }, h("div", {},
      h("div", { class: "row" }, sp.klass !== "Alm" ? tag(sp.klass) : null, h("span", { class: "muted" }, ({ sommer: "sommergæst", vinter: "vintergæst", passage: "gennemtrækker/standfugl" })[ph.summary?.profile] || ph.type || "")),
      h("h1", {}, sp.navn), sp.latin ? h("p", { class: "lead" }, h("i", {}, sp.latin)) : null)),
    h("div", { class: "stats", style: { gridTemplateColumns: "repeat(3, 1fr)" } },
      stat(fmtN(sp.total?.n_obs), "observationer i alt"), stat(fmtN(sp.total?.n_days), "dage med fund"), stat(sp.total?.last_date ? fmtDate(sp.total.last_date) : "–", "senest før i dag")),
    sp.finds ? findsCard(sp.finds, artnr) : null,
    h("div", { class: "card mt" }, h("h2", {}, "Forekomst gennem året"), phenoChart(ph), summaryBox(ph.summary)),
    h("div", { class: "grid-2 mt" },
      h("div", { class: "card" }, h("h3", {}, "Fund de seneste 30 dage"), mapEl, recentList(sp.recent)),
      h("div", { class: "card" }, h("h3", {}, "Mediandato pr. år"), yearChart(ph), h("h3", { class: "mt2" }, "Observationer pr. lokalafdeling"),
        sp.by_afdeling.length ? h("ul", { class: "mini-list" }, ...sp.by_afdeling.map(a => h("li", {}, a.navn, h("b", { class: "num" }, fmtN(a.n_obs))))) : h("p", { class: "muted" }, "–"))),
    pttEl, atlasEl);
  const map = baseMap(mapEl, { zoom: 6, center: [56.1, 11] });
  for (const r of sp.recent) L.circleMarker([r.lat, r.lon], { radius: 5, color: "#fff", weight: 1, fillColor: katColor(r.kategori), fillOpacity: .9 }).addTo(map)
    .bindPopup(`${fmtN(r.antal)} · ${esc(r.lok)}<br>${fmtDate(r.dato)}<br><a href="#/traad/${r.dato}/${artnr}/${r.loknr}">Åbn</a>`);
  // DOF's punkttællinger og Atlas hentes efter siden er vist (eksterne tjenester, caches på serveren)
  api(`/api/species/${artnr}/punkttaelling`).then(d => pttEl.replaceChildren(pointCountCard(d, sp.navn) || "")).catch(() => {});
  api(`/api/species/${artnr}/atlas`).then(async d => {
    if (!d.available) return;
    const squares = await api("/api/atlas/squares");
    atlasEl.replaceChildren(atlasCard(d, squares, cleanups));
  }).catch(() => {});
  return () => { map.remove(); cleanups.forEach(f => f()); };
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

// Estimeret antal fund for SU/SUB-arter (observationer < 15 km og højst 1 dag fra hinanden = ét fund)
function findsCard(f, artnr) {
  const year = new Date().getFullYear();
  const byYear = Object.fromEntries(f.per_year.map(p => [p.year, p.n]));
  const first = f.per_year[0]?.year ?? year;
  const pts = Array.from({ length: year - first + 1 }, (_, i) => [first + i, byYear[first + i] || 0]);
  const best = f.per_year.reduce((a, b) => (b.n > (a?.n ?? -1) ? b : a), null);
  const span = (x) => x.first === x.last ? fmtDate(x.first, false) : `${fmtDate(x.first, false)} – ${fmtDate(x.last, false)}`;
  const rows = f.finds.slice(0, 40);
  return h("div", { class: "card mt" },
    h("h2", {}, "Fund (estimeret)"),
    h("div", { class: "stats", style: { gridTemplateColumns: "repeat(3, 1fr)" } },
      stat(fmtN(byYear[year] || 0), `fund i ${year} til dato`), stat(fmtN(f.total), `fund siden ${first}`), stat(best ? fmtN(best.n) : "–", best ? `flest (${best.year})` : "flest")),
    pts.length > 1 ? h("div", { class: "mt" }, lineChart({ series: [{ name: "Fund", points: pts, color: PALETTE[3] }], height: 200, xFormat: String, yFormat: (v) => fmtN(Math.round(v)) })) : null,
    h("details", { class: "more mt" }, h("summary", {}, `Seneste ${rows.length} fund`),
      h("div", { class: "table-wrap mt" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Fund"), h("th", {}, "Periode"), h("th", {}, "Lokalitet"), h("th", { class: "right" }, "Maks."), h("th", { class: "right" }, "Obs."))),
        h("tbody", {}, ...rows.map(x => h("tr", {},
          h("td", { class: "nowrap muted" }, `${x.year_no}. i ${x.first.slice(0, 4)}`),
          h("td", { class: "nowrap" }, x.loknr ? h("a", { href: `#/traad/${x.first}/${artnr}/${x.loknr}` }, span(x)) : span(x)),
          h("td", {}, x.loknr ? h("a", { href: `#/lok/${x.loknr}` }, x.lok) : h("span", { class: "muted" }, "Skjult lokalitet"), x.n_sites > 1 ? h("span", { class: "muted" }, ` +${x.n_sites - 1}`) : null),
          h("td", { class: "right num" }, fmtN(x.max_antal)), h("td", { class: "right num" }, fmtN(x.n_obs)))))))),
    h("p", { class: "muted", style: { fontSize: ".82rem", marginBottom: 0 } },
      `Estimat: observationer af arten under ${f.link_km} km og højst ${f.link_days} dag fra hinanden regnes som samme fund (kædet), så en fugl der bliver flere dage eller ses af mange tæller én gang.`));
}

// ---------------------------------------------------------------- punkttællinger (DOF's bestandsindeks)

const pct = (v) => `${v > 0 ? "+" : ""}${String(v).replace(".", ",")} %`;
const trendText = (t) => t ? `${pct(t.pct_per_year)} pr. år (${pct(t.pct_total)} ${t.from}–${t.to})${t.significant ? "" : " – ikke statistisk sikker"}` : "–";

function pointCountCard(d, navn) {
  if (!d?.available || !d.seasons.length) return null;
  const series = d.seasons.map(s => ({ name: s.summer ? "Sommer (1/5–15/6)" : "Vinter (20/12–20/1)", color: s.summer ? "#e0673c" : "#4779ae",
    points: s.points.map(p => [p.year, p.index]) }));
  const facts = Object.values(d.facts || {});
  const red = (d.redlists || []).find(r => /dansk/i.test(r.title || ""));
  return h("div", { class: "card mt" },
    h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, "Bestandsudvikling (punkttællinger)"),
      h("a", { class: "btn sm ghost", href: d.source, target: "_blank", rel: "noopener" }, "Danmarksfugle ↗")),
    h("p", { class: "muted", style: { fontSize: ".85rem" } }, `Indeks for ${navn.toLowerCase()} fra DOF's punkttællingsprogram; første år = 100. Trends er beregnet her med log-lineær regression.`),
    lineChart({ series, height: 240, xFormat: String, yMin: 0, yFormat: (v) => fmtN(Math.round(v)) }), seriesLegend(series),
    h("div", { class: "grid-2 mt" }, ...d.seasons.map(s => h("div", { class: "card", style: { boxShadow: "none", background: "var(--surface-2)" } },
      h("h3", { style: { marginTop: 0 } }, s.summer ? "Sommerbestand" : "Vinterbestand"),
      h("p", { style: { margin: ".2rem 0" } }, "Seneste 12 år: ", h("b", {}, s.trend_12 ? s.trend_12.direction : "–"), h("span", { class: "muted" }, ` · ${trendText(s.trend_12)}`)),
      h("p", { style: { margin: ".2rem 0" } }, "Hele perioden: ", h("b", {}, s.trend_all ? s.trend_all.direction : "–"), h("span", { class: "muted" }, ` · ${trendText(s.trend_all)}`)),
      h("p", { class: "muted", style: { margin: ".2rem 0", fontSize: ".85rem" } },
        s.last ? `Seneste indeks ${fmtN(s.last.index)} (${s.last.year})` : "", s.max ? ` · højest ${fmtN(s.max.index)} (${s.max.year}) · lavest ${fmtN(s.min.index)} (${s.min.year})` : "")))),
    facts.length || red ? h("div", { class: "stats mt", style: { gridTemplateColumns: `repeat(${facts.length + (red ? 1 : 0)}, 1fr)` } },
      ...facts.map(f => stat(f.text, f.title)), red ? stat(red.classification || "–", "Dansk rødliste") : null) : null,
    facts.length ? h("p", { class: "muted", style: { fontSize: ".78rem", marginBottom: 0 } }, "Nøgletal fra DOF's Danmarksfugle.") : null);
}

// ---------------------------------------------------------------- Atlas (yngleudbredelse i 5×5 km-kvadrater)

const ATLAS_COLORS = { sikker: "#1f7a3f", sandsynlig: "#e09a1f", mulig: "#f2d64b", kun_2: "#c0392b", kun_3: "#2e9e4f", begge: "#8a8f98" };
const ATLAS_LABEL = { sikker: "Sikker", sandsynlig: "Sandsynlig", mulig: "Mulig", kun_2: "Kun Atlas II (forsvundet)", kun_3: "Kun Atlas III (ny)", begge: "Begge" };

function atlasCard(d, squares, cleanups) {
  const mapEl = h("div", { class: "map", style: { height: "460px" } });
  const legend = h("div", { class: "legend mt" });
  const info = h("p", { class: "muted", style: { fontSize: ".85rem" } });
  const total = (p) => Object.values(d.counts[p]).reduce((a, b) => a + b, 0);
  const bars = h("div", { class: "stats mt", style: { gridTemplateColumns: "repeat(3, 1fr)" } },
    ...[1, 2, 3].map(p => stat(fmtN(total(p)), `${d.names[p]} · ${fmtN(d.counts[p].sikker)} sikre`)));
  const card = h("div", { class: "card mt" },
    h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, "Atlas – yngleudbredelse"),
      h("a", { class: "btn sm ghost", href: `https://dofbasen.dk/atlas/arter/${d.artnr}`, target: "_blank", rel: "noopener" }, "DOF Atlas ↗")),
    seg([["3", "Atlas III"], ["2", "Atlas II"], ["1", "Atlas I"], ["diff", "Ændring II → III"]], "3", (v) => draw(v)),
    info, mapEl, legend, bars);
  const map = baseMap(mapEl, { zoom: 6, center: [56.15, 11.2] });
  cleanups.push(() => map.remove());
  const layer = L.layerGroup().addTo(map);
  const renderer = L.canvas();
  const poly = (kode, color, title) => {
    const sq = squares[kode];
    if (!sq) return;
    L.polygon(sq[1], { renderer, color, weight: .6, fillColor: color, fillOpacity: .7 }).bindTooltip(`${esc(kode)} ${esc(sq[0])}<br>${title}`).addTo(layer);
  };
  const draw = (v) => {
    layer.clearLayers();
    if (v === "diff") {
      const p2 = d.periods[2], p3 = d.periods[3];
      for (const k of Object.keys({ ...p2, ...p3 })) {
        const key = k in p2 && k in p3 ? "begge" : k in p3 ? "kun_3" : "kun_2";
        poly(k, ATLAS_COLORS[key], ATLAS_LABEL[key]);
      }
      info.textContent = `Kvadrater med mulig, sandsynlig eller sikker ynglen: ${fmtN(d.change.kun_3)} nye, ${fmtN(d.change.kun_2)} forsvundne og ${fmtN(d.change.begge)} i begge atlas.`;
      legend.replaceChildren(...["kun_3", "kun_2", "begge"].map(k => h("span", {}, h("i", { style: { background: ATLAS_COLORS[k], height: "10px" } }), ATLAS_LABEL[k])));
    } else {
      for (const [k, s] of Object.entries(d.periods[v])) poly(k, ATLAS_COLORS[s], ATLAS_LABEL[s]);
      info.textContent = `${d.names[v]}: ${fmtN(total(v))} kvadrater med ynglefund. Hvert kvadrat er 5×5 km.`;
      legend.replaceChildren(...["sikker", "sandsynlig", "mulig"].map(k => h("span", {}, h("i", { style: { background: ATLAS_COLORS[k], height: "10px" } }), `${ATLAS_LABEL[k]} (${fmtN(d.counts[v][k])})`)));
    }
  };
  draw("3");
  return card;
}
