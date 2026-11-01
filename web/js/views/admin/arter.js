// Moderation → Arter: bemærk-grænser, fænologivinduer, artslisten, listekategorier og manuelle fund.
import { api, h, icon, fmtN, fmtDate, seg, params, setParams, toast, errorBox, empty, modal, can } from "../../core.js";

// Moderation → Arter: arternes klassifikation, bemærk-filtre pr. lokalafdeling og fænologiens perioder
export function species(body) {
  // Arter, Bemærk-filtre og Fænologi hører under modulet arter, listekategorierne under kategorier
  const views = [["arter", "Arter", speciesList, "arter"], ["bemaerk", "Bemærk-filtre", bemaerkPanel, "arter"], ["faenologi", "Fænologi", phenoPanel, "arter"],
    ["kategorier", "Kategorier", categoriesPanel, "kategorier"]].filter(v => can(v[3]));
  let vis = views.some(v => v[0] === params().vis) ? params().vis : views[0][0];
  const inner = h("div", {});
  const draw = () => { inner.replaceChildren(); setParams({ vis: vis === views[0][0] ? "" : vis }); views.find(v => v[0] === vis)[2](inner); };
  body.append(h("div", { class: "row mb" }, seg(views.map(v => [v[0], v[1]]), vis, (v) => { vis = v; setParams({ afd: "", art: "", y: "", q: "" }); draw(); })), inner);
  draw();
}

/** Søgefelt med forslag fra artslisten; onPick(art) ved valg. */
function speciesInput(all, onPick, placeholder = "Art …") {
  const input = h("input", { type: "search", placeholder, autocomplete: "off", spellcheck: "false" });
  const suggest = h("div", { class: "suggest dropdown" });
  const pick = (sp) => { input.value = sp.navn; suggest.replaceChildren(); onPick(sp); };
  input.oninput = () => { onPick(null); suggest.replaceChildren(...searchSpecies(all, input.value).map(sp => h("button", { type: "button", onmousedown: (e) => e.preventDefault(), onclick: () => pick(sp) }, h("span", {}, sp.navn)))); };
  input.onblur = () => setTimeout(() => suggest.replaceChildren(), 150);
  input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); suggest.querySelector("button")?.click(); } };
  return { el: h("div", { class: "dd-wrap", style: { flex: "1 1 220px" } }, input, suggest), clear: () => { input.value = ""; onPick(null); } };
}

const NOTE_REF = "SU- og SUB-arter er altid bemærkelsesværdige og vises derfor ikke. Ændringer herfra bevares, når referencedata hentes fra DOFbasen igen, og gælder nye og ændrede observationer. «Fortryd» sætter DOFbasens værdi ind igen.";

const notSU = (s) => !["SU", "SUB"].includes(s.klass);  // altid bemærkelsesværdige – hører ikke til i filtrene

async function bemaerkPanel(body) {
  let d, all;
  try { [d, all] = await Promise.all([api("/api/admin/bemaerk"), api("/api/species/index")]); } catch (err) { body.append(errorBox(err)); return; }
  all = all.filter(notSU);
  let afd = Number(params().afd) || d.afdelinger[0]?.id;
  const afdSel = h("select", { style: { maxWidth: "280px" } }, ...d.afdelinger.map(a => h("option", { value: a.id, selected: a.id === afd }, a.navn)));
  const filter = h("input", { type: "search", placeholder: "Filtrér arter …", style: { maxWidth: "240px" } });
  const list = h("div", { class: "card table-wrap mt" });
  let picked = null;
  const sp = speciesInput(all, (x) => { picked = x; }, "Tilføj art …");
  const antal = h("input", { type: "number", min: 1, inputmode: "numeric", placeholder: "Antal", style: { width: "110px" } });
  const save = async (artnavn, n) => {
    try { await api(`/api/admin/bemaerk/${afd}`, { method: "PUT", body: { artnavn, antal: n } }); toast("Gemt"); return true; } catch (err) { toast(err.message, "err"); return false; }
  };
  const call = async (url, opts, msg) => { try { await api(url, opts); toast(msg); load(); } catch (err) { toast(err.message, "err"); } };
  const add = h("form", { class: "row card mt", style: { gap: "8px", alignItems: "center" }, onsubmit: async (e) => {
    e.preventDefault();
    if (!picked) return toast("Vælg en art fra listen", "err");
    if (!(Number(antal.value) >= 1)) return toast("Angiv et antal på mindst 1", "err");
    if (await save(picked.navn, Number(antal.value))) { sp.clear(); antal.value = ""; load(); }
  } }, sp.el, antal, h("button", { class: "btn primary" }, "Tilføj / ret"));
  let rows = [], removed = [];
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    const shown = rows.filter(r => !f || r.artnavn.toLowerCase().includes(f));
    list.replaceChildren(
      h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Bemærk fra antal"), h("th", {}, ""), h("th", {}, ""))),
        h("tbody", {}, ...shown.map(r => {
          const inp = h("input", { type: "number", min: 1, value: r.antal, style: { width: "100px", height: "32px" } });
          inp.onchange = async () => { if (Number(inp.value) >= 1 && await save(r.artnavn, Number(inp.value))) load(); else inp.value = r.antal; };
          return h("tr", {}, h("td", {}, r.artnavn), h("td", {}, inp),
            h("td", { class: "muted" }, r.edit_id ? h("span", {}, h("span", { class: "tag local" }, "ændret"), ` DOFbasen: ${r.dof_antal ?? "ingen"} `,
              h("button", { class: "link-btn", type: "button", onclick: () => call(`/api/admin/reference-edits/${r.edit_id}`, { method: "DELETE" }, "Fortrudt") }, "Fortryd")) : ""),
            h("td", { class: "right" }, h("button", { class: "btn sm ghost danger", type: "button", onclick: () => {
              if (confirm(`Fjern bemærk-filteret for ${r.artnavn}?`)) call(`/api/admin/bemaerk/${afd}?artnavn=${encodeURIComponent(r.artnavn)}`, { method: "DELETE" }, "Fjernet");
            } }, "Fjern")));
        }))),
      shown.length ? null : h("p", { class: "muted" }, "Ingen arter."),
      removed.length ? h("div", { class: "mt" }, h("h3", {}, "Fjernet i portalen"), ...removed.map(r => h("div", { class: "row between" },
        h("span", {}, r.artnavn, h("small", { class: "muted" }, ` DOFbasen: ${r.dof_antal ?? "ingen"}`)),
        h("button", { class: "btn sm", type: "button", onclick: () => call(`/api/admin/reference-edits/${r.edit_id}`, { method: "DELETE" }, "Fortrudt") }, "Fortryd")))) : null);
  };
  const load = async () => {
    try { const r = await api(`/api/admin/bemaerk?afdeling=${afd}`); rows = r.rows; removed = r.removed; draw(); } catch (err) { list.replaceChildren(errorBox(err)); }
  };
  afdSel.onchange = () => { afd = Number(afdSel.value); setParams({ afd }); load(); };
  filter.oninput = draw;
  body.append(h("p", { class: "muted" }, "En observation er bemærkelsesværdig, når antallet er mindst tærsklen i én af observationens lokalafdelinger. ", NOTE_REF),
    h("div", { class: "row", style: { gap: "8px" } }, afdSel, filter), add, list);
  await load();
}

async function phenoPanel(body) {
  let all;
  try { all = (await api("/api/species/index")).filter(notSU); } catch (err) { body.append(errorBox(err)); return; }
  const list = h("div", { class: "card table-wrap mt" });
  const filter = h("input", { type: "search", placeholder: "Filtrér arter …", style: { maxWidth: "240px" } });
  const ddmm = (v = "") => h("input", { type: "text", value: v, placeholder: "DD-MM", maxlength: 5, inputmode: "numeric", style: { width: "80px", height: "32px" } });
  const call = async (url, opts, msg) => { try { await api(url, opts); toast(msg); load(); return true; } catch (err) { toast(err.message, "err"); return false; } };
  // Én eller flere perioder (fx sommer og vinter) som redigerbare par
  const windowsEditor = (windows) => {
    const box = h("div", { class: "stack", style: { gap: "4px" } });
    const pair = (a, b) => { const row = h("div", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } }, ddmm(a), h("span", { class: "muted" }, "–"), ddmm(b),
      h("button", { class: "btn sm ghost", type: "button", "aria-label": "Fjern periode", onclick: () => { if (box.children.length > 1) row.remove(); } }, "×")); box.append(row); };
    (windows.length ? windows : [["", ""]]).forEach(([a, b]) => pair(a, b));
    return { el: h("div", { class: "stack", style: { gap: "4px" } }, box, h("button", { class: "link-btn", type: "button", onclick: () => pair("", "") }, "+ periode")),
      value: () => [...box.children].map(r => [...r.querySelectorAll("input")].map(i => i.value.trim())) };
  };
  let picked = null;
  const sp = speciesInput(all, (x) => { picked = x; }, "Tilføj art …");
  const newWin = windowsEditor([]);
  const add = h("form", { class: "row card mt", style: { gap: "8px", alignItems: "flex-start" }, onsubmit: async (e) => {
    e.preventDefault();
    if (!picked) return toast("Vælg en art fra listen", "err");
    if (await call("/api/admin/phenology", { method: "PUT", body: { artnavn: picked.navn, windows: newWin.value() } }, "Gemt")) { sp.clear(); newWin.el.querySelectorAll("input").forEach(i => { i.value = ""; }); }
  } }, sp.el, newWin.el, h("button", { class: "btn primary" }, "Tilføj / ret"));
  let rows = [], removed = [];
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    const shown = rows.filter(r => !f || r.artnavn.toLowerCase().includes(f));
    list.replaceChildren(
      h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Bemærk i perioden"), h("th", {}, ""), h("th", {}, ""))),
        h("tbody", {}, ...shown.map(r => {
          const ed = windowsEditor(r.windows);
          return h("tr", {}, h("td", {}, r.artnavn), h("td", {}, ed.el),
            h("td", { class: "muted" }, r.edit_id ? h("span", {}, h("span", { class: "tag local" }, "ændret"),
              ` DOFbasen: ${r.dof_windows?.length ? r.dof_windows.map(w => w.join("–")).join(", ") : "ingen"} `,
              h("button", { class: "link-btn", type: "button", onclick: () => call(`/api/admin/reference-edits/${r.edit_id}`, { method: "DELETE" }, "Fortrudt") }, "Fortryd")) : ""),
            h("td", { class: "right" }, h("div", { class: "row", style: { gap: "4px", justifyContent: "flex-end" } },
              h("button", { class: "btn sm", type: "button", onclick: () => call("/api/admin/phenology", { method: "PUT", body: { artnavn: r.artnavn, windows: ed.value() } }, "Gemt") }, "Gem"),
              h("button", { class: "btn sm ghost danger", type: "button", onclick: () => {
                if (confirm(`Fjern ${r.artnavn} fra fænologien?`)) call(`/api/admin/phenology?artnavn=${encodeURIComponent(r.artnavn)}`, { method: "DELETE" }, "Fjernet");
              } }, "Fjern"))));
        }))),
      shown.length ? null : h("p", { class: "muted" }, "Ingen arter."),
      removed.length ? h("div", { class: "mt" }, h("h3", {}, "Fjernet i portalen"), ...removed.map(r => h("div", { class: "row between" },
        h("span", {}, r.artnavn, h("small", { class: "muted" }, ` DOFbasen: ${r.dof_windows?.length ? r.dof_windows.map(w => w.join("–")).join(", ") : "ingen"}`)),
        h("button", { class: "btn sm", type: "button", onclick: () => call(`/api/admin/reference-edits/${r.edit_id}`, { method: "DELETE" }, "Fortrudt") }, "Fortryd")))) : null);
  };
  const load = async () => {
    try { const r = await api("/api/admin/phenology"); rows = r.rows; removed = r.removed; draw(); } catch (err) { list.replaceChildren(errorBox(err)); }
  };
  filter.oninput = draw;
  body.append(h("p", { class: "muted" }, "Observationer af arten i perioden (DD-MM, fx 21-04 – 25-09; en periode kan gå hen over nytår) bliver bemærkelsesværdige, og perioden bruges i fænologifilteret. ", NOTE_REF),
    filter, add, list);
  await load();
}

const SPECIES_TYPES = [["art", "Art"], ["underart", "Underart"], ["hybrid", "Hybrid"], ["ub", "Ubestemt"], ["andre", "Andre"]];

const SPECIES_LABELS = { navn: "Navn", latin: "Latin", sortering: "Sortering", klass: "Status", art_type: "Type", list_artnr: "Tæller som", excluded: "Udeluk" };

async function speciesList(body) {
  const q = h("input", { type: "search", placeholder: "Søg navn, latin eller nr.…", style: { maxWidth: "280px" } });
  const type = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle typer"), ...SPECIES_TYPES.map(([v, t]) => h("option", { value: v }, t)));
  const edited = h("input", { type: "checkbox" });
  const count = h("small", { class: "muted" });
  const out = h("div", { class: "card table-wrap mt" });
  let all = [];
  body.append(h("p", { class: "muted" }, "Alle arter og taxa fra DOFbasen. Ret navn, latin, sortering, status, type, hvilken art en underart tæller som på årslister (tom = tæller ikke) og om arten er udelukket fra lister og notifikationer. Rettelser lægges oven på DOFbasens artsdata, hver gang de hentes, og kan fortrydes."),
    h("div", { class: "row" }, q, type, h("label", { class: "check" }, edited, " Kun rettede"), count, h("span", { class: "spacer" }),
      can("kategorier") ? h("button", { class: "btn", html: `${icon("plus")} Opret art`, title: "Opret en art, der ikke findes i listen",
        onclick: () => newSpeciesForm(q.value, () => load()) }) : null), out);
  const draw = () => {
    const term = q.value.trim().toLowerCase();
    const rows = all.filter(s => (!term || s.navn.toLowerCase().includes(term) || (s.latin || "").toLowerCase().includes(term) || String(s.artnr) === term)
      && (!type.value || s.art_type === type.value) && (!edited.checked || s.edited.length));
    count.textContent = `${fmtN(rows.length)} af ${fmtN(all.length)}`;
    out.replaceChildren(h("table", { class: "data stack-sm", style: { "--cols": 3 } },
      h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Nr."), h("th", {}, "Type"), h("th", {}, "Status"), h("th", {}, "Tæller som"), h("th", {}, "Udeluk"), h("th", {}))),
      h("tbody", {}, ...rows.map(s => h("tr", {},
        h("td", {}, h("b", {}, s.navn), s.latin ? h("small", { class: "muted" }, ` ${s.latin}`) : null,
          s.edited.length ? h("span", { class: "tag bemaerk", style: { marginLeft: "6px" }, title: `Rettet: ${s.edited.map(f => SPECIES_LABELS[f]).join(", ")}` }, "rettet") : null),
        h("td", { class: "num muted", "data-label": "Nr." }, s.artnr), h("td", { class: "muted", "data-label": "Type" }, s.art_type),
        h("td", { "data-label": "Status" }, s.klass), h("td", { class: "muted", "data-label": "Tæller som" }, s.list_artnr === s.artnr ? "sig selv" : s.list_navn || "–"),
        h("td", { "data-label": "Udeluk" }, s.excluded ? "ja" : ""),
        h("td", { class: "right" }, h("button", { class: "btn sm", type: "button", html: `${icon("edit")} Ret`, onclick: () => editSpecies(s, all, load) })))))));
  };
  const load = async () => {
    try { all = await api("/api/admin/species"); draw(); } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(draw, 150); };
  type.onchange = draw; edited.onchange = draw;
  await load();
}

function editSpecies(s, all, done) {
  const navn = h("input", { value: s.navn, required: true, maxlength: 200 });
  const latin = h("input", { value: s.latin || "", maxlength: 200 });
  const sortering = h("input", { type: "number", value: s.sortering ?? "", min: 0, style: { width: "120px" } });
  const klass = h("select", {}, ...["Alm", "SUB", "SU"].map(k => h("option", { value: k, selected: k === s.klass }, k)));
  const artType = h("select", {}, ...SPECIES_TYPES.map(([v, t]) => h("option", { value: v, selected: v === s.art_type }, t)));
  const targets = all.filter(x => x.art_type === "art" && x.list_artnr === x.artnr && x.artnr !== s.artnr);
  const list = h("select", {}, h("option", { value: "" }, "Tæller ikke"), h("option", { value: s.artnr }, "Sig selv"),
    ...targets.map(x => h("option", { value: x.artnr }, `${x.navn} (${x.artnr})`)));
  list.value = s.list_artnr == null ? "" : String(s.list_artnr);
  const excluded = h("input", { type: "checkbox", checked: s.excluded });
  const dof = s.dof || {};
  const was = (f, label) => s.edited.includes(f) ? h("small", { class: "muted" }, `DOFbasen: ${label ?? (dof[f] ?? "–")}`) : null;
  const field = (t, el, f, label) => h("label", { class: "field" }, h("span", {}, t), el, was(f, label));
  const listName = (n) => n == null ? "tæller ikke" : n === s.artnr ? "sig selv" : all.find(x => x.artnr === n)?.navn || n;
  const { close } = modal(`Ret ${s.navn}`, h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const v = { navn: navn.value.trim(), latin: latin.value.trim() || null, sortering: sortering.value === "" ? null : +sortering.value, klass: klass.value,
      art_type: artType.value, list_artnr: list.value === "" ? null : +list.value, excluded: excluded.checked };
    const changed = Object.fromEntries(Object.entries(v).filter(([k, x]) => x !== (s[k] ?? null)));
    if (!Object.keys(changed).length) { close(); return; }
    try { await api(`/api/admin/species/${s.artnr}`, { method: "PATCH", body: changed }); close(); toast("Gemt"); done(); }
    catch (err) { toast(err.message, "err"); }
  } },
    h("p", { class: "muted", style: { margin: 0 } }, `Nr. ${s.artnr}. Rettelser lægges oven på DOFbasens artsdata, hver gang de hentes.`),
    field("Navn", navn, "navn"), field("Latin", latin, "latin"),
    h("div", { class: "row", style: { gap: "12px", alignItems: "flex-end" } }, field("Sortering", sortering, "sortering"), field("Status", klass, "klass"), field("Type", artType, "art_type")),
    field("Tæller som (årslister)", list, "list_artnr", listName(dof.list_artnr)),
    h("label", { class: "check" }, excluded, " Udeluk fra lister og notifikationer", s.edited.includes("excluded") ? h("small", { class: "muted" }, ` (DOFbasen: ${dof.excluded ? "ja" : "nej"})`) : null),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem"),
      s.edited.length ? h("button", { class: "btn", type: "button", onclick: async () => {
        if (!confirm(`Fortryd alle portalens rettelser af ${s.navn}?`)) return;
        try { await api(`/api/admin/species/${s.artnr}/edits`, { method: "DELETE" }); close(); toast("Fortrudt"); done(); } catch (err) { toast(err.message, "err"); }
      } }, "Fortryd rettelser") : null)));
}

const COUNTABLE = ["A", "B", "C"];

const katTag = (k, title) => h("span", { class: `tag ${COUNTABLE.includes(k) ? "plain" : "SUB"}`, title: title || (COUNTABLE.includes(k) ? "Krydsbar" : "Ikke krydsbar") }, `Kat. ${k}`);

const SOURCE_TXT = { portal: "sat i portalen", dofbasen: "DOFbasen har [klammer] om navnet", standard: "standard" };

async function categoriesPanel(body) {
  const q = h("input", { type: "search", placeholder: "Søg art…", value: params().q || "" });
  const list = h("div", { class: "stack", style: { maxHeight: "70vh", overflow: "auto" } });
  const detail = h("div", { class: "stack" });
  const listBox = h("details", { class: "card more", style: { marginBottom: "12px" } },
    h("summary", {}, "Den Danske Liste (Netfugl) – læg kategorierne ind"));
  listBox.append(h("div", { class: "stack mt" }, h("p", { class: "muted", style: { margin: 0 } }, "Sammenligner portalens arter med Netfugls DK-liste (www.netfugl.dk/artslister/dk: kategori A–C; D, E og F supplerer vi fra Sjældenhedsudvalgets PDF, da Netfugl ikke viser dem). Se først hvad en import vil ændre, og kør den bagefter. Rækker I selv har lavet, røres ikke, og afløste rækker bevares i historikken."),
    h("button", { class: "btn", style: { alignSelf: "flex-start" }, onclick: async (e) => {
      const btn = e.currentTarget; btn.disabled = true;
      try { renderListReport(listBox, await api("/api/categories/danish-list"), () => { loadList(); loadDetail(); }); }
      catch (err) { toast(err.message, "err"); } finally { btn.disabled = false; }
    } }, "Se hvad der ændres")));
  const findsBox = h("details", { class: "card more", style: { marginBottom: "12px" } }, h("summary", {}, "Manuelle fund – fund der ikke står i DOFbasen"));
  findsBox.addEventListener("toggle", () => { if (findsBox.open && !findsBox.dataset.loaded) { findsBox.dataset.loaded = "1"; manualFinds(findsBox); } });
  body.append(
    listBox, findsBox,
    h("p", { class: "muted", style: { marginTop: 0 } }, "Kun kategori A, B og C er krydsbare på årslisterne (D, E og F er ikke). Kategorierne følger Den Danske Liste (A–C fra Netfugl, D–F fra Sjældenhedsudvalgets PDF). En arts kategori kan skifte over tid (gyldig fra en dato), og en serie af observationer på én lokalitet kan overstyres – fx et godkendt A-fund af en D-art. Ændringer slettes aldrig; tidligere værdier ses i historikken."),
    h("div", { class: "cat-layout" },
      h("div", { class: "card stack" }, h("div", { class: "row", style: { flexWrap: "nowrap" } }, q,
        h("button", { class: "btn", html: `${icon("plus")} Opret art`, title: "Opret en art, der ikke findes i listen", onclick: () => newSpeciesForm(q.value, (artnr) => { q.value = ""; setParams({ art: artnr, y: "" }); loadList(); loadDetail(); }) })), list), detail));
  const loadList = async () => {
    setParams({ q: q.value });
    const rows = await api(`/api/categories/species?q=${encodeURIComponent(q.value)}`);
    list.replaceChildren(
      !q.value ? h("small", { class: "muted" }, "Arter der ikke er kategori A, eller som har overstyrede serier. Søg for at finde andre.") : null,
      ...(rows.length ? rows.map(s => h("a", { href: "#", class: "row between", style: { gap: "6px" }, onclick: (e) => { e.preventDefault(); setParams({ art: s.artnr, y: s.kategori === "A" && !s.n_overrides ? new Date().getFullYear() : "" }); loadDetail(); } },
        h("span", {}, s.navn, s.n_overrides ? h("small", { class: "muted" }, ` · ${s.n_overrides} serie${s.n_overrides === 1 ? "" : "r"}`) : null), katTag(s.kategori))) : [empty("Ingen arter")]));
  };
  const loadDetail = async () => {
    const p = params();
    if (!p.art) { detail.replaceChildren(h("div", { class: "card" }, empty("Vælg en art", "Søg til venstre"))); return; }
    detail.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } }));
    let d;
    try { d = await api(`/api/categories/species/${p.art}${p.y ? `?year=${p.y}` : ""}`); } catch (err) { detail.replaceChildren(errorBox(err)); return; }
    const reload = () => { loadDetail(); loadList(); };
    const katSelect = (value, extra = []) => h("select", { style: { width: "auto" } }, ...extra, ...Object.entries(d.categories).map(([k, t]) => h("option", { value: k, selected: k === value }, `${k} – ${t}`)));
    const who = (r) => [r.created_by && `af ${r.created_by}`, fmtDate(r.created_at)].filter(Boolean).join(" ");
    const removed = (r) => r.removed_at ? h("small", { class: "muted" }, ` · fjernet ${fmtDate(r.removed_at)}${r.removed_by ? ` af ${r.removed_by}` : ""}`) : null;

    // Artens kategori over tid
    const kat = katSelect(d.current.kategori), from = h("input", { type: "date" }), note = h("input", { placeholder: "Note / kilde (valgfri)", maxlength: 1000 });
    const active = d.periods.filter(r => !r.removed_at);
    const periodRow = (r) => h("div", { class: `mod-item ${r.removed_at ? "hidden" : ""}` },
      h("div", {}, katTag(r.kategori), " ", h("b", {}, r.valid_from ? `fra ${fmtDate(r.valid_from)}` : "altid"),
        r.note ? h("div", { style: { fontSize: ".9rem" } }, r.note) : null, h("small", { class: "muted" }, who(r)), removed(r)),
      r.removed_at ? null : h("button", { class: "btn sm ghost danger", onclick: async () => {
        if (!confirm("Fjern kategorien? Den bevares i historikken.")) return;
        await api(`/api/categories/periods/${r.id}`, { method: "DELETE" }); toast("Fjernet"); reload();
      } }, "Fjern"));
    const periodsCard = h("div", { class: "card stack" },
      h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, d.navn, d.latin ? h("i", { class: "muted", style: { fontSize: "1rem", fontWeight: 400 } }, ` ${d.latin}`) : null),
        h("span", {}, katTag(d.current.kategori), h("small", { class: "muted" }, ` i dag (${SOURCE_TXT[d.current.source]})`))),
      h("h3", { style: { margin: 0 } }, "Artens kategori over tid"),
      ...(active.length ? active.map(periodRow) : [h("p", { class: "muted", style: { margin: 0 } }, d.current.source === "dofbasen" ? "Ingen kategori sat i portalen – arten tæller ikke, fordi DOFbasen har [klammer] om navnet." : "Ingen kategori sat – arten er kategori A.")]),
      h("form", { class: "row", onsubmit: async (e) => {
        e.preventDefault();
        try { await api(`/api/categories/species/${d.artnr}/periods`, { method: "POST", body: { kategori: kat.value, valid_from: from.value || null, note: note.value } }); toast("Gemt"); reload(); }
        catch (err) { toast(err.message, "err"); }
      } }, kat, h("label", { class: "row", style: { gap: "4px" } }, h("small", { class: "muted" }, "gyldig fra"), from), note, h("button", { class: "btn primary" }, "Tilføj")),
      h("small", { class: "muted" }, "Uden dato gælder kategorien altid. Med dato gælder den fra datoen, indtil en senere kategori tager over – sådan registreres en art der skifter kategori."),
      d.periods.some(r => r.removed_at) ? h("details", { class: "more" }, h("summary", {}, "Historik"), h("div", { class: "stack mt" }, ...d.periods.filter(r => r.removed_at).map(periodRow))) : null);

    // Serier: lokalitet × art
    const year = h("select", { style: { width: "auto" }, onchange: () => { setParams({ y: year.value }); loadDetail(); } },
      h("option", { value: "" }, "Alle år"), ...d.years.map(y => h("option", { value: y, selected: String(y) === String(d.year) }, y)));
    const setSeries = async (sr, k) => {
      const n = prompt(`Sæt kategori ${k} for ${d.navn} på ${sr.lok || sr.loknr} ${fmtDate(sr.first)}–${fmtDate(sr.last)}.\nNote / kilde (fx DKU-godkendelse):`, "");
      if (n === null) return false;
      try {
        await api("/api/categories/overrides", { method: "POST", body: { artnr: d.artnr, loknr: sr.loknr, date_from: sr.first, date_to: sr.last, kategori: k, note: n } });
        toast("Gemt"); reload(); return true;
      } catch (err) { toast(err.message, "err"); return false; }
    };
    const seriesCard = h("div", { class: "card stack" },
      h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Serier (lokalitet × art)"), year),
      h("small", { class: "muted" }, `Observationer på samme lokalitet hører til samme serie, indtil der går mere end ${d.gap_days} dage uden fund (som ved fund-aggregeringen). Viser højst 400, nyeste først.`),
      d.series.length ? h("div", { class: "table-wrap", style: { maxHeight: "520px" } }, h("table", { class: "data stack-sm", style: { "--cols": 3 } },
        h("thead", {}, h("tr", {}, h("th", {}, "Lokalitet"), h("th", {}, "Periode"), h("th", { class: "right" }, "Dage"), h("th", { class: "right" }, "Obs."), h("th", { class: "right" }, "Maks."), h("th", {}, "Observatører"), h("th", {}, "Kategori"))),
        h("tbody", {}, ...d.series.map(sr => {
          const sel = katSelect(null, [h("option", { value: "", selected: true }, "Sæt…")]);
          sel.style.height = "30px";
          sel.onchange = async () => { if (!await setSeries(sr, sel.value)) sel.value = ""; };
          return h("tr", {},
            h("td", {}, h("a", { href: `#/lok/${sr.loknr}` }, sr.lok || sr.loknr), sr.secret ? h("small", { class: "muted" }, " (hemmelig)") : null),
            h("td", { "data-label": "Periode", class: "nowrap" }, sr.first === sr.last ? fmtDate(sr.first) : `${fmtDate(sr.first)} – ${fmtDate(sr.last)}`),
            h("td", { class: "right num", "data-label": "Dage" }, sr.n_days), h("td", { class: "right num", "data-label": "Obs." }, sr.n_obs), h("td", { class: "right num", "data-label": "Maks." }, fmtN(sr.max_antal)),
            h("td", { class: "muted", "data-label": "Observatører", style: { fontSize: ".85rem" } }, sr.observers.join(", ")),
            h("td", { "data-label": "Kategori" }, h("span", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } }, ...sr.kategorier.map(k => katTag(k)), sel)));
        })))) : empty("Ingen observationer", d.year ? `i ${d.year}` : null));

    // Overstyrede serier
    const ovRow = (v) => h("div", { class: `mod-item ${v.removed_at ? "hidden" : ""}` },
      h("div", {}, katTag(v.kategori), " ", h("a", { href: `#/lok/${v.loknr}` }, v.lok || v.loknr), " ",
        h("span", { class: "nowrap" }, v.date_from === v.date_to ? fmtDate(v.date_from) : `${fmtDate(v.date_from)} – ${fmtDate(v.date_to)}`),
        v.note ? h("div", { style: { fontSize: ".9rem" } }, v.note) : null, h("small", { class: "muted" }, who(v)), removed(v)),
      v.removed_at ? null : h("button", { class: "btn sm ghost danger", onclick: async () => {
        if (!confirm("Fjern overstyringen? Serien får igen artens kategori. Overstyringen bevares i historikken.")) return;
        await api(`/api/categories/overrides/${v.id}`, { method: "DELETE" }); toast("Fjernet"); reload();
      } }, "Fjern"));
    const ovActive = d.overrides.filter(v => !v.removed_at), ovOld = d.overrides.filter(v => v.removed_at);
    const overridesCard = d.overrides.length ? h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Overstyrede serier"),
      ...(ovActive.length ? ovActive.map(ovRow) : [h("p", { class: "muted", style: { margin: 0 } }, "Ingen aktive overstyringer.")]),
      ovOld.length ? h("details", { class: "more" }, h("summary", {}, `Historik (${ovOld.length})`), h("div", { class: "stack mt" }, ...ovOld.map(ovRow))) : null) : null;

    detail.replaceChildren(periodsCard, overridesCard, seriesCard);
  };
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(loadList, 250); };
  await Promise.all([loadList(), loadDetail()]);
}

/** Manuelle fund (fx godkendte fund til havs): tæller i Arter pr. år og på lokaliteten, men ikke på personlige lister. */
async function manualFinds(box) {
  const out = h("div", { class: "stack mt" });
  box.append(out);
  let all = [];
  const load = async () => {
    let d;
    try { d = await api("/api/categories/finds"); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    out.replaceChildren(
      h("p", { class: "muted", style: { margin: 0 } }, "Fundet tæller som en almindelig observation i Arter pr. år, på lokaliteten og på artens side – men ikke på nogens personlige liste. Kategorien følger artens kategori som for andre fund. Synkroniseringen fra DOFbasen rører det ikke."),
      d.can_edit ? findForm(load) : h("small", { class: "muted" }, "Kun administratorer kan indsætte og slette fund."),
      d.items.length ? h("div", { class: "table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 2 } },
        h("thead", {}, h("tr", {}, h("th", {}, "Dato"), h("th", {}, "Art"), h("th", { class: "right" }, "Antal"), h("th", {}, "Sted"), h("th", {}, "Note"), h("th", {}))),
        h("tbody", {}, ...d.items.map(f => h("tr", {},
          h("td", { class: "nowrap" }, fmtDate(f.dato)), h("td", {}, h("b", {}, f.art)), h("td", { class: "right num", "data-label": "Antal" }, fmtN(f.antal)),
          h("td", { "data-label": "Sted" }, f.lok ? h("a", { href: `#/lok/${f.loknr}` }, f.lok) : null, f.lok && f.sted ? " · " : null, f.sted || null),
          h("td", { class: "muted", "data-label": "Note" }, f.note || "", h("small", { style: { display: "block" } }, `${f.created_by || "Indlæst automatisk"} · ${fmtDate(f.created_at)}`)),
          h("td", { class: "right" }, d.can_edit ? h("button", { class: "btn sm ghost danger", onclick: async () => {
            if (!confirm(`Slet fundet af ${f.art} ${fmtDate(f.dato)}?`)) return;
            try { await api(`/api/categories/finds/${f.id}`, { method: "DELETE" }); toast("Fundet er slettet"); load(); } catch (err) { toast(err.message, "err"); }
          } }, "Slet") : null)))))) : h("small", { class: "muted" }, "Ingen manuelle fund endnu."));
  };
  const findForm = (onSaved) => {
    let artnr = null, loknr = null;
    const art = h("input", { type: "search", placeholder: "Søg art på artslisten…", autocomplete: "off", required: true });
    const artHits = h("div", { class: "suggest" });
    // Arterne fra hovedlisten (Moderation → Arter)
    let failed = false;
    const ready = api("/api/admin/species").then(l => { all = l; }, (err) => {
      failed = true;  // fejlen bliver stående i stedet for tomme forslag
      artHits.replaceChildren(h("div", { class: "notice err" }, `Artslisten kunne ikke hentes: ${err.message}`));
    });
    art.addEventListener("input", () => ready.then(() => {
      artnr = null;
      if (failed) return;
      artHits.replaceChildren(...searchSpecies(all, art.value).map(sp => h("button", { type: "button", onclick: () => { artnr = sp.artnr; art.value = sp.navn; artHits.replaceChildren(); } },
        h("span", {}, sp.navn), sp.latin ? h("i", { class: "muted" }, sp.latin) : null)));
    }));
    const lok = h("input", { type: "search", placeholder: "Søg lokalitet (valgfri)…", autocomplete: "off" });
    const lokHits = h("div", { class: "suggest" });
    let t;
    lok.addEventListener("input", () => {
      loknr = null; clearTimeout(t);
      if (lok.value.trim().length < 2) { lokHits.replaceChildren(); return; }
      t = setTimeout(async () => {
        const rows = await api(`/api/locations?q=${encodeURIComponent(lok.value.trim())}`).catch(() => []);
        lokHits.replaceChildren(...rows.slice(0, 8).map(l => h("button", { type: "button", onclick: () => { loknr = l.loknr; lok.value = l.navn; lokHits.replaceChildren(); } },
          h("span", {}, l.navn), l.kommune ? h("i", { class: "muted" }, l.kommune) : null)));
      }, 250);
    });
    const dato = h("input", { type: "date", required: true, max: new Date().toISOString().slice(0, 10) });
    const antal = h("input", { type: "number", min: 1, value: 1, required: true });
    const sted = h("input", { maxlength: 200, placeholder: "Fx '60 km VNV for Hanstholm' (hvis ingen lokalitet)" });
    const note = h("input", { maxlength: 2000, placeholder: "Kilde, fx 'Godkendt af SU 2023' (valgfri)" });
    return h("form", { class: "stack", style: { background: "var(--surface-2)", padding: "12px", borderRadius: "10px" }, onsubmit: async (e) => {
      e.preventDefault();
      if (!artnr) { toast("Vælg arten fra artslisten", "err"); return; }
      if (lok.value.trim() && !loknr) { toast("Vælg lokaliteten på listen – eller ryd feltet og skriv stedet", "err"); return; }
      try {
        await api("/api/categories/finds", { method: "POST", body: { artnr, dato: dato.value, antal: Number(antal.value), loknr, sted: sted.value || null, note: note.value || null } });
        toast("Fundet er lagt ind – Arter pr. år genberegnes i baggrunden"); onSaved();
      } catch (err) { toast(err.message, "err"); }
    } },
      h("div", { class: "form-grid two" },
        h("label", { class: "field" }, h("span", {}, "Art"), art, artHits),
        h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Dato"), dato), h("label", { class: "field" }, h("span", {}, "Antal"), antal))),
      h("div", { class: "form-grid two" },
        h("label", { class: "field" }, h("span", {}, "Lokalitet"), lok, lokHits),
        h("label", { class: "field" }, h("span", {}, "Sted (fritekst)"), sted)),
      h("label", { class: "field" }, h("span", {}, "Note"), note),
      h("div", {}, h("button", { class: "btn primary" }, "Indsæt fund")));
  };
  await load();
}

/** Rapport fra Den Danske Liste: hvad en import vil ændre, med knap til at gennemføre den. */
function renderListReport(box, r, done) {
  const out = box.querySelector(".stack");
  out.querySelector(".list-report")?.remove();
  const row = (c) => h("li", {}, h("b", {}, c.navn), h("i", { class: "muted" }, ` ${c.latin} `), katTag(c.kategori), c.valid_from ? h("small", { class: "muted" }, ` fra ${fmtDate(c.valid_from)}`) : null);
  const rep = h("div", { class: "list-report stack" },
    h("p", { style: { margin: 0 } }, h("b", {}, `${fmtN(r.matched)} af ${fmtN(r.of)} arter på listen`), ` findes i portalen. ${fmtN(r.n_changes)} ændringer: `,
      Object.entries(r.summary).sort().map(([k, n]) => `${n}× ${k}`).join(", ") || "ingen", `. ${fmtN(r.unchanged)} er allerede som på listen.`),
    r.conflicts.length ? h("div", { class: "notice warn" }, h("b", {}, `${r.conflicts.length} arter røres ikke, fordi en moderator har sat kategorien: `),
      r.conflicts.map(c => `${c.navn} (listen: ${c.liste}, portalen: ${c.portal.map(p => p.kategori).join("/")})`).join(", ")) : null,
    r.unmatched.length ? h("details", {}, h("summary", {}, `${r.n_unmatched} arter på listen findes ikke i portalen`),
      h("small", { class: "muted" }, "Typisk undsluppne arter, som DOFbasen aldrig har haft – vist er A–D og F: "), r.unmatched.map(u => `${u.navn} (${u.kategori})`).join(", ")) : null,
    r.bracket_off_list.length ? h("small", { class: "muted" }, `Står med [klammer] i DOFbasen, men ikke på listen (beholder kategori D): ${r.bracket_off_list.map(x => x.navn).join(", ")}`) : null,
    r.n_changes ? h("details", {}, h("summary", {}, `Vis ændringerne${r.n_changes > r.changes.length ? ` (de første ${r.changes.length})` : ""}`), h("ul", { class: "stack-sm", style: { columns: "2 260px" } }, ...r.changes.map(row))) : null,
    r.n_changes ? h("button", { class: "btn primary", style: { alignSelf: "flex-start" }, onclick: async (e) => {
      if (!confirm(`Læg ${r.n_changes} kategoriændringer ind fra Den Danske Liste?`)) return;
      e.currentTarget.disabled = true;
      try { const w = await api("/api/categories/danish-list", { method: "POST" }); toast(`${w.written} kategorier lagt ind`); rep.remove(); done(); }
      catch (err) { toast(err.message, "err"); e.currentTarget.disabled = false; }
    } }, `Læg ${fmtN(r.n_changes)} kategorier ind`) : h("p", { class: "muted", style: { margin: 0 } }, "Portalen følger allerede listen."));
  out.append(rep);
}

/** Opret en art med kategori (fx en undsluppen art, DOFbasen endnu ikke har på artslisten). */
function newSpeciesForm(navn, onCreated) {
  const f = {
    navn: h("input", { required: true, minlength: 2, maxlength: 200, value: navn || "", placeholder: "Fx Rødhalset Gås eller [Mandarinand]" }),
    latin: h("input", { maxlength: 200, placeholder: "Videnskabeligt navn (valgfrit)" }),
    artnr: h("input", { type: "number", min: 1, max: 899999, placeholder: "DOFbasens artsnummer (valgfrit)" }),
    kategori: h("select", {}, ...Object.entries({ A: "Vild fugl (krydsbar)", B: "Kun vild 1800–1949 (krydsbar)", C: "Indført, selvopretholdende (krydsbar)",
      D: "Mulig vild – tvivl om oprindelse (ikke krydsbar)", E: "Undsluppet/udsat (ikke krydsbar)", F: "Kun registreret før 1800 (ikke krydsbar)" }).map(([k, t]) => h("option", { value: k, selected: k === "E" }, `${k} – ${t}`))),
    from: h("input", { type: "date" }),
    note: h("input", { maxlength: 1000, placeholder: "Note / kilde (valgfri)" }),
  };
  const field = (label, el, help) => h("label", { class: "field" }, h("span", {}, label), el, help ? h("small", { class: "muted" }, help) : null);
  const dlg = modal("Opret art", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/categories/species", { method: "POST", body: {
        navn: f.navn.value, latin: f.latin.value || null, artnr: f.artnr.value ? Number(f.artnr.value) : null,
        kategori: f.kategori.value, valid_from: f.from.value || null, note: f.note.value || null } });
      toast("Arten er oprettet"); dlg.close(); onCreated(r.artnr);
    } catch (err) { toast(err.message, "err"); }
  } },
    field("Navn", f.navn), field("Latin", f.latin),
    field("Artsnummer i DOFbasen", f.artnr, "Med DOFbasens nummer kobles arten på observationerne, så snart de hentes. Uden nummer får arten et portalnummer og har ingen observationer."),
    h("div", { class: "form-grid two" }, field("Kategori", f.kategori), field("Gyldig fra (valgfri)", f.from)),
    field("Note", f.note),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Opret"), h("button", { type: "button", class: "btn ghost", onclick: () => dlg.close() }, "Annullér"))));
  f.navn.focus();
}

// ---------------------------------------------------------------- datapaneler (DOFbasen og vejrarkiv)
