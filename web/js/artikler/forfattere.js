// Forfattere: alle med antal artikler; klik for at se deres artikler.
import { api, h } from "../core.js";
import { BASE, nowrap } from "./common.js";

export async function page() {
  const list = await api("/api/artikler/forfattere");
  let filter = "";
  const body = h("div", { class: "art-cols" });
  const draw = () => body.replaceChildren(...list.filter(a => !filter || a.navn.toLowerCase().includes(filter))
    .map(a => h("div", {}, h("a", { href: `${BASE}/soeg?forfatter=${encodeURIComponent(a.navn)}` }, a.navn), " ", h("small", {}, `(${nowrap(a.n)})`))));
  draw();
  return h("div", {}, h("h2", {}, "Forfattere"), h("input", { type: "search", placeholder: "Find forfatter …", style: { width: "100%", maxWidth: "360px", marginBottom: "12px" }, oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); } }), body);
}
