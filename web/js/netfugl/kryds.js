// Netfugl: krydslister. DK-lister beregnes automatisk ud fra deltagernes observationer i DOFbasen (første fund pr. art);
// manuelle kryds supplerer (VP, ringmærkning, ædelarter, arter fra før DOFbasen) og kan skjule et automatisk kryds.
import { api, h, fmtN, toast } from "../core.js";
import { BASE, headline, title, link, loadingBox, notice, md, editPage, autosuggest, dkDate, on, tabs } from "./shell.js";

const VIEWS = [["ranking", "Krydsliste"], ["seneste", "Seneste kryds"], ["hvem", "Hvem har set hvad?"], ["blockers", "Blockers"], ["blockerstat", "Blockers statistik"], ["tophits", "Årstophits"]];
let meta = null;
const loadMeta = async () => (meta ||= await api("/api/netfugl/kryds/lister"));

export async function page(parts, q) {
  const key = parts[1] || "";
  if (!key) return overview();
  if (key === "alle") return alle();
  if (key === "regler") return rules();
  if (parts[2] === "art") return speciesWho(key, decodeURIComponent(parts[3] || ""));
  return listView(key, parts[2] || "ranking", q);
}

function autoNote(at) {
  return h("p", { class: "small muted" }, "Krydslisterne beregnes automatisk ud fra observationerne i DOFbasen",
    at ? ` (senest ${dkDate(at, true)})` : " (første beregning er i gang)", ". Manuelle kryds tæller med med det samme. ",
    link("/mine-kryds", "Ret mine kryds"));
}

function table(head, rows, opts = {}) {
  const filter = h("input", { type: "search", placeholder: "Søg …", style: { width: "200px" } });
  const tbody = h("tbody", {});
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    tbody.replaceChildren(...rows.filter(r => !f || r.text.toLowerCase().includes(f)).map(r => h("tr", { class: r.cls || "" }, ...r.cells.map(c => h("td", { class: typeof c === "number" ? "num" : "" }, typeof c === "number" ? fmtN(c) : c)))));
  };
  filter.oninput = draw;
  draw();
  const csv = () => {
    const lines = [head.join(";"), ...rows.map(r => r.cells.map(c => (c instanceof Node ? c.textContent : String(c ?? ""))).map(s => `"${s.replaceAll('"', '""')}"`).join(";"))];
    const a = h("a", { href: URL.createObjectURL(new Blob(["﻿" + lines.join("\n")], { type: "text/csv" })), download: `${opts.name || "krydsliste"}.csv` });
    document.body.append(a); a.click(); a.remove();
  };
  return h("div", {}, h("div", { class: "nf-tools" }, h("button", { type: "button", class: "btn", onclick: csv }, "CSV"), h("button", { type: "button", class: "btn", onclick: () => print() }, "Print"),
    h("span", { class: "spacer" }), h("span", {}, "Søg: "), filter),
    h("div", { class: "nf-scroll" }, h("table", { class: "nf-table" }, h("thead", {}, h("tr", {}, ...head.map(t => h("th", {}, t)))), tbody)));
}

async function chooser(key, view) {
  const m = await loadMeta();
  const opts = [["dk", "Danske Arter"], ["vp", "VP Artsliste"], ["falskvp", "Falsk VP Artsliste"], ["ring", "Danske Ringmærkningsarter"], ["aedel", "Danske Ædelarter"],
    ...m.years.map(y => [`aar:${y}`, `Danske Årsarter ${y}`]), ...m.aedel_years.map(y => [`aedelaar:${y}`, `Danske Ædelårsarter ${y}`]),
    ...m.months.map((n, i) => [`md:${i + 1}`, `Månedsarter ${n}`]), ...m.afdelinger.map(a => [`afd:${a.id}`, `Lokalarter: ${a.navn}`]), ...m.kommuner.map(k => [`kom:${k.kode}`, `Lokalarter: ${k.navn}`])];
  const listSel = h("select", { onchange: () => { location.hash = `${BASE}/kryds/${listSel.value}/${viewSel.value}`; } }, ...opts.map(([v, t]) => h("option", { value: v, selected: v === key }, t)));
  const viewSel = h("select", { onchange: () => { location.hash = `${BASE}/kryds/${listSel.value}/${viewSel.value}`; } }, ...VIEWS.map(([v, t]) => h("option", { value: v, selected: v === view }, t)));
  return h("div", {}, h("hr"), h("p", {}, "Herunder er det muligt at vælge blandt alle krydslister, bl.a. årsartslister, kommuner, osv."), h("div", { class: "nf-tools" }, listSel, viewSel), h("hr"));
}

async function listView(key, view, q) {
  const out = h("div", {}, loadingBox("Beregner krydslisten …"));
  const head = h("div", {});
  const wrap = h("div", { class: "wrap" }, await chooser(key, view), out);
  const page = h("div", {}, head, wrap);
  let d;
  try { d = await api(`/api/netfugl/kryds/${encodeURIComponent(key)}/${view}${q.aar ? `?aar=${q.aar}` : ""}`); }
  catch (err) { out.replaceChildren(notice(err.message, "err")); head.replaceChildren(headline("Krydslister")); return page; }
  head.replaceChildren(headline(d.name));
  const vp = key === "vp" || key === "falskvp";
  const artLink = (r) => link(`/kryds/${encodeURIComponent(key)}/art/${encodeURIComponent(r.key)}`, h("span", {}, r.su ? h("span", { class: "nf-su" }, "* ") : null, r.navn, r.latin ? [" (", h("i", {}, r.latin), ")"] : null));
  const person = (r) => link(`/bruger/${r.kode}`, r.navn || r.person);
  let body;
  if (view === "ranking") {
    body = [h("p", {}, `Nedenstående ses en liste over personer, som har krydset deres arter af for krydslisten: ${d.name}`),
      table(["#", "Navn", ...(vp ? ["Land"] : []), "#X", "Opdateret", "Seneste kryds"], d.rows.map(r => ({ text: r.navn, cells: [r.placering, person(r), ...(vp ? [r.seneste_land || ""] : []),
        h("b", {}, link(`/bruger/${r.kode}`, String(r.antal))), r.opdateret || "", `${r.seneste || ""} ${r.opdateret || ""}`] })), { name: key })];
  } else if (view === "seneste") {
    body = [h("p", {}, `Nedenfor ses de seneste 100 nye kryds på ${d.name}.`),
      table(["#", "Navn", "#X", "Seneste nye kryds", "Lokation", "Dato", ""], d.rows.map((r, i) => ({ text: `${r.person} ${r.navn} ${r.lok || ""}`, cells: [i + 1, link(`/bruger/${r.kode}`, r.person),
        h("b", {}, r.x), artLink(r), r.lok || "", r.dato, r.kilde === "dofbasen" ? h("span", { class: "nf-chip ok", title: "Beregnet fra DOFbasen" }, "DOFbasen") : h("span", { class: "nf-chip" }, "manuel")] })), { name: `${key}-seneste` })];
  } else if (view === "hvem") {
    body = [h("p", {}, "Nedenstående ses den fulde liste over arter, som 'må' krydses. Arter der er set af en eller flere deltagere er markeret med et kryds (X). SU-arter er markeret med *."),
      table(["#", "X", "*", "Navn", `Set af (ud af ${d.total})`], d.rows.map((r, i) => ({ text: `${r.navn} ${r.latin}`, cls: r.antal ? "" : "race", cells: [i + 1, r.antal ? "X" : "", r.su ? "*" : "", artLink(r), r.antal] })), { name: `${key}-hvem` })];
  } else if (view === "blockers") {
    body = [h("p", {}, `Her ses de arter, som er set af færrest på krydslisten. De betegnes som 'blockers'. Definitionen på en blocker er en art, som er krydset af max 10% af deltagerne (her højst ${d.limit} af ${d.total}).`),
      table(["#", "Navn", `Set af (ud af ${d.total})`], d.rows.map((r, i) => ({ text: r.navn, cells: [i + 1, artLink(r), r.antal] })), { name: `${key}-blockers` })];
  } else if (view === "blockerstat") {
    body = [h("p", {}, "Her ses statistik over 'blockers', som er de arter der er mindst krydset blandt deltagerne. ", link(`/kryds/${key}/blockers`, "Se listen med blockers"), "."),
      table(["#", "Navn", "#X", "#Blockers"], d.stat.map((r, i) => ({ text: r.navn, cells: [i + 1, link(`/bruger/${r.kode}`, r.navn), r.antal, r.blockers] })), { name: `${key}-blockerstat` })];
  } else {
    const y = h("select", { onchange: () => { location.hash = `${BASE}/kryds/${key}/tophits?aar=${y.value}`; } }, ...Array.from({ length: 10 }, (_, i) => new Date().getFullYear() - i).map(v => h("option", { value: v, selected: v === d.aar }, v)));
    body = [h("p", {}, "Årets Tophits er en liste med de 50 mest krydsede arter for et givent år. Idéen stammer fra Club300 i Sverige ('Kungsarter')."), h("div", { class: "nf-tools" }, "År: ", y),
      table(["#", "Navn", "Krydset af", `Totalt set af (ud af ${d.total})`], d.rows.map((r, i) => ({ text: r.navn, cells: [i + 1, artLink(r), r.aar, r.total] })), { name: `${key}-tophits-${d.aar}` })];
  }
  const auto = !["vp", "falskvp", "ring", "aedel"].includes(key) && !key.startsWith("aedelaar");
  out.replaceChildren(auto || key === "vp" ? autoNote(d.auto_at) : h("p", { class: "small muted" }, "Listen består af deltagernes egne kryds. ", link("/mine-kryds", "Ret mine kryds")),
    h("p", { class: "small muted" }, `${d.deltagere} deltagere.`), ...body);
  return page;
}

async function speciesWho(key, skey) {
  const d = await api(`/api/netfugl/kryds/${encodeURIComponent(key)}/art/${encodeURIComponent(skey)}`);
  const obs = (r) => r.obsid ? (d.dofbasen ? h("a", { href: `#/dofbasen/obs/${r.obsid}` }, "Se observationen") : h("a", { href: `https://dofbasen.dk/popobs.php?obsid=${r.obsid}&summering=tur&obs=obs`, target: "_blank", rel: "noopener" }, "DOFbasen ↗")) : "";
  return h("div", {}, headline(d.art || "Art"), h("div", { class: "wrap" },
    h("p", {}, link(`/kryds/${key}/hvem`, `← ${d.name}`)),
    title(`Hvem har krydset ${d.art || "arten"}?`),
    d.rows.length ? table(["#", "Navn", "Dato", "Lokalitet", "Land", "Kilde"], d.rows.map((r, i) => ({ text: r.person, cells: [i + 1, link(`/bruger/${r.kode}`, r.person), r.dato || "", r.lok || "", r.land || "",
      r.kilde === "dofbasen" ? h("span", {}, h("span", { class: "nf-chip ok" }, "DOFbasen"), " ", obs(r)) : h("span", { class: "nf-chip" }, "manuel")] })), { name: "art" })
      : notice("Ingen deltagere har krydset arten.", "info")));
}

async function overview() {
  const [p, d] = await Promise.all([api("/api/netfugl/sider/krydslister").catch(() => null), api("/api/netfugl/kryds/oversigt")]);
  const box = h("div", {});
  const sec = (t, rows, key, linkTxt) => [title(t), rows.length ? table(["#", "Navn", "#X", "Seneste nye kryds", "Lokation", "Dato"], rows.map(r => ({ text: r.person,
    cells: [r.x, link(`/bruger/${r.kode}`, r.person), h("b", {}, r.x), link(`/kryds/${key}/art/${encodeURIComponent(r.key)}`, r.navn), r.lok || "", r.dato] })), { name: key })
    : h("p", { class: "muted" }, "Ingen kryds endnu."), h("p", { class: "small" }, link(`/kryds/${key}/seneste`, linkTxt))];
  box.append(p ? h("div", {}, p.can_edit ? h("div", { class: "nf-tools" }, h("button", { class: "btn btn-edit", type: "button", onclick: () => editPage(p, () => location.reload()) }, "Redigér tekst")) : null, md(p.body)) : null,
    autoNote(d.auto_at),
    ...sec("Seneste 10 nye DK kryds", d.dk, "dk", "Se de seneste 100 DK kryds"), ...sec("Seneste 10 nye VP kryds", d.vp, "vp", "Se de seneste 100 VP kryds"),
    ...(d.dk350.length ? sec("Seneste 10 nye DK kryds 350+", d.dk350, "dk", "Se de seneste 100 DK kryds") : []),
    ...(d.vp700.length ? sec("Seneste 10 nye VP kryds 700+", d.vp700, "vp", "Se de seneste 100 VP kryds") : []));
  return h("div", {}, headline("Krydslister"), h("div", { class: "wrap" }, box));
}

async function alle() {
  const m = await loadMeta();
  const views = (k, top = true) => h("span", { class: "small" }, ...VIEWS.filter(([v]) => top || v !== "tophits").map(([v, t], i) => [i ? " | " : "", link(`/kryds/${k}/${v === "ranking" ? "ranking" : v}`, v === "ranking" ? "Liste" : t)]));
  const row = (k, name, top = true) => h("div", { style: { margin: "8px 0" } }, h("b", {}, link(`/kryds/${k}/ranking`, name)), h("br"), views(k, top));
  return h("div", {}, headline("Alle krydslister"), h("div", { class: "wrap" },
    h("p", {}, "Nedenstående ses en komplet oversigt over krydslisterne. DK-listerne beregnes automatisk ud fra DOFbasen; VP, ringmærkning og ædelarter består af deltagernes egne kryds."),
    title("VP artslister"), row("vp", "VP Artsliste"), row("falskvp", "Falsk VP Artsliste"),
    title("Danske arter"), row("dk", "Danske Arter"), row("ring", "Danske Ringmærkningsarter"), row("aedel", "Danske Ædelarter"),
    ...m.years.map(y => row(`aar:${y}`, `Danske Årsarter ${y}`, false)), ...m.aedel_years.map(y => row(`aedelaar:${y}`, `Danske Ædelårsarter ${y}`, false)),
    title("Månedsarter"), ...m.months.map((n, i) => row(`md:${i + 1}`, `Månedsarter ${n}`, false)),
    title("Lokalarter – lokalafdelinger"), ...m.afdelinger.map(a => row(`afd:${a.id}`, a.navn, false)),
    title("Lokalarter – kommuner"), h("div", { style: { columns: "3 220px" } }, ...m.kommuner.map(k => h("div", {}, link(`/kryds/kom:${k.kode}/ranking`, k.navn))))));
}

async function rules() {
  const box = h("div", {});
  const load = async () => {
    const p = await api("/api/netfugl/sider/regler");
    box.replaceChildren(p.can_edit ? h("div", { class: "nf-tools" }, h("button", { class: "btn btn-edit", type: "button", onclick: () => editPage(p, load) }, "Redigér")) : null, md(p.body));
  };
  await load();
  return h("div", {}, headline("Krydsregler"), h("div", { class: "wrap" }, box));
}

// ---------------------------------------------------------------- mine kryds

export async function mine(parts, q) {
  const liste = ["dk", "vp", "falskvp", "ring", "aedel"].includes(q.liste) ? q.liste : "dk";
  const out = h("div", {});
  const load = async (l) => {
    out.replaceChildren(loadingBox());
    const d = await api(`/api/netfugl/mine-kryds/${l}`);
    const ref = l === "dk" || l === "ring" || l === "aedel" ? "dk" : "vp";
    const f = { species_id: null, dato: "", lokalitet: "", land: ref === "dk" ? "Danmark" : "", note: "" };
    const inp = (k, attrs = {}) => h("input", { type: attrs.type || "text", value: f[k], oninput: (e) => { f[k] = e.target.value; }, ...attrs });
    const add = h("form", { class: "nf-card", onsubmit: async (e) => {
      e.preventDefault();
      if (!f.species_id) { toast("Vælg en art", "err"); return; }
      try { await api("/api/netfugl/mine-kryds", { method: "POST", body: { liste: l, ...f, dato: f.dato || null } }); toast("Krydset er gemt"); load(l); }
      catch (err) { toast(err.message, "err"); }
    } }, h("div", { class: "body" }, h("b", {}, "Tilføj et kryds"),
      h("div", { class: "nf-row", style: { marginTop: "8px" } },
        h("label", { class: "nf-field" }, h("span", {}, "Art"), autosuggest({ url: `/api/netfugl/arter/${ref}?q=`, placeholder: "Artsnavn", label: (r) => `${r.su ? "* " : ""}${r.navn} (${r.latin})`, onPick: (r) => { f.species_id = r?.id || null; } })),
        h("label", { class: "nf-field" }, h("span", {}, "Dato"), inp("dato", { type: "date" })),
        h("label", { class: "nf-field" }, h("span", {}, "Lokalitet"), inp("lokalitet")),
        h("label", { class: "nf-field" }, h("span", {}, "Land"), inp("land", { style: { width: "130px" } })),
        h("label", { class: "nf-field" }, h("span", {}, "Note"), inp("note")),
        h("button", { class: "btn primary", type: "submit" }, "Tilføj")),
      h("p", { class: "small muted", style: { margin: 0 } }, ref === "dk" ? "SU-arter kræver dato og lokalitet." : "Arter markeret med * (VP-blockers) kræver land, lokalitet og dato.")));
    out.replaceChildren(
      d.participant ? null : notice("Du er ikke med på krydslisterne, fordi du har valgt ikke at blive vist på scoreboards. Slå det til under Profil → Lister og privatliv.", "info"),
      l === "dk" ? notice("Dine DK-kryds kommer automatisk fra dine observationer i DOFbasen (første fund pr. art). Tilføj arter fra før DOFbasen manuelt, og skjul et automatisk kryds, hvis det bygger på en fejl.", "info") : null,
      add,
      title(`${d.name}: ${d.rows.length} kryds`),
      table(["#", "Art", "Dato", "Lokalitet", "Land", "Kilde", ""], d.rows.map((r, i) => ({ text: `${r.navn} ${r.lok || ""}`, cells: [i + 1, h("span", {}, r.navn, r.latin ? [" (", h("i", {}, r.latin), ")"] : null),
        r.dato || "", r.lok || "", r.land || "",
        r.kilde === "dofbasen" ? h("span", {}, h("span", { class: "nf-chip ok" }, "DOFbasen"), r.obsid && on("netfugl_dofbasen") ? [" ", h("a", { href: `#/dofbasen/obs/${r.obsid}` }, "obs.")] : null) : h("span", { class: "nf-chip" }, "manuel"),
        r.kilde === "manuel" ? h("button", { type: "button", class: "btn sm danger", onclick: async () => { await api(`/api/netfugl/mine-kryds/${r.id}`, { method: "DELETE" }); load(l); } }, "Slet")
          : l === "dk" ? h("button", { type: "button", class: "btn sm", title: "Skjul krydset (fx en fejlbestemmelse)", onclick: async () => { await api("/api/netfugl/mine-kryds/skjul", { method: "POST", body: { artnr: r.artnr, hidden: true } }); load(l); } }, "Skjul") : ""] })), { name: `mine-${l}` }),
      d.hidden.length ? [title("Skjulte automatiske kryds"), ...d.hidden.map(x => h("div", { class: "nf-row" }, x.navn,
        h("button", { type: "button", class: "btn sm", onclick: async () => { await api("/api/netfugl/mine-kryds/skjul", { method: "POST", body: { artnr: x.artnr, hidden: false } }); load(l); } }, "Vis igen")))] : null);
  };
  load(liste);
  return h("div", {}, headline("Mine kryds"), h("div", { class: "wrap" },
    tabs([["dk", "Danske arter"], ["vp", "VP"], ["falskvp", "Falsk VP"], ["ring", "Ringmærkning"], ["aedel", "Ædelarter"]], liste, (v) => load(v)),
    h("div", { style: { marginTop: "14px" } }, out)));
}

