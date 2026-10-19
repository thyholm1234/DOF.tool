// Matrikelbirding (Boligbirding): eget modul med oversigt over placeringer, liste, scoreboards og matrikler.
import { api, state, h, icon, fmtN, fmtDate, params, setParams, loadRef, requireLogin, empty, errorBox, modal, toast, seg, store } from "../core.js";
import { lineChart, PALETTE } from "../charts.js";
import { drawList, ranking, race, matrix, unique } from "./lists.js";

const YEAR = new Date().getFullYear();
const dayOfYear = (iso) => { const d = new Date(iso + "T12:00:00"); return Math.floor((d - new Date(d.getFullYear(), 0, 0)) / 86400000); };
const monthTicks = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
const monthName = (doy) => ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"][monthTicks.findLastIndex(t => t <= doy)] || "";
const KIND = { dk: "Landsplan", afd: "Lokalafdeling", kom: "Kommune", group: "Gruppe" };

export async function render(view) {
  const tab = params().tab || "oversigt";
  view.append(h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Matrikel"), h("p", { class: "lead" }, "Boligbirding: arter set fra din matrikel – og hvor du ligger i landet, din lokalafdeling, din kommune og dine grupper.")),
    h("span", { class: "spacer" }),
    seg([["oversigt", "Oversigt"], ["liste", "Min liste"], ["score", "Scoreboard"], ["matrikler", "Matrikler"]], tab, (v) => { setParams({ tab: v }); body.replaceChildren(); draw(v); })));
  const body = h("div", {});
  view.append(body);
  const draw = (t) => ({ liste: myList, score: scoreboard, matrikler }[t] || landing)(body);
  await draw(tab);
}

const yearSelect = (value, onchange, withAll = true) => h("select", { style: { width: "auto" }, onchange: (e) => onchange(e.target.value) },
  withAll ? h("option", { value: "all", selected: value === "all" }, "Alle år") : null,
  ...Array.from({ length: 10 }, (_, i) => YEAR - i).map(y => h("option", { value: y, selected: String(value) === String(y) }, y)));

// ---------------------------------------------------------------- oversigt (landingsside)

async function landing(body) {
  if (requireLogin(body)) return;
  const st = { year: params().year || String(YEAR) };
  const out = h("div", { class: "stack" }, h("div", { class: "skeleton", style: { height: "320px" } }));
  body.append(out);
  const load = async () => {
    setParams({ tab: "oversigt", year: st.year === String(YEAR) ? "" : st.year });
    let d;
    try { d = await api(`/api/lists/matrikel/summary?year=${st.year}`); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    const isYear = st.year !== "all";
    const pts = d.items.map((it, i) => [isYear ? dayOfYear(it.dato) : new Date(it.dato).getFullYear(), i + 1]);
    const latest = [...d.items].reverse().slice(0, 8);
    const ySel = yearSelect(st.year, (v) => { st.year = v; load(); });
    out.replaceChildren(
      h("div", { class: "card" },
        h("div", { class: "row between" },
          h("div", {}, h("div", { class: "muted" }, `${d.navn} · `, h("span", { class: "tag plain" }, d.tag)),
            h("div", { style: { fontSize: "2.6rem", fontWeight: 800, letterSpacing: "-0.03em" } }, fmtN(d.count), h("span", { class: "muted", style: { fontSize: "1rem", fontWeight: 600 } }, isYear ? ` arter i ${st.year}` : " arter i alt"))),
          ySel),
        d.n_trips ? null : h("div", { class: "notice mt" }, `Skriv ${d.tag} i turnoten i DOFbasen, når du tæller fra matriklen – så kommer arterne med her.`),
        pts.length > 1 ? lineChart({ series: [{ name: "Arter", points: pts, color: PALETTE[0] }], step: true, height: 190,
          xFormat: isYear ? monthName : String, xTicks: isYear ? monthTicks : undefined, yFormat: (v) => fmtN(Math.round(v)) }) : null),
      h("h2", { class: "mt" }, "Placeringer"),
      d.standings.length > 1 ? null : h("p", { class: "muted" }, "Vælg lokalafdeling og kommune under ", h("a", { href: "#/profil" }, "profil"), " og deltag i ", h("a", { href: "#/lister?tab=grupper" }, "grupper"), " for at se flere placeringer."),
      h("div", { class: "grid-2" }, ...d.standings.map(s => standingCard(s, st.year))),
      latest.length ? h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Seneste nye arter"),
        h("table", { class: "data" }, h("tbody", {}, ...latest.map(it => h("tr", {},
          h("td", {}, h("a", { href: `#/art/${it.artnr}` }, it.art)), h("td", { class: "nowrap muted" }, fmtDate(it.dato, !isYear))))))) : null);
  };
  await load();
}

function standingCard(s, year) {
  const href = s.kind === "group" ? `#/matrikel?tab=score&group=${s.ref}&year=${year}` : `#/matrikel?tab=score&scope=${s.ref}&year=${year}`;
  const me = state.user?.obserkode;
  return h("div", { class: "card" },
    h("div", { class: "row between" }, h("div", {}, h("div", { class: "muted", style: { fontSize: ".78rem", textTransform: "uppercase", letterSpacing: ".04em", fontWeight: 700 } }, KIND[s.kind]),
      h("h3", { style: { margin: "2px 0 0" } }, s.label)),
      h("div", { class: "right" }, h("div", { style: { fontSize: "2rem", fontWeight: 800, lineHeight: 1 } }, `#${s.placering}`), h("div", { class: "muted", style: { fontSize: ".85rem" } }, `af ${fmtN(s.n)}`))),
    h("p", { class: "muted", style: { margin: ".5rem 0" } },
      s.above ? [`${fmtN(s.above.gap)} ${s.above.gap === 1 ? "art" : "arter"} op til `, h("b", {}, s.above.navn)] : s.n > 1 ? "Du fører! " : "Du er den eneste deltager endnu. ",
      s.below ? [` · ${fmtN(s.below.gap)} foran `, h("b", {}, s.below.navn)] : null,
      s.public ? null : h("div", { style: { fontSize: ".82rem" } }, "Din liste er ikke offentlig – placeringen er beregnet, men du vises ikke for andre.")),
    s.top.length ? h("div", { class: "rank" }, ...s.top.map(r => h("div", { class: `rank-row ${r.obserkode === me ? "me" : ""}`, style: { padding: "6px 10px" } },
      h("div", { class: "place" }, r.placering), h("div", { class: "who" }, r.navn), h("div", { class: "score" }, fmtN(r.antal))))) : null,
    h("a", { class: "btn sm ghost mt", href }, "Se hele scoreboardet →"));
}

// ---------------------------------------------------------------- min liste (alle matrikler, perioder)

async function myList(body) {
  if (requireLogin(body)) return;
  const meta = await api("/api/lists/meta");
  const p = params();
  const st = { m: Number(p.m || 1), year: p.year || String(YEAR), periode: p.periode || "" };
  const mSel = h("select", { style: { width: "auto" } }, ...meta.matrikler.map(m => h("option", { value: m.nummer, selected: m.nummer === st.m },
    m.nummer === 1 ? `Boligbirding (${m.tag})` : `Matrikel ${m.nummer} · ${m.navn} (${m.tag})`)));
  const ySel = yearSelect(st.year, (v) => { st.year = v; st.periode = ""; load(); });
  const periodSel = h("select", { style: { width: "auto" }, class: "hide" });
  const out = h("div", { class: "stack mt" });
  body.append(h("div", { class: "card" }, h("div", { class: "row" }, mSel, ySel, periodSel),
    h("p", { class: "muted mt", style: { marginBottom: 0, fontSize: ".85rem" } },
      `Skriv ${meta.matrikel_prefix}${String(YEAR).slice(2)} i turnoten (matrikel 2: ${meta.matrikel_prefix}${String(YEAR).slice(2)}-2). Matrikel 1 tæller på scoreboards, 2 og op er private. Navne, flytning og perioder under `,
      h("a", { href: "#/matrikel?tab=matrikler" }, "Matrikler"), ".")), out);
  const load = async () => {
    setParams({ tab: "liste", m: st.m === 1 ? "" : st.m, year: st.year, periode: st.periode });
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "220px" } }));
    try {
      const q = new URLSearchParams({ scope: "dk", year: st.year, matrikel: st.m });
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
  mSel.onchange = () => { st.m = Number(mSel.value); st.periode = ""; load(); };
  await load();
}

// ---------------------------------------------------------------- scoreboard (matrikel 1)

async function scoreboard(body) {
  await loadRef();
  const p = params();
  const st = { scope: p.scope || "dk", year: p.year || String(YEAR), group: p.group || "", m: 1 };
  const scopeSel = h("select", { style: { width: "auto" } }, h("option", { value: "dk" }, "Danmark"),
    h("optgroup", { label: "Lokalafdelinger" }, ...state.afdelinger.map(a => h("option", { value: `afd:${a.id}` }, a.navn))),
    h("optgroup", { label: "Kommuner" }, ...state.kommuner.map(k => h("option", { value: `kom:${k.kode}` }, k.navn))));
  scopeSel.value = st.scope;
  const ySel = yearSelect(st.year, (v) => { st.year = v; load(); });
  const groupSel = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle deltagere"));
  if (state.user) for (const g of await api("/api/lists/groups")) groupSel.append(h("option", { value: g.id }, `Gruppe: ${g.name}`));
  groupSel.value = st.group;
  const mode = { v: store("mbview") || "rank" };
  const view = seg([["rank", "Rangliste"], ["race", "Kapløb"], ["matrix", "Matrix"], ["unique", "Unikke"]], mode.v, (v) => { mode.v = v; store("mbview", v); draw(); });
  const out = h("div", { class: "mt" });
  body.append(h("div", { class: "card" }, h("div", { class: "row" }, scopeSel, ySel, groupSel, h("span", { style: { flex: 1 } }), view),
    h("p", { class: "muted mt", style: { fontSize: ".85rem", marginBottom: 0 } }, "Boligbirding (matrikel 1). Kommuner viser deltagere, hvis matrikel ligger i kommunen (aflæst af hvor matrikelobservationerne er gjort). Lokalafdelinger viser deltagere, der har valgt afdelingen under profil. En gruppe viser kun gruppens medlemmer.")), out);
  let data = null;
  const load = async () => {
    st.scope = scopeSel.value; st.group = groupSel.value;
    scopeSel.disabled = !!st.group;
    setParams({ tab: "score", scope: st.scope === "dk" ? "" : st.scope, year: st.year, group: st.group });
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "300px" } }));
    try {
      const q = new URLSearchParams({ scope: st.scope, year: st.year });
      if (st.group) q.set("group", st.group);
      data = await api(`/api/lists/matrikel/scoreboard?${q}`);
      draw();
    } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  const draw = () => {
    if (!data) return;
    const rows = data.rows;
    if (!rows.length) { out.replaceChildren(empty("Ingen deltagere endnu")); return; }
    const lst = { scope: "dk", year: st.year, m: 1 };
    if (mode.v === "race") out.replaceChildren(h("div", { class: "card" }, race(rows.slice(0, 10), st.year)));
    else if (mode.v === "matrix") out.replaceChildren(h("div", { class: "card table-wrap" }, matrix(rows.slice(0, 25), data.species)));
    else if (mode.v === "unique") out.replaceChildren(h("div", { class: "card" }, unique(rows, data.species)));
    else out.replaceChildren(ranking(rows, lst));
  };
  scopeSel.onchange = load; groupSel.onchange = load;
  await load();
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

