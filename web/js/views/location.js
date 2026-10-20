import { api, h, fmtN, fmtDate, tag, errorBox, KAT, seg, params, setParams, busyBlock } from "../core.js";

// Filtre over artslisten: højeste kategori arten har haft på lokaliteten, og trækkortets artsgrupper
const KAT_FILTER = [["", "Alle"], ["SU", "SU"], ["SUB", "SUB"], ["bemaerk", "Bemærk"]];
const GROUP_FILTER = [["", "Alle grupper"], ["rov", "Rovfugle"], ["vade", "Vadefugle"], ["gaes", "Gæs"], ["hav", "Havfugle"], ["drossel", "Drosler"], ["spurv", "Spurvefugle"]];
const SORTS = [["dage", "Flest dage"], ["rekord", "Største antal"], ["senest", "Senest"], ["syst", "Systematisk"]];
const inGroup = (s, g) => !g || (g === "rov" ? ["rov_svaev", "rov_aktiv"].includes(s.group) : s.group === g);
import { baseMap } from "../maps.js";

export async function render(view, [loknr]) {
  let d;
  view.replaceChildren(busyBlock("Henter lokaliteten…", 5));
  try { d = await api(`/api/locations/${loknr}`); } catch (err) { view.replaceChildren(errorBox(err)); return; }
  view.replaceChildren();
  d.species = [];  // artslisten og rekorderne er tunge og hentes for sig, så resten af siden kan vises med det samme
  let speciesState = "loading";
  const mapEl = h("div", { class: "map small" });
  let q = "";
  const p = params();
  const f = { kat: p.kat || "", grp: p.grp || "", sort: p.sort || "dage", year: p.y || "" };
  const tbody = h("tbody");
  const count = h("small", { class: "muted" });
  const open = new Set();  // arter med top 10-dage foldet ud
  const records = {};      // artnr → top 10 (hentet ved første klik)
  const recordRow = (s) => {
    const cell = h("td", { colspan: 4, style: { background: "var(--bg-2)" } });
    const draw = (rows) => cell.replaceChildren(
      h("b", {}, `Top 10 dage – ${s.navn}${f.year ? ` (${f.year})` : ""}`), h("small", { class: "muted" }, " · dagens største enkeltobservation"),
      rows.length ? h("ol", { class: "mini-list", style: { marginTop: "6px" } }, ...rows.map((r, i) => h("li", {},
        h("span", {}, h("b", { class: "num muted", style: { display: "inline-block", minWidth: "2.2ch" } }, `${i + 1}.`), " ",
          h("a", { href: `#/traad/${r.dato}/${s.artnr}/${loknr}` }, fmtDate(r.dato)), h("small", { class: "muted" }, ` · ${r.observer}${r.adfkode ? ` · ${r.adfkode}` : ""}`)),
        h("b", { class: "num" }, fmtN(r.antal))))) : h("p", { class: "muted" }, "Ingen tal."));
    if (records[s.artnr]) draw(records[s.artnr]);
    else {
      cell.append(h("small", { class: "muted" }, "Henter…"));
      const year = f.year;
      api(`/api/locations/${loknr}/records/${s.artnr}${year ? `?year=${year}` : ""}`).then(r => { if (year === f.year) { records[s.artnr] = r; draw(r); } }).catch(err => cell.replaceChildren(errorBox(err)));
    }
    return h("tr", {}, cell);
  };
  const drawSpecies = () => {
    setParams({ kat: f.kat, grp: f.grp, sort: f.sort === "dage" ? "" : f.sort, y: f.year });
    const list = d.species.filter(s => (!q || s.navn.toLowerCase().includes(q)) && (!f.kat || s.kategori === f.kat) && inGroup(s, f.grp));
    const by = { dage: (a, b) => b.n_obs - a.n_obs, rekord: (a, b) => b.rekord - a.rekord, senest: (a, b) => String(b.last_date).localeCompare(String(a.last_date)),
      syst: (a, b) => (a.sortering ?? 1e9) - (b.sortering ?? 1e9) }[f.sort];
    list.sort(by);
    if (speciesState !== "ready") { count.textContent = ""; return; }
    count.textContent = `${fmtN(list.length)} ${list.length === 1 ? "art" : "arter"}${f.year ? ` i ${f.year}` : ""} · tryk på en art for dens top 10 dage`;
    if (!list.length) { tbody.replaceChildren(h("tr", {}, h("td", { colspan: 4, class: "muted" }, "Ingen arter matcher filtrene."))); return; }
    tbody.replaceChildren(...list.flatMap(s => {
      const row = h("tr", { style: { cursor: "pointer" }, onclick: () => { open.has(s.artnr) ? open.delete(s.artnr) : open.add(s.artnr); drawSpecies(); } },
        h("td", {}, s.navn, s.kategori !== "alm" ? [" ", tag(s.kategori, KAT[s.kategori])] : null),
        h("td", { class: "right num" }, fmtN(s.n_obs)),
        h("td", { class: "right nowrap", title: "Største antal på én dag" }, h("b", { class: "num" }, fmtN(s.rekord)), h("small", { class: "muted", style: { display: "block" } }, fmtDate(s.rekord_dato))),
        h("td", { class: "nowrap" }, fmtDate(s.last_date)));
      return open.has(s.artnr) ? [row, recordRow(s)] : [row];
    }));
  };
  const wrapSeg = (el) => { el.classList.add("wrap"); return el; };
  const filters = h("div", { style: { display: "flex", flexDirection: "column", gap: "6px", minWidth: 0 } },
    wrapSeg(seg(KAT_FILTER, f.kat, (v) => { f.kat = v; drawSpecies(); })),
    wrapSeg(seg(GROUP_FILTER, f.grp, (v) => { f.grp = v; drawSpecies(); })),
    wrapSeg(seg(SORTS, f.sort, (v) => { f.sort = v; drawSpecies(); })), count);
  const speciesBody = h("div", { class: "lok-body" }, busyBlock("Beregner arter og rekorder for lokaliteten… det kan tage et par sekunder på store lokaliteter", 6));
  view.append(
    h("p", {}, h("a", { href: "#/", onclick: (e) => { if (history.length > 1) { e.preventDefault(); history.back(); } } }, "← Tilbage")),
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, d.navn), h("p", { class: "lead" }, [d.kommune && `${d.kommune} Kommune`, d.afdeling].filter(Boolean).join(" · ")))),
    h("div", { class: "stats", style: { gridTemplateColumns: "repeat(3, 1fr)" } },
      h("div", { class: "stat", title: "Én pr. art pr. dag – flere turlister samme dag tæller ikke dobbelt" }, h("b", {}, fmtN(d.stats.n_obs)), h("span", {}, "observationer")),
      h("div", { class: "stat" }, h("b", {}, fmtN(d.stats.n_taxa)), h("span", {}, "arter/taxa")),
      h("div", { class: "stat" }, h("b", {}, d.stats.first ? new Date(d.stats.first).getFullYear() : "–"), h("span", {}, "første år"))),
    h("div", { class: "grid-2 mt lok-grid" },
      h("div", { class: "lok-col" }, h("div", { class: "card", style: { padding: "8px" } }, mapEl),
        h("div", { class: "card lok-fill" }, h("h3", { style: { marginTop: 0 } }, "Seneste 30 dage"),
          h("small", { class: "muted" }, "Største antal pr. art pr. dag"),
          d.recent.length ? h("div", { class: "lok-scroll" }, h("ul", { class: "mini-list" }, ...d.recent.map(r => h("li", {},
          h("span", {}, fmtDate(r.dato, false), " · ", h("a", { href: `#/traad/${r.dato}/${r.artnr}/${loknr}` }, r.art)),
          h("span", {}, r.kategori !== "alm" ? tag(r.kategori, KAT[r.kategori]) : null, " ", h("b", { class: "num" }, fmtN(r.antal))))))) : h("p", { class: "muted" }, "Ingen observationer."))),
      h("div", { class: "card lok-fill" }, h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Arter på lokaliteten"),
        h("input", { type: "search", placeholder: "Søg…", style: { width: "160px" }, oninput: (e) => { q = e.target.value.toLowerCase(); drawSpecies(); } })),
        speciesBody)));
  const map = baseMap(mapEl, { center: d.lat ? [d.lat, d.lon] : [56, 10.5], zoom: d.lat ? 12 : 6 });
  if (d.lat) L.marker([d.lat, d.lon]).addTo(map);

  // Artslisten hentes for sig (og igen ved årsskifte i vælgeren); resten af siden er allerede vist
  let gone = false, built = false, years = [];
  const yearSel = h("select", { style: { width: "auto" }, "aria-label": "Vælg år", onchange: (e) => { f.year = e.target.value; loadSpecies(); } });
  const tableWrap = h("div", { class: "table-wrap lok-scroll" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", { class: "right" }, "Dage"), h("th", { class: "right" }, "Rekord"), h("th", {}, "Senest"))), tbody));
  const drawYears = () => yearSel.replaceChildren(h("option", { value: "" }, "Alle år"),
    ...years.map(y => h("option", { value: y.year, selected: String(y.year) === String(f.year) }, `${y.year} · ${fmtN(y.n_taxa)} arter`)));
  async function loadSpecies() {
    speciesState = "loading"; open.clear(); for (const k of Object.keys(records)) delete records[k];
    if (built) { tableWrap.classList.add("is-busy"); count.replaceChildren(h("span", { class: "spinner" })); }
    try {
      const r = await api(`/api/locations/${loknr}/species${f.year ? `?year=${encodeURIComponent(f.year)}` : ""}`);
      if (gone) return;
      d.species = r.species; years = r.years; speciesState = "ready";
      drawYears();
      if (!built) {
        built = true;
        speciesBody.replaceChildren(
          h("small", { class: "muted" }, "Kategori = den højeste arten har haft her (i det valgte år). Dage = antal dage med arten. Rekord = største antal på én dag (én observation, ikke summen af flere)."),
          h("div", { style: { display: "flex", flexDirection: "column", gap: "6px", minWidth: 0 } },
            h("div", { class: "row", style: { gap: "8px" } }, h("small", { class: "muted" }, "År"), yearSel), filters),
          tableWrap);
      }
      tableWrap.classList.remove("is-busy");
      drawSpecies();
    } catch (err) {
      if (gone) return;
      if (!built) speciesBody.replaceChildren(errorBox(err));
      else { tableWrap.classList.remove("is-busy"); count.textContent = "Kunne ikke hente året – prøv igen"; }
    }
  }
  loadSpecies();
  return () => { gone = true; map.remove(); };
}