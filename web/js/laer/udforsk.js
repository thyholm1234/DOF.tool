// Læring 2.0: Udforsk – øvelser, quizzer og kurser med filtre (port af Trækrutes train.js).
import { api, h, empty } from "../core.js";
import { BASE, KINDS } from "./common.js";
import { courseCard } from "./kort.js";

export async function render(view, _p, info) {
  const d = await api("/api/learn/katalog");
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const state = { type: q.get("type") || "alle", skill: q.get("skill") || "alle", q: q.get("q") || "" };
  const auto = info.data ? d.auto.map(a => ({ kind: "quiz", slug: `auto/${a.kind}`, title: a.title, card: "Genereret af DOFbasens data.", skills: ["Forbered"], auto: true, hue: "himmel", sil: "spurvefugl", category: "Automatisk" })) : [];
  const all = [...d.oevelser, ...auto, ...d.kurser];
  const list = h("div", { class: "lr-grid" });
  const draw = () => {
    const rows = all.filter(r => (state.type === "alle" || r.kind === state.type) && (state.skill === "alle" || (r.skills || []).includes(state.skill))
      && (!state.q || `${r.title} ${r.card || r.summary || ""} ${r.category || ""}`.toLowerCase().includes(state.q.toLowerCase())));
    list.replaceChildren(...(rows.length ? rows.map(r => r.auto ? h("a", { class: "lr-tile", href: `${BASE}/ovelse/${r.slug}` }, h("div", { class: "lr-kicker" }, "Quiz · automatisk"), h("h3", {}, r.title), h("p", { class: "muted" }, r.card)) : courseCard(r)) : [empty("Intet matcher filtrene")]));
    history.replaceState(null, "", `${BASE}/udforsk?type=${state.type}&skill=${encodeURIComponent(state.skill)}${state.q ? "&q=" + encodeURIComponent(state.q) : ""}`);
  };
  const types = [["alle", "Alle"], ...Object.entries(KINDS)];
  const typeBox = h("div", { class: "lr-chips", role: "group", "aria-label": "Type" });
  const skillBox = h("div", { class: "lr-chips", role: "group", "aria-label": "Færdighed" });
  const chips = () => {
    typeBox.replaceChildren(...types.map(([k, t]) => h("button", { class: `lr-chip${state.type === k ? " on" : ""}`, onclick: () => { state.type = k; chips(); draw(); } }, t)));
    skillBox.replaceChildren(...["alle", ...info.skills].map(k => h("button", { class: `lr-chip${state.skill === k ? " on" : ""}`, onclick: () => { state.skill = k; chips(); draw(); } }, k === "alle" ? "Alle færdigheder" : k)));
  };
  chips();
  view.append(h("div", { class: "lr-intro" }, h("h2", {}, "Find det, du vil øve"), h("p", { class: "muted" }, "Filtrér på type og færdighed.")),
    h("input", { type: "search", class: "lr-search", placeholder: "Søg …", value: state.q, "aria-label": "Søg", oninput: (e) => { state.q = e.target.value; draw(); } }), typeBox, skillBox, list);
  draw();
}
