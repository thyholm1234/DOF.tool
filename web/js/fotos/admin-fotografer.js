// Fotos → Admin → Fotografer: alle fotografer med antal fotos og match-status (auto/manuel/tvetydig/ingen); kobl til bruger/obserkode, fjern kobling eller flet.
import { api, h, toast, modal } from "../core.js";
import { BASE, loadingBox, nowrap, pager } from "./common.js";

export async function page() {
  let match = "", q = "", side = 1;
  const out = h("div", { class: "stack" }), list = h("div", {});
  const search = h("input", { type: "search", placeholder: "Søg navn eller obserkode", style: { width: "240px" }, onchange: (e) => { q = e.target.value; side = 1; load(); } });
  async function load() {
    list.replaceChildren(loadingBox());
    const d = await api(`/api/admin/fotos/fotografer?match=${match}&q=${encodeURIComponent(q)}&side=${side}`);
    const filters = h("div", { class: "seg" }, ...[["", "Alle"], ["tvetydig", "Tvetydige"], ["ingen", "Uden match"], ["auto", "Automatisk"], ["manuel", "Manuel"]].map(([k, t]) =>
      h("button", { type: "button", class: k === match ? "on" : "", onclick: () => { match = k; side = 1; load(); } }, `${t}${k ? ` (${nowrap(d.antal[k] || 0)})` : ""}`)));
    list.replaceChildren(filters, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ...["Fotograf", "Fotos", "Match", "Bruger", "Obserkode", ""].map(x => h("th", {}, x)))),
      h("tbody", {}, ...d.rows.map(r => h("tr", {}, h("td", {}, h("a", { href: `${BASE}/fotograf/${r.id}` }, r.navn)), h("td", {}, nowrap(r.fotos)), h("td", {}, r.match),
        h("td", {}, r.bruger || ""), h("td", {}, r.obserkode || ""),
        h("td", {}, h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: () => edit(r) }, "Kobl …"), h("button", { class: "btn sm", type: "button", onclick: () => merge(r) }, "Flet …"))))))),
      pager(side, Math.ceil(d.total / 50), (p) => { side = p; load(); }));
  }
  function edit(r) {
    const find = h("input", { type: "search", placeholder: "Søg bruger …" }), res = h("div", { class: "stack" });
    const kode = h("input", { type: "text", placeholder: "Obserkode", maxlength: 16, value: r.obserkode || "" });
    const m = modal(`Kobl «${r.navn}»`, h("div", { class: "stack" }, find, res, h("div", { class: "row" }, kode, h("button", { class: "btn sm", type: "button", onclick: () => patch(r.id, { obserkode: kode.value }, m) }, "Gem obserkode")),
      r.user_id || r.obserkode ? h("button", { class: "btn sm danger", type: "button", onclick: () => patch(r.id, { unlink: true }, m) }, "Fjern kobling") : null));
    let t;
    find.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
      const rows = await api("/api/admin/fotos/brugere?q=" + encodeURIComponent(find.value)).catch(() => []);
      res.replaceChildren(...rows.map(u => h("button", { type: "button", class: "btn sm", onclick: () => patch(r.id, { user_id: u.id }, m) }, `${u.navn || u.obserkode} (${u.obserkode})`)));
    }, 250); };
  }
  async function patch(id, body, m) { try { await api(`/api/admin/fotos/fotografer/${id}`, { method: "PATCH", body }); m.close(); toast("Gemt"); load(); } catch (e) { toast(e.message, "err"); } }
  function merge(r) {
    const id = h("input", { type: "number", placeholder: "Fotograf-id at flette ind i" });
    const m = modal(`Flet «${r.navn}» (id ${r.id})`, h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      if (!confirm("Fotos og grupper flyttes til den valgte fotograf. Fortsæt?")) return;
      try { await api("/api/admin/fotos/fotografer/flet", { method: "POST", body: { from_id: r.id, into_id: +id.value } }); m.close(); toast("Flettet"); load(); } catch (x) { toast(x.message, "err"); }
    } }, h("p", { class: "muted" }, "Find id'et i kolonnen Fotograf (adressen #/fotos/fotograf/<id>). Brugerkoblingen følger med den, der lever videre."), id, h("div", { class: "row" }, h("button", { class: "btn primary" }, "Flet"))));
  }
  out.append(h("div", { class: "row" }, search, h("button", { class: "btn sm", type: "button", onclick: async () => {
    try { const r = await api("/api/admin/fotos/fotografer/match", { method: "POST" }); toast(`Match: ${r.auto} automatisk, ${r.tvetydig} tvetydige, ${r.ingen} uden`); load(); } catch (e) { toast(e.message, "err"); } } }, "Kør automatisk match igen")), list);
  await load();
  return out;
}
