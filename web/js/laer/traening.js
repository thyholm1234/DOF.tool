// Læring 2.0: egne trænings­sessioner med foto og lyd (docs/laering-spec.md §6). Afløser #/laer/arter og #/laer/lyd.
import { api, h, toast, errorBox, can, state } from "../core.js";
import { BASE, loadingBox, backLink } from "./common.js";
import { runPlayer } from "./player.js";
import { speciesPicker, locationPicker, afdelingSelect } from "./pickers.js";

const KILDER = [["gruppe", "Artsgruppe"], ["arter", "Egne arter"], ["mine", "Mine arter (fra mine observationer)"], ["afdeling", "Min afdeling"],
  ["lokalitet", "En lokalitet"], ["kursus", "Arterne i et kursus"], ["svage", "Arter jeg tager fejl af"]];

export async function render(view, parts, info) {
  const [a, b] = parts;
  const opts = await api("/api/learn/traen/muligheder");
  const cfg = { kilde: "gruppe", gruppe: "", arter: [], m: a === "lyd" ? "lyd" : opts.foto ? "foto" : "lyd", metode: "valg", rtype: "begge", n: 20 };
  const names = new Map();
  let stop = null;
  const root = h("div", { class: "lr-train" });
  view.append(root);
  if (a === "delt" && b) {
    try { Object.assign(cfg, (await api(`/api/learn/traeninger/delt/${b}`)).config); toast("Opsætning hentet fra en delt træning"); } catch (e) { toast(e.message, "err"); }
  }
  const afd = state.user?.prefs?.afdelinger?.[0];
  if (afd && !cfg.afdeling_id) cfg.afdeling_id = afd;

  async function setup() {
    stop?.();
    const picked = new Set(cfg.arter);
    const kurser = (await api("/api/learn/katalog").catch(() => ({ kurser: [] }))).kurser;
    const mine = await api("/api/learn/traeninger").catch(() => []);
    const detail = h("div", { class: "lr-field" });
    const drawDetail = async () => {
      if (cfg.kilde === "gruppe") detail.replaceChildren(h("div", { class: "lr-groups" }, ...opts.grupper.map(g => h("button", { type: "button", class: `lr-group${cfg.gruppe === g.key ? " on" : ""}`, title: g.beskrivelse,
        onclick: () => { cfg.gruppe = g.key; drawDetail(); } }, h("b", {}, g.navn), h("small", { class: "muted" }, `${g.n} arter`)))));
      else if (cfg.kilde === "arter") { const p = speciesPicker(picked, names, () => (cfg.arter = [...picked])); detail.replaceChildren(p.el); }
      else if (cfg.kilde === "afdeling") detail.replaceChildren(await afdelingSelect(cfg.afdeling_id, (v) => (cfg.afdeling_id = v)));
      else if (cfg.kilde === "lokalitet") detail.replaceChildren(locationPicker(cfg.loknr ? { loknr: cfg.loknr, navn: `Lokalitet ${cfg.loknr}` } : null, (v) => (cfg.loknr = v.loknr)).el);
      else if (cfg.kilde === "kursus") detail.replaceChildren(h("select", { onchange: (e) => (cfg.kursus = e.target.value) }, h("option", { value: "" }, "Vælg kursus …"), ...kurser.map(k => h("option", { value: k.slug, selected: k.slug === cfg.kursus }, k.title))));
      else detail.replaceChildren(h("p", { class: "muted" }, cfg.kilde === "mine" ? "Alle arter du selv har indrapporteret i DOFbasen – de mest observerede først." : "Arter, hvor din repetitionskasse er lav (du har svaret forkert for nylig)."));
    };
    const radio = (name, items, key) => h("div", { class: "lr-radios", role: "radiogroup" }, ...items.map(([v, t]) => h("label", { class: `lr-radio${cfg[key] === v ? " on" : ""}` },
      h("input", { type: "radio", name, checked: cfg[key] === v, onchange: () => { cfg[key] = v; setup(); } }), t)));
    const modes = [["foto", "Fotos"], ["lyd", "Lyd"], ["begge", "Foto og lyd"]].filter(([v]) => (v === "foto" ? opts.foto : v === "lyd" ? opts.lyd : opts.foto && opts.lyd));
    root.replaceChildren(
      h("div", { class: "lr-intro" }, h("h2", {}, "Træn arter på foto og lyd"), h("p", { class: "muted" }, "Vælg selv, hvilke arter du vil øve. Fotos kommer fra fotobiblioteket, lyd fra xeno-canto og Macaulay Library.")),
      modes.length ? null : h("div", { class: "notice" }, "Foto- og lydtræning er ikke slået til endnu."),
      h("h3", {}, "1. Hvad vil du træne?"), radio("m", modes, "m"),
      h("h3", {}, "2. Hvilke arter?"), radio("kilde", KILDER, "kilde"), detail,
      h("h3", {}, "3. Hvordan vil du svare?"), radio("metode", [["valg", "Multiple choice"], ["tekst", "Skriv artens navn"]], "metode"),
      cfg.m !== "foto" ? [h("h3", {}, "Hvilken lyd?"), radio("rtype", [["sang", "Sang"], ["kald", "Kald"], ["begge", "Begge"]], "rtype")] : null,
      h("h3", {}, "4. Hvor mange?"), h("select", { onchange: (e) => (cfg.n = +e.target.value) }, ...[10, 20, 40].map(n => h("option", { value: n, selected: n === cfg.n }, `${n} spørgsmål`))),
      h("div", { class: "lr-actions" }, h("button", { class: "btn primary", disabled: !modes.length, onclick: () => start() }, "Start træning"),
        h("button", { class: "btn", onclick: save }, "Gem som træning")),
      mine.length ? [h("h3", {}, "Mine træninger"), h("div", { class: "lr-list" }, ...mine.map(t => h("div", { class: "lr-listrow" }, h("b", {}, t.title),
        h("span", { class: "lr-meta" }, h("button", { class: "btn sm", onclick: () => { Object.assign(cfg, t.config); picked.clear(); (cfg.arter || []).forEach(x => picked.add(x)); setup(); } }, "Åbn"),
          h("button", { class: "btn sm", onclick: async () => { const r = await api(`/api/learn/traeninger/${t.id}/del`, { method: "POST" }); const url = `${location.origin}${location.pathname}#/laer/traen/delt/${r.token}`; try { await navigator.clipboard.writeText(url); toast("Link kopieret"); } catch { prompt("Del dette link", url); } } }, "Del"),
          h("button", { class: "btn sm ghost danger", onclick: async () => { if (confirm("Slet træningen?")) { await api(`/api/learn/traeninger/${t.id}`, { method: "DELETE" }); setup(); } } }, "Slet"))))) ] : null,
      cfg.m !== "foto" && can("laering") ? await mlPanel() : null);
    await drawDetail();
  }

  async function save() {
    const title = prompt("Navn på træningen", cfg.kilde === "gruppe" ? `Træning: ${cfg.gruppe}` : "Min træning");
    if (!title) return;
    try { await api("/api/learn/traeninger", { method: "POST", body: { ...cfg, title } }); toast("Gemt"); setup(); } catch (e) { toast(e.message, "err"); }
  }

  async function start() {
    root.replaceChildren(loadingBox(cfg.m === "foto" ? "Henter fotos …" : "Finder optagelser … (kan tage lidt første gang)"));
    let qs;
    try { qs = (await api("/api/learn/traen", { method: "POST", body: cfg })).questions; } catch (e) { toast(e.message, "err"); await setup(); return; }
    const holder = h("div", { class: "lr-run" });
    root.replaceChildren(backLink(`${BASE}/traen`, "Ny træning"), holder);
    stop = runPlayer(holder, { title: "Træning", questions: qs, again: start, doneExtra: () => h("button", { class: "btn", onclick: setup }, "Ændr arter") });
  }

  await setup();
  return () => stop?.();
}

/** Macaulay Library-optagelser, som redaktører lægger ind (ML har intet åbent API). */
async function mlPanel() {
  const list = h("div", { class: "lr-list" });
  const load = async () => {
    const rows = await api("/api/learn/lyd/ml").catch(() => []);
    list.replaceChildren(...(rows.length ? rows.map(r => h("div", { class: "lr-listrow" }, h("span", {}, `${r.navn || r.artnr} · ML ${r.ext_id}${r.rtype ? " · " + r.rtype : ""}`),
      h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/learn/lyd/ml/${r.id}`, { method: "DELETE" }); load(); } }, "Fjern"))) : [h("p", { class: "muted" }, "Ingen endnu.")]));
  };
  await load();
  const ref = h("input", { placeholder: "ML-katalognummer eller link" }), art = h("input", { type: "number", placeholder: "Artsnummer (artnr)" });
  return h("details", { class: "lr-ml" }, h("summary", {}, "Macaulay Library-optagelser (redaktør)"),
    h("div", { class: "lr-actions" }, ref, art, h("button", { class: "btn sm primary", onclick: async () => { try { await api("/api/learn/lyd/ml", { method: "POST", body: { ref: ref.value, artnr: +art.value } }); ref.value = ""; load(); } catch (e) { toast(e.message, "err"); } } }, "Tilføj")), list);
}
