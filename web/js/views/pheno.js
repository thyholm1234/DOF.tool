import { api, h, fmtN, fmtDate, doyLabel, params, setParams, seg, empty, errorBox, windArrow, compass, esc } from "../core.js";
import { lineChart, PALETTE } from "../charts.js";
import { baseMap, arrowSvg } from "../maps.js";
import { windLayer } from "../wind.js";

// Oprydning for den aktive fane (kort og animation)
let tabCleanup = null;
const cleanupTab = () => { try { tabCleanup?.(); } catch { /* ignore */ } tabCleanup = null; };

export async function render(view) {
  const tab = params().tab || "status";
  const body = h("div", {});
  view.append(h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Træk & fænologi"), h("p", { class: "lead" }, "Kommer fuglene tidligere eller senere end normalt? Baseret på indsatskorrigerede DOFbasen-data og DMI's klimadata.")),
    h("span", { class: "spacer" }),
    seg([["status", "Tidligere/senere"], ["ankomst", "Ankomster i år"], ["kort", "Trækkort"], ["vejr", "Trækvejr"], ["klima", "Klima"], ["metode", "Metode"]], tab, (v) => { setParams({ tab: v }); cleanupTab(); body.replaceChildren(); draw(v); })), body);
  const draw = (t) => ({ status, ankomst: arrivals, kort: migrationMap, vejr: weather, klima: climate, metode: method }[t] || status)(body);
  await draw(tab);
  return cleanupTab;
}

const EVENT = { ankomst: "Ankomst", afrejse: "Afrejse", "træktop": "Træktop" };
const PROFILE = { sommer: "sommergæst", vinter: "vintergæst", passage: "gennemtrækker/standfugl" };
export { EVENT, PROFILE };

const shiftEl = (d, unit = "d") => {
  if (d === null || d === undefined || Number.isNaN(d)) return h("span", { class: "muted" }, "–");
  const r = Math.round(d * 10) / 10;
  const cls = Math.abs(r) < 1 ? "zero" : r < 0 ? "early" : "late";
  return h("span", { class: `shift ${cls}` }, `${r > 0 ? "+" : ""}${String(r).replace(".", ",")} ${unit}`);
};

async function status(body) {
  const data = await api("/api/pheno/overview");
  if (!data.species.length) { body.append(empty("Fænologimodellerne er ikke beregnet endnu", "De beregnes om natten, når historiske observationer er importeret (kræver mindst 3 hele år).")); return; }
  const season = { s: params().season || (new Date().getMonth() < 6 ? "spring" : "autumn") };
  let sort = "status";
  let q = "";
  const tbody = h("tbody");
  const drawRows = () => {
    let rows = data.species.filter(s => s.seasons[season.s] && (!q || s.navn.toLowerCase().includes(q)));
    const key = (s) => { const x = s.seasons[season.s]; return sort === "trend" ? (x.trend_q50?.slope_per_decade ?? 0) : sort === "navn" ? 0 : (x.status?.lag_days ?? (x.this_year && x.normal ? x.this_year.q50 - x.normal.q50 : 999)); };
    rows.sort(sort === "navn" ? (a, b) => a.navn.localeCompare(b.navn, "da") : (a, b) => key(a) - key(b));
    tbody.replaceChildren(...rows.map(s => {
      const x = s.seasons[season.s];
      const tr = x.trend_q50;
      const sig = tr && tr.p < 0.05;
      return h("tr", { class: "click", onclick: () => { location.hash = `#/art/${s.artnr}`; } },
        h("td", {}, h("b", {}, s.navn), h("div", { class: "muted", style: { fontSize: ".78rem" } }, PROFILE[s.profile] || s.type)),
        h("td", { class: "nowrap wide", "data-label": "Normalt" }, h("span", { class: "tag plain" }, EVENT[x.event] || "Træktop"), " ", h("b", {}, doyLabel(x.normal.q50)),
          h("div", { class: "muted", style: { fontSize: ".78rem" } }, `10–90 %: ${doyLabel(x.normal.q10)} – ${doyLabel(x.normal.q90)}`)),
        h("td", { class: "right", "data-label": "I år" }, x.status ? shiftEl(x.status.lag_days) : x.this_year ? shiftEl(x.this_year.q50 - x.normal.q50) : h("span", { class: "muted" }, "–"),
          x.status ? h("div", { class: "muted", style: { fontSize: ".72rem" } }, `${Math.round(x.status.season_progress * 100)} % af sæsonen`) : null),
        h("td", { class: "right", "data-label": "Trend" }, tr ? shiftEl(tr.slope_per_decade, "d/10 år") : "–",
          tr ? h("div", { class: "muted", style: { fontSize: ".72rem" } }, sig ? `sikker, p=${tr.p.toFixed(3).replace(".", ",")}` : `usikker, p=${tr.p.toFixed(2).replace(".", ",")}`) : null),
        h("td", { class: "right", "data-label": "Klima" }, x.climate ? shiftEl(x.climate.days_per_degree, "d/°C") : "–"));
    }));
  };
  const search = h("input", { type: "search", placeholder: "Søg art…", style: { maxWidth: "220px" }, oninput: (e) => { q = e.target.value.toLowerCase(); drawRows(); } });
  const sortSel = h("select", { style: { width: "auto" }, onchange: (e) => { sort = e.target.value; drawRows(); } },
    h("option", { value: "status" }, "Sortér: i år (tidligst først)"), h("option", { value: "trend" }, "Sortér: trend"), h("option", { value: "navn" }, "Sortér: navn"));
  body.append(h("div", { class: "card" },
    h("div", { class: "row" }, seg([["spring", "Forår"], ["autumn", "Efterår"]], season.s, (v) => { season.s = v; setParams({ season: v }); drawRows(); }), search, sortSel,
      h("span", { style: { flex: 1 } }), h("small", { class: "muted" }, `Beregnet ${fmtDate(data.generated)}`)),
    h("p", { class: "muted mt", style: { fontSize: ".85rem" } }, h("span", { class: "shift early" }, "Blå = tidligere"), " · ", h("span", { class: "shift late" }, "orange = senere"),
      ". 'I år' sammenligner årets kurve med de seneste 10 års gennemsnit. 'Trend' er ændringen i mediandatoen over alle år."),
    h("div", { class: "table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 3 } },
      h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Normalt"), h("th", { class: "right" }, "I år"), h("th", { class: "right" }, "Trend"), h("th", { class: "right", title: "Hvor mange dage tidligere ankomst pr. grad varmere feb–apr (negativ = tidligere)" }, "Klima"))),
      tbody))));
  drawRows();
}

async function arrivals(body) {
  const year = Number(params().year || new Date().getFullYear());
  const out = h("div", {}, h("div", { class: "skeleton", style: { height: "300px" } }));
  const ysel = h("select", { style: { width: "auto" }, onchange: (e) => { setParams({ year: e.target.value }); body.replaceChildren(); arrivals(body); } },
    ...Array.from({ length: 10 }, (_, i) => new Date().getFullYear() - i).map(y => h("option", { value: y, selected: y === year }, y)));
  body.append(h("div", { class: "card" }, h("div", { class: "row" }, h("h2", { style: { margin: 0 } }, "Årets første forårsfund af sommergæster"), h("span", { style: { flex: 1 } }), ysel),
    h("p", { class: "muted" }, "Første observation fra 1. februar (så overvintrende enkeltfugle ikke tæller) sammenlignet med medianen for de foregående 10 år. Første fund er følsomt for enkeltfund og indsats – den robuste ankomstdato står under 'Tidligere/senere'."), out));
  try {
    const rows = await api(`/api/pheno/arrivals?year=${year}`);
    if (!rows.length) { out.replaceChildren(empty("Ingen data for året endnu")); return; }
    out.replaceChildren(h("div", { class: "table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 3 } },
      h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Første fund"), h("th", {}, "Normalt"), h("th", { class: "right" }, "Forskel"))),
      h("tbody", {}, ...rows.map(r => h("tr", { class: "click", onclick: () => { location.hash = `#/art/${r.artnr}`; } },
        h("td", {}, h("b", {}, r.navn)), h("td", { class: "nowrap", "data-label": "Første fund" }, fmtDate(r.dato, false)), h("td", { class: "nowrap muted", "data-label": "Normalt" }, fmtDate(r.normal, false)),
        h("td", { class: "right", "data-label": "Forskel" }, shiftEl(r.diff_days))))))));
  } catch (err) { out.replaceChildren(errorBox(err)); }
}

async function weather(body) {
  const out = h("div", { class: "stack" }, h("div", { class: "skeleton", style: { height: "200px" } }));
  body.append(out);
  try {
    const data = await api("/api/weather/forecast");
    if (!data.spots.length) { out.replaceChildren(empty("DMI's prognose er ikke tilgængelig lige nu", "Prøv igen om lidt – DMI's API begrænser antallet af kald.")); return; }
    out.replaceChildren(h("div", { class: "notice" }, data.explain),
      ...data.spots.map(spot => {
        const days = {};
        spot.hours = spot.hours.filter(hr => new Date(hr.t) > Date.now() - 3600000);
        for (const hr of spot.hours) { const d = hr.t.slice(0, 10); (days[d] ||= []).push(hr); }
        const dayScore = (hrs, k) => { const day = hrs.filter(x => { const hh = new Date(x.t).getHours(); return hh >= 5 && hh <= 12; }); const v = day.map(x => x[k]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
        return h("div", { class: "card" },
          h("div", { class: "card-head" }, h("h3", {}, spot.navn), h("span", { class: "spacer" }),
            ...Object.entries(days).slice(0, 3).map(([d, hrs]) => h("span", { class: "muted", style: { fontSize: ".8rem", marginLeft: "10px" } },
              fmtDate(d, false), " ", dots(dayScore(hrs, "land")), spot.sea ? [" ", dots(dayScore(hrs, "sea"), true)] : null))),
          h("div", { class: "hours" }, ...spot.hours.filter((_, i) => i % 2 === 0).map(hr => {
            const t = new Date(hr.t);
            const col = hr.land >= 3.5 ? "var(--ok)" : hr.land >= 2.5 ? "var(--warn)" : "var(--line-strong)";
            return h("div", { class: "hour", title: hr.reason || "" },
              h("b", {}, t.getHours().toString().padStart(2, "0")), h("div", { class: "muted", style: { fontSize: ".65rem" } }, t.toLocaleDateString("da-DK", { weekday: "short" })),
              h("div", { class: "arrow", html: windArrow(hr.dir, 16) }), `${Math.round(hr.speed)} m/s`, h("div", {}, compass(hr.dir)),
              hr.temp != null ? h("div", {}, `${Math.round(hr.temp)}°`) : null, hr.rain > 0.1 ? h("div", { style: { color: "var(--bem)" } }, `${hr.rain} mm`) : null,
              h("div", { class: "q", style: { background: col } }));
          })),
          h("small", { class: "muted" }, "Prikker: landfugletræk (grøn) ", spot.sea ? "og seawatch (blå) " : "", "for morgen–formiddag. Søjlefarve pr. time: grøn = gode forhold."));
      }));
  } catch (err) { out.replaceChildren(errorBox(err)); }
}

function dots(v, sea = false) {
  if (v == null) return h("span", {}, "–");
  const n = Math.round(v);
  return h("span", { class: `score-dots ${sea ? "sea" : ""}`, title: `${v.toFixed(1)}/5` }, ...Array.from({ length: 5 }, (_, i) => h("i", { class: i < n ? "on" : "" })));
}

async function climate(body) {
  const data = await api("/api/pheno/climate");
  if (!data.spring.length) { body.append(empty("Klimadata hentes fra DMI…")); return; }
  const pts = data.spring.map(s => [s.year, s.temp]);
  body.append(h("div", { class: "card" }, h("h2", {}, "Forårstemperatur i Danmark (feb–apr)"),
    h("p", { class: "muted" }, "Landsgennemsnit fra DMI's klimadatabase. Bruges til at måle hvor følsom hver arts ankomst er over for temperaturen."),
    lineChart({ series: [{ name: "Middeltemperatur", points: pts, color: PALETTE[1] }], yMin: Math.floor(Math.min(...pts.map(p => p[1]))) - 1, height: 240,
      xFormat: String, yFormat: (v) => `${v.toFixed(1).replace(".", ",")} °C` })));
  const months = data.months.slice(-36);
  body.append(h("div", { class: "card" }, h("h3", {}, "Seneste 36 måneder"),
    lineChart({ series: [{ name: "Månedstemperatur", points: months.map((m, i) => [i, m.temp]), color: PALETTE[2] }], yMin: Math.min(...months.map(m => m.temp)) - 1, height: 200,
      xFormat: (i) => months[i] ? `${months[i].month}/${String(months[i].year).slice(2)}` : "", yFormat: (v) => `${Math.round(v)} °C` })));
}

function method(body) {
  body.append(h("div", { class: "card", style: { maxWidth: "820px" } },
    h("h2", {}, "Sådan beregnes det"),
    h("p", {}, h("b", {}, "Indsatskorrektion. "), "For hver dag beregnes rapporteringsraten: andelen af alle registrerede ture (lister) i DOFbasen, hvor arten er noteret. Det fjerner det meste af effekten af, at der er flere ornitologer ude i weekender, godt vejr og nyere år."),
    h("p", {}, h("b", {}, "Sæsonkurve. "), "Raten glattes over 7 dage, og et helårs-grundniveau (10 %-fraktilen) trækkes fra. Ud fra normalkurvens form deles arterne i sommergæster, vintergæster og gennemtrækkere."),
    h("p", {}, h("b", {}, "Hændelser. "), "Sommergæster: ", h("i", {}, "ankomst"), " = første dag i foråret hvor kurven når 50 % af sin top (10 %/90 % angiver forløbet) og ", h("i", {}, "afrejse"),
      " = sidste dag i efteråret over 50 %. Vintergæster omvendt (afrejse om foråret, ankomst om efteråret). Gennemtrækkere: ", h("i", {}, "træktop"), " = dagen hvor halvdelen af sæsonens forekomst er passeret. Kun år med data i mindst 11 måneder indgår (arkivet har kun maj/juni før 2010)."),
    h("p", {}, h("b", {}, "Trend. "), "Mediandatoen (50 %) plottes mod år, og hældningen findes robust med Theil–Sen-metoden; usikkerheden testes med Kendalls τ. En trend på −3 d/10 år betyder, at trækket i gennemsnit er rykket 3 dage frem pr. årti."),
    h("p", {}, h("b", {}, "I år. "), "Mens en sæson er i gang, forskydes de seneste 10 års gennemsnitskurve op til ±30 dage, og den forskydning, der passer bedst med årets kurve indtil nu, vises. Tallet vises først, når mindst 15 % af en normal sæson er gået, kurverne ligner hinanden (R² ≥ 0,5), og arten optræder på mindst 0,4 % af alle ture i sæsonens top."),
    h("p", {}, h("b", {}, "Klima. "), "DMI's landsgennemsnitlige månedstemperatur for februar–april sammenholdes med forårets 10 %-dato. Hældningen angiver antal dage pr. °C (negativ = tidligere ankomst i varme forår)."),
    h("p", {}, h("b", {}, "Trækvejr. "), "DMI's HARMONIE-prognose for klassiske træksteder vurderes med enkle tommelfingerregler (medvind, regn, vindstyrke, pålandsvind for seawatch). Det er en vejledning, ikke en model."),
    h("p", { class: "muted" }, "Begrænsninger: Rapporteringsrater påvirkes af kampagner, nye lokaliteter og ændret indtastningspraksis. Arter med få observationer giver usikre datoer.")));
}

// ---------------------------------------------------------------- trækkort: vind kl. 12 + trækretning pr. fuglegruppe

const GROUP_COLORS = { rov_svaev: "#c0392b", rov_aktiv: "#e67e22", vade: "#16a085", gaes: "#8e5a2b", hav: "#2a6fb0", spurv: "#7b4bb7", drossel: "#d35485" };
const DAY_LABEL = ["I dag", "I går", "I forgårs"];


async function migrationMap(body) {
  const p = params();
  const st = { back: Math.min(2, Number(p.d || 0)), groups: new Set((p.g || "").split(",").filter(Boolean)), wind: p.w !== "0" };
  const mapEl = h("div", { class: "map", style: { height: "min(70vh, 640px)" } });
  const chips = h("div", { class: "chips wrap" });
  const summary = h("div", { class: "mt" });
  const info = h("p", { class: "muted", style: { fontSize: ".85rem", margin: ".4rem 0 0" } });
  const windBox = h("input", { type: "checkbox", checked: st.wind });
  body.append(h("div", { class: "card" },
    h("div", { class: "row" }, seg(DAY_LABEL.map((l, i) => [String(i), l]), String(st.back), (v) => { st.back = Number(v); load(); }),
      h("label", { class: "check", style: { marginLeft: "auto" } }, windBox, "Vindanimation (kl. 12)")),
    h("div", { class: "mt" }, chips), info),
    h("div", { class: "card mt", style: { padding: 0, overflow: "hidden" } }, mapEl), summary);
  const map = baseMap(mapEl, { zoom: 7, center: [56.1, 10.9] });
  const arrows = L.layerGroup().addTo(map);
  let wind = null, data = null;
  tabCleanup = () => { wind?.remove(); map.remove(); };
  const save = () => setParams({ tab: "kort", d: st.back || "", g: [...st.groups].join(","), w: st.wind ? "" : "0" });
  const shown = (g) => !st.groups.size || st.groups.has(g);

  const drawWind = () => {
    wind?.remove(); wind = null;
    if (st.wind && data?.wind?.stations?.length) wind = windLayer(map, data.wind.stations);
  };
  windBox.onchange = () => { st.wind = windBox.checked; save(); drawWind(); };

  const drawChips = () => chips.replaceChildren(
    h("button", { class: `chip ${!st.groups.size ? "on" : ""}`, onclick: () => { st.groups.clear(); redraw(); } }, "Alle grupper"),
    ...(data?.groups || []).map(g => h("button", { class: `chip ${st.groups.has(g.group) ? "on" : ""}`, onclick: () => {
      st.groups.has(g.group) ? st.groups.delete(g.group) : st.groups.add(g.group); redraw();
    } }, h("i", { style: { display: "inline-block", width: "10px", height: "10px", borderRadius: "50%", background: GROUP_COLORS[g.group], marginRight: "6px" } }),
      g.label, h("span", { class: "muted" }, ` ${fmtN(g.n)}`))));

  const drawArrows = () => {
    arrows.clearLayers();
    for (const s of data.sites) {
      if (!shown(s.group)) continue;
      const size = Math.round(Math.min(46, 14 + 7 * Math.log10(s.n + 1)));
      const g = data.groups.find(x => x.group === s.group);
      L.marker([s.lat, s.lon], { icon: L.divIcon({ className: "mig-arrow", html: arrowSvg(s.dir, size, GROUP_COLORS[s.group]), iconSize: [size, size], iconAnchor: [size / 2, size / 2] }) })
        .bindPopup(`<b>${esc(s.lok)}</b><br>${g.label}: ${fmtN(s.n)} ind. mod ${compass(s.dir)} (${s.dir}°)` +
          `<br><small>${s.species.map(([n, c]) => `${esc(n)} ${fmtN(c)}`).join(", ")}</small>`)
        .addTo(arrows);
    }
  };

  const drawSummary = () => {
    const rows = data.groups.filter(g => shown(g.group));
    summary.replaceChildren(h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, `Hele landet – ${DAY_LABEL[st.back].toLowerCase()}`),
      h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Gruppe"), h("th", { class: "right" }, "Individer"), h("th", {}, "Retning (vægtet)"), h("th", { class: "right", title: "1 = alle samme vej, 0 = spredt" }, "Enighed"), h("th", {}, "Flest"))),
        h("tbody", {}, ...rows.map(g => h("tr", {},
          h("td", { class: "nowrap" }, h("i", { style: { display: "inline-block", width: "10px", height: "10px", borderRadius: "50%", background: GROUP_COLORS[g.group], marginRight: "6px" } }), g.label),
          h("td", { class: "right num" }, fmtN(g.n)),
          h("td", { class: "nowrap" }, g.dir == null ? "–" : h("span", { html: `${arrowSvg(g.dir, 16, GROUP_COLORS[g.group])} mod ${compass(g.dir)} (${g.dir}°)` })),
          h("td", { class: "right num" }, g.n ? String(g.r).replace(".", ",") : "–"),
          h("td", { class: "muted", style: { fontSize: ".85rem" } }, g.species.slice(0, 4).map(([n, c]) => `${n} ${fmtN(c)}`).join(", "))))))),
      h("p", { class: "muted", style: { fontSize: ".82rem", marginBottom: 0 } },
        "Retning fra adfærdskoden (trækkende mod N, NØ … NV) vægtet med antal individer. Pilene på kortet er pr. lokalitet og gruppe; størrelsen følger antallet. ",
        "Grupper efter slægt: rovfugle deles i svævere (fx våger, glenter, ørne) og aktive flyvere (høge, falke, kærhøge, fiskeørn); havfugle omfatter også havænder og terner. ",
        "Vind: DMI-stationernes middelvind kl. 12 (i dag før kl. 12: seneste time), interpoleret mellem stationerne.")));
  };

  const redraw = () => { save(); drawChips(); drawArrows(); drawSummary(); };
  const load = async () => {
    info.textContent = "Henter…";
    try { data = await api(`/api/pheno/migration?days_back=${st.back}`); } catch (err) { summary.replaceChildren(errorBox(err)); return; }
    const total = data.groups.reduce((a, g) => a + g.n, 0);
    info.textContent = `${fmtDate(data.date)} · ${fmtN(total)} trækkende individer med retning på ${fmtN(new Set(data.sites.map(s => s.loknr)).size)} lokaliteter · vind ${data.wind.time} fra ${data.wind.stations.length} DMI-stationer`;
    redraw(); drawWind();
  };
  await load();
}
