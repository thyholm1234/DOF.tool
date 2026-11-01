// Admin → Indstillinger: deltagere, antal årslister og tekstsiderne «Om krydslister» og «Krydsregler».
import { api, h, toast } from "../core.js";
import { dkDate } from "./common.js";

export async function page() {
  const d = await api("/api/admin/krydslister/indstillinger");
  const part = h("select", {}, ...[["brugere", "Portalens brugere, der viser deres lister"], ["aktive", "De mest aktive DOFbasen-observatører (demo)"]].map(([v, t]) => h("option", { value: v, selected: v === d.participants }, t)));
  const years = h("input", { type: "number", min: 1, max: 15, value: d.year_lists }), maxA = h("input", { type: "number", min: 10, max: 2000, value: d.aktive_max });
  const om = h("textarea", { rows: 10, style: { width: "100%" } }), regler = h("textarea", { rows: 14, style: { width: "100%" } });
  om.value = d.tekster.om || ""; regler.value = d.tekster.regler || "";
  return h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try { await api("/api/admin/krydslister/indstillinger", { method: "PUT", body: { participants: part.value, year_lists: +years.value, aktive_max: +maxA.value, tekster: { om: om.value, regler: regler.value } } }); toast("Gemt – listerne genberegnes"); } catch (x) { toast(x.message, "err"); }
  } }, h("p", { class: "muted small" }, d.auto_at ? `Seneste automatiske beregning: ${dkDate(d.auto_at, true)}` : "Den automatiske beregning har ikke kørt endnu."),
    h("label", { class: "field" }, h("span", {}, "Deltagere"), part), h("label", { class: "field" }, h("span", {}, "Antal årslister"), years), h("label", { class: "field" }, h("span", {}, "Antal aktive observatører (demo)"), maxA),
    h("label", { class: "field" }, h("span", {}, "Om krydslister (markdown)"), om), h("label", { class: "field" }, h("span", {}, "Krydsregler (markdown)"), regler), h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem")));
}
