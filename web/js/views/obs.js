// Én observation (#/obs/<obsid>), åbnet fra Observationer (dagslisten): alle DOFbasens felter som popobs.php
// og resten af turlistens indtastninger. Trådene på Mit overblik bevarer deres samlede format.
import { api, h, fmtN, fmtDate, errorBox, tag, KAT, observerLink, backLink, icon } from "../core.js";
import { baseMap, katColor } from "../maps.js";

const SEX = { M: "han", F: "hun", MF: "han og hun", FM: "han og hun" };
const sexText = (k) => !k || k === "-" ? null : SEX[k.replace(/[^MF]/g, "")] || k;
const withCode = (txt, code) => txt && code && txt !== code ? `${txt} (${code})` : txt || code || null;
const span = (a, b) => [a, b].filter(Boolean).join("–");
const DKU = { behandling: "Til behandling hos DKU", dku: "Behandlet af DKU", su: "Godkendt af SU" };

export async function render(view, [obsid]) {
  view.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } }));
  let d;
  try { d = await api(`/api/dagsliste/obs/${obsid}`); }
  catch (err) { view.replaceChildren(h("p", {}, backLink("#/dagsliste", "Observationer")), errorBox(err)); return; }
  const o = d.obs;
  const thread = `#/traad/${o.dato}/${o.artnr}/${o.loknr}`;

  const facts = [
    ["Art", h("span", {}, h("a", { href: `#/art/${o.artnr}` }, o.art), o.latin ? h("i", { class: "muted" }, ` (${o.latin})`) : null)],
    ["Dato", fmtDate(o.dato)],
    ["Lokalitet", o.loknr ? h("a", { href: `#/lok/${o.loknr}` }, o.lokalitet || String(o.loknr)) : null],
    ["Kommune", o.kommune ? `${o.kommune} Kommune` : null],
    ["Afdeling", o.afdeling],
    ["Antal", fmtN(o.antal)],
    ["Køn", sexText(o.koen)],
    ["Alder", o.alderkode],
    ["Dragt", o.dragtkode && o.dragtkode !== "-" ? withCode(o.dragt, o.dragtkode) : null],
    ["Adfærd", withCode(o.adfaerd, o.adfkode)],
    ["Metode", o.metode],
    ["Obs.-tid", span(o.obstidfra, o.obstidtil)],
    ["Turtid", span(o.turtidfra, o.turtidtil)],
    ["Observatør", o.obserkode ? observerLink(o.obserkode, o.observator) : o.observator],
    ["Medobs.", o.medobser],
    ["Position", o.lat != null ? `${Number(o.lat).toFixed(5)}, ${Number(o.lon).toFixed(5)}${o.praecis ? "" : " (lokaliteten)"}` : null],
    ["DKU", o.dku ? DKU[o.dku] || o.dku : null],
    ["Obsid", String(o.obsid)],
  ].filter(([, v]) => v != null && v !== "");

  const mapEl = h("div", { class: "map small" });
  view.replaceChildren(
    h("p", {}, backLink("#/dagsliste", "Observationer")),
    h("div", { class: "page-head" },
      h("div", {},
        h("div", { class: "row" }, o.kategori && o.kategori !== "alm" ? tag(o.kategori, KAT[o.kategori]) : null, h("span", { class: "muted" }, fmtDate(o.dato))),
        h("h1", { style: { marginTop: "6px" } }, `${fmtN(o.antal)} ${o.art}`),
        h("p", { class: "lead" }, o.lokalitet || "", o.kommune ? ` · ${o.kommune} Kommune` : "")),
      h("span", { class: "spacer" }),
      h("div", { class: "row" },
        h("a", { class: "btn", href: thread }, "Se tråden"),
        h("a", { class: "btn ghost", target: "_blank", rel: "noopener", href: `https://dofbasen.dk/popobs.php?obsid=${o.obsid}&summering=tur&obs=obs`, html: `${icon("external")} DOFbasen` }))),
    h("div", { class: "grid-2" },
      h("div", { class: "stack" },
        h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Observationen"),
          h("dl", { class: "obs-facts" }, ...facts.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
          o.fuglnoter ? h("p", { style: { margin: "10px 0 0", whiteSpace: "pre-wrap" } }, o.fuglnoter) : null,
          o.turnoter ? h("p", { class: "muted", style: { margin: "6px 0 0", fontSize: ".86rem", whiteSpace: "pre-wrap" } }, "Tur: ", o.turnoter) : null)),
      o.lat != null ? h("div", { class: "card", style: { padding: "8px" } }, mapEl) : null),
    tripCard(d.tur, o));

  if (o.lat != null) {
    const map = baseMap(mapEl, { center: [o.lat, o.lon], zoom: o.praecis ? 13 : 11 });
    L.circleMarker([o.lat, o.lon], { radius: 8, color: katColor(o.kategori), weight: 3, fillOpacity: .4 }).addTo(map);
  }
}

/** Resten af turlisten: alle (ikke-hemmelige) indtastninger på samme tur, den åbne markeret. */
function tripCard(tur, o) {
  if (!tur.length) return null;
  const rows = tur.map(r => h("tr", { class: r.obsid === o.obsid ? "cur" : "" },
    h("td", { class: "num" }, r.obsid === o.obsid ? h("b", {}, fmtN(r.antal)) : h("a", { href: `#/obs/${r.obsid}` }, fmtN(r.antal))),
    h("td", {}, h("a", { href: `#/art/${r.artnr}` }, r.art), r.kategori === "SU" || r.kategori === "SUB" ? [" ", tag(r.kategori)] : null),
    h("td", {}, [sexText(r.koen), r.alderkode, r.dragtkode && r.dragtkode !== "-" ? r.dragt || r.dragtkode : null].filter(Boolean).join(", ")),
    h("td", {}, r.adfaerd || r.adfkode || ""),
    h("td", { class: "nowrap" }, span(r.obstidfra, r.obstidtil)),
    h("td", { class: "muted" }, r.fuglnoter || "")));
  return h("div", { class: "card mt" },
    h("h3", { style: { marginTop: 0 } }, `Turlisten · ${tur.length} indtastning${tur.length === 1 ? "" : "er"}`),
    h("div", { class: "obs-trip-wrap" }, h("table", { class: "obs-trip" }, h("tbody", {}, ...rows))));
}
