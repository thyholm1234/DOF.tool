// Opret og ret opslag (docs/snak-modul-spec.md §2.4). Samme komponent bruges inline i feedet og på #/snak/ny og #/snak/<id>/rediger.
import { api, h, toast, store } from "../core.js";
import { ctx } from "./common.js";

/** composer({categories, post?, category?, onDone, onCancel}) → <form>. post: det opslag, der rettes. */
export function composer({ categories, post = null, category = null, onDone, onCancel }) {
  const usable = categories.filter(c => !c.arkiveret && (!c.kun_staff || ctx().can_moderate || ctx().can_admin));
  const remembered = store("snak")?.kategori;
  const start = post?.category?.id || usable.find(c => c.slug === category)?.id || usable.find(c => c.id === remembered)?.id || usable.find(c => c.slug === "fund")?.id || usable[0]?.id;
  const sel = h("select", { "aria-label": "Kategori", disabled: !!post }, ...usable.map(c => h("option", { value: c.id, selected: c.id === start }, c.navn)));
  const titel = h("input", { type: "text", maxlength: 200, placeholder: "Titel", "aria-label": "Titel" });
  const body = h("textarea", { rows: 5, maxlength: 10000, placeholder: "Hvad har du på hjerte?", "aria-label": "Opslag" });
  const hint = h("small", { class: "muted" });
  if (post) { titel.value = post.titel || ""; body.value = post.md || ""; }
  const btn = h("button", { class: "btn primary", type: "submit" }, post ? "Gem" : "Slå op");
  const sync = () => {
    const c = usable.find(x => x.id === +sel.value);
    titel.required = !!c?.kraever_titel;
    titel.placeholder = c?.kraever_titel ? "Titel (påkrævet)" : "Titel (valgfri)";
    hint.textContent = c?.beskrivelse || "";
  };
  sel.addEventListener("change", sync);
  sync();
  return h("form", { class: "sn-composer stack", onsubmit: async (e) => {
    e.preventDefault();
    btn.disabled = true;
    try {
      const payload = { category_id: +sel.value, titel: titel.value.trim() || null, body: body.value };
      const r = post ? await api(`/api/snak/${post.id}`, { method: "PUT", body: payload }) : await api("/api/snak", { method: "POST", body: payload });
      if (!post) store("snak", { ...(store("snak") || {}), kategori: +sel.value });
      toast(post ? "Gemt" : "Dit opslag er lagt op");
      onDone?.(post ? post.id : r.id);
    } catch (err) { toast(err.message, "err"); }
    btn.disabled = false;
  } },
  h("div", { class: "row" }, sel, hint),
  titel, body,
  h("div", { class: "row between" }, h("small", { class: "muted" }, "Du kan bruge **fed**, _kursiv_, lister og [links](https://…)."),
    h("span", { class: "row" }, onCancel ? h("button", { class: "btn", type: "button", onclick: onCancel }, "Annullér") : null, btn)));
}
