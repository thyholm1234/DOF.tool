// Fotos → Admin → Kommentarer: anmeldte, skjulte og seneste kommentarer på fotos.
import { api, h, toast } from "../core.js";
import { BASE, dkDate, loadingBox } from "./common.js";

export async function page() {
  let tab = "anmeldte";
  const out = h("div", { class: "stack" }), body = h("div", {});
  async function load() {
    body.replaceChildren(loadingBox());
    const rows = await api(`/api/admin/fotos/kommentarer?tab=${tab}`);
    const act = async (r, action) => {
      let reason = "";
      if (action === "skjul" || action === "slet") { reason = prompt("Årsag:", "") ?? null; if (reason === null) return; }
      try { await api(`/api/admin/fotos/kommentarer/${r.id}/${action}`, { method: "POST", body: { reason } }); load(); } catch (e) { toast(e.message, "err"); }
    };
    body.replaceChildren(...(rows.length ? rows.map(r => h("div", { class: "card stack" },
      h("div", {}, h("a", { href: `${BASE}/${r.photo_id}?k=${r.id}` }, r.titel || `Foto ${r.photo_id}`), " · ", h("b", {}, r.navn || "–"), h("span", { class: "muted small" }, ` ${dkDate(r.created_at, { time: true })} · ${r.status}${r.report_count ? ` · 🚩 ${r.report_count}` : ""}`)),
      h("div", {}, r.body), ...(r.reports || []).map(x => h("div", { class: "muted small" }, `Anmeldt af ${x.af}: ${x.reason}${x.note ? " – " + x.note : ""}`)),
      h("div", { class: "row" }, r.status === "synlig" ? h("button", { class: "btn sm", onclick: () => act(r, "skjul") }, "Skjul") : null, r.status === "skjult" ? h("button", { class: "btn sm", onclick: () => act(r, "gendan") }, "Gendan") : null,
        r.report_count ? h("button", { class: "btn sm", onclick: () => act(r, "afvis") }, "Afvis anmeldelser") : null, h("button", { class: "btn sm danger", onclick: () => act(r, "slet") }, "Slet")))) : [h("p", { class: "muted" }, "Ingen kommentarer her.")]));
  }
  out.append(h("div", { class: "seg" }, ...[["anmeldte", "Anmeldte"], ["skjulte", "Skjulte"], ["seneste", "Seneste"]].map(([k, t]) => h("button", { type: "button", onclick: (e) => { tab = k; [...e.target.parentNode.children].forEach(b => b.classList.toggle("on", b === e.target)); load(); }, class: k === tab ? "on" : "" }, t))), body);
  await load();
  return out;
}
