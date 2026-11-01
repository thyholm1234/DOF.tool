// Alarmer → Notifikationer: modulernes valg samlet ét sted (Artikler og Fotos). Regler for fugle ligger under fanen Regler.
import { api, state, h, toast, errorBox } from "./core.js";

const check = (on, onchange, text, disabled = false) =>
  h("label", { class: "check" }, h("input", { type: "checkbox", checked: on, disabled, onchange: (e) => onchange(e.target.checked) }), h("span", {}, text));

/** Én boks pr. modul; data hentes fra modulets eget endpoint. */
async function box(title, url, draw) {
  const body = h("div", { class: "stack" });
  const card = h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, title), body);
  try {
    const p = await api(url);
    const save = async (patch) => { try { Object.assign(p, await api(url, { method: "PUT", body: patch })); toast("Gemt"); } catch (e) { toast(e.message, "err"); } };
    body.append(...draw(p, save));
  } catch (e) { body.append(errorBox(e)); }
  return card;
}

export async function notifications(view) {
  const u = state.user || {};
  const boxes = [];
  if (u.artikler?.features?.artikler && u.artikler.features.artikler_notifikationer) boxes.push(box("Artikler", "/api/artikler/notifikationer", (p, save) => [
    check(p.alle, (v) => save({ alle: v }), "Giv mig besked, når der kommer en ny artikel"),
    check(p.kommentarer, (v) => save({ kommentarer: v }), "Nye kommentarer på artikler, jeg har kommenteret (og svar på mine kommentarer)"),
    p.foelger?.length ? h("div", {}, h("b", {}, "Artikler du følger"),
      h("ul", {}, ...p.foelger.map(f => h("li", {}, h("a", { href: `#/artikler/${f.id}` }, f.title))))) : null,
    h("p", { class: "muted small" }, "Du kan følge eller holde op med at følge en enkelt artikel nederst på artiklen.")]));
  if (u.fotos?.features?.fotos) boxes.push(box("Fotos", "/api/fotos/notifikationer", (p, save) => [
    check(p.svar, (v) => save({ svar: v }), "Svar på mine kommentarer på fotos"),
    check(p.kommentarer, (v) => save({ kommentarer: v }), "Kommentarer på mine fotos (højst én i timen pr. foto)"),
    check(true, () => {}, "Mine fotos er godkendt eller afvist (kan ikke slås fra)", true)]));
  view.append(h("p", { class: "muted" }, "Beskeder kommer i din indbakke (klokken) og som push, hvis du har slået push til under Push & enheder; stille timer respekteres."),
    ...(boxes.length ? await Promise.all(boxes) : [h("p", {}, "Ingen moduler med egne notifikationer er slået til.")]));
}
