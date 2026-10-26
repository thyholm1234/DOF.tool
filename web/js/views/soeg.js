// Søg i observationerne: samme felter som DOFbasen-modulets søgning (services/dofbasen.py). Resultatet vises med et kort
// over alle fund øverst og en tabel efter dato, der kan sorteres på alle kolonner (på serveren, så det gælder hele
// resultatet), og kan hentes som CSV, ODS eller XLSX.
import { api, state, h, fmtN, fmtDate, tag, toast, errorBox, empty, busyBlock, params, setParams } from "../core.js";
import { baseMap, katColor } from "../maps.js";

const DEFAULTS = { periode: "antaldage", dage: 7, species: "alle", omraade: "none", artdata: "keine", antalregl: "", sort: "dato", desc: true };
const BOOLS = ["medobser", "koordinat", "kommentar", "udelad_burfugle", "udelad_hybrider_ubestemte", "vis_underarter_som_arter", "summering", "desc"];
const NUMS = ["dage", "aar", "maaned", "dag", "lok", "lokal", "iba", "stor", "art", "fra_art", "til_art", "antal", "adf", "metode", "biggestobs"];
const MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];
const PAGE = 100;
// [sorteringsnøgle, overskrift]
const COLS = [["dato", "Dato"], ["art", "Art"], ["antal", "Antal"], ["koen", "Køn"], ["adf", "Adfærd"], ["alder", "Alder"], ["dragt", "Dragt"],
  ["lokalitet", "Lokalitet"], ["afdeling", "Afdeling"], ["observator", "Observatør"], ["tid", "Tid"], ["kommentar", "Kommentar"]];

function fromQuery(q) {
  const f = { ...DEFAULTS };
  for (const [k, v] of Object.entries(q)) {
    if (v === "" || k === "soeg" || k === "side") continue;
    f[k] = BOOLS.includes(k) ? v === "1" : NUMS.includes(k) ? +v : v;
  }
  return f;
}

function toQuery(f) {
  const q = {};
  for (const [k, v] of Object.entries(f)) {
    if (v === "" || v == null || k.endsWith("_navn") || DEFAULTS[k] === v || (v === false && !(k in DEFAULTS))) continue;
    q[k] = v === true ? "1" : v === false ? "0" : String(v);
  }
  return q;
}

/** Fritekstfelt med forslag fra serveren (datalist); onPick får det valgte element eller null. */
function suggest(url, { value = "", placeholder, label, onPick }) {
  const list = h("datalist", { id: `sg-${Math.random().toString(36).slice(2)}` });
  const items = new Map();
  let timer;
  const inp = h("input", { type: "search", value, placeholder, list: list.id, autocomplete: "off",
    oninput: () => {
      const hit = items.get(inp.value);
      if (hit) { onPick(hit); return; }
      onPick(null);
      clearTimeout(timer);
      if (inp.value.trim().length < 2) return;
      timer = setTimeout(async () => {
        try {
          const rows = await api(url + encodeURIComponent(inp.value.trim()));
          items.clear();
          list.replaceChildren(...rows.map(r => { items.set(label(r), r); return h("option", { value: label(r) }); }));
        } catch { /* forslag er en hjælp */ }
      }, 200);
    } });
  return h("span", { class: "grow" }, inp, list);
}

export async function render(view) {
  if (!state.user?.search) {
    view.append(h("div", { class: "notice" }, "Søgesiden er ikke slået til.",
      state.user?.role === "superadmin" ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå den til under Admin → Funktioner")] : null));
    return;
  }
  const q = params();
  const f = fromQuery(q);
  let valg;
  try { valg = await api("/api/search/valg"); } catch (err) { view.append(errorBox(err)); return; }
  const st = { page: +q.side || 1, body: null };
  const mapBox = h("div", {}), resBox = h("div", {});

  // ---- felter
  const set = (k, v) => { f[k] = v === "" || v == null || Number.isNaN(v) ? null : v; };
  const sel = (k, opts, onChange) => h("select", { onchange: (e) => { set(k, NUMS.includes(k) ? +e.target.value || null : e.target.value); onChange?.(); } },
    ...opts.map(([v, t]) => h("option", { value: v, selected: String(f[k] ?? "") === String(v) }, t)));
  const inp = (k, attrs = {}) => h("input", { value: f[k] ?? "", ...attrs, oninput: (e) => set(k, attrs.type === "number" ? (e.target.value === "" ? null : +e.target.value) : e.target.value) });
  const chk = (k, label) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!f[k], onchange: (e) => { f[k] = e.target.checked; } }), label);
  const field = (label, ...ctl) => h("label", { class: "field" }, h("span", {}, label), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, ...ctl));

  const periodBox = h("div", { class: "stack" });
  const drawPeriod = () => {
    const p = f.periode, r = [field("Periode", sel("periode", [["antaldage", "Seneste antal dage"], ["dato", "Dato"], ["mellemdato", "Datointerval"], ["maanedaar", "År/måned/dag"], ["none", "Ingen (alle år)"]], drawPeriod))];
    if (p === "antaldage") r.push(field("Antal dage (1–366)", inp("dage", { type: "number", min: 1, max: 366 })));
    if (p === "dato") r.push(field("Dato", inp("dato", { type: "date" })));
    if (p === "mellemdato") r.push(field("Fra – til", inp("dato_first", { type: "date" }), inp("dato_second", { type: "date" })));
    if (p === "maanedaar") r.push(field("År / måned / dag", inp("aar", { type: "number", min: 1900, max: 2100, placeholder: String(new Date().getFullYear()) }),
      sel("maaned", [["", "–"], ...MONTHS.map((m, i) => [i + 1, m])]), inp("dag", { type: "number", min: 1, max: 31, placeholder: "dag" })));
    periodBox.replaceChildren(...r);
  };
  drawPeriod();

  const areaBox = h("div", { class: "row grow", style: { gap: "6px" } });
  const drawArea = () => {
    const o = f.omraade;
    areaBox.replaceChildren(
      o === "lokalitet" ? suggest("/api/locations?q=", { value: f.lok_navn || (f.lok ? `#${f.lok}` : ""), placeholder: "Lokalitet …",
        label: (r) => r.kommune ? `${r.navn} (${r.kommune})` : r.navn, onPick: (r) => { f.lok = r?.loknr ?? null; f.lok_navn = r?.navn; } })
      : o === "lokal" ? sel("lokal", [["", "Vælg …"], ...valg.afdelinger.map(a => [a.id, a.navn])])
      : o === "kommune" ? sel("kommune", [["", "Vælg …"], ...valg.kommuner.map(k => [k.kode, k.navn])])
      : o === "iba" || o === "stor" ? sel(o, [["", "Vælg …"], ...valg.omraader.filter(a => a.kind === o).map(a => [a.id, a.navn])]) : "");
  };
  drawArea();

  const artBox = h("div", { class: "row grow", style: { gap: "6px" } });
  const drawArt = () => {
    const sp = (k, ph) => suggest("/api/species?q=", { value: f[k + "_navn"] || (f[k] ? `#${f[k]}` : ""), placeholder: ph,
      label: (r) => r.navn, onPick: (r) => { f[k] = r?.artnr ?? null; f[k + "_navn"] = r?.navn; } });
    artBox.replaceChildren(...(f.artdata === "art" ? [sp("art", "Art …")] : f.artdata === "artinterval" ? [sp("fra_art", "Fra art"), sp("til_art", "Til art")] : []));
  };
  drawArt();

  const form = h("form", { class: "card stack", onsubmit: (e) => { e.preventDefault(); f.sort = "dato"; f.desc = true; run(1); } },
    h("div", { class: "grid-3" },
      h("div", { class: "stack" }, periodBox,
        field("Fugle", sel("species", Object.entries(valg.species))),
        field("Område", sel("omraade", [["none", "Hele landet"], ["lokalitet", "Lokalitet"], ["lokal", "Lokalafdeling"], ["kommune", "Kommune"], ["iba", "IBA"], ["stor", "Større område"]], drawArea), areaBox),
        field("Art", sel("artdata", [["keine", "Alle arter"], ["art", "Én art"], ["artinterval", "Artsinterval"]], drawArt), artBox)),
      h("div", { class: "stack" },
        field("Antal", sel("antalregl", [["", "–"], ["stoerre", "≥"], ["mindre", "≤"], ["ligmed", "="]]), inp("antal", { type: "number", min: 0 })),
        field("Adfærd / alder", sel("adf", [["", "Adfærd"], ...Object.entries(valg.adf)]), sel("alder", [["", "Alder"], ...valg.alder.map(a => [a, a])])),
        field("Dragt / køn", sel("dragt", [["", "Dragt"], ...Object.entries(valg.dragt)]), sel("sex", [["", "Køn"], ["M", "Han"], ["F", "Hun"]])),
        field("Metode", sel("metode", [["", "Alle"], ...Object.entries(valg.metode).sort((a, b) => a[1].localeCompare(b[1], "da"))])),
        field("Observatør (obserkode)", inp("obserdata", { placeholder: "fx ABC1", style: { textTransform: "uppercase" } })),
        chk("medobser", "Også som medobservatør")),
      h("div", { class: "stack" },
        field("Tekst i fugle- og turnoter", inp("textsearch", { type: "search" })),
        field("Kun de N største", inp("biggestobs", { type: "number", min: 1, max: 1000 })),
        chk("koordinat", "Kun med fuglens position"), chk("kommentar", "Kun med kommentar"),
        chk("udelad_burfugle", "Udelad burfugle"), chk("udelad_hybrider_ubestemte", "Udelad hybrider og ubestemte"),
        chk("vis_underarter_som_arter", "Vis underarter som arter"), chk("summering", "Summér pr. tur og art"))),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Søg"),
      h("button", { class: "btn ghost", type: "button", onclick: () => { location.hash = "#/soeg"; } }, "Nulstil")));

  const payload = () => Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== null && v !== undefined && v !== "" && !k.endsWith("_navn")));

  const run = async (page = 1, { map = true } = {}) => {
    st.page = page;
    st.body = payload();
    setParams({ ...Object.fromEntries(Object.keys(params()).map(k => [k, ""])), ...toQuery(st.body), soeg: "1", side: page > 1 ? page : "" });
    resBox.replaceChildren(busyBlock("Søger …"));
    if (map) drawMap(st.body);
    try { drawResult(await api("/api/search", { method: "POST", body: { ...st.body, page, limit: PAGE } })); }
    catch (err) { resBox.replaceChildren(errorBox(err)); }
  };

  const sortBy = (key) => {
    if (f.sort === key) f.desc = !f.desc;
    else { f.sort = key; f.desc = key === "dato" || key === "antal"; }
    run(1, { map: false });
  };

  const download = async (fmt) => {
    try {
      const res = await fetch(`/api/search/export/${fmt}`, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" }, body: JSON.stringify(st.body) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fejl ${res.status}`);
      const a = h("a", { href: URL.createObjectURL(await res.blob()), download: `observationer-${new Date().toISOString().slice(0, 10)}.${fmt}` });
      document.body.append(a); a.click(); a.remove();
    } catch (err) { toast(err.message, "err"); }
  };

  const drawResult = (r) => {
    const pages = Math.max(1, Math.ceil(r.total / r.limit));
    const period = r.period[0] ? `${fmtDate(r.period[0])} – ${fmtDate(r.period[1])}` : "alle år";
    const pager = pages > 1 ? h("div", { class: "row", style: { justifyContent: "center" } },
      h("button", { class: "btn sm", disabled: st.page <= 1, onclick: () => run(st.page - 1, { map: false }) }, "‹ Forrige"),
      h("span", { class: "muted" }, `Side ${fmtN(st.page)} af ${fmtN(pages)}`),
      h("button", { class: "btn sm", disabled: st.page >= pages, onclick: () => run(st.page + 1, { map: false }) }, "Næste ›")) : null;
    const th = ([key, label]) => h("th", { style: { cursor: "pointer", userSelect: "none" }, title: "Sortér", "aria-sort": f.sort === key ? (f.desc ? "descending" : "ascending") : null,
      onclick: () => sortBy(key) }, label, f.sort === key ? (f.desc ? " ▼" : " ▲") : "");
    resBox.replaceChildren(
      ...r.notes.map(n => h("div", { class: "notice" }, n)),
      h("div", { class: "row between mt" },
        h("p", { style: { margin: 0 } }, h("b", {}, r.capped ? "Over 50.000" : fmtN(r.total)), ` ${st.body.summering ? "summerede rækker" : "observationer"} · ${period}`),
        h("span", { class: "row", style: { gap: "6px" } }, h("small", { class: "muted" }, "Hent:"),
          ...["csv", "ods", "xlsx"].map(x => h("button", { class: "btn sm", type: "button", onclick: () => download(x), title: "Hele resultatet (højst 50.000 rækker) i den viste sortering" }, x.toUpperCase())))),
      r.rows.length ? h("div", { class: "card table-wrap mt" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, ...COLS.map(th))),
        h("tbody", {}, ...r.rows.map(o => h("tr", {},
          h("td", { class: "nowrap" }, fmtDate(o.dato)),
          h("td", {}, h("a", { href: `#/art/${o.artnr}` }, o.art), o.kategori && o.kategori !== "alm" ? [" ", tag(o.kategori)] : null),
          h("td", { class: "num right" }, fmtN(o.antal), st.body.summering ? h("small", { class: "muted" }, ` (${o.n_obs} obs.)`) : null),
          h("td", {}, o.koen || ""), h("td", { title: o.adfaerd || "" }, o.adfkode || ""), h("td", {}, o.alderkode || ""), h("td", {}, o.dragtkode || ""),
          h("td", {}, o.loknr ? h("a", { href: `#/lok/${o.loknr}` }, o.lokalitet || String(o.loknr)) : ""),
          h("td", { class: "muted" }, (o.afdeling || "").replace("DOF ", "")),
          h("td", {}, o.observator || o.obserkode),
          h("td", { class: "muted nowrap" }, o.obstidfra || o.turtidfra || ""),
          h("td", { class: "muted", style: { fontSize: ".85rem" } }, (o.fuglnoter || "").slice(0, 140))))))) : empty("Ingen observationer matcher søgningen"),
      pager);
  };

  let map;
  const drawMap = async (body) => {
    const el = h("div", { style: { height: "380px", borderRadius: "12px", overflow: "hidden" } });
    const info = h("small", { class: "muted" }, "Henter kort …");
    mapBox.replaceChildren(h("div", { class: "card stack mt" }, el, info));
    map?.remove();
    map = baseMap(el, { zoom: 6 });
    try {
      const r = await api("/api/search/points", { method: "POST", body });
      const bounds = [], total = r.points.reduce((s, p) => s + p.n, 0);
      for (const p of r.points.slice().reverse()) {  // de største øverst
        const color = katColor(p.kategori);
        L.circleMarker([p.lat, p.lon], { radius: Math.min(14, 4 + Math.sqrt(p.n)), color, weight: 1, fillColor: color, fillOpacity: .75 }).addTo(map)
          .bindPopup(h("div", {}, p.loknr ? h("a", { href: `#/lok/${p.loknr}` }, h("b", {}, p.lokalitet || `Lokalitet ${p.loknr}`)) : h("b", {}, "Position"),
            p.praecis ? h("small", { class: "muted" }, " (fuglens position)") : null, h("br"),
            `${fmtN(p.n)} observationer`, h("br"), h("small", {}, p.arter.join(", ") + (p.arter.length >= 6 ? " …" : ""))));
        bounds.push([p.lat, p.lon]);
      }
      if (bounds.length) map.fitBounds(bounds, { maxZoom: 12, padding: [20, 20] });
      info.textContent = `${fmtN(total)} observationer på ${fmtN(r.points.length)} positioner${r.capped ? " (kortet viser de 5.000 positioner med flest observationer)" : ""}. Farven viser den sjældneste kategori (SU, SUB, bemærkelsesværdig).`;
    } catch (err) { info.textContent = `Kortet kunne ikke hentes: ${err.message}`; }
  };

  view.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Søg"), h("p", { class: "lead" }, "Søg i alle observationer – som DOFbasens søgning, med kort og regneark."))),
    form, mapBox, resBox);
  if (q.soeg) run(st.page);
  return () => map?.remove();
}
