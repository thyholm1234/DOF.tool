// Kort (tidligere Puls): fanerne Live (kommandokortet) og Dag (en valgfri dags observationer med filtre).
// Dag-fanen vises kun, når funktionen daymap er synlig for brugeren (Admin → Funktioner).
import { state, h, params, seg } from "../core.js";

export async function render(view) {
  const tabs = [["live", "Live"], ...(state.user?.daymap ? [["dag", "Dag"]] : [])];
  let tab = tabs.some(t => t[0] === params().tab) ? params().tab : "live";
  const body = h("div", {});
  let off = null;
  const draw = async (t) => {
    off?.();
    off = null;
    body.replaceChildren();
    const mod = t === "dag" ? await import("./kort-dag.js") : await import("./kort-live.js");
    off = await (t === "dag" ? mod.renderDay(body) : mod.renderLive(body));
  };
  view.append(h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Kort")), h("span", { class: "spacer" }),
    tabs.length > 1 ? seg(tabs, tab, (v) => { tab = v; history.replaceState(null, "", `#/kort?tab=${v}`); draw(v); }) : null), body);
  await draw(tab);
  return () => off?.();
}
