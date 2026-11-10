// Admin → Tællinger: spontantællinger (metode 98) og FiNN-tællinger (99). Underfaner: Oversigt (status og statistik),
// Udtræk (CSV/XLSX/ODS – én række pr. tælling eller pr. observation), FiNN-områder (ret og synkronisér med DOFbasen,
// taellinger-finn.js) og Gridceller (Det Danske Kvadratnet, taellinger-grid.js). Server: api/admin/taellinger.py.
import { api, h, fmtN, fmtDate, toast, errorBox, empty, seg, params, setParams, modal, todayISO, loading } from "../../core.js";
import { barChart } from "../../charts.js";
import { st } from "./common.js";

export const BASE = "/api/admin/taellinger";
const VIEWS = [["oversigt", "Oversigt"], ["udtraek", "Udtræk"], ["finn", "FiNN-områder"], ["grid", "Gridceller"]];
const KINDS = [["alle", "Begge"], ["spontan", "Spontan"], ["finn", "FiNN"]];
const MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];
const pct = (x) => `${Math.round(x * 100)} %`;
const dec = (x) => x == null ? "–" : String(x).replace(".", ",");

export async function renderCounts(body) {
  const inner = h("div", {});
  let cleanup = null;
  const show = async (v) => {
    try { cleanup?.(); } catch { /* kortet er allerede væk */ }
    cleanup = null;
    inner.replaceChildren();
    try {
      if (v === "udtraek") cleanup = await exportView(inner);
      else if (v === "finn") cleanup = await (await import("./taellinger-finn.js")).renderFinn(inner);
      else if (v === "grid") cleanup = await (await import("./taellinger-grid.js")).renderGrid(inner);
      else cleanup = await overview(inner);
    } catch (err) { inner.append(errorBox(err)); }
  };
  const first = VIEWS.some(([k]) => k === params().vis) ? params().vis : "oversigt";
  body.append(h("div", { class: "row mb" }, seg(VIEWS, first, (v) => { setParams({ vis: v }); show(v); })), inner);
  await show(first);
}

// ---------------------------------------------------------------- fælles filter (gemmes i adressen, så det følger med mellem underfanerne)

/** Filterlinjen: type, periode, FiNN-område, observatør og art. onChange() kaldes, når noget ændres; qs() giver forespørgslen. */
export function filterBar(onChange, { sites = null, withSite = true } = {}) {
  const p = params();
  const F = { kind: p.kind || "alle", fra: p.fra || "", til: p.til || "", site: p.site || "", obs: p.obs || "", art: p.art || "", artnavn: p.artnavn || "" };
  const save = () => { setParams(F); onChange(); };
  const fra = h("input", { type: "date", value: F.fra, max: todayISO(), onchange: (e) => { F.fra = e.target.value; save(); } });
  const til = h("input", { type: "date", value: F.til, max: todayISO(), onchange: (e) => { F.til = e.target.value; save(); } });
  const obs = h("input", { type: "search", value: F.obs, placeholder: "Obserkode", style: { width: "120px" }, maxlength: 16,
    onchange: (e) => { F.obs = e.target.value.trim().toUpperCase(); save(); } });
  const siteSel = h("select", { style: { width: "auto", maxWidth: "220px" }, onchange: (e) => { F.site = e.target.value; save(); } },
    h("option", { value: "" }, "Alle FiNN-områder"));
  const fillSites = (list) => siteSel.replaceChildren(h("option", { value: "" }, "Alle FiNN-områder"),
    ...list.map(s => h("option", { value: s.id, selected: String(s.id) === F.site }, s.title)));
  if (withSite) {
    if (sites) fillSites(sites);
    else api(`${BASE}/finn`).then(d => fillSites(d.sites)).catch(() => {});
  }
  // Art: søg og vælg; × fjerner
  const artQ = h("input", { type: "search", placeholder: "Art …", value: F.artnavn, style: { width: "150px" }, autocomplete: "off" });
  const artHits = h("div", { class: "menu-list", style: { position: "absolute", zIndex: 1000, background: "var(--surface)", boxShadow: "var(--shadow)", borderRadius: "8px", minWidth: "200px" } });
  let t = null;
  artQ.oninput = () => {
    clearTimeout(t);
    const q = artQ.value.trim();
    if (!q) { artHits.replaceChildren(); if (F.art) { F.art = ""; F.artnavn = ""; save(); } return; }
    t = setTimeout(async () => {
      const rows = await api(`/api/species?q=${encodeURIComponent(q)}&limit=10`).catch(() => []);
      artHits.replaceChildren(...rows.map(r => h("a", { href: "javascript:void 0", style: { display: "block", padding: "6px 10px" },
        onclick: () => { artHits.replaceChildren(); artQ.value = r.navn; F.art = String(r.artnr); F.artnavn = r.navn; save(); } }, r.navn)));
    }, 200);
  };
  const quick = (days) => h("button", { class: "btn sm ghost", type: "button", onclick: () => {
    if (days) { const d = new Date(); d.setDate(d.getDate() - days); F.fra = d.toISOString().slice(0, 10); } else F.fra = "";
    F.til = ""; fra.value = F.fra; til.value = ""; save();
  } }, days ? (days === 365 ? "1 år" : `${days} d`) : "Alt");
  const el = h("div", { class: "card row wrap", style: { gap: "8px", alignItems: "center", marginBottom: "12px" } },
    seg(KINDS, F.kind, (v) => { F.kind = v; save(); }),
    h("span", { class: "row", style: { gap: "4px" } }, fra, h("span", { class: "muted" }, "–"), til),
    quick(30), quick(365), quick(0),
    withSite ? siteSel : null, obs,
    h("span", { style: { position: "relative" } }, artQ, artHits));
  const qs = (extra = {}) => {
    const q = new URLSearchParams({ kind: F.kind });
    if (F.fra) q.set("fra", F.fra);
    if (F.til) q.set("til", F.til);
    if (F.site && withSite && F.kind !== "spontan") q.set("site", F.site);
    if (F.obs) q.set("obserkode", F.obs);
    if (F.art) q.set("artnr", F.art);
    for (const [k, v] of Object.entries(extra)) if (v !== null && v !== undefined && v !== "") q.set(k, v);
    return q.toString();
  };
  return { el, qs, F };
}

// ---------------------------------------------------------------- én tælling

export async function showTrip(id) {
  let d;
  try { d = await api(`${BASE}/liste/${id}`); } catch (err) { toast(err.message, "err"); return; }
  const weather = [d.temperatur != null && `${d.temperatur} °C`, d.skydaekke != null && `skydække ${d.skydaekke}/8`,
    d.vindstyrke != null && `vind ${d.vindretning || ""} ${d.vindstyrke} m/s`, d.nedboer ? `nedbør ${d.nedboer} ${d.nedboertype || ""}` : null].filter(Boolean).join(" · ");
  modal(`${d.metode} ${fmtDate(d.dato)}`, h("div", { class: "stack" },
    h("div", {}, h("b", {}, d.navn || d.obserkode), h("span", { class: "muted" }, ` (${d.obserkode}) · ${d.turtidfra || "?"}–${d.turtidtil || "?"}`)),
    h("div", {}, h("a", { href: `#/lok/${d.loknr}` }, d.lok || d.loknr), d.omraade ? ` · FiNN-område: ${d.omraade}` : d.metode_id === 99 ? " · intet FiNN-område" : "",
      d.lat != null ? h("small", { class: "muted", style: { display: "block" } }, `${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}`) : null),
    weather ? h("small", { class: "muted" }, weather) : null,
    d.turnoter ? h("p", { style: { whiteSpace: "pre-wrap", margin: 0 } }, d.turnoter) : null,
    d.observationer.length ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Antal"), h("th", {}, "Adfærd"), h("th", {}, "Noter"))),
      h("tbody", {}, ...d.observationer.map(o => h("tr", {}, h("td", {}, o.navn || o.artnr, o.hemmelig ? h("span", { class: "tag plain", style: { marginLeft: "6px" } }, "hemmelig") : null),
        h("td", {}, fmtN(o.antal)), h("td", {}, o.adfkode || ""), h("td", {}, o.fuglnoter || "")))))) : empty("Ingen observationer"),
  ), { wide: true });
}

export function tripTable(rows, { compact = false } = {}) {
  if (!rows.length) return empty("Ingen tællinger");
  return h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, h("th", {}, "Dato"), h("th", {}, "Type"), h("th", {}, "Observatør"), h("th", {}, "Lokalitet / FiNN-område"),
      compact ? null : h("th", {}, "Gridcelle"), h("th", { style: { textAlign: "right" } }, "Arter"), h("th", { style: { textAlign: "right" } }, "Individer"))),
    h("tbody", {}, ...rows.map(r => h("tr", { class: "click", onclick: () => showTrip(r.id) },
      h("td", { style: { whiteSpace: "nowrap" } }, fmtDate(r.dato), h("small", { class: "muted", style: { display: "block" } }, r.fra || "")),
      h("td", {}, h("span", { class: `tag ${r.metode_id === 99 ? "SUB" : "plain"}` }, r.metode_id === 99 ? "FiNN" : "Spontan")),
      h("td", {}, r.navn || r.obserkode, h("small", { class: "muted", style: { display: "block" } }, r.obserkode)),
      h("td", {}, r.lok || r.loknr, r.omraade ? h("small", { class: "muted", style: { display: "block" } }, r.omraade)
        : r.metode_id === 99 ? h("small", { class: "muted", style: { display: "block" } }, "intet FiNN-område") : null),
      compact ? null : h("td", {}, h("small", {}, r.celle || "–")),
      h("td", { style: { textAlign: "right" } }, fmtN(r.n_arter)), h("td", { style: { textAlign: "right" } }, fmtN(r.n_ind)))))));
}

// ---------------------------------------------------------------- Oversigt: status og statistik

function statusCard(s) {
  const p = s.perioder, f = s.finn;
  const warn = [
    p.finn_uden_omraade && `${fmtN(p.finn_uden_omraade)} FiNN-tællinger er ikke knyttet til et FiNN-område`,
    s.finn_uden_for_omraade && `${fmtN(s.finn_uden_for_omraade)} FiNN-tællinger står uden for det område, de er knyttet til`,
    p.uden_obs && `${fmtN(p.uden_obs)} tællinger har ingen observationer`,
    p.uden_position && `${fmtN(p.uden_position)} tællinger har ingen position (kommer ikke med i gridcellerne)`,
    p.ikke_komplet && `${fmtN(p.ikke_komplet)} tællinger er ikke markeret som komplet artsliste`,
  ].filter(Boolean);
  return h("div", { class: "card stack" },
    h("div", { class: "row between wrap" }, h("h3", { style: { margin: 0 } }, "Status"),
      h("small", { class: "muted" }, f.kilde === "portal"
        ? `FiNN-områder: ${fmtN(f.omraader_i_appen)} i appen · ${fmtN(f.punkter_i_appen)} punkter · synkroniseret ${f.synk ? fmtDate(f.synk) : "aldrig"}${f.rettede ? ` · ${fmtN(f.rettede)} rettet i portalen` : ""}`
        : "FiNN-områderne hentes direkte fra DOFbasen – synkronisér under FiNN-områder for at kunne rette dem")),
    h("div", { class: "stats", style: { "--cols": 5 } }, st(fmtN(p.i_dag), "i dag"), st(fmtN(p.uge), "seneste 7 dage"), st(fmtN(p.maaned), "seneste 30 dage"),
      st(fmtN(p.aar), "i år"), st(fmtN(p.alle), "i alt")),
    s.i_gang.length ? h("div", {}, h("b", {}, `I gang lige nu (${s.i_gang.length})`),
      ...s.i_gang.map(r => h("div", { class: "row", style: { gap: "8px" } }, h("span", { class: "tag local" }, r.metode),
        `${r.navn || r.obserkode} · ${r.lok || ""} · ${r.turtidfra}–${r.turtidtil} · ${fmtN(r.n_obs)} obs.`))) : null,
    f.afventer ? h("div", { class: "notice" }, "En hentning fra DOFbasen venter på at blive gennemset under FiNN-områder.") : null,
    warn.length ? h("details", {}, h("summary", {}, `Datakvalitet (${warn.length})`), h("ul", { style: { margin: "6px 0 0", paddingLeft: "1.2em" } }, ...warn.map(w => h("li", {}, w)))) : null);
}

function monthChart(rows) {
  if (!rows.length) return h("p", { class: "muted" }, "Ingen tællinger i perioden.");
  // Alle måneder fra den første til den sidste, også tomme
  const [y0, m0] = rows[0].m.split("-").map(Number), [y1, m1] = rows[rows.length - 1].m.split("-").map(Number);
  const by = Object.fromEntries(rows.map(r => [r.m, r]));
  const all = [];
  for (let y = y0, m = m0; y < y1 || (y === y1 && m <= m1); m === 12 ? (y++, m = 1) : m++) all.push(`${y}-${String(m).padStart(2, "0")}`);
  const bars = all.map(k => { const r = by[k] || { spontan: 0, finn: 0 }; const [y, m] = k.split("-");
    return { v: r.spontan + r.finn, tip: `${MONTHS[m - 1]} ${y}: ${fmtN(r.spontan + r.finn)} (${fmtN(r.spontan)} spontan, ${fmtN(r.finn)} FiNN)` }; });
  const every = Math.max(1, Math.ceil(all.length / 12));
  const xTicks = all.map((k, i) => [i, k]).filter(([i]) => i % every === 0).map(([i, k]) => [i, `${MONTHS[k.slice(5) - 1]}${k.endsWith("01") || i === 0 ? " " + k.slice(2, 4) : ""}`]);
  return barChart({ bars, xTicks, yFormat: fmtN });
}

function hourChart(rows) {
  const by = Object.fromEntries(rows.map(r => [r.h, r.n]));
  const bars = Array.from({ length: 24 }, (_, i) => ({ v: by[i] || 0, tip: `kl. ${String(i).padStart(2, "0")}: ${fmtN(by[i] || 0)} tællinger` }));
  return barChart({ bars, width: 520, xTicks: [0, 4, 8, 12, 16, 20].map(i => [i, String(i).padStart(2, "0")]), yFormat: fmtN });
}

const table = (head, rows) => h("div", { class: "table-wrap" }, h("table", { class: "data" },
  h("thead", {}, h("tr", {}, ...head.map(([t, right]) => h("th", { style: right ? { textAlign: "right" } : {} }, t)))),
  h("tbody", {}, ...rows)));
const num = (v) => h("td", { style: { textAlign: "right", fontVariantNumeric: "tabular-nums" } }, v);

async function overview(body) {
  const statusEl = h("div", {});
  const out = h("div", { class: "stack" });
  const recent = h("div", {});
  let seq = 0;
  const bar = filterBar(() => load());
  body.append(statusEl, h("div", { class: "mt" }, bar.el), out);
  api(`${BASE}/status`).then(s => statusEl.replaceChildren(statusCard(s))).catch(err => statusEl.replaceChildren(errorBox(err)));
  async function load() {
    const my = ++seq;
    loading(out, 3);
    let d, l;
    try { [d, l] = await Promise.all([api(`${BASE}/oversigt?${bar.qs()}`), api(`${BASE}/liste?${bar.qs({ limit: 15 })}`)]); }
    catch (err) { if (my === seq) out.replaceChildren(errorBox(err)); return; }
    if (my !== seq) return;
    const t = d.total;
    recent.replaceChildren(tripTable(l.rows, { compact: true }));
    out.replaceChildren(
      h("div", { class: "stats", style: { "--cols": 4 } },
        st(fmtN(t.taellinger), `tællinger (${fmtN(t.spontan)} spontan, ${fmtN(t.finn)} FiNN)`), st(fmtN(t.observatoerer), "observatører"),
        st(fmtN(t.arter), "arter i alt"), st(dec(t.arter_pr_taelling), `arter pr. tælling (median ${t.median_arter ?? "–"})`),
        st(fmtN(t.observationer), "observationer"), st(fmtN(t.individer), "individer"), st(fmtN(t.omraader), "FiNN-områder talt"),
        st(fmtN(d.omraader_uden.length), "FiNN-områder uden tællinger")),
      h("div", { class: "card" }, h("h3", {}, "Tællinger pr. måned"), monthChart(d.maaneder)),
      h("div", { class: "grid-2", style: { display: "grid", gap: "12px", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" } },
        h("div", { class: "card" }, h("h3", {}, "Starttidspunkt"), hourChart(d.timer)),
        h("div", { class: "card" }, h("h3", {}, "Seneste tællinger"), recent)),
      h("div", { class: "card" }, h("h3", {}, "Arter"),
        h("p", { class: "muted", style: { marginTop: 0 } }, "Frekvens = andel af tællingerne, hvor arten er registreret. De 40 hyppigste."),
        d.arter.length ? table([["Art"], ["Tællinger", 1], ["Frekvens", 1], ["Individer", 1], ["Gns. pr. tælling med arten", 1], ["Maks.", 1]],
          d.arter.map(a => h("tr", {}, h("td", {}, a.navn || a.artnr), num(fmtN(a.taellinger)), num(pct(a.frekvens)), num(fmtN(a.individer)),
            num(dec(Math.round(a.individer / a.taellinger * 10) / 10)), num(fmtN(a.max))))) : empty("Ingen observationer")),
      h("div", { style: { display: "grid", gap: "12px", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" } },
        h("div", { class: "card" }, h("h3", {}, "Flest tællinger"),
          d.observatoerer.length ? table([["Observatør"], ["Tællinger", 1], ["Seneste", 1]], d.observatoerer.map(o => h("tr", {},
            h("td", {}, o.navn || o.obserkode, h("small", { class: "muted" }, ` ${o.obserkode}`)), num(fmtN(o.taellinger)), num(fmtDate(o.seneste))))) : empty("Ingen")),
        bar.F.kind !== "spontan" ? h("div", { class: "card" }, h("h3", {}, "FiNN-områder"),
          d.omraader.length ? table([["Område"], ["Tællinger", 1], ["Observatører", 1], ["Seneste", 1]], d.omraader.map(o => h("tr", {},
            h("td", {}, o.title), num(fmtN(o.taellinger)), num(fmtN(o.observatoerer)), num(fmtDate(o.seneste))))) : empty("Ingen FiNN-tællinger i perioden"),
          d.omraader_uden.length ? h("details", { class: "mt" }, h("summary", {}, `Områder uden tællinger i perioden (${d.omraader_uden.length})`),
            h("p", { class: "muted" }, d.omraader_uden.map(o => o.title).join(" · "))) : null) : null));
  }
  await load();
}

// ---------------------------------------------------------------- Udtræk

async function download(qs, niveau, fmt, btn) {
  btn.disabled = true;
  try {
    const res = await fetch(`${BASE}/udtraek?${qs}&niveau=${niveau}&fmt=${fmt}`, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fejl ${res.status}`);
    const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "")?.[1] || `taellinger.${fmt}`;
    const a = h("a", { href: URL.createObjectURL(await res.blob()), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } catch (err) { toast(err.message, "err"); }
  finally { btn.disabled = false; }
}

async function exportView(body) {
  const PAGE = 100;
  let offset = 0, seq = 0;
  const info = h("b", {});
  const list = h("div", {});
  const pager = h("div", { class: "row", style: { gap: "8px", justifyContent: "flex-end" } });
  const bar = filterBar(() => { offset = 0; load(); });
  const dl = (niveau) => h("div", { class: "row wrap", style: { gap: "6px", alignItems: "center" } },
    h("span", { style: { minWidth: "150px" } }, niveau === "taellinger" ? "Én række pr. tælling" : "Én række pr. observation"),
    ...["csv", "xlsx", "ods"].map(fmt => { const b = h("button", { class: "btn sm", type: "button", onclick: () => download(bar.qs(), niveau, fmt, b) }, fmt.toUpperCase()); return b; }));
  body.append(bar.el,
    h("div", { class: "card stack" },
      h("div", { class: "row between wrap" }, h("h3", { style: { margin: 0 } }, "Udtræk"), info),
      h("p", { class: "muted", style: { margin: 0 } }, "Udtrækket følger filteret ovenfor. Tællingerne har dato, tider, observatør, lokalitet, FiNN-område, position, ",
        "gridceller (1 og 10 km, Det Danske Kvadratnet), vejr og antal arter/individer. Observationerne har derudover art, antal, køn, adfærd, alder og dragt. ",
        "CSV er semikolonsepareret (UTF-8), som Excel på dansk forventer."),
      dl("taellinger"), dl("observationer")),
    h("div", { class: "card stack mt" }, list, pager));
  async function load() {
    const my = ++seq;
    loading(list, 4);
    let d;
    try { d = await api(`${BASE}/liste?${bar.qs({ limit: PAGE, offset })}`); } catch (err) { if (my === seq) list.replaceChildren(errorBox(err)); return; }
    if (my !== seq) return;
    info.textContent = `${fmtN(d.total)} tællinger`;
    list.replaceChildren(tripTable(d.rows));
    pager.replaceChildren(
      h("small", { class: "muted" }, d.total ? `${fmtN(offset + 1)}–${fmtN(Math.min(offset + PAGE, d.total))} af ${fmtN(d.total)}` : ""),
      h("button", { class: "btn sm ghost", type: "button", disabled: offset === 0, onclick: () => { offset = Math.max(0, offset - PAGE); load(); } }, "Forrige"),
      h("button", { class: "btn sm ghost", type: "button", disabled: offset + PAGE >= d.total, onclick: () => { offset += PAGE; load(); } }, "Næste"));
  }
  await load();
}
