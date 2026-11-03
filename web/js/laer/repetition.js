// Læring 2.0: dagens repetition – forfaldne spørgsmål og arter (docs/laering-spec.md §6.3).
import { api, h, empty } from "../core.js";
import { BASE } from "./common.js";
import { runPlayer } from "./player.js";

export async function render(view) {
  const d = await api("/api/learn/repetition");
  const root = h("div", {});
  const c = d.antal;
  view.append(h("div", { class: "lr-intro" }, h("h2", {}, "Dagens repetition"), h("p", { class: "muted" },
    `Forfaldent i dag: ${c.spoergsmaal} spørgsmål, ${c.foto} arter på foto, ${c.lyd} arter på lyd. Forkerte svar kommer igen efter få minutter, rigtige efter længere og længere tid.`)), root);
  if (!d.questions.length) { root.append(empty("Ingen repetition i dag", "Kom tilbage senere – eller træn nogle arter, så der er noget at gentage."), h("p", {}, h("a", { class: "btn", href: `${BASE}/traen` }, "Træn arter"))); return; }
  return runPlayer(root, { title: "Repetition", questions: d.questions, again: () => { view.replaceChildren(); render(view); } });
}
