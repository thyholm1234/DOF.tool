// Observatørsider (#/observator/KODE) og søgning efter observatører (#/observator) – services/observers.py.
// Tal fra DOFbasen-observationerne uden hemmelige; portalbrugere kan skjule deres side under Profil.
import { api, state, h, fmtN, fmtDate, tag, errorBox, empty, busyBlock, KAT, requireLogin } from "../core.js";
import { barChart } from "../charts.js";

const YEAR = new Date().getFullYear();

export async function render(view, [kode]) {
  if (requireLogin(view)) return;
  if (!state.user?.profiler) { view.append(empty("Observatørsider er ikke slået til", "Funktionen slås til under Admin → Funktioner.")); return; }
  if (!kode) return searchPage(view);
  kode = decodeURIComponent(kode);
  view.replaceChildren(busyBlock("Henter observatøren…", 5));
  let d;
  try { d = await api(`/api/observers/${encodeURIComponent(kode)}`); } catch (err) { view.replaceChildren(errorBox(err)); return; }
  view.replaceChildren(
    h("p", {}, h("a", { href: "#/observator" }, "← Søg observatør")),
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, d.navn),
      h("p", { class: "lead" }, d.obserkode, d.portalbruger ? " · bruger DOF.tool" : "", d.portal?.siden ? ` siden ${fmtDate(d.portal.siden)}` : "",
        d.portal?.afdelinger?.length ? ` · ${d.portal.afdelinger.join(", ")}` : ""))));
  if (d.skjult) { view.append(empty("Observatøren har valgt ikke at vise sin side", "")); return; }
  if (d.mig) view.append(h("div", { class: "notice mb" }, d.portal?.offentlig === false
    ? "Din side er skjult for andre brugere – du kan vise den under Profil." : "Sådan ser andre brugere din side. Du kan skjule den under Profil."));
  const t = d.tal;
  if (!t || !t.n_obs) { view.append(empty("Ingen observationer i DOFbasen endnu", "")); return; }
  const stat = (v, l, title) => h("div", { class: "stat", title }, h("b", {}, v), h("span", {}, l));
  const thisYear = d.aar.find(y => y.aar === YEAR);
  view.append(
    h("div", { class: "stats", style: { gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" } },
      stat(fmtN(t.n_arter), "arter i alt", "Krydsbare arter (kategori A–C) som på årslisterne"),
      stat(fmtN(thisYear?.n_arter ?? 0), `arter i ${YEAR}`),
      stat(fmtN(t.n_obs), "observationer"), stat(fmtN(t.n_dage), "dage i felten"), stat(fmtN(t.n_lok), "lokaliteter"),
      stat(t.first ? String(new Date(t.first).getFullYear()) : "–", "første år")),
    d.aar.length > 1 ? h("div", { class: "card mt" }, h("h3", { style: { marginTop: 0 } }, "Arter år for år"),
      barChart({ bars: d.aar.map(y => ({ v: y.n_arter, hl: y.aar === YEAR, tip: `${y.aar}: ${fmtN(y.n_arter)} arter · ${fmtN(y.n_obs)} obs. · ${fmtN(y.n_dage)} dage` })),
        xTicks: d.aar.map((y, i) => [i, y.aar]).filter(([, y]) => y % 5 === 0).map(([i, y]) => [i, String(y)]), yFormat: (v) => fmtN(Math.round(v)), height: 200 })) : null,
    h("div", { class: "grid-2 mt" },
      h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Bemærkelsesværdige fund det seneste år"),
        d.fund.length ? h("ul", { class: "mini-list" }, ...d.fund.map(f => h("li", {},
          h("span", {}, fmtDate(f.dato, false), " · ", h("a", { href: `#/traad/${f.dato}/${f.artnr}/${f.loknr}` }, f.art), h("small", { class: "muted" }, ` · ${f.lok}`)),
          h("span", {}, tag(f.kategori, KAT[f.kategori]), " ", h("b", { class: "num" }, fmtN(f.antal)))))) : h("p", { class: "muted" }, "Ingen SU, SUB eller Bemærk det seneste år.")),
      h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Foretrukne lokaliteter"),
        h("ul", { class: "mini-list" }, ...d.lokaliteter.map(l => h("li", {},
          h("a", { href: `#/lok/${l.loknr}` }, l.navn), h("span", { class: "muted" }, `${fmtN(l.dage)} dage · senest ${fmtDate(l.senest, false)}`)))))),
    h("div", { class: "card mt" }, h("h3", { style: { marginTop: 0 } }, "Seneste ture"),
      h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Dato"), h("th", {}, "Lokalitet"), h("th", { class: "right" }, "Arter"), h("th", { class: "right" }, "Obs."))),
        h("tbody", {}, ...d.ture.map(r => h("tr", {}, h("td", { class: "nowrap" }, fmtDate(r.dato)), h("td", {}, h("a", { href: `#/lok/${r.loknr}` }, r.lok)),
          h("td", { class: "right num" }, fmtN(r.arter)), h("td", { class: "right num" }, fmtN(r.obs)))))))),
    h("p", { class: "muted", style: { fontSize: ".82rem" } }, "Tallene kommer fra DOFbasen og opdateres løbende. Hemmelige observationer indgår ikke."));
}

function searchPage(view) {
  const out = h("div", { class: "stack mt" });
  let timer = null, seq = 0;
  const input = h("input", { type: "search", placeholder: "Navn eller obserkode …", autofocus: true, style: { maxWidth: "420px" }, oninput: () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { out.replaceChildren(); return; }
    timer = setTimeout(async () => {
      const my = ++seq;
      let rows;
      try { rows = await api(`/api/observers?q=${encodeURIComponent(q)}`); } catch (err) { out.replaceChildren(errorBox(err)); return; }
      if (my !== seq) return;
      out.replaceChildren(rows.length ? h("ul", { class: "mini-list" }, ...rows.map(r => h("li", {},
        h("a", { href: `#/observator/${encodeURIComponent(r.obserkode)}` }, r.navn), h("span", { class: "muted" }, r.obserkode)))) : empty("Ingen observatører fundet", ""));
    }, 200);
  } });
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Observatører"),
    h("p", { class: "lead" }, "Find en observatør og se tal, fund og foretrukne lokaliteter fra DOFbasen."))),
    h("div", { class: "card" }, input), out,
    state.user?.obserkode ? h("p", { class: "mt" }, h("a", { href: `#/observator/${encodeURIComponent(state.user.obserkode)}` }, "Se din egen side →")) : null);
}
