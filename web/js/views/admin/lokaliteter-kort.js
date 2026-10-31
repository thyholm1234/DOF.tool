// Admin → Lokaliteter → Kort: se og ret lokaliteternes grænser (services/location_edit.py).
// Grænserne hentes pr. kortudsnit, forenklet til zoom; den valgte lokalitet hentes i fuld opløsning, så det er den rigtige
// form, der rettes. Redigering med Leaflet-Geoman: hjørner fastgøres til nabogrænser (snapping), selvskæring er ikke tilladt.
import { api, h, fmtN, fmtDate, fmtTime, toast, errorBox, modal, esc } from "../../core.js";
import { baseMap, cssVar, geoman } from "../../maps.js";

const BASE = "/api/admin/locations";
const DRAW = { snappable: true, snapDistance: 15, allowSelfIntersection: false, templineStyle: { color: "#e0673c" }, hintlineStyle: { color: "#e0673c", dashArray: "5,5" } };
const when = (iso) => iso ? `${fmtDate(iso, false)} ${fmtTime(iso)}` : "–";
const SOURCE = { dofbasen: "DOFbasen", portal: "rettet i portalen" };

export async function renderLocationMap(body) {
  await geoman();  // før kortet oprettes – Geoman kobler sig på nye kort
  const mapEl = h("div", { class: "map full" });
  const side = h("div", { class: "card pulse-side stack" });
  const info = h("small", { class: "muted" });
  const search = h("input", { type: "search", placeholder: "Søg lokalitet …", style: { width: "min(320px, 100%)" } });
  const hits = h("div", { class: "menu-list", style: { position: "absolute", zIndex: 1000, background: "var(--surface)", boxShadow: "var(--shadow)", borderRadius: "8px", maxWidth: "360px" } });
  const newBtn = h("button", { class: "btn sm", type: "button", onclick: () => drawNew(null) }, "Ny lokalitet");
  body.append(
    h("div", { class: "row wrap", style: { gap: "8px", marginBottom: "8px", position: "relative" } }, h("div", { style: { position: "relative" } }, search, hits), newBtn, info),
    h("div", { class: "pulse-layout" }, mapEl, side));

  const map = baseMap(mapEl, { center: [56.17, 9.38], zoom: 12 });
  try { map.pm.setLang("da"); } catch { /* ingen dansk oversættelse – engelsk */ }
  map.pm.setGlobalOptions({ ...DRAW });
  const canvas = L.canvas({ padding: 0.3 });
  const shapes = L.geoJSON(null, { renderer: canvas, style: (f) => style(f.properties), onEachFeature: (f, layer) => {
    layer.bindTooltip(esc(f.properties.navn), { sticky: true });
    layer.on("click", (e) => { L.DomEvent.stopPropagation(e); onPick(f.properties.loknr); });
  } }).addTo(map);
  const points = L.layerGroup().addTo(map);
  const overlay = L.layerGroup().addTo(map);  // tidligere version vist stiplet
  let selected = null;   // { d: detalje, layer }
  let mode = null;       // "merge" mens der vælges, hvem der skal lægges sammen med
  let busy = false;

  function style(p) {
    const portal = p.source === "portal" || p.origin === "portal";
    return { color: portal ? cssVar("--accent") : cssVar("--brand"), weight: 1.5, fillOpacity: 0.08, dashArray: p.parent_loknr ? "4,4" : null };
  }

  // ------------------------------------------------ data pr. kortudsnit
  let seq = 0, timer = null;
  async function load() {
    const my = ++seq;
    const b = map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(v => v.toFixed(5)).join(",");
    let d;
    try { d = await api(`${BASE}/map?bbox=${bbox}&zoom=${map.getZoom()}`); } catch (err) { info.textContent = err.message; return; }
    if (my !== seq) return;
    shapes.clearLayers(); points.clearLayers();
    if (d.zoom_ind) { info.textContent = "Zoom ind for at se lokaliteternes grænser."; return; }
    const sel = selected?.d.loknr;
    shapes.addData({ ...d, features: d.features.filter(f => f.geometry.type !== "Point" && f.properties.loknr !== sel) });
    for (const f of d.features.filter(f => f.geometry.type === "Point")) {
      const [lon, lat] = f.geometry.coordinates;
      L.circleMarker([lat, lon], { radius: 4, color: cssVar("--text-3"), fillOpacity: 0.6, pmIgnore: true, snapIgnore: true })
        .bindTooltip(`${esc(f.properties.navn)} (uden grænse)`).on("click", () => onPick(f.properties.loknr)).addTo(points);
    }
    const n = d.features.length;
    info.textContent = `${fmtN(n)} lokaliteter i udsnittet${d.afkortet ? " (de største – zoom ind for alle)" : ""}`;
  }
  map.on("moveend", () => { clearTimeout(timer); timer = setTimeout(load, 250); });

  // ------------------------------------------------ søgning
  let sTimer = null;
  search.addEventListener("input", () => {
    clearTimeout(sTimer);
    const q = search.value.trim();
    if (q.length < 2) { hits.replaceChildren(); return; }
    sTimer = setTimeout(async () => {
      const rows = await api(`/api/locations?q=${encodeURIComponent(q)}`).catch(() => []);
      hits.replaceChildren(...rows.slice(0, 12).map(r => h("a", { href: "javascript:void 0", style: { display: "block", padding: "6px 10px" },
        onclick: () => { hits.replaceChildren(); search.value = r.navn; select(r.loknr, true); } }, r.navn, h("small", { class: "muted" }, ` ${r.loknr}${r.kommune ? " · " + r.kommune : ""}`))));
    }, 200);
  });

  // ------------------------------------------------ valg og sidepanel
  async function onPick(loknr) {
    if (busy) return;
    if (mode === "merge" && selected) {
      mode = null; mapEl.style.cursor = "";
      if (loknr === selected.d.loknr) return;
      if (!confirm(`Læg ${loknr} sammen med ${selected.d.navn}? ${loknr} peger derefter videre til ${selected.d.loknr}, og grænserne forenes.`)) return;
      return act(() => api(`${BASE}/${selected.d.loknr}/merge`, { method: "POST", body: { other: loknr, base_id: selected.d.graense?.id ?? null } }), "Lagt sammen");
    }
    select(loknr, false);
  }

  async function select(loknr, fit) {
    let d;
    try { d = await api(`${BASE}/${loknr}`); } catch (err) { toast(err.message, "err"); return; }
    if (selected?.layer) map.removeLayer(selected.layer);
    overlay.clearLayers();
    const layer = d.graense ? L.geoJSON(d.graense.geojson, { style: { color: "#e0673c", weight: 3, fillOpacity: 0.15 } }).getLayers()[0] : null;
    if (layer) layer.addTo(map);
    selected = { d, layer };
    if (fit) { if (layer) map.fitBounds(layer.getBounds(), { maxZoom: 15 }); else if (d.lat) map.setView([d.lat, d.lon], 15); }
    drawSide();
    load();
  }

  const btn = (label, onclick, cls = "btn sm") => h("button", { class: cls, type: "button", onclick }, label);

  function drawSide() {
    if (!selected) {
      side.replaceChildren(h("h3", {}, "Lokaliteter"), h("p", { class: "muted" }, "Klik på en lokalitet på kortet eller søg efter den. ",
        "Orange = valgt, grøn = DOFbasens grænse, gul = rettet eller oprettet i portalen, stiplet = ligger inden i en anden."));
      return;
    }
    const { d } = selected;
    const g = d.graense;
    const det = d.detaljer || {};
    side.replaceChildren(
      h("div", {}, h("h3", { style: { margin: 0 } }, d.navn),
        h("small", { class: "muted" }, h("a", { href: `#/lok/${d.loknr}` }, d.loknr), d.origin === "portal" ? " · oprettet i portalen" : " · DOFbasen",
          d.kommune ? ` · ${d.kommune}` : "")),
      d.parent_loknr ? h("div", {}, "Ligger i ", h("a", { href: "javascript:void 0", onclick: () => select(d.parent_loknr, true) }, d.parent_navn || d.parent_loknr)) : null,
      h("div", { class: "muted", style: { fontSize: ".9rem" } },
        g ? `${fmtN(g.ha)} ha · ${fmtN(g.punkter)} hjørnepunkter · ${SOURCE[g.source] || g.source}` : "Ingen grænse",
        ` · ${fmtN(d.observationer)} observationer`),
      det.typer_tekst ? h("div", {}, h("b", {}, "Type: "), det.typer_tekst) : null,
      det.beskrivelse ? h("details", {}, h("summary", {}, "Beskrivelse"), h("p", { style: { whiteSpace: "pre-wrap" } }, det.beskrivelse)) : null,
      ...(d.advarsler || []).map(w => h("div", { class: "notice" }, w.tekst,
        w.type === "overlap" ? h("div", { class: "mt" }, ...w.naboer.slice(0, 8).map(n => h("div", {}, `${n.navn} (${Math.round(n.andel * 100)} %)`)),
          btn("Fjern overlap fra naboerne", () => trim(w.naboer.map(n => n.loknr)))) : null)),
      h("div", { class: "row wrap", style: { gap: "6px" } },
        g ? btn("Ret grænse", editShape) : btn("Tegn grænse", () => drawShape()),
        g ? btn("Opret inden i", () => drawNew(d.loknr)) : null,
        g ? btn("Del op", splitShape) : null,
        btn("Læg sammen med …", () => { mode = "merge"; mapEl.style.cursor = "crosshair"; toast("Klik på den lokalitet, der skal lægges sammen med denne"); }),
        btn("Ret oplysninger", editInfo),
        d.kan_slettes ? btn("Slet", remove, "btn sm ghost") : null),
      d.boern.length ? h("div", {}, h("b", {}, "Inden i: "), ...d.boern.map((c, i) => [i ? ", " : "", h("a", { href: "javascript:void 0", onclick: () => select(c.loknr, true) }, c.navn)])) : null,
      h("details", {}, h("summary", {}, `Versioner (${d.versioner.length})`),
        h("div", { class: "stack mt" }, ...d.versioner.map(v => h("div", { class: "row between", style: { gap: "6px" } },
          h("small", {}, `${when(v.valid_from)} · ${SOURCE[v.source] || v.source} · ${fmtN(v.punkter)} pkt${v.note ? " · " + v.note : ""}${v.valid_to ? "" : " · gældende"}`),
          h("span", { class: "row", style: { gap: "4px" } },
            btn("Vis", () => showVersion(v.id), "btn sm ghost"),
            v.valid_to ? btn("Gendan", () => restore(v.id), "btn sm ghost") : null))))),
      d.log.length ? h("details", {}, h("summary", {}, "Ændringer"),
        h("div", { class: "stack mt" }, ...d.log.map(e => h("small", {}, `${when(e.created_at)} · ${e.action}${e.note ? " · " + e.note : ""}`)))) : null,
      btn("Luk", () => { if (selected?.layer) map.removeLayer(selected.layer); selected = null; overlay.clearLayers(); drawSide(); load(); }, "btn sm ghost"));
  }

  // ------------------------------------------------ handlinger
  async function act(fn, ok) {
    busy = true;
    try {
      const r = await fn();
      toast(ok);
      const w = r?.advarsler?.length ? r.advarsler.map(x => x.tekst).join(" · ") : "";
      if (w) toast(w);
      await select(r?.loknr || selected.d.loknr, !!r?.loknr);
      return r;
    } catch (err) {
      toast(err.message, "err");
      if (err.status === 409 && selected) await select(selected.d.loknr, false);
    } finally { busy = false; }
  }

  function editBar(onSave, onCancel) {
    const bar = h("div", { class: "row", style: { gap: "6px" } },
      btn("Gem", async () => { bar.remove(); await onSave(); }, "btn sm primary"),
      btn("Annullér", () => { bar.remove(); onCancel(); }, "btn sm ghost"));
    side.prepend(h("div", { class: "notice" }, "Træk i hjørnerne; de fastgøres til nabogrænser. Klik på en kant for at tilføje et hjørne, højreklik på et hjørne for at fjerne det.", bar));
  }

  function editShape() {
    const { layer, d } = selected;
    if (!layer) return;
    busy = true;
    layer.pm.enable({ ...DRAW });
    editBar(async () => {
      layer.pm.disable(); busy = false;
      await act(() => api(`${BASE}/${d.loknr}/shape`, { method: "PUT", body: { geojson: layer.toGeoJSON().geometry, base_id: d.graense.id } }), "Grænsen er gemt");
    }, () => { layer.pm.disable(); busy = false; select(d.loknr, false); });
  }

  /** Tegn et polygon eller en linje; resolve med GeoJSON-geometrien (eller null ved Esc/annullér). */
  function draw(kind, hint) {
    return new Promise((resolve) => {
      toast(hint);
      busy = true;
      const esc = (e) => { if (e.key === "Escape") map.pm.disableDraw(); };
      document.addEventListener("keydown", esc);
      const done = (g) => { map.off("pm:create", created); map.off("pm:drawend", ended); document.removeEventListener("keydown", esc); busy = false; resolve(g); };
      const created = (e) => { const g = e.layer.toGeoJSON().geometry; e.layer.remove(); done(g); };
      const ended = () => setTimeout(() => { if (busy) done(null); }, 0);
      map.on("pm:create", created);
      map.on("pm:drawend", ended);
      map.pm.enableDraw(kind, { ...DRAW, finishOn: kind === "Line" ? "dblclick" : null });
    });
  }

  async function drawShape() {
    const g = await draw("Polygon", "Klik hjørnerne rundt; klik på det første for at afslutte");
    if (g) await act(() => api(`${BASE}/${selected.d.loknr}/shape`, { method: "PUT", body: { geojson: g, base_id: null } }), "Grænsen er gemt");
  }

  async function drawNew(parent) {
    const g = await draw("Polygon", parent ? "Tegn den nye lokalitet inden i den valgte (det, der stikker udenfor, klippes væk)" : "Tegn den nye lokalitet");
    if (!g) return;
    const navn = h("input", { type: "text", maxlength: 250, required: true });
    const typer = h("input", { type: "text", maxlength: 300, placeholder: "fx Sø/Permanent vandhul/mose" });
    const besk = h("textarea", { rows: 4, maxlength: 20000 });
    const m = modal(parent ? "Ny lokalitet inden i " + selected.d.navn : "Ny lokalitet", h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault(); m.close();
      await act(() => api(BASE, { method: "POST", body: { navn: navn.value, geojson: g, parent_loknr: parent, typer_tekst: typer.value || null, beskrivelse: besk.value || null } }), "Lokaliteten er oprettet");
    } }, h("label", { class: "field" }, h("span", {}, "Navn"), navn), h("label", { class: "field" }, h("span", {}, "Type(r)"), typer),
      h("label", { class: "field" }, h("span", {}, "Beskrivelse"), besk), h("button", { class: "btn primary" }, "Opret")));
    navn.focus();
  }

  async function splitShape() {
    const { d } = selected;
    const line = await draw("Line", "Tegn en linje tværs over lokaliteten; dobbeltklik for at afslutte");
    if (!line) return;
    const names = prompt("Navne på de nye dele, adskilt af komma (tomt = automatiske navne). Den oprindelige bliver forælder og beholder sine observationer.", "");
    if (names === null) return;
    await act(() => api(`${BASE}/${d.loknr}/split`, { method: "POST", body: { line, names: names.split(",").map(x => x.trim()).filter(Boolean), base_id: d.graense.id } }), "Lokaliteten er delt");
  }

  function editInfo() {
    const { d } = selected;
    const det = d.detaljer || {};
    const navn = h("input", { type: "text", value: d.navn, maxlength: 250, disabled: d.origin !== "portal" });
    const typer = h("input", { type: "text", value: det.typer_tekst || "", maxlength: 300 });
    const besk = h("textarea", { rows: 6, maxlength: 20000 }, det.beskrivelse || "");
    const parent = h("input", { type: "number", value: d.parent_loknr ?? "", placeholder: "loknr (tom = ingen)" });
    const m = modal("Ret oplysninger", h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault(); m.close();
      const body = { typer_tekst: typer.value, beskrivelse: besk.value, parent_loknr: parent.value ? Number(parent.value) : null };
      if (d.origin === "portal") body.navn = navn.value;
      await act(() => api(`${BASE}/${d.loknr}`, { method: "PATCH", body }), "Gemt");
    } }, h("label", { class: "field" }, h("span", {}, d.origin === "portal" ? "Navn" : "Navn (følger DOFbasen)"), navn),
      h("label", { class: "field" }, h("span", {}, "Type(r)"), typer), h("label", { class: "field" }, h("span", {}, "Beskrivelse"), besk),
      h("label", { class: "field" }, h("span", {}, "Ligger inden i (loknr)"), parent), h("button", { class: "btn primary" }, "Gem")), { wide: true });
  }

  async function showVersion(id) {
    overlay.clearLayers();
    const g = await api(`${BASE}/${selected.d.loknr}/shapes/${id}`).catch((err) => { toast(err.message, "err"); return null; });
    if (g) L.geoJSON(g, { style: { color: "#7b4bb7", weight: 2, dashArray: "6,4", fillOpacity: 0 }, pmIgnore: true }).addTo(overlay);
  }

  async function restore(id) {
    if (!confirm("Gendan denne version? Den bliver en ny version – intet slettes.")) return;
    await act(() => api(`${BASE}/${selected.d.loknr}/revert`, { method: "POST", body: { shape_id: id, base_id: selected.d.graense?.id ?? null } }), "Versionen er gendannet");
  }

  async function trim(loknrs) {
    if (!confirm("Skær denne lokalitets areal ud af naboerne? Hver nabo får en ny version, som kan gendannes.")) return;
    await act(() => api(`${BASE}/${selected.d.loknr}/trim-neighbours`, { method: "POST", body: { loknrs } }), "Overlap fjernet");
  }

  async function remove() {
    if (!confirm(`Slet ${selected.d.navn}? Det kan kun gøres for portalens egne lokaliteter uden observationer.`)) return;
    busy = true;
    try { await api(`${BASE}/${selected.d.loknr}`, { method: "DELETE" }); toast("Slettet"); if (selected.layer) map.removeLayer(selected.layer); selected = null; drawSide(); load(); }
    catch (err) { toast(err.message, "err"); } finally { busy = false; }
  }

  drawSide();
  load();
  return () => map.remove();
}
