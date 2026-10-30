// Emneord: oversigt grupperet efter slags med bogstavfilter, og en side pr. emneord med dets artikler (kan kombineres under Søg).
import { api, h } from "../core.js";
import { BASE, nowrap } from "./common.js";
import { results, readState } from "./soeg.js";

const KINDS = [["emne", "Emner"], ["sted", "Steder"], ["publikation", "Publikationer"], ["aar", "År"], ["art", "Arter"], ["type", "Typer"]];

export async function page(parts) {
  const tags = await api("/api/artikler/emneord");
  if (parts[1] && /^\d+$/.test(parts[1])) {
    const t = tags.find(x => x.id === +parts[1]);
    const st = { ...readState({}), tag: [+parts[1]] };
    return h("div", {}, h("h2", {}, t ? t.navn : "Emneord"), h("p", { class: "muted" }, t ? `${nowrap(t.n)} artikler · ` : "", h("a", { href: `${BASE}/soeg?tag=${parts[1]}` }, "Kombinér med andre emneord eller tekst")),
      results(st).el);
  }
  let letter = "", filter = "";
  const body = h("div", {}), az = h("div", { class: "art-az" });
  const first = (t) => (t.navn.trim()[0] || "#").toUpperCase();
  const draw = () => {
    const shown = tags.filter(t => (!letter || first(t) === letter) && (!filter || t.navn.toLowerCase().includes(filter)));
    const letters = [...new Set(tags.map(first))].sort((a, b) => a.localeCompare(b, "da"));
    az.replaceChildren(h("a", { href: "javascript:void 0", class: letter ? "" : "on", onclick: () => { letter = ""; draw(); } }, "Alle"),
      ...letters.map(l => h("a", { href: "javascript:void 0", class: l === letter ? "on" : "", onclick: () => { letter = l; draw(); } }, l)));
    body.replaceChildren(...KINDS.map(([k, label]) => {
      const items = shown.filter(t => t.kind === k);
      return items.length ? h("section", { class: "art-group" }, h("h3", {}, `${label} (${items.length})`),
        h("div", { class: "art-cols" }, ...items.map(t => h("div", {}, h("a", { href: `${BASE}/emneord/${t.id}` }, t.navn), " ", h("small", {}, `(${nowrap(t.n)})`))))) : null;
    }), shown.length ? null : h("p", { class: "muted" }, "Ingen emneord matcher."));
  };
  draw();
  return h("div", {}, h("h2", {}, "Emneord"), h("input", { type: "search", placeholder: "Filtrér emneord …", style: { width: "100%", maxWidth: "360px" }, oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); } }), az, body);
}
