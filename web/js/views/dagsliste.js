// Dagslisten: dagens observationer som en traditionel liste, med de samme filtre som DOFbasens observationsside –
// visning, dato (med dag frem/tilbage), sortering på art eller lokalitet, summering (tur/dato/ingen) og lokalafdeling.
import { api, state, h, fmtN, fmtDate, todayISO, errorBox, empty, busyBlock, params, setParams, store, toast, icon, tag } from "../core.js";

/** SU/SUB-mærkat efter artsnavnet (bemærk-kategorien vises ikke). */
const rareTag = (kat) => kat === "SU" || kat === "SUB" ? [" ", tag(kat)] : null;

const VISNING = [["fugle", "Fugle – bemærkelsesværdige"], ["allfugle", "Fugle – alle"], ["sufugle", "Fugle – sjældne"], ["trkfugle", "Fugle – trækkende"],
  ["patdyr", "Pattedyr"], ["somfugle", "Sommerfugle"], ["krybdyr", "Padder/krybdyr"]];
const SORT = [["art", "Art"], ["lokalitet", "Lokalitet"]];
const SUM = [["tur", "Summér pr. tur"], ["dato", "Summér pr. lokalitet"], ["ingen", "Ingen summering"]];
const ISO = /^\d{4}-\d{2}-\d{2}$/;

const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const pick = (v, list, def) => list.some(x => x[0] === v) ? v : def;
const sexSign = (k) => !k ? "" : k.includes("M") && k.includes("F") ? " ♂♀" : k.includes("M") ? " ♂" : k.includes("F") ? " ♀" : "";
// Trækkende: flere retninger (N, S, SØ …) på samme linje samles til ét «T»; øvrige adfærdskoder bevares
const DIR = /^(T|TF|TI|TU|N|NNØ|NØ|ØNØ|Ø|ØSØ|SØ|SSØ|S|SSV|SV|VSV|V|VNV|NV|NNV)( |$)/;
function adf(code) {
  const codes = String(code || "").split(", ").filter(Boolean);
  const dirs = codes.filter(c => DIR.test(c));
  return dirs.length > 1 ? ["T", ...codes.filter(c => !DIR.test(c))].join(", ") : codes.join(", ");
}
const WEEKDAY = ["søndag", "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag"];

function select(label, options, value, onChange) {
  const sel = h("select", { "aria-label": label, onchange: () => onChange(sel.value) },
    ...options.map(([v, t]) => h("option", { value: v, selected: String(v) === String(value) }, t)));
  return h("label", { class: "dl-field" }, h("span", { class: "muted small" }, label), sel);
}

/** Boks ved hover over et ikon: viser noten/forklaringen ved siden af ikonet (data-pop-t = overskrift, data-pop = indhold). */
let pop = null;
function initPopup(root) {
  const hide = () => { pop?.remove(); pop = null; };
  const show = (el) => {
    hide();
    const t = el.dataset.popT, txt = el.dataset.pop;
    pop = h("div", { class: "dl-pop", role: "tooltip" }, h("b", {}, t), txt ? h("div", {}, txt) : null);
    document.body.append(pop);
    const r = el.getBoundingClientRect(), w = pop.offsetWidth, ph = pop.offsetHeight;
    const left = Math.max(8, Math.min(r.left, innerWidth - w - 8));
    pop.style.left = `${left}px`;
    pop.style.top = `${r.bottom + ph + 8 > innerHeight && r.top > ph + 8 ? r.top - ph - 6 : r.bottom + 6}px`;
  };
  root.addEventListener("mouseover", e => { const el = e.target.closest?.("[data-pop-t]"); if (el) show(el); else if (!e.target.closest?.(".dl-pop")) hide(); });
  root.addEventListener("mouseleave", hide);
  root.addEventListener("focusin", e => { const el = e.target.closest?.("[data-pop-t]"); if (el) show(el); });
  root.addEventListener("focusout", hide);
  addEventListener("hashchange", hide, { once: true });
  addEventListener("scroll", hide, { passive: true });
}

export async function render(view) {
  if (!state.user?.dagsliste) {
    view.append(h("div", { class: "notice" }, "Dagslisten er ikke slået til.",
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
          h("button", { class: "btn sm", type: "button", disabled: isDefault(), title: "Brug denne visning, når du åbner Dagslisten", onclick: () => { saved = view_(); store(KEY, saved); toast("Gemt som standardvisning"); drawFilters(); } }, isDefault() && Object.keys(saved).length ? "✓ Standard" : "Gem som standard"),
          Object.keys(saved).length ? h("button", { class: "btn sm", type: "button", title: "Fjern din standardvisning", onclick: () => { saved = {}; localStorage.removeItem(KEY); toast("Standardvisningen er fjernet"); drawFilters(); } }, "Nulstil") : null)),
      st.lokalitet ? h("div", { class: "dl-field" }, h("span", { class: "muted small" }, "Lokalitet"),
        h("button", { class: "btn sm", type: "button", onclick: () => go({ lokalitet: "" }) }, "Kun én lokalitet ✕")) : null);
  }

  // Ét samlet, kompakt skema (som DOFbasens gamle liste): ingen kolonnetitler, faste kolonnebredder hele vejen ned,
  // noteikonerne forrest – billede, lyd, nål og tid står efter antal/alder/adfærd.
  // Ikonerne er links til observationen; ved hover vises indholdet (noten) i en boks, ved klik åbnes observationen.
  const icoLink = (r, dato, cls, ic, title, text) => h("a", { class: `dl-ic ${cls}`, href: `#/traad/${dato}/${r.artnr}/${r.loknr}`, "data-pop-t": title, "data-pop": typeof text === "string" ? text : "",
    "aria-label": typeof text === "string" ? `${title}: ${text}` : title, html: icon(ic) });
  const note = (r, dato, cls, title, text) => text || text === true ? icoLink(r, dato, cls, "chat", title, text) : null;
  const notes = (r, dato) => h("td", { class: "dl-notes" },
    r.n_obs > 1 ? icoLink(r, dato, "dl-sum", "chat", "Sammensat observation", `Summeret af ${r.n_obs} observationer`) : null,
    note(r, dato, "dl-tur", "Turnote", r.turnote || r.turkommentar),
    note(r, dato, "dl-vejr", "Vejrnote", r.vejrnote || r.vejrkommentar),
    note(r, dato, "dl-obs", "Observationsnote", r.obsnote || r.kommentar),
    r.medobser ? icoLink(r, dato, "dl-med", "users", r.medobsnote || "Medobservatører", true) : null);  // feltets indhold (fx «MV») vises i stedet for en generisk tekst
  const extra = (r, dato) => h("td", { class: "dl-extra" },
    r.foto ? icoLink(r, dato, "", "camera", "Foto", true) : null,
    r.lyd ? icoLink(r, dato, "", "sound", "Lydoptagelse", true) : null,
    r.position ? icoLink(r, dato, "", "pin", "Fuglens position", true) : null,
    r.tid ? icoLink(r, dato, "", "now", "Tidspunkt angivet", true) : null);
  const ald = (r) => [r.alder, sexSign(r.koen).trim(), r.dragt].filter(Boolean).join(" ");

  const row = (r, dato, byLoc) => h("tr", {},
    notes(r, dato),
    h("td", { class: "num" }, h("a", { href: `#/traad/${dato}/${r.artnr}/${r.loknr}`, title: r.n_obs > 1 ? `Summeret af ${r.n_obs} observationer` : "Se observationen" }, fmtN(r.antal)),
      ),
    h("td", {}, ald(r)), h("td", {}, adf(r.adfkode)), extra(r, dato),
    h("td", {}, byLoc ? [h("a", { href: `#/art/${r.artnr}` }, r.art), rareTag(r.kat)]
      : h("a", { href: `#/lok/${r.loknr}` }, r.lokalitet || String(r.loknr)),
      h("span", { class: "dl-obs-m" }, obsLink(r))),  // på smalle skærme står observatøren under lokaliteten
    h("td", { class: "dl-obs" }, obsLink(r)));
  const obsLink = (r) => h("a", { href: `#/observator/${r.obserkode}` }, r.observator || r.obserkode);

  const head = (content, cls = "dl-head") => h("tr", { class: cls }, h("td", { colspan: 7 }, ...content));

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
        ? head([h("a", { href: `#/art/${f.artnr}` }, h("b", {}, f.art)), " ", h("span", { class: "muted" }, "(", h("i", {}, f.latin || ""), ")"),
          rareTag(["SU", "SUB"].find(k => g.some(r => r.kat === k))), ":"])
        : head([h("a", { href: `#/lok/${f.loknr}` }, h("b", {}, f.lokalitet || String(f.loknr))), ":"]));
      out.push(...g.map(r => row(r, dato, st.sort !== "art")));
    }
    return out;
  }

  const sheet = (bodyRows) => h("div", { class: "table-wrap" }, h("table", { class: "dl-table" },
    h("colgroup", {}, h("col", { class: "c-notes" }), h("col", { class: "c-num" }), h("col", { class: "c-ald" }), h("col", { class: "c-adf" }),
      h("col", { class: "c-extra" }), h("col", { class: "c-lok" }), h("col", { class: "c-obs" })),
    h("tbody", {}, ...bodyRows)));

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
      d.rows.length ? sheet([...byAfd].flatMap(([id, rows]) => [byAfd.size > 1 ? head([afdName[id] || "Uden afdeling"], "dl-afd") : null, ...groups(rows, st.dato)])) : null);
  }

  view.append(h("h1", {}, "Dagsliste"), h("p", { class: "muted dl-intro" }, "Dagens observationer som en klassisk liste. Klik på antallet for at se observationen og dens tråd."), filters, out);
  drawFilters();
  initPopup(out);
  load();
}
