// DOFbasen: fænologi (/faeno/…) – artsside, første iagttagelse, samme dato og artskort.
import { api, h, fmtN, setParams } from "../core.js";
import { BASE, head, link, dmySlash, notice, loadingBox, autosuggest, MONTHS, on } from "./shell.js";
import { layout, faenobar, CAT_NAMES, popupLinks } from "./common.js";

export async function page(parts, q) {
  const sub = parts[1] || "";
  if (sub === "firstobs") return firstobs(q);
  if (sub === "samedate") return samedate(q);
  if (sub === "artkort") return artkort(q);
  return artPage(parts[2] ? +parts[2] : null);
}

function artPicker(onPick, initial = "") {
  return h("div", { class: "db-form-row" }, h("label", {}, "Art:", autosuggest({ url: "/api/dofbasen/arter/soeg?q=", placeholder: "Skriv artsnavn", width: "240px", initial, onPick: (r) => r && onPick(r) })));
}

async function artPage(artnr) {
  const out = h("div", {});
  const main = h("div", {}, head("Fænologi og fotos"),
    h("ul", { class: "db-md" }, h("li", {}, "Vælg art for visning af førstegangs-iagttagelser, årsfordeling og fænologi.")),
    artPicker((r) => { location.hash = `${BASE}/faeno/art/${r.artnr}`; }), out);
  if (!artnr) return layout(main);
  out.append(loadingBox("Beregner fænologi …"));
  api(`/api/dofbasen/faeno/art/${artnr}`).then(d => {
    const year = new Date().getFullYear();
    const max = Math.max(0.0001, ...d.periods.map(p => Math.max(p.ref_per_tur, p.aar_per_tur || 0)));
    out.replaceChildren(
      head(`${d.navn} - ${d.latin || ""}`),
      h("p", {}, "Første iagttagelse de seneste 10 år (maks. 5 lokaliteter):"),
      h("table", { class: "db-table plain" }, h("tbody", {}, ...d.first.map(f => h("tr", {},
        h("td", { style: { width: "86px" } }, dmySlash(f.dato) + ":"),
        h("td", {}, ...f.lokaliteter.map((l, i) => [i ? ", " : "", link(`/lok/${l.loknr}`, l.navn || String(l.loknr))])))))),
      head("Diagrammer/kort"),
      h("p", {}, `Søjlerne viser det gennemsnitlige antal individer observeret af arten pr. tur; indtastet i DOFbasen i perioden ${d.years[0]}-${d.years[1]} fordelt på 10-dagesperioder. Den sorte streg viser forekomsten i ${year}. Farven viser forekomstkategorien.`),
      h("div", { class: "db-chartbox", style: { paddingLeft: "10px" } },
        h("div", { class: "db-prof" }, ...d.periods.map((p, i) => h("div", { title: `${MONTHS[Math.floor(i / 3)]} ${["1.–10.", "11.–20.", "21.–"][i % 3]}: ${p.ref_per_tur.toFixed(2)} ind./tur (${CAT_NAMES[p.cat]})${p.aar_per_tur != null ? `, ${year}: ${p.aar_per_tur.toFixed(2)}` : ""}` },
          h("i", { class: `fc${p.cat}`, style: { height: `${(p.ref_per_tur / max) * 100}%`, border: "1px solid #777", borderBottom: 0 } }),
          p.aar_per_tur != null ? h("b", { style: { bottom: `${(p.aar_per_tur / max) * 100}%` } }) : null))),
        h("div", { class: "db-bars-x" }, ...MONTHS.map(m => h("span", { style: { flex: 3 } }, m.slice(0, 3))))),
      h("div", { style: { display: "flex", gap: "10px", flexWrap: "wrap", margin: "8px 0" } }, ...CAT_NAMES.map((n, i) => h("span", {}, h("i", { class: `fc${i}`, style: { display: "inline-block", width: "14px", height: "10px", border: "1px solid #999", verticalAlign: "middle", marginRight: "4px" } }), n))),
      head("Mere om arten"),
      h("div", { style: { display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" } }, faenobar(d.periods),
        h("span", {}, `Forekomst af ${d.navn} i Danmark ${d.years[0]}-${d.years[1]}.`)),
      h("ul", { class: "db-md", style: { marginTop: "8px" } },
        on("dofbasen_search") ? h("li", {}, link(`/soeg?periode=antaldage&dage=14&artdata=art&art=${d.artnr}&soeg=1`, `Seneste 14 dages obs. af ${d.navn}`), ` (${fmtN(d.seneste14)})`) : null,
        h("li", {}, link(`/faeno/artkort?art=${d.artnr}`, `${d.navn} på kort`)),
        h("li", {}, h("a", { href: `#/art/${d.artnr}` }, `Artens side i DOF.tool (træk, fænologi og trends)`))));
  }).catch(err => out.replaceChildren(notice(err.message, true)));
  const main2 = h("div", {}, main);
  popupLinks(main2);
  return layout(main2);
}

async function firstobs(q) {
  const year = +q.aar || new Date().getFullYear();
  const out = h("div", {}, loadingBox("Finder førstegangs-iagttagelser …"));
  const yearSel = h("select", { onchange: () => { location.hash = `${BASE}/faeno/firstobs?aar=${yearSel.value}`; } },
    ...Array.from({ length: 20 }, (_, i) => new Date().getFullYear() - i).map(y => h("option", { value: y, selected: y === year }, y)));
  api(`/api/dofbasen/faeno/firstobs?aar=${year}`).then(d => {
    let last = null;
    out.replaceChildren(h("p", {}, `Førstegangs-iagttagelser (${fmtN(d.rows.length)} arter og racer) i ${year} (antal observationer hele året):`),
      h("table", { class: "db-table plain" }, h("tbody", {}, ...d.rows.map(r => {
        const first = r.dato !== last; last = r.dato;
        return h("tr", {}, h("td", { style: { width: "86px" } }, first ? dmySlash(r.dato) + ":" : ""),
          h("td", {}, link(`/faeno/art/${r.artnr}`, r.art, { class: r.klass === "SU" ? "db-su" : r.klass === "SUB" ? "db-subart" : "" }),
            r.klass === "SU" ? " (SU)" : "", ` (${fmtN(r.n)})`, h("span", { class: "muted" }, " – ", link(`/obs/${r.obsid}`, r.lokalitet || "obs."))));
      }))));
  }).catch(err => out.replaceChildren(notice(err.message, true)));
  const main = h("div", {}, head("Fænologi"), h("div", { class: "db-form-row" }, h("label", {}, "År:", yearSel)), out);
  popupLinks(main);
  return layout(main, undefined);
}

async function samedate(q) {
  const d0 = q.dato && /^\d{4}-\d{2}-\d{2}$/.test(q.dato) ? q.dato : new Date().toISOString().slice(0, 10);
  const dt = new Date(d0 + "T12:00:00");
  const out = h("div", {}, loadingBox());
  const input = h("input", { type: "date", value: d0, onchange: () => { setParams({ dato: input.value }); location.hash = `${BASE}/faeno/samedate?dato=${input.value}`; } });
  api(`/api/dofbasen/faeno/samedate?dato=${d0}`).then(d => {
    let last = null;
    out.replaceChildren(h("ul", { class: "db-md" }, h("li", {}, "Sjældne, fåtallige og periodemæssigt usædvanlige fugle der er indtastet på samme dato fra tidligere år (antal observationer):")),
      d.rows.length ? h("table", { class: "db-table plain" }, h("tbody", {}, ...d.rows.map(r => {
        const first = r.aar !== last; last = r.aar;
        return h("tr", {}, h("td", { style: { width: "50px" } }, first ? `${r.aar}:` : ""),
          h("td", {}, link(`/faeno/art/${r.artnr}`, r.art, { class: r.kat === "SU" ? "db-su" : r.kat === "SUB" ? "db-subart" : "db-season" })),
          h("td", {}, `(${fmtN(r.n)})`));
      }))) : h("p", { class: "muted" }, "Ingen."));
  }).catch(err => out.replaceChildren(notice(err.message, true)));
  return layout(h("div", {}, head(`Sjældne og fåtallige fugle den ${dt.getDate()}. ${MONTHS[dt.getMonth()]}`), h("div", { class: "db-form-row" }, h("label", {}, "Dato:", input)), out));
}

async function artkort(q) {
  const st = { art: +q.art || null, aar: +q.aar || new Date().getFullYear(), navn: "" };
  const mapEl = h("div", { class: "db-map tall" });
  const info = h("p", { class: "muted" });
  let map = null, layer = null;
  const yearSel = h("select", { onchange: () => { st.aar = +yearSel.value; draw(); } },
    ...Array.from({ length: 30 }, (_, i) => new Date().getFullYear() - i).map(y => h("option", { value: y, selected: y === st.aar }, y)));
  const draw = async () => {
    setParams({ art: st.art || "", aar: st.aar });
    if (!st.art) { info.textContent = "Vælg en art."; return; }
    info.textContent = "Henter …";
    const d = await api(`/api/dofbasen/faeno/artkort?art=${st.art}&aar=${st.aar}`);
    if (!map) {
      map = L.map(mapEl).setView([56.1, 11.0], 7);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
    }
    layer?.remove();
    layer = L.layerGroup().addTo(map);
    const maxN = Math.max(1, ...d.points.map(p => p.max));
    for (const p of d.points) {
      L.circleMarker([p.lat, p.lon], { radius: 3 + 9 * Math.sqrt(p.max / maxN), color: "#b33", weight: 1, fillColor: "#e55", fillOpacity: .6 }).addTo(layer)
        .bindPopup(`<b>${p.navn.replace(/</g, "&lt;")}</b><br>${p.n} obs., største antal ${p.max}<br><a href="${BASE}/lok/${p.loknr}">Om lokaliteten</a>`);
    }
    info.textContent = `${fmtN(d.points.length)} lokaliteter med arten i ${st.aar}. Cirklens størrelse viser det største antal.`;
  };
  const main = h("div", {}, head("Artsforekomst på kort"),
    h("div", { class: "db-form-row" }, h("label", {}, "Art:", autosuggest({ url: "/api/dofbasen/arter/soeg?q=", placeholder: "Skriv artsnavn", width: "220px", initial: st.art ? String(st.art) : "",
      onPick: (r) => { if (r) { st.art = r.artnr; draw(); } } })), h("label", {}, "År:", yearSel)), info, mapEl);
  requestAnimationFrame(() => { if (st.art) draw(); else info.textContent = "Vælg en art."; });
  return layout(main);
}
