// Moderation → Nyheder.
import { api, h, fmtDate, toast } from "../../core.js";

export async function content(body) {
  const title = h("input", { placeholder: "Overskrift" });
  const text = h("textarea", { placeholder: "Tekst" });
  const news = h("div", { class: "stack" });
  body.append(h("form", { class: "card stack", style: { maxWidth: "720px" }, onsubmit: async (e) => { e.preventDefault(); try { await api("/api/admin/news", { method: "POST", body: { title: title.value, body: text.value } }); title.value = text.value = ""; load(); toast("Publiceret"); } catch (err) { toast(err.message, "err"); } } },
    h("h3", {}, "Ny nyhed"), title, text, h("button", { class: "btn primary" }, "Publicér"), news));
  const load = async () => {
    const n = await api("/api/news?limit=20");
    news.replaceChildren(...n.map(x => h("div", { class: "row between" }, h("span", {}, h("b", {}, x.title), h("small", { class: "muted" }, ` ${fmtDate(x.created_at)}`)),
      h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/admin/news/${x.id}`, { method: "DELETE" }); load(); } }, "Slet"))));
  };
  await load();
}

// ---------------------------------------------------------------- moderation af kommentarer (moderator og op)
