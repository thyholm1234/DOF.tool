// Læring 2.0: hold – mine hold, tilmelding, holdside og undervisernes oversigt (docs/laering-spec.md §9.2).
import { api, h, toast, errorBox, empty, modal } from "../core.js";
import { BASE, backLink, chip, loadingBox } from "./common.js";

export async function render(view, [id], info) {
  if (!id) return list(view, info, true);
  const d = await api(`/api/learn/hold/${id}`);
  const reload = () => { view.replaceChildren(); return render(view, [id], info); };
  const head = h("section", { class: "lr-course-head" }, h("div", {}, h("div", { class: "lr-kicker" }, "Hold"), h("h2", {}, d.title), h("p", {}, "Forløb: ", h("a", { href: `${BASE}/forloeb/${d.path.slug}?hold=${d.id}` }, d.path.title)),
    h("div", { class: "lr-meta" }, d.archived ? chip("arkiveret", "warn") : null, d.teacher ? chip(`Holdkode: ${d.join_code}`, "soft") : null),
    h("div", { class: "lr-actions" }, h("a", { class: "btn primary", href: `${BASE}/forloeb/${d.path.slug}?hold=${d.id}` }, "Åbn forløbet"),
      h("button", { class: "btn ghost", onclick: async () => { if (confirm("Forlad holdet?")) { try { await api(`/api/learn/hold/${id}/forlad`, { method: "POST" }); location.hash = `${BASE}/hold`; } catch (e) { toast(e.message, "err"); } } } }, "Forlad holdet"))));
  const members = h("div", {}, h("h3", {}, `Deltagere (${d.members.length})`), h("div", { class: "lr-chips" }, ...d.members.map(m => chip(m.navn + (m.role === "underviser" ? " · underviser" : ""), m.role === "underviser" ? "soft" : ""))));
  view.append(backLink(`${BASE}/hold`, "Hold"), head, members);
  if (!d.teacher) return;
  const mx = d.matrix;
  view.append(h("h3", {}, "Fremgang"),
    h("div", { class: "lr-scroll" }, h("table", { class: "lr-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Deltager"), ...mx.punkter.map(p => h("th", { title: p.title }, p.title.slice(0, 18))))),
      h("tbody", {}, ...mx.deltagere.map(r => h("tr", {}, h("td", {}, r.navn), ...mx.punkter.map(p => { const c = r.punkter[p.id]; return h("td", { class: c.done ? "ok" : c.venter ? "wait" : "" }, c.done ? "✓" : c.venter ? "…" : "", c.pct != null ? h("small", { class: "muted" }, ` ${c.pct}%`) : null); })))))),
    h("div", { class: "lr-actions" }, h("a", { class: "btn", href: `/api/learn/hold/${id}/fremgang.csv` }, "Hent som CSV"),
      h("button", { class: "btn", onclick: () => { const t = h("textarea", { rows: 4, maxlength: 500, placeholder: "Besked til holdet" }); const m = modal("Besked til holdet", h("div", { class: "stack" }, t, h("button", { class: "btn primary", onclick: async () => { try { const r = await api(`/api/learn/hold/${id}/besked`, { method: "POST", body: { tekst: t.value } }); toast(`Sendt til ${r.sendt}`); m.close(); } catch (e) { toast(e.message, "err"); } } }, "Send"))); } }, "Skriv til holdet"),
      h("button", { class: "btn ghost", onclick: async () => { await api(`/api/learn/hold/${id}`, { method: "PUT", body: { archived: !d.archived } }); reload(); } }, d.archived ? "Genåbn" : "Arkivér")),
    d.venter?.length ? [h("h3", {}, "Feltopgaver til godkendelse"), h("div", { class: "lr-list" }, ...d.venter.map(v => h("div", { class: "lr-listrow" },
      h("span", {}, `${v.navn}: ${v.title || "feltopgave"} – ${v.obs?.skjult ? "observation (sted skjult)" : v.obs ? `${v.obs.navn}, ${v.obs.dato}${v.obs.sted ? " · " + v.obs.sted : ""}` : ""}`),
      h("span", { class: "lr-meta" }, h("button", { class: "btn sm primary", onclick: async () => { await api(`/api/learn/hold/${id}/felt/${v.path_item_id}/${v.user_id}`, { method: "POST", body: { godkend: true } }); reload(); } }, "Godkend"),
        h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/learn/hold/${id}/felt/${v.path_item_id}/${v.user_id}`, { method: "POST", body: { godkend: false } }); reload(); } }, "Afvis")))))] : null,
    (mx.svageste.arter.length || mx.svageste.aktiviteter.length) ? [h("h3", {}, "Hvor holdet har sværest ved det (90 dage)"),
      h("div", { class: "lr-two" }, h("div", {}, h("b", {}, "Arter"), h("ul", {}, ...mx.svageste.arter.map(a => h("li", {}, `${a.navn}: ${a.fejl} % forkerte (${a.n} svar)`)))),
        h("div", {}, h("b", {}, "Aktiviteter"), h("ul", {}, ...mx.svageste.aktiviteter.map(a => h("li", {}, `${a.prompt}: ${a.fejl} % forkerte`)))))] : null);
}

export async function list(view, info, page = false) {
  const body = h("div", {}, loadingBox());
  (page ? view : view).append(...(page ? [h("div", { class: "lr-intro" }, h("h2", {}, "Mine hold")), body] : []));
  const target = page ? body : view;
  try {
    const rows = await api("/api/learn/hold");
    const canTeach = info.underviser || info.editor;
    const paths = canTeach ? (await api("/api/learn/forloeb")).forloeb : [];
    const create = () => {
      const t = h("input", { placeholder: "Navn på holdet", required: true }), p = h("select", {}, ...paths.map(x => h("option", { value: x.slug }, x.title))), d = h("input", { type: "date" });
      const m = modal("Nyt hold", h("form", { class: "stack", onsubmit: async (e) => { e.preventDefault(); try { const r = await api("/api/learn/hold", { method: "POST", body: { path: p.value, title: t.value, starts_on: d.value ? new Date(d.value).toISOString() : null, open_join: true } }); m.close(); location.hash = `${BASE}/hold/${r.id}`; } catch (er) { toast(er.message, "err"); } } },
        h("label", { class: "lr-f" }, h("span", {}, "Forløb"), p), h("label", { class: "lr-f" }, h("span", {}, "Navn"), t), h("label", { class: "lr-f" }, h("span", {}, "Startdato"), d), h("button", { class: "btn primary" }, "Opret hold")));
    };
    target.replaceChildren(canTeach ? h("div", { class: "lr-actions" }, h("button", { class: "btn primary", onclick: create }, "+ Nyt hold")) : null,
      rows.length ? h("div", { class: "lr-list" }, ...rows.map(r => h("a", { class: "lr-listrow", href: `${BASE}/hold/${r.id}` }, h("b", {}, r.title), h("span", { class: "muted" }, `${r.path?.title || ""} · ${r.role}${r.archived ? " · arkiveret" : ""}`)))) : empty("Du er ikke på et hold endnu", "Brug en holdkode under Forløb."));
  } catch (e) { target.replaceChildren(errorBox(e)); }
}
