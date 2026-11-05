// Læring 2.0 – bygger: indholdsblokke i en lektion (spec §8.3).
import { api, h, toast } from "../../core.js";
import { photoPicker, soundPicker } from "./pickere.js";
import { speciesPicker, locationPicker } from "../pickers.js";
import { SILS, HUES } from "./konstanter.js";

const TYPER = [["tekst", "Tekst"], ["foto", "Foto"], ["fotoraekke", "Fotorække (sammenlign)"], ["lyd", "Lyd"], ["silhuet", "Silhuet"], ["artskort", "Artskort"],
  ["kort", "Sted"], ["artikel", "Artikel"], ["tip", "Tip"], ["advarsel", "Advarsel"], ["metode", "Metoden (fem trin)"]];

/** Kort tekst for en automatisk medie-reference (startindhold): netfugl-foto, XC-nr. eller art + type. */
export function refText(r) {
  if (!r) return "";
  return [r.nf ? `netfugl-foto ${r.nf}` : "", r.xc ? `XC${r.xc}` : "", r.latin || (r.artnr ? `art ${r.artnr}` : ""), r.alder || "", r.adfaerd || "", r.type || "", r.n ? `nr. ${r.n + 1}` : ""].filter(Boolean).join(", ");
}

/** «Brug automatisk igen»: fjerner et fast valgt medie, så blokkens reference bruges igen. */
function autoBtn(b, keys, prev, label, change) {
  if (!b.ref && !b.refs) return null;
  return h("button", { type: "button", class: "btn sm ghost", title: "Fjern det valgte og brug det automatisk valgte foto/den automatisk valgte lyd", onclick: () => { keys.forEach(k => delete b[k]); prev.textContent = label(); change(); } }, "Brug automatisk");
}

/** Blok-editor. ``blocks`` er en liste, der redigeres på stedet; ``onChange`` kaldes ved hver ændring. */
export function blocksEditor(blocks, onChange) {
  const root = h("div", { class: "lr-blocks" });
  const draw = () => {
    root.replaceChildren(...blocks.map((b, i) => block(b, i)),
      h("div", { class: "lr-actions" }, h("select", { "aria-label": "Tilføj blok", onchange: (e) => { if (!e.target.value) return; blocks.push({ type: e.target.value }); e.target.value = ""; change(); draw(); } },
        h("option", { value: "" }, "+ Tilføj indholdsblok …"), ...TYPER.map(([k, t]) => h("option", { value: k }, t)))));
  };
  const change = () => onChange?.();
  const mv = (i, d) => { const j = i + d; if (j < 0 || j >= blocks.length) return; [blocks[i], blocks[j]] = [blocks[j], blocks[i]]; change(); draw(); };
  function block(b, i) {
    const body = h("div", { class: "lr-bbody" });
    const set = (k, v) => { b[k] = v; change(); };
    const field = (label, el) => h("label", { class: "lr-f" }, h("span", {}, label), el);
    const text = (k, ph = "") => h("input", { value: b[k] || "", placeholder: ph, oninput: (e) => set(k, e.target.value) });
    const area = (k, ph = "") => h("textarea", { rows: 4, placeholder: ph, oninput: (e) => set(k, e.target.value) }, b[k] || "");
    switch (b.type) {
      case "tekst": case "tip": case "advarsel": body.append(b.type !== "tekst" ? field("Overskrift", text("titel")) : null, field("Tekst (markdown)", area("md"))); break;
      case "foto": {
        const prev = h("span", { class: "muted" }, b.photo_id ? `Foto ${b.photo_id}` : b.ref ? `Automatisk: ${refText(b.ref)}` : "Intet foto valgt");
        body.append(h("div", { class: "lr-actions" }, prev, h("button", { type: "button", class: "btn sm", onclick: () => photoPicker({ onPick: (p) => { set("photo_id", p.id); prev.textContent = `Foto ${p.id} · ${p.arter || ""}`; } }) }, b.ref ? "Skift foto" : "Vælg foto"),
          autoBtn(b, ["photo_id"], prev, () => `Automatisk: ${refText(b.ref)}`, change)), field("Billedtekst", text("tekst")));
        break;
      }
      case "fotoraekke": {
        const count = () => (b.photo_ids || []).length ? `${b.photo_ids.length} fotos` : (b.refs || []).length ? `Automatisk: ${b.refs.map(refText).join(" · ")}` : "0 fotos";
        const prev = h("span", { class: "muted" }, count());
        body.append(h("div", { class: "lr-actions" }, prev, h("button", { type: "button", class: "btn sm", onclick: () => photoPicker({ multi: true, onPick: (ps) => { set("photo_ids", ps.map(p => p.id).slice(0, 6)); prev.textContent = count(); } }) }, b.refs ? "Skift fotos" : "Vælg fotos"),
          autoBtn(b, ["photo_ids"], prev, count, change)),
          field("Etiketter (én pr. foto, adskilt af |)", h("input", { value: (b.labels || []).join(" | "), oninput: (e) => set("labels", e.target.value.split("|").map(x => x.trim())) })), field("Billedtekst", text("tekst")));
        break;
      }
      case "lyd": {
        const prev = h("span", { class: "muted" }, b.rec_id ? `Optagelse ${b.rec_id}` : b.ref ? `Automatisk: ${refText(b.ref)}` : "Ingen lyd valgt");
        body.append(h("div", { class: "lr-actions" }, prev, h("button", { type: "button", class: "btn sm", onclick: () => soundPicker({ onPick: (r) => { set("rec_id", r.id); prev.textContent = `${r.source.toUpperCase()} ${r.ext_id}`; } }) }, b.ref ? "Skift optagelse" : "Vælg optagelse"),
          autoBtn(b, ["rec_id"], prev, () => `Automatisk: ${refText(b.ref)}`, change)), field("Tekst", text("tekst")));
        break;
      }
      case "silhuet": body.append(field("Silhuet", h("select", { onchange: (e) => set("sil", e.target.value) }, ...SILS.map(s => h("option", { value: s, selected: s === b.sil }, s)))),
        field("Baggrund", h("select", { onchange: (e) => set("hue", e.target.value) }, ...HUES.map(s => h("option", { value: s, selected: s === b.hue }, s)))), field("Tekst", text("tekst"))); break;
      case "artskort": { const picked = new Set(b.artnr ? [b.artnr] : []), names = new Map(); const sp = speciesPicker(picked, names, () => { while (picked.size > 1) picked.delete([...picked][0]); set("artnr", [...picked][0] || null); sp.redraw(); }); body.append(sp.el, h("p", { class: "muted small" }, b.artnr ? `Art ${b.artnr}` : "")); break; }
      case "kort": body.append(locationPicker(b.loknr ? { loknr: b.loknr, navn: `Lokalitet ${b.loknr}` } : null, (v) => set("loknr", v.loknr)).el); break;
      case "artikel": body.append(field("Artikel-id (fra Artikler)", h("input", { type: "number", value: b.id || "", oninput: (e) => set("id", +e.target.value || null) }))); break;
      case "metode": body.append(field("Fremhæv trin", h("select", { onchange: (e) => set("trin", +e.target.value) }, ...["Forudsig", "Opdag", "Tolk", "Adskil", "Beslut"].map((t, k) => h("option", { value: k, selected: k === (b.trin || 0) }, `${k + 1}. ${t}`))))); break;
    }
    return h("div", { class: "lr-block" }, h("div", { class: "lr-bhead" }, h("b", {}, TYPER.find(t => t[0] === b.type)?.[1] || b.type), h("span", { class: "lr-meta" },
      h("button", { type: "button", class: "btn sm ghost", "aria-label": "Flyt op", onclick: () => mv(i, -1) }, "↑"), h("button", { type: "button", class: "btn sm ghost", "aria-label": "Flyt ned", onclick: () => mv(i, 1) }, "↓"),
      h("button", { type: "button", class: "btn sm ghost danger", "aria-label": "Fjern", onclick: () => { blocks.splice(i, 1); change(); draw(); } }, "×"))), body);
  }
  draw();
  return root;
}
