// Søgning: fritekst (dansk stemming) eller præcis råtekst (også delord og OCR-tekst fra PDF'er), emneord, arter, år, type og sortering.
// Tilstanden ligger i adressen, så en søgning kan deles. results() bruges også af emneords- og artssiderne.
import { api, h, setParams, seg } from "../core.js";
import { BASE, listCard, pager, loadingBox, nowrap, notice } from "./common.js";

const SORTS = [["relevans", "Relevans"], ["nyeste", "Nyeste"], ["aeldste", "Ældste"], ["mest_laest", "Mest læst"], ["mest_kommenteret", "Mest kommenteret"]];
const list = (v) => String(v || "").split(",").filter(Boolean).map(Number).filter(Number.isFinite);

export function readState(q) {
  return { q: q.q || "", mode: q.mode === "praecis" ? "praecis" : "ord", tag: list(q.tag), art: list(q.art), forfatter: q.forfatter || "", fra: q.fra || "", til: q.til || "",
    type: q.type || "", billeder: q.billeder === "1", pdf: q.pdf === "1", sort: q.sort || "", side: +q.side || 1 };
}
export function toQuery(st, fixed = {}) {
  const p = new URLSearchParams();
  const s = { ...st, ...fixed };
  if (s.q) p.set("q", s.q);
  if (s.mode === "praecis") p.set("mode", "praecis");
  s.tag.forEach(t => p.append("tag", t));
  s.art.forEach(a => p.append("art", a));
  if (s.forfatter) p.set("forfatter", s.forfatter);
  if (s.fra) p.set("fra", s.fra);
  if (s.til) p.set("til", s.til);
  if (s.type) p.set("type", s.type);
  if (s.billeder) p.set("har_billeder", "true");
  if (s.pdf) p.set("har_pdf", "true");
  if (s.sort) p.set("sort", s.sort);
  p.set("side", s.side);
  return p;
}

/** Resultatliste med antal, kort og sideskift. fixed: filtre der ikke kan ændres (fx art eller emneord på deres egne sider). */
export function results(st, { fixed = {}, onState = () => {}, empty = "Ingen artikler matcher." } = {}) {
  const box = h("div", {});
  const load = async () => {
    box.replaceChildren(loadingBox());
    let d;
    try { d = await api("/api/artikler?" + toQuery(st, fixed)); } catch (e) { box.replaceChildren(notice(e.message, "err")); return; }
    onState(d);
    box.replaceChildren(
      h("div", { class: "art-count" }, `${nowrap(d.total)} ${d.total === 1 ? "artikel" : "artikler"}`),
      d.total ? h("div", {}, ...d.rows.map(listCard)) : h("p", { class: "muted", style: { padding: "20px 0" } }, empty),
      pager(d.page, Math.ceil(d.total / d.per), (p) => { st.side = p; setParams({ side: p > 1 ? p : "" }); load(); window.scrollTo({ top: 0 }); }));
  };
  load();
  return { el: box, reload: load };
}

export async function page(parts, query) {
  const st = readState(query);
  const [tags, species] = await Promise.all([api("/api/artikler/emneord"), api("/api/artikler/arter")]);
  const tagById = new Map(tags.map(t => [t.id, t])), artById = new Map(species.map(s => [s.artnr, s]));
  const types = tags.filter(t => t.kind === "type").map(t => t.navn);
  const out = h("div", {}), chips = h("div", { class: "art-chips", style: { margin: "6px 0" } });
  let res;
  const sync = () => {
    setParams({ q: st.q, mode: st.mode === "praecis" ? "praecis" : "", tag: st.tag.join(","), art: st.art.join(","), forfatter: st.forfatter, fra: st.fra, til: st.til,
      type: st.type, billeder: st.billeder ? "1" : "", pdf: st.pdf ? "1" : "", sort: st.sort, side: st.side > 1 ? st.side : "" });
    drawChips();
    res = results(st, { onState: d => { years.dataset.n = d.facets.aar.length; } });
    out.replaceChildren(res.el);
  };
  const run = () => { st.side = 1; sync(); };
  const drawChips = () => chips.replaceChildren(
    ...st.tag.map(id => h("button", { class: "art-chip", type: "button", title: "Fjern", onclick: () => { st.tag = st.tag.filter(x => x !== id); run(); } }, `${tagById.get(id)?.navn || "emneord " + id} ×`)),
    ...st.art.map(n => h("button", { class: "art-chip art", type: "button", title: "Fjern", onclick: () => { st.art = st.art.filter(x => x !== n); run(); } }, `${artById.get(n)?.navn || "art " + n} ×`)),
    st.forfatter ? h("button", { class: "art-chip", type: "button", onclick: () => { st.forfatter = ""; run(); } }, `Forfatter: ${st.forfatter} ×`) : null);

  const q = h("input", { type: "search", value: st.q, placeholder: "Ord eller tekst …", "aria-label": "Søgetekst", style: { flex: 1, minWidth: "200px" } });
  const years = h("span");
  const num = (key, ph) => h("input", { type: "number", min: 1900, max: 2100, placeholder: ph, value: st[key], style: { width: "100%" }, onchange: (e) => { st[key] = e.target.value; run(); } });
  const pick = (what, items, label, onPick) => {
    const dl = "dl-" + what, inp = h("input", { list: dl, placeholder: label, autocomplete: "off", style: { width: "100%" } });
    inp.addEventListener("change", () => { const m = items.find(i => i.label.toLowerCase() === inp.value.trim().toLowerCase()); if (m) { onPick(m.id); inp.value = ""; run(); } });
    return h("div", {}, inp, h("datalist", { id: dl }, ...items.map(i => h("option", { value: i.label }))));
  };
  const form = h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); st.q = q.value.trim(); run(); } },
    h("div", { class: "row" }, q, h("button", { class: "btn primary", type: "submit" }, "Søg"),
      seg([["ord", "Ord"], ["praecis", "Præcis tekst"]], st.mode, (v) => { st.mode = v; st.q = q.value.trim(); run(); })),
    h("p", { class: "muted small", style: { margin: 0 } }, st.mode === "praecis"
      ? "Præcis tekst finder præcis det, du skriver – også inde i ord og i PDF-tekst. Ingen bøjninger."
      : 'Ord finder også bøjninger (hjejle/hjejler). Brug "citationstegn" til en frase, - foran et ord for at udelukke det.'),
    h("div", { class: "art-filters" },
      pick("tag", tags.filter(t => !t.hidden).map(t => ({ id: t.id, label: t.navn })), "Tilføj emneord …", id => { if (!st.tag.includes(id)) st.tag.push(id); }),
      pick("art", species.map(s => ({ id: s.artnr, label: s.navn })), "Tilføj art …", id => { if (!st.art.includes(id)) st.art.push(id); }),
      h("select", { "aria-label": "Type", onchange: (e) => { st.type = e.target.value; run(); } }, h("option", { value: "" }, "Alle typer"), ...types.map(t => h("option", { value: t, selected: t === st.type }, t))),
      num("fra", "Fra år"), num("til", "Til år"),
      h("select", { "aria-label": "Sortering", onchange: (e) => { st.sort = e.target.value; run(); } }, ...SORTS.map(([v, t]) => h("option", { value: v, selected: v === (st.sort || (st.q && st.mode === "ord" ? "relevans" : "nyeste")) }, t)))),
    h("div", { class: "row" },
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: st.billeder, onchange: (e) => { st.billeder = e.target.checked; run(); } }), h("span", {}, "Kun med billeder")),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: st.pdf, onchange: (e) => { st.pdf = e.target.checked; run(); } }), h("span", {}, "Kun med PDF"))),
    chips);
  drawChips();
  sync();
  return h("div", {}, form, out);
}
