// Galleriet: fritekst, avanceret søgning (art, dato, måned, fotograf, land, lokalitet, køn/alder/dragt/adfærd, emneord), sortering og uendeligt feed.
// Alle filtre ligger i adressen, så Tilbage og deling virker. Visningen er et uendeligt feed: næste fotos hentes, når bunden nås.
import { api, h, setParams, searchSpecies } from "../core.js";
import { BASE, loadingBox, notice, nowrap, photoCard } from "./common.js";

const SORTS = [["dato", "Fotodato"], ["nyeste", "Senest tilføjet"], ["likes", "Flest likes"], ["kommenteret", "Senest kommenteret"], ["set", "Set af flest"]];
const FILTERS = ["q", "fra", "til", "md", "art", "taxon", "fotograf", "land", "lok", "loknr", "sj", "koen", "alder", "dragt", "adf", "tag"];
const MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];
const SJ = [["almindelig", "Almindelig"], ["relativ_almindelig", "Relativ almindelig"], ["ualmindelig", "Ualmindelig"], ["sjaelden", "Sjælden"], ["su", "SU"]];

function readState(q, fixed = {}) {
  const st = { sort: q.sort || "dato", per: 24, side: 1 };
  for (const k of FILTERS) st[k] = fixed[k] ?? q[k] ?? "";
  return st;
}
const toQuery = (st) => {
  const p = new URLSearchParams();
  for (const k of FILTERS) if (st[k]) p.set(k, st[k]);
  if (st.sort !== "dato") p.set("sort", st.sort);
  p.set("per", st.per);
  p.set("side", st.side);
  return p;
};

export async function page(parts, query, fixed = {}) {
  const st = readState(query, fixed);
  const [codes, banner] = await Promise.all([api("/api/fotos/koder"), api("/api/fotos/banner").catch(() => null)]);
  // Forslag: kun arter/racer og lokaliteter, der findes på fotos
  let sug = null;
  const getSug = async () => sug ||= await api("/api/fotos/forslag").catch(() => ({ arter: [], lokaliteter: [] }));
  const out = h("div", {});
  const q = h("input", { type: "search", value: st.q, placeholder: "Søg i titel, lokalitet, beskrivelse, fotograf eller art …", "aria-label": "Søg i fotos", style: { flex: 1, minWidth: "220px" } });
  let advOpen = FILTERS.some(k => k !== "q" && st[k]);

  // Feedet: første side erstatter indholdet, derefter hentes næste side, når sentinelen under gitteret kommer til syne.
  let gen = 0, observer = null;
  async function load() {
    const my = ++gen;
    observer?.disconnect();
    st.side = 1;
    out.replaceChildren(loadingBox());
    let d;
    try { d = await api("/api/fotos?" + toQuery(st)); } catch (e) { if (my === gen) out.replaceChildren(notice(e.message, "err")); return; }
    if (my !== gen) return;
    if (!d.total) { out.replaceChildren(h("div", { class: "fot-count" }, "0 fotos"), h("p", { class: "muted", style: { padding: "20px 0" } }, "Ingen fotos matcher.")); return; }
    const grid = h("div", { class: "fot-grid" }, ...d.rows.map(r => photoCard(r)));
    const foot = h("div", { class: "fot-more", role: "status", "aria-live": "polite" });
    out.replaceChildren(h("div", { class: "fot-count" }, `${nowrap(d.total)} ${d.total === 1 ? "foto" : "fotos"}`), grid, foot);
    let shown = d.rows.length, busy = false;
    const done = () => { observer.disconnect(); foot.className = "fot-more done"; foot.replaceChildren(`Du har set alle ${nowrap(d.total)} fotos`); };
    const more = async () => {
      if (busy) return;
      busy = true;
      foot.className = "fot-more loading";
      foot.replaceChildren(h("span", { class: "fot-spin", "aria-hidden": "true" }), "Henter flere fotos …");
      try {
        st.side++;
        const n = await api("/api/fotos?" + toQuery(st));
        if (my !== gen) return;
        grid.append(...n.rows.map(r => photoCard(r)));
        shown += n.rows.length;
        busy = false;
        if (!n.rows.length || shown >= n.total) return done();
        foot.className = "fot-more"; foot.replaceChildren();
        observer.unobserve(foot); observer.observe(foot); // giver en ny måling, hvis bunden stadig er i syne
      } catch (e) {
        if (my !== gen) return;
        st.side--; busy = false;
        foot.className = "fot-more err";
        foot.replaceChildren("Kunne ikke hente flere fotos. ", h("button", { type: "button", class: "btn sm", onclick: more }, "Prøv igen"));
      }
    };
    observer = new IntersectionObserver((es) => { if (es.some(e => e.isIntersecting)) more(); }, { rootMargin: "300px 0px" });
    if (shown >= d.total) done(); else observer.observe(foot);
  }
  const sync = () => {
    setParams({ ...Object.fromEntries(FILTERS.map(k => [k, fixed[k] ? "" : st[k]])), sort: st.sort === "dato" ? "" : st.sort });
    load();
  };
  const run = () => sync();

  const select = (key, label, options, { multi = false } = {}) => h("label", { class: "field" }, h("span", {}, label),
    h("select", { onchange: (e) => { st[key] = e.target.value; run(); } }, h("option", { value: "" }, "Alle"), ...options.map(([v, t]) => h("option", { value: v, selected: String(st[key]) === String(v) }, t))));
  const date = (key, label) => h("label", { class: "field" }, h("span", {}, label), h("input", { type: "date", value: st[key], onchange: (e) => { st[key] = e.target.value; run(); } }));
  const text = (key, label, ph) => h("label", { class: "field" }, h("span", {}, label), h("input", { type: "text", value: st[key], placeholder: ph || "", onchange: (e) => { st[key] = e.target.value.trim(); run(); } }));

  const artBox = h("div", { class: "field" }), artList = h("div", { class: "fot-suggest" });
  const artInput = h("input", { type: "search", placeholder: "Art eller race …", autocomplete: "off" });
  const artName = h("small", { class: "muted" }, st.art ? `Art #${st.art}` : "");
  const fold = (x) => (x || "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  const showArt = async () => {
    const all = (await getSug()).arter, v = fold(artInput.value.trim());
    const hits = (v ? all.filter(a => fold(a.navn).split(/[\s,()/-]+/).some(w => w.startsWith(v)) || fold(a.navn).includes(v) || fold(a.latin).includes(v)) : all).slice(0, 12);
    artList.replaceChildren(...hits.map(a => h("button", { type: "button", onmousedown: (e) => e.preventDefault(), onclick: () => {
      st.art = String(a.artnr); artName.textContent = a.navn; artInput.value = ""; artList.replaceChildren(); run();
    } }, a.navn, h("small", { class: "muted" }, `${a.latin ? ` · ${a.latin}` : ""} · ${nowrap(a.n)}`))));
  };
  artInput.oninput = artInput.onfocus = showArt;
  artInput.onblur = () => setTimeout(() => artList.replaceChildren(), 150);
  artBox.append(h("span", {}, "Art / race"), artInput, artList, artName,
    st.art ? h("button", { type: "button", class: "btn sm", onclick: () => { st.art = ""; artName.textContent = ""; run(); } }, "Fjern art") : null);
  if (st.art) getSug().then(l => { const s = l.arter.find(x => String(x.artnr) === st.art); if (s) artName.textContent = s.navn; });

  const lokList = h("datalist", { id: "fot-lok-forslag" });
  const lokField = text("lok", "Lokalitet (tekst)", "fx Skagen");
  const lokInput = lokField.querySelector("input");
  lokInput.setAttribute("list", "fot-lok-forslag");
  lokInput.autocomplete = "off";
  lokInput.onfocus = async () => { if (!lokList.children.length) lokList.replaceChildren(...(await getSug()).lokaliteter.map(l => h("option", { value: l.navn }, `${l.n} ${l.n === 1 ? "foto" : "fotos"}`))); };
  lokField.append(lokList);

  const adv = h("details", { class: "fot-adv", open: advOpen },
    h("summary", {}, "Avanceret søgning"),
    h("div", { class: "fot-filters" },
      artBox, date("fra", "Fra dato"), date("til", "Til dato"),
      select("md", "Måned", MONTHS.map((m, i) => [i + 1, m])),
      select("land", "Land", codes.lande.map(l => [l.id, l.navn])),
      lokField,
      select("sj", "Sjældenhed", SJ),
      select("koen", "Køn", codes.koen.map(c => [c.code, c.text])),
      select("alder", "Alder", codes.alder.map(c => [c.code, c.text])),
      select("dragt", "Dragt", codes.dragt.map(c => [c.code, c.text])),
      select("adf", "Adfærd", codes.adfaerd.map(c => [c.code, c.text])),
      select("tag", "Emneord", codes.emneord.map(c => [c.code, c.text]))),
    h("button", { class: "btn sm", type: "button", onclick: () => { for (const k of FILTERS) if (!fixed[k]) st[k] = ""; st.sort = "dato"; q.value = ""; location.hash = location.hash.split("?")[0]; } }, "Ryd filter"));

  const bar = h("div", { class: "fot-bar" },
    h("form", { class: "row", style: { flex: 1 }, role: "search", onsubmit: (e) => { e.preventDefault(); st.q = q.value.trim(); run(); } }, q, h("button", { class: "btn primary", type: "submit" }, "Søg")),
    h("label", { class: "field inline" }, h("span", {}, "Sortér"),
      h("select", { onchange: (e) => { st.sort = e.target.value; run(); } }, ...SORTS.map(([v, t]) => h("option", { value: v, selected: v === st.sort }, t)))));
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
