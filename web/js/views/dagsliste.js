// Dagslisten: dagens observationer som en traditionel liste, med de samme filtre som DOFbasens observationsside –
// visning, dato (med dag frem/tilbage), sortering på art eller lokalitet, summering (tur/dato/ingen) og lokalafdeling.
import { api, state, h, fmtN, fmtDate, todayISO, errorBox, empty, busyBlock, params, setParams, store, toast } from "../core.js";
import { artName, obsRow, head, sheet, initPopup, timeLegend } from "../obslist.js";

const VISNING = [["fugle", "Fugle – bemærkelsesværdige"], ["allfugle", "Fugle – alle"], ["sufugle", "Fugle – sjældne"], ["trkfugle", "Fugle – trækkende"],
  ["patdyr", "Pattedyr"], ["somfugle", "Sommerfugle"], ["krybdyr", "Padder/krybdyr"]];
const SORT = [["art", "Art"], ["lokalitet", "Lokalitet"]];
const SUM = [["tur", "Summér pr. tur"], ["dato", "Summér pr. lokalitet"], ["ingen", "Ingen summering"]];
// Matrixen: afdeling × niveau. Hver markeret celle viser den afdelings observationer på det niveau (som filtrene i Mit overblik).
const LEVELS = [["SU", "SU"], ["SUB", "SUB"], ["bemaerk", "Bemærk"], ["all", "Alle"], ["pheno", "Fænologi"], ["local", "Lokale hits"]];
// Hvert felt rummer kun sig selv: SUB er kun SUB, ikke SU og SUB
const LEVEL_HINT = { SU: "Kun SU-arter", SUB: "Kun SUB-arter", bemaerk: "Kun bemærkelsesværdige (uden SU og SUB)", all: "Almindelige observationer (uden SU, SUB og bemærk)",
  pheno: "Arter uden for deres normale periode", local: "Arter med få fund på lokaliteten" };
/** Hører observationen (en række fra serveren) til modulet? Samme regler som serveren (services/dofbasen.py). */
const IN_LEVEL = { SU: r => r.kat === "SU", SUB: r => r.kat === "SUB", bemaerk: r => r.kat === "bemaerk", all: r => !r.kat, pheno: r => !!r.pheno, local: r => !!r.local };
const parseMatrix = (raw) => {
  const m = {};
  for (const part of String(raw || "").split(",")) {
    const [afd, lv = ""] = part.split(":");
    const levels = lv.split(".").filter(x => LEVELS.some(l => l[0] === x));
    if (/^\d+$/.test(afd) && levels.length) m[afd] = levels;
  }
  return m;
};
const matrixStr = (m) => Object.entries(m).filter(([, v]) => v.length).map(([a, v]) => `${a}:${v.join(".")}`).join(",");
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
    matrix: parseMatrix(q.matrix),
  };
  // Klassisk (DOFbasens egne filtre) eller Avanceret (matrix); en matrix i adressen/standarden åbner Avanceret
  st.mode = q.mode === "klassisk" ? "klassisk" : q.mode === "avanceret" || Object.keys(st.matrix).length ? "avanceret" : "klassisk";
  const view_ = () => ({ visning: st.visning, sort: st.sort, summering: st.summering, lok: st.lok, mode: st.mode, matrix: matrixStr(st.matrix) });
  const isDefault = () => JSON.stringify(view_()) === JSON.stringify({ visning: "fugle", sort: "art", summering: "tur", lok: "", mode: "klassisk", matrix: "", ...saved });
  // Rækkefølgen af modulerne (kolonnerne) vælger brugeren selv – huskes pr. bruger på enheden
  const ORDER_KEY = `${KEY}:rakkefolge`;
  let order = (() => {
    const keys = LEVELS.map(l => l[0]), got = (store(ORDER_KEY) || []).filter(k => keys.includes(k));
    return [...new Set([...got, ...keys])];
  })();
  const levels = () => order.map(k => LEVELS.find(l => l[0] === k));
  const advanced = () => st.mode === "avanceret";
  const hasMatrix = () => advanced() && !!matrixStr(st.matrix);
  let afd = [];
  try { afd = await api("/api/dagsliste/afdelinger"); } catch (err) { view.append(errorBox(err)); return; }

  const out = h("div", {});
  const filters = h("div", { class: "dl-filters" });
  const matrixEl = h("div", { class: "dl-matrix" });
  // Filtrene kan klappes sammen (huskes); overskriften opsummerer, hvad der er valgt
  const OPEN_KEY = `${KEY}:filter-aaben`;
  const box = h("details", { class: "card dl-box" });
  box.open = store(OPEN_KEY) !== false;
  box.addEventListener("toggle", () => store(OPEN_KEY, box.open));
  const boxTitle = h("summary", {});
  let seq = 0;

  const go = (patch = {}) => {
    Object.assign(st, patch);
    setParams({ visning: st.visning, dato: st.dato, sort: st.sort, summering: st.summering, lok: st.lok, lokalitet: st.lokalitet, mode: st.mode, matrix: matrixStr(st.matrix) });
    drawFilters();
    load();
  };

  function toggleCells(afdIds, levels) {
    const all = afdIds.every(a => levels.every(l => st.matrix[a]?.includes(l)));  // alle markeret → fjern, ellers markér alle
    for (const a of afdIds) {
      const cur = new Set(st.matrix[a] || []);
      for (const l of levels) all ? cur.delete(l) : cur.add(l);
      if (cur.size) st.matrix[a] = order.filter(x => cur.has(x)); else delete st.matrix[a];
    }
    go();
  }

  const PRESETS = [
    ["Bemærk og derover", (ids) => Object.fromEntries(ids.map(a => [a, ["SU", "SUB", "bemaerk"]]))],
    ["SU og SUB", (ids) => Object.fromEntries(ids.map(a => [a, ["SU", "SUB"]]))],
    ["Fænologi og lokale hits", (ids) => Object.fromEntries(ids.map(a => [a, ["pheno", "local"]]))],
  ];

  function drawMatrix() {
    matrixEl.hidden = !advanced();
    if (!advanced()) return;
    const ids = afd.map(a => String(a.id));
    const levelKeys = [...order];
    const n = Object.values(st.matrix).reduce((s, v) => s + v.length, 0);
    const cell = (a, l) => {
      const on = !!st.matrix[a]?.includes(l);
      return h("td", {}, h("button", { type: "button", class: `mx-cell mx-${l}${on ? " on" : ""}`, "aria-pressed": String(on),
        title: `${afd.find(x => String(x.id) === a)?.navn} · ${LEVEL_HINT[l]}`, onclick: () => toggleCells([a], [l]) }, on ? "✓" : ""));
    };
    matrixEl.replaceChildren(...[
      h("div", { class: "row between", style: { alignItems: "flex-start" } },
        h("div", {}, h("b", {}, "Vælg pr. lokalafdeling"),
          h("p", { class: "muted small", style: { margin: "2px 0 0" } }, "Markér, hvad du vil se fra hver afdeling. Klik på en overskrift for at markere hele kolonnen eller rækken.")),
        n ? h("button", { class: "btn sm ghost", type: "button", onclick: () => { st.matrix = {}; go(); } }, `Ryd (${n})`) : null),
      h("div", { class: "mx-presets" }, h("span", { class: "muted small" }, "Hurtigvalg:"),
        ...PRESETS.map(([t, fn]) => h("button", { class: "chip", type: "button", onclick: () => { st.matrix = fn(ids); go(); } }, t))),
      h("div", { class: "table-wrap" }, h("table", { class: "dl-mx" },
        h("thead", {}, h("tr", {}, h("th", {}, ""), ...levels().map(([k, t]) => h("th", { "data-key": k },
          h("span", { class: "mx-grip", role: "button", tabindex: "0", title: "Træk for at flytte kolonnen (eller brug piletasterne)", "aria-label": `Flyt ${t}`, html: "⋮⋮" }),
          h("button", { class: `mx-head mx-${k}`, type: "button", title: `${LEVEL_HINT[k]} – alle afdelinger`, onclick: () => toggleCells(ids, [k]) }, t))))),
        h("tbody", {}, ...afd.map(a => {
          const sel = st.matrix[String(a.id)]?.length || 0;
          return h("tr", { class: sel ? "has" : "" },
            h("th", { scope: "row" }, h("button", { class: "mx-row", type: "button", title: "Hele rækken", onclick: () => toggleCells([String(a.id)], levelKeys) }, a.navn)),
            ...levels().map(([k]) => cell(String(a.id), k)));
        }))))].filter(Boolean));
    enableDrag(matrixEl.querySelector("table"));
  }

  /** Flyt kolonner ved at trække i grebet – med pegehændelser, så det virker med mus og på berøringsskærme – eller med piletasterne. */
  function enableDrag(table) {
    if (!table) return;
    const rows = [...table.rows];
    const keys = () => [...table.tHead.rows[0].cells].slice(1).map(c => c.dataset.key);
    const move = (from, to) => {  // kolonne fra → to (0-baseret uden rækkeoverskriften)
      if (from === to) return;
      for (const r of rows) { const cells = [...r.cells]; const el = cells[from + 1], ref = cells[to + 1]; r.insertBefore(el, to > from ? ref.nextSibling : ref); }
    };
    const commit = () => { order = keys(); store(ORDER_KEY, order); drawFilters(); load(); };
    for (const grip of table.querySelectorAll(".mx-grip")) {
      const th = grip.closest("th");
      grip.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        grip.setPointerCapture(e.pointerId);
        table.classList.add("dragging"); th.classList.add("drag");
        const onMove = (ev) => {
          const heads = [...table.tHead.rows[0].cells].slice(1);
          const from = heads.indexOf(th);
          let to = heads.findIndex(c => { const r = c.getBoundingClientRect(); return ev.clientX >= r.left && ev.clientX <= r.right; });
          if (to < 0) to = ev.clientX < heads[0].getBoundingClientRect().left ? 0 : heads.length - 1;
          move(from, to);
        };
        const onEnd = () => {
          grip.removeEventListener("pointermove", onMove); grip.removeEventListener("pointerup", onEnd); grip.removeEventListener("pointercancel", onEnd);
          table.classList.remove("dragging"); th.classList.remove("drag");
          if (keys().join() !== order.join()) commit();
        };
        grip.addEventListener("pointermove", onMove); grip.addEventListener("pointerup", onEnd); grip.addEventListener("pointercancel", onEnd);
      });
      grip.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const i = keys().indexOf(th.dataset.key), j = i + (e.key === "ArrowLeft" ? -1 : 1);
        if (j < 0 || j >= order.length) return;
        move(i, j); order = keys(); store(ORDER_KEY, order); drawFilters(); load();
        matrixEl.querySelector(`th[data-key="${th.dataset.key}"] .mx-grip`)?.focus();
      });
    }
  }

  const modeToggle = () => h("div", { class: "dl-field" }, h("span", { class: "muted small" }, "Filtrering"),
    h("div", { class: "seg", role: "group", "aria-label": "Filtrering" },
      ...[["klassisk", "Klassisk"], ["avanceret", "Avanceret"]].map(([k, t]) =>
        h("button", { type: "button", class: st.mode === k ? "on" : "", "aria-pressed": String(st.mode === k), onclick: () => { if (st.mode !== k) go({ mode: k }); } }, t))));

  const summaryText = () => {
    const wd = fmtDate(st.dato);
    if (advanced()) {
      const n = Object.values(st.matrix).reduce((s, v) => s + v.length, 0);
      return `Avanceret · ${n ? `${n} valgt i matrixen` : "intet valgt"} · ${wd}`;
    }
    return `Klassisk · ${VISNING.find(v => v[0] === st.visning)?.[1] || ""} · ${wd}${st.lok ? ` · ${afd.find(a => String(a.id) === String(st.lok))?.navn || ""}` : ""}`;
  };

  function drawFilters() {
    boxTitle.replaceChildren(h("b", {}, "Filtre"), h("span", { class: "muted small dl-box-sum" }, summaryText()));
    const isToday = st.dato >= todayISO();
    const date = h("input", { type: "date", value: st.dato, max: todayISO(), "aria-label": "Dato", onchange: () => { if (ISO.test(date.value)) go({ dato: date.value }); } });
    drawMatrix();
    const mx = advanced();
    filters.replaceChildren(...[
      modeToggle(),
      mx ? null : select("Observationer", VISNING, st.visning, v => go({ visning: v })),
      h("div", { class: "dl-field dl-date" }, h("span", { class: "muted small" }, "Dato"), h("div", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } },
        h("button", { class: "btn sm", type: "button", title: "Dagen før", onclick: () => go({ dato: addDays(st.dato, -1) }) }, "‹"), date,
        h("button", { class: "btn sm", type: "button", title: "Dagen efter", disabled: isToday, onclick: () => go({ dato: addDays(st.dato, 1) }) }, "›"),
        isToday ? null : h("button", { class: "btn sm", type: "button", onclick: () => go({ dato: todayISO() }) }, "I dag"))),
      select("Sortering", SORT, st.sort, v => go({ sort: v })),
      select("Summering", SUM, st.summering, v => go({ summering: v })),
      mx ? null : select("Afdeling", [["", "Hele landet"], ...afd.map(a => [a.id, a.navn])], st.lok, v => go({ lok: v })),
      h("div", { class: "dl-field dl-save" }, h("span", { class: "muted small" }, "Standard"),
        h("div", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } },
          h("button", { class: "btn sm", type: "button", disabled: isDefault(), title: "Brug denne visning, når du åbner Observationer", onclick: () => { saved = view_(); store(KEY, saved); toast("Gemt som standardvisning"); drawFilters(); } }, isDefault() && Object.keys(saved).length ? "✓ Standard" : "Gem som standard"),
          Object.keys(saved).length ? h("button", { class: "btn sm", type: "button", title: "Fjern din standardvisning", onclick: () => { saved = {}; localStorage.removeItem(KEY); toast("Standardvisningen er fjernet"); drawFilters(); } }, "Nulstil") : null)),
      st.lokalitet ? h("div", { class: "dl-field" }, h("span", { class: "muted small" }, "Lokalitet"),
        h("button", { class: "btn sm", type: "button", onclick: () => go({ lokalitet: "" }) }, "Kun én lokalitet ✕")) : null].filter(Boolean));
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

  /** Avanceret: afdelingens observationer delt i de valgte moduler i brugerens rækkefølge (hver observation under det første modul, den hører til). */
  function modules(afdId, rows) {
    const chosen = order.filter(k => st.matrix[String(afdId)]?.includes(k));
    const buckets = new Map(chosen.map(k => [k, []]));
    for (const r of rows) { const k = chosen.find(k => IN_LEVEL[k](r)); if (k) buckets.get(k).push(r); }
    return chosen.flatMap(k => buckets.get(k).length ? [h("tr", { class: "dl-mod" }, h("td", { colspan: 9 },
      h("span", { class: `mx-dot mx-${k}` }), " ", h("b", {}, LEVELS.find(l => l[0] === k)[1]), h("span", { class: "muted" }, ` · ${buckets.get(k).length}`))), ...groups(buckets.get(k), st.dato)] : []);
  }

  async function load() {
    const my = ++seq;
    if (advanced() && !hasMatrix()) { out.replaceChildren(empty("Vælg i matrixen", "Markér, hvad du vil se fra hver lokalafdeling – fx SU, SUB og Bemærk i alle afdelinger.")); return; }
    out.replaceChildren(busyBlock("Henter observationer …", 5));
    let d;
    try {
      d = await api(`/api/dagsliste?${new URLSearchParams({ visning: st.visning, dato: st.dato, summering: st.summering, ...(hasMatrix() ? { matrix: matrixStr(st.matrix) } : {}), ...(st.lok ? { lok: st.lok } : {}), ...(st.lokalitet ? { lokalitet: st.lokalitet } : {}) })}`);
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
      d.rows.length ? timeLegend() : null,
      d.rows.length ? sheet([...byAfd].flatMap(([id, rows]) => [head([afdName[id] || "Uden afdeling"], "dl-afd"), ...(hasMatrix() ? modules(id, rows) : groups(rows, st.dato))])) : null);
  }

  view.append(h("h1", {}, "Observationer"), h("p", { class: "muted dl-intro" }, "Dagens observationer som en klassisk liste. Klik på antallet for at se observationen og resten af turlisten."), box, out);
  box.append(boxTitle, filters, matrixEl);
  drawFilters();
  initPopup(out);
  load();
}
