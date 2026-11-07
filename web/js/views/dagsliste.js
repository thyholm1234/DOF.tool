// Dagslisten: dagens observationer som en traditionel liste, med de samme filtre som DOFbasens observationsside –
// visning, dato (med dag frem/tilbage), sortering på art eller lokalitet, summering (tur/dato/ingen) og lokalafdeling.
import { api, state, h, fmtN, fmtDate, todayISO, errorBox, empty, busyBlock, params, setParams, store, toast } from "../core.js";
import { artName, obsRow, head, sheet, initPopup } from "../obslist.js";

const VISNING = [["fugle", "Fugle – bemærkelsesværdige"], ["allfugle", "Fugle – alle"], ["sufugle", "Fugle – sjældne"], ["trkfugle", "Fugle – trækkende"],
  ["patdyr", "Pattedyr"], ["somfugle", "Sommerfugle"], ["krybdyr", "Padder/krybdyr"]];
const SORT = [["art", "Art"], ["lokalitet", "Lokalitet"]];
const SUM = [["tur", "Summér pr. tur"], ["dato", "Summér pr. lokalitet"], ["ingen", "Ingen summering"]];
const ISO = /^\d{4}-\d{2}-\d{2}$/;

const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const pick = (v, list, def) => list.some(x => x[0] === v) ? v : def;
const WEEKDAY = ["søndag", "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag"];

function select(label, options, value, onChange) {
  const sel = h("select", { "aria-label": label, onchange: () => onChange(sel.value) },
    ...options.map(([v, t]) => h("option", { value: v, selected: String(v) === String(value) }, t)));
  return h("label", { class: "dl-field" }, h("span", { class: "muted small" }, label), sel);
}

export async function render(view) {
  if (!state.user?.dagsliste) {
    view.append(h("div", { class: "notice" }, "Observationer er ikke slået til.",
      state.user?.role === "superadmin" ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå den til under Admin → Funktioner")] : null));
    return;
  }
  // Gemt standardvisning (visning, sortering, summering, afdeling – ikke datoen) gælder, medmindre adressen siger noget andet
  const KEY = `dagsliste:${state.user.id ?? state.user.obserkode ?? ""}`;
  let saved = store(KEY) || {};
  const q = { ...saved, ...params() };
  const st = {
    visning: pick(q.visning, VISNING, "fugle"), dato: ISO.test(q.dato || "") && q.dato <= todayISO() ? q.dato : todayISO(),
    sort: pick(q.sort, SORT, "art"), summering: pick(q.summering, SUM, "tur"), lok: /^\d+$/.test(q.lok || "") ? q.lok : "", lokalitet: /^\d+$/.test(q.lokalitet || "") ? q.lokalitet : "",
  };
  const view_ = () => ({ visning: st.visning, sort: st.sort, summering: st.summering, lok: st.lok });
  const isDefault = () => JSON.stringify(view_()) === JSON.stringify({ visning: "fugle", sort: "art", summering: "tur", lok: "", ...saved });
  let afd = [];
  try { afd = await api("/api/dagsliste/afdelinger"); } catch (err) { view.append(errorBox(err)); return; }

  const out = h("div", {});
  const filters = h("div", { class: "dl-filters" });
  let seq = 0;

  const go = (patch = {}) => {
    Object.assign(st, patch);
    setParams({ visning: st.visning, dato: st.dato, sort: st.sort, summering: st.summering, lok: st.lok, lokalitet: st.lokalitet });
    drawFilters();
    load();
  };

  function drawFilters() {
    const isToday = st.dato >= todayISO();
    const date = h("input", { type: "date", value: st.dato, max: todayISO(), "aria-label": "Dato", onchange: () => { if (ISO.test(date.value)) go({ dato: date.value }); } });
    filters.replaceChildren(
      select("Observationer", VISNING, st.visning, v => go({ visning: v })),
      h("div", { class: "dl-field dl-date" }, h("span", { class: "muted small" }, "Dato"), h("div", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } },
        h("button", { class: "btn sm", type: "button", title: "Dagen før", onclick: () => go({ dato: addDays(st.dato, -1) }) }, "‹"), date,
        h("button", { class: "btn sm", type: "button", title: "Dagen efter", disabled: isToday, onclick: () => go({ dato: addDays(st.dato, 1) }) }, "›"),
        isToday ? null : h("button", { class: "btn sm", type: "button", onclick: () => go({ dato: todayISO() }) }, "I dag"))),
      select("Sortering", SORT, st.sort, v => go({ sort: v })),
      select("Summering", SUM, st.summering, v => go({ summering: v })),
      select("Afdeling", [["", "Hele landet"], ...afd.map(a => [a.id, a.navn])], st.lok, v => go({ lok: v })),
      h("div", { class: "dl-field dl-save" }, h("span", { class: "muted small" }, "Standard"),
        h("div", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } },
          h("button", { class: "btn sm", type: "button", disabled: isDefault(), title: "Brug denne visning, når du åbner Observationer", onclick: () => { saved = view_(); store(KEY, saved); toast("Gemt som standardvisning"); drawFilters(); } }, isDefault() && Object.keys(saved).length ? "✓ Standard" : "Gem som standard"),
          Object.keys(saved).length ? h("button", { class: "btn sm", type: "button", title: "Fjern din standardvisning", onclick: () => { saved = {}; localStorage.removeItem(KEY); toast("Standardvisningen er fjernet"); drawFilters(); } }, "Nulstil") : null)),
      st.lokalitet ? h("div", { class: "dl-field" }, h("span", { class: "muted small" }, "Lokalitet"),
        h("button", { class: "btn sm", type: "button", onclick: () => go({ lokalitet: "" }) }, "Kun én lokalitet ✕")) : null);
  }

  function groups(rows, dato) {
    const out = [];
    const by = new Map();
    const key = st.sort === "art" ? (r) => r.artnr : (r) => r.loknr;
    for (const r of rows) { if (!by.has(key(r))) by.set(key(r), []); by.get(key(r)).push(r); }
    let list = [...by.values()];
    if (st.sort !== "art") list = list.sort((a, b) => (a[0].lokalitet || "").localeCompare(b[0].lokalitet || "", "da"));
    for (const g of list) {
      const f = g[0];
      out.push(st.sort === "art"
        ? head([h("b", {}, artName(f.artnr, f.art, ["SU", "SUB"].find(k => g.some(r => r.kat === k)))), " ", h("span", { class: "muted" }, "(", h("i", {}, f.latin || ""), ")"),
          ":"])
        : head([h("a", { href: `#/lok/${f.loknr}` }, h("b", {}, f.lokalitet || String(f.loknr))), ":"]));
      out.push(...g.map(r => obsRow(r, st.sort !== "art" ? "art" : "lok")));
    }
    return out;
  }

  async function load() {
    const my = ++seq;
    out.replaceChildren(busyBlock("Henter observationer …", 5));
    let d;
    try {
      d = await api(`/api/dagsliste?${new URLSearchParams({ visning: st.visning, dato: st.dato, summering: st.summering, ...(st.lok ? { lok: st.lok } : {}), ...(st.lokalitet ? { lokalitet: st.lokalitet } : {}) })}`);
    } catch (err) { if (my === seq) out.replaceChildren(errorBox(err)); return; }
    if (my !== seq) return;
    const n = d.rows.reduce((s, r) => s + r.n_obs, 0);
    const wd = WEEKDAY[new Date(st.dato + "T12:00:00").getDay()];
    const afdName = Object.fromEntries(afd.map(a => [a.id, a.navn]));
    const byAfd = new Map();
    for (const r of d.rows) { const k = r.afdeling_id ?? 0; if (!byAfd.has(k)) byAfd.set(k, []); byAfd.get(k).push(r); }
    out.replaceChildren(h("p", { class: "muted" }, `${wd[0].toUpperCase()}${wd.slice(1)} ${fmtDate(st.dato)} · ${fmtN(n)} observationer${d.rows.length >= 10000 ? " (første 10.000 poster)" : ""}`),
      d.note ? h("div", { class: "notice" }, d.note) : null,
      !d.rows.length && !d.note ? empty("Ingen observationer", "Prøv en anden visning, dato eller afdeling") : null,
      d.rows.length ? sheet([...byAfd].flatMap(([id, rows]) => [head([afdName[id] || "Uden afdeling"], "dl-afd"), ...groups(rows, st.dato)])) : null);
  }

  view.append(h("h1", {}, "Observationer"), h("p", { class: "muted dl-intro" }, "Dagens observationer som en klassisk liste. Klik på antallet for at se observationen og resten af turlisten."), filters, out);
  drawFilters();
  initPopup(out);
  load();
}
