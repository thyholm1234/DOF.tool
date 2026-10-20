// Arter: indgang til alle arters sider – søg, filtrér på klasse og sortér efter systematik, navn eller årets tal.
import { api, h, fmtN, fmtDate, tag, empty, errorBox, seg, store, sortBar, todayISO } from "../core.js";

const KLASSER = [["all", "Alle"], ["SU", "SU"], ["SUB", "SUB"], ["Alm", "Alm."]];
const TYPES = [["art", "Arter"], ["all", "Alle taxa"]];
const YEAR = new Date().getFullYear();

export async function render(view) {
  const saved = store("arter") || {};
  const st = { q: "", klass: saved.klass || "all", type: saved.type || "art", seen: !!saved.seen };
  const sort = { key: saved.sort || "sortering", dir: saved.dir || 1 };
  const save = () => store("arter", { klass: st.klass, type: st.type, seen: st.seen, sort: sort.key, dir: sort.dir });
  const out = h("div", { style: { marginTop: "12px" } });
  const countEl = h("small", { class: "muted" });
  const search = h("input", { type: "search", placeholder: "Søg dansk eller latinsk navn…", autofocus: true,
    oninput: () => { st.q = search.value.trim().toLowerCase(); draw(); },
    onkeydown: (e) => { if (e.key === "Enter") { const first = filtered()[0]; if (first) location.hash = `#/art/${first.artnr}`; } } });
  const seenBtn = h("button", { type: "button", class: "twostate", title: "Kun arter set i år", onclick: () => { st.seen = !st.seen; save(); draw(); } }, `Set i ${YEAR}`);
  const bar = sortBar([["sortering", "Systematisk"], ["navn", "Navn"], ["n_year", `Obs. ${YEAR}`], ["last_date", "Senest set"]], sort, () => { save(); draw(); });

  view.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Arter"), h("p", { class: "lead" }, "Alle arter i DOFbasen – åbn en art for fænologi, fund, atlas og punkttællinger."))),
    h("div", { class: "card toolbox" },
      search,
      h("div", { class: "toolbar" },
        seg(KLASSER, st.klass, (v) => { st.klass = v; save(); draw(); }),
        seg(TYPES, st.type, (v) => { st.type = v; save(); draw(); }),
        seenBtn, h("span", { class: "spacer" }), countEl),
      bar),
    out);

  let all = [];
  try { all = await api("/api/species/index"); } catch (err) { out.replaceChildren(errorBox(err)); return; }

  function filtered() {
    const q = st.q;
    const cmp = {
      sortering: (a, b) => (a.sortering ?? 1e9) - (b.sortering ?? 1e9),
      navn: (a, b) => a.navn.localeCompare(b.navn, "da"),
      n_year: (a, b) => a.n_year - b.n_year,
      last_date: (a, b) => (a.last_date || "").localeCompare(b.last_date || ""),
    }[sort.key];
    return all.filter(s => (st.klass === "all" || s.klass === st.klass)
        && (st.type === "all" || s.art_type === "art")
        && (!st.seen || s.n_year > 0)
        && (!q || s.navn.toLowerCase().includes(q) || (s.latin || "").toLowerCase().includes(q)))
      .sort((a, b) => sort.dir * cmp(a, b) || a.navn.localeCompare(b.navn, "da"))
      // Ved søgning står navne der begynder med søgeordet først
      .sort((a, b) => q ? (b.navn.toLowerCase().startsWith(q) - a.navn.toLowerCase().startsWith(q)) : 0);
  }

  function draw() {
    seenBtn.classList.toggle("on", st.seen);
    const rows = filtered();
    countEl.textContent = `${fmtN(rows.length)} af ${fmtN(all.length)}`;
    if (!rows.length) { out.replaceChildren(empty("Ingen arter matcher", "Prøv et andet navn eller slå filtrene fra.")); return; }
    const today = todayISO();
    out.replaceChildren(h("div", { class: "card table-wrap" }, h("table", { class: "data stack-sm species-index" },
      h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Klasse"), h("th", { class: "right" }, `Obs. ${YEAR}`), h("th", { class: "right" }, "30 dage"),
        h("th", {}, `Først ${YEAR}`), h("th", {}, "Senest set"))),
      h("tbody", {}, ...rows.slice(0, 600).map(s => h("tr", { class: "click", onclick: () => { location.hash = `#/art/${s.artnr}`; } },
        h("td", { class: "wide" }, h("a", { href: `#/art/${s.artnr}`, onclick: (e) => e.stopPropagation() }, h("b", {}, s.navn)),
          s.latin ? h("i", { class: "muted", style: { marginLeft: "6px", fontSize: ".85rem" } }, s.latin) : null,
          s.art_type !== "art" ? h("span", { class: "tag plain", style: { marginLeft: "6px" } }, s.art_type) : null,
          s.n_today ? h("span", { class: "tag portal", style: { marginLeft: "6px" }, title: `${s.n_today} obs. i dag` }, "i dag") : null),
        h("td", { "data-label": "Klasse" }, s.klass !== "Alm" ? tag(s.klass) : h("span", { class: "muted" }, "Alm.")),
        h("td", { class: "right num", "data-label": `Obs. ${YEAR}` }, s.n_year ? fmtN(s.n_year) : "–"),
        h("td", { class: "right num", "data-label": "30 dage" }, s.n_30 ? fmtN(s.n_30) : "–"),
        h("td", { class: "nowrap", "data-label": `Først ${YEAR}` }, s.first_year ? fmtDate(s.first_year, false) : "–"),
        h("td", { class: "nowrap muted", "data-label": "Senest set" }, s.last_date ? (s.last_date === today ? "i dag" : fmtDate(s.last_date, s.last_date.slice(0, 4) !== String(YEAR))) : "–"))))),
      rows.length > 600 ? h("p", { class: "muted", style: { padding: "8px 12px" } }, `Viser de første 600 – søg for at finde resten.`) : null));
  }
  draw();
  search.focus();
}
