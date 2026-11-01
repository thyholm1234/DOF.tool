// Kort: Live-kortet er flyttet ind i Nu (#/?vis=kort) og Dag-kortet ind i Statistik (#/statistik?tab=dag).
// Adressen #/kort (og den gamle #/puls) sender videre. Uden Statistik-modulet vises Dag-kortet her (menupunktet Dagskort under Analyse).
import { state, h, params } from "../core.js";

export async function render(view) {
  const dag = params().tab === "dag" && state.user?.daymap;
  if (!dag) { location.replace("#/?vis=kort"); return; }
  if (state.user?.statistik) { location.replace(`#/statistik?${new URLSearchParams({ ...params(), tab: "dag" })}`); return; }
  const body = h("div", {});
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Dagskort"))), body);
  const { renderDay } = await import("./kort-dag.js");
  return await renderDay(body);
}
