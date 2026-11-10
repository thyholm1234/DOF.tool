// Mine kryds: automatiske (DOFbasen) og manuelle kryds pr. liste; tilføj (manuel indtastning), slet og skjul/vis automatiske kryds.
import { api, h, toast, fmtN, icon } from "../core.js";
import { table, title, loadingBox, notice, taxonPicker, on } from "./common.js";

export async function page(parts, q) {
  const meta = await api("/api/krydslister/lister");
  const mineLists = meta.lister.filter(l => !l.param && (l.manual || l.auto));
  let liste = mineLists.some(l => l.key === q.liste) ? q.liste : "dk";
  const out = h("div", { class: "stack" }), tabs = h("div", { class: "seg wrap lister-kl-tabs" });
  async function load() {
    tabs.replaceChildren(...mineLists.map(l => h("button", { type: "button", class: l.key === liste ? "on" : "", onclick: () => { liste = l.key; history.replaceState(history.state, "", `#/lister/mine?liste=${encodeURIComponent(l.key)}`); load(); } }, l.name)));
    out.replaceChildren(loadingBox());
    const d = await api(`/api/krydslister/mine/${liste}`);
    const f = { taxon: null, dato: "", lokalitet: "", land: d.ref === "dk" ? "Danmark" : "", note: "" };
    const inp = (k, a = {}) => h("input", { type: a.type || "text", value: f[k], oninput: (e) => { f[k] = e.target.value; }, ...a });
    const add = d.kan_krydse && on("krydslister_manuel") ? h("form", { class: "card stack", onsubmit: async (e) => {
      e.preventDefault();
      if (!f.taxon) { toast("Vælg en art fra listen", "err"); return; }
      try { const r = await api("/api/krydslister/mine", { method: "POST", body: { liste, taxon_id: f.taxon.id, dato: f.dato || null, lokalitet: f.lokalitet, land: f.land, note: f.note } }); toast(r.status === "afventer" ? "Krydset afventer godkendelse" : "Krydset er gemt"); load(); }
      catch (x) { toast(x.message, "err"); }
    } }, h("b", {}, "Tilføj et kryds"), h("div", { class: "kl-form" },
      h("label", { class: "field" }, h("span", {}, "Art"), taxonPicker({ liste: d.ref, onPick: (r) => { f.taxon = r; } })), h("label", { class: "field" }, h("span", {}, "Dato"), inp("dato", { type: "date" })),
      h("label", { class: "field" }, h("span", {}, "Lokalitet"), inp("lokalitet")), h("label", { class: "field" }, h("span", {}, "Land"), inp("land")), h("label", { class: "field" }, h("span", {}, "Note"), inp("note")),
      h("button", { class: "btn primary", type: "submit" }, "Tilføj")),
      h("p", { class: "small muted" }, d.ref === "dk" ? "Sjældne arter (*) kræver dato og lokalitet." : "Sjældne arter (*) kræver land, lokalitet og dato.", d.godkendelse ? " Krydser på denne liste skal godkendes af en moderator." : "",
        " Døde fugle tæller ikke på personlige lister.")) : null;
    out.replaceChildren(
      h("div", { class: "card lister-hero" }, h("div", { class: "lister-hero-head" }, h("div", {}, h("div", { class: "muted" }, d.name), h("div", { class: "lister-big" }, fmtN(d.rows.length), h("span", {}, " arter"))),
        h("div", { class: "row" }, h("a", { class: "btn sm", href: `#/lister/kryds/${encodeURIComponent(liste)}` }, "Se ranglisten")))),
      liste === "aedel" ? notice(h("span", {}, "Ædelarter kan også markeres direkte med ", h("span", { class: "lister-star on", html: icon("star") }), " på din ",
        h("a", { href: "#/lister/liste" }, "årsliste"), " (tæller på årets ædelårsliste og på ædel-livslisten) eller ", h("a", { href: "#/lister/liste?year=all" }, "livsliste"), ". En art kan være ædel i flere år."), "info") : null,
      d.participant ? null : notice("Du er ikke med på krydslisterne, fordi du har valgt ikke at blive vist på ranglister. Slå det til under Profil → Lister og privatliv.", "info"),
      d.kan_skjule ? notice("Dine DK-kryds kommer automatisk fra dine observationer i DOFbasen (første fund pr. art). Tilføj arter fra før DOFbasen manuelt, og skjul et automatisk kryds, hvis det bygger på en fejl.", "info") : null,
      add, d.ventende.length ? [title("Afventer godkendelse"), ...d.ventende.map(v => h("div", {}, `${v.navn} · ${v.status}`))] : null, title(`Mine kryds (${d.rows.length})`),
      table(["#", "Art", "Dato", "Lokalitet", "Land", "Kilde", ""], d.rows.map((r, i) => ({ text: `${r.navn} ${r.lok || ""}`, cells: [i + 1, h("span", {}, r.navn, r.latin ? [" (", h("i", {}, r.latin), ")"] : null), r.dato || "", r.lok || "", r.land || "",
        r.kilde === "dofbasen" ? h("span", {}, h("span", { class: "kl-chip ok" }, "DOFbasen"), r.obsid ? [" ", h("a", { href: `#/dofbasen/obs/${r.obsid}` }, "obs.")] : null)
          : h("span", {}, h("span", { class: "kl-chip" }, r.obsid ? "markeret" : "manuel"), r.obsid ? [" ", h("a", { href: `#/dofbasen/obs/${r.obsid}` }, "obs.")] : null),
        r.kilde === "manuel" ? h("button", { type: "button", class: "btn sm danger", onclick: async () => { await api(`/api/krydslister/mine/${r.id}`, { method: "DELETE" }); load(); } }, "Slet")
          : d.kan_skjule && r.artnr ? h("button", { type: "button", class: "btn sm", title: "Skjul krydset (fx en fejlbestemmelse)", onclick: async () => { await api("/api/krydslister/mine/skjul", { method: "POST", body: { liste, artnr: r.artnr, hidden: true } }); load(); } }, "Skjul") : ""] })), { name: `mine-${liste}` }),
      d.hidden.length ? [title("Skjulte automatiske kryds"), ...d.hidden.map(x => h("div", { class: "row" }, x.navn, h("button", { type: "button", class: "btn sm", onclick: async () => { await api("/api/krydslister/mine/skjul", { method: "POST", body: { liste, artnr: x.artnr, hidden: false } }); load(); } }, "Vis igen")))] : null);
  }
  await load();
  return h("div", { class: "stack" }, h("div", { class: "lister-crumb" }, h("a", { href: "#/lister" }, "← Mine lister")), tabs, out);
}
