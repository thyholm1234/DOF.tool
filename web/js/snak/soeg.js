// Søg (#/snak/soeg?q=): fritekst i titel, tekst og kommentarer med filtre (docs/snak-modul-spec.md §2.5).
import { api, ago, empty, errorBox, h, params, seg, setParams, state } from "../core.js";
import { BASE, categoryTag, loadCss, loadingBox } from "./common.js";
import { searchPicker } from "./composer.js";

const PERIODS = [["", "Alle"], ["7", "7 dage"], ["30", "30 dage"], ["365", "1 år"]];

/** Træfordene er omgivet af \x02 … \x03 fra serveren; <mark> bygges her, så brugerens tekst aldrig tolkes som HTML. */
export function highlight(text = "") {
  return text.split("\x02").flatMap((part, i) => {
    if (i === 0) return [part];
    const [hit, rest = ""] = part.split("\x03");
    return [h("mark", {}, hit), rest];
  });
}

export async function page() {
  loadCss("snak");
  const meta = await api("/api/snak/kategorier");
  const p = params();
  const f = { q: p.q || "", kategori: p.k || "", dage: p.d || "", uloeste: p.u === "1", billeder: p.b === "1", art: p.a ? { artnr: +p.a, navn: p.an || `Art ${p.a}` } : null };
  const results = h("div", { class: "sn-list" });
  const more = h("button", { type: "button", class: "btn", hidden: true }, "Vis flere");
  let offset = 0, gen = 0;

  const q = h("input", { type: "search", value: f.q, placeholder: "Søg i opslag og kommentarer …", "aria-label": "Søg i Snak", autofocus: true });
  const cat = h("select", { "aria-label": "Kategori", onchange: () => { f.kategori = cat.value; run(true); } }, h("option", { value: "" }, "Alle kategorier"),
    ...meta.categories.map(c => h("option", { value: c.slug, selected: c.slug === f.kategori }, c.navn)));
  const artBox = h("span", { class: "row" });
  const drawArt = () => artBox.replaceChildren(f.art
    ? h("span", { class: "sn-chip" }, "🐦 ", f.art.navn, " ", h("button", { type: "button", class: "sn-x", "aria-label": `Fjern ${f.art.navn}`, onclick: () => { f.art = null; drawArt(); run(true); } }, "✕"))
    : h("button", { type: "button", class: "btn sm", onclick: () => searchPicker("art", "Filtrér på art", (r) => { f.art = { artnr: +r.ref, navn: r.title }; drawArt(); run(true); }) }, "🐦 Art"));
  drawArt();

  async function run(reset) {
    if (reset) { offset = 0; ++gen; results.replaceChildren(); }
    const my = gen;
    setParams({ q: f.q, k: f.kategori, d: f.dage, u: f.uloeste ? "1" : "", b: f.billeder ? "1" : "", a: f.art?.artnr || "", an: f.art?.navn || "" });
    if (f.q.trim().length < 2) { more.hidden = true; results.replaceChildren(h("p", { class: "muted" }, "Skriv mindst to bogstaver.")); return; }
    if (reset) results.replaceChildren(loadingBox());
    try {
      const qs = new URLSearchParams({ q: f.q, offset });
      if (f.kategori) qs.set("kategori", f.kategori);
      if (f.dage) qs.set("dage", f.dage);
      if (f.uloeste) qs.set("uloeste", "true");
      if (f.billeder) qs.set("billeder", "true");
      if (f.art) qs.set("art", f.art.artnr);
      const d = await api(`/api/snak/soeg?${qs}`);
      if (my !== gen) return;
      if (reset) results.replaceChildren();
      results.append(...d.rows.map(r => h("article", { class: "sn-card sn-hit" },
        h("div", { class: "row" }, categoryTag({ slug: r.slug, navn: r.kategori, farve: r.farve }), h("span", { class: "muted small" }, `${r.kind === "comment" ? "Kommentar" : "Opslag"} af ${r.navn || "?"} · ${ago(r.created_at)}`)),
        h("a", { class: "sn-title", href: `${BASE}/${r.post_id}${r.comment_id ? `?k=${r.comment_id}` : ""}` }, r.titel || (r.kind === "comment" ? "Kommentar i et opslag" : "Opslag")),
        h("p", { class: "sn-snip" }, ...highlight(r.uddrag)))));
      if (!results.children.length) results.replaceChildren(empty("Ingen træf", "Prøv færre eller andre ord."));
      offset += d.rows.length;
      more.hidden = !d.more;
    } catch (err) { if (my === gen) results.replaceChildren(errorBox(err)); }
  }
  more.onclick = () => run(false);
  q.addEventListener("input", (() => { let t; return () => { clearTimeout(t); t = setTimeout(() => { f.q = q.value; run(true); }, 300); }; })());
  document.title = "Søg – Snak";
  const root = h("div", { class: "sn-feed stack" }, h("h1", {}, "Søg i Snak"),
    h("form", { class: "row", role: "search", onsubmit: (e) => { e.preventDefault(); f.q = q.value; run(true); } }, q, h("button", { class: "btn primary", type: "submit" }, "Søg")),
    h("div", { class: "row" }, cat, seg(PERIODS, f.dage, (v) => { f.dage = v; run(true); }), artBox,
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: f.uloeste, onchange: (e) => { f.uloeste = e.target.checked; run(true); } }), h("span", {}, "Kun uløste")),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: f.billeder, onchange: (e) => { f.billeder = e.target.checked; run(true); } }), h("span", {}, "Med billeder"))),
    results, h("div", { class: "row", style: { justifyContent: "center" } }, more));
  if (state.user) run(true);
  return root;
}
