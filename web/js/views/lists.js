import { api, state, h, icon, fmtN, fmtDate, tag, params, setParams, loadRef, requireLogin, empty, errorBox, modal, toast, seg, store, sortBar } from "../core.js";
import { lineChart, seriesLegend, PALETTE } from "../charts.js";

const YEAR = new Date().getFullYear();
const dayOfYear = (iso) => { const d = new Date(iso + "T12:00:00"); return Math.floor((d - new Date(d.getFullYear(), 0, 0)) / 86400000); };
const monthTicks = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
const monthName = (doy) => ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"][monthTicks.findLastIndex(t => t <= doy)] || "";

export async function render(view) {
  const p = params();
  const tab = p.tab || "mine";
  view.append(h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Lister & scoreboards"), h("p", { class: "lead" }, "Årslister, matrikellister, kommuner, egne områder – beregnet direkte fra DOFbasen-data.")),
    h("span", { class: "spacer" }),
    seg([["mine", "Mine"], ["score", "Scoreboard"], ["grupper", "Grupper"], ["matrikler", "Matrikler"]], tab, (v) => { setParams({ tab: v }); body.replaceChildren(); draw(v); })));
  const body = h("div", {});
  view.append(body);
  const draw = (t) => t === "score" ? scoreboard(body) : t === "grupper" ? groups(body) : t === "matrikler" ? matrikler(body) : mine(body);
  await draw(tab);
}

// ---------------------------------------------------------------- mine lister

async function mine(body) {
  if (requireLogin(body)) return;
  const meta = await api("/api/lists/meta");
  const p = params();
  const st = { scope: p.scope || "dk", year: p.year || String(YEAR), matrikel: p.m ? Number(p.m) : 0, sort: "dato" };
  const scopeSel = h("select", { style: { width: "auto" } });
  for (const sc of meta.scopes) scopeSel.append(h("option", { value: sc.scope }, sc.label));
  for (const m of meta.matrikler) scopeSel.append(h("option", { value: `matrikel:${m.nummer}` }, m.nummer === 1 ? `Boligbirding (${m.tag})` : `Matrikel ${m.nummer} · ${m.navn} (${m.tag})`));
  scopeSel.value = st.matrikel ? `matrikel:${st.matrikel}` : st.scope;
  const yearSel = h("select", { style: { width: "auto" } }, h("option", { value: "all" }, "Livsliste (alle år)"),
    ...[...new Set([YEAR, ...meta.years])].sort((a, b) => b - a).map(y => h("option", { value: y }, y)));
  yearSel.value = st.year;
  const out = h("div", { class: "stack mt" });
  const periodSel = h("select", { style: { width: "auto" }, class: "hide" });
  const addArea = h("a", { class: "btn sm ghost", href: "#/alarmer?tab=omraader", html: `${icon("plus")} Nyt område` });
  body.append(h("div", { class: "card" },
    h("div", { class: "row" }, scopeSel, yearSel, periodSel, h("span", { class: "spacer", style: { flex: 1 } }), addArea,
      h("button", { class: "btn sm", onclick: compare, html: `${icon("users")} Sammenlign` })),
    h("p", { class: "muted mt", style: { marginBottom: 0, fontSize: ".85rem" } },
      `Matrikellister: skriv ${meta.matrikel_prefix}${String(YEAR).slice(2)} i turnoten (matrikel 2: ${meta.matrikel_prefix}${String(YEAR).slice(2)}-2). Navne, flytning og perioder under `,
      h("a", { href: "#/lister?tab=matrikler" }, "Matrikler"), ". ",
      "Afdelinger og kommuner vælges under ", h("a", { href: "#/profil" }, "profil"), ", egne områder (polygon/radius) under ", h("a", { href: "#/alarmer?tab=omraader" }, "Alarmer → Områder"), ".")),
    out);

  st.periode = p.periode || "";
  const load = async () => {
    const v = scopeSel.value;
    st.matrikel = v.startsWith("matrikel:") ? Number(v.split(":")[1]) : 0;
    st.scope = st.matrikel ? "dk" : v;
    st.year = yearSel.value;
    if (!st.matrikel) st.periode = "";
    setParams({ scope: st.scope === "dk" ? "" : st.scope, year: st.year, m: st.matrikel || "", periode: st.periode });
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "220px" } }));
    try {
      const q = new URLSearchParams({ scope: st.scope, year: st.year });
      if (st.matrikel) q.set("matrikel", st.matrikel);
      if (st.periode) q.set("periode", st.periode);
      const data = await api(`/api/lists/user/${state.user.obserkode}?${q}`);
      drawPeriods(data.periode);
      drawList(out, data, st.year);
    } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  // Periodevælger for matrikler med flere adresser: aktiv periode (standard), en tidligere eller alle
  const drawPeriods = (info) => {
    const opts = info?.options || [];
    periodSel.classList.toggle("hide", !opts.length);
    if (!opts.length) return;
    const span = (o) => `${fmtDate(o.start, true)} – ${o.slut ? fmtDate(o.slut, true) : "nu"}`;
    periodSel.replaceChildren(...opts.map(o => h("option", { value: o.id }, `${o.navn}${o.id === info.active ? " (aktiv)" : ""} · ${span(o)}`)),
      h("option", { value: "alle" }, "Alle perioder (historik)"));
    periodSel.value = String(info.selected ?? "");
  };
  periodSel.onchange = () => { st.periode = periodSel.value; load(); };
  scopeSel.onchange = () => { st.periode = ""; load(); }; yearSel.onchange = () => { st.periode = ""; load(); };
  await load();

  async function compare() {
    const inp = h("input", { placeholder: "Obserkode, fx 8000ABC", style: { textTransform: "uppercase" } });
    const res = h("div", { class: "mt" });
    modal("Sammenlign lister", h("form", { onsubmit: async (e) => {
      e.preventDefault();
      try {
        const q = new URLSearchParams({ a: state.user.obserkode, b: inp.value.trim(), scope: st.scope, year: st.year });
        const d = await api(`/api/lists/compare?${q}`);
        res.replaceChildren(h("p", {}, `Fælles: ${d.both} arter`),
          h("div", { class: "grid-2" },
            h("div", {}, h("h3", {}, `Kun ${d.a.navn} (${d.only_a.length})`), h("ul", {}, ...d.only_a.map(i => h("li", {}, i.art)))),
            h("div", {}, h("h3", {}, `Kun ${d.b.navn} (${d.only_b.length})`), h("ul", {}, ...d.only_b.map(i => h("li", {}, i.art, h("small", {}, ` ${fmtDate(i.dato, false)}`)))))));
      } catch (err) { res.replaceChildren(errorBox(err)); }
    } }, h("div", { class: "row" }, inp, h("button", { class: "btn primary" }, "Sammenlign")), res), { wide: true });
  }
}

export function drawList(out, data, year) {
  const items = data.items;
  const isYear = year && year !== "all";
  const pts = items.map((it, i) => [isYear ? dayOfYear(it.dato) : new Date(it.dato).getFullYear(), i + 1]);
  const sort = store("listsort") || { key: "dato", dir: 1 };
  const tbody = h("tbody");
  const cmp = {
    dato: (a, b) => a.dato.localeCompare(b.dato) || a.obsid - b.obsid,
    navn: (a, b) => a.art.localeCompare(b.art, "da"),
    sys: (a, b) => (a.sortering || 0) - (b.sortering || 0),
    lok: (a, b) => (a.lok || "").localeCompare(b.lok || "", "da"),
  };
  const drawRows = () => {
    const sorted = [...items].sort((a, b) => sort.dir * (cmp[sort.key] || cmp.dato)(a, b));
    tbody.replaceChildren(...sorted.map((it) => h("tr", {},
      h("td", { class: "num muted" }, items.indexOf(it) + 1),
      h("td", {}, h("a", { href: `#/art/${it.artnr}` }, it.art), it.klass !== "Alm" ? [" ", tag(it.klass)] : null),
      h("td", { class: "nowrap" }, fmtDate(it.dato, !isYear)),
      h("td", {}, it.loknr ? h("a", { href: `#/lok/${it.loknr}` }, it.lok) : "–"))));
  };
  drawRows();
  const th = (label, key) => h("th", { style: { cursor: "pointer" }, onclick: () => { sort.dir = sort.key === key ? -sort.dir : 1; sort.key = key; store("listsort", sort); drawRows(); } }, label);
  const bar = sortBar([["dato", "Dato"], ["navn", "Navn"], ["sys", "Systematik"], ["lok", "Lokalitet"]], sort, () => { store("listsort", sort); drawRows(); });
  out.replaceChildren(
    h("div", { class: "card" },
      h("div", { class: "row between" }, h("div", {}, h("div", { class: "muted" }, data.navn), h("div", { style: { fontSize: "2.4rem", fontWeight: 800, letterSpacing: "-0.03em" } }, fmtN(data.count), h("span", { class: "muted", style: { fontSize: "1rem", fontWeight: 600 } }, " arter"))),
        items.length ? h("div", { class: "right muted" }, "Seneste: ", h("b", {}, items[items.length - 1].art), h("br"), fmtDate(items[items.length - 1].dato)) : null),
      items.length > 1 ? lineChart({ series: [{ name: "Arter", points: pts, color: PALETTE[0] }], step: true, height: 200,
        xFormat: isYear ? monthName : String, xTicks: isYear ? monthTicks : undefined, yFormat: (v) => fmtN(Math.round(v)) }) : null),
    items.length ? h("div", { class: "card table-wrap" }, h("div", { class: "mb" }, bar), h("table", { class: "data list-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "#"), th("Art", "navn"), th("Første dato", "dato"), h("th", {}, "Lokalitet"))), tbody))
      : empty("Ingen arter på denne liste endnu", "Data kommer fra DOFbasen – nye observationer tæller med inden for få minutter."));
}

// ---------------------------------------------------------------- matrikler: navne, perioder og flytning

const mName = (m) => m.nummer === 1 ? "Boligbirding" : `Matrikel ${m.nummer}`;

export async function matrikler(body) {
  if (requireLogin(body)) return;
  const out = h("div", { class: "stack" }, h("div", { class: "skeleton", style: { height: "200px" } }));
  body.append(h("div", { class: "row between mb" },
    h("p", { class: "muted", style: { margin: 0, maxWidth: "62ch" } }, "Boligbirding (matrikel 1) tæller på scoreboards; matrikel 2 og op er kun dine. Har en matrikel ligget flere steder, deler perioder listen op – ved flytning starter en ny periode, og fremgangen nulstilles fra flyttedatoen."),
    h("button", { class: "btn primary", onclick: () => newMatrikel(), html: `${icon("plus")} Ny matrikel` })), out);
  let data;
  const reload = (d) => { data = d; draw(); };
  const call = async (path, method = "GET", payload) => {
    try { reload(await api(path, { method, body: payload })); return true; } catch (err) { toast(err.message, "err"); return false; }
  };
  const span = (p) => `${fmtDate(p.start)} – ${p.slut ? fmtDate(p.slut) : "nu"}`;

  const draw = () => out.replaceChildren(...data.matrikler.map(m => {
    const name = h("input", { value: m.navn, maxlength: 120, "aria-label": "Navn" });
    return h("div", { class: "card stack" },
      h("div", { class: "row between" },
        h("div", { class: "row", style: { gap: "8px" } }, h("h2", { style: { margin: 0 } }, mName(m)),
          h("span", { class: "tag plain" }, m.tag), m.scoreboard ? h("span", { class: "tag portal" }, "Scoreboard") : h("span", { class: "tag plain", html: `${icon("lock")} Privat` }))),
      h("p", { class: "muted", style: { margin: 0 } }, m.n_trips
        ? `${fmtN(m.n_trips)} ture med tagget · ${fmtDate(m.first)} – ${fmtDate(m.last)}`
        : `Ingen ture med tagget endnu – skriv ${m.tag} i turnoten i DOFbasen.`),
      h("form", { class: "row", onsubmit: async (e) => { e.preventDefault(); if (await call(`/api/lists/matrikler/${m.nummer}`, "PUT", { navn: name.value })) toast("Navnet er gemt"); } },
        h("div", { style: { flex: 1, minWidth: "160px" } }, name), h("button", { class: "btn" }, "Gem navn")),
      h("div", {}, h("h3", { style: { margin: "4px 0 8px" } }, "Perioder"),
        m.perioder.length ? h("ul", { class: "periods" }, ...[...m.perioder].reverse().map(p => h("li", { class: p.id === m.active ? "active" : "" },
          h("div", { style: { minWidth: 0 } }, h("b", {}, p.navn), p.id === m.active ? h("span", { class: "tag portal", style: { marginLeft: "6px" } }, "Aktiv") : null,
            h("div", { class: "muted", style: { fontSize: ".86rem" } }, span(p))),
          h("div", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } },
            h("button", { class: "btn sm ghost", onclick: () => periodModal(m, p), html: icon("edit"), "aria-label": "Redigér" }),
            h("button", { class: "btn sm ghost danger", "aria-label": "Slet", html: icon("trash"), onclick: async () => {
              if (confirm(`Slet perioden '${p.navn}'?`)) await call(`/api/lists/perioder/${p.id}`, "DELETE");
            } })))))
          : h("p", { class: "muted", style: { margin: 0 } }, "Ingen perioder – alle fund med tagget tæller. Tilføj perioder, hvis matriklen har ligget flere steder.")),
      h("div", { class: "row" },
        h("button", { class: "btn primary", onclick: () => moveModal(m), html: `${icon("pin")} Flyt matrikel…` }),
        h("button", { class: "btn", onclick: () => periodModal(m, null), html: `${icon("plus")} Tilføj periode` }),
        m.defined ? h("button", { class: "btn ghost danger", style: { marginLeft: "auto" }, onclick: async () => {
          if (confirm(`Nulstil navn og perioder for ${mName(m)}? Dine tags i DOFbasen røres ikke.`)) await call(`/api/lists/matrikler/${m.nummer}`, "DELETE");
        } }, "Nulstil") : null));
  }));

  function periodModal(m, p) {
    const navn = h("input", { value: p?.navn || "", maxlength: 160, placeholder: "Adresse eller navn", required: true });
    const start = h("input", { type: "date", value: p?.start || "", required: true });
    const slut = h("input", { type: "date", value: p?.slut || "" });
    const open = h("input", { type: "checkbox", checked: p ? !p.slut : true });
    const syncOpen = () => { slut.disabled = open.checked; if (open.checked) slut.value = ""; };
    open.onchange = syncOpen; syncOpen();
    const dlg = modal(p ? "Redigér periode" : `Ny periode – ${mName(m)}`, h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      const payload = { navn: navn.value, start: start.value, slut: open.checked ? null : slut.value || null };
      const ok = p ? await call(`/api/lists/perioder/${p.id}`, "PATCH", { ...payload, aaben: open.checked })
        : await call(`/api/lists/matrikler/${m.nummer}/perioder`, "POST", payload);
      if (ok) { dlg.close(); toast("Perioden er gemt"); }
    } },
      h("label", { class: "field" }, h("span", {}, "Navn/adresse"), navn),
      h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Fra"), start), h("label", { class: "field" }, h("span", {}, "Til"), slut)),
      h("label", { class: "check" }, open, "Stadig aktiv (ingen slutdato)"),
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem"), h("button", { type: "button", class: "btn ghost", onclick: () => dlg.close() }, "Annullér"))));
  }

  function moveModal(m) {
    const current = m.perioder.find(p => p.id === m.active);
    const dato = h("input", { type: "date", value: new Date().toISOString().slice(0, 10), required: true });
    const ny = h("input", { maxlength: 160, placeholder: "Ny adresse", required: true });
    const gammel = h("input", { maxlength: 160, value: current?.navn || "", placeholder: "Tidligere adresse" });
    const preview = h("div", { class: "notice" });
    const dayBefore = (iso) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); };
    const update = () => {
      if (!dato.value) { preview.textContent = ""; return; }
      preview.replaceChildren(
        h("div", {}, h("b", {}, gammel.value || "Tidligere adresse"), ` slutter ${fmtDate(dayBefore(dato.value))}`,
          current ? "" : m.first ? ` (startet ved første fund ${fmtDate(m.first)})` : ""),
        h("div", {}, h("b", {}, ny.value || "Ny adresse"), ` starter ${fmtDate(dato.value)}`),
        h("div", { class: "muted mt", style: { fontSize: ".85rem" } }, `${mName(m)}-listen tæller fra flyttedatoen${m.scoreboard ? ", også på scoreboardet" : ""}. Fund fra den gamle adresse kan ses under 'Alle perioder'. Brug fortsat ${m.tag} i turnoten.`));
    };
    for (const el of [dato, ny, gammel]) el.oninput = update;
    update();
    const dlg = modal(`Flyt ${mName(m).toLowerCase()}`, h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      if (await call(`/api/lists/matrikler/${m.nummer}/flyt`, "POST", { dato: dato.value, ny_navn: ny.value, gammel_navn: gammel.value || null })) { dlg.close(); toast("Flytningen er registreret"); }
    } },
      h("label", { class: "field" }, h("span", {}, "Flyttedato (første dag på den nye adresse)"), dato),
      h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Ny adresse"), ny), h("label", { class: "field" }, h("span", {}, "Tidligere adresse"), gammel)),
      preview,
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Registrér flytning"), h("button", { type: "button", class: "btn ghost", onclick: () => dlg.close() }, "Annullér"))));
  }

  function newMatrikel() {
    const used = new Set(data.matrikler.map(m => m.nummer));
    let next = 2; while (used.has(next)) next++;
    const nr = h("input", { type: "number", min: 1, max: 20, value: next });
    const navn = h("input", { maxlength: 120, placeholder: "Fx Sommerhus, Kolonihave", required: true });
    const hint = h("p", { class: "muted", style: { margin: 0 } });
    const yy = String(new Date().getFullYear()).slice(2);
    const sync = () => { const n = Number(nr.value); hint.textContent = `Tag i turnoten: ${data.prefix}${yy}${n > 1 ? "-" + n : ""}${n === 1 ? " (tæller på scoreboards)" : " (privat liste)"}`; };
    nr.oninput = sync; sync();
    const dlg = modal("Ny matrikel", h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      if (await call(`/api/lists/matrikler/${Number(nr.value)}`, "PUT", { navn: navn.value })) { dlg.close(); toast("Matriklen er oprettet"); }
    } },
      h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Nummer"), nr), h("label", { class: "field" }, h("span", {}, "Navn"), navn)),
      hint,
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Opret"), h("button", { type: "button", class: "btn ghost", onclick: () => dlg.close() }, "Annullér"))));
  }

  try { reload(await api("/api/lists/matrikler")); } catch (err) { out.replaceChildren(errorBox(err)); }
}

// ---------------------------------------------------------------- scoreboard

async function scoreboard(body) {
  await loadRef();
  const p = params();
  const st = { scope: p.scope || "dk", year: p.year || String(YEAR), m: Number(p.m || 0), group: p.group || "" };
  const scopeSel = h("select", { style: { width: "auto" } },
    h("option", { value: "dk" }, "Danmark"), h("option", { value: "m1" }, "Boligbirding"),
    h("optgroup", { label: "Lokalafdelinger" }, ...state.afdelinger.map(a => h("option", { value: `afd:${a.id}` }, a.navn))),
    h("optgroup", { label: "Kommuner" }, ...state.kommuner.map(k => h("option", { value: `kom:${k.kode}` }, k.navn))));
  scopeSel.value = st.m ? `m${st.m}` : st.scope;
  const yearSel = h("select", { style: { width: "auto" } }, h("option", { value: "all" }, "Alle år"),
    ...Array.from({ length: 12 }, (_, i) => YEAR - i).map(y => h("option", { value: y }, y)));
  yearSel.value = st.year;
  const groupSel = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle deltagere"));
  if (state.user) for (const g of await api("/api/lists/groups")) groupSel.append(h("option", { value: g.id }, `Gruppe: ${g.name}`));
  groupSel.value = st.group;
  const view = seg([["rank", "Rangliste"], ["race", "Kapløb"], ["matrix", "Matrix"], ["unique", "Unikke"]], store("sbview") || "rank", (v) => { store("sbview", v); draw(); });
  const out = h("div", { class: "mt" });
  body.append(h("div", { class: "card" }, h("div", { class: "row" }, scopeSel, yearSel, groupSel, h("span", { style: { flex: 1 } }), view),
    h("p", { class: "muted mt", style: { fontSize: ".85rem", marginBottom: 0 } }, "Afdelings- og kommuneboards viser brugere, der har valgt området under profil. Grupper kan indeholde alle obserkoder.")), out);
  let data = null;
  const load = async () => {
    const v = scopeSel.value;
    st.m = v.startsWith("m") && !v.startsWith("matrikel") && v.length === 2 ? Number(v[1]) : 0;
    st.scope = st.m ? "dk" : v;
    st.year = yearSel.value; st.group = groupSel.value;
    setParams({ tab: "score", scope: st.scope === "dk" ? "" : st.scope, year: st.year, m: st.m || "", group: st.group });
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "300px" } }));
    try {
      const q = new URLSearchParams({ scope: st.scope, year: st.year });
      if (st.m) q.set("matrikel", st.m);
      if (st.group) q.set("group", st.group);
      data = await api(`/api/lists/scoreboard?${q}`);
      draw();
    } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  const draw = () => {
    if (!data) return;
    const mode = view.querySelector(".on")?.textContent;
    const rows = data.rows;
    if (!rows.length) { out.replaceChildren(empty("Ingen deltagere endnu")); return; }
    if (mode === "Kapløb") out.replaceChildren(h("div", { class: "card" }, race(rows.slice(0, 10), st.year)));
    else if (mode === "Matrix") out.replaceChildren(h("div", { class: "card table-wrap" }, matrix(rows.slice(0, 25), data.species)));
    else if (mode === "Unikke") out.replaceChildren(h("div", { class: "card" }, unique(rows, data.species)));
    else out.replaceChildren(ranking(rows, st));
  };
  [scopeSel, yearSel, groupSel].forEach(el => el.onchange = load);
  await load();
}

function ranking(rows, st) {
  const top = rows[0]?.antal || 1;
  return h("div", { class: "rank" }, ...rows.map(r => h("div", { class: `rank-row ${state.user?.obserkode === r.obserkode ? "me" : ""}`, onclick: () => showUser(r, st) },
    h("div", { class: "place" }, r.placering),
    h("div", {}, h("div", { class: "who" }, r.navn), h("div", { class: "last" }, r.sidste_art ? `Seneste: ${r.sidste_art} · ${fmtDate(r.sidste_dato, false)}` : "Ingen arter endnu"),
      h("div", { class: "bar-inline" }, h("i", { style: { width: `${(r.antal / top) * 100}%` } }))),
    h("div", { class: "score" }, fmtN(r.antal)))));
}

async function showUser(r, st) {
  const out = h("div", {}, h("div", { class: "skeleton" }));
  modal(r.navn, out, { wide: true });
  try {
    const q = new URLSearchParams({ scope: st.scope, year: st.year });
    if (st.m) q.set("matrikel", st.m);
    drawList(out, await api(`/api/lists/user/${r.obserkode}?${q}`), st.year);
  } catch (err) { out.replaceChildren(errorBox(err)); }
}

function race(rows, year) {
  const isYear = year !== "all";
  const series = rows.map((r, i) => ({ name: r.navn, color: PALETTE[i % PALETTE.length],
    points: r.firsts.map(([, d], j) => [isYear ? dayOfYear(d) : new Date(d).getFullYear() + dayOfYear(d) / 366, j + 1]) }));
  return h("div", {}, h("h3", {}, "Kumulativt antal arter"),
    lineChart({ series, step: true, height: 320, xFormat: isYear ? monthName : (x) => String(Math.floor(x)), xTicks: isYear ? monthTicks : undefined, yFormat: (v) => fmtN(Math.round(v)) }),
    seriesLegend(series));
}

function matrix(rows, names) {
  const sets = rows.map(r => new Map(r.firsts.map(([a, d]) => [a, d])));
  const species = [...new Set(rows.flatMap(r => r.firsts.map(f => f[0])))].sort((a, b) => (names[a] || "").localeCompare(names[b] || "", "da"));
  return h("table", { class: "matrix" },
    h("thead", {}, h("tr", {}, h("th", {}, `${species.length} arter`), ...rows.map(r => h("th", { class: "rot", title: r.navn }, r.navn)))),
    h("tbody", {}, ...species.map(a => {
      const have = sets.filter(s => s.has(a)).length;
      return h("tr", {}, h("td", { class: "sp" }, names[a] || a),
        ...sets.map(s => s.has(a) ? h("td", { class: have === 1 ? "only" : "yes", title: s.get(a) }, have === 1 ? "★" : "✓") : h("td", {})));
    })));
}

function unique(rows, names) {
  const count = new Map();
  rows.forEach(r => r.firsts.forEach(([a]) => count.set(a, (count.get(a) || 0) + 1)));
  const items = rows.map(r => ({ r, only: r.firsts.filter(([a]) => count.get(a) === 1) })).filter(x => x.only.length);
  if (!items.length) return empty("Ingen arter som kun én deltager har");
  return h("div", { class: "stack" }, h("h3", {}, "Arter som kun én deltager har"),
    ...items.sort((a, b) => b.only.length - a.only.length).map(({ r, only }) => h("div", {},
      h("b", {}, `${r.navn} (${only.length})`), h("div", { class: "muted" }, only.map(([a, d]) => `${names[a]} (${fmtDate(d, false)})`).join(", ")))));
}

// ---------------------------------------------------------------- grupper

async function groups(body) {
  if (requireLogin(body)) return;
  const list = h("div", { class: "stack" });
  const name = h("input", { placeholder: "Gruppenavn, fx 'Fugleholdet'" });
  const code = h("input", { placeholder: "Invitationskode", style: { textTransform: "uppercase" } });
  body.append(h("div", { class: "grid-2" },
    h("form", { class: "card stack", onsubmit: async (e) => { e.preventDefault(); try { await api("/api/lists/groups", { method: "POST", body: { name: name.value } }); name.value = ""; load(); } catch (err) { toast(err.message, "err"); } } },
      h("h3", {}, "Opret gruppe"), h("p", { class: "muted" }, "En gruppe får sit eget scoreboard. Du kan tilføje alle obserkoder – også folk der ikke bruger portalen."), name, h("button", { class: "btn primary" }, "Opret")),
    h("form", { class: "card stack", onsubmit: async (e) => { e.preventDefault(); try { const g = await api("/api/lists/groups/join", { method: "POST", body: { code: code.value } }); toast(`Du er nu med i ${g.name}`); code.value = ""; load(); } catch (err) { toast(err.message, "err"); } } },
      h("h3", {}, "Deltag med kode"), h("p", { class: "muted" }, "Få koden af gruppens ejer."), code, h("button", { class: "btn" }, "Deltag"))),
    h("h2", { class: "mt2" }, "Mine grupper"), list);
  const load = async () => {
    const gs = await api("/api/lists/groups");
    list.replaceChildren(...(gs.length ? gs.map(g => groupCard(g, load)) : [empty("Du er ikke med i nogen grupper endnu")]));
  };
  await load();
}

function groupCard(g, reload) {
  const members = h("div", { class: "muted" }, "…");
  const add = h("input", { placeholder: "Tilføj obserkode", style: { width: "180px", textTransform: "uppercase" } });
  const card = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", {}, g.name), h("span", { class: "spacer" }),
      g.invite_code ? h("span", { class: "muted" }, "Kode: ", h("b", { class: "kbd" }, g.invite_code)) : null,
      h("a", { class: "btn sm", href: `#/lister?tab=score&group=${g.id}` }, "Scoreboard")),
    members,
    g.owner ? h("form", { class: "row mt", onsubmit: async (e) => { e.preventDefault(); try { await api(`/api/lists/groups/${g.id}/members`, { method: "POST", body: { obserkode: add.value } }); add.value = ""; loadMembers(); } catch (err) { toast(err.message, "err"); } } },
      add, h("button", { class: "btn sm" }, "Tilføj"), h("span", { style: { flex: 1 } }),
      h("button", { type: "button", class: "btn sm ghost danger", onclick: async () => { if (confirm("Slet gruppen?")) { await api(`/api/lists/groups/${g.id}`, { method: "DELETE" }); reload(); } } }, "Slet gruppe"))
      : h("button", { class: "btn sm ghost danger mt", onclick: async () => { await api(`/api/lists/groups/${g.id}/members/${state.user.obserkode}`, { method: "DELETE" }); reload(); } }, "Forlad gruppen"));
  const loadMembers = async () => {
    const d = await api(`/api/lists/groups/${g.id}`);
    members.replaceChildren(...d.members.map(m => h("span", { class: "chip", style: { marginRight: "6px", marginBottom: "6px" } }, m.navn,
      g.owner && m.obserkode !== state.user.obserkode ? h("span", { style: { cursor: "pointer", marginLeft: "4px" }, onclick: async () => { await api(`/api/lists/groups/${g.id}/members/${m.obserkode}`, { method: "DELETE" }); loadMembers(); } }, "×") : null)));
  };
  loadMembers();
  return card;
}
