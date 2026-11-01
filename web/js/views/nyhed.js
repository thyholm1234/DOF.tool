// En nyhed fra portalen i fuld længde (#/nyheder/<id>) – markdown renderet og renset af serveren. Nyheder er en artikeltype (kind = nyhed):
// med Artikler-modulet synligt vises de også dér, og her linkes til de øvrige nyheder.
import { api, h, fmtDate, requireLogin, errorBox, state } from "../core.js";

export async function render(view, [id]) {
  if (requireLogin(view)) return;
  let n;
  try { n = await api(`/api/news/${id}`); } catch (err) { view.replaceChildren(h("p", {}, h("a", { href: "#/" }, "← Til forsiden")), err.status === 404 ? h("div", { class: "notice" }, "Nyheden findes ikke.") : errorBox(err)); return; }
  const { loadCss } = await import("../artikler/common.js");
  loadCss("artikler");
  document.title = `${n.title} – DOF.tool`;
  view.replaceChildren(h("div", { class: "art" }, h("article", { class: "art-page" },
    h("p", { class: "row" }, h("a", { href: "#/" }, "← Til forsiden"), h("span", { class: "spacer" }),
      state.user?.artikler?.features?.artikler ? h("a", { href: "#/artikler/soeg?kind=nyhed" }, "Alle nyheder i Artikler →") : null),
    h("h1", {}, n.title), n.lead ? h("p", { class: "lead" }, n.lead) : null,
    h("div", { class: "art-byline" }, h("span", {}, fmtDate(n.created_at)), n.author ? h("span", {}, "Af " + n.author) : null),
    n.cover ? h("figure", { class: "art-cover" }, h("img", { src: n.cover.medium, alt: "", style: { width: "100%", height: "auto" } })) : null,
    h("div", { class: "art-body", html: n.html }))));
  return () => { document.title = "DOF.tool"; };
}
