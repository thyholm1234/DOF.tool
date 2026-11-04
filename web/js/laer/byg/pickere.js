// Læring 2.0 – bygger: vælgere til fotos (fotobiblioteket) og lyd (xeno-canto/Macaulay Library).
import { api, h, modal, toast } from "../../core.js";
import { speciesPicker } from "../pickers.js";

/** Fotovælger: søg i fotobiblioteket; alle publicerede fotos kan vælges. */
export function photoPicker({ multi = false, onPick }) {
  const picked = [];
  const grid = h("div", { class: "lr-pgrid" });
  const q = h("input", { type: "search", placeholder: "Søg art, fotograf eller lokalitet", "aria-label": "Søg fotos" });
  let offset = 0;
  const load = async (more) => {
    if (!more) { offset = 0; grid.replaceChildren(); }
    const rows = await api(`/api/learn/byg/fotos?q=${encodeURIComponent(q.value)}&offset=${offset}`).catch(e => { toast(e.message, "err"); return []; });
    offset += rows.length;
    grid.append(...rows.map(p => {
      const b = h("button", { type: "button", class: "lr-pcard", title: `${p.arter || "Foto"} · ${p.fotograf || ""}`, onclick: () => {
        if (!multi) { m.close(); onPick(p); return; }
        const i = picked.findIndex(x => x.id === p.id);
        if (i >= 0) { picked.splice(i, 1); b.classList.remove("on"); } else { picked.push(p); b.classList.add("on"); }
      } }, h("img", { src: p.thumb, alt: "", loading: "lazy" }), h("small", {}, p.arter || "Foto"));
      return b;
    }));
    if (!rows.length && !more) grid.append(h("p", { class: "muted" }, "Ingen fotos fundet."));
  };
  let t = null;
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => load(false), 250); });
  const m = modal("Vælg foto fra fotobiblioteket", h("div", { class: "stack" }, q, grid, h("div", { class: "lr-actions" }, h("button", { class: "btn", onclick: () => load(true) }, "Vis flere"),
    multi ? h("button", { class: "btn primary", onclick: () => { m.close(); onPick(picked.slice()); } }, "Brug valgte") : null)), { wide: true });
  load(false);
}

/** Lydvælger: vælg art, se optagelser (og hent fra xeno-canto), vælg én. */
export function soundPicker({ onPick }) {
  const names = new Map(), sel = new Set();
  const list = h("div", { class: "lr-list" });
  let art = null;
  const load = async () => {
    if (!art) return;
    const rows = await api(`/api/learn/byg/lyde?artnr=${art}`).catch(() => []);
    list.replaceChildren(h("div", { class: "lr-actions" }, h("button", { class: "btn sm", onclick: async () => {
      try { await api(`/api/learn/byg/lyde/hent?artnr=${art}`, { method: "POST" }); toast("Hentet fra xeno-canto"); load(); } catch (e) { toast(e.message, "err"); } } }, "Hent fra xeno-canto")),
      ...(rows.length ? rows.map(r => h("div", { class: "lr-listrow" }, h("span", {}, `${r.source.toUpperCase()} ${r.ext_id} · ${r.rtype || ""} · ${r.recordist || ""} · ${r.license || ""}`.replace(/ · +·/g, " ·")),
        h("span", { class: "lr-meta" }, h("a", { href: r.page_url, target: "_blank", rel: "noopener" }, "kilde"), h("button", { class: "btn sm primary", onclick: () => { m.close(); onPick(r); } }, "Vælg")))) : [h("p", { class: "muted" }, "Ingen optagelser endnu.")]));
  };
  const sp = speciesPicker(sel, names, () => { art = [...sel].pop() || null; if (sel.size > 1) { [...sel].slice(0, -1).forEach(x => sel.delete(x)); sp.redraw(); } load(); });
  const m = modal("Vælg lydoptagelse", h("div", { class: "stack" }, h("p", { class: "muted" }, "Vælg først en art."), sp.el, list), { wide: true });
}

/** Upload af egen fil (billede eller lyd); kræver kreditering. */
export async function uploadMedia(file) {
  const fd = new FormData();
  fd.append("file", file);
  return api("/api/learn/byg/medie", { method: "POST", form: fd });
}
