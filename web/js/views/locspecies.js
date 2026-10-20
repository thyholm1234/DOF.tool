// Én art på én lokalitet: gennem året, år for år, tidspunkt i forhold til solen og adfærd.
import { api, h, fmtN, fmtDate, doyLabel, tag, errorBox, busyBlock, empty, seg, params, setParams, esc } from "../core.js";
import { barChart, showTip, hideTip } from "../charts.js";

const NS = "http://www.w3.org/2000/svg";
const s = (t, attrs = {}) => { const el = document.createElementNS(NS, t); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };
const MON = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];
const MONTH_WEEK = [0, 4, 8, 13, 17, 21, 26, 30, 34, 39, 43, 47];  // første uge (7-dages blok) i hver måned
const WEEK_TICKS = MONTH_WEEK.map((w, i) => [w, MON[i]]);
const pct = (v) => `${(Math.round(v * 10) / 10).toLocaleString("da-DK")} %`;
const clock = (min) => { const m = ((Math.round(min) % 1440) + 1440) % 1440; return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`; };
const hrs = (min) => { const a = Math.abs(min), t = a >= 60 ? `${Math.floor(a / 60)} t${a % 60 ? ` ${a % 60} min` : ""}` : `${a} min`; return min === 0 ? "0" : `${t} ${min < 0 ? "før" : "efter"}`; };
// Smalle skærme: tegn graferne smallere, så teksten ikke skaleres ned
const chartW = () => matchMedia("(max-width: 600px)").matches ? 400 : 720;
const weekLabel = (w) => `${doyLabel(w * 7 + 1)} – ${doyLabel(w * 7 + 7)}`;

export async function render(view, [loknr, artnr]) {
  const p = params();
  const st = { year: p.y || "", weight: p.vis === "fugle" ? "birds" : "obs", season: p.s || "andel", yearMetric: p.ym || "andel" };
  let d, gone = false;
  const body = h("div", {});
  view.replaceChildren(busyBlock("Henter arten på lokaliteten…", 6));

  const save = () => setParams({ y: st.year, vis: st.weight === "birds" ? "fugle" : "", s: st.season === "andel" ? "" : st.season,
    ym: st.yearMetric === "andel" ? "" : st.yearMetric });

  async function load() {
    body.classList.add("is-busy");
    try { d = await api(`/api/locations/${loknr}/species/${artnr}${st.year ? `?year=${st.year}` : ""}`); }
    catch (err) { if (!gone) view.replaceChildren(errorBox(err)); return; }
    if (gone) return;
    body.classList.remove("is-busy");
    if (!view.contains(body)) { view.replaceChildren(head(), body); }
    draw();
  }

  const head = () => h("div", {},
    h("p", {}, h("a", { href: `#/lok/${loknr}` }, `← ${d.location.navn}`)),
    h("div", { class: "page-head" }, h("div", {},
      h("div", { class: "row" }, d.species.klass && d.species.klass !== "Alm" ? tag(d.species.klass) : null,
        h("span", { class: "muted" }, "Art på lokalitet")),
      h("h1", {}, d.species.navn),
      h("p", { class: "lead" }, d.species.latin ? [h("i", {}, d.species.latin), " · "] : null, "på ",
        h("a", { href: `#/lok/${loknr}` }, d.location.navn), d.location.kommune ? `, ${d.location.kommune} Kommune` : "")),
      h("div", { class: "row" },
        h("a", { class: "btn", href: `#/art/${artnr}` }, "Arten i hele landet"),
        h("a", { class: "btn", href: `#/lok/${loknr}` }, "Alle arter på lokaliteten"))));

  function draw() {
    save();
    const yearSel = h("select", { style: { width: "auto" }, "aria-label": "Vælg år", onchange: (e) => { st.year = e.target.value; load(); } },
      h("option", { value: "" }, "Alle år"),
      ...[...d.years].reverse().map(y => h("option", { value: y.year, selected: String(y.year) === String(st.year) }, `${y.year} · ${fmtN(y.days)} dage`)));
    const sv = d.stats;
    body.replaceChildren(
      h("div", { class: "row", style: { gap: "8px", marginBottom: "12px" } }, h("small", { class: "muted" }, "År"), yearSel,
        h("small", { class: "muted", style: { marginLeft: "8px" } }, "Tæl"),
        seg([["obs", "Observationer"], ["birds", "Fugle"]], st.weight, (v) => { st.weight = v; draw(); })),
      h("div", { class: "stats" },
        stat(fmtN(sv.days), "dage med arten", sv.visits ? `af ${fmtN(sv.visits)} dage med observationer her (${pct(100 * sv.days / sv.visits)})` : null),
        stat(fmtN(sv.obs), "observationer", `${fmtN(sv.birds)} fugle i alt (dagens største antal pr. dag)`),
        stat(sv.max ? fmtN(sv.max) : "–", "største antal", sv.max_date ? fmtDate(sv.max_date) : null),
        stat(sv.last ? fmtDate(sv.last) : "–", "senest set", sv.first ? `første gang ${fmtDate(sv.first)}` : null)),
      sv.days ? null : h("div", { class: "card mt" }, empty("Ingen fund", st.year ? `Arten er ikke registreret her i ${st.year}.` : "Arten er ikke registreret her.")),
      sv.days ? h("div", { class: "card mt" }, ...seasonCard()) : null,
      d.years.length > 1 ? h("div", { class: "card mt" }, ...yearsCard()) : null,
      sv.days ? h("div", { class: "card mt" }, ...timeCard()) : null,
      sv.days ? h("div", { class: "grid-2 mt" }, h("div", { class: "card" }, ...behaviourCard()), h("div", { class: "card" }, ...directionCard())) : null,
      sv.days ? h("div", { class: "card mt" }, ...topDays()) : null);
  }

  // ------------------------------------------------------------------ gennem året
  function seasonCard() {
    const M = {
      andel: { v: (w) => w.visits ? 100 * w.days / w.visits : 0, f: pct, note: "Andel af dagene med observationer på lokaliteten (af alle arter), hvor arten er set – udligner, at nogle uger er bedre dækket end andre." },
      dage: { v: (w) => w.days, f: fmtN, note: "Antal dage med arten i ugen, lagt sammen over årene." },
      max: { v: (w) => w.max, f: fmtN, note: "Største antal på én dag i ugen (én observation, ikke summen af flere)." },
    }[st.season];
    const bars = d.weeks.map((w, i) => ({ v: M.v(w),
      tip: `<div style="opacity:.7">${weekLabel(i)}</div>${fmtN(w.days)} dage med arten af ${fmtN(w.visits)} besøgsdage${w.visits ? ` (${pct(100 * w.days / w.visits)})` : ""}<br>Største antal: <b>${fmtN(w.max)}</b>` }));
    return [h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, `Gennem året${st.year ? ` – ${st.year}` : ""}`),
        seg([["andel", "Andel af dage"], ["dage", "Dage"], ["max", "Største antal"]], st.season, (v) => { st.season = v; draw(); })),
      barChart({ bars, width: chartW(), height: 240, xTicks: chartW() < 720 ? WEEK_TICKS.filter((_, i) => i % 2 === 0) : WEEK_TICKS, yFormat: M.f }),
      h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } }, M.note, " Uger à 7 dage fra 1. januar.")];
  }

  // ------------------------------------------------------------------ år for år
  function yearsCard() {
    const by = Object.fromEntries(d.years.map(y => [y.year, y]));
    const y0 = d.years[0].year, y1 = d.years.at(-1).year;
    const all = Array.from({ length: y1 - y0 + 1 }, (_, i) => by[y0 + i] || { year: y0 + i, days: 0, visits: 0, max: 0 });
    const M = { andel: { v: (y) => y.visits ? 100 * y.days / y.visits : 0, f: pct }, dage: { v: (y) => y.days, f: fmtN }, max: { v: (y) => y.max, f: fmtN } }[st.yearMetric];
    const bars = all.map(y => ({ v: M.v(y), hl: String(y.year) === String(st.year),
      tip: `<div style="opacity:.7">${y.year}</div>${fmtN(y.days)} dage med arten${y.visits ? ` af ${fmtN(y.visits)} (${pct(100 * y.days / y.visits)})` : ""}<br>Største antal: <b>${fmtN(y.max)}</b>${y.max_date ? ` (${esc(fmtDate(y.max_date))})` : ""}<br><small style="opacity:.7">Klik for at vælge året</small>` }));
    const every = Math.max(1, Math.ceil(all.length / (chartW() < 720 ? 6 : 12)));
    const ticks = all.map((y, i) => [i, String(y.year)]).filter(([i]) => (all.length - 1 - i) % every === 0);
    return [h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, "År for år"),
        seg([["andel", "Andel af dage"], ["dage", "Dage"], ["max", "Største antal"]], st.yearMetric, (v) => { st.yearMetric = v; draw(); })),
      barChart({ bars, width: chartW(), height: 200, xTicks: ticks, yFormat: M.f,
        onClick: (i) => { st.year = String(all[i].year) === String(st.year) ? "" : String(all[i].year); load(); } }),
      h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } }, st.year ? `${st.year} er fremhævet – klik på det igen for alle år.` : "Klik på et år for at se det for sig.")];
  }

  // ------------------------------------------------------------------ tidspunkt på dagen
  function timeCard() {
    const t = d.time, k = st.weight === "birds" ? 1 : 0, unit = st.weight === "birds" ? "fugle" : "observationer";
    if (!t.timed) return [h("h2", {}, "Tidspunkt på dagen"), empty("Ingen klokkeslæt", "Ingen af observationerne har et klokkeslæt angivet.")];
    const n = t.rise.length, half = n / 2;
    // Vis kun det spænd, hvor der er data (mindst ±3 timer), ens for begge sider, så de kan sammenlignes
    const used = (arr) => arr.map((b, i) => b[0] ? Math.abs(i - half + (i >= half ? 1 : 0)) : 0);
    const reach = Math.min(half, Math.max(6, ...used(t.rise), ...used(t.set)));
    const crop = (arr) => arr.slice(half - reach, half + reach);
    const yMax = Math.max(...crop(t.rise).map(b => b[k]), ...crop(t.set).map(b => b[k]), 1);
    const side = (arr, ev) => {
      const part = crop(arr);
      const ticks = [], step = 2 * 60 / t.bin;  // en etiket hver anden time, talt fra solopgang/-nedgang
      for (let j = -Math.floor(reach / step) * step; j <= reach; j += step) ticks.push([reach + j - .5, j ? `${j > 0 ? "+" : "−"}${Math.abs(j) * t.bin / 60} t` : "0"]);
      return barChart({ bars: part.map((b, i) => {
        const from = (i - reach) * t.bin;
        return { v: b[k], tip: `<div style="opacity:.7">${hrs(from)}–${hrs(from + t.bin)} ${ev}</div><b>${fmtN(b[0])}</b> observationer · <b>${fmtN(b[1])}</b> fugle` };
      }), width: 380, height: 200, yMax, xTicks: ticks, markers: [{ x: reach, label: ev }], yFormat: fmtN });
    };
    return [h("h2", {}, "Tidspunkt på dagen"),
      h("p", { class: "muted", style: { marginTop: 0, fontSize: ".86rem" } },
        `${fmtN(t.timed)} af ${fmtN(d.stats.obs)} observationer har et klokkeslæt. Hver observation er lagt ved den nærmeste af solopgang og solnedgang den dag her på lokaliteten – så morgen og aften kan sammenlignes hele året, selvom solen står op på vidt forskellige klokkeslæt. Søjlerne viser ${unit} pr. halve time.`),
      h("div", { class: "grid-2" },
        h("div", {}, h("h3", { style: { margin: "0 0 4px" } }, "Omkring solopgang"), side(t.rise, "solopgang")),
        h("div", {}, h("h3", { style: { margin: "0 0 4px" } }, "Omkring solnedgang"), side(t.set, "solnedgang"))),
      h("h3", { class: "mt" }, "Klokkeslæt gennem året"),
      dayGrid(t, k, unit),
      h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } }, `Hver celle er en uge og en halv time; jo mørkere, jo flere ${unit}. Stregerne er solopgang og solnedgang her (${t.curve_year}, med sommertid).`)];
  }

  // ------------------------------------------------------------------ adfærd
  function behaviourCard() {
    const k = st.weight === "birds" ? "birds" : "n";
    const list = [...d.behaviour].sort((a, b) => b[k] - a[k]);
    const total = list.reduce((a, b) => a + b[k], 0) || 1;
    const shown = list.slice(0, 10);
    const top = shown[0]?.[k] || 1;
    const seasonal = list.slice(0, 6).filter(b => b.n >= 3);
    return [h("h2", {}, "Adfærd"),
      h("div", { class: "hbar-list" }, ...shown.map(b => h("div", { class: "hbar", title: `${b.text}: ${fmtN(b.n)} observationer, ${fmtN(b.birds)} fugle` },
        h("span", { class: "hbar-label" }, b.text, b.code ? h("small", { class: "muted" }, ` ${b.code}`) : null),
        h("span", { class: "hbar-track" }, h("i", { style: { width: `${100 * b[k] / top}%` } })),
        h("b", { class: "num hbar-val" }, pct(100 * b[k] / total))))),
      list.length > shown.length ? h("small", { class: "muted" }, `+ ${list.length - shown.length} andre adfærdskoder`) : null,
      seasonal.length > 1 ? h("h3", { class: "mt" }, "Hvornår på året") : null,
      seasonal.length > 1 ? monthTable(seasonal) : null,
      seasonal.length > 1 ? h("small", { class: "muted" }, "Observationer pr. måned for hver adfærd; hver række skaleret for sig.") : null];
  }

  function directionCard() {
    const k = st.weight === "birds" ? "birds" : "n";
    const total = d.directions.reduce((a, x) => a + x[k], 0);
    if (!total) return [h("h2", {}, "Trækretning"), empty("Ingen trækretninger", "Ingen observationer med en retning (fx 'SV' eller 'R NØ').")];
    return [h("h2", {}, "Trækretning"), rose(d.directions, k, total),
      h("small", { class: "muted" }, `${fmtN(total)} ${st.weight === "birds" ? "fugle" : "observationer"} med retning. Retningen fuglene trak mod; 16 streger er lagt sammen til 8.`)];
  }

  // ------------------------------------------------------------------ top 10
  function topDays() {
    const box = h("div", {}, h("small", { class: "muted" }, "Henter…"));
    const year = st.year;
    api(`/api/locations/${loknr}/records/${artnr}${year ? `?year=${year}` : ""}`).then(rows => {
      if (gone || year !== st.year) return;
      box.replaceChildren(rows.length ? h("ol", { class: "mini-list" }, ...rows.map((r, i) => h("li", {},
        h("span", {}, h("b", { class: "num muted", style: { display: "inline-block", minWidth: "2.2ch" } }, `${i + 1}.`), " ",
          h("a", { href: `#/traad/${r.dato}/${artnr}/${loknr}` }, fmtDate(r.dato)), h("small", { class: "muted" }, ` · ${r.observer}${r.adfkode ? ` · ${r.adfkode}` : ""}`)),
        h("b", { class: "num" }, fmtN(r.antal))))) : h("p", { class: "muted" }, "Ingen tal."));
    }).catch(err => box.replaceChildren(errorBox(err)));
    return [h("h2", {}, `Top 10 dage${st.year ? ` i ${st.year}` : ""}`), h("small", { class: "muted" }, "Dagens største enkeltobservation · klik for tråden"), box];
  }

  await load();
  return () => { gone = true; hideTip(); };
}

const stat = (v, l, sub) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, l), sub ? h("small", { class: "muted", style: { display: "block", fontSize: ".74rem" } }, sub) : null);

/** Uge × halve time med solopgang og -nedgang lagt ovenpå. */
function dayGrid(t, k, unit) {
  const grid = k ? t.grid_birds : t.grid;
  const W = chartW(), H = 300, m = { l: 44, r: 12, t: 8, b: 26 };
  const cw = (W - m.l - m.r) / grid.length, ch = (H - m.t - m.b) / 48;
  const X = (w) => m.l + w * cw, Y = (min) => m.t + min / 30 * ch;
  const max = Math.max(1, ...grid.flat());
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": `${unit} pr. uge og klokkeslæt` });
  svg.append(s("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, style: "fill: var(--bg-2)" }));
  grid.forEach((col, w) => col.forEach((v, hh) => {
    if (!v) return;
    const cell = s("rect", { x: X(w) + .5, y: Y(hh * 30) + .5, width: cw - 1, height: ch - 1, rx: 1.5,
      style: `fill: var(--brand-2); opacity: ${(.18 + .82 * Math.sqrt(v / max)).toFixed(2)}` });
    cell.addEventListener("pointermove", (e) => showTip(e, `<div style="opacity:.7">${weekLabel(w)} · ${clock(hh * 30)}–${clock(hh * 30 + 30)}</div><b>${fmtN(v)}</b> ${unit}`));
    cell.addEventListener("pointerleave", hideTip);
    svg.append(cell);
  }));
  for (let hh = 0; hh <= 24; hh += 3) {
    svg.append(s("line", { x1: m.l, x2: W - m.r, y1: Y(hh * 60), y2: Y(hh * 60), class: "grid" }));
    const tx = s("text", { x: m.l - 6, y: Y(hh * 60) + 4, "text-anchor": "end" }); tx.textContent = `${String(hh).padStart(2, "0")}:00`; svg.append(tx);
  }
  for (const [w, label] of W < 720 ? WEEK_TICKS.filter((_, i) => i % 2 === 0) : WEEK_TICKS) { const tx = s("text", { x: X(w) + cw / 2, y: H - 8, "text-anchor": "middle" }); tx.textContent = label; svg.append(tx); }
  for (const [i, name] of [[0, "solopgang"], [1, "solnedgang"]]) {
    const pts = t.curve.map((c, w) => c ? `${X(w) + cw / 2},${Y(c[i])}` : null).filter(Boolean);
    svg.append(s("polyline", { points: pts.join(" "), fill: "none", style: "stroke: var(--accent)", "stroke-width": 2, "stroke-linejoin": "round", "pointer-events": "none" }));
    const mid = t.curve[24];
    if (mid) { const tx = s("text", { x: X(24) + cw / 2, y: Y(mid[i]) + (i ? 14 : -6), "text-anchor": "middle", style: "fill: var(--text-2); font-weight: 600" }); tx.textContent = name; svg.append(tx); }
  }
  return svg;
}

/** Adfærd × måned; hver række skaleret til sit eget maksimum. */
function monthTable(list) {
  return h("div", { class: "table-wrap" }, h("table", { class: "month-heat" },
    h("thead", {}, h("tr", {}, h("th", {}), ...MON.map(m => h("th", {}, m)))),
    h("tbody", {}, ...list.map(b => {
      const max = Math.max(1, ...b.months);
      return h("tr", {}, h("th", { title: b.text }, b.text),
        ...b.months.map((v, i) => h("td", { title: `${b.text}, ${MON[i]}: ${fmtN(v)} observationer` },
          v ? h("i", { style: { opacity: (.15 + .85 * v / max).toFixed(2) } }) : null)));
    }))));
}

/** Retningsrose: kronbladenes areal svarer til andelen (radius ~ kvadratroden). */
function rose(dirs, k, total) {
  const S = 260, c = S / 2, R = c - 30;
  const max = Math.max(...dirs.map(x => x[k]));
  const svg = s("svg", { viewBox: `0 0 ${S} ${S}`, class: "chart", role: "img", style: "max-width: 300px; margin: 0 auto", "aria-label": "Trækretninger" });
  for (const f of [.5, 1]) svg.append(s("circle", { cx: c, cy: c, r: R * f, fill: "none", class: "grid" }));
  dirs.forEach((x, i) => {
    const a = i * 45, rr = R * Math.sqrt(x[k] / max), w = 19;
    const pt = (ang, r) => [c + r * Math.sin(ang * Math.PI / 180), c - r * Math.cos(ang * Math.PI / 180)];
    const [ax, ay] = pt(a - w, rr), [bx, by] = pt(a + w, rr);
    if (x[k]) {
      const petal = s("path", { d: `M${c},${c}L${ax},${ay}A${rr},${rr} 0 0 1 ${bx},${by}Z`, style: "fill: var(--brand-2); stroke: var(--surface); stroke-width: 2" });
      petal.addEventListener("pointermove", (e) => showTip(e, `<div style="opacity:.7">Mod ${x.dir}</div><b>${fmtN(x.n)}</b> observationer · <b>${fmtN(x.birds)}</b> fugle<br>${pct(100 * x[k] / total)}`));
      petal.addEventListener("pointerleave", hideTip);
      svg.append(petal);
    }
    const [lx, ly] = pt(a, R + 16);
    const tx = s("text", { x: lx, y: ly + 4, "text-anchor": "middle", style: "fill: var(--text-2); font-weight: 600" }); tx.textContent = x.dir; svg.append(tx);
  });
  return svg;
}
