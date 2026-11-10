// En krydsliste og dens visninger: krydsliste, seneste kryds, hvem har set hvad, blockers, blockers-statistik og årstophits; samt «hvem har krydset arten».
import { api, h, fmtN, state } from "../core.js";
import { BASE, link, table, title, loadingBox, notice, dkDate } from "./common.js";

const VIEWS = [["ranking", "Rangliste"], ["seneste", "Seneste kryds"], ["hvem", "Hvem har set hvad?"], ["blockers", "Blockers"], ["blockerstat", "Blockers statistik"], ["tophits", "Årstophits"]];
const person = (r) => h("a", { href: `#/observator/${r.kode}` }, r.navn || r.person);

export async function page(parts, q) {
  const key = decodeURIComponent(parts[1]), view = parts[2] || "ranking";
  const [meta, d] = await Promise.all([api("/api/krydslister/lister"), api(`/api/krydslister/kryds/${encodeURIComponent(key)}/${view}${q.aar ? `?aar=${q.aar}` : ""}`).catch(e => ({ error: e.message }))]);
  const groups = [];  // [label, [[værdi, navn]]] – vælgeren grupperes, så de mange lister er til at finde rundt i
  for (const l of meta.lister) {
    const add = (label, items) => groups.push([label, items]);
    if (!l.param) { (groups.find(g => g[0] === "Lister") || (groups.push(["Lister", []]), groups.at(-1)))[1].push([l.key, l.name]); continue; }
    const title = l.name.replace(/[:\s]*\{p\}/, "").trim();
    if (l.param === "year") add(title, (l.key === "aedelaar" ? meta.aedel_years : meta.years).map(y => [`${l.key}:${y}`, l.name.replace("{p}", y)]));
    else if (l.param === "month") add(title, meta.months.map((n, i) => [`${l.key}:${i + 1}`, l.name.replace("{p}", n)]));
    else if (l.param === "afdeling") add(`${title} – lokalafdelinger`, meta.afdelinger.map(a => [`${l.key}:${a.id}`, a.navn]));
    else if (l.param === "kommune") add(`${title} – kommuner`, meta.kommuner.map(k => [`${l.key}:${k.kode}`, k.navn]));
  }
  const go = () => { location.hash = `${BASE}/kryds/${encodeURIComponent(listSel.value)}/${viewSel.value}`; };
  const listSel = h("select", { "aria-label": "Krydsliste", onchange: go }, ...groups.map(([label, items]) => h("optgroup", { label }, ...items.map(([v, t]) => h("option", { value: v, selected: v === key }, t)))));
  const viewSel = h("select", { "aria-label": "Visning", onchange: go }, ...VIEWS.map(([v, t]) => h("option", { value: v, selected: v === view }, t)));
  const head = h("div", {}, h("div", { class: "lister-switch" }, h("span", { class: "muted" }, "Vis:"),
    h("div", { class: "seg" }, h("button", { type: "button", onclick: () => { location.hash = "#/lister/rangliste"; } }, "Årslister fra DOFbasen"), h("button", { type: "button", class: "on" }, "Krydslister"))),
    h("div", { class: "card lister-filter" }, h("div", { class: "lister-filter-row" }, listSel, viewSel, h("span", { class: "spacer" }), link("/nyt", "Seneste kryds"), link("/alle", "Alle krydslister"))));
  if (d.error) return h("div", {}, head, notice(d.error, "err"));
  const artLink = (r) => link(`/kryds/${encodeURIComponent(key)}/art/${encodeURIComponent(r.key)}`, h("span", {}, r.su ? h("b", { class: "kl-su" }, "* ") : null, r.navn, r.latin ? [" (", h("i", {}, r.latin), ")"] : null));
  const vp = key === "vp" || key === "falskvp";
  let body;
  if (view === "ranking") {
    const top = d.rows[0]?.antal || 1;
    body = [d.rows.length ? h("div", { class: "rank" }, ...d.rows.map(r => h("a", { class: `rank-row ${state.user?.obserkode === r.kode ? "me" : ""}`, href: `#/observator/${r.kode}` },
      h("div", { class: "place" }, r.placering),
      h("div", {}, h("div", { class: "who" }, r.navn), h("div", { class: "last" }, r.seneste ? `Seneste: ${r.seneste}${r.opdateret ? ` · ${dkDate(r.opdateret)}` : ""}${vp && r.seneste_land ? ` · ${r.seneste_land}` : ""}` : ""),
        h("div", { class: "bar-inline" }, h("i", { style: { width: `${(r.antal / top) * 100}%` } }))),
      h("div", { class: "score" }, fmtN(r.antal))))) : notice("Ingen deltagere har kryds på listen endnu."),
    h("details", { class: "mt" }, h("summary", { class: "muted" }, "Vis som tabel (CSV og print)"), table(["#", "Navn", ...(vp ? ["Land"] : []), "#X", "Opdateret", "Seneste kryds"], d.rows.map(r => ({ text: r.navn, cells: [r.placering, person(r), ...(vp ? [r.seneste_land || ""] : []), h("b", {}, String(r.antal)), r.opdateret || "", r.seneste || ""] })), { name: key }))];
  } else if (view === "seneste") {
    body = [h("p", {}, `Nedenfor ses de seneste 100 nye kryds på ${d.name}.`), table(["#", "Navn", "#X", "Seneste nye kryds", "Lokation", "Dato", ""], d.rows.map((r, i) => ({ text: `${r.person} ${r.navn} ${r.lok || ""}`,
      cells: [i + 1, person(r), h("b", {}, r.x), artLink(r), r.lok || "", r.dato, r.kilde === "dofbasen" ? h("span", { class: "kl-chip ok", title: "Beregnet fra DOFbasen" }, "DOFbasen") : h("span", { class: "kl-chip" }, "manuel")] })), { name: `${key}-seneste` })];
  } else if (view === "hvem") {
    body = [h("p", {}, "Nedenstående ses den fulde liste over arter, som 'må' krydses. Arter der er set af en eller flere deltagere er markeret med et kryds (X). Sjældne arter er markeret med *."),
      table(["#", "X", "*", "Navn", `Set af (ud af ${d.total})`], d.rows.map((r, i) => ({ text: `${r.navn} ${r.latin}`, cls: r.antal ? "" : "kl-none", cells: [i + 1, r.antal ? "X" : "", r.su ? "*" : "", artLink(r), r.antal] })), { name: `${key}-hvem` })];
  } else if (view === "blockers") {
    body = [h("p", {}, `Arter, som er set af færrest på krydslisten ('blockers'): krydset af højst 10 % af deltagerne (her højst ${d.limit} af ${d.total}).`),
      table(["#", "Navn", `Set af (ud af ${d.total})`], d.rows.map((r, i) => ({ text: r.navn, cells: [i + 1, artLink(r), r.antal] })), { name: `${key}-blockers` })];
  } else if (view === "blockerstat") {
    body = [h("p", {}, "Statistik over 'blockers'. ", link(`/kryds/${key}/blockers`, "Se listen med blockers"), "."),
      table(["#", "Navn", "#X", "#Blockers"], d.stat.map((r, i) => ({ text: r.navn, cells: [i + 1, person(r), r.antal, r.blockers] })), { name: `${key}-blockerstat` })];
  } else {
    const y = h("select", { onchange: () => { location.hash = `${BASE}/kryds/${encodeURIComponent(key)}/tophits?aar=${y.value}`; } }, ...Array.from({ length: 10 }, (_, i) => new Date().getFullYear() - i).map(v => h("option", { value: v, selected: v === d.aar }, v)));
    body = [h("p", {}, "Årets Tophits: de 50 mest krydsede arter for et givent år."), h("div", { class: "kl-tools" }, "År: ", y),
      table(["#", "Navn", "Krydset af", `Totalt set af (ud af ${d.total})`], d.rows.map((r, i) => ({ text: r.navn, cells: [i + 1, artLink(r), r.aar, r.total] })), { name: `${key}-tophits-${d.aar}` })];
  }
  return h("div", { class: "stack" }, h("h2", {}, d.name), head,
    d.auto ? h("p", { class: "small muted" }, "Listen beregnes automatisk ud fra observationerne i DOFbasen", d.auto_at ? ` (senest ${dkDate(d.auto_at, true)})` : "", ". Manuelle kryds tæller med. ", link("/mine?liste=" + encodeURIComponent(key.split(":")[0]), "Ret mine kryds"))
      : h("p", { class: "small muted" }, "Listen består af deltagernes egne kryds. ", link("/mine?liste=" + encodeURIComponent(key.split(":")[0]), "Ret mine kryds")),
    h("p", { class: "small muted" }, `${d.deltagere} deltagere.`), ...body);
}

export async function hvem(parts) {
  const key = decodeURIComponent(parts[1]), skey = decodeURIComponent(parts[3] || "");
  const d = await api(`/api/krydslister/kryds/${encodeURIComponent(key)}/art/${encodeURIComponent(skey)}`);
  const obs = (r) => r.obsid ? h("a", { href: `#/dofbasen/obs/${r.obsid}` }, "Se observationen") : "";
  return h("div", { class: "stack" }, link(`/kryds/${key}/hvem`, `← ${d.name}`), h("h2", {}, `Hvem har krydset ${d.art || "arten"}?`),
    d.rows.length ? table(["#", "Navn", "Dato", "Lokalitet", "Land", "Kilde"], d.rows.map((r, i) => ({ text: r.person, cells: [i + 1, person(r), r.dato || "", r.lok || "", r.land || "",
      r.kilde === "dofbasen" ? h("span", {}, h("span", { class: "kl-chip ok" }, "DOFbasen"), " ", obs(r)) : h("span", { class: "kl-chip" }, "manuel")] })), { name: "art" }) : notice("Ingen deltagere har krydset arten."));
}
