// Admin → Godkendelse: manuelle kryds på lister med regler.godkendelse (fx Falsk VP). Godkend eller afvis med besked til deltageren.
import { api, h, toast } from "../core.js";
import { dkDate } from "./common.js";

export async function page() {
  const rows = await api("/api/admin/krydslister/godkendelse");
  const act = async (r, action) => {
    const reason = prompt(action === "afvis" ? "Begrundelse (sendes til deltageren):" : "Evt. besked til deltageren:", "");
    if (reason === null) return;
    try { await api(`/api/admin/krydslister/godkendelse/${r.id}/${action}`, { method: "POST", body: { reason } }); toast("Behandlet"); location.reload(); } catch (e) { toast(e.message, "err"); }
  };
  return h("div", { class: "stack" }, rows.length ? rows.map(r => h("div", { class: "card stack" },
    h("div", {}, h("b", {}, r.navn), " ", h("i", {}, r.latin), ` · ${r.liste} · ${r.bruger || r.obserkode}`), h("div", { class: "muted small" }, [r.dato, r.lokalitet, r.land].filter(Boolean).join(" · "), ` · indsendt ${dkDate(r.created_at, true)}`),
    r.note ? h("div", {}, r.note) : null, h("div", { class: "row" }, h("button", { class: "btn sm primary", type: "button", onclick: () => act(r, "godkend") }, "Godkend"), h("button", { class: "btn sm danger", type: "button", onclick: () => act(r, "afvis") }, "Afvis"))))
    : [h("p", { class: "muted" }, "Ingen kryds venter på godkendelse.")]);
}
