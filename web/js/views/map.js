import { api, h, fmtN, fmtDate, esc, KAT, KAT_RANK, store, seg, compass, ago } from "../core.js";
import { baseMap, katColor, cssVar, arrowSvg, GROUP_COLORS } from "../maps.js";
import { live } from "../live.js";
import { windLayer, speedLegend } from "../wind.js";

// Klasser som i DOFbasen; almindelige arter vises kun når de er trækkende med retning
const CLASSES = [["SU", "SU"], ["SUB", "SUB"], ["bemaerk", "Bemærk"], ["alm", "Alm. (trækkende)"]];
const DAYS = [["1", "I dag"], ["y", "I går"], ["sum", "Sum"]];
const SUM_DAYS = [2, 3, 5, 7];
const COMPASS = ["N", "NØ", "Ø", "SØ", "S", "SV", "V", "NV"];

/** Vægtet middelretning (grader mod) og enighed 0–1 for {N: antal, NØ: antal, …}. */
function vector(dirs, deg) {
  let x = 0, y = 0, n = 0;
  for (const [d, c] of Object.entries(dirs)) {
    const r = deg[d] * Math.PI / 180;
    x += c * Math.sin(r); y += c * Math.cos(r); n += c;
  }
  if (!n || (Math.abs(x) < 1e-9 && Math.abs(y) < 1e-9)) return { dir: null, r: 0, n };
  return { dir: Math.round((Math.atan2(x, y) * 180 / Math.PI + 360) % 360), r: Math.hypot(x, y) / n, n };
}
const addDirs = (into, dirs) => { for (const [d, c] of Object.entries(dirs || {})) into[d] = (into[d] || 0) + c; return into; };

/** Kompasrose: en søjle pr. retning, længden efter antal individer. */
function rose(dirs, deg, color, main) {
  const S = 220, c = S / 2, R = 84, max = Math.max(1, ...COMPASS.map(d => dirs[d] || 0));
  const ns = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, text) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text) e.textContent = text; return e; };
  const svg = el("svg", { viewBox: `0 0 ${S} ${S}`, class: "rose", role: "img", "aria-label": "Fordeling af trækretninger" });
  for (const f of [.33, .66, 1]) svg.append(el("circle", { cx: c, cy: c, r: R * f, class: "rose-ring" }));
  for (const d of COMPASS) {
    const a = deg[d] * Math.PI / 180, len = R * Math.sqrt((dirs[d] || 0) / max), w = 0.33;
    if (len > 0) {
      const p = (ang, r) => `${c + r * Math.sin(ang)},${c - r * Math.cos(ang)}`;
      svg.append(el("path", { d: `M${c},${c} L${p(a - w, len)} A${len},${len} 0 0 1 ${p(a + w, len)} Z`, fill: color, opacity: .85 }));
    }
    const lx = c + (R + 14) * Math.sin(a), ly = c - (R + 14) * Math.cos(a);
    svg.append(el("text", { x: lx, y: ly + 4, "text-anchor": "middle", class: "rose-label" }, d));
  }
  if (main != null) {
    const a = main * Math.PI / 180;
    svg.append(el("line", { x1: c, y1: c, x2: c + R * .95 * Math.sin(a), y2: c - R * .95 * Math.cos(a), class: "rose-main" }));
  }
  svg.append(el("circle", { cx: c, cy: c, r: 3, class: "rose-hub" }));
  return svg;
}

export async function render(view, { embedded = false } = {}) {
  const saved = store("mapst") || {};
  const st = { days: ["1", "y", "sum"].includes(saved.days) ? saved.days : "1", n: SUM_DAYS.includes(saved.n) ? saved.n : 3, on: new Set(saved.on || ["SU", "SUB", "bemaerk", "alm"]), migOnly: !!saved.migOnly, wind: saved.windOn === true,
    group: saved.group || "all" };
  const save = () => store("mapst", { days: st.days, n: st.n, on: [...st.on], migOnly: st.migOnly, windOn: st.wind, group: st.group });  // vind er slået fra, til man selv tænder den
  const mapEl = h("div", { class: "map full" });
  const chips = h("div", { class: "chips wrap", style: { marginTop: 0 } });
  const viewChips = h("div", { class: "chips wrap", style: { marginTop: 0 } });
  const periodEl = h("div", { class: "row wrap", style: { gap: "8px" } });
  // Periode: i dag (aktuel vind), i går eller sum af N døgn (observationer + middelvind kl. 6–18)
  const range = () => st.days === "1" ? { days: 1, back: 0 } : st.days === "y" ? { days: 1, back: 1 } : { days: st.n, back: 0 };
  const periodText = () => st.days === "1" ? "i dag" : st.days === "y" ? "i går" : `de sidste ${st.n} døgn`;
  const drawPeriod = () => periodEl.replaceChildren(
    seg(DAYS, st.days, (v) => { st.days = v; save(); drawPeriod(); load(); wind(); }),
    st.days === "sum" ? h("div", { class: "seg-n" }, ...SUM_DAYS.map(n => h("button", { type: "button", class: st.n === n ? "on" : "",
      onclick: () => { st.n = n; save(); drawPeriod(); load(); wind(); } }, `${n} døgn`))) : null);
  const info = h("small", { class: "muted" });
  const panel = h("div", { class: "card mig-panel" });
  let items = [], groups = {}, deg = {};

  const inClass = (t) => st.on.has(t.kategori);
  const inGroup = (t) => st.group === "all" || t.group === st.group;
  const groupColor = () => st.group === "all" ? cssVar("--brand") : GROUP_COLORS[st.group];

  const drawChips = () => { chips.replaceChildren(
    ...CLASSES.map(([k, label]) => {
      const n = items.filter(t => t.kategori === k && inGroup(t) && (!st.migOnly || t.dir != null)).length;
      const solo = st.on.size === 1 && st.on.has(k);
      return h("span", { class: `chip toggle ${st.on.has(k) ? "on" : ""}`, role: "button", tabindex: 0, title: "Slå til/fra",
        onclick: () => { st.on.has(k) ? st.on.delete(k) : st.on.add(k); update(); } },
        h("i", { class: "dot", style: { background: katColor(k) } }), label, h("span", { class: "n" }, ` ${fmtN(n)}`),
        h("button", { type: "button", class: `solo ${solo ? "active" : ""}`, title: solo ? "Vis alle klasser igen" : "Vis kun denne klasse",
          onclick: (e) => { e.stopPropagation(); st.on = solo ? new Set(CLASSES.map(c => c[0])) : new Set([k]); update(); } }, "S"));
    }));
    viewChips.replaceChildren(
      h("button", { class: `chip ${st.migOnly ? "on" : ""}`, title: "Skjul fund uden trækretning", onclick: () => { st.migOnly = !st.migOnly; update(); } }, "Kun trækkende"),
      h("button", { class: `chip ${st.wind ? "on" : ""}`, title: st.days === "1" ? "Aktuel vind" : "Middelvind kl. 6–18 i perioden",
        onclick: () => { st.wind = !st.wind; save(); drawChips(); wind(); } }, st.days === "1" ? "Vind nu" : "Middelvind"));
  };
  const windInfo = h("div", { class: "row between wrap", style: { gap: "8px" } });
  const tool = (label, ...content) => h("div", { class: "map-tool" }, h("span", { class: "map-tool-label" }, label), h("div", {}, ...content));

  view.append(embedded ? h("div", { style: { margin: "0 0 6px 2px" } }, info) : h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Kort over fund og træk"), info)),
    h("div", { class: "card", style: { padding: "8px" } }, mapEl),
    h("div", { class: "card map-toolbar" },
      tool("Periode", periodEl), tool("Klasser", chips), tool("Visning", viewChips), tool("Vind", windInfo)),
    panel);
  drawPeriod();
  const map = baseMap(mapEl, { center: [56.0, 11.0], zoom: 7 });
  const dotLayer = L.layerGroup().addTo(map);
  const arrowLayer = L.layerGroup().addTo(map);
  let windAnim = null;
  const renderer = L.canvas();

  /** Én pil pr. lokalitet: retningerne summeres og vægtes med antal individer. */
  function sites(list) {
    const by = new Map();
    for (const t of list) {
      if (!t.dirs || !Object.keys(t.dirs).length) continue;
      const s = by.get(t.loknr) || by.set(t.loknr, { loknr: t.loknr, lok: t.lok, lat: t.lat, lon: t.lon, dirs: {}, species: {} }).get(t.loknr);
      addDirs(s.dirs, t.dirs);
      const n = Object.values(t.dirs).reduce((a, b) => a + b, 0);
      s.species[t.art] = (s.species[t.art] || 0) + n;
    }
    return [...by.values()].map(s => ({ ...s, ...vector(s.dirs, deg) })).filter(s => s.dir != null);
  }

  function draw() {
    dotLayer.clearLayers(); arrowLayer.clearLayers();
    const order = KAT_RANK;
    const shown = items.filter(t => inClass(t) && inGroup(t));
    // Prikker: fund uden retning (klassefarve)
    if (!st.migOnly) for (const t of shown.filter(t => t.dir == null).sort((a, b) => order[a.kategori] - order[b.kategori])) {
      L.circleMarker([t.lat, t.lon], { renderer, radius: { SU: 9, SUB: 7, bemaerk: 5, alm: 4 }[t.kategori], color: "#fff", weight: 1.5,
        fillColor: katColor(t.kategori), fillOpacity: .95 })
        .bindPopup(`<b>${fmtN(t.max_antal)} ${esc(t.art)}</b><br>${esc(t.lok || "")}<br><small>${KAT[t.kategori] || "Alm."} · ${esc(t.dato)}</small><br><a href="#/traad/${t.id}">Åbn tråd →</a>`)
        .addTo(dotLayer);
    }
    // Pile: én pr. lokalitet
    const color = groupColor();
    const ss = sites(shown);
    for (const s of ss) {
      const size = Math.round(Math.min(44, 14 + 7 * Math.log10(s.n + 1)));
      const top = Object.entries(s.species).sort((a, b) => b[1] - a[1]);
      L.marker([s.lat, s.lon], { zIndexOffset: Math.round(s.n), icon: L.divIcon({ className: "mig-arrow", html: arrowSvg(s.dir, size, color), iconSize: [size, size], iconAnchor: [size / 2, size / 2] }) })
        .bindPopup(`<b>${esc(s.lok)}</b><br>${fmtN(s.n)} trækkende mod ${compass(s.dir)} (${s.dir}°) · enighed ${Math.round(s.r * 100)} %` +
          `<br><small>${top.slice(0, 6).map(([n, c]) => `${esc(n)} ${fmtN(c)}`).join(", ")}${top.length > 6 ? ` + ${top.length - 6} arter` : ""}</small>` +
          `<br><a href="#/lok/${s.loknr}">Lokaliteten →</a>`)
        .addTo(arrowLayer);
    }
    info.textContent = `${fmtN(shown.length)} tråde · træk på ${fmtN(ss.length)} lokaliteter`;
    drawPanel(ss);
  }

  function drawPanel(ss) {
    // Gruppevalg tæller individer med retning inden for de valgte klasser
    const byGroup = { all: {} };
    for (const t of items.filter(inClass)) {
      if (!t.dirs) continue;
      addDirs(byGroup.all, t.dirs);
      if (t.group) addDirs(byGroup[t.group] ||= {}, t.dirs);
    }
    const total = (d) => Object.values(d || {}).reduce((a, b) => a + b, 0);
    const opts = [["all", "Alle fugle"], ...Object.entries(groups)];
    const dirs = byGroup[st.group] || {};
    const v = vector(dirs, deg);
    const color = groupColor();
    const species = {};
    for (const t of items.filter(t => inClass(t) && inGroup(t) && t.dirs)) species[t.art] = (species[t.art] || 0) + total(t.dirs);
    const topSp = Object.entries(species).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const topSites = [...ss].sort((a, b) => b.n - a.n).slice(0, 8);
    const bar = (n, max) => h("div", { class: "bar-inline" }, h("i", { style: { width: `${Math.max(3, n / max * 100)}%`, background: color } }));
    const dayText = periodText();

    panel.replaceChildren(
      h("div", { class: "row between wrap", style: { gap: "8px" } }, h("h2", { style: { margin: 0 } }, "Træk ", dayText),
        h("small", { class: "muted" }, "Retning fra adfærdskoden, vægtet med antal individer")),
      h("div", { class: "chips wrap" }, ...opts.map(([k, label]) => h("button", { class: `chip ${st.group === k ? "on" : ""}`, onclick: () => { st.group = k; update(); } },
        k !== "all" ? h("i", { class: "dot", style: { background: GROUP_COLORS[k], marginRight: "5px" } }) : null, label,
        h("span", { class: "n" }, ` ${fmtN(total(byGroup[k]))}`)))),
      !v.n ? h("p", { class: "muted mt" }, "Ingen trækkende fugle med retning i det valgte udsnit.") : h("div", { class: "mig-grid mt" },
        h("div", { class: "mig-rose" }, rose(dirs, deg, color, v.dir)),
        h("div", { class: "stack", style: { gap: "10px" } },
          h("div", { class: "stats", style: { gridTemplateColumns: "repeat(2, 1fr)" } },
            h("div", { class: "stat" }, h("b", {}, fmtN(v.n)), h("span", {}, "trækkende individer")),
            h("div", { class: "stat" }, h("b", {}, fmtN(ss.length)), h("span", {}, "lokaliteter med træk")),
            h("div", { class: "stat" }, h("b", { style: { display: "flex", alignItems: "center", gap: "6px" } }, v.dir == null ? "–" : [h("span", { html: arrowSvg(v.dir, 20, color) }), `${compass(v.dir)}`]),
              h("span", {}, v.dir == null ? "hovedretning" : `hovedretning (${v.dir}°)`)),
            h("div", { class: "stat" }, h("b", {}, `${Math.round(v.r * 100)} %`), h("span", { title: "100 % = alle samme vej, 0 % = spredt" }, "enighed om retning"))),
          h("div", { class: "grid-2" },
            h("div", {}, h("h3", { class: "mig-h" }, "Flest trækkende"),
              h("ul", { class: "mini-list" }, ...topSp.map(([n, c]) => h("li", { class: "bar-row" }, h("span", {}, n), h("b", { class: "num" }, fmtN(c)), bar(c, topSp[0][1]))))),
            h("div", {}, h("h3", { class: "mig-h" }, "Største træksteder"),
              h("ul", { class: "mini-list" }, ...topSites.map(s => h("li", { class: "bar-row" },
                h("a", { href: `#/lok/${s.loknr}` }, s.lok), h("b", { class: "num" }, h("span", { html: arrowSvg(s.dir, 13, color) }), ` ${fmtN(s.n)}`), bar(s.n, topSites[0].n)))))))));
  }

  function update() { save(); drawChips(); draw(); }

  async function load() {
    try {
      const r = range();
      const d = await api(`/api/map/days?days=${r.days}&back=${r.back}`);
      groups = d.groups; deg = d.dirs; items = d.items;
    } catch { items = []; }
    if (st.group !== "all" && !groups[st.group]) st.group = "all";
    drawChips(); draw();
  }

  // Vind som partikler (Windy-agtigt) over et farvelag for vindstyrken; feltet er DMI's HARMONIE-model
  async function wind() {
    windAnim?.remove(); windAnim = null;
    windInfo.replaceChildren();
    if (!st.wind) return;
    try {
      const r = range();
      const g = await api(st.days === "1" ? "/api/weather/grid" : `/api/weather/grid?days=${r.days}&back=${r.back}`);
      if (!st.wind || !g.u?.length) { windInfo.replaceChildren(h("small", { class: "muted" }, "Vinddata er ikke tilgængelige lige nu.")); return; }
      windAnim = windLayer(map, g);
      const when = g.mean ? `middel kl. 6–18, ${fmtDate(g.start, false)}${g.end !== g.start ? `–${fmtDate(g.end, false)}` : ""}` : (g.time ? `målt ${ago(g.time + "Z")}` : "aktuel");
      windInfo.replaceChildren(speedLegend(), h("small", { class: "muted" }, `10 m over jorden · ${when} · ${g.source}`));
    } catch { /* vinddata utilgængelige */ }
  }

  await Promise.all([load(), wind()]);
  let t;
  const off = live.on("feed", () => { if (st.days !== "y") { clearTimeout(t); t = setTimeout(load, 1500); } });
  const windTimer = setInterval(() => { if (st.wind && document.visibilityState === "visible") wind(); }, 15 * 60000);
  return () => { off(); clearTimeout(t); clearInterval(windTimer); windAnim?.remove(); map.remove(); };
}
