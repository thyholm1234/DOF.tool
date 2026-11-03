// Læring 2.0 (#/laer/…): e-læringsportal – forside, kurser, udforsk, kursus, øvelser, egne træninger, repetition, fremgang, bygger og forløb.
// Skal og faner her; hver side er en fil under laer/ (docs/laering-spec.md §4).
import { h, errorBox, requireLogin } from "../core.js";
import { BASE, loadCss, loadingBox } from "../laer/common.js";

const PAGES = {
  forside: () => import("../laer/forside.js"), kurser: () => import("../laer/kurser.js"), udforsk: () => import("../laer/udforsk.js"),
  kursus: () => import("../laer/kursus.js"), ovelse: () => import("../laer/ovelse.js"), traen: () => import("../laer/traening.js"),
  forbered: () => import("../laer/forbered.js"), repetition: () => import("../laer/repetition.js"), fremgang: () => import("../laer/fremgang.js"),
  dagens: () => import("../laer/dagens.js"), test: () => import("../laer/test.js"), byg: () => import("../laer/byg/oversigt.js"), forloeb: () => import("../laer/forloeb.js"), hold: () => import("../laer/hold.js"), bevis: () => import("../laer/bevis.js"),
};

/** Rute → [modul, argumenter]. */
function route(parts, info) {
  const [a, ...rest] = parts;
  if (a === undefined) return ["forside", []];
  if (["kurser", "udforsk", "repetition", "fremgang", "forbered"].includes(a)) return [a === "forbered" && !info.data ? null : a, rest];
  if (a === "kursus" || a === "ovelse" || a === "test") return [a, rest];
  if (a === "dagens") return info.data ? [a, rest] : null;
  if (a === "quiz") return ["kursus", rest];          // gammel adresse
  if (a === "auto") return ["ovelse", ["auto", ...rest]];
  if (a === "arter") return ["traen", ["foto"]];       // gammel adresse
  if (a === "lyd") return ["traen", ["lyd"]];
  if (a === "traen") return ["traen", rest];
  if (a === "byg" && info.byg) return ["byg", rest];
  if (a === "rediger") return info.byg ? ["byg", ["kursus", ...rest]] : "legacy";
  if (a === "forloeb" && info.forloeb) return ["forloeb", rest];
  if (a === "hold" && info.forloeb) return ["hold", rest];
  if (a === "bevis" && info.forloeb) return ["bevis", rest];
  return null;
}

export async function render(view, [sub], info) {
  if (requireLogin(view)) return;
  loadCss("laer");
  const parts = (sub || "").split("/").filter(Boolean);
  const spec = route(parts, info);
  if (spec === "legacy") return (await import("./learn.js")).legacy(view, [parts.join("/")]);
  const first = parts[0];
  const tab = (href, text, on) => h("a", { href, class: on ? "on" : "", "aria-current": on ? "page" : null }, text);
  const content = h("div", { class: "lr-content" }, loadingBox());
  const tabs = [tab(BASE, "Forside", first === undefined), tab(`${BASE}/kurser`, "Kurser", first === "kurser" || first === "kursus" || first === "quiz"),
    tab(`${BASE}/udforsk`, "Udforsk", first === "udforsk" || first === "ovelse" || first === "auto"), tab(`${BASE}/traen`, "Træn", ["traen", "arter", "lyd"].includes(first)),
    info.data ? tab(`${BASE}/forbered`, "Forbered tur", first === "forbered") : null,
    tab(`${BASE}/repetition`, "Repetition", first === "repetition"), tab(`${BASE}/fremgang`, "Fremgang", first === "fremgang"),
    info.forloeb ? tab(`${BASE}/forloeb`, "Forløb", ["forloeb", "hold", "bevis"].includes(first)) : null,
    info.byg ? tab(`${BASE}/byg`, "Byg", first === "byg" || first === "rediger") : null];
  view.replaceChildren(h("div", { class: "lr" },
    h("header", { class: "lr-head" }, h("h1", {}, h("a", { href: BASE }, "Læring")), h("span", { class: "spacer" })),
    h("nav", { class: "lr-tabs", "aria-label": "Læring" }, h("div", { class: "seg" }, ...tabs)), content));
  if (!spec) { content.replaceChildren(h("div", { class: "notice" }, "Siden findes ikke, eller funktionen er ikke slået til."), h("p", {}, h("a", { href: BASE }, "← Til forsiden"))); return; }
  try {
    const mod = await PAGES[spec[0]]();
    content.replaceChildren();
    return await mod.render(content, spec[1], info);
  } catch (err) {
    content.replaceChildren(errorBox(err));
  }
}
