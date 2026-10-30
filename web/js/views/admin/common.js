// Hjælpere, som flere admin-faner bruger.
import { api, state, h, toast } from "../../core.js";

export const st = (v, l) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, l));

// ---------------------------------------------------------------- lagerforbrug (måles på forespørgsel, gemmes 5 min på serveren)

/** Rollevalg for en bruger. Superadmin (fra .env) kan ikke ændres og kan ikke tildeles. */
export function roleSelect(u, names, onDone) {
  if (u.role === "superadmin") return h("span", { class: "tag plain", title: "Styres fra .env" }, "Superadmin");
  return h("select", { style: { width: "auto", height: "32px" }, disabled: u.id === state.user.id, onchange: async (e) => {
    try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { role: e.target.value } }); toast("Rolle ændret"); } catch (err) { toast(err.message, "err"); }
    onDone();
  } }, ...Object.entries(names).map(([k, n]) => h("option", { value: k, selected: k === u.role }, n)),
    names[u.role] ? null : h("option", { value: u.role, selected: true }, u.role));
}
