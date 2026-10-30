// Admin → Support: samtaler med brugerne.
import { api, h, ago, seg, params, setParams, errorBox, empty } from "../../core.js";
import { conversation, KINDS } from "../support.js";

export async function supportPanel(body) {
  let status = "open";
  const list = h("div", { class: "stack" });
  const detail = h("div", {});
  body.append(h("div", { class: "row mb" }, seg([["open", "Åbne"], ["closed", "Lukkede"], ["all", "Alle"]], status, (v) => { status = v; load(); })),
    h("div", { class: "report-layout" }, list, detail));
  const load = async () => {
    try {
      const rows = await api(`/api/admin/support?status=${status}`);
      list.replaceChildren(...(rows.length ? rows.map(r => h("button", { type: "button", class: "loc-item", onclick: () => open(r.id) },
        h("span", {}, r.unread ? h("span", { class: "badge-inline", style: { marginLeft: 0, marginRight: "6px" } }, "nyt") : null, h("b", {}, r.subject),
          h("small", { class: "muted" }, ` · ${r.navn} · ${KINDS[r.kind] || r.kind} · ${r.n} besked${r.n === 1 ? "" : "er"}`)),
        h("small", { class: "muted" }, ago(r.updated_at)))) : [empty("Ingen samtaler")]));
    } catch (err) { list.replaceChildren(errorBox(err)); }
  };
  const open = async (id) => {
    setParams({ tab: "support", t: id });
    try { detail.replaceChildren(conversation(await api(`/api/support/${id}`), () => { load(); open(id); }, { admin: true })); load(); }
    catch (err) { detail.replaceChildren(errorBox(err)); }
  };
  await load();
  if (params().t) open(params().t);
}
