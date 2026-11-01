// Tekstsiden «Retningslinjer» (redigeres under Fotos → Admin → Indstillinger).
import { api, h } from "../core.js";

export async function retningslinjer() {
  const d = await api("/api/fotos/sider/retningslinjer");
  return h("div", { class: "fot-text stack" }, h("h2", {}, "Retningslinjer for fotos"),
    d.html ? h("div", { class: "art-body", html: d.html }) : h("p", { class: "muted" }, "Retningslinjerne er ikke skrevet endnu."),
    h("p", { class: "muted small" }, `Der bør højst være ${d.max_pr_art} publicerede fotos pr. art (gælder ikke sjældne arter).`));
}
