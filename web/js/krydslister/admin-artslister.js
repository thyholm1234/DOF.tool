// Admin → Artslister: ret arterne på en artsliste (DK, VP …). Tilføj fra World Bird List eller som ny art (fx en split, der ikke er i AviList),
// ret dansk/engelsk navn, * (SU), sjældenhed, kategorier og placering, fjern; ny liste (evt. som kopi af en anden); CSV-eksport og -import med diff.
import { api, h, toast, modal, fmtN, icon } from "../core.js";
import { loadingBox, taxonPicker } from "./common.js";

const SJ = [["almindelig", "Almindelig"], ["relativ_almindelig", "Relativ almindelig"], ["ualmindelig", "Ualmindelig"], ["sjaelden", "Sjælden"], ["su", "SU / * (sjælden)"]];
const SJ_TXT = Object.fromEntries(SJ);
const STEP = 100;

export async function page(parts, q) {
  let lists = await api("/api/krydslister/artslister");
  let cur = lists.find(l => l.key === q.liste) || lists[0];
  const st = { q: "", filter: "", shown: STEP };
  const top = h("div", { class: "kl-tools" }), body = h("div", { class: "stack" }, loadingBox());
  let rows = [];

  async function reload(keepFilter = true) {
    lists = await api("/api/krydslister/artslister");
    cur = lists.find(l => l.id === cur.id) || lists[0];
    if (!keepFilter) Object.assign(st, { q: "", filter: "", shown: STEP });
    await load();
  }

  async function load() {
    history.replaceState(history.state, "", `#/lister/admin/artslister?liste=${cur.key}`);
    top.replaceChildren(
      h("select", { "aria-label": "Artsliste", onchange: (e) => { cur = lists.find(l => l.key === e.target.value); Object.assign(st, { q: "", filter: "", shown: STEP }); load(); } },
        ...lists.map(l => h("option", { value: l.key, selected: l.key === cur.key }, `${l.navn} (${fmtN(l.antal)})`))),
      h("button", { class: "btn sm", type: "button", onclick: newList }, "+ Ny artsliste"), h("span", { class: "spacer" }),
      h("a", { class: "btn sm", href: `/api/admin/krydslister/artslister/${cur.id}/csv`, download: `${cur.key}.csv` }, "CSV-eksport"), importBtn());
    body.replaceChildren(loadingBox());
    rows = (await api(`/api/krydslister/artslister/${cur.key}`)).rows;
    draw();
  }

  function draw() {
    const hasCats = cur.key === "dk" || rows.some(r => r.kategorier.length);  // kategorier A–F hører til DK-listen; VP-listen bruger * (SU)
    let picked = null;
    const add = h("div", { class: "card stack" }, h("b", {}, "Tilføj art"),
      h("div", { class: "kl-tools" }, taxonPicker({ placeholder: "Søg i World Bird List …", onPick: (r) => { picked = r; } }),
        h("button", { class: "btn sm primary", type: "button", onclick: async () => {
          if (!picked) { toast("Vælg en art på listen", "err"); return; }
          if (rows.some(r => r.id === picked.id)) { toast(`${picked.navn} er allerede på listen`, "err"); return; }
          try { await api(`/api/admin/krydslister/artslister/${cur.id}/arter/${picked.id}`, { method: "PUT", body: { kategorier: [], sjaeldenhed: "almindelig" } }); toast(`${picked.navn} er tilføjet sidst på listen`); reload(); }
          catch (e) { toast(e.message, "err"); } } }, "Tilføj"),
        h("span", { class: "muted small" }, "eller"),
        h("button", { class: "btn sm", type: "button", onclick: () => edit(null, hasCats) }, "+ Ny art (findes ikke i World Bird List)")));
    const search = h("input", { type: "search", placeholder: "Søg navn, latin eller engelsk …", value: st.q, "aria-label": "Søg på listen" });
    const filter = h("select", { "aria-label": "Filter" }, h("option", { value: "" }, "Alle arter"), h("option", { value: "su", selected: st.filter === "su" }, "Kun * (SU)"),
      h("option", { value: "uden", selected: st.filter === "uden" }, "Uden dansk navn"), h("option", { value: "udenartnr", selected: st.filter === "udenartnr" }, "Ikke koblet til DOFbasen"));
    const tbody = h("tbody"), more = h("div", { class: "row mt", style: { justifyContent: "center" } }), count = h("span", { class: "muted small" });
    const drawRows = () => {
      const f = st.q.trim().toLowerCase();
      const hits = rows.map((r, i) => ({ r, i })).filter(({ r }) => (!f || `${r.navn} ${r.latin} ${r.engelsk || ""}`.toLowerCase().includes(f))
        && (st.filter !== "su" || r.su) && (st.filter !== "uden" || r.navn === r.latin || r.navn === r.engelsk) && (st.filter !== "udenartnr" || !r.artnr));
      count.textContent = `${fmtN(hits.length)} af ${fmtN(rows.length)} arter`;
      tbody.replaceChildren(...hits.slice(0, st.shown).map(({ r, i }) => h("tr", {},
        h("td", { class: "num muted" }, i + 1),
        h("td", { class: "kl-su" }, r.su ? h("b", { title: "SU / sjælden" }, "*") : ""),
        h("td", {}, h("b", {}, r.navn), r.engelsk && r.engelsk !== r.navn ? h("div", { class: "muted small" }, r.engelsk) : null,
          h("div", { class: "muted small kl-only-sm" }, h("i", {}, r.latin), hasCats && r.kategorier.length ? ` · ${r.kategorier.join(", ")}` : "", r.artnr ? "" : " · ikke koblet")),
        h("td", { class: "kl-hide-sm" }, h("i", {}, r.latin), r.artnr ? null : h("div", { class: "muted small" }, "ikke koblet til DOFbasen")),
        hasCats ? h("td", { class: "kl-hide-sm" }, r.kategorier.join(", ")) : null,
        h("td", { class: "small kl-hide-sm" }, SJ_TXT[r.sjaeldenhed] || r.sjaeldenhed),
        h("td", { class: "nowrap right" },
          h("button", { class: "btn sm", type: "button", "aria-label": `Ret ${r.navn}`, onclick: () => edit({ ...r, placering: i + 1 }, hasCats), html: `${icon("edit")}<span class="kl-hide-sm"> Ret</span>` }), " ",
          h("button", { class: "btn sm ghost danger", type: "button", "aria-label": `Fjern ${r.navn}`, html: icon("trash"), onclick: async () => {
            if (!confirm(`Fjern ${r.navn} fra ${cur.navn}?`)) return;
            try { await api(`/api/admin/krydslister/artslister/${cur.id}/arter/${r.id}`, { method: "DELETE" }); toast("Fjernet"); reload(); } catch (e) { toast(e.message, "err"); } } })))));
      more.replaceChildren(hits.length > st.shown ? h("button", { class: "btn", type: "button", onclick: () => { st.shown += STEP * 3; drawRows(); } }, `Vis flere (${fmtN(hits.length - st.shown)} tilbage)`) : "");
    };
    let t;
    search.oninput = () => { clearTimeout(t); t = setTimeout(() => { st.q = search.value; st.shown = STEP; drawRows(); }, 150); };
    filter.onchange = () => { st.filter = filter.value; st.shown = STEP; drawRows(); };
    drawRows();
    body.replaceChildren(cur.beskrivelse ? h("p", { class: "muted" }, cur.beskrivelse) : null, add,
      h("div", { class: "card" }, h("div", { class: "kl-tools" }, search, filter, h("span", { class: "spacer" }), count),
        h("div", { class: "kl-scroll" }, h("table", { class: "kl-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", { title: "SU / sjælden" }, "*"), h("th", {}, "Navn"), h("th", { class: "kl-hide-sm" }, "Latinsk navn"), hasCats ? h("th", { class: "kl-hide-sm" }, "Kategorier") : null,
            h("th", { class: "kl-hide-sm" }, "Sjældenhed"), h("th", {}, ""))), tbody)), more),
      h("p", { class: "muted small" }, "Navnene gælder arten overalt (fx både på DK- og VP-listen). En ændring slår igennem på krydslisterne med det samme."));
  }

  /** Ret en art (r) eller opret en ny (r = null). */
  function edit(r, hasCats) {
    const f = { latin: r?.latin || "", dansk: r ? (r.navn !== r.latin && r.navn !== r.engelsk ? r.navn : "") : "", engelsk: r?.engelsk || "",
      sjaeldenhed: r?.sjaeldenhed || "almindelig", kategorier: (r?.kategorier || []).join(", "), placering: r?.placering || "" };
    const inp = (k, a = {}) => h("input", { type: "text", value: f[k], oninput: (e) => { f[k] = e.target.value; }, ...a });
    const field = (label, el, hint) => h("label", { class: "field" }, h("span", {}, label), el, hint ? h("small", { class: "muted" }, hint) : null);
    const m = modal(r ? `Ret ${r.navn}` : `Ny art på ${cur.navn}`, h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      const body = { dansk: f.dansk.trim(), engelsk: f.engelsk.trim(), sjaeldenhed: f.sjaeldenhed, kategorier: f.kategorier.toUpperCase().split(/[,\s]+/).filter(Boolean),
        placering: +f.placering && (!r || +f.placering !== r.placering) ? +f.placering : null };
      try {
        if (r) await api(`/api/admin/krydslister/artslister/${cur.id}/arter/${r.id}`, { method: "PUT", body });
        else await api(`/api/admin/krydslister/artslister/${cur.id}/ny-art`, { method: "POST", body: { ...body, latin: f.latin.trim() } });
        toast(r ? "Gemt" : "Arten er tilføjet"); m.close(); reload();
      } catch (x) { toast(x.message, "err"); }
    } },
      r ? h("p", { class: "muted", style: { margin: 0 } }, h("i", {}, r.latin)) : field("Latinsk navn", inp("latin", { required: true, placeholder: "fx Saxicola maurus" }),
        "Bruges en art, der allerede findes i World Bird List, genbruges den."),
      h("div", { class: "kl-form" }, field("Dansk navn", inp("dansk")), field("Engelsk navn", inp("engelsk"))),
      h("div", { class: "kl-form" },
        field("Sjældenhed", h("select", { onchange: (e) => { f.sjaeldenhed = e.target.value; } }, ...SJ.map(([v, t]) => h("option", { value: v, selected: v === f.sjaeldenhed }, t)))),
        hasCats ? field("Kategorier (A–F)", inp("kategorier", { placeholder: "fx A, C", maxlength: 20 })) : null,
        field("Placering på listen", inp("placering", { type: "number", min: 1, max: rows.length + 1, placeholder: r ? "" : "sidst" }), `1–${fmtN(rows.length + (r ? 0 : 1))}`)),
      h("p", { class: "muted small", style: { margin: 0 } }, "SU / * betyder, at manuelle kryds af arten kræver dato og lokalitet (på VP-listen også land)."),
      h("div", { class: "row" }, h("button", { class: "btn primary" }, r ? "Gem" : "Tilføj art"), h("button", { class: "btn", type: "button", onclick: () => m.close() }, "Annullér"))));
  }

  function importBtn() {
    const file = h("input", { type: "file", accept: ".csv,text/csv", hidden: true, onchange: async () => {
      const send = async (apply) => { const fd = new FormData(); fd.append("file", file.files[0]); fd.append("anvend", apply ? "true" : "false"); return api(`/api/admin/krydslister/artslister/${cur.id}/csv`, { method: "POST", form: fd }); };
      try {
        const d = await send(false);
        const txt = `Nye: ${d.nye.length} · Ændrede: ${d.aendrede.length} · Uændrede: ${d.uaendret} · Ukendte arter: ${d.ukendte.length} · Mangler i filen (røres ikke): ${d.mangler_i_filen.length}\n${d.fejl.join("\n")}${d.ukendte.length ? `\nUkendte (tilføj dem med «Ny art»): ${d.ukendte.slice(0, 10).join(", ")}${d.ukendte.length > 10 ? " …" : ""}` : ""}\n\nAnvend ændringerne?`;
        if ((d.nye.length || d.aendrede.length) && confirm(txt)) { await send(true); toast("Importeret"); reload(); } else if (!d.nye.length && !d.aendrede.length) alert(txt.replace("\n\nAnvend ændringerne?", "\n\nIntet at ændre."));
      } catch (e) { toast(e.message, "err"); }
      file.value = "";
    } });
    return h("span", {}, file, h("button", { class: "btn sm", type: "button", onclick: () => file.click() }, "CSV-import (med diff)"));
  }

  function newList() {
    const f = { key: "", navn: "", kopier_fra: "" };
    const m = modal("Ny artsliste", h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const r = await api("/api/admin/krydslister/artslister", { method: "POST", body: { key: f.key.trim().toLowerCase(), navn: f.navn.trim(), kopier_fra: f.kopier_fra ? +f.kopier_fra : null } });
        m.close(); toast("Artslisten er oprettet"); cur = { id: r.id }; await reload(false);
      } catch (x) { toast(x.message, "err"); }
    } },
      h("label", { class: "field" }, h("span", {}, "Nøgle (små bogstaver, fx falskvp)"), h("input", { required: true, pattern: "[a-z][a-z0-9_]{0,23}", oninput: (e) => { f.key = e.target.value; } })),
      h("label", { class: "field" }, h("span", {}, "Navn"), h("input", { required: true, oninput: (e) => { f.navn = e.target.value; } })),
      h("label", { class: "field" }, h("span", {}, "Start med arterne fra"), h("select", { onchange: (e) => { f.kopier_fra = e.target.value; } },
        h("option", { value: "" }, "Tom liste"), ...lists.map(l => h("option", { value: l.id }, `${l.navn} (${fmtN(l.antal)})`)))),
      h("p", { class: "muted small", style: { margin: 0 } }, "En listetype bruger en artsliste som reference (Admin → Listetyper). Fx kan Falsk VP få sin egen liste som kopi af VP-listen."),
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Opret"), h("button", { class: "btn", type: "button", onclick: () => m.close() }, "Annullér"))));
  }

  await load();
  return h("div", { class: "stack" }, top, body);
}
