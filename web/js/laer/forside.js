// Læring 2.0: forside (docs/laering-spec.md §4.1).
import { api, h } from "../core.js";
import { BASE, bar } from "./common.js";
import { courseCard } from "./kort.js";

const METODE_TEKST = [
  ["Forudsig", "Hvad er sandsynligt her og nu – ud fra sted, årstid og levested?"],
  ["Opdag", "Hvad ser og hører du faktisk? Beskriv det, du kan bekræfte."],
  ["Tolk", "Hvad betyder sporene? Form, bevægelse, lyd og adfærd."],
  ["Adskil", "Hvilke arter kan forveksles, og hvad skiller dem?"],
  ["Beslut", "Hvilket niveau kan du bestemme fuglen på, og hvor sikker er du?"],
];

export async function render(view, _p, info) {
  const d = await api("/api/learn/katalog");
  const dag = [
    ["Lær at iagttage", "Kurser, der træner øjne og ører – før du navngiver.", `${BASE}/kurser`],
    ["Arbejd med det svære", "Lignende arter og dine egne fejl.", `${BASE}/repetition`],
    info.data ? ["Forbered din næste tur", "Vælg sted og dato – træn arterne, du sandsynligvis møder.", `${BASE}/forbered`] : null,
    ["Træn foto og lyd", "Gæt arten på fotos og optagelser – vælg selv arter.", `${BASE}/traen`],
  ].filter(Boolean);
  const spil = [["Færdighedstest", "20 spørgsmål – find ud af, hvor du skal starte.", `${BASE}/test`], info.data ? ["Fugl for fugl", "Dagens art: gæt den med så få ledetråde som muligt.", `${BASE}/dagens`] : null].filter(Boolean);
  const fortsaet = d.kurser.filter(c => c.progress && c.progress.done > 0 && c.progress.done < c.progress.total);
  const quiz = [
    info.artsquiz ? ["Artsquiz", "Gæt arten på fotos fra fotobiblioteket.", `${BASE}/traen/foto`] : null,
    info.lydquiz ? ["Lydquiz", "Sang og kald fra xeno-canto og Macaulay Library.", `${BASE}/traen/lyd`] : null,
    ...(info.data ? d.auto.map(a => [a.title, "Genereret af DOFbasens data.", `${BASE}/ovelse/auto/${a.kind}`]) : []),
  ].filter(Boolean);
  view.append(
    h("section", { class: `lr-hero${d.banner ? " lr-hero-photo" : ""}`, style: d.banner ? { "--lr-banner": `url("${d.banner.url}")` } : null },
      d.banner ? h("small", { class: "lr-credit" }, `${d.banner.art ? `${d.banner.art} · ` : ""}Foto: ${d.banner.credit}`) : null,
      h("div", {}, h("h2", {}, "Lær at fuglekigge – ikke kun at få svaret."),
      h("p", {}, "Træn det, erfarne fuglekiggere gør: forudsige, iagttage, sammenligne og beslutte – med rigtige danske fotos, lyde og DOFbasens observationer."),
      h("div", { class: "lr-actions" }, h("a", { class: "btn primary", href: `${BASE}/kurser` }, "Start et kursus"), h("a", { class: "btn", href: `${BASE}/traen` }, "Træn arter")))),
    d.forfald ? h("a", { class: "lr-due", href: `${BASE}/repetition` }, h("b", {}, d.forfald), ` ${d.forfald === 1 ? "ting" : "ting"} til repetition i dag →`) : null,
    fortsaet.length ? [h("h2", {}, "Fortsæt hvor du slap"), h("div", { class: "lr-grid" }, ...fortsaet.map(courseCard))] : null,
    h("h2", {}, "Hvad vil du i dag?"),
    h("div", { class: "lr-grid four" }, ...dag.map(([t, s, href]) => h("a", { class: "lr-tile", href }, h("h3", {}, t), h("p", { class: "muted" }, s)))),
    h("h2", {}, "Kurserne"), d.kurser.length ? h("div", { class: "lr-grid" }, ...d.kurser.map(courseCard)) : h("p", { class: "muted" }, "Der er ingen publicerede kurser endnu."),
    h("h2", {}, "Metoden"), h("ol", { class: "lr-method" }, ...METODE_TEKST.map(([t, s], i) => h("li", {}, h("b", {}, `${i + 1}. ${t}`), h("span", {}, s)))),
    h("h2", {}, "Test og spil"), h("div", { class: "lr-grid four" }, ...spil.map(([t, s, href]) => h("a", { class: "lr-tile", href }, h("h3", {}, t), h("p", { class: "muted" }, s)))),
    quiz.length ? [h("h2", {}, "Quizzer"), h("div", { class: "lr-grid four" }, ...quiz.map(([t, s, href]) => h("a", { class: "lr-tile", href }, h("h3", {}, t), h("p", { class: "muted" }, s))))] : null,
    d.oevelser.length ? [h("h2", {}, "Øvelser"), h("div", { class: "lr-grid" }, ...d.oevelser.slice(0, 6).map(courseCard)), h("p", {}, h("a", { href: `${BASE}/udforsk` }, "Se alle øvelser →"))] : null,
    d.forloeb?.length ? [h("h2", {}, "Forløb"), h("div", { class: "lr-grid" }, ...d.forloeb.map(p => h("a", { class: "lr-tile", href: `${BASE}/forloeb/${p.slug}` }, h("h3", {}, p.title), h("p", { class: "muted" }, p.summary))))] : null);
}
