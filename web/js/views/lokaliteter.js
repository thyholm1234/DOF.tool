// Lokaliteter: indgang til alle lokaliteters sider – søg, vælg år (indeværende, et tidligere eller alle år), filtrér på kommune,
// afdeling og SU/SUB/Bemærk og sortér efter aktivitet, tal eller afstand. Filtrering og sider sker på serveren, så listen er
// hurtig på telefonen. Indeværende år er live (med seneste 30 dage); øvrige år er forudberegnet.
import { api, state, h, icon, fmtN, fmtDate, fmtTime, tag, empty, errorBox, seg, store, loadRef, todayISO, KAT, busyBlock } from "../core.js";

const YEAR = new Date().getFullYear();
const KATS = [["", "Alle"], ["SU", "SU"], ["SUB", "SUB"], ["bemaerk", "Bemærk"]];
const PAGE = 100;

export async function render(view) {
  const saved = store("lokaliteter") || {};
  const st = { q: "", kat: saved.kat || "", kommune: saved.kommune || "", afdeling: saved.afdeling || "", active: saved.active ?? true,
    year: saved.year || "",  // "" = indeværende år, "alle" eller et årstal
    sort: saved.sort === "afstand" ? "aktivitet" : saved.sort || "aktivitet", pos: null, years: [], live: true };
  if (st.year && st.year !== String(YEAR) && st.sort === "aktivitet") st.sort = "aar";  // seneste 30 dage findes kun for indeværende år
  const save = () => store("lokaliteter", { kat: st.kat, kommune: st.kommune, afdeling: st.afdeling, active: st.active, sort: st.sort, year: st.year });
  // Navn på perioden: "2026", "2018" eller "i alt"
  const period = () => st.year === "alle" ? "i alt" : st.year || String(YEAR);
  const out = h("div", { style: { marginTop: "12px" } });
  const countEl = h("small", { class: "muted" });
  const builtEl = h("small", { class: "muted" });
  let items = [], total = 0, all = 0, seq = 0, timer = null;

  const search = h("input", { type: "search", placeholder: "Søg lokalitet…", autofocus: true, oninput: () => { st.q = search.value.trim(); later(); },
    onkeydown: (e) => { if (e.key === "Enter" && items[0]) location.hash = `#/lok/${items[0].loknr}`; } });
  const activeBtn = h("button", { type: "button", class: "twostate", title: "Kun lokaliteter med observationer i perioden", onclick: () => { st.active = !st.active; save(); load(); } });
  const katHint = h("small", { class: "muted" });
  const yearSel = h("select", { style: { width: "auto" }, "aria-label": "Vælg år", onchange: (e) => {
    st.year = e.target.value;
    if (st.sort === "aktivitet" && st.year) st.sort = "aar";  // seneste 30 dage findes kun for indeværende år
    save(); load();
  } });
  const drawYears = () => {
    const years = st.years.length ? st.years : [YEAR];
    const opts = [["", `${YEAR} (i år)`], ...years.filter(y => y !== YEAR).map(y => [String(y), String(y)]), ["alle", "Alle år"]];
    if (st.year && !opts.some(o => o[0] === st.year)) opts.splice(1, 0, [st.year, st.year]);  // gemt år der ikke (endnu) er på listen
    yearSel.replaceChildren(...opts.map(([v, l]) => h("option", { value: v, selected: v === st.year }, l)));
  };
  const nearBtn = h("button", { type: "button", class: "twostate", title: "Sortér efter afstand fra din position", onclick: () => {
    if (st.sort === "afstand") { st.sort = "aktivitet"; st.pos = null; save(); load(); return; }
    if (!navigator.geolocation) { out.prepend(errorBox(new Error("Din browser kan ikke finde din position"))); return; }
    nearBtn.disabled = true; nearBtn.textContent = "Finder position…";
    navigator.geolocation.getCurrentPosition((p) => { st.pos = { lat: p.coords.latitude, lon: p.coords.longitude }; st.sort = "afstand"; nearBtn.disabled = false; load(); },
      () => { nearBtn.disabled = false; drawButtons(); out.replaceChildren(errorBox(new Error("Kunne ikke hente din position – tjek at siden må bruge den"))); },
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 });
  } });
  const wrapSeg = (el) => { el.classList.add("wrap"); return el; };
  const sortSeg = () => wrapSeg(seg([...(!st.year || st.year === String(YEAR) ? [["aktivitet", "Seneste 30 dage"]] : []),
    ["aar", st.year === "alle" ? "Observationer" : `I ${period()}`], ["arter", "Arter"], ["senest", "Senest"], ["navn", "Navn"]], st.sort === "afstand" ? "" : st.sort,
    (v) => { st.sort = v; st.pos = null; save(); load(); }));
  const sortBox = h("div", {});
  const drawButtons = () => {
    activeBtn.textContent = st.year === "alle" ? "Med observationer" : `Aktive i ${period()}`;
    katHint.textContent = !st.year || st.year === String(YEAR) ? "de seneste 30 dage" : st.year === "alle" ? "nogensinde" : `i ${st.year}`;
    drawYears();
    activeBtn.classList.toggle("on", st.active);
    nearBtn.classList.toggle("on", st.sort === "afstand");
    nearBtn.innerHTML = `${icon("pin")} Nær mig`;
    sortBox.replaceChildren(sortSeg());
  };

  try { await loadRef(); } catch { /* filtrene virker uden */ }
  const select = (opts, value, onChange, label) => {
    const el = h("select", { style: { width: "auto", maxWidth: "46vw" }, "aria-label": label, onchange: (e) => onChange(e.target.value) },
      h("option", { value: "" }, label), ...opts.map(([v, l]) => h("option", { value: v, selected: String(v) === String(value) }, l)));
    return el;
  };

  view.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Lokaliteter"), h("p", { class: "lead" }, "Alle lokaliteter i DOFbasen – åbn en lokalitet for arter, rekorder og de seneste dage."))),
    h("div", { class: "card toolbox" },
      search,
      h("div", { class: "toolbar" }, wrapSeg(seg(KATS, st.kat, (v) => { st.kat = v; save(); load(); })),
        katHint, h("span", { class: "spacer" }), countEl),
      h("div", { class: "toolbar" },
        yearSel,
        select(state.kommuner.map(k => [k.kode, k.navn]), st.kommune, (v) => { st.kommune = v; save(); load(); }, "Alle kommuner"),
        select(state.afdelinger.map(a => [a.id, a.navn]), st.afdeling, (v) => { st.afdeling = v; save(); load(); }, "Alle afdelinger"),
        activeBtn, nearBtn),
      sortBox),
    out);

  function later() { clearTimeout(timer); timer = setTimeout(load, 250); }

  function query(offset) {
    const p = new URLSearchParams({ sort: st.sort, limit: PAGE, offset });
    if (st.q) p.set("q", st.q);
    if (st.kat) p.set("kat", st.kat);
    if (st.kommune) p.set("kommune", st.kommune);
    if (st.afdeling) p.set("afdeling", st.afdeling);
    if (st.active) p.set("active", "true");
    if (st.year && st.year !== String(YEAR)) p.set("year", st.year);
    if (st.sort === "afstand" && st.pos) { p.set("lat", st.pos.lat); p.set("lon", st.pos.lon); }
    return `/api/locations/index?${p}`;
  }

  async function load() {
    const me = ++seq;
    drawButtons();
    // Første visning: skeleton med besked (serveren tæller årets observationer første gang). Senere: listen dæmpes
    // og et lille spinner-mærke vises, så man kan se at filteret arbejder.
    if (!items.length) out.replaceChildren(busyBlock(st.year ? "Henter tallene for perioden… et enkelt år tager et par sekunder første gang" : "Beregner årets tal for lokaliteterne… første gang kan det tage et par sekunder", 8));
    else { out.firstElementChild?.classList.add("is-busy"); countEl.replaceChildren(h("span", { class: "spinner" })); }
    try {
      const d = await api(query(0));
      if (me !== seq) return;  // et nyere opslag er på vej
      st.years = d.years || st.years; st.live = d.live !== false;
      // Oversigten bygges af serveren hver 6. time
      builtEl.textContent = d.built ? `Oversigten er bygget ${fmtDate(d.built.at, false)} kl. ${fmtTime(d.built.at)} og opdateres hver 6. time.` : "";
      if (d.building) {
        // Alle år tager ca. 20 sekunder og regnes i baggrunden – spørg igen om lidt
        items = [];
        out.replaceChildren(busyBlock("Serveren bygger lokalitetsoversigten… det tager op til et minut og sker kun her første gang", 6));
        countEl.textContent = "";
        drawButtons();
        clearTimeout(timer); timer = setTimeout(() => { if (me === seq) load(); }, 5000);
        return;
      }
      items = d.items; total = d.total; all = d.all;
      drawButtons();
      draw();
    } catch (err) { if (me === seq) { items = []; out.replaceChildren(errorBox(err)); countEl.textContent = ""; } }
  }

  async function more(btn) {
    btn.disabled = true; btn.replaceChildren(h("span", { class: "spinner" }), " Henter…");
    try { const d = await api(query(items.length)); items = items.concat(d.items); draw(); }
    catch (err) { btn.disabled = false; out.append(errorBox(err)); }
  }

  function draw() {
    countEl.textContent = `${fmtN(total)} af ${fmtN(all)}`;
    if (!items.length) { out.replaceChildren(empty("Ingen lokaliteter matcher", "Prøv et andet navn eller slå filtrene fra.")); return; }
    const today = todayISO(), near = st.sort === "afstand";
    const more_ = items.length < total ? h("button", { class: "btn", style: { margin: "10px auto", display: "block" }, onclick: (e) => more(e.currentTarget) },
      `Vis flere (${fmtN(total - items.length)} tilbage)`) : null;
    const per = period(), live = st.live;
    out.replaceChildren(h("div", { class: "card table-wrap" }, h("table", { class: "data stack-sm species-index" },
      h("thead", {}, h("tr", {}, h("th", {}, "Lokalitet"), near ? h("th", { class: "right" }, "Afstand") : null, live ? h("th", { class: "right" }, "30 dage") : null,
        h("th", { class: "right" }, `Obs. ${per}`), h("th", { class: "right" }, `Arter ${per}`), h("th", {}, "Senest set"))),
      h("tbody", {}, ...items.map(l => h("tr", { class: "click", onclick: () => { location.hash = `#/lok/${l.loknr}`; } },
        h("td", { class: "wide" }, h("a", { href: `#/lok/${l.loknr}`, onclick: (e) => e.stopPropagation() }, h("b", {}, l.navn)),
          l.kategori ? h("span", { style: { marginLeft: "6px" } }, tag(l.kategori, KAT[l.kategori])) : null,
          l.kommune ? h("small", { class: "muted", style: { marginLeft: "6px" } }, l.kommune) : null),
        near ? h("td", { class: "right num nowrap", "data-label": "Afstand" }, l.km < 10 ? `${String(l.km).replace(".", ",")} km` : `${Math.round(l.km)} km`) : null,
        live ? h("td", { class: "right num", "data-label": "30 dage" }, l.n_30 ? fmtN(l.n_30) : "–") : null,
        h("td", { class: "right num", "data-label": `Obs. ${per}` }, l.n_year ? fmtN(l.n_year) : "–"),
        h("td", { class: "right num", "data-label": `Arter ${per}` }, l.n_taxa ? fmtN(l.n_taxa) : "–"),
        h("td", { class: "nowrap muted", "data-label": "Senest set" }, l.last_date ? (l.last_date === today ? "i dag" : fmtDate(l.last_date, st.year === "alle" || l.last_date.slice(0, 4) !== String(YEAR))) : "–"))))),
      h("p", { style: { padding: "0 12px 8px", margin: 0 } }, builtEl),
      more_));
  }

  await load();
  search.focus();
  return () => { clearTimeout(timer); seq++; };
}
