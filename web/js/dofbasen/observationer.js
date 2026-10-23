// DOFbasen: dagens observationer (/observationer/index.php) – visning, dato, sortering, summering og afdeling.
import { api, h, fmtN, setParams, todayISO } from "../core.js";
import { BASE, head, link, dmy, weekday, notice, loadingBox, shortcuts, ctx } from "./shell.js";
import { dkuArrow, popupLinks } from "./common.js";

const VISNING = [["fugle", "Fugle - bemærkelsesværdige"], ["allfugle", "Fugle - alle"], ["sufugle", "Fugle - sjældne"], ["trkfugle", "Fugle - trækkende"],
  ["patdyr", "Pattedyr"], ["somfugle", "Sommerfugle"], ["krybdyr", "Padder/krybdyr"]];

/** dd-mm-åååå eller åååå-mm-dd → åååå-mm-dd */
export function parseDate(s) {
  if (!s) return null;
  let m = String(s).match(/^(\d{1,2})-(\d{1,2})-(\d{4})$/);
  if (m) return `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`;
  m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? s : null;
}
const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

export async function page(parts, q) {
  const st = {
    visning: VISNING.some(v => v[0] === q.visning) ? q.visning : "fugle",
    dato: parseDate(q.visidag || q.dato) || todayISO(),
    showstyle: q.showstyle === "lokalitet" ? "lokalitet" : "art",
    summering: ["tur", "dato", "ingen"].includes(q.summering) ? q.summering : "tur",
    lok: q.lok && q.lok !== "dk" ? q.lok : "", lokalitet: q.lokalitet || "",
  };
  const out = h("div", {});
  const visSel = h("select", {}, ...VISNING.map(([v, t]) => h("option", { value: v, selected: v === st.visning }, t)));
  const datoIn = h("input", { type: "text", value: dmy(st.dato), size: 10 });
  const sortSel = h("select", {}, ...[["art", "Art"], ["lokalitet", "Lokalitet"]].map(([v, t]) => h("option", { value: v, selected: v === st.showstyle }, t)));
  const sumSel = h("select", {}, ...[["tur", "Tur"], ["dato", "Dato"], ["ingen", "Ingen summering"]].map(([v, t]) => h("option", { value: v, selected: v === st.summering }, t)));
  const afdSel = h("select", {}, h("option", { value: "" }, ""), ...(ctx.data.afdelinger || []).map(a => h("option", { value: a.id, selected: String(a.id) === st.lok }, a.navn)));
  const go = () => {
    st.visning = visSel.value; st.dato = parseDate(datoIn.value) || todayISO(); st.showstyle = sortSel.value; st.summering = sumSel.value; st.lok = afdSel.value;
    setParams({ visning: st.visning, dato: st.dato, showstyle: st.showstyle, summering: st.summering, lok: st.lok, lokalitet: st.lokalitet });
    load();
  };
  const form = h("form", { onsubmit: (e) => { e.preventDefault(); go(); } }, h("div", { class: "db-form-row" },
    h("label", {}, "Observationer:", visSel), h("label", {}, "Dato:", datoIn), h("label", {}, "Sortering:", sortSel),
    h("label", {}, "Summering:", sumSel), h("label", {}, "Afdeling:", afdSel), h("button", { type: "submit" }, "Vis observationer")));
  const navRow = h("div", { class: "db-form-row" });

  const load = async () => {
    navRow.replaceChildren(
      h("button", { type: "button", onclick: () => { datoIn.value = dmy(addDays(st.dato, -1)); go(); } }, `<< ${dmy(addDays(st.dato, -1))}`),
      st.dato < todayISO() ? h("button", { type: "button", onclick: () => { datoIn.value = dmy(addDays(st.dato, 1)); go(); } }, `${dmy(addDays(st.dato, 1))} >>`) : null,
      st.lokalitet ? h("span", {}, "Kun én lokalitet · ", h("a", { href: "javascript:void 0", onclick: () => { st.lokalitet = ""; go(); } }, "vis alle")) : null);
    out.replaceChildren(loadingBox("Henter observationer …"));
    let d;
    try {
      d = await api(`/api/dofbasen/observationer?${new URLSearchParams({ visning: st.visning, dato: st.dato, summering: st.summering, ...(st.lok ? { lok: st.lok } : {}), ...(st.lokalitet ? { lokalitet: st.lokalitet } : {}) })}`);
    } catch (err) { out.replaceChildren(notice(err.message, true)); return; }
    const n = d.rows.reduce((s, r) => s + r.n_obs, 0);
    out.replaceChildren(head(`${weekday(st.dato)} den ${dmy(st.dato)} (${fmtN(n)} obs.)`, { kind: "blue", center: true }),
      d.note ? notice(d.note) : null, ...render(d.rows, st));
  };

  const main = h("div", {},
    head("Observationer"),
    h("p", {}, "Herunder ses de seneste observationer i DOFbasen. Du kan vælge imellem observationstypen, dato og lokalafdeling. Visningen af observationer kan sorteres efter henholdsvis lokalitet eller art. Der kan vælges at summere antal individer for en art indtastet af samme observatør på samme lokalitet, enten kun indenfor turen eller på hele datoen.",
      h("br"), "Klik på observationens antal fugle for yderligere oplysninger om observationen."),
    form, navRow, out);
  popupLinks(main);
  load();
  const right = h("div", { class: "db-col r" }, ...shortcuts(), legend(), h("p", { style: { marginTop: "10px" } }, link("/side/english", "See page in English")));
  return h("div", { class: "db-page" }, h("div", { class: "db-col" }, main), right);
}

const ICONS = [["turkommentar", "💬", "c1", "Turkommentar"], ["summeret", "💬", "c2", "Summeret obs."], ["kommentar", "🗨", "c3", "Kommentar"],
  ["medobser", "💬", "c4", "Medobservatør"], ["tid", "🕓", "c3", "Tidspunkt"], ["position", "📍", "c3", "Fuglens position"]];

function icons(r) {
  const flags = { turkommentar: r.turkommentar, summeret: r.n_obs > 1, kommentar: r.kommentar, medobser: r.medobser };
  return h("td", { class: "icons" }, ...ICONS.slice(0, 4).map(([k, , c, t]) => flags[k] ? h("span", { class: `db-ico ${c}`, title: t }, k === "kommentar" ? "▢" : "●") : h("span", { class: "db-ico" }, " ")));
}

const sexSign = (k) => !k ? "" : k.includes("M") && k.includes("F") ? " ♂♀" : k.includes("M") ? " ♂" : k.includes("F") ? " ♀" : "";
const katClass = (k) => k === "SU" ? "db-su" : k === "SUB" ? "db-subart" : "db-default";

function obsRow(r, { showArt }) {
  return h("tr", {},
    icons(r),
    showArt ? h("td", { style: { width: "28%" } }, h("span", { class: katClass(r.kat) }, r.art)) : null,
    h("td", { class: "n" }, h("a", { href: `${BASE}/obs/${r.obsid}`, class: "plain", title: r.n_obs > 1 ? `Summeret af ${r.n_obs} observationer` : "Yderligere oplysninger" }, fmtN(r.antal)), sexSign(r.koen), " ", dkuArrow(r.dku)),
    h("td", { class: "a" }, r.alder || ""),
    h("td", { class: "k" }, r.adfkode || ""),
    h("td", { class: "t" }, r.tid ? h("span", { title: "Tidspunkt" }, "🕓") : "", r.position ? h("span", { title: "Fuglens position" }, " 📍") : "", r.dragt ? ` ${r.dragt}` : ""),
    showArt ? null : h("td", { class: "loc" }, link(`/lok/${r.loknr}`, r.lokalitet || String(r.loknr))),
    h("td", {}, "[", link(`/obser/${r.obserkode}`, r.observator || r.obserkode), "]"));
}

function render(rows, st) {
  if (!rows.length) return [h("p", { class: "muted" }, "Ingen observationer.")];
  const afdName = Object.fromEntries((ctx.data.afdelinger || []).map(a => [a.id, a.navn]));
  const byAfd = new Map();
  for (const r of rows) {
    const k = r.afdeling_id ?? 0;
    if (!byAfd.has(k)) byAfd.set(k, []);
    byAfd.get(k).push(r);
  }
  const out = [];
  for (const [afd, list] of byAfd) {
    out.push(head(afdName[afd] || "Uden afdeling"));
    const tb = h("tbody", {});
    if (st.showstyle === "art") {
      let last = null;
      for (const r of list) {
        if (r.artnr !== last) {
          tb.append(h("tr", { class: "art" }, h("td", { colspan: 8 }, h("span", { class: katClass(r.kat) }, r.art), " (", h("i", {}, r.latin), "):")));
          last = r.artnr;
        }
        tb.append(obsRow(r, { showArt: false }));
      }
    } else {
      const byLoc = new Map();
      for (const r of list) { if (!byLoc.has(r.loknr)) byLoc.set(r.loknr, []); byLoc.get(r.loknr).push(r); }
      for (const [loknr, lr] of [...byLoc].sort((a, b) => (a[1][0].lokalitet || "").localeCompare(b[1][0].lokalitet || "", "da"))) {
        tb.append(h("tr", { class: "art" }, h("td", { colspan: 8 }, h("b", {}, link(`/lok/${loknr}`, lr[0].lokalitet || String(loknr))), ":")));
        for (const r of lr) tb.append(obsRow(r, { showArt: true }));
      }
    }
    out.push(h("div", { class: "db-scroll" }, h("table", { class: "db-obs" }, tb)));
  }
  return out;
}

function legend() {
  const row = (ic, cls, t) => h("div", {}, h("span", { class: `db-ico ${cls}` }, ic), h("span", {}, t));
  return h("div", { class: "db-legend" }, h("h4", {}, "TEGNFORKLARING"),
    row("●", "c1", "Turkommentar"), row("●", "c2", "Summeret obs."), row("▢", "c3", "Kommentar"), row("●", "c4", "Medobservatør"),
    row("🕓", "", "Tidspunkt"), row("📍", "", "Fuglens position"), row("♂", "", "Han"), row("♀", "", "Hun"),
    row("⇑", "db-arrow dku", "Behandlet af DKU"), row("⇑", "db-arrow su", "Godkendt af SU"), row("⇑", "db-arrow behandling", "Til behandling"),
    h("div", {}, h("span", {}), h("span", { class: "db-su" }, "Sjælden")), h("div", {}, h("span", {}), h("span", { class: "db-subart" }, "Fåtallig")),
    h("div", {}, h("span", {}), h("span", { class: "db-season" }, "Periodisk fåtallig")), h("div", {}, h("span", {}), h("span", {}, "[burfugl]")));
}
