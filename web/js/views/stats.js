import { api, state, h, fmtN, fmtDate, tag, params, setParams, requireLogin, empty, errorBox, seg, store, sortBar } from "../core.js";
import { lineChart, barChart, PALETTE } from "../charts.js";

const YEAR = new Date().getFullYear();
const dayOfYear = (iso) => { const d = new Date(iso + "T12:00:00"); return Math.floor((d - new Date(d.getFullYear(), 0, 0)) / 86400000); };
const monthTicks = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
const monthName = (doy) => ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"][monthTicks.findLastIndex(t => t <= doy)] || "";

export async function render(view) {
  if (requireLogin(view)) return;
  if (!state.user?.statistik) { view.append(empty("Statistik er ikke slået til", "Modulet slås til under Admin → Funktioner.")); return; }
  const p = params();
  const tab = p.tab === "nfl" ? "nfl" : "arter";
  const body = h("div", {});
  view.append(h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Statistik"), h("p", { class: "lead" }, "Arter pr. år og nye arter for landet – beregnet fra DOFbasen-data.")),
    h("span", { class: "spacer" }),
    // Ny fane: kun tab bevares i adressen
    seg([["arter", "Arter pr. år"], ["nfl", "NFL"]], tab, (v) => { history.replaceState(null, "", `#/statistik?tab=${v}`); body.replaceChildren(); draw(v); })), body);
  const draw = (t) => t === "nfl" ? nflTab(body) : regionsTab(body, "arter");
  await draw(tab);
}

// ---------------------------------------------------------------- NFL: nye fuglearter for landet

async function nflTab(body) {
  const out = h("div", {});
  body.append(out);
  out.append(h("div", { class: "skeleton", style: { height: "320px" } }));
  let d;
  try { d = await api("/api/stats/nfl"); } catch (err) { out.replaceChildren(errorBox(err)); return; }
  const st = { y: Number(params().y) || 0 };
  const total = d.species.length;
  const best = d.years.reduce((a, b) => b.n > a.n ? b : a, d.years[0]);
  const thisYear = d.years.find(x => x.year === YEAR)?.n ?? 0;
  const chartBox = h("div", { class: "mt" });
  const listBox = h("div", {});
  const yearSel = h("select", { style: { width: "auto" }, onchange: () => pick(Number(yearSel.value)) },
    h("option", { value: 0 }, `Alle år (${fmtN(total)})`),
    ...[...d.years].reverse().map(x => h("option", { value: x.year }, `${x.year} (${fmtN(x.n)})`)));

  const drawChart = () => chartBox.replaceChildren(barChart({
    bars: d.years.map(x => ({ v: x.n, hl: x.year === st.y, tip: `${x.year}${x.year === YEAR ? " (til dato)" : ""}: ${x.n === 1 ? "1 ny art" : `${fmtN(x.n)} nye arter`}` })),
    xTicks: d.years.map((x, i) => [i, x.year]).filter(([, y]) => y % 5 === 0).map(([i, y]) => [i, String(y)]),
    yFormat: (v) => fmtN(Math.round(v)), height: 220, onClick: (i) => pick(d.years[i].year === st.y ? 0 : d.years[i].year) }));

  const drawList = () => {
    const rows = d.species.filter(s => !st.y || s.year === st.y).reverse();
    listBox.replaceChildren(rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", {}, "Første fund"), h("th", {}, "Art"), h("th", {}, "Lokalitet"), h("th", { class: "right" }, "Antal"),
        h("th", { class: "right", title: "År med fund i Danmark siden" }, "År"), h("th", { class: "right", title: "Observationer i alt" }, "Obs."))),
      h("tbody", {}, ...rows.map(s => h("tr", {},
        h("td", { class: "nowrap" }, fmtDate(s.first_date, false)),
        h("td", {}, h("a", { href: `#/art/${s.artnr}` }, s.navn), s.klass !== "Alm" ? [" ", tag(s.klass)] : null,
          s.latin ? h("div", { class: "muted", style: { fontSize: ".8rem", fontStyle: "italic" } }, s.latin) : null),
        h("td", {}, s.loknr ? h("a", { href: `#/lok/${s.loknr}` }, s.lokalitet) : h("span", { class: "muted" }, "–")),
        h("td", { class: "right num" }, s.antal ? fmtN(s.antal) : "–"),
        h("td", { class: "right num", title: s.n_years === 1 ? "Kun set i fundåret" : `Set i ${s.n_years} år` }, fmtN(s.n_years)),
        h("td", { class: "right num" }, fmtN(s.n_obs)))))))
      : empty(st.y ? `Ingen nye arter i ${st.y}` : "Ingen nye arter", st.y === YEAR ? "Året er ikke slut endnu." : ""));
  };
  const pick = (y) => { st.y = y; yearSel.value = String(y); setParams({ tab: "nfl", y: y || "" }); drawChart(); drawList(); };

  out.replaceChildren(
    h("div", { class: "card" },
      h("p", { class: "muted", style: { fontSize: ".85rem", margin: 0 } },
        `Ny Fugleart for Landet (NFL): arter, hvis første krydsbare fund (kategori A–C) i DOFbasen ligger i året. Underarter tæller som arten. `,
        `Arter med et fund før ${d.from} tæller ikke – heller ikke, hvis de først er indtastet senere. Ældre fund, der mangler i DOFbasen, kan give en art for meget. Opdateres hver nat.`),
      h("div", { class: "stats mt" },
        h("div", { class: "stat" }, h("b", {}, fmtN(total)), h("span", {}, `nye arter ${d.from}–${YEAR}`)),
        h("div", { class: "stat" }, h("b", {}, fmtN(thisYear)), h("span", {}, `i ${YEAR} til dato`)),
        h("div", { class: "stat" }, h("b", {}, (total / d.years.length).toLocaleString("da-DK", { maximumFractionDigits: 1 })), h("span", {}, "pr. år i gennemsnit")),
        best?.n ? h("div", { class: "stat" }, h("b", {}, fmtN(best.n)), h("span", {}, `rekord (${best.year})`)) : null),
      h("div", { class: "mt" }, h("h3", { style: { margin: "0 0 .25rem" } }, "Nye arter pr. år"),
        h("p", { class: "muted", style: { fontSize: ".8rem", margin: 0 } }, "Klik på en søjle for at se årets arter."), chartBox)),
    h("div", { class: "card mt" }, h("div", { class: "row between mb" }, h("h2", { style: { margin: 0 } }, "Arterne"), yearSel), listBox));
  if (st.y && !d.years.some(x => x.year === st.y)) st.y = 0;
  pick(st.y);
}

// ---------------------------------------------------------------- arter pr. år: Danmark, lokalafdelinger, kommuner

const SHOW_YEARS = 12;

/** Fanen Arter pr. år (også brugt af Lister, når Statistik-modulet ikke er synligt). tab = fanens navn i adressen. */
export async function regionsTab(body, tab = "arter") {
  const p = params();
  // y = 0: alle år samlet (standard)
  const st = { lv: p.lv || "dk", r: p.r || "", y: p.y ? Number(p.y) : 0, sort: p.sort || "label", q: "" };
  if (st.lv === "dk") st.r = "dk";
  const out = h("div", { class: "mt" });
  const levelSeg = seg([["dk", "Danmark"], ["afd", "Lokalafdelinger"], ["kom", "Kommuner"]], st.lv, (v) => { st.lv = v; st.r = v === "dk" ? "dk" : ""; load(); });
  body.append(h("div", { class: "card" }, h("div", { class: "row" }, levelSeg),
    h("p", { class: "muted mt", style: { fontSize: ".85rem", marginBottom: 0 } },
      "Antal arter registreret i DOFbasen pr. år. Samme regler som årslisterne: underarter tæller som arten, udelukkede arter og antal 0 tæller ikke. Indeværende år er til dato. Opdateres hver nat.")), out);
  const save = () => setParams({ tab, lv: st.lv, r: st.lv === "dk" ? "" : st.r, y: st.r && st.y ? st.y : "", sort: st.sort === "label" ? "" : st.sort });
  const load = () => { save(); return st.r ? detail() : overview(); };

  async function overview() {
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "320px" } }));
    let d;
    try { d = await api(`/api/lists/regions?level=${st.lv}`); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    if (!d.regions.length) { out.replaceChildren(empty("Ikke beregnet endnu", "Tallene beregnes i baggrunden – kom tilbage om lidt.")); return; }
    const years = d.years.slice(0, SHOW_YEARS);
    const max = Object.fromEntries(years.map(y => [y, Math.max(...d.regions.map(r => r.years[y] || 0))]));
    const search = h("input", { type: "search", placeholder: st.lv === "kom" ? "Søg kommune…" : "Søg lokalafdeling…", value: st.q, style: { maxWidth: "260px" } });
    const wrap = h("div", { class: "table-wrap", style: { maxHeight: "70vh" } });
    const val = (r, k) => k === "label" ? r.label : k === "total" ? r.total || 0 : r.years[k] || 0;
    const th = (label, key, cls = "right") => h("th", { class: `${cls} click`, style: { cursor: "pointer" }, onclick: () => { st.sort = String(key); save(); draw(); } },
      label, String(st.sort) === String(key) ? (key === "label" ? " ↑" : " ↓") : "");
    const draw = () => {
      const q = st.q.toLowerCase();
      const k = st.sort === "label" || st.sort === "total" ? st.sort : Number(st.sort);
      const rows = d.regions.filter(r => !q || r.label.toLowerCase().includes(q))
        .sort((a, b) => k === "label" ? a.label.localeCompare(b.label, "da") : val(b, k) - val(a, k) || a.label.localeCompare(b.label, "da"));
      const open = (r, y) => { st.r = r.key; st.y = y || 0; load(); };
      wrap.replaceChildren(h("table", { class: "data" },
        h("thead", {}, h("tr", {}, th(st.lv === "kom" ? "Kommune" : "Lokalafdeling", "label", ""), th("Alle år", "total"),
          ...years.map(y => th(y === YEAR ? `${y}*` : String(y), y)))),
        h("tbody", {}, ...rows.map(r => h("tr", {},
          h("td", { class: "nowrap" }, h("a", { href: "javascript:void 0", onclick: () => open(r) }, r.label)),
          h("td", { class: "right num muted" }, fmtN(r.total)),
          ...years.map(y => h("td", { class: "right num", style: { cursor: "pointer", fontWeight: r.years[y] && r.years[y] === max[y] ? 800 : null }, title: `${r.label} ${y}`,
            onclick: () => open(r, y) }, r.years[y] ? fmtN(r.years[y]) : "–")))))));
    };
    let t; search.oninput = () => { clearTimeout(t); t = setTimeout(() => { st.q = search.value; draw(); }, 150); };
    out.replaceChildren(h("div", { class: "card" }, h("div", { class: "row between mb" }, search,
      h("span", { class: "muted", style: { fontSize: ".85rem" } }, `Seneste ${years.length} år · * = til dato · fed = flest i året · klik for artsliste`)), wrap));
    draw();
  }

  async function detail() {
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "420px" } }));
    let d;
    try { d = await api(`/api/lists/regions/${encodeURIComponent(st.r)}${st.y ? `?year=${st.y}` : ""}`); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    if (!d.per_year.length) { out.replaceChildren(empty("Ikke beregnet endnu", "Tallene beregnes i baggrunden – kom tilbage om lidt.")); return; }
    const yearSel = h("select", { style: { width: "auto" }, onchange: () => { st.y = Number(yearSel.value); load(); } },
      h("option", { value: 0, selected: !st.y }, "Alle år"),
      ...[...d.per_year].reverse().map(x => h("option", { value: x.year, selected: x.year === st.y }, `${x.year} (${fmtN(x.n)} arter)`)));
    if (st.y && !d.per_year.some(x => x.year === st.y)) yearSel.append(h("option", { value: st.y, selected: true }, `${st.y} (0 arter)`));
    const all = !st.y;
    // Årsvisning: akkumulerede arter pr. dag i året
    const racePts = all ? [] : [...d.species].map(s => s.first_date).sort().map((dt, i) => [dayOfYear(dt), i + 1]);
    const nNew = d.species.filter(s => s.new).length;
    const best = d.per_year.reduce((a, b) => b.n > a.n ? b : a);
    const pts = d.per_year.map(x => [x.year, x.n]);
    const sortSt = { key: store("rgsort") || "sortering", dir: 1 };
    const bar = sortBar([["sortering", "Systematisk"], ["navn", "Navn"], ["first_date", "Første dato"], ["n_obs", "Obs."], ...(all ? [["n_years", "Antal år"]] : [])], sortSt, () => { store("rgsort", sortSt.key); drawList(); });
    const tbody = h("tbody");
    const drawList = () => {
      const k = sortSt.key;
      const rows = [...d.species].sort((a, b) => sortSt.dir * (k === "navn" ? a.navn.localeCompare(b.navn, "da") : k === "first_date" ? a.first_date.localeCompare(b.first_date)
        : k === "n_obs" ? a.n_obs - b.n_obs : k === "n_years" ? (a.n_years || 0) - (b.n_years || 0) : (a.sortering ?? 1e9) - (b.sortering ?? 1e9)));
      tbody.replaceChildren(...rows.map((s, i) => h("tr", {},
        h("td", { class: "muted num" }, i + 1),
        h("td", {}, h("a", { href: `#/art/${s.artnr}` }, s.navn), " ", s.klass !== "Alm" ? tag(s.klass) : null, s.new ? h("span", { class: "tag local", title: "Første gang registreret i regionen" }, "ny") : null),
        h("td", { class: "right num" }, fmtN(s.n_obs)),
        all ? h("td", { class: "right num", title: `Senest set ${s.last_year}` }, fmtN(s.n_years)) : null,
        h("td", { class: "nowrap" }, fmtDate(s.first_date, false)),
        h("td", { class: "nowrap muted" }, fmtDate(s.last_date, false)))));
    };
    drawList();
    out.replaceChildren(
      h("div", { class: "card" },
        h("div", { class: "row" }, st.lv !== "dk" ? h("button", { class: "btn sm ghost", onclick: () => { st.r = ""; load(); } }, "← Alle") : null,
          h("h2", { style: { margin: 0 } }, d.label), h("span", { style: { flex: 1 } }), yearSel),
        all ? h("div", { class: "stats mt" }, h("div", { class: "stat" }, h("b", {}, fmtN(d.species.length)), h("span", {}, "arter i alt")),
          h("div", { class: "stat" }, h("b", {}, fmtN(d.per_year.length)), h("span", {}, `år med data (${d.per_year[0].year}–${d.per_year.at(-1).year})`)),
          h("div", { class: "stat" }, h("b", {}, fmtN(d.per_year.at(-1).n)), h("span", {}, `arter i ${d.per_year.at(-1).year}${d.per_year.at(-1).year === YEAR ? " til dato" : ""}`)),
          h("div", { class: "stat" }, h("b", {}, fmtN(best.n)), h("span", {}, `rekord (${best.year})`)))
        : h("div", { class: "stats mt" }, h("div", { class: "stat" }, h("b", {}, fmtN(d.species.length)), h("span", {}, st.y === YEAR ? "arter til dato" : "arter")),
          h("div", { class: "stat" }, h("b", {}, fmtN(nNew)), h("span", {}, "nye for regionen")),
          h("div", { class: "stat" }, h("b", {}, fmtN(d.missing.length)), h("span", {}, `set i ${st.y - 1}, ikke i ${st.y}`)),
          h("div", { class: "stat" }, h("b", {}, fmtN(best.n)), h("span", {}, `rekord (${best.year})`))),
        all && pts.length > 1 ? h("div", { class: "mt" }, h("h3", { style: { margin: "0 0 .25rem" } }, "Arter pr. år"),
          lineChart({ series: [{ name: "Arter", points: pts, color: PALETTE[0] }], height: 220, xFormat: String,
            yFormat: (v) => fmtN(Math.round(v)) })) : null,
        racePts.length > 1 ? h("div", { class: "mt" }, h("h3", { style: { margin: "0 0 .25rem" } }, `Arter gennem ${st.y}`),
          lineChart({ series: [{ name: "Arter", points: racePts, color: PALETTE[0] }], step: true, height: 240, xFormat: monthName, xTicks: monthTicks,
            yFormat: (v) => fmtN(Math.round(v)) })) : null),
      h("div", { class: "card table-wrap mt" }, h("div", { class: "mb" }, bar), h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Art"), h("th", { class: "right" }, "Obs."), all ? h("th", { class: "right" }, "År") : null, h("th", {}, all ? "Første fund" : "Første"), h("th", {}, all ? "Seneste fund" : "Sidste"))), tbody)),
      d.missing.length ? h("details", { class: "more mt" }, h("summary", {}, `${d.missing.length} arter set i ${st.y - 1}, men ikke i ${st.y}${st.y === YEAR ? " (endnu)" : ""}`),
        h("div", { class: "chips wrap mt" }, ...d.missing.map(m => h("a", { class: "chip", href: `#/art/${m.artnr}` }, m.navn)))) : null);
  }

  await load();
}
