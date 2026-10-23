// DOFbasen: søg observationer (/search/) – samme felter som originalen; resultat som tabel, kort eller CSV.
// Links i DOFbasens formater (search1.php og result.php) oversættes, så eksisterende links virker.
import { api, h, fmtN, toast, setParams } from "../core.js";
import { BASE, head, link, dmy, notice, loadingBox, autosuggest, ctx, artnr5 } from "./shell.js";
import { dkuArrow, popupLinks } from "./common.js";
import { parseDate } from "./observationer.js";

const DEFAULTS = { periode: "antaldage", dage: 7, species: "alle", omraade: "none", artdata: "keine", sortering: "dato", antalregl: "" };
const BOOLS = ["summering", "medobser", "koordinat", "kommentar", "udelad_burfugle", "udelad_hybrider_ubestemte", "vis_underarter_som_arter"];
const BEHAVIOUR = { 1: 1, 2: 2, 3: 3, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9 };

/** Adresse-parametre (egne og DOFbasens) → søgeformularens værdier. */
export function fromQuery(q) {
  const f = { ...DEFAULTS };
  // search1.php: datovalg, idag, daysnumber, yeardato, maaned, lokal, lokdata, obserdata, behaviour, visning, art, minantal
  if (q.datovalg) {
    const dv = q.datovalg;
    if (dv === "idag") { f.periode = "dato"; f.dato = parseDate(q.idag); }
    else if (dv === "daysnumber") { f.periode = "antaldage"; f.dage = Math.min(30, +q.daysnumber || 7); }
    else if (dv === "yeardato") { f.periode = "maanedaar"; f.aar = +q.yeardato; }
    else if (dv === "maaned") { f.periode = "maanedaar"; f.aar = +(q.yeardato || new Date().getFullYear()); f.maaned = +q.maaned; }
    else if (dv === "betweendato") { f.periode = "mellemdato"; f.dato_first = parseDate(q.fradato || q.dato_first); f.dato_second = parseDate(q.tildato || q.dato_second); }
    if (q.omraade === "lokal" && q.lokal && q.lokal !== "alle") { f.omraade = "lokal"; f.lokal = +q.lokal; }
    if (q.omraade === "lokdata" && q.lokdata) { f.omraade = "lokalitet"; f.lok = +q.lokdata; }
    if (q.behaviour && BEHAVIOUR[q.behaviour]) f.adf = BEHAVIOUR[q.behaviour];
    if (q.visning === "fugle") f.species = "weird";
    if (q.visning === "sufugle") f.species = "rare";
    if (q.minantal) { f.antalregl = "stoerre"; f.antal = +q.minantal; }
    if (q.art) { f.artdata = "art"; f.art = +q.art; }
    if (q.obserdata) f.obserdata = q.obserdata;
    return f;
  }
  for (const [k, v] of Object.entries(q)) {
    if (v === "" || v == null || k === "soeg" || k === "design" || k === "obstype" || k === "visning_res" || k === "side") continue;
    if (BOOLS.includes(k)) f[k] = v === "1" || v === "true" || v === "on";
    else if (["dato", "dato_first", "dato_second"].includes(k)) f[k] = parseDate(v);
    else if (["dage", "aar", "maaned", "dag", "lok", "lokal", "iba", "stor", "art", "fra_art", "til_art", "antal", "adf", "metode", "biggestobs"].includes(k)) f[k] = +v;
    else f[k] = v;
  }
  if (f.art && f.artdata === "keine") f.artdata = "art";
  if (f.lok && f.omraade === "none") f.omraade = "lokalitet";
  return f;
}

function toQuery(f) {
  const q = {};
  for (const [k, v] of Object.entries(f)) {
    if (v === "" || v == null || v === false || (k in DEFAULTS && DEFAULTS[k] === v)) continue;
    q[k] = v === true ? "1" : String(v);
  }
  return q;
}

export async function page(parts, q) {
  const f = fromQuery(q);
  const valg = await api("/api/dofbasen/soeg/valg");
  const resBox = h("div", {});
  const st = { view: q.design === "map" ? "map" : "table", page: +q.side || 1 };

  // ---- felter
  const sel = (name, opts, onChange) => {
    const s = h("select", { onchange: () => { f[name] = s.value === "" ? null : s.value; onChange?.(); } }, ...opts.map(([v, t]) => h("option", { value: v, selected: String(f[name] ?? "") === String(v) }, t)));
    return s;
  };
  const inp = (name, attrs = {}) => {
    const i = h("input", { type: "text", value: f[name] ?? "", ...attrs, oninput: () => { f[name] = i.value === "" ? null : (attrs.inputmode === "numeric" ? +i.value : i.value); } });
    return i;
  };
  const chk = (name) => h("input", { type: "checkbox", checked: !!f[name], onchange: (e) => { f[name] = e.target.checked; } });
  const row = (label, title, ...ctl) => h("div", { class: "row" }, h("span", { title }, label + ":"), h("span", {}, ...ctl));

  const periodBox = h("div", {});
  const drawPeriod = () => {
    const p = f.periode;
    const r = [row("Periode", "Vælg hvordan perioden angives", sel("periode", [["none", "[VÆLG PERIODE]"], ["dato", "Dato"], ["mellemdato", "Datointerval"], ["antaldage", "Seneste antal dage"], ["maanedaar", "År/måned/dag"]], drawPeriod))];
    if (p === "dato") r.push(row("Dato", "Dato (dd-mm-åååå)", dateInput("dato")));
    if (p === "mellemdato") r.push(row("Fra dato", "Fra og med", dateInput("dato_first")), row("Til dato", "Til og med", dateInput("dato_second")));
    if (p === "antaldage") r.push(row("Seneste antal dage", "1–366 dage", inp("dage", { size: 3, inputmode: "numeric", class: "hi" })));
    if (p === "maanedaar") r.push(row("År", "Årstal", inp("aar", { size: 4, inputmode: "numeric", placeholder: String(new Date().getFullYear()) })),
      row("Måned", "Valgfri", sel("maaned", [["", ""], ...Array.from({ length: 12 }, (_, i) => [i + 1, ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"][i]])])),
      row("Dag", "Valgfri (kræver måned)", inp("dag", { size: 2, inputmode: "numeric" })));
    periodBox.replaceChildren(...r);
  };
  const dateInput = (name) => {
    const i = h("input", { type: "text", value: f[name] ? dmy(f[name]) : "", size: 10, placeholder: "dd-mm-åååå", oninput: () => { f[name] = parseDate(i.value); } });
    return i;
  };
  drawPeriod();

  const areaBox = h("span", {});
  const drawArea = () => {
    const o = f.omraade;
    if (o === "lokalitet") areaBox.replaceChildren(autosuggest({ url: "/api/dofbasen/lokaliteter/soeg?q=", placeholder: "Skriv mindst 2 bogstaver", width: "200px",
      initial: f.lok_navn || (f.lok ? String(f.lok) : ""), onPick: (r) => { f.lok = r?.loknr ?? null; f.lok_navn = r?.navn; } }));
    else if (o === "lokal") areaBox.replaceChildren(sel("lokal", [["", ""], ...(ctx.data.afdelinger || []).map(a => [a.id, a.navn])]));
    else if (o === "kommune") areaBox.replaceChildren(sel("kommune", [["", ""], ...valg.kommuner.map(k => [k.kode, k.navn])]));
    else if (o === "iba" || o === "stor") areaBox.replaceChildren(sel(o, [["", ""], ...valg.omraader.filter(a => a.kind === o).map(a => [a.id, a.navn])]));
    else areaBox.replaceChildren();
  };
  drawArea();

  const artBox = h("span", {});
  const drawArt = () => {
    const a = f.artdata;
    const sug = (key, ph) => autosuggest({ url: "/api/dofbasen/arter/soeg?q=", placeholder: ph, width: "170px", initial: f[key + "_navn"] || (f[key] ? artnr5(f[key]) : ""),
      label: (r) => r.navn, onPick: (r) => { f[key] = r?.artnr ?? null; f[key + "_navn"] = r?.navn; } });
    if (a === "art") artBox.replaceChildren(sug("art", "Art"));
    else if (a === "artinterval") artBox.replaceChildren(sug("fra_art", "Fra art"), " – ", sug("til_art", "Til art"));
    else artBox.replaceChildren();
  };
  drawArt();

  const left = h("div", {},
    periodBox, h("hr"),
    row("Observationstype", "Kun observationer i showcasen (ynglepar og IBA-tællinger findes ikke i portalens data)", h("select", { disabled: true }, h("option", {}, "Observationer"))),
    row("Fugle/andre dyr", "Gruppe", sel("species", Object.entries(valg.species))), h("hr"),
    row("Område", "Begræns til et område", sel("omraade", [["none", ""], ["lokalitet", "Lokalitet"], ["lokal", "Lokalafdeling"], ["kommune", "Kommune"], ["iba", "IBA"], ["stor", "Større område"]], drawArea), areaBox), h("hr"),
    row("Art/artsgruppe", "Art eller interval i systematisk rækkefølge", sel("artdata", [["keine", ""], ["art", "Art"], ["artinterval", "Artsinterval"]], drawArt), artBox),
    row("Antal", "Antal fugle i observationen", sel("antalregl", [["", ""], ["stoerre", ">="], ["mindre", "<="], ["ligmed", "="]]), inp("antal", { size: 5, inputmode: "numeric" })),
    row("Adfærd/alder", "Adfærd og alder", sel("adf", [["", ""], ...Object.entries(valg.adf)]), sel("alder", [["", ""], ...valg.alder.map(a => [a, a])])),
    row("Dragt/køn", "Dragt og køn", sel("dragt", [["", ""], ...Object.entries(valg.dragt).map(([k, t]) => [k, t])]), sel("sex", [["", ""], ["M", "M"], ["F", "F"]])), h("hr"),
    row("Bilag 1/rødlisten", "Findes ikke i portalens data", h("select", { disabled: true }, h("option", {}, ""))), h("hr"),
    row("Summering", "Summér antal pr. tur og art", chk("summering")));
  const right = h("div", { class: "r2" },
    row("Medobservatør", "Søg også hvor observatøren er medobservatør", chk("medobser")),
    row("Observatør", "Obserkode", inp("obserdata", { size: 8 })), h("hr"),
    row("Koordinater", "Kun observationer med fuglens position", chk("koordinat")),
    row("Kommentarer", "Kun observationer med kommentar", chk("kommentar")),
    row("Udelad burfugle", "Udelad arter i [klammer]", chk("udelad_burfugle")),
    row("Udelad hybrider/ubestemte", "Udelad hybrider og sp.", chk("udelad_hybrider_ubestemte")),
    row("Vis underarter som arter", "Underarter tælles som arten", chk("vis_underarter_som_arter")),
    row("Største forekomst", "De N største observationer", inp("biggestobs", { size: 4, inputmode: "numeric" })),
    row("Tekststreng", "Søg i fugle- og turnoter", inp("textsearch", { size: 12 })),
    row("Metode", "Turens metode", sel("metode", [["", ""], ...Object.entries(valg.metode).sort((a, b) => a[1].localeCompare(b[1], "da"))])), h("hr"),
    row("Sortering", "Rækkefølge", sel("sortering", [["dato", "Dato"], ["art", "Art"], ["lokalitet", "Lokalitet"], ["observat", "Observatør"]])),
    h("div", { style: { marginTop: "6px" } }, h("button", { type: "submit" }, "SØG"), " ", h("button", { type: "button", onclick: () => { location.hash = `${BASE}/soeg`; } }, "Nulstil")));

  const run = async (pg = 1) => {
    st.page = pg;
    const body = Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== null && v !== undefined && v !== "" && !k.endsWith("_navn")));
    setParams({ ...toQuery(body), soeg: "1", design: st.view === "map" ? "map" : "", side: pg > 1 ? pg : "" });
    resBox.replaceChildren(loadingBox("Søger …"));
    try {
      const r = await api("/api/dofbasen/soeg", { method: "POST", body: { ...body, page: pg, limit: st.view === "map" ? 500 : 100 } });
      drawResult(r, body);
    } catch (err) { resBox.replaceChildren(notice(err.message, true)); }
  };

  const drawResult = (r, body) => {
    const pages = Math.max(1, Math.ceil(r.total / r.limit));
    const viewSel = h("span", {}, "Vis som: ",
      h("a", { href: "javascript:void 0", style: { fontWeight: st.view === "table" ? 700 : 400 }, onclick: () => { st.view = "table"; run(1); } }, "tabel"), " · ",
      h("a", { href: "javascript:void 0", style: { fontWeight: st.view === "map" ? 700 : 400 }, onclick: () => { st.view = "map"; run(1); } }, "kort"), " · ",
      h("a", { href: "javascript:void 0", onclick: () => csv(body) }, "regneark (CSV)"));
    const pager = pages > 1 && st.view === "table" ? h("div", { class: "db-pager" },
      h("button", { type: "button", disabled: st.page <= 1, onclick: () => run(st.page - 1) }, "«"),
      `Side ${st.page} af ${fmtN(pages)}`,
      h("button", { type: "button", disabled: st.page >= pages, onclick: () => run(st.page + 1) }, "»")) : null;
    const period = r.period[0] ? `${dmy(r.period[0])} – ${dmy(r.period[1])}` : "alle år";
    resBox.replaceChildren(
      head("Søgeresultat", { kind: "blue" }),
      ...r.notes.map(n => notice(n)),
      h("div", { class: "db-results-bar" }, h("b", {}, r.capped ? "Over 50.000" : fmtN(r.total)), ` ${body.summering ? "summerede rækker" : "observationer"} · ${period}`, h("span", { style: { flex: 1 } }), viewSel),
      st.view === "map" ? mapView(r.rows) : tableView(r.rows, body), pager);
  };

  const csv = async (body) => {
    try {
      const res = await fetch("/api/dofbasen/soeg/csv", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" }, body: JSON.stringify(body) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fejl ${res.status}`);
      const blob = await res.blob();
      const a = h("a", { href: URL.createObjectURL(blob), download: `dofbasen-soegning-${new Date().toISOString().slice(0, 10)}.csv` });
      document.body.append(a); a.click(); a.remove();
      toast("Regnearket er hentet (højst 50.000 rækker)");
    } catch (err) { toast(err.message, "err"); }
  };

  const form = h("form", { onsubmit: (e) => { e.preventDefault(); run(1); } }, h("div", { class: "db-search" }, left, right));
  const main = h("div", {}, head("Søg i DOFbasen"), form, resBox);
  popupLinks(main);
  if (q.soeg) run(st.page);
  return h("div", { class: "db-page wide" }, h("div", { class: "db-col" }, main));
}

const katClass = (k) => k === "SU" ? "db-su" : k === "SUB" ? "db-subart" : "";

function tableView(rows, body) {
  if (!rows.length) return h("p", { class: "muted" }, "Ingen observationer matcher søgningen.");
  return h("div", { class: "db-scroll" }, h("table", { class: "db-table" },
    h("thead", {}, h("tr", {}, ...["Dato", "Art", "Antal", "Køn", "Adfærd", "Alder", "Dragt", "Lokalitet", "Afdeling", "Observatør", "Tid", body.summering ? "Obs." : "Kommentar"].map(t => h("th", {}, t)))),
    h("tbody", {}, ...rows.map(r => h("tr", {},
      h("td", { style: { whiteSpace: "nowrap" } }, dmy(r.dato)),
      h("td", {}, h("span", { class: katClass(r.kategori) }, r.art)),
      h("td", { class: "num" }, h("a", { href: `${BASE}/obs/${r.obsid}` }, fmtN(r.antal)), " ", dkuArrow(r.dku)),
      h("td", {}, r.koen || ""), h("td", { title: r.adfaerd || "" }, r.adfkode || ""), h("td", {}, r.alderkode || ""), h("td", {}, r.dragtkode || ""),
      h("td", {}, link(`/lok/${r.loknr}`, r.lokalitet || String(r.loknr || ""))),
      h("td", { class: "muted" }, (r.afdeling || "").replace("DOF ", "")),
      h("td", {}, link(`/obser/${r.obserkode}`, r.observator || r.obserkode)),
      h("td", { class: "muted", style: { whiteSpace: "nowrap" } }, r.obstidfra || r.turtidfra || ""),
      h("td", { class: "muted small" }, body.summering ? fmtN(r.n_obs) : (r.fuglnoter || "").slice(0, 120)))))));
}

function mapView(rows) {
  const el = h("div", { class: "db-map tall" });
  const pts = rows.filter(r => r.lat != null);
  requestAnimationFrame(() => {
    const map = L.map(el, { scrollWheelZoom: true }).setView([56.1, 11.0], 6);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
    const byLoc = new Map();
    for (const r of pts) { const k = `${r.lat.toFixed(5)},${r.lon.toFixed(5)}`; if (!byLoc.has(k)) byLoc.set(k, []); byLoc.get(k).push(r); }
    const bounds = [];
    for (const [, list] of byLoc) {
      const r0 = list[0];
      const color = list.some(r => r.kategori === "SU") ? "#e00" : list.some(r => r.kategori === "SUB") ? "#0a0" : "#1d6fe0";
      L.circleMarker([r0.lat, r0.lon], { radius: Math.min(12, 4 + Math.sqrt(list.length)), color, weight: 1, fillColor: color, fillOpacity: .8 }).addTo(map)
        .bindPopup(`<b>${esc(r0.lokalitet || "")}</b>${r0.praecis ? " (fuglens position)" : ""}<br>` + list.slice(0, 15).map(r => `${dmy(r.dato)}: <a href="${BASE}/obs/${r.obsid}">${esc(r.art)} ${r.antal}</a>`).join("<br>") + (list.length > 15 ? `<br>… og ${list.length - 15} mere` : ""));
      bounds.push([r0.lat, r0.lon]);
    }
    if (bounds.length) map.fitBounds(bounds, { maxZoom: 12, padding: [20, 20] });
  });
  return h("div", {}, el, h("p", { class: "muted" }, `${fmtN(pts.length)} af ${fmtN(rows.length)} viste observationer har en position (fuglens eller lokalitetens). Kortet viser højst 500 observationer.`));
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
