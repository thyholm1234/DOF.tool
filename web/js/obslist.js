// Observationslisten i DOFbasens gamle, kompakte stil – fælles for Observationer (dagslisten) og Søg.
// Ét skema uden kolonnetitler med faste kolonnebredder: noteikoner forrest, så antal, alder/køn/dragt, adfærd,
// billede/lyd, fuglens position, tidspunkt, art eller lokalitet og observatør. Ikonerne er links til observationen; ved
// hover vises noten (og for uret tidsrummet). Nålen har ingen boks – den siger kun, at positionen er angivet.
import { state, h, fmtN, icon } from "./core.js";

/** Artsnavn som link; for SU/SUB-arter er selve navnet et mærkat (hvid fed skrift på kategoriens farve).
 *  Bemærk-kategorien vises ikke. */
export const artName = (artnr, navn, kat) => h("a", { href: `#/art/${artnr}`, class: kat === "SU" || kat === "SUB" ? `dl-rare ${kat}` : null, title: kat === "SU" || kat === "SUB" ? kat : null }, navn);

export const sexSign = (k) => !k ? "" : k.includes("M") && k.includes("F") ? " ♂♀" : k.includes("M") ? " ♂" : k.includes("F") ? " ♀" : "";
// Trækkende: flere retninger (N, S, SØ …) på samme linje samles til ét «T»; øvrige adfærdskoder bevares
const DIR = /^(T|TF|TI|TU|N|NNØ|NØ|ØNØ|Ø|ØSØ|SØ|SSØ|S|SSV|SV|VSV|V|VNV|NV|NNV)( |$)/;
export function adf(code) {
  const codes = String(code || "").split(", ").filter(Boolean);
  const dirs = codes.filter(c => DIR.test(c));
  return dirs.length > 1 ? ["T", ...codes.filter(c => !DIR.test(c))].join(", ") : codes.join(", ");
}

/** Den enkelte observation: egen side, når Observationer er slået til – ellers tråden. */
const obsHref = (r) => state.user?.dagsliste ? `#/obs/${r.obsid}` : `#/traad/${r.dato}/${r.artnr}/${r.loknr}`;

/** Forklaring af urene – vises over listen. */
export const timeLegend = () => h("p", { class: "muted dl-legend" },
  h("span", { class: "dl-ic dl-tid-obs", html: icon("now") }), " observationens eget tidspunkt  ",
  h("span", { class: "dl-ic dl-tid-tur", html: icon("now") }), " kun turens tidsrum – peg på uret for at se tiden");

/** Boks ved hover over et ikon: viser noten/forklaringen ved siden af ikonet (data-pop-t = overskrift, data-pop = indhold). */
let pop = null;
export function initPopup(root) {
  const hide = () => { pop?.remove(); pop = null; };
  const show = (el) => {
    hide();
    const t = el.dataset.popT, txt = el.dataset.pop;
    pop = h("div", { class: "dl-pop", role: "tooltip" }, h("b", {}, t), txt ? h("div", {}, txt) : null);
    document.body.append(pop);
    const r = el.getBoundingClientRect(), w = pop.offsetWidth, ph = pop.offsetHeight;
    const left = Math.max(8, Math.min(r.left, innerWidth - w - 8));
    pop.style.left = `${left}px`;
    pop.style.top = `${r.bottom + ph + 8 > innerHeight && r.top > ph + 8 ? r.top - ph - 6 : r.bottom + 6}px`;
  };
  root.addEventListener("mouseover", e => { const el = e.target.closest?.("[data-pop-t]"); if (el) show(el); else if (!e.target.closest?.(".dl-pop")) hide(); });
  root.addEventListener("mouseleave", hide);
  root.addEventListener("focusin", e => { const el = e.target.closest?.("[data-pop-t]"); if (el) show(el); });
  root.addEventListener("focusout", hide);
  addEventListener("hashchange", hide, { once: true });
  addEventListener("scroll", hide, { passive: true });
}

const icoLink = (r, cls, ic, title, text) => h("a", { class: `dl-ic ${cls}`, href: obsHref(r), "data-pop-t": title, "data-pop": typeof text === "string" ? text : "",
  "aria-label": typeof text === "string" ? `${title}: ${text}` : title, html: icon(ic) });
const note = (r, cls, title, text) => text || text === true ? icoLink(r, cls, "chat", title, text) : null;
// Fem faste pladser, så hver slags kommentar står i sin egen kolonne hele vejen ned (tom plads, når den mangler)
const notes = (r) => h("td", { class: "dl-notes" }, h("span", { class: "dl-slots" },
  ...[r.n_obs > 1 ? icoLink(r, "dl-sum", "chat", "Sammensat observation", `Summeret af ${r.n_obs} observationer`) : null,
    note(r, "dl-tur", "Turnote", r.turnote || r.turkommentar),
    note(r, "dl-vejr", "Vejrnote", r.vejrnote || r.vejrkommentar),
    note(r, "dl-obs", "Observationsnote", r.obsnote || r.kommentar),
    r.medobser ? icoLink(r, "dl-med", "chat", r.medobsnote || "Medobservatører", true) : null,  // feltets indhold (fx «MV») vises i stedet for en generisk tekst
  ].map(el => el || h("span", {}))));
const extra = (r) => h("td", { class: "dl-extra" },
  r.foto ? icoLink(r, "", "camera", "Foto", true) : null,
  r.lyd ? icoLink(r, "", "sound", "Lydoptagelse", true) : null);
const posCell = (r) => h("td", { class: "dl-pos" }, r.position
  ? h("a", { class: "dl-ic", href: obsHref(r), "aria-label": "Fuglens position er angivet", html: icon("pin") }) : null);

/** Tidsrum som tekst: «07:42–07:50», «07:42» (kun ét tidspunkt) eller «til 08:00» (kun sluttid). */
export const timeSpan = (fra, til) => fra && til && fra !== til ? `${fra}–${til}` : fra || (til ? `til ${til}` : "");
/** Uret: udfyldt, når observationen har sit eget tidspunkt (og turens tidsrum står under i boksen); som omrids, når
 *  kun turens tidsrum kendes. Boksens overskrift siger altid, hvilket tidspunkt det er. */
const timeCell = (r) => {
  const obs = timeSpan(r.obstidfra, r.obstidtil), tur = timeSpan(r.turtidfra, r.turtidtil);
  const own = r.n_obs > 1 ? "Observationernes tidsrum" : "Observationens tidspunkt";
  return h("td", { class: "dl-tid" },
    obs ? icoLink(r, "dl-tid-obs", "now", own, tur ? `${obs}\nTuren: ${tur}` : obs)
      : tur ? icoLink(r, "dl-tid-tur", "now", "Turens tidsrum", `${tur}\nObservationen har ikke sit eget tidspunkt`) : null);
};
const ald = (r) => [r.alder, sexSign(r.koen).trim(), r.dragt].filter(Boolean).join(" ");
const obsLink = (r) => r.obserkode ? h("a", { href: `#/observator/${r.obserkode}` }, r.observator || r.obserkode) : (r.observator || "");
const lokLink = (r) => r.loknr ? h("a", { href: `#/lok/${r.loknr}` }, r.lokalitet || String(r.loknr)) : (r.lokalitet || "");

/** DOFbasens DKU-status som lille mærke efter antallet (farve: til behandling / behandlet af DKU / godkendt af SU). */
export const dkuBadge = (r) => r.dku_status ? h("span", { class: `dl-dku ${r.dku_kind || "dku"}`, title: r.dku_status }, "DKU") : null;
/** Gyldent SU-mærke: observationen af en SU-art er godkendt af SU (ingen mærke, før den er godkendt). */
export const suBadge = (r) => r.su_ok ? h("span", { class: "dl-dku su-ok", title: "Godkendt af SU (Sjældenhedsudvalget)" }, "SU") : null;

/** Én række. what: "art" (gruppen er en lokalitet), "lok" (gruppen er en art) eller "begge" (art · lokalitet);
 *  pre: tekst foran (fx datoen, når listen ikke er grupperet efter dato). */
export const obsRow = (r, what, pre = null) => h("tr", {},
  notes(r),
  h("td", { class: "num" }, h("a", { href: obsHref(r), title: r.n_obs > 1 ? `Summeret af ${r.n_obs} observationer` : "Se observationen" }, fmtN(r.antal)), suBadge(r), dkuBadge(r)),
  h("td", {}, ald(r)), h("td", {}, adf(r.adfkode)), extra(r), posCell(r), timeCell(r),
  h("td", {}, pre ? h("span", { class: "muted" }, pre, " · ") : null,
    what === "art" ? artName(r.artnr, r.art, r.kat) : what === "lok" ? lokLink(r) : [artName(r.artnr, r.art, r.kat), h("span", { class: "muted" }, " · "), lokLink(r)],
    h("span", { class: "dl-obs-m" }, obsLink(r))),  // på smalle skærme står observatøren under lokaliteten
  h("td", { class: "dl-obs" }, obsLink(r)));

/** Overskriftslinje i skemaet (art, lokalitet, dato – eller lokalafdeling med cls "dl-afd"). */
export const head = (content, cls = "dl-head") => h("tr", { class: cls }, h("td", { colspan: 9 }, ...content));

export const sheet = (bodyRows) => h("div", { class: "table-wrap" }, h("table", { class: "dl-table" },
  h("colgroup", {}, h("col", { class: "c-notes" }), h("col", { class: "c-num" }), h("col", { class: "c-ald" }), h("col", { class: "c-adf" }),
    h("col", { class: "c-extra" }), h("col", { class: "c-pos" }), h("col", { class: "c-tid" }), h("col", { class: "c-lok" }), h("col", { class: "c-obs" })),
  h("tbody", {}, ...bodyRows)));
