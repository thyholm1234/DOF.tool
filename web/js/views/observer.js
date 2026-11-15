// Observatørsider (#/observator/KODE) og søgning efter observatører (#/observator) – services/observers.py.
// Tal fra DOFbasen-observationerne uden hemmelige; portalbrugere kan skjule deres side under Profil. Krydslisterne (antal, placering) linker til
// observatørens kryds pr. liste (#/lister/person/KODE/LISTE), som linker tilbage hertil. Fotos i galleriet og artikler vises, når en fotograf
// eller forfatter er koblet til observatøren (obserkode eller portalbruger) – /api/observers/KODE/indhold.
import { api, state, h, fmtN, fmtDate, tag, errorBox, empty, busyBlock, KAT, requireLogin } from "../core.js";
import { barChart } from "../charts.js";

const YEAR = new Date().getFullYear();

export async function render(view, [kode]) {
  if (requireLogin(view)) return;
  if (!state.user?.profiler) { view.append(empty("Observatørsider er ikke slået til", "Funktionen slås til under Admin → Funktioner.")); return; }
  if (!kode) {  // søgningen er en fane under Statistik (hvis modulet er synligt), ellers en side for sig
    if (state.user?.statistik) { location.replace("#/statistik?tab=observatorer"); return; }
    return searchPage(view);
  }
  kode = decodeURIComponent(kode);
  view.replaceChildren(busyBlock("Henter observatøren…", 5));
  let d;
  const klOn = !!state.user?.krydslister?.features?.krydslister;
  const krydsP = klOn ? api(`/api/krydslister/person/${encodeURIComponent(kode)}`).catch(() => null) : Promise.resolve(null);
  const contentOn = !!(state.user?.fotos?.features?.fotos || state.user?.artikler?.features?.artikler);
  const contentP = contentOn ? api(`/api/observers/${encodeURIComponent(kode)}/indhold`).catch(() => null) : Promise.resolve(null);
  try { d = await api(`/api/observers/${encodeURIComponent(kode)}`); } catch (err) { view.replaceChildren(errorBox(err)); return; }
  view.replaceChildren(
    h("p", {}, h("a", { href: state.user?.statistik ? "#/statistik?tab=observatorer" : "#/observator" }, "← Søg observatør")),
    h("div", { class: "page-head", style: d.avatar ? { alignItems: "center", gap: "16px", justifyContent: "flex-start" } : null },
      d.avatar ? h("img", { class: "obs-avatar", src: d.avatar, alt: "", width: 96, height: 96, decoding: "async" }) : null,
      h("div", {}, h("h1", {}, d.navn),
      h("p", { class: "lead" }, d.obserkode, d.portalbruger ? " · bruger DOF.tool" : "", d.portal?.siden ? ` siden ${fmtDate(d.portal.siden)}` : "",
        d.portal?.afdelinger?.length ? ` · ${d.portal.afdelinger.join(", ")}` : ""))));
  if (d.skjult) { view.append(empty("Observatøren har valgt ikke at vise sin side", "")); return; }
  if (d.mig) view.append(h("div", { class: "notice mb" }, d.portal?.offentlig === false
    ? "Din side er skjult for andre brugere – du kan vise den under Profil." : "Sådan ser andre brugere din side. Du kan skjule den under Profil."));
  const kryds = await krydsP;
  const krydsCard = krydslisterCard(d, kryds);
  // Fotos og artikler hentes ved siden af og sættes ind, når de kommer (siden venter ikke på dem)
  const contentEl = h("div", {});
  contentP.then(c => contentEl.replaceChildren(...contentCards(c))).catch(() => {});
  const t = d.tal;
  if (!t || !t.n_obs) { view.append(empty("Ingen observationer i DOFbasen endnu", ""), krydsCard || "", contentEl); return; }
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
    krydsCard,
    contentEl,
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

/** Krydslisterne (antal og placering) med links til observatørens kryds pr. liste og til ranglisten. */
function krydslisterCard(d, kryds) {
  if (!kryds) return null;
  const head = h("h3", { style: { marginTop: 0 } }, "Krydslister");
  if (!kryds.participant) return d.mig ? h("div", { class: "card mt" }, head, h("p", { class: "muted" }, "Du er ikke med på krydslisterne, fordi du har valgt ikke at vise dine lister. Slå det til under ",
    h("a", { href: "#/profil" }, "Profil → Lister og privatliv"), ".")) : null;
  if (!kryds.lister.length) return null;
  const k = encodeURIComponent(d.obserkode);
  return h("div", { class: "card mt" }, head,
    h("ul", { class: "mini-list" }, ...kryds.lister.map(l => h("li", {},
      h("a", { href: `#/lister/person/${k}/${encodeURIComponent(l.key)}` }, l.name),
      h("span", {}, h("b", { class: "num" }, fmtN(l.antal)), " arter",
        l.placering ? [" · ", h("a", { class: "muted", href: `#/lister/kryds/${encodeURIComponent(l.key)}`, title: "Se ranglisten" }, `nr. ${fmtN(l.placering)} af ${fmtN(l.deltagere)}`)] : null)))),
    h("p", { class: "small", style: { marginBottom: 0 } }, d.mig ? h("a", { href: "#/lister" }, "Mine lister →") : h("a", { href: "#/lister/alle" }, "Alle krydslister →")));
}

/** Fotos i galleriet (gitter som galleriet) og artikler (kort som i arkivet), med links til det hele. */
function contentCards(c) {
  const out = [];
  const head = (title, total, n, href, label) => h("div", { class: "row between" },
    h("h3", { style: { margin: 0 } }, title), h("a", { href }, total > n ? `Se alle ${fmtN(total)} →` : label));
  if (c?.fotos?.rows?.length) {
    const f = c.fotos;
    const el = h("div", { class: "card mt" }, head(`Fotos i galleriet (${fmtN(f.total)})`, f.total, f.rows.length, `#/fotos/fotograf/${f.fotograf_id}`, "Fotografens side →"));
    import("../fotos/common.js").then(({ loadCss, photoCard }) => {
      loadCss("fotos");
      el.append(h("div", { class: "fot-grid mt" }, ...f.rows.map(p => photoCard(p))));
    }).catch(() => {});
    out.push(el);
  }
  if (c?.artikler?.rows?.length) {
    const a = c.artikler;
    const el = h("div", { class: "card mt" }, head(`Artikler (${fmtN(a.total)})`, a.total, a.rows.length, `#/artikler/soeg?forfatter_id=${a.forfatter_id}`, "Alle artikler →"));
    import("../artikler/common.js").then(({ loadCss, listCard }) => {
      loadCss("artikler");
      el.append(h("div", { class: "card art sp-articles mt", style: { boxShadow: "none", border: 0, padding: 0 } }, ...a.rows.map(listCard)));
    }).catch(() => {});
    out.push(el);
  }
  return out;
}

/** Søgefeltet og træffene (fanen Observatører under Statistik og den selvstændige side). */
export function searchTab(body) {
  const out = h("div", { class: "stack mt" });
  let timer = null, seq = 0;
  const input = h("input", { type: "search", placeholder: "Navn eller obserkode …", style: { maxWidth: "420px" }, oninput: () => {
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
  body.append(h("div", { class: "card" }, input), out,
    state.user?.obserkode ? h("p", { class: "mt" }, h("a", { href: `#/observator/${encodeURIComponent(state.user.obserkode)}` }, "Se din egen side →")) : null);
}

function searchPage(view) {
  const body = h("div", {});
  searchTab(body);
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Observatører"),
    h("p", { class: "lead" }, "Find en observatør og se tal, fund og foretrukne lokaliteter fra DOFbasen."))), body);
}
