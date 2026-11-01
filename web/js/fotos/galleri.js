// Galleriet: fritekst, avanceret søgning (art, dato, måned, fotograf, land, lokalitet, køn/alder/dragt/adfærd, emneord), sortering og sider.
// Alle filtre ligger i adressen, så Tilbage og deling virker (rulleposition gemmes af skallen).
import { api, h, setParams, searchSpecies, seg } from "../core.js";
import { BASE, loadingBox, notice, nowrap, pager, photoCard } from "./common.js";

const SORTS = [["dato", "Fotodato"], ["nyeste", "Senest tilføjet"], ["likes", "Flest likes"], ["kommenteret", "Senest kommenteret"], ["set", "Set af flest"]];
const FILTERS = ["q", "fra", "til", "md", "art", "fotograf", "land", "lok", "loknr", "sj", "koen", "alder", "dragt", "adf", "tag"];
const MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];
const SJ = [["almindelig", "Almindelig"], ["relativ_almindelig", "Relativ almindelig"], ["ualmindelig", "Ualmindelig"], ["sjaelden", "Sjælden"], ["su", "SU"]];

function readState(q, fixed = {}) {
  const st = { sort: q.sort || "dato", per: [24, 48, 96].includes(+q.per) ? +q.per : 24, side: +q.side || 1 };
  for (const k of FILTERS) st[k] = fixed[k] ?? q[k] ?? "";
  return st;
}
const toQuery = (st) => {
  const p = new URLSearchParams();
  for (const k of FILTERS) if (st[k]) p.set(k, st[k]);
  if (st.sort !== "dato") p.set("sort", st.sort);
  if (st.per !== 24) p.set("per", st.per);
  p.set("side", st.side);
  return p;
};

export async function page(parts, query, fixed = {}) {
  const st = readState(query, fixed);
  const [codes, banner] = await Promise.all([api("/api/fotos/koder"), api("/api/fotos/banner").catch(() => null)]);
  let species = null;
  const getSpecies = async () => species ||= await api("/api/species/index").catch(() => []);
  const out = h("div", {});
  const q = h("input", { type: "search", value: st.q, placeholder: "Søg i titel, lokalitet, beskrivelse, fotograf eller art …", "aria-label": "Søg i fotos", style: { flex: 1, minWidth: "220px" } });
  let advOpen = FILTERS.some(k => k !== "q" && st[k]);

  async function load() {
    out.replaceChildren(loadingBox());
    let d;
    try { d = await api("/api/fotos?" + toQuery(st)); } catch (e) { out.replaceChildren(notice(e.message, "err")); return; }
    const pages = Math.ceil(d.total / d.per);
    out.replaceChildren(
      h("div", { class: "fot-count" }, `${nowrap(d.total)} ${d.total === 1 ? "foto" : "fotos"}`),
      d.total ? h("div", { class: "fot-grid" }, ...d.rows.map(r => photoCard(r))) : h("p", { class: "muted", style: { padding: "20px 0" } }, "Ingen fotos matcher."),
      pager(d.page, pages, (p) => { st.side = p; sync(); window.scrollTo({ top: 0 }); }));
  }
  const sync = () => {
    setParams({ ...Object.fromEntries(FILTERS.map(k => [k, fixed[k] ? "" : st[k]])), sort: st.sort === "dato" ? "" : st.sort, per: st.per === 24 ? "" : st.per, side: st.side > 1 ? st.side : "" });
    load();
  };
  const run = () => { st.side = 1; sync(); };

  const select = (key, label, options, { multi = false } = {}) => h("label", { class: "field" }, h("span", {}, label),
    h("select", { onchange: (e) => { st[key] = e.target.value; run(); } }, h("option", { value: "" }, "Alle"), ...options.map(([v, t]) => h("option", { value: v, selected: String(st[key]) === String(v) }, t))));
  const date = (key, label) => h("label", { class: "field" }, h("span", {}, label), h("input", { type: "date", value: st[key], onchange: (e) => { st[key] = e.target.value; run(); } }));
  const text = (key, label, ph) => h("label", { class: "field" }, h("span", {}, label), h("input", { type: "text", value: st[key], placeholder: ph || "", onchange: (e) => { st[key] = e.target.value.trim(); run(); } }));

  const artBox = h("div", { class: "field" }), artList = h("div", { class: "fot-suggest" });
  const artInput = h("input", { type: "search", placeholder: "Art eller race …", autocomplete: "off" });
  const artName = h("small", { class: "muted" }, st.art ? `Art #${st.art}` : "");
  artInput.oninput = async () => {
    const hits = searchSpecies(await getSpecies(), artInput.value, 8);
    artList.replaceChildren(...hits.map(s => h("button", { type: "button", onclick: () => {
      st.art = String(s.artnr); artName.textContent = s.navn; artInput.value = ""; artList.replaceChildren(); run();
    } }, s.navn, h("small", { class: "muted" }, s.latin ? ` · ${s.latin}` : ""))));
  };
  artBox.append(h("span", {}, "Art / race"), artInput, artList, artName,
    st.art ? h("button", { type: "button", class: "btn sm", onclick: () => { st.art = ""; artName.textContent = ""; run(); } }, "Fjern art") : null);
  if (st.art) getSpecies().then(l => { const s = l.find(x => String(x.artnr) === st.art); if (s) artName.textContent = s.navn; });

  const adv = h("details", { class: "fot-adv", open: advOpen },
    h("summary", {}, "Avanceret søgning"),
    h("div", { class: "fot-filters" },
      artBox, date("fra", "Fra dato"), date("til", "Til dato"),
      select("md", "Måned", MONTHS.map((m, i) => [i + 1, m])),
      select("land", "Land", codes.lande.map(l => [l.id, l.navn])),
      text("lok", "Lokalitet (tekst)", "fx Skagen"),
      select("sj", "Sjældenhed", SJ),
      select("koen", "Køn", codes.koen.map(c => [c.code, c.text])),
      select("alder", "Alder", codes.alder.map(c => [c.code, c.text])),
      select("dragt", "Dragt", codes.dragt.map(c => [c.code, c.text])),
      select("adf", "Adfærd", codes.adfaerd.map(c => [c.code, c.text])),
      select("tag", "Emneord", codes.emneord.map(c => [c.code, c.text]))),
    h("button", { class: "btn sm", type: "button", onclick: () => { for (const k of FILTERS) if (!fixed[k]) st[k] = ""; st.sort = "dato"; q.value = ""; st.side = 1; location.hash = location.hash.split("?")[0]; } }, "Ryd filter"));

  const bar = h("div", { class: "fot-bar" },
    h("form", { class: "row", style: { flex: 1 }, role: "search", onsubmit: (e) => { e.preventDefault(); st.q = q.value.trim(); run(); } }, q, h("button", { class: "btn primary", type: "submit" }, "Søg")),
    h("label", { class: "field inline" }, h("span", {}, "Sortér"),
      h("select", { onchange: (e) => { st.sort = e.target.value; run(); } }, ...SORTS.map(([v, t]) => h("option", { value: v, selected: v === st.sort }, t)))),
    seg([[24, "24"], [48, "48"], [96, "96"]], st.per, (v) => { st.per = +v; run(); }));
  load();
  const head = [];
  if (banner?.url) head.push(h("a", { class: "fot-banner", href: `${BASE}/${banner.photo_id}` }, h("img", { src: banner.url, alt: banner.tekst || "Banner" }),
    h("span", {}, banner.tekst ? banner.tekst + " · " : "", `Foto: ${banner.fotograf}`)));
  return h("div", {}, ...head, bar, adv, out);
}

/** En fotografs fotos = galleriet filtreret på fotografen. */
export async function photographer(parts, query) {
  return page(parts, query, { fotograf: parts[1] });
}
