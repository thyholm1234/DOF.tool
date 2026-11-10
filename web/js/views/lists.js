import { api, state, h, icon, fmtN, fmtDate, tag, params, setParams, loadRef, requireLogin, empty, errorBox, modal, toast, seg, store, sortBar } from "../core.js";
import { lineChart, seriesLegend, PALETTE } from "../charts.js";
import { regionsTab } from "./stats.js";

const YEAR = new Date().getFullYear();
const dayOfYear = (iso) => { const d = new Date(iso + "T12:00:00"); return Math.floor((d - new Date(d.getFullYear(), 0, 0)) / 86400000); };
const monthTicks = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
const monthName = (doy) => ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"][monthTicks.findLastIndex(t => t <= doy)] || "";

// Lister-modulet (#/lister/…): mine lister, ranglister, huller og grupper – samlet med krydslisterne (krydslister, mine kryds med manuel
// indtastning, ædelarter, artslister, World Bird List, regler og admin). Krydslisternes sider ligger i ../krydslister/ og vises her.
const TABS = [["", "Mine lister"], ["rangliste", "Ranglister"], ["huller", "Huller"], ["grupper", "Grupper"]];
const KL_PAGES = { alle: ["alle", "page"], regler: ["sider", "regler"], om: ["sider", "om"], mine: ["mine", "page"], artslister: ["artslister", "page"],
  wbl: ["wbl", "page"], art: ["art", "page"], admin: ["admin", "page"], kryds: ["liste", "page"], nyt: ["oversigt", "page"] };
const KL_FILES = { alle: () => import("../krydslister/alle.js"), sider: () => import("../krydslister/sider.js"), mine: () => import("../krydslister/mine.js"),
  artslister: () => import("../krydslister/artslister.js"), wbl: () => import("../krydslister/wbl.js"), art: () => import("../krydslister/art.js"),
  admin: () => import("../krydslister/admin.js"), liste: () => import("../krydslister/liste.js"), oversigt: () => import("../krydslister/oversigt.js") };
const kl = () => state.user?.krydslister || { features: {}, can_admin: false };
const klOn = (f = "krydslister") => !!kl().features?.[f];

export async function render(view, [sub] = []) {
  const p = params();
  const parts = (sub || "").split("/").filter(Boolean);
  // Gamle adresser (?tab=…) fra før modulerne blev samlet
  const stats = !!state.user?.statistik;
  if (!parts.length && p.tab) {
    const { tab, ...rest } = p, q = new URLSearchParams(rest).toString();
    if (tab === "matrikler") { location.replace("#/matrikel?tab=matrikler"); return; }
    if (tab === "regioner" && stats) { location.replace(`#/statistik?${new URLSearchParams({ ...rest, tab: "arter" })}`); return; }
    const to = { score: "rangliste", huller: "huller", grupper: "grupper", regioner: "arter-pr-aar", mine: "liste" }[tab];
    if (to) { location.replace(`#/lister/${to}${q ? `?${q}` : ""}`); return; }
  }
  if (!parts.length && (p.scope || p.year)) { location.replace(`#/lister/liste?${new URLSearchParams(p)}`); return; }  // fx «Min liste» fra Alarmer → Områder
  const first = parts[0] || "";
  loadCss("krydslister", "lister");
  const isKl = Object.hasOwn(KL_PAGES, first);
  if (isKl && !klOn()) { view.append(h("div", { class: "notice" }, "Krydslisterne er ikke slået til.")); return; }
  const active = first === "liste" ? "" : first === "kryds" || first === "alle" || first === "nyt" ? "rangliste" : first === "wbl" || first === "art" ? "artslister" : first === "om" ? "regler" : first === "mine" ? "" : first;
  const tabs = [...TABS, ...(stats ? [] : [["arter-pr-aar", "Arter pr. år"]]),
    ...(klOn() && klOn("krydslister_wbl") ? [["artslister", "Artslister"]] : []), ...(klOn() ? [["regler", "Regler"]] : []), ...(klOn() && kl().can_admin ? [["admin", "Admin"]] : [])];
  const body = h("div", { class: "lister-body" });
  view.append(h("div", { class: "kl lister" },
    h("header", { class: "page-head" }, h("div", {}, h("h1", {}, h("a", { href: "#/lister" }, "Lister")),
      h("p", { class: "lead" }, "Dine årslister, livslister, ædelarter og krydslister – og hvordan du ligger på ranglisterne."))),
    h("nav", { class: "lister-tabs", "aria-label": "Lister" }, ...tabs.map(([k, t]) => h("a", { href: k ? `#/lister/${k}` : "#/lister", class: k === active ? "on" : "", "aria-current": k === active ? "page" : null }, t))),
    body));
  document.title = "Lister – DOF.tool";
  const cleanups = [];
  try {
    if (isKl) {
      const { cleanups: klClean } = await import("../krydslister/common.js");
      klClean.length = 0;
      const [file, fn] = first === "kryds" && parts[2] === "art" ? ["liste", "hvem"] : KL_PAGES[first];
      body.append(h("div", {}, h("div", { class: "muted" }, "Henter …")));
      body.replaceChildren(await (await KL_FILES[file]())[fn](parts, p));
      cleanups.push(() => { for (const f of klClean.splice(0)) { try { f(); } catch { /* ignorér */ } } });
    } else if (first === "liste") await myList(body);
    else if (first === "rangliste") await rankPage(body);
    else if (first === "huller") await gaps(body);
    else if (first === "grupper") await groups(body);
    else if (first === "arter-pr-aar") await regionsTab(body, "regioner");
    else if (!first) await (await import("../lister/oversigt.js")).page(body);
    else body.append(h("div", { class: "notice err" }, "Siden findes ikke."));
  } catch (err) {
    console.error(err);
    body.replaceChildren(err.status === 404 ? h("div", { class: "notice err" }, "Siden findes ikke, eller du har ikke adgang til den.") : errorBox(err));
  }
  return () => { for (const f of cleanups) f(); document.title = "DOF.tool"; };
}

const cssLoaded = new Set();
function loadCss(...names) {
  for (const name of names) {
    if (cssLoaded.has(name) || document.querySelector(`link[data-css="${name}"]`)) continue;
    const path = new URL(import.meta.url).pathname;
    const href = path.startsWith("/v/") ? `/v/${path.split("/")[2]}/css/${name}.css` : `/css/${name}.css`;
    document.head.append(h("link", { rel: "stylesheet", href, "data-css": name }));
    cssLoaded.add(name);
  }
}

/** Ranglister: årslisterne fra DOFbasen (Danmark, afdelinger, kommuner, grupper) eller krydslisterne (Danske Arter, Ædelarter, VP …). */
async function rankPage(body) {
  if (!klOn()) { await scoreboard(body); return; }
  const out = h("div", {});
  body.append(h("div", { class: "lister-switch" }, h("span", { class: "muted" }, "Vis:"),
    h("div", { class: "seg" }, h("button", { type: "button", class: "on" }, "Årslister fra DOFbasen"),
      h("button", { type: "button", onclick: () => { location.hash = "#/lister/kryds/dk"; } }, "Krydslister"))), out);
  await scoreboard(out);
}

// ---------------------------------------------------------------- min liste (årsliste/livsliste for et område) med ædelarter

async function myList(body) {
  if (requireLogin(body)) return;
  const meta = await api("/api/lists/meta");
  const p = params();
  const st = { scope: p.scope || "dk", year: p.year || String(YEAR) };
  const scopeSel = h("select", { "aria-label": "Område" });
  for (const sc of meta.scopes) scopeSel.append(h("option", { value: sc.scope }, sc.label));
  if (![...scopeSel.options].some(o => o.value === st.scope)) st.scope = "dk";
  scopeSel.value = st.scope;
  const yearSel = h("select", { "aria-label": "År" }, h("option", { value: "all" }, "Livsliste (alle år)"),
    ...[...new Set([YEAR, ...meta.years])].sort((a, b) => b - a).map(y => h("option", { value: y }, y)));
  yearSel.value = st.year;
  const out = h("div", { class: "stack mt" });
  body.append(h("div", { class: "lister-crumb" }, h("a", { href: "#/lister" }, "← Mine lister")),
    h("div", { class: "card lister-filter" },
      h("div", { class: "lister-filter-row" }, scopeSel, yearSel, h("span", { class: "spacer" }),
        h("a", { class: "btn sm ghost", href: "#/alarmer?tab=omraader", html: `${icon("plus")} Nyt område` }),
        h("button", { class: "btn sm", type: "button", onclick: compare, html: `${icon("users")} Sammenlign` })),
      h("p", { class: "muted small", style: { margin: "8px 0 0" } },
        "Døde fugle tæller ikke på dine lister. Afdelinger og kommuner vælges under ", h("a", { href: "#/profil?fane=omraader" }, "profil"),
        ", egne områder under ", h("a", { href: "#/alarmer?tab=omraader" }, "Alarmer → Områder"), ". Matrikellister finder du under ", h("a", { href: "#/matrikel" }, "Matrikel"), ".")),
    out);

  const load = async () => {
    st.scope = scopeSel.value;
    st.year = yearSel.value;
    setParams({ scope: st.scope === "dk" ? "" : st.scope, year: st.year });
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "220px" } }));
    try {
      const q = new URLSearchParams({ scope: st.scope, year: st.year });
      // Ædelarter hører til DK-listen: på årslisten for året, på livslisten for ædel-livslisten
      const [data, aedel] = await Promise.all([api(`/api/lists/user/${state.user.obserkode}?${q}`),
        st.scope === "dk" && klOn() ? api("/api/krydslister/aedel").catch(() => null) : null]);
      drawList(out, data, st.year, aedel?.on ? { aedel: { ...aedel, year: st.year === "all" ? null : +st.year } } : {});
    } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  scopeSel.onchange = load; yearSel.onchange = load;
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

/** En liste (årsliste eller livsliste) med graf og sortérbar tabel. opts.aedel: {marks, year, kan_markere} – så vises ædelarterne med ★,
 *  og man kan markere dem (på årslisten for året; på livslisten for ædel-livslisten). */
export function drawList(out, data, year, opts = {}) {
  const items = data.items;
  const isYear = year && year !== "all";
  const pts = items.map((it, i) => [isYear ? dayOfYear(it.dato) : new Date(it.dato).getFullYear(), i + 1]);
  const sort = store("listsort") || { key: "dato", dir: 1 };
  const ae = opts.aedel || null;
  let onlyAedel = false;
  const tbody = h("tbody");
  const cmp = {
    dato: (a, b) => a.dato.localeCompare(b.dato) || a.obsid - b.obsid,
    navn: (a, b) => a.art.localeCompare(b.art, "da"),
    sys: (a, b) => (a.sortering || 0) - (b.sortering || 0),
    lok: (a, b) => (a.lok || "").localeCompare(b.lok || "", "da"),
  };
  // Ædelkryds pr. art: artnr eller latinsk navn (krydslisternes arter kobles til DOFbasen via begge)
  const marksFor = (it) => !ae ? [] : ae.marks.filter(m => (m.artnr && m.artnr === it.artnr) || (m.latin && it.latin && m.latin.toLowerCase() === it.latin.toLowerCase()));
  const isAedel = (it) => { const ms = marksFor(it); return ae?.year ? ms.some(m => m.dato?.startsWith(String(ae.year))) : ms.length > 0; };
  const aedelCount = () => items.filter(isAedel).length;
  const aedelStat = h("div", { class: "lister-aedel-stat" });
  const drawStat = () => {
    if (!ae) return;
    const n = aedelCount();
    aedelStat.replaceChildren(h("span", { class: "lister-star on", html: icon("star") }), h("b", {}, fmtN(n)), ` ${n === 1 ? "ædelart" : "ædelarter"} ${ae.year ? `i ${ae.year}` : "på ædel-livslisten"}`,
      h("label", { class: "check small" }, h("input", { type: "checkbox", checked: onlyAedel, onchange: (e) => { onlyAedel = e.target.checked; drawRows(); } }), h("span", {}, "Vis kun ædelarter")));
  };
  const toggle = async (it, btn) => {
    const on = !isAedel(it);
    const ms = marksFor(it);
    if (!on && !ae.year && ms.some(m => m.dato) && !confirm(`Fjern ${it.art} som ædelart i alle år (${[...new Set(ms.filter(m => m.dato).map(m => m.dato.slice(0, 4)))].join(", ")})?`)) return;
    btn.disabled = true;
    try {
      await api("/api/krydslister/aedel", { method: "PUT", body: { artnr: it.artnr, aar: ae.year, aedel: on, dato: ae.year ? it.dato : null, lokalitet: it.lok || "", obsid: it.obsid || null } });
      const fresh = await api("/api/krydslister/aedel");
      ae.marks = fresh.marks;
      toast(on ? `${it.art} er markeret som ædelart${ae.year ? ` i ${ae.year}` : ""}` : `${it.art} er ikke længere ædelart${ae.year ? ` i ${ae.year}` : ""}`);
      drawRows(); drawStat();
    } catch (err) { toast(err.message, "err"); btn.disabled = false; }
  };
  const star = (it) => {
    const on = isAedel(it), ms = marksFor(it);
    const years = ms.filter(m => m.dato).map(m => m.dato.slice(0, 4));
    const title = on ? `Ædelart${ae.year ? ` i ${ae.year}` : years.length ? ` (${[...new Set(years)].join(", ")})` : " på livslisten"}${ae.kan_markere ? " – klik for at fjerne" : ""}`
      : ae.kan_markere ? `Markér som ædelart${ae.year ? ` i ${ae.year}` : " på livslisten"}` : "Ikke ædelart";
    if (!ae.kan_markere) return h("span", { class: `lister-star ${on ? "on" : ""}`, title, html: icon("star") });
    const b = h("button", { type: "button", class: `lister-star ${on ? "on" : ""}`, title, "aria-label": title, "aria-pressed": on ? "true" : "false", html: icon("star") });
    b.onclick = () => toggle(it, b);
    return b;
  };
  const drawRows = () => {
    const sorted = [...items].filter(it => !onlyAedel || isAedel(it)).sort((a, b) => sort.dir * (cmp[sort.key] || cmp.dato)(a, b));
    tbody.replaceChildren(...sorted.map((it) => h("tr", { class: ae && isAedel(it) ? "is-aedel" : "" },
      h("td", { class: "num muted" }, items.indexOf(it) + 1),
      ae ? h("td", { class: "lister-star-cell" }, star(it)) : null,
      h("td", { class: "lister-art" }, h("a", { href: `#/art/${it.artnr}` }, it.art), it.klass !== "Alm" ? [" ", tag(it.klass)] : null),
      h("td", { class: "nowrap" }, fmtDate(it.dato, !isYear)),
      h("td", { class: "lister-lok" }, it.loknr ? h("a", { href: `#/lok/${it.loknr}` }, it.lok) : "–"))));
  };
  drawRows();
  drawStat();
  const th = (label, key) => h("th", { style: { cursor: "pointer" }, onclick: () => { sort.dir = sort.key === key ? -sort.dir : 1; sort.key = key; store("listsort", sort); drawRows(); } }, label);
  const bar = sortBar([["dato", "Dato"], ["navn", "Navn"], ["sys", "Systematik"], ["lok", "Lokalitet"]], sort, () => { store("listsort", sort); drawRows(); });
  const last = items[items.length - 1];
  out.replaceChildren(
    h("div", { class: "card lister-hero" },
      h("div", { class: "lister-hero-head" },
        h("div", {}, h("div", { class: "muted" }, data.navn), h("div", { class: "lister-big" }, fmtN(data.count), h("span", {}, " arter"))),
        last ? h("div", { class: "lister-latest" }, h("span", { class: "muted" }, "Seneste"), h("b", {}, last.art), h("span", { class: "muted" }, fmtDate(last.dato))) : null),
      ae ? aedelStat : null,
      ae?.kan_markere ? h("p", { class: "muted small", style: { margin: "6px 0 0" } }, ae.year
        ? `Tryk på ★ for at markere en art som ædelart i ${ae.year}. Den kommer på Ædelårslisten ${ae.year} og på din ædel-livsliste.`
        : "Tryk på ★ for at markere en art som ædelart på din ædel-livsliste (uden at den kommer på en bestemt ædelårsliste).") : null,
      items.length > 1 ? lineChart({ series: [{ name: "Arter", points: pts, color: PALETTE[0] }], step: true, height: 200,
        xFormat: isYear ? monthName : String, xTicks: isYear ? monthTicks : undefined, yFormat: (v) => fmtN(Math.round(v)) }) : null),
    items.length ? h("div", { class: "card table-wrap" }, h("div", { class: "mb" }, bar), h("table", { class: `data list-table ${ae ? "has-aedel" : ""}` },
      h("thead", {}, h("tr", {}, h("th", {}, "#"), ae ? h("th", { title: "Ædelart" }, h("span", { class: "lister-star on", html: icon("star") })) : null, th("Art", "navn"), th("Første dato", "dato"), h("th", {}, "Lokalitet"))), tbody))
      : empty("Ingen arter på denne liste endnu", "Data kommer fra DOFbasen – nye observationer tæller med inden for få minutter."));
}

// ---------------------------------------------------------------- huller: arter andre har, men ikke jeg

const GAP_STEP = 40;

async function gaps(body) {
  if (requireLogin(body)) return;
  const meta = await api("/api/lists/meta");
  const p = params();
  const st = { scope: p.scope || "dk", year: p.year || String(YEAR), by: p.by === "obs" ? "obs" : "trips", q: "", klass: "", shown: GAP_STEP };
  const scopeSel = h("select", { style: { width: "auto" } }, ...meta.scopes.map(sc => h("option", { value: sc.scope }, sc.label)));
  scopeSel.value = st.scope;
  const yearSel = h("select", { style: { width: "auto" } }, h("option", { value: "all" }, "Livsliste (alle år)"),
    ...[...new Set([YEAR, ...meta.years])].sort((a, b) => b - a).map(y => h("option", { value: y }, y)));
  yearSel.value = st.year;
  const metric = seg([["trips", "Andel af ture"], ["obs", "Observatører"]], st.by, (v) => { st.by = v; st.shown = GAP_STEP; save(); draw(); });
  const search = h("input", { type: "search", placeholder: "Søg art…", style: { maxWidth: "200px" } });
  const klassSel = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle kategorier"));
  const out = h("div", { class: "stack mt" });
  const note = h("p", { class: "muted", style: { margin: 0, fontSize: ".85rem" } });
  body.append(h("div", { class: "card" },
    h("div", { class: "row" }, scopeSel, yearSel, h("span", { style: { flex: 1 } }), metric),
    note), out);
  let data = null;
  const save = () => setParams({ scope: st.scope === "dk" ? "" : st.scope, year: st.year, by: st.by === "trips" ? "" : st.by });
  // Livslisten tæller observatør-år (se regions.refresh_reach), så observatørmålet er en andel – ikke et antal personer
  const obsShare = () => st.year === "all" && data?.scope && /^(dk|afd:|kom:)/.test(data.scope);
  const value = (it) => st.by === "trips" ? it.share : obsShare() ? it.obs_share : it.n_observers;
  const obsText = (it) => obsShare() ? pct(it.obs_share) : fmtN(it.n_observers);
  const pct = (x) => x >= 0.1 ? `${Math.round(x * 100)} %` : x >= 0.01 ? `${(x * 100).toFixed(1).replace(".", ",")} %` : "< 1 %";

  const load = async () => {
    st.scope = scopeSel.value; st.year = yearSel.value; st.shown = GAP_STEP; save();
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "320px" } }));
    try {
      data = await api(`/api/lists/gaps?${new URLSearchParams({ scope: st.scope, year: st.year })}`);
      const ks = [...new Set(data.items.map(i => i.klass))].sort();
      klassSel.replaceChildren(h("option", { value: "" }, "Alle kategorier"), ...ks.map(k => h("option", { value: k }, k === "Alm" ? "Almindelige" : k)));
      st.klass = "";
      draw();
    } catch (err) { out.replaceChildren(errorBox(err)); }
  };

  const draw = () => {
    if (!data) return;
    note.textContent = st.by === "trips"
      ? `Andel af de ${fmtN(data.trips)} ture i området${st.year === "all" ? "" : ` i ${st.year}`}, hvor arten blev indtastet – hvor stor en chance du reelt har for at støde på den.`
      : obsShare()
        ? "Andel af områdets aktive observatører, der har indtastet arten – gennemsnit over årene, vægtet med antal aktive observatører hvert år. Hvor udbredt arten er blandt birdere."
        : `Antal af de ${fmtN(data.observers)} observatører i området${st.year === "all" ? "" : ` i ${st.year}`}, der har indtastet arten – hvor udbredt arten er blandt birdere.`;
    const q = st.q.toLowerCase();
    const all = data.items.filter(i => (!q || i.art.toLowerCase().includes(q)) && (!st.klass || i.klass === st.klass))
      .sort((a, b) => value(b) - value(a) || a.art.localeCompare(b.art, "da"));
    const max = Math.max(...all.map(value), 1e-9);
    const top = all[0];
    const rows = all.slice(0, st.shown).map((it, i) => {
      const v = value(it);
      return h("tr", {},
        h("td", { class: "num muted" }, i + 1),
        h("td", {}, h("a", { href: `#/art/${it.artnr}` }, it.art), it.klass !== "Alm" ? [" ", tag(it.klass)] : null),
        h("td", { style: { width: "42%", minWidth: "120px" } }, h("div", { style: { display: "flex", alignItems: "center", gap: "8px" } },
          h("div", { style: { flex: 1, height: "10px", background: "var(--bg-2)", borderRadius: "5px", overflow: "hidden" } },
            h("div", { style: { width: `${Math.max(2, v / max * 100)}%`, height: "100%", background: "var(--brand-2)", borderRadius: "5px" } })),
          h("b", { class: "num nowrap", style: { minWidth: "5.5ch", textAlign: "right" }, title: `${fmtN(it.n_observers)} ${obsShare() ? "observatør-år" : "observatører"} · ${fmtN(it.n_trips)} ture` },
            st.by === "trips" ? pct(it.share) : obsText(it)))),
        h("td", { class: "nowrap muted right" }, it.last ? fmtDate(it.last, false) : "–"));
    });
    out.replaceChildren(
      h("div", { class: "card" },
        h("div", { class: "stats" },
          h("div", { class: "stat" }, h("b", {}, fmtN(data.n_gaps)), h("span", {}, "huller")),
          h("div", { class: "stat" }, h("b", {}, fmtN(data.mine)), h("span", {}, "arter på din liste")),
          top ? h("div", { class: "stat" }, h("b", {}, top.art), h("span", {}, `største hul · ${st.by === "trips" ? pct(top.share) + " af ture" : (obsShare() ? pct(top.obs_share) + " af observatører" : fmtN(top.n_observers) + " observatører")}`)) : null)),
      h("div", { class: "card table-wrap" },
        h("div", { class: "row between mb" }, search, klassSel,
          h("span", { class: "muted", style: { fontSize: ".85rem" } }, `${fmtN(all.length)} arter · sorteret efter ${st.by === "trips" ? "andel af ture" : obsShare() ? "andel af observatører" : "antal observatører"}`)),
        all.length ? h("table", { class: "data" },
          h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Art"), h("th", {}, st.by === "trips" ? "Andel af ture" : obsShare() ? "Andel af observatører" : "Observatører"), h("th", { class: "right" }, "Senest set")))
          , h("tbody", {}, ...rows)) : empty(data.n_gaps ? "Ingen arter matcher" : "Ingen huller – du har alle arter andre har!"),
        all.length > st.shown ? h("div", { class: "row mt", style: { justifyContent: "center" } },
          h("button", { class: "btn", onclick: () => { st.shown += GAP_STEP * 2; draw(); } }, `Vis flere (${fmtN(all.length - st.shown)} tilbage)`)) : null));
  };
  let t; search.oninput = () => { clearTimeout(t); t = setTimeout(() => { st.q = search.value; st.shown = GAP_STEP; draw(); }, 150); };
  klassSel.onchange = () => { st.klass = klassSel.value; st.shown = GAP_STEP; draw(); };
  scopeSel.onchange = load; yearSel.onchange = load;
  await load();
}

// ---------------------------------------------------------------- ranglister (årslister fra DOFbasen)

async function scoreboard(body) {
  await loadRef();
  const p = params();
  const st = { scope: p.scope || "dk", year: p.year || String(YEAR), m: 0, group: p.group || "" };
  const scopeSel = h("select", { style: { width: "auto" } },
    h("option", { value: "dk" }, "Danmark"),
    h("optgroup", { label: "Lokalafdelinger" }, ...state.afdelinger.map(a => h("option", { value: `afd:${a.id}` }, a.navn))),
    h("optgroup", { label: "Kommuner" }, ...state.kommuner.map(k => h("option", { value: `kom:${k.kode}` }, k.navn))));
  scopeSel.value = st.scope;
  const yearSel = h("select", { style: { width: "auto" } }, h("option", { value: "all" }, "Alle år"),
    ...Array.from({ length: 12 }, (_, i) => YEAR - i).map(y => h("option", { value: y }, y)));
  yearSel.value = st.year;
  const groupSel = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle deltagere"));
  if (state.user) for (const g of await api("/api/lists/groups")) groupSel.append(h("option", { value: g.id }, `Gruppe: ${g.name}`));
  groupSel.value = st.group;
  const view = seg([["rank", "Rangliste"], ["race", "Kapløb"], ["matrix", "Matrix"], ["unique", "Unikke"]], store("sbview") || "rank", (v) => { store("sbview", v); draw(); });
  const out = h("div", { class: "mt" });
  body.append(h("div", { class: "card" }, h("div", { class: "row" }, scopeSel, yearSel, groupSel, h("span", { style: { flex: 1 } }), view),
    h("p", { class: "muted mt", style: { fontSize: ".85rem", marginBottom: 0 } }, "Afdelings- og kommuneranglister viser brugere, der har valgt området under profil. Grupper kan indeholde alle obserkoder. Døde fugle tæller ikke.")), out);
  let data = null;
  const load = async () => {
    st.scope = scopeSel.value;
    st.year = yearSel.value; st.group = groupSel.value;
    setParams({ scope: st.scope === "dk" ? "" : st.scope, year: st.year, group: st.group });
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "300px" } }));
    try {
      const q = new URLSearchParams({ scope: st.scope, year: st.year });
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

export function ranking(rows, st) {
  if (!rows.length) return empty("Ingen deltagere endnu");
  const top = rows[0]?.antal || 1;
  return h("div", { class: "rank" }, ...rows.map(r => h("div", { class: `rank-row ${state.user?.obserkode === r.obserkode ? "me" : ""}`, onclick: () => showUser(r, st) },
    h("div", { class: "place" }, r.placering),
    h("div", {}, h("div", { class: "who" }, r.navn), h("div", { class: "last" }, r.sidste_art ? `Seneste: ${r.sidste_art} · ${fmtDate(r.sidste_dato, false)}` : "Ingen arter endnu"),
      h("div", { class: "bar-inline" }, h("i", { style: { width: `${(r.antal / top) * 100}%` } }))),
    h("div", { class: "score" }, fmtN(r.antal)))));
}

export async function showUser(r, st) {
  const out = h("div", {}, h("div", { class: "skeleton" }));
  modal(r.navn, out, { wide: true });
  try {
    const q = new URLSearchParams({ scope: st.scope, year: st.year });
    if (st.m) q.set("matrikel", st.m);
    drawList(out, await api(`/api/lists/user/${r.obserkode}?${q}`), st.year);
  } catch (err) { out.replaceChildren(errorBox(err)); }
}

export function race(rows, year) {
  const isYear = year !== "all";
  const series = rows.map((r, i) => ({ name: r.navn, color: PALETTE[i % PALETTE.length],
    points: r.firsts.map(([, d], j) => [isYear ? dayOfYear(d) : new Date(d).getFullYear() + dayOfYear(d) / 366, j + 1]) }));
  return h("div", {}, h("h3", {}, "Kumulativt antal arter"),
    lineChart({ series, step: true, height: 320, xFormat: isYear ? monthName : (x) => String(Math.floor(x)), xTicks: isYear ? monthTicks : undefined, yFormat: (v) => fmtN(Math.round(v)) }),
    seriesLegend(series));
}

export function matrix(rows, names) {
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

export function unique(rows, names) {
  const count = new Map();
  rows.forEach(r => r.firsts.forEach(([a]) => count.set(a, (count.get(a) || 0) + 1)));
  const items = rows.map(r => ({ r, only: r.firsts.filter(([a]) => count.get(a) === 1) })).filter(x => x.only.length);
  if (!items.length) return empty("Ingen arter som kun én deltager har");
  return h("div", { class: "stack" }, h("h3", {}, "Arter som kun én deltager har"),
    ...items.sort((a, b) => b.only.length - a.only.length).map(({ r, only }) => h("div", {},
      h("b", {}, `${r.navn} (${only.length})`), h("div", { class: "muted" }, only.map(([a, d]) => `${names[a]} (${fmtDate(d, false)})`).join(", ")))));
}

// ---------------------------------------------------------------- grupper (også under Matrikel → Administrér)

export async function groups(body, board = "#/lister") {
  if (requireLogin(body)) return;
  const list = h("div", { class: "stack" });
  const name = h("input", { placeholder: "Gruppenavn, fx 'Fugleholdet'" });
  const code = h("input", { placeholder: "Invitationskode", style: { textTransform: "uppercase" } });
  body.append(h("div", { class: "grid-2" },
    h("form", { class: "card stack", onsubmit: async (e) => { e.preventDefault(); try { await api("/api/lists/groups", { method: "POST", body: { name: name.value } }); name.value = ""; load(); } catch (err) { toast(err.message, "err"); } } },
      h("h3", {}, "Opret gruppe"), h("p", { class: "muted" }, "En gruppe får sin egen rangliste. Du kan tilføje alle obserkoder – også folk der ikke bruger portalen."), name, h("button", { class: "btn primary" }, "Opret")),
    h("form", { class: "card stack", onsubmit: async (e) => { e.preventDefault(); try { const g = await api("/api/lists/groups/join", { method: "POST", body: { code: code.value } }); toast(`Du er nu med i ${g.name}`); code.value = ""; load(); } catch (err) { toast(err.message, "err"); } } },
      h("h3", {}, "Deltag med kode"), h("p", { class: "muted" }, "Få koden af gruppens ejer."), code, h("button", { class: "btn" }, "Deltag"))),
    h("h2", { class: "mt2" }, "Mine grupper"), list);
  const load = async () => {
    const gs = await api("/api/lists/groups");
    list.replaceChildren(...(gs.length ? gs.map(g => groupCard(g, load, board)) : [empty("Du er ikke med i nogen grupper endnu")]));
  };
  await load();
}

function groupCard(g, reload, board) {
  const members = h("div", { class: "muted" }, "…");
  const add = h("input", { placeholder: "Tilføj obserkode", style: { width: "180px", textTransform: "uppercase" } });
  const card = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", {}, g.name), h("span", { class: "spacer" }),
      g.invite_code ? h("span", { class: "muted" }, "Kode: ", h("b", { class: "kbd" }, g.invite_code)) : null,
      h("a", { class: "btn sm", href: board === "#/lister" ? `#/lister/rangliste?group=${g.id}` : `${board}?tab=score&group=${g.id}` }, "Rangliste")),
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
