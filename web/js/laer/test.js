// Læring 2.0: færdighedstest – 20 spørgsmål på tværs af færdigheder; anbefaler bagefter, hvad der skal øves (docs/laering-spec.md §12.2).
import { api, h, errorBox } from "../core.js";
import { BASE, backLink, loadingBox } from "./common.js";
import { runPlayer } from "./player.js";
import { courseCard } from "./kort.js";

export async function render(view) {
  const root = h("div", {});
  view.append(backLink(BASE, "Forside"), h("div", { class: "lr-intro" }, h("h2", {}, "Færdighedstest"), h("p", { class: "muted" }, "Et udgangspunkt: 20 spørgsmål på tværs af færdighederne. Bagefter får du forslag til, hvor du skal starte.")), root);
  let stop = null;
  async function go() {
    root.replaceChildren(loadingBox());
    try {
      const t = await api("/api/learn/faerdighedstest");
      if (!t.questions.length) { root.replaceChildren(h("div", { class: "notice" }, "Der er endnu ingen publicerede aktiviteter at teste med.")); return; }
      stop?.();
      stop = runPlayer(root, { title: "Færdighedstest", questions: t.questions, again: go, doneExtra: (score, n, results) => advice(results) });
    } catch (e) { root.replaceChildren(errorBox(e)); }
  }
  function advice(results) {
    const by = {};
    for (const r of results) for (const s of (r.q.skills || [])) { by[s] ||= [0, 0]; by[s][1]++; if (r.ok) by[s][0]++; }
    const weak = Object.entries(by).filter(([, [a, b]]) => b >= 2).sort((x, y) => x[1][0] / x[1][1] - y[1][0] / y[1][1]).slice(0, 3).map(([s]) => s);
    const box = h("div", { class: "lr-advice" }, weak.length ? h("p", {}, "Øv især: ", ...weak.map(s => h("span", { class: "lr-chip soft", style: { marginRight: "4px" } }, s))) : null);
    api("/api/learn/katalog").then(k => {
      const rec = k.kurser.filter(c => weak.some(s => (c.skills || []).includes(s))).slice(0, 3);
      box.append(rec.length ? h("div", {}, h("p", {}, "Start med:"), h("div", { class: "lr-grid" }, ...rec.map(courseCard))) : h("p", {}, h("a", { href: `${BASE}/kurser` }, "Se alle kurser →")));
    }).catch(() => {});
    return box;
  }
  await go();
  return () => stop?.();
}
