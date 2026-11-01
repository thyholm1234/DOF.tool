// Admin → Artslister: opret liste, tilføj art (søg i World Bird List), kategorier A–F, sjældenhed, fjern; CSV-eksport og -import med diff.
import { api, h, toast } from "../core.js";
import { loadingBox, taxonPicker, table } from "./common.js";

const SJ = [["almindelig", "Almindelig"], ["relativ_almindelig", "Relativ almindelig"], ["ualmindelig", "Ualmindelig"], ["sjaelden", "Sjælden"], ["su", "SU"]];

export async function page(parts, q) {
  const lists = await api("/api/krydslister/artslister");
  let cur = lists.find(l => l.key === q.liste) || lists[0];
  const body = h("div", {}, loadingBox()), top = h("div", { class: "row" });
  async function load() {
    top.replaceChildren(h("select", { onchange: (e) => { cur = lists.find(l => l.key === e.target.value); load(); } }, ...lists.map(l => h("option", { value: l.key, selected: l.key === cur.key }, `${l.navn} (${l.antal})`))),
      h("button", { class: "btn sm", type: "button", onclick: newList }, "+ Ny artsliste"),
      h("a", { class: "btn sm", href: `/api/admin/krydslister/artslister/${cur.id}/csv`, download: `${cur.key}.csv` }, "CSV-eksport"), importBtn());
    const d = await api(`/api/krydslister/artslister/${cur.key}`);
    let picked = null;
    const add = h("div", { class: "row" }, taxonPicker({ placeholder: "Tilføj art (søg i World Bird List) …", onPick: (r) => { picked = r; } }), h("button", { class: "btn sm primary", type: "button", onclick: async () => {
      if (!picked) { toast("Vælg en art", "err"); return; }
      try { await api(`/api/admin/krydslister/artslister/${cur.id}/arter/${picked.id}`, { method: "PUT", body: { kategorier: [], sjaeldenhed: "almindelig" } }); toast("Tilføjet sidst på listen"); load(); } catch (e) { toast(e.message, "err"); } } }, "Tilføj"));
    const rows = d.rows.map(r => {
      const cats = h("input", { type: "text", value: r.kategorier.join(","), maxlength: 20, style: { width: "80px" } });
      const sj = h("select", {}, ...SJ.map(([v, t]) => h("option", { value: v, selected: v === r.sjaeldenhed }, t)));
      const save = async () => { try { await api(`/api/admin/krydslister/artslister/${cur.id}/arter/${r.id}`, { method: "PUT", body: { kategorier: cats.value.toUpperCase().split(/[,\s]+/).filter(Boolean), sjaeldenhed: sj.value } }); toast("Gemt"); } catch (e) { toast(e.message, "err"); } };
      return { text: `${r.navn} ${r.latin}`, cells: [r.navn, h("i", {}, r.latin), cats, sj, h("button", { class: "btn sm", type: "button", onclick: save }, "Gem"),
        h("button", { class: "btn sm danger", type: "button", onclick: async () => { if (confirm(`Fjern ${r.navn} fra listen?`)) { await api(`/api/admin/krydslister/artslister/${cur.id}/arter/${r.id}`, { method: "DELETE" }); load(); } } }, "Fjern")] };
    });
    body.replaceChildren(add, table(["Navn", "Latinsk navn", "Kategorier", "Sjældenhed", "", ""], rows, { name: cur.key }));
  }
  function importBtn() {
    const file = h("input", { type: "file", accept: ".csv,text/csv", hidden: true, onchange: async () => {
      const send = async (apply) => { const fd = new FormData(); fd.append("file", file.files[0]); fd.append("anvend", apply ? "true" : "false"); return api(`/api/admin/krydslister/artslister/${cur.id}/csv`, { method: "POST", form: fd }); };
      try {
        const d = await send(false);
        const txt = `Nye: ${d.nye.length} · Ændrede: ${d.aendrede.length} · Uændrede: ${d.uaendret} · Ukendte arter: ${d.ukendte.length} · Mangler i filen (røres ikke): ${d.mangler_i_filen.length}\n${d.fejl.join("\n")}\n\nAnvend ændringerne?`;
        if ((d.nye.length || d.aendrede.length) && confirm(txt)) { await send(true); toast("Importeret"); load(); } else if (!d.nye.length && !d.aendrede.length) alert(txt.replace("\n\nAnvend ændringerne?", "\n\nIntet at ændre."));
      } catch (e) { toast(e.message, "err"); }
      file.value = "";
    } });
    return h("span", {}, file, h("button", { class: "btn sm", type: "button", onclick: () => file.click() }, "CSV-import (med diff)"));
  }
  async function newList() {
    const key = prompt("Nøgle til den nye artsliste (små bogstaver):"); if (!key) return;
    const navn = prompt("Navn:"); if (!navn) return;
    try { await api("/api/admin/krydslister/artslister", { method: "POST", body: { key, navn } }); location.reload(); } catch (e) { toast(e.message, "err"); }
  }
  await load();
  return h("div", { class: "stack" }, top, body);
}
