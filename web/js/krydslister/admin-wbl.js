// Admin → World Bird List: indlæs AviList-CSV med diff før anvendelse, ret danske navne og kobl arter uden artnr til DOFbasen.
import { api, h, toast } from "../core.js";

export async function page() {
  const out = h("div", { class: "stack" });
  const file = h("input", { type: "file", accept: ".csv,text/csv" }), ver = h("input", { type: "text", placeholder: "Version, fx AviList 2025", maxlength: 40 });
  const diffBox = h("div", {}), unl = h("div", {});
  const list = (items, f) => h("ul", { class: "small" }, ...items.slice(0, 30).map(f), items.length > 30 ? h("li", { class: "muted" }, `… og ${items.length - 30} flere`) : null);
  async function show(imp) {
    const d = imp.diff;
    const deact = h("input", { type: "checkbox" });
    diffBox.replaceChildren(h("div", { class: "card stack" }, h("h4", {}, `Diff for ${imp.version}`),
      h("div", { class: "kl-statgrid" }, ...[["I filen", d.antal_fil], ["Nye", d.antal_nye], ["Omdøbte (split/lump)", d.antal_omdoebt], ["Ændrede", d.antal_aendret], ["Forsvundne", d.antal_forsvundet]].map(([t, n]) => h("div", {}, h("b", {}, n), h("span", {}, t)))),
      d.nye.length ? h("details", {}, h("summary", {}, "Nye arter"), list(d.nye, x => h("li", {}, `${x.latin} (${x.engelsk || ""})`))) : null,
      d.omdoebt.length ? h("details", { open: true }, h("summary", {}, "Omdøbte"), list(d.omdoebt, x => h("li", {}, `${x.fra} → ${x.til}`))) : null,
      d.aendret.length ? h("details", {}, h("summary", {}, "Ændrede"), list(d.aendret, x => h("li", {}, x.latin))) : null,
      d.forsvundet.length ? h("details", {}, h("summary", {}, "Forsvundne (findes ikke i filen)"), list(d.forsvundet, x => h("li", {}, x.latin))) : null,
      h("label", { class: "check" }, deact, h("span", {}, "Deaktivér arter, der ikke findes i filen")),
      h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onclick: async () => {
        if (!confirm("Anvend AviList-importen? Det sker i én transaktion.")) return;
        try { const r = await api(`/api/admin/krydslister/wbl/import/${imp.id}/anvend?deaktiver_forsvundne=${deact.checked}`, { method: "POST" }); toast(`Anvendt: ${r.nye} nye, ${r.omdoebt} omdøbt, ${r.opdateret} opdateret`); diffBox.replaceChildren(); loadUnlinked(); }
        catch (e) { toast(e.message, "err"); } } }, "Anvend"))));
  }
  async function loadUnlinked() {
    const rows = await api("/api/admin/krydslister/wbl/uden-artnr");
    unl.replaceChildren(h("h3", {}, `Arter uden kobling til DOFbasen (${rows.length})`), h("p", { class: "muted small" }, "Skriv DOFbasens artnr for at koble (så de fund og kryds vises)."),
      ...rows.slice(0, 100).map(r => { const i = h("input", { type: "number", placeholder: "artnr", style: { width: "100px" } });
        return h("div", { class: "row" }, h("span", { style: { width: "260px" } }, r.dansk || r.engelsk || "", " ", h("i", {}, r.latin)), i,
          h("button", { class: "btn sm", type: "button", onclick: async () => { try { await api(`/api/admin/krydslister/wbl/${r.id}`, { method: "PUT", body: { artnr: +i.value } }); toast("Koblet"); loadUnlinked(); } catch (e) { toast(e.message, "err"); } } }, "Kobl")); }));
  }
  out.append(h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Indlæs AviList"),
    h("p", { class: "muted" }, "Upload AviList-CSV'en (åben licens, CC BY 4.0 – se docs/funktioner.md). Intet skrives, før du har set diffen og trykker Anvend. Kolonner: Scientific name, English name, Order, Family, AviList ID, Taxon rank."),
    h("label", { class: "field" }, h("span", {}, "CSV-fil"), file), ver, h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onclick: async () => {
      if (!file.files[0]) { toast("Vælg en fil", "err"); return; }
      const fd = new FormData(); fd.append("file", file.files[0]); fd.append("version", ver.value);
      try { const r = await api("/api/admin/krydslister/wbl/import", { method: "POST", form: fd }); show(r); } catch (e) { toast(e.message, "err"); } } }, "Vis diff"),
      h("button", { class: "btn", type: "button", onclick: async () => { await api("/api/admin/krydslister/wbl/netfugl-ids", { method: "POST" }); toast("Kobling til Netfugls artsnumre bestilt hos workeren"); } }, "Kobl Netfugl-artsnumre (fotos)"))),
    diffBox, unl);
  await loadUnlinked();
  return out;
}
