// Moderation → Ture: portalens egne arrangementer.
import { api, h, icon, fmtDate, empty } from "../../core.js";
import { eventForm } from "../events.js";

export async function portalEvents(body) {
  const list = h("div", { class: "stack" });
  body.append(h("div", { class: "card stack" },
    h("div", { class: "row between" }, h("p", { class: "muted", style: { margin: 0 } }, "Ture og begivenheder oprettet i portalen. De vises under Ture sammen med DOF's officielle ture."),
      h("button", { class: "btn primary", html: `${icon("plus")} Ny tur`, onclick: () => eventForm(null, () => load()) })), list));
  const load = async () => {
    const rows = (await api("/api/events?past=true")).filter(e => e.source === "portal").sort((a, b) => b.dato.localeCompare(a.dato));
    list.replaceChildren(...(rows.length ? rows.map(e => h("div", { class: "mod-item" },
      h("div", { style: { minWidth: 0 } }, h("a", { href: `#/ture/${e.id}` }, h("b", {}, e.title)),
        h("div", { class: "muted", style: { fontSize: ".86rem" } }, [fmtDate(e.dato), e.tid_fra, e.lok, e.afdeling, e.created_by_navn && `oprettet af ${e.created_by_navn}`].filter(Boolean).join(" · "))),
      h("div", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } },
        h("button", { class: "btn sm ghost", html: icon("edit"), "aria-label": "Ret", onclick: async () => eventForm(await api(`/api/events/${e.id}`), () => load()) }),
        h("button", { class: "btn sm ghost danger", html: icon("trash"), "aria-label": "Slet", onclick: async () => {
          if (!confirm(`Slet "${e.title}"?`)) return; await api(`/api/events/${e.id}`, { method: "DELETE" }); load();
        } })))) : [empty("Ingen egne ture endnu")]));
  };
  await load();
}

// ---------------------------------------------------------------- listekategorier (moderator og op)
