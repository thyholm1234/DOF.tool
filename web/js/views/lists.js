import { api, state, h, icon, fmtN, fmtDate, tag, params, setParams, loadRef, requireLogin, empty, errorBox, modal, toast, seg, store, sortBar, todayISO } from "../core.js";
import { lineChart, seriesLegend, PALETTE } from "../charts.js";

const YEAR = new Date().getFullYear();
const dayOfYear = (iso) => { const d = new Date(iso + "T12:00:00"); return Math.floor((d - new Date(d.getFullYear(), 0, 0)) / 86400000); };
const monthTicks = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
const monthName = (doy) => ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"][monthTicks.findLastIndex(t => t <= doy)] || "";

export async function render(view) {
  const p = params();
  const tab = p.tab || "mine";
  // Matrikler er flyttet til eget modul
  if (tab === "matrikler") { location.hash = "#/matrikel?tab=matrikler"; return; }
  view.append(h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Lister & scoreboards"), h("p", { class: "lead" }, "Årslister, kommuner, egne områder og arter pr. år – beregnet direkte fra DOFbasen-data. Matrikelbirding har sit eget modul.")),
    h("span", { class: "spacer" }),
    seg([["mine", "Mine"], ["score", "Scoreboard"], ["regioner", "Arter pr. år"], ["grupper", "Grupper"]], tab, (v) => { setParams({ tab: v }); body.replaceChildren(); draw(v); })));
  const body = h("div", {});
  view.append(body);
  const draw = (t) => t === "score" ? scoreboard(body) : t === "regioner" ? regionsTab(body) : t === "grupper" ? groups(body) : mine(body);
  await draw(tab);
}

// ---------------------------------------------------------------- arter pr. år: Danmark, lokalafdelinger, kommuner

const SHOW_YEARS = 12;

async function regionsTab(body) {
  const p = params();
  // y = 0: alle år samlet (standard)
  const st = { lv: p.lv || "dk", r: p.r || "", y: p.y ? Number(p.y) : 0, sort: p.sort || "label", q: "" };
  if (st.lv === "dk") st.r = "dk";
  const out = h("div", { class: "mt" });
  const levelSeg = seg([["dk", "Danmark"], ["afd", "Lokalafdelinger"], ["kom", "Kommuner"]], st.lv, (v) => { st.lv = v; st.r = v === "dk" ? "dk" : ""; load(); });
  body.append(h("div", { class: "card" }, h("div", { class: "row" }, levelSeg),
    h("p", { class: "muted mt", style: { fontSize: ".85rem", marginBottom: 0 } },
      "Antal arter registreret i DOFbasen pr. år. Samme regler som årslisterne: underarter tæller som arten, udelukkede arter og antal 0 tæller ikke. Indeværende år er til dato. Opdateres hver nat.")), out);
  const save = () => setParams({ tab: "regioner", lv: st.lv, r: st.lv === "dk" ? "" : st.r, y: st.r && st.y ? st.y : "", sort: st.sort === "label" ? "" : st.sort });
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
    // Kapløb for det valgte år mod året før og rekordåret (akkumulerede arter pr. dag i året)
    let raceSeries = [];
    if (!all) {
      const cum = (species) => [...species].map(s => s.first_date).sort().map((dt, i) => [dayOfYear(dt), i + 1]);
      const bestYear = d.per_year.reduce((a, b) => b.n > a.n ? b : a).year;
      const others = [...new Set([st.y - 1, bestYear])].filter(y => y !== st.y && d.per_year.some(x => x.year === y));
      const extra = await Promise.all(others.map(y => api(`/api/lists/regions/${encodeURIComponent(st.r)}?year=${y}`).catch(() => null)));
      raceSeries = [{ name: String(st.y), points: cum(d.species), color: PALETTE[0], width: 2.5 },
        ...others.map((y, i) => extra[i] && { name: y === bestYear ? `${y} (rekord)` : String(y), points: cum(extra[i].species), color: PALETTE[i + 1], width: 1.5, dash: "4 3" })]
        .filter(x => x && x.points.length);
    }
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
        !all && raceSeries.length ? h("div", { class: "mt" }, h("h3", { style: { margin: "0 0 .25rem" } }, `Kapløb ${st.y} – arter gennem året`),
          lineChart({ series: raceSeries, step: true, height: 260, xFormat: monthName, xTicks: monthTicks, yFormat: (v) => fmtN(Math.round(v)),
            markers: st.y === YEAR ? [{ x: dayOfYear(todayISO()), label: "i dag" }] : [] }),
          raceSeries.length > 1 ? seriesLegend(raceSeries) : null) : null),
      h("div", { class: "card table-wrap mt" }, h("div", { class: "mb" }, bar), h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Art"), h("th", { class: "right" }, "Obs."), all ? h("th", { class: "right" }, "År") : null, h("th", {}, all ? "Første fund" : "Første"), h("th", {}, all ? "Seneste fund" : "Sidste"))), tbody)),
      d.missing.length ? h("details", { class: "more mt" }, h("summary", {}, `${d.missing.length} arter set i ${st.y - 1}, men ikke i ${st.y}${st.y === YEAR ? " (endnu)" : ""}`),
        h("div", { class: "chips wrap mt" }, ...d.missing.map(m => h("a", { class: "chip", href: `#/art/${m.artnr}` }, m.navn)))) : null);
  }

  await load();
}

// ---------------------------------------------------------------- mine lister

async function mine(body) {
  if (requireLogin(body)) return;
  const meta = await api("/api/lists/meta");
  const p = params();
  const st = { scope: p.scope || "dk", year: p.year || String(YEAR), sort: "dato" };
  const scopeSel = h("select", { style: { width: "auto" } });
  for (const sc of meta.scopes) scopeSel.append(h("option", { value: sc.scope }, sc.label));
  scopeSel.value = st.scope;
  const yearSel = h("select", { style: { width: "auto" } }, h("option", { value: "all" }, "Livsliste (alle år)"),
    ...[...new Set([YEAR, ...meta.years])].sort((a, b) => b - a).map(y => h("option", { value: y }, y)));
  yearSel.value = st.year;
  const out = h("div", { class: "stack mt" });
  const addArea = h("a", { class: "btn sm ghost", href: "#/alarmer?tab=omraader", html: `${icon("plus")} Nyt område` });
  body.append(h("div", { class: "card" },
    h("div", { class: "row" }, scopeSel, yearSel, h("span", { class: "spacer", style: { flex: 1 } }), addArea,
      h("button", { class: "btn sm", onclick: compare, html: `${icon("users")} Sammenlign` })),
    h("p", { class: "muted mt", style: { marginBottom: 0, fontSize: ".85rem" } },
      "Matrikellister finder du under ", h("a", { href: "#/matrikel" }, "Matrikel"), ". ",
      "Afdelinger og kommuner vælges under ", h("a", { href: "#/profil" }, "profil"), ", egne områder (polygon/radius) under ", h("a", { href: "#/alarmer?tab=omraader" }, "Alarmer → Områder"), ".")),
    out);

  const load = async () => {
    st.scope = scopeSel.value;
    st.year = yearSel.value;
    setParams({ scope: st.scope === "dk" ? "" : st.scope, year: st.year });
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "220px" } }));
    try {
      const q = new URLSearchParams({ scope: st.scope, year: st.year });
      drawList(out, await api(`/api/lists/user/${state.user.obserkode}?${q}`), st.year);
    } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  scopeSel.onchange = load; yearSel.onchange = load;
  await load();

  async function compare() {
    const inp = h("input", { placeholder: "Obserkode, fx 8000ABC", style: { textTransform: "uppercase" } });
    const res = h("div", { class: "mt" });
    modal("Sammenlign lister", h("form", { onsubmit: async (e) => {
      e.preventDefault();
      try {
        const q = new URLSearchParams({ a: state.user.obserkode, b: inp.value.trim(), scope: st.scope, year: st.year });
        const d = await api(`/api/lists/compare?${q}`);
        res.replaceChildren(h("p", {}, `Fælles: ${d.both} arter`),
          h("div", { class: "grid-2" },
            h("div", {}, h("h3", {}, `Kun ${d.a.navn} (${d.only_a.length})`), h("ul", {}, ...d.only_a.map(i => h("li", {}, i.art)))),
            h("div", {}, h("h3", {}, `Kun ${d.b.navn} (${d.only_b.length})`), h("ul", {}, ...d.only_b.map(i => h("li", {}, i.art, h("small", {}, ` ${fmtDate(i.dato, false)}`)))))));
      } catch (err) { res.replaceChildren(errorBox(err)); }
    } }, h("div", { class: "row" }, inp, h("button", { class: "btn primary" }, "Sammenlign")), res), { wide: true });
  }
}

export function drawList(out, data, year) {
  const items = data.items;
  const isYear = year && year !== "all";
  const pts = items.map((it, i) => [isYear ? dayOfYear(it.dato) : new Date(it.dato).getFullYear(), i + 1]);
  const sort = store("listsort") || { key: "dato", dir: 1 };
  const tbody = h("tbody");
  const cmp = {
    dato: (a, b) => a.dato.localeCompare(b.dato) || a.obsid - b.obsid,
    navn: (a, b) => a.art.localeCompare(b.art, "da"),
    sys: (a, b) => (a.sortering || 0) - (b.sortering || 0),
    lok: (a, b) => (a.lok || "").localeCompare(b.lok || "", "da"),
  };
  const drawRows = () => {
    const sorted = [...items].sort((a, b) => sort.dir * (cmp[sort.key] || cmp.dato)(a, b));
    tbody.replaceChildren(...sorted.map((it) => h("tr", {},
      h("td", { class: "num muted" }, items.indexOf(it) + 1),
      h("td", {}, h("a", { href: `#/art/${it.artnr}` }, it.art), it.klass !== "Alm" ? [" ", tag(it.klass)] : null),
      h("td", { class: "nowrap" }, fmtDate(it.dato, !isYear)),
      h("td", {}, it.loknr ? h("a", { href: `#/lok/${it.loknr}` }, it.lok) : "–"))));
  };
  drawRows();
  const th = (label, key) => h("th", { style: { cursor: "pointer" }, onclick: () => { sort.dir = sort.key === key ? -sort.dir : 1; sort.key = key; store("listsort", sort); drawRows(); } }, label);
  const bar = sortBar([["dato", "Dato"], ["navn", "Navn"], ["sys", "Systematik"], ["lok", "Lokalitet"]], sort, () => { store("listsort", sort); drawRows(); });
  out.replaceChildren(
    h("div", { class: "card" },
      h("div", { class: "row between" }, h("div", {}, h("div", { class: "muted" }, data.navn), h("div", { style: { fontSize: "2.4rem", fontWeight: 800, letterSpacing: "-0.03em" } }, fmtN(data.count), h("span", { class: "muted", style: { fontSize: "1rem", fontWeight: 600 } }, " arter"))),
        items.length ? h("div", { class: "right muted" }, "Seneste: ", h("b", {}, items[items.length - 1].art), h("br"), fmtDate(items[items.length - 1].dato)) : null),
      items.length > 1 ? lineChart({ series: [{ name: "Arter", points: pts, color: PALETTE[0] }], step: true, height: 200,
        xFormat: isYear ? monthName : String, xTicks: isYear ? monthTicks : undefined, yFormat: (v) => fmtN(Math.round(v)) }) : null),
    items.length ? h("div", { class: "card table-wrap" }, h("div", { class: "mb" }, bar), h("table", { class: "data list-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "#"), th("Art", "navn"), th("Første dato", "dato"), h("th", {}, "Lokalitet"))), tbody))
      : empty("Ingen arter på denne liste endnu", "Data kommer fra DOFbasen – nye observationer tæller med inden for få minutter."));
}

// ---------------------------------------------------------------- scoreboard

async function scoreboard(body) {
  await loadRef();
  const p = params();
  const st = { scope: p.scope || "dk", year: p.year || String(YEAR), m: 0, group: p.group || "" };
  const scopeSel = h("select", { style: { width: "auto" } },
    h("option", { value: "dk" }, "Danmark"),
    h("optgroup", { label: "Lokalafdelinger" }, ...state.afdelinger.map(a => h("option", { value: `afd:${a.id}` }, a.navn))),
    h("optgroup", { label: "Kommuner" }, ...state.kommuner.map(k => h("option", { value: `kom:${k.kode}` }, k.navn))));
  scopeSel.value = st.scope;
  const yearSel = h("select", { style: { width: "auto" } }, h("option", { value: "all" }, "Alle år"),
    ...Array.from({ length: 12 }, (_, i) => YEAR - i).map(y => h("option", { value: y }, y)));
  yearSel.value = st.year;
  const groupSel = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle deltagere"));
  if (state.user) for (const g of await api("/api/lists/groups")) groupSel.append(h("option", { value: g.id }, `Gruppe: ${g.name}`));
  groupSel.value = st.group;
  const view = seg([["rank", "Rangliste"], ["race", "Kapløb"], ["matrix", "Matrix"], ["unique", "Unikke"]], store("sbview") || "rank", (v) => { store("sbview", v); draw(); });
  const out = h("div", { class: "mt" });
  body.append(h("div", { class: "card" }, h("div", { class: "row" }, scopeSel, yearSel, groupSel, h("span", { style: { flex: 1 } }), view),
    h("p", { class: "muted mt", style: { fontSize: ".85rem", marginBottom: 0 } }, "Afdelings- og kommuneboards viser brugere, der har valgt området under profil. Grupper kan indeholde alle obserkoder.")), out);
  let data = null;
  const load = async () => {
    st.scope = scopeSel.value;
    st.year = yearSel.value; st.group = groupSel.value;
    setParams({ tab: "score", scope: st.scope === "dk" ? "" : st.scope, year: st.year, group: st.group });
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "300px" } }));
    try {
      const q = new URLSearchParams({ scope: st.scope, year: st.year });
      if (st.group) q.set("group", st.group);
      data = await api(`/api/lists/scoreboard?${q}`);
      draw();
    } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  const draw = () => {
    if (!data) return;
    const mode = view.querySelector(".on")?.textContent;
    const rows = data.rows;
    if (!rows.length) { out.replaceChildren(empty("Ingen deltagere endnu")); return; }
    if (mode === "Kapløb") out.replaceChildren(h("div", { class: "card" }, race(rows.slice(0, 10), st.year)));
    else if (mode === "Matrix") out.replaceChildren(h("div", { class: "card table-wrap" }, matrix(rows.slice(0, 25), data.species)));
    else if (mode === "Unikke") out.replaceChildren(h("div", { class: "card" }, unique(rows, data.species)));
    else out.replaceChildren(ranking(rows, st));
  };
  [scopeSel, yearSel, groupSel].forEach(el => el.onchange = load);
  await load();
}

export function ranking(rows, st) {
  if (!rows.length) return empty("Ingen deltagere endnu");
  const top = rows[0]?.antal || 1;
  return h("div", { class: "rank" }, ...rows.map(r => h("div", { class: `rank-row ${state.user?.obserkode === r.obserkode ? "me" : ""}`, onclick: () => showUser(r, st) },
    h("div", { class: "place" }, r.placering),
    h("div", {}, h("div", { class: "who" }, r.navn), h("div", { class: "last" }, r.sidste_art ? `Seneste: ${r.sidste_art} · ${fmtDate(r.sidste_dato, false)}` : "Ingen arter endnu"),
      h("div", { class: "bar-inline" }, h("i", { style: { width: `${(r.antal / top) * 100}%` } }))),
    h("div", { class: "score" }, fmtN(r.antal)))));
}

export async function showUser(r, st) {
  const out = h("div", {}, h("div", { class: "skeleton" }));
  modal(r.navn, out, { wide: true });
  try {
    const q = new URLSearchParams({ scope: st.scope, year: st.year });
    if (st.m) q.set("matrikel", st.m);
    drawList(out, await api(`/api/lists/user/${r.obserkode}?${q}`), st.year);
  } catch (err) { out.replaceChildren(errorBox(err)); }
}

export function race(rows, year) {
  const isYear = year !== "all";
  const series = rows.map((r, i) => ({ name: r.navn, color: PALETTE[i % PALETTE.length],
    points: r.firsts.map(([, d], j) => [isYear ? dayOfYear(d) : new Date(d).getFullYear() + dayOfYear(d) / 366, j + 1]) }));
  return h("div", {}, h("h3", {}, "Kumulativt antal arter"),
    lineChart({ series, step: true, height: 320, xFormat: isYear ? monthName : (x) => String(Math.floor(x)), xTicks: isYear ? monthTicks : undefined, yFormat: (v) => fmtN(Math.round(v)) }),
    seriesLegend(series));
}

export function matrix(rows, names) {
  const sets = rows.map(r => new Map(r.firsts.map(([a, d]) => [a, d])));
  const species = [...new Set(rows.flatMap(r => r.firsts.map(f => f[0])))].sort((a, b) => (names[a] || "").localeCompare(names[b] || "", "da"));
  return h("table", { class: "matrix" },
    h("thead", {}, h("tr", {}, h("th", {}, `${species.length} arter`), ...rows.map(r => h("th", { class: "rot", title: r.navn }, r.navn)))),
    h("tbody", {}, ...species.map(a => {
      const have = sets.filter(s => s.has(a)).length;
      return h("tr", {}, h("td", { class: "sp" }, names[a] || a),
        ...sets.map(s => s.has(a) ? h("td", { class: have === 1 ? "only" : "yes", title: s.get(a) }, have === 1 ? "★" : "✓") : h("td", {})));
    })));
}

export function unique(rows, names) {
  const count = new Map();
  rows.forEach(r => r.firsts.forEach(([a]) => count.set(a, (count.get(a) || 0) + 1)));
  const items = rows.map(r => ({ r, only: r.firsts.filter(([a]) => count.get(a) === 1) })).filter(x => x.only.length);
  if (!items.length) return empty("Ingen arter som kun én deltager har");
  return h("div", { class: "stack" }, h("h3", {}, "Arter som kun én deltager har"),
    ...items.sort((a, b) => b.only.length - a.only.length).map(({ r, only }) => h("div", {},
      h("b", {}, `${r.navn} (${only.length})`), h("div", { class: "muted" }, only.map(([a, d]) => `${names[a]} (${fmtDate(d, false)})`).join(", ")))));
}

// ---------------------------------------------------------------- grupper

async function groups(body) {
  if (requireLogin(body)) return;
  const list = h("div", { class: "stack" });
  const name = h("input", { placeholder: "Gruppenavn, fx 'Fugleholdet'" });
  const code = h("input", { placeholder: "Invitationskode", style: { textTransform: "uppercase" } });
  body.append(h("div", { class: "grid-2" },
    h("form", { class: "card stack", onsubmit: async (e) => { e.preventDefault(); try { await api("/api/lists/groups", { method: "POST", body: { name: name.value } }); name.value = ""; load(); } catch (err) { toast(err.message, "err"); } } },
      h("h3", {}, "Opret gruppe"), h("p", { class: "muted" }, "En gruppe får sit eget scoreboard. Du kan tilføje alle obserkoder – også folk der ikke bruger portalen."), name, h("button", { class: "btn primary" }, "Opret")),
    h("form", { class: "card stack", onsubmit: async (e) => { e.preventDefault(); try { const g = await api("/api/lists/groups/join", { method: "POST", body: { code: code.value } }); toast(`Du er nu med i ${g.name}`); code.value = ""; load(); } catch (err) { toast(err.message, "err"); } } },
      h("h3", {}, "Deltag med kode"), h("p", { class: "muted" }, "Få koden af gruppens ejer."), code, h("button", { class: "btn" }, "Deltag"))),
    h("h2", { class: "mt2" }, "Mine grupper"), list);
  const load = async () => {
    const gs = await api("/api/lists/groups");
    list.replaceChildren(...(gs.length ? gs.map(g => groupCard(g, load)) : [empty("Du er ikke med i nogen grupper endnu")]));
  };
  await load();
}

function groupCard(g, reload) {
  const members = h("div", { class: "muted" }, "…");
  const add = h("input", { placeholder: "Tilføj obserkode", style: { width: "180px", textTransform: "uppercase" } });
  const card = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", {}, g.name), h("span", { class: "spacer" }),
      g.invite_code ? h("span", { class: "muted" }, "Kode: ", h("b", { class: "kbd" }, g.invite_code)) : null,
      h("a", { class: "btn sm", href: `#/lister?tab=score&group=${g.id}` }, "Scoreboard")),
    members,
    g.owner ? h("form", { class: "row mt", onsubmit: async (e) => { e.preventDefault(); try { await api(`/api/lists/groups/${g.id}/members`, { method: "POST", body: { obserkode: add.value } }); add.value = ""; loadMembers(); } catch (err) { toast(err.message, "err"); } } },
      add, h("button", { class: "btn sm" }, "Tilføj"), h("span", { style: { flex: 1 } }),
      h("button", { type: "button", class: "btn sm ghost danger", onclick: async () => { if (confirm("Slet gruppen?")) { await api(`/api/lists/groups/${g.id}`, { method: "DELETE" }); reload(); } } }, "Slet gruppe"))
      : h("button", { class: "btn sm ghost danger mt", onclick: async () => { await api(`/api/lists/groups/${g.id}/members/${state.user.obserkode}`, { method: "DELETE" }); reload(); } }, "Forlad gruppen"));
  const loadMembers = async () => {
    const d = await api(`/api/lists/groups/${g.id}`);
    members.replaceChildren(...d.members.map(m => h("span", { class: "chip", style: { marginRight: "6px", marginBottom: "6px" } }, m.navn,
      g.owner && m.obserkode !== state.user.obserkode ? h("span", { style: { cursor: "pointer", marginLeft: "4px" }, onclick: async () => { await api(`/api/lists/groups/${g.id}/members/${m.obserkode}`, { method: "DELETE" }); loadMembers(); } }, "×") : null)));
  };
  loadMembers();
  return card;
}
