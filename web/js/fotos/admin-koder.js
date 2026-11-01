// Fotos → Admin → Koder: redigér adfærd, køn, alder og dragt (tekst, sortering, DOFbasen-kode), tilføj nye og flet to koder.
import { api, h, toast } from "../core.js";

const KINDS = [["adfaerd", "Adfærd"], ["koen", "Køn"], ["alder", "Alder"], ["dragt", "Dragt"]];

export async function page() {
  let items = await api("/api/admin/fotos/koder"), kind = "adfaerd";
  const out = h("div", { class: "stack" }), body = h("div", { class: "stack" });
  function draw() {
    const rows = items.filter(i => i.kind === kind);
    body.replaceChildren(h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ...["Kode", "Tekst", "Sortering", "DOFbasen-kode", "Brugt", ""].map(x => h("th", {}, x)))),
      h("tbody", {}, ...rows.map(r => h("tr", {}, h("td", {}, r.code || h("i", { class: "muted" }, "ny")),
        h("td", {}, h("input", { type: "text", value: r.text, maxlength: 120, oninput: (e) => { r.text = e.target.value; r._dirty = true; } })),
        h("td", {}, h("input", { type: "number", value: r.sort, style: { width: "80px" }, oninput: (e) => { r.sort = +e.target.value; r._dirty = true; } })),
        h("td", {}, h("input", { type: "text", value: r.dofbasen_code || "", maxlength: 16, style: { width: "100px" }, oninput: (e) => { r.dofbasen_code = e.target.value; r._dirty = true; } })),
        h("td", {}, String(r.brugt)),
        h("td", {}, r.code ? h("button", { class: "btn sm", type: "button", onclick: () => merge(r) }, "Flet ind i …") : null))))),
      h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: () => { items.push({ kind, code: "", text: "", sort: 900, dofbasen_code: "", brugt: 0, _dirty: true }); draw(); } }, "+ Ny kode"),
        h("button", { class: "btn primary", type: "button", onclick: save }, "Gem ændringer")));
  }
  async function save() {
    const changed = items.filter(i => i._dirty && i.text.trim());
    try { await api("/api/admin/fotos/koder", { method: "PUT", body: { items: changed.map(({ kind, code, text, sort, dofbasen_code }) => ({ kind, code, text, sort, dofbasen_code: dofbasen_code || null })) } }); toast("Gemt"); items = await api("/api/admin/fotos/koder"); draw(); }
    catch (e) { toast(e.message, "err"); }
  }
  async function merge(r) {
    const others = items.filter(i => i.kind === kind && i.code && i.code !== r.code);
    const to = prompt(`Flet «${r.text}» (${r.brugt} fugle) ind i hvilken kode?\n` + others.map(o => `${o.code} = ${o.text}`).join("\n"), others[0]?.code || "");
    if (!to) return;
    try { const x = await api("/api/admin/fotos/koder/flet", { method: "POST", body: { kind, from_code: r.code, into_code: to.trim() } }); toast(`${x.fugle} fugle flyttet`); items = await api("/api/admin/fotos/koder"); draw(); } catch (e) { toast(e.message, "err"); }
  }
  out.append(h("p", { class: "muted" }, "Ukendte tekster fra arkivet oprettes ved import med sortering 999. DOFbasen-koden bruges, når et foto knyttes til en observation."),
    h("div", { class: "seg" }, ...KINDS.map(([k, t]) => h("button", { type: "button", class: k === kind ? "on" : "", onclick: () => { kind = k; draw(); } }, t))), body);
  draw();
  return out;
}
