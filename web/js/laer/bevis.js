// Læring 2.0: bevis for et gennemført forløb – kan printes og verificeres via nummeret (docs/laering-spec.md §9.1).
import { api, h } from "../core.js";
import { BASE, backLink } from "./common.js";

export async function render(view, [no]) {
  const c = await api(`/api/learn/bevis/${encodeURIComponent(no)}`);
  const d = new Date(c.completed_at);
  view.append(backLink(`${BASE}/forloeb`, "Forløb"), h("div", { class: "lr-cert" }, h("p", { class: "lr-kicker" }, "Bevis for gennemført forløb"), h("h2", {}, c.navn), h("p", {}, "har gennemført forløbet"), h("h3", {}, c.title),
    c.hold ? h("p", {}, `på holdet «${c.hold}»`, c.undervisere ? ` med ${c.undervisere}` : "") : null, c.certificate_text ? h("p", { class: "muted" }, c.certificate_text) : null,
    h("p", {}, d.toLocaleDateString("da-DK", { day: "numeric", month: "long", year: "numeric" })), h("p", { class: "muted small" }, `Bevisnummer ${c.certificate_no} · udstedt via DOF.tool`),
    h("div", { class: "lr-actions no-print" }, h("button", { class: "btn primary", onclick: () => window.print() }, "Print"))));
}
