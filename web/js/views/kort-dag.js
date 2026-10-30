// Kort → Dag: en valgfri dags tråde i hele landet. Filtrene SU, SUB, Bemærk, lokale hits og fænologi slås til og fra
// uden at hente igen (serveren sender et flag pr. filter). Én markør pr. lokalitet; farven er det vigtigste synlige fund.
// Den seneste uge caches på serveren, så dagknapperne er hurtige.
import { api, h, fmtN, fmtDate, esc, tag, todayISO, params, setParams, store, empty, errorBox, doyLabel, KAT } from "../core.js";
import { baseMap, cssVar } from "../maps.js";
import { live } from "../live.js";

const FILTERS = [["SU", "SU"], ["SUB", "SUB"], ["bemaerk", "Bemærk"], ["local", "Lokale hits"], ["pheno", "Fænologi"]];
const COLOR = { SU: "--su", SUB: "--sub", bemaerk: "--bem", local: "--local", pheno: "--warn" };
const PRIO = ["SU", "SUB", "bemaerk", "pheno", "local"];
const WEEK = 7;
const WD = ["søn", "man", "tir", "ons", "tor", "fre", "lør"];

const isoShift = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
/** Filtrene et punkt hører til. */
const flagsOf = (p) => [...(["SU", "SUB", "bemaerk"].includes(p.kategori) ? [p.kategori] : []), ...(p.pheno ? ["pheno"] : []), ...(p.local ? ["local"] : [])];
const threadHash = (p, day) => `#/traad/${day}/${p.artnr}/${p.loknr}`;

export async function renderDay(view) {
  const p = params();
  const saved = store("kort-dag") || {};
  const today = todayISO();
  const st = {
    date: p.date && p.date <= today ? p.date : today,
    on: new Set(saved.on?.length ? saved.on : ["SU", "SUB", "bemaerk"]),
    data: null,
  };
  const mapEl = h("div", { class: "map full daymap-map" });
  const listEl = h("div", { class: "pulse-list" });
  const chipsEl = h("div", { class: "chips wrap", style: { marginTop: 0 } });
  const daysEl = h("div", { class: "chips wrap daymap-days", style: { marginTop: 0 } });
  const infoEl = h("small", { class: "muted" });
  const dateInput = h("input", { type: "date", value: st.date, max: today, style: { width: "auto" }, onchange: (e) => go(e.target.value) });
  view.append(
    h("p", { class: "lead" }, "En dags observationer i hele landet. Vælg dag og filtre – den seneste uge ligger klar."),
    h("div", { class: "card toolbox" },
      h("div", { class: "row between wrap", style: { gap: "8px" } }, daysEl,
        h("div", { class: "row", style: { gap: "6px" } },
          h("button", { class: "btn sm ghost", "aria-label": "Forrige dag", onclick: () => go(isoShift(st.date, -1)) }, "‹"), dateInput,
          h("button", { class: "btn sm ghost", "aria-label": "Næste dag", onclick: () => go(isoShift(st.date, 1)) }, "›"))),
      h("div", { class: "row between wrap", style: { gap: "8px", marginTop: "8px" } }, chipsEl, infoEl)),
    h("div", { class: "pulse-layout" }, mapEl,
      h("div", { class: "card pulse-side" }, h("div", { class: "card-head" }, h("h3", {}, "Fund")), listEl)));

  const map = baseMap(mapEl, { center: [56.15, 11.2], zoom: 7 });
  const layer = L.layerGroup().addTo(map);
  const markers = new Map();  // loknr -> marker

  // ------------------------------------------------ dagknapper (seneste uge) og filtre
  const drawDays = () => daysEl.replaceChildren(...Array.from({ length: WEEK + 1 }, (_, i) => isoShift(today, -i)).map(d => {
    const label = d === today ? "I dag" : d === isoShift(today, -1) ? "I går" : `${WD[new Date(d + "T12:00:00").getDay()]} ${Number(d.slice(8))}.`;
    return h("button", { class: `chip ${d === st.date ? "on" : ""}`, type: "button", onclick: () => go(d) }, label);
  }));
  const drawChips = () => {
    const c = st.data?.counts || {};
    chipsEl.replaceChildren(...FILTERS.map(([k, l]) => h("button", {
      class: `chip toggle ${st.on.has(k) ? "on" : ""}`, type: "button",
      title: k === "local" ? `Arten er fundet færre end ${st.data?.local_max ?? 10} gange på lokaliteten gennem alle år (estimerede fund)`
        : k === "pheno" ? "Arten ses uden for sin normale periode" : "",
      onclick: () => { st.on.has(k) ? st.on.delete(k) : st.on.add(k); store("kort-dag", { on: [...st.on] }); drawChips(); drawMap(); } },
      h("i", { style: { background: cssVar(COLOR[k]), width: "10px", height: "10px", borderRadius: "50%", display: "inline-block" } }),
      l, st.data ? h("span", { class: "n" }, fmtN(c[k] ?? 0)) : null)));
  };

  // ------------------------------------------------ kort og liste
  const visible = () => (st.data?.points || []).map(p => ({ ...p, flags: flagsOf(p).filter(f => st.on.has(f)) })).filter(p => p.flags.length);
  const top = (p) => PRIO.find(f => p.flags.includes(f));
  const popupHtml = (pts) => `<b>${esc(pts[0].lok)}</b>${pts[0].kommune ? ` <small>${esc(pts[0].kommune)}</small>` : ""}<br>` +
    pts.map(p => `<a href="${threadHash(p, st.date)}" style="color:${cssVar(COLOR[top(p)])}">${p.antal ? fmtN(p.antal) + " " : ""}${esc(p.art)}</a>`).join("<br>");

  function drawMap() {
    layer.clearLayers();
    markers.clear();
    const pts = visible();
    const byLok = new Map();
    for (const pt of pts) (byLok.get(pt.loknr) || byLok.set(pt.loknr, []).get(pt.loknr)).push(pt);
    for (const [loknr, group] of byLok) {
      group.sort((a, b) => PRIO.indexOf(top(a)) - PRIO.indexOf(top(b)));
      const k = top(group[0]);
      const m = L.circleMarker([group[0].lat, group[0].lon], {
        radius: Math.min(5 + 2 * Math.sqrt(group.length) + (k === "SU" ? 3 : 0), 16), color: "#fff", weight: 1.5,
        fillColor: cssVar(COLOR[k]), fillOpacity: .9 })
        .bindTooltip(group.length > 1 ? `${esc(group[0].art)} + ${group.length - 1} · ${esc(group[0].lok)}` : `${esc(group[0].art)} · ${esc(group[0].lok)}`)
        .bindPopup(popupHtml(group), { maxHeight: 260 })
        .addTo(layer);
      if (k === "SU") m.bringToFront();
      markers.set(loknr, m);
    }
    drawList(pts);
    infoEl.textContent = st.data ? `${fmtN(pts.length)} tråde på ${fmtN(byLok.size)} lokaliteter · ${fmtDate(st.date)}` : "";
  }
  function drawList(pts) {
    if (!pts.length) { listEl.replaceChildren(empty("Ingen fund med disse filtre", st.date === today ? "Kortet opdateres, når nye observationer kommer ind." : "")); return; }
    const sorted = [...pts].sort((a, b) => PRIO.indexOf(top(a)) - PRIO.indexOf(top(b)) || b.last_seen.localeCompare(a.last_seen));
    listEl.replaceChildren(...sorted.slice(0, 300).map(pt => h("a", { class: `pulse-item ${pt.kategori}`, href: threadHash(pt, st.date),
      style: { borderLeftColor: cssVar(COLOR[top(pt)]) },
      onmouseenter: () => { const m = markers.get(pt.loknr); if (m) { map.panTo(m.getLatLng(), { animate: true }); m.openTooltip(); } },
      onmouseleave: () => markers.get(pt.loknr)?.closeTooltip() },
      h("span", { class: "muted num" }, pt.antal ? fmtN(pt.antal) : ""),
      h("span", {}, h("b", {}, pt.art), ...pt.flags.filter(f => f !== "pheno").map(f => [" ", tag(f, KAT[f])]),
        pt.flags.includes("pheno") ? [" ", h("span", { class: "tag", title: pt.pheno?.fra ? `Normalt ${doyLabel(pt.pheno.fra)} – ${doyLabel(pt.pheno.til)}` : "" }, "Fænologi")] : null,
        h("small", { class: "muted", style: { display: "block" } }, pt.lok + (pt.kommune ? ` · ${pt.kommune}` : ""))))));
    if (sorted.length > 300) listEl.append(h("p", { class: "muted" }, `+ ${fmtN(sorted.length - 300)} flere på kortet`));
  }

  // ------------------------------------------------ hent dagen
  let seq = 0;
  async function load({ quiet = false } = {}) {
    const my = ++seq;
    if (!quiet) { listEl.replaceChildren(h("div", { class: "skeleton", style: { height: "240px" } })); infoEl.textContent = "Henter …"; }
    try {
      const d = await api(`/api/daymap?date=${st.date}`);
      if (my !== seq) return;
      st.data = d;
      drawChips();
      drawMap();
    } catch (err) { if (my === seq && !quiet) { listEl.replaceChildren(errorBox(err)); infoEl.textContent = ""; } }
  }
  function go(day) {
    if (!day || day > today || day === st.date) return;
    st.date = day; dateInput.value = day; setParams({ tab: "dag", date: day === today ? "" : day });
    drawDays(); load();
  }

  drawDays();
  drawChips();
  await load();
  // I dag: hent igen, når der kommer nye observationer (højst hvert andet minut – serverens cache gælder alligevel så længe)
  let last = Date.now();
  const off = live.on("feed", (d) => {
    if (st.date !== todayISO() || !(d.new || d.changed || d.portal) || Date.now() - last < 120000) return;
    last = Date.now(); load({ quiet: true });
  });
  return () => { off(); map.remove(); };
}
