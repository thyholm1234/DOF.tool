// DOFbasen: fælles bokse (Danmarks fugle, højrekolonne) og popups for observation, lokalitet og observatør.
import { api, h, fmtN, toast } from "../core.js";
import "../maps.js";  // henter Leaflet til kortene på siderne her (søg, fænologi, opslag, forside)
import { BASE, head, link, dmy, notice, loadingBox, shortcuts, dbModal, on, MONTHS } from "./shell.js";

export const CAT_NAMES = ["Tilfældig", "Meget sjælden", "Sjælden", "Fåtallig", "Ret almindelig", "Almindelig", "Meget almindelig"];

/** Forekomst pr. 10-dagesperiode som DOFbasens farvebjælke (36 felter under månedsbogstaverne). */
export function faenobar(periods) {
  return h("div", {}, h("div", { class: "db-months" }, ..."jfmamjjasond".split("").map(c => h("span", {}, c))),
    h("div", { class: "db-faenobar" }, ...periods.map((p, i) => h("i", { class: `fc${p.cat}`, title: `${MONTHS[Math.floor(i / 3)]} ${["1.–10.", "11.–20.", "21.–"][i % 3]}: ${CAT_NAMES[p.cat]}` }))));
}

let artCache = null;
/** "Danmarks fugle": dagens art med forekomstbjælke. */
export async function dagensArt(el) {
  const box = h("div", { style: { textAlign: "center", marginBottom: "10px" } }, loadingBox());
  el.append(box);
  try {
    artCache = artCache || await api("/api/dofbasen/forside/art");
    const a = artCache;
    if (!a) { box.replaceChildren(); return; }
    box.replaceChildren(
      h("div", { style: { fontSize: "13px", fontWeight: 700, margin: "4px 0" } }, link(`/faeno/art/${a.artnr}`, a.navn)),
      h("div", { class: "muted" }, h("i", {}, a.latin)),
      h("div", { style: { display: "flex", justifyContent: "center", margin: "6px 0" } }, faenobar(a.periods)),
      h("i", { class: "small" }, "Forekomst af ", h("b", {}, a.navn), ` i DOFbasen ${a.years[0]}-${a.years[1]}. `, link(`/faeno/art/${a.artnr}`, "Se graf og farvekoder")));
  } catch { box.replaceChildren(); }
}

/** Standard-højrekolonne på indholdssider: genveje, Danmarks fugle og en tilfældig IBA-lokalitet. */
export function rightCol(extra = []) {
  const col = h("div", { class: "db-col r" }, ...shortcuts(), ...extra, h("div", { class: "db-h blue" }, h("span", {}, "Danmarks fugle")));
  dagensArt(col);
  const iba = h("div", {});
  col.append(iba);
  api("/api/dofbasen/omraader?kind=iba").then(rows => {
    if (!rows.length) return;
    const a = rows[Math.floor(Math.random() * rows.length)];
    iba.replaceChildren(head("IBA lokaliteter", { kind: "blue" }),
      h("p", { class: "small" }, "Tilfældig lokalitet udvalgt fra Danmarks vigtigste lokaliteter: ", link(`/opslag/omraade/${a.id}`, a.navn)));
  }).catch(() => {});
  return col;
}

/** Indholdsside: hovedkolonne + højrekolonne. */
export function layout(main, right = rightCol()) {
  return h("div", { class: "db-page" }, h("div", { class: "db-col" }, main), right);
}

// ---------------------------------------------------------------- popups

const DKU = { behandling: ["⇑", "Til behandling hos DKU"], dku: ["⇑", "Behandlet af DKU"], su: ["⇑", "Godkendt af SU"] };
export function dkuArrow(status) {
  if (!status || !DKU[status]) return null;
  return h("span", { class: `db-arrow ${status}`, title: DKU[status][1] }, DKU[status][0]);
}

function kv(rows) {
  return h("table", { class: "db-table" }, h("tbody", {}, ...rows.filter(Boolean).map(([k, v]) => h("tr", {}, h("td", { style: { width: "34%" } }, k ? `${k}:` : ""), h("td", {}, v)))));
}

/** Data for én observation (popobs.php) – som modal, eller som side når el er givet. */
export async function obsView(obsid, el) {
  const box = el || h("div", {}, loadingBox());
  const m = el ? null : dbModal(`Data for observation nr. ${obsid}`, box, { wide: false });
  let d;
  try { d = await api(`/api/dofbasen/obs/${obsid}`); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
  const o = d.obs;
  const nav = (e, path) => { if (m) { m.close(); } };
  const kat = o.kategori;
  const dkuBox = h("div", {});
  const drawDku = () => {
    dkuBox.replaceChildren();
    if (!o.can_dku) return;
    const sel = h("select", {}, ...[["", "Ingen"], ["behandling", "Til behandling (blå pil)"], ["dku", "Behandlet af DKU"], ["su", "Godkendt af SU"]]
      .map(([v, t]) => h("option", { value: v, selected: (o.dku || "") === v }, t)));
    const note = h("input", { type: "text", value: o.dku_note || "", placeholder: "Note til observatøren", style: { width: "100%" } });
    dkuBox.append(head("Kvalitetssikring (DKU)", { kind: "blue" }), h("div", { class: "db-form-row" }, sel,
      h("button", { type: "button", class: "db-primary", onclick: async () => {
        try { await api(`/api/dofbasen/obs/${obsid}/dku`, { method: "PUT", body: { status: sel.value, note: note.value } }); o.dku = sel.value; o.dku_note = note.value; toast("Status gemt"); render(); } catch (err) { toast(err.message, "err"); }
      } }, "Gem status")), note);
  };
  const render = () => {
    box.replaceChildren(
      head(`Data for observation nr. ${o.obsid} - ${dmy(o.dato).replaceAll("-", "/")}`, { kind: "blue", center: true }),
      kv([
        [null, h("span", {}, h("span", { class: kat === "SU" ? "db-su" : kat === "SUB" ? "db-subart" : "db-default" }, o.art), " (", h("i", {}, o.latin), ") ", h("b", {}, fmtN(o.antal)), " ", dkuArrow(o.dku))],
        ["Adfærd", o.adfaerd || o.adfkode || "–"],
        o.koen ? ["Køn", o.koen] : null,
        o.alderkode ? ["Alder", o.alderkode] : null,
        o.dragtkode ? ["Dragt", o.dragtkode] : null,
        ["Lokalitet", h("span", {}, link(`/lok/${o.loknr}`, o.lokalitet || String(o.loknr), { onclick: nav }), o.turtidfra ? ` (${o.turtidfra}${o.turtidtil ? " - " + o.turtidtil : ""})` : "")],
        o.obstidfra ? ["Tidspunkt", `${o.obstidfra}${o.obstidtil ? " - " + o.obstidtil : ""}`] : null,
        ["Observatør", link(`/obser/${o.obserkode}`, o.observator || o.obserkode, { onclick: nav })],
        o.medobser ? ["Medobservatører", o.medobser] : null,
        o.fuglnoter ? ["Kommentar", o.fuglnoter] : null,
        o.turnoter ? ["Turkommentar", o.turnoter] : null,
        o.metode ? ["Metode", o.metode] : null,
        o.praecis ? ["Fuglens position", `${o.lat.toFixed(5)}, ${o.lon.toFixed(5)}`] : null,
        o.dku ? ["Kvalitetssikring", h("span", {}, DKU[o.dku][1], o.dku_note ? ` – ${o.dku_note}` : "")] : null,
        ["Indtastet", o.first_seen ? new Date(o.first_seen).toLocaleString("da-DK") + " (hentet til portalen)" : "–"],
        ["Links", h("span", {}, link(`/faeno/art/${o.artnr}`, `Information om ${o.art}`, { onclick: nav }))],
        [null, h("a", { href: `#/traad/${o.dato}/${o.artnr}/${o.loknr}`, onclick: nav }, "Se tråden i DOF.tool (kommentarer, status, kort)")],
      ]),
      d.netfugl?.length ? [h("br"), head("Fotos på Netfugl", { kind: "blue", center: true }),
        h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: "8px" } }, ...d.netfugl.map(p =>
          h("a", { href: `#/netfugl/fotos/${p.id}`, title: `${p.art} – ${p.fotograf}`, style: { display: "block", border: "4px solid #eee" } },
            h("img", { src: p.url, alt: p.art, loading: "lazy", style: { width: "100%", height: "100px", objectFit: "cover", display: "block" } }),
            h("small", { style: { display: "block", padding: "2px 4px" } }, `© ${p.fotograf}`))))] : null,
      d.tur.length ? [h("br"), head("Alle observationer på turen", { kind: "blue", center: true }),
        h("table", { class: "db-table" }, h("tbody", {}, ...d.tur.map(t => h("tr", {},
          h("td", {}, h("a", { href: `${BASE}/obs/${t.obsid}`, class: "plain", onclick: (e) => { if (m) { e.preventDefault(); m.close(); obsView(t.obsid); } } },
            h("span", { class: t.kategori === "SU" ? "db-su" : t.kategori === "SUB" ? "db-subart" : "db-default" }, t.art))),
          h("td", {}, `${fmtN(t.antal)} ${(t.adfaerd || "").toLowerCase()}`)))))] : null,
      dkuBox);
    drawDku();
  };
  render();
  return box;
}

/** Lokalitet (poplok.php). */
export async function lokView(loknr, el) {
  const box = el || h("div", {}, loadingBox());
  const m = el ? null : dbModal(`Lokalitet ${loknr}`, box);
  let d;
  try { d = await api(`/api/dofbasen/lok/${loknr}`); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
  const mapEl = h("div", { class: "db-map small" });
  const s = d.stats || {};
  box.replaceChildren(head(`Data for ${d.navn}`, { kind: "blue", center: true }),
    kv([["Lokalitet", `${d.navn} (${d.loknr})`], ["Lokalafdeling", d.afdeling || "–"], ["Kommune", d.kommune || "–"],
      d.omraader?.length ? ["Indgår i", h("span", {}, ...d.omraader.map((a, i) => [i ? ", " : "", link(`/opslag/omraade/${a.id}`, `${a.navn} (${a.kind === "iba" ? "IBA" : "storlokalitet"})`, { onclick: () => m?.close() })]))] : null,
      ["Observationer", fmtN(s.n_obs)], ["Arter", fmtN(s.n_arter)], ["Observatører", fmtN(s.n_obser)],
      ["Første / seneste", `${dmy(s.first)} / ${dmy(s.last)}`],
      ["Links", h("span", {},
        on("dofbasen_search") ? link(`/soeg?periode=antaldage&dage=30&omraade=lokalitet&lok=${d.loknr}&soeg=1`, "Seneste 30 dages observationer", { onclick: () => m?.close() }) : null,
        " · ", h("a", { href: `#/lok/${d.loknr}`, onclick: () => m?.close() }, "Lokalitetens side i DOF.tool"))]]),
    d.lat ? mapEl : null,
    d.seneste.length ? [head("Seneste observationer", { kind: "blue" }), h("table", { class: "db-table" }, h("tbody", {}, ...d.seneste.map(o => h("tr", {},
      h("td", { style: { width: "80px" } }, dmy(o.dato)),
      h("td", {}, h("a", { href: `${BASE}/obs/${o.obsid}`, class: "plain", onclick: (e) => { if (m) { e.preventDefault(); m.close(); obsView(o.obsid); } } },
        h("span", { class: o.kategori === "SU" ? "db-su" : o.kategori === "SUB" ? "db-subart" : "" }, o.art))),
      h("td", { class: "num" }, fmtN(o.antal))))))] : null);
  if (d.lat) requestAnimationFrame(() => {
    const map = L.map(mapEl, { scrollWheelZoom: false }).setView([d.lat, d.lon], 13);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
    L.marker([d.lat, d.lon]).addTo(map);
  });
  return box;
}

/** Observatør (popobser.php) – uden adresse og telefon. */
export async function obserView(kode, el) {
  const box = el || h("div", {}, loadingBox());
  if (!el) dbModal(`Observatør ${kode}`, box);
  let d;
  try { d = await api(`/api/dofbasen/obser/${encodeURIComponent(kode)}`); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
  const navn = `${d.fornavn || ""} ${d.efternavn || ""}`.trim() || d.obserkode;
  const mx = Math.max(1, ...d.months.map(x => x.n));
  const ym = Math.max(1, ...d.years.map(x => x.n));
  const bars = (rows, max, label) => h("div", {}, h("div", { class: "db-bars", style: { height: "90px" } }, ...rows.map(r => h("div", { style: { height: `${(r.n / max) * 100}%` }, title: `${label(r)}: ${fmtN(r.n)}` }))),
    h("div", { class: "db-bars-x" }, ...rows.map(r => h("span", {}, label(r)))));
  box.replaceChildren(head(`Data for ${navn}`, { kind: "blue", center: true }),
    kv([["Navn", navn], ["Obserkode", d.obserkode], ["By", d.by || "–"], ["Seneste bidrag", h("span", { style: { color: "#ff3300" } }, dmy(d.stats?.last))],
      ["Observationer i databasen", fmtN(d.stats?.n_obs)], ["Antal fuglearter i databasen", fmtN(d.stats?.n_arter)],
      [null, on("dofbasen_search") ? link(`/soeg?periode=antaldage&dage=30&obserdata=${d.obserkode}&soeg=1`, "Seneste 30 dages observationer") : null]]),
    h("p", { class: "muted small" }, "Adresse og telefonnummer vises ikke i showcasen."),
    head(`Fordelingen af observationer af ${navn}`, { kind: "blue", center: true }),
    h("p", {}, "Observationer pr. måned"), bars(d.months, mx, (r) => "jfmamjjasond"[r.m - 1]),
    h("p", { style: { marginTop: "12px" } }, "Observationer pr. år"), bars(d.years.slice(-30), ym, (r) => String(r.y).slice(2)));
  return box;
}

/** Klik på links til /obs, /lok og /obser åbner popup i stedet for at skifte side (som DOFbasens popup-vinduer). */
export function popupLinks(root) {
  root.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const href = a.getAttribute("href");
    let m;
    if ((m = href.match(/^#\/dofbasen\/obs\/(\d+)$/))) { e.preventDefault(); obsView(+m[1]); }
    else if ((m = href.match(/^#\/dofbasen\/lok\/(\d+)$/))) { e.preventDefault(); lokView(+m[1]); }
    else if ((m = href.match(/^#\/dofbasen\/obser\/([\w]+)$/))) { e.preventDefault(); obserView(m[1]); }
  });
}

