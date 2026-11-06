// Dagslisten: dagens observationer som en traditionel liste, med de samme filtre som DOFbasens observationsside –
// visning, dato (med dag frem/tilbage), sortering på art eller lokalitet, summering (tur/dato/ingen) og lokalafdeling.
import { api, state, h, fmtN, fmtDate, todayISO, errorBox, empty, busyBlock, params, setParams, tag } from "../core.js";

const VISNING = [["fugle", "Fugle – bemærkelsesværdige"], ["allfugle", "Fugle – alle"], ["sufugle", "Fugle – sjældne"], ["trkfugle", "Fugle – trækkende"],
  ["patdyr", "Pattedyr"], ["somfugle", "Sommerfugle"], ["krybdyr", "Padder/krybdyr"]];
const SORT = [["art", "Art"], ["lokalitet", "Lokalitet"]];
const SUM = [["tur", "Summér pr. tur"], ["dato", "Summér pr. dato"], ["ingen", "Ingen summering"]];
const ISO = /^\d{4}-\d{2}-\d{2}$/;

const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const pick = (v, list, def) => list.some(x => x[0] === v) ? v : def;
const sexSign = (k) => !k ? "" : k.includes("M") && k.includes("F") ? " ♂♀" : k.includes("M") ? " ♂" : k.includes("F") ? " ♀" : "";
const WEEKDAY = ["søndag", "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag"];

function select(label, options, value, onChange) {
  const sel = h("select", { "aria-label": label, onchange: () => onChange(sel.value) },
    ...options.map(([v, t]) => h("option", { value: v, selected: String(v) === String(value) }, t)));
  return h("label", { class: "dl-field" }, h("span", { class: "muted small" }, label), sel);
}

export async function render(view) {
  if (!state.user?.dagsliste) {
    view.append(h("div", { class: "notice" }, "Dagslisten er ikke slået til.",
      state.user?.role === "superadmin" ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå den til under Admin → Funktioner")] : null));
    return;
  }
  const q = params();
  const st = {
    visning: pick(q.visning, VISNING, "fugle"), dato: ISO.test(q.dato || "") && q.dato <= todayISO() ? q.dato : todayISO(),
    sort: pick(q.sort, SORT, "art"), summering: pick(q.summering, SUM, "tur"), lok: /^\d+$/.test(q.lok || "") ? q.lok : "", lokalitet: /^\d+$/.test(q.lokalitet || "") ? q.lokalitet : "",
  };
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
      h("div", { class: "dl-field" }, h("span", { class: "muted small" }, "Dato"), h("div", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } },
        h("button", { class: "btn sm", type: "button", title: "Dagen før", onclick: () => go({ dato: addDays(st.dato, -1) }) }, "‹"), date,
        h("button", { class: "btn sm", type: "button", title: "Dagen efter", disabled: isToday, onclick: () => go({ dato: addDays(st.dato, 1) }) }, "›"),
        isToday ? null : h("button", { class: "btn sm", type: "button", onclick: () => go({ dato: todayISO() }) }, "I dag"))),
      select("Sortering", SORT, st.sort, v => go({ sort: v })),
      select("Summering", SUM, st.summering, v => go({ summering: v })),
      select("Afdeling", [["", "Hele landet"], ...afd.map(a => [a.id, a.navn])], st.lok, v => go({ lok: v })),
      st.lokalitet ? h("div", { class: "dl-field" }, h("span", { class: "muted small" }, "Lokalitet"),
        h("button", { class: "btn sm", type: "button", onclick: () => go({ lokalitet: "" }) }, "Kun én lokalitet ✕")) : null);
  }

  const row = (r, dato, showArt) => h("tr", {},
    showArt ? h("td", {}, h("a", { href: `#/art/${r.artnr}` }, r.art), r.kat && r.kat !== "alm" ? [" ", tag(r.kat)] : null) : null,
    h("td", { class: "num" }, h("a", { href: `#/traad/${dato}/${r.artnr}/${r.loknr}`, title: r.n_obs > 1 ? `Summeret af ${r.n_obs} observationer` : "Se observationen" }, fmtN(r.antal)), sexSign(r.koen),
      r.n_obs > 1 ? h("span", { class: "muted small", title: `Summeret af ${r.n_obs} observationer` }, ` Σ${r.n_obs}`) : null),
    h("td", {}, r.alder || ""), h("td", {}, r.adfkode || ""), h("td", {}, r.dragt || ""),
    showArt ? null : h("td", {}, h("a", { href: `#/lok/${r.loknr}` }, r.lokalitet || String(r.loknr))),
    h("td", {}, r.tid ? h("span", { title: "Tidspunkt angivet" }, "🕓 ") : null, r.position ? h("span", { title: "Fuglens position" }, "📍 ") : null,
      r.kommentar || r.turkommentar ? h("span", { title: "Kommentar" }, "💬") : null),
    h("td", {}, h("a", { href: `#/observator/${r.obserkode}` }, r.observator || r.obserkode)));

  function table(rows, dato, byLoc) {
    const head = h("thead", {}, h("tr", {}, byLoc ? h("th", {}, "Art") : null, h("th", { class: "num" }, "Antal"), h("th", {}, "Alder"), h("th", {}, "Adfærd"), h("th", {}, "Dragt"),
      byLoc ? null : h("th", {}, "Lokalitet"), h("th", {}, ""), h("th", {}, "Observatør")));
    return h("div", { class: "table-wrap" }, h("table", { class: "data dl-table" }, head, h("tbody", {}, ...rows.map(r => row(r, dato, byLoc)))));
  }

  function groups(rows, dato) {
    const out = [];
    if (st.sort === "art") {
      const by = new Map();
      for (const r of rows) { if (!by.has(r.artnr)) by.set(r.artnr, []); by.get(r.artnr).push(r); }
      for (const [, list] of by) {
        const n = list.reduce((s, r) => s + (r.antal || 0), 0);
        out.push(h("section", { class: "dl-group" }, h("h3", {}, h("a", { href: `#/art/${list[0].artnr}` }, list[0].art), " ", h("i", { class: "muted small" }, list[0].latin || ""),
          list[0].kat && list[0].kat !== "alm" ? [" ", tag(list[0].kat)] : null, h("span", { class: "muted small" }, ` · ${fmtN(n)} ind.`)), table(list, dato, false)));
      }
    } else {
      const by = new Map();
      for (const r of rows) { if (!by.has(r.loknr)) by.set(r.loknr, []); by.get(r.loknr).push(r); }
      for (const [loknr, list] of [...by].sort((a, b) => (a[1][0].lokalitet || "").localeCompare(b[1][0].lokalitet || "", "da"))) {
        out.push(h("section", { class: "dl-group" }, h("h3", {}, h("a", { href: `#/lok/${loknr}` }, list[0].lokalitet || String(loknr)),
          h("span", { class: "muted small" }, ` · ${list.length} ${list.length === 1 ? "art" : "poster"}`)), table(list, dato, true)));
      }
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
      ...[...byAfd].flatMap(([id, rows]) => [byAfd.size > 1 ? h("h2", { class: "dl-afd" }, afdName[id] || "Uden afdeling") : null, ...groups(rows, st.dato)]));
  }

  view.append(h("h1", {}, "Dagsliste"), h("p", { class: "muted" }, "Dagens observationer som en klassisk liste. Klik på antallet for at se observationen og dens tråd."), filters, out);
  drawFilters();
  load();
}
