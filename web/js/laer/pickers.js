// Læring 2.0: små vælgere til arter, lokaliteter og afdelinger (bruges af Træn, Forbered en tur og bygger).
import { api, h, loadRef, state } from "../core.js";

/** Artsvælger: søg og læg på listen. ``picked`` er et Set af artnr; ``names`` et Map artnr → navn. */
export function speciesPicker(picked, names, onChange) {
  const input = h("input", { type: "search", placeholder: "Søg art (navn eller latin)", "aria-label": "Søg art" });
  const sugg = h("div", { class: "lr-suggest" });
  const chips = h("div", { class: "lr-chips" });
  const draw = () => chips.replaceChildren(...[...picked].map(a => h("span", { class: "lr-chip soft" }, names.get(a) || a, " ",
    h("button", { type: "button", class: "lr-x", "aria-label": "Fjern", onclick: () => { picked.delete(a); draw(); onChange?.(); } }, "×"))));
  let t = null;
  input.addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      if (input.value.trim().length < 2) { sugg.replaceChildren(); return; }
      const rows = await api(`/api/learn/traen/arter?q=${encodeURIComponent(input.value)}`).catch(() => []);
      sugg.replaceChildren(...rows.map(s => h("button", { type: "button", onclick: () => { picked.add(s.artnr); names.set(s.artnr, s.navn); input.value = ""; sugg.replaceChildren(); draw(); onChange?.(); } },
        s.navn, h("small", { class: "muted" }, ` · ${s.latin || ""}`))));
    }, 200);
  });
  draw();
  return { el: h("div", { class: "lr-picker" }, input, sugg, chips), redraw: draw };
}

/** Lokalitetsvælger: søg og vælg én. Returnerer {el, value()}. */
export function locationPicker(initial, onPick) {
  let value = initial || null;
  const label = h("span", { class: "lr-chip soft", hidden: !value }, value?.navn || "");
  const input = h("input", { type: "search", placeholder: "Søg lokalitet", "aria-label": "Søg lokalitet" });
  const sugg = h("div", { class: "lr-suggest" });
  let t = null;
  input.addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      if (input.value.trim().length < 2) { sugg.replaceChildren(); return; }
      const rows = await api(`/api/locations?q=${encodeURIComponent(input.value)}`).catch(() => []);
      sugg.replaceChildren(...rows.slice(0, 10).map(l => h("button", { type: "button", onclick: () => {
        value = { loknr: l.loknr, navn: l.navn }; label.textContent = l.navn; label.hidden = false; input.value = ""; sugg.replaceChildren(); onPick?.(value);
      } }, l.navn)));
    }, 200);
  });
  return { el: h("div", { class: "lr-picker" }, label, input, sugg), value: () => value };
}

export async function afdelingSelect(selected, onChange) {
  await loadRef();
  const sel = h("select", { "aria-label": "Lokalafdeling", onchange: (e) => onChange?.(+e.target.value || null) }, h("option", { value: "" }, "Vælg afdeling …"),
    ...state.afdelinger.map(a => h("option", { value: a.id, selected: a.id === selected }, a.navn || a.name)));
  return sel;
}
