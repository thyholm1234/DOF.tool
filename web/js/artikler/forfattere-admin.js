// Artikler → Admin → Forfattere: forfatterregisteret. Rettelser samles og gemmes med én knap; omdøbning til et eksisterende navn fletter de to;
// «Del op» deler en sammensat forfatter («A og B»); eksport/import som CSV (rettelser fra importen lægges i tabellen som ugemte ændringer).
import { api, h, toast } from "../core.js";
import { nowrap } from "./common.js";

export async function page() {
  let rows = await api("/api/admin/artikler/forfattere");
  const pending = new Map();   // id → {navn?, skjult?, bio_md?}
  const out = h("div", { class: "stack" }), table = h("div", {}), bar = h("div", { class: "row" });
  let filter = "", onlyCombined = false;
  const mark = (id, patch) => { pending.set(id, { ...(pending.get(id) || {}), ...patch }); drawBar(); };
  const looksCombined = (n) => /\s+og\s+|,|&|\//i.test(n);
  function drawBar() {
    const n = pending.size;
    save.textContent = n ? `Gem ${n} ændring${n > 1 ? "er" : ""}` : "Gem";
    save.disabled = !n;
    discard.hidden = !n;
  }
  const save = h("button", { class: "btn primary", type: "button", onclick: async () => {
    try {
      const r = await api("/api/admin/artikler/forfattere", { method: "PUT", body: { changes: [...pending].map(([id, c]) => ({ id, ...c })) } });
      toast(`Gemt${r.flettet.length ? ` – ${r.flettet.length} flettet` : ""}`);
      pending.clear(); rows = await api("/api/admin/artikler/forfattere"); draw(); drawBar();
    } catch (e) { toast(e.message, "err"); }
  } }, "Gem");
  const discard = h("button", { class: "btn ghost", type: "button", hidden: true, onclick: () => { pending.clear(); draw(); drawBar(); } }, "Fortryd ugemte");
  function draw() {
    const f = filter.toLowerCase();
    const shown = rows.filter(r => (!f || r.navn.toLowerCase().includes(f)) && (!onlyCombined || looksCombined(r.navn)));
    table.replaceChildren(h("table", { class: "art-tbl" }, h("thead", {}, h("tr", {}, ...["Navn", "Artikler", "Skjult", "Netfugl-id", "Bruger", "Bio", ""].map(x => h("th", {}, x)))),
      h("tbody", {}, ...shown.slice(0, 600).map(r => {
        const p = pending.get(r.id) || {};
        return h("tr", { class: pending.has(r.id) ? "dirty" : "" },
          h("td", {}, h("input", { type: "text", value: p.navn ?? r.navn, maxlength: 160, style: { width: "100%" }, oninput: (e) => mark(r.id, { navn: e.target.value }) }),
            h("small", { class: "muted" }, (r.eksempler || []).join(" · "))),
          h("td", { class: "num" }, nowrap(r.n)),
          h("td", {}, h("input", { type: "checkbox", checked: p.skjult ?? r.skjult, title: "Skjult forfatter vises ikke i forfatteroversigten", onchange: (e) => mark(r.id, { skjult: e.target.checked }) })),
          h("td", {}, r.netfugl_id || ""), h("td", {}, r.bruger || ""),
          h("td", {}, h("input", { type: "text", value: p.bio_md ?? r.bio_md ?? "", maxlength: 5000, style: { width: "100%" }, oninput: (e) => mark(r.id, { bio_md: e.target.value }) })),
          h("td", { class: "nowrap" },
            looksCombined(r.navn) ? h("button", { class: "btn sm", type: "button", onclick: () => split(r) }, "Del op …") : null, " ",
            h("button", { class: "btn sm", type: "button", onclick: () => merge(r) }, "Flet …")));
      }))), shown.length > 600 ? h("p", { class: "muted" }, `Viser de første 600 af ${shown.length} – brug søgningen.`) : null);
  }
  async function split(r) {
    const guess = r.navn.split(/\s+og\s+|\s*,\s*|\s*&\s*|\s*\/\s*/i).map(x => x.trim()).filter(Boolean);
    const txt = prompt(`Del «${r.navn}» op i flere forfattere (ét navn pr. linje). Artiklerne får alle navnene i samme rækkefølge.`, guess.join("\n"));
    if (txt === null) return;
    try { await api(`/api/admin/artikler/forfattere/${r.id}/del`, { method: "POST", body: { navne: txt.split("\n").map(x => x.trim()).filter(Boolean) } }); toast("Delt"); rows = await api("/api/admin/artikler/forfattere"); draw(); }
    catch (e) { toast(e.message, "err"); }
  }
  async function merge(r) {
    const into = prompt(`Flet «${r.navn}» (id ${r.id}) ind i hvilken forfatter? Skriv id'et (se eksporten eller søg).`, "");
    if (!into) return;
    try { const x = await api(`/api/admin/artikler/forfattere/${r.id}/flet`, { method: "POST", body: { into: +into } }); toast(`${x.flyttet} artikler flyttet`); rows = await api("/api/admin/artikler/forfattere"); draw(); }
    catch (e) { toast(e.message, "err"); }
  }
  const file = h("input", { type: "file", accept: ".csv,text/csv", hidden: true, onchange: async () => {
    const fd = new FormData(); fd.append("file", file.files[0]);
    try {
      const r = await api("/api/admin/artikler/forfattere/import", { method: "POST", form: fd });
      for (const c of r.changes) { const { id, ...patch } = c; mark(id, patch); }
      draw(); toast(`${r.changes.length} rettelser lagt ind i tabellen – gennemse og tryk Gem${r.ukendte.length ? ` (${r.ukendte.length} ukendte rækker ignoreret)` : ""}`);
    } catch (e) { toast(e.message, "err"); }
    file.value = "";
  } });
  bar.append(h("input", { type: "search", placeholder: "Find forfatter …", style: { width: "240px" }, oninput: (e) => { filter = e.target.value; draw(); } }),
    h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => { onlyCombined = e.target.checked; draw(); } }), h("span", {}, "Kun sammensatte navne")),
    save, discard, h("a", { class: "btn sm", href: "/api/admin/artikler/forfattere/eksport", download: "forfattere.csv" }, "Eksport (CSV)"), file,
    h("button", { class: "btn sm", type: "button", onclick: () => file.click() }, "Import (CSV)"));
  out.append(h("p", { class: "muted" }, `${nowrap(rows.length)} forfattere. Sammensatte navne som «Lars Witting og Sakari Kauppinen» deles ikke automatisk – brug «Del op».`), bar, table);
  draw(); drawBar();
  return out;
}
