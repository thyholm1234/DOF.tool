// Admin → Tællinger → FiNN-områder: kortet med områder og punkter, redigering (Leaflet-Geoman) og synkronisering med
// DOFbasen (services/finn_admin.py). En hentning fra DOFbasen ændrer intet, før ændringsrapporten er set og anvendt.
import { api, h, fmtN, fmtDate, fmtTime, toast, errorBox, empty, modal, esc } from "../../core.js";
import { baseMap, geoman } from "../../maps.js";
import { BASE } from "./taellinger.js";

const DRAW = { snappable: true, snapDistance: 12, allowSelfIntersection: false };
const GROUPS = [
  ["nye", "Nye", "Findes hos DOFbasen, ikke i portalen", true],
  ["aendrede", "Ændrede", "Ændret hos DOFbasen; portalen har ikke rettet dem – portalen følger med", true],
  ["konflikter", "Konflikter", "Ændret hos DOFbasen, og portalen har også rettet dem", true],
  ["fjernede", "Fjernede", "Væk hos DOFbasen – skjules i appen (slettes ikke)", true],
];
const FIELD = { title: "navn", loknr: "lokalitet", ring: "grænse", site: "område", lat: "position", lon: "position", source: "kilde", type: "type", "genopstået": "tilbage hos DOFbasen" };
const SOURCE = { researcher: "forskere", volunteer: "frivillige" };
const TYPE = { reference: "referencepunkt", management: "forvaltningspunkt" };
const when = (iso) => iso ? `${fmtDate(iso, false)} ${fmtTime(iso)}` : "–";
// Farver: DOFbasens (grøn), rettet i portalen (orange), portalens egne (blå), skjult/fjernet (grå/rød stiplet)
const C = { dof: "#2f7a5d", edited: "#e0673c", portal: "#2a6fb0", off: "#8a8f98", gone: "#c0392b" };
const siteColor = (s) => s.fjernet ? C.gone : !s.aktiv ? C.off : s.origin === "portal" ? C.portal : s.edited ? C.edited : C.dof;
// Ringene er lukkede (første = sidste hjørne, som DOFbasens WKT); Leaflet vil have dem åbne – serveren lukker dem igen
const ringLL = (ring) => {
  const r = ring.map(([a, b]) => [a, b]);
  return r.length > 3 && r[0][0] === r[r.length - 1][0] && r[0][1] === r[r.length - 1][1] ? r.slice(0, -1) : r;
};
const btn = (label, onclick, cls = "btn sm") => h("button", { class: cls, type: "button", onclick }, label);

export async function renderFinn(body) {
  await geoman();
  const syncEl = h("div", {});
  const mapEl = h("div", { class: "map", style: { height: "min(70vh, 640px)" } });
  const side = h("div", { class: "card pulse-side stack" });
  const listEl = h("div", { class: "card mt" });
  body.append(syncEl, h("div", { class: "pulse-layout mt" }, mapEl, side), listEl);

  const map = baseMap(mapEl, { center: [56.1, 10.8], zoom: 7 });
  try { map.pm.setLang("da"); } catch { /* engelsk */ }
  map.pm.setGlobalOptions({ ...DRAW });
  const siteLayer = L.layerGroup().addTo(map), pointLayer = L.layerGroup().addTo(map), overlay = L.layerGroup().addTo(map);
  let D = { sites: [], points: [] }, sel = null, busy = false, showHidden = true, query = "";
  const layers = new Map();

  async function load(keep = true) {
    try { D = await api(`${BASE}/finn`); } catch (err) { side.replaceChildren(errorBox(err)); return; }
    draw();
    drawList();
    if (keep && sel) select(sel.kind, sel.id, false); else drawSide();
  }

  function draw() {
    siteLayer.clearLayers(); pointLayer.clearLayers(); layers.clear();
    for (const s of D.sites) {
      if (!showHidden && (!s.aktiv || s.fjernet)) continue;
      if (s.ring.length < 3) continue;
      const c = siteColor(s);
      const layer = L.polygon(ringLL(s.ring), { color: c, weight: 2, fillColor: c, fillOpacity: 0.18, dashArray: s.aktiv && !s.fjernet ? null : "5,5" })
        .bindTooltip(`${esc(s.title)}${s.taellinger ? ` · ${s.taellinger} tællinger` : ""}`, { sticky: true })
        .on("click", (e) => { L.DomEvent.stopPropagation(e); if (!busy) select("site", s.id, false); }).addTo(siteLayer);
      layers.set(`site:${s.id}`, layer);
    }
    for (const p of D.points) {
      if (!showHidden && (!p.aktiv || p.fjernet)) continue;
      const ref = p.type === "reference";
      const col = !p.aktiv || p.fjernet ? C.off : p.source === "researcher" ? "#e53935" : "#1f7aaf";
      const m = L.circleMarker([p.lat, p.lon], { radius: ref ? 6 : 4, color: col, weight: 2, fillColor: ref ? "#fff" : col, fillOpacity: 1, pmIgnore: true, snapIgnore: true })
        .bindTooltip(`${TYPE[p.type] || p.type} ${p.id}${p.source ? ` (${SOURCE[p.source] || p.source})` : ""}`)
        .on("click", (e) => { L.DomEvent.stopPropagation(e); if (!busy) select("point", p.id, false); }).addTo(pointLayer);
      layers.set(`point:${p.id}`, m);
    }
  }

  // ------------------------------------------------ sidepanel
  function select(kind, id, fit) {
    overlay.clearLayers();
    const item = (kind === "site" ? D.sites : D.points).find(x => x.id === id);
    if (!item) { sel = null; drawSide(); return; }
    sel = { kind, id };
    if (kind === "site") {
      L.polygon(ringLL(item.ring), { color: "#ffb300", weight: 4, fill: false, interactive: false, pmIgnore: true }).addTo(overlay);
      if (fit) map.fitBounds(L.polygon(ringLL(item.ring)).getBounds(), { maxZoom: 15, padding: [20, 20] });
    } else {
      L.circleMarker([item.lat, item.lon], { radius: 12, color: "#ffb300", weight: 3, fill: false, interactive: false, pmIgnore: true }).addTo(overlay);
      if (fit) map.setView([item.lat, item.lon], 15);
    }
    drawSide();
  }

  function drawSide() {
    if (!sel) {
      const n = D.sites.length;
      side.replaceChildren(h("h3", { style: { margin: 0 } }, "FiNN-områder"),
        D.kilde === "dofbasen" ? h("div", { class: "notice" }, "Portalen har endnu ingen FiNN-områder. Hent dem fra DOFbasen ovenfor og anvend dem – derefter kan de rettes her, og appen bruger portalens version.")
          : h("p", { class: "muted", style: { margin: 0 } }, `${fmtN(n)} områder og ${fmtN(D.points.length)} punkter. Klik på et område eller punkt på kortet.`),
        h("div", { class: "muted", style: { fontSize: ".85rem" } },
          ...[["dof", "DOFbasens"], ["edited", "rettet i portalen"], ["portal", "oprettet i portalen"], ["off", "skjult i appen"], ["gone", "fjernet hos DOFbasen"]]
            .map(([k, t]) => h("div", { class: "row", style: { gap: "6px" } }, h("span", { style: { width: "14px", height: "10px", background: C[k], borderRadius: "2px", display: "inline-block" } }), t)),
          h("div", { class: "mt" }, "Punkter: ○ referencepunkt, ● forvaltningspunkt; rød = foreslået af forskere, blå = af frivillige.")),
        D.kilde === "portal" ? h("div", { class: "row wrap", style: { gap: "6px" } }, btn("Nyt område", newSite), btn("Nyt punkt", newPoint)) : null,
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: showHidden, onchange: (e) => { showHidden = e.target.checked; draw(); } }), h("span", {}, "Vis skjulte og fjernede")));
      return;
    }
    return sel.kind === "site" ? siteSide(D.sites.find(s => s.id === sel.id)) : pointSide(D.points.find(p => p.id === sel.id));
  }

  const close = () => { sel = null; overlay.clearLayers(); drawSide(); };
  const tags = (x) => [
    x.origin === "portal" ? h("span", { class: "tag plain" }, "oprettet i portalen") : h("span", { class: "tag local" }, "DOFbasen"),
    x.edited ? h("span", { class: "tag bemaerk" }, "rettet") : null,
    !x.aktiv ? h("span", { class: "tag plain" }, "skjult i appen") : null,
    x.fjernet ? h("span", { class: "tag SU" }, `fjernet hos DOFbasen ${fmtDate(x.fjernet)}`) : null];

  function siteSide(s) {
    const pts = D.points.filter(p => p.site === s.id);
    side.replaceChildren(
      h("div", {}, h("h3", { style: { margin: 0 } }, s.title), h("small", { class: "muted" }, `id ${s.id}${s.loknr ? " · " : ""}`,
        s.loknr ? h("a", { href: `#/lok/${s.loknr}` }, `lokalitet ${s.loknr}`) : null)),
      h("div", { class: "row wrap", style: { gap: "4px" } }, ...tags(s)),
      h("div", { class: "muted", style: { fontSize: ".9rem" } }, `${fmtN(s.taellinger)} FiNN-tællinger${s.seneste ? ` · seneste ${fmtDate(s.seneste)}` : ""} · ${pts.length} punkter · ${s.ring.length} hjørner`),
      s.note ? h("p", { style: { whiteSpace: "pre-wrap", margin: 0 } }, s.note) : null,
      s.afviger && s.dof ? h("div", { class: "notice" }, "Afviger fra DOFbasens version",
        h("small", { style: { display: "block" } }, [s.dof.title !== s.title && `navn hos DOFbasen: «${s.dof.title}»`, s.dof.loknr !== s.loknr && `lokalitet hos DOFbasen: ${s.dof.loknr ?? "ingen"}`,
          JSON.stringify(s.dof.ring) !== JSON.stringify(s.ring) && "grænsen er rettet"].filter(Boolean).join(" · ")),
        h("div", { class: "row wrap mt", style: { gap: "6px" } }, btn("Vis DOFbasens grænse", () => showDof(s), "btn sm ghost"), btn("Gendan DOFbasens", () => revert("sites", s), "btn sm"))) : null,
      D.kilde === "portal" ? h("div", { class: "row wrap", style: { gap: "6px" } },
        btn("Ret grænse", () => editRing(s)), btn("Ret oplysninger", () => editSite(s)),
        btn(s.aktiv ? "Skjul i appen" : "Vis i appen", () => act(() => api(`${BASE}/finn/sites/${s.id}`, { method: "PATCH", body: { aktiv: !s.aktiv } }), s.aktiv ? "Skjult i appen" : "Vises i appen")),
        s.origin === "portal" ? btn("Slet", () => removeSite(s), "btn sm ghost danger") : null) : h("small", { class: "muted" }, "Synkronisér først for at kunne rette."),
      pts.length ? h("details", {}, h("summary", {}, `Punkter i området (${pts.length})`), h("div", { class: "stack mt", style: { gap: "4px" } },
        ...pts.map(p => h("a", { href: "javascript:void 0", onclick: () => select("point", p.id, true) }, `${TYPE[p.type] || p.type} ${p.id}${p.aktiv ? "" : " (skjult)"}`)))) : null,
      h("small", { class: "muted" }, `Synkroniseret ${when(s.synced_at)} · ændret ${when(s.updated_at)}`),
      btn("Luk", close, "btn sm ghost"));
  }

  function pointSide(p) {
    const site = D.sites.find(s => s.id === p.site);
    side.replaceChildren(
      h("div", {}, h("h3", { style: { margin: 0 } }, `${TYPE[p.type] || "Punkt"} ${p.id}`),
        h("small", { class: "muted" }, `${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}${p.source ? ` · foreslået af ${SOURCE[p.source] || p.source}` : ""}`)),
      h("div", { class: "row wrap", style: { gap: "4px" } }, ...tags(p)),
      h("div", {}, site ? ["I ", h("a", { href: "javascript:void 0", onclick: () => select("site", site.id, true) }, site.title)] : h("span", { class: "muted" }, "Ligger ikke i et FiNN-område")),
      p.note ? h("p", { style: { whiteSpace: "pre-wrap", margin: 0 } }, p.note) : null,
      p.afviger && p.dof ? h("div", { class: "notice" }, "Afviger fra DOFbasens version",
        h("div", { class: "row wrap mt", style: { gap: "6px" } }, btn("Gendan DOFbasens", () => revert("points", p), "btn sm"))) : null,
      D.kilde === "portal" ? h("div", { class: "row wrap", style: { gap: "6px" } },
        btn("Flyt", () => movePoint(p)), btn("Ret oplysninger", () => editPoint(p)),
        btn(p.aktiv ? "Skjul i appen" : "Vis i appen", () => act(() => api(`${BASE}/finn/points/${p.id}`, { method: "PATCH", body: { aktiv: !p.aktiv } }), p.aktiv ? "Skjult" : "Vises")),
        p.origin === "portal" ? btn("Slet", async () => { if (confirm(`Slet punkt ${p.id}?`)) { await act(() => api(`${BASE}/finn/points/${p.id}`, { method: "DELETE" }), "Slettet"); close(); } }, "btn sm ghost danger") : null) : null,
      btn("Luk", close, "btn sm ghost"));
  }

  // ------------------------------------------------ handlinger
  async function act(fn, ok) {
    busy = true;
    try { await fn(); toast(ok); await load(); }
    catch (err) { toast(err.message, "err"); }
    finally { busy = false; }
  }

  function editBar(text, onSave, onCancel) {
    const bar = h("div", { class: "notice" }, text, h("div", { class: "row mt", style: { gap: "6px" } },
      btn("Gem", async () => { bar.remove(); await onSave(); }, "btn sm primary"), btn("Annullér", () => { bar.remove(); onCancel(); }, "btn sm ghost")));
    side.prepend(bar);
  }

  function editRing(s) {
    busy = true;
    overlay.clearLayers();
    const layer = L.polygon(ringLL(s.ring), { color: "#e0673c", weight: 3, fillOpacity: 0.15 }).addTo(map);
    layers.get(`site:${s.id}`)?.remove();
    map.fitBounds(layer.getBounds(), { maxZoom: 16, padding: [20, 20] });
    layer.pm.enable({ ...DRAW });
    editBar("Træk i hjørnerne. Klik på en kant for at tilføje et hjørne, højreklik på et hjørne for at fjerne det.", async () => {
      layer.pm.disable();
      const ring = layer.getLatLngs()[0].map(ll => [ll.lat, ll.lng]);
      layer.remove(); busy = false;
      await act(() => api(`${BASE}/finn/sites/${s.id}`, { method: "PATCH", body: { ring } }), "Grænsen er gemt");
    }, () => { layer.pm.disable(); layer.remove(); busy = false; draw(); select("site", s.id, false); });
  }

  function showDof(s) {
    overlay.clearLayers();
    L.polygon(ringLL(s.dof.ring), { color: "#7b4bb7", weight: 2, dashArray: "6,4", fillOpacity: 0, interactive: false, pmIgnore: true }).bindTooltip("DOFbasens grænse").addTo(overlay);
    toast("DOFbasens grænse er vist stiplet (lilla)");
  }

  async function revert(kind, x) {
    if (!confirm("Gendan DOFbasens version? Portalens rettelser af navn, lokalitet og grænse/position forsvinder.")) return;
    await act(() => api(`${BASE}/finn/${kind}/${x.id}/revert`, { method: "POST" }), "DOFbasens version er gendannet");
  }

  function siteForm(title, s, onSubmit) {
    const name = h("input", { type: "text", maxlength: 200, required: true, value: s?.title || "" });
    const loknr = h("input", { type: "number", value: s?.loknr ?? "", placeholder: "loknr (tom = ingen)" });
    const note = h("textarea", { rows: 3, maxlength: 2000 }, s?.note || "");
    const m = modal(title, h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault(); m.close();
      await onSubmit({ title: name.value.trim(), loknr: loknr.value ? Number(loknr.value) : null, note: note.value.trim() || null });
    } }, h("label", { class: "field" }, h("span", {}, "Navn"), name), h("label", { class: "field" }, h("span", {}, "Lokalitet i DOFbasen (loknr)"), loknr),
      h("label", { class: "field" }, h("span", {}, "Note (kun i admin)"), note), h("button", { class: "btn primary" }, "Gem")));
    name.focus();
  }

  const editSite = (s) => siteForm("Ret FiNN-område", s, (b) => act(() => api(`${BASE}/finn/sites/${s.id}`, { method: "PATCH", body: b }), "Gemt"));

  function newSite() {
    toast("Klik hjørnerne rundt på kortet; klik på det første for at afslutte (Esc annullerer)");
    busy = true;
    const done = () => { map.off("pm:create", created); map.off("pm:drawend", ended); busy = false; };
    const created = (e) => {
      const ring = e.layer.getLatLngs()[0].map(ll => [ll.lat, ll.lng]);
      e.layer.remove(); done();
      siteForm("Nyt FiNN-område", null, async (b) => {
        busy = true;
        try { const s = await api(`${BASE}/finn/sites`, { method: "POST", body: { ...b, ring } }); toast("Området er oprettet"); await load(false); select("site", s.id, false); }
        catch (err) { toast(err.message, "err"); } finally { busy = false; }
      });
    };
    const ended = () => setTimeout(() => { if (busy) done(); }, 0);
    map.on("pm:create", created); map.on("pm:drawend", ended);
    map.pm.enableDraw("Polygon", { ...DRAW });
  }

  function pointForm(title, p, onSubmit) {
    const type = h("select", {}, ...Object.entries(TYPE).map(([k, t]) => h("option", { value: k, selected: (p?.type || "reference") === k }, t)));
    const source = h("select", {}, ...Object.entries(SOURCE).map(([k, t]) => h("option", { value: k, selected: (p?.source || "volunteer") === k }, `foreslået af ${t}`)));
    const note = h("textarea", { rows: 3, maxlength: 2000 }, p?.note || "");
    const m = modal(title, h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault(); m.close();
      await onSubmit({ type: type.value, source: source.value, note: note.value.trim() || null });
    } }, h("label", { class: "field" }, h("span", {}, "Type"), type), h("label", { class: "field" }, h("span", {}, "Kilde"), source),
      h("label", { class: "field" }, h("span", {}, "Note (kun i admin)"), note), h("button", { class: "btn primary" }, "Gem")));
  }

  const editPoint = (p) => pointForm(`Ret punkt ${p.id}`, p, (b) => act(() => api(`${BASE}/finn/points/${p.id}`, { method: "PATCH", body: b }), "Gemt"));

  function newPoint() {
    toast("Klik på kortet, hvor punktet skal ligge");
    busy = true; mapEl.style.cursor = "crosshair";
    map.once("click", (e) => {
      mapEl.style.cursor = ""; busy = false;
      pointForm("Nyt FiNN-punkt", null, async (b) => {
        busy = true;
        try { const p = await api(`${BASE}/finn/points`, { method: "POST", body: { ...b, lat: e.latlng.lat, lon: e.latlng.lng } }); toast("Punktet er oprettet"); await load(false); select("point", p.id, false); }
        catch (err) { toast(err.message, "err"); } finally { busy = false; }
      });
    });
  }

  function movePoint(p) {
    busy = true;
    const m = L.marker([p.lat, p.lon], { draggable: true, pmIgnore: true }).addTo(map);
    map.setView([p.lat, p.lon], Math.max(map.getZoom(), 15));
    editBar("Træk markøren til punktets nye placering. Området findes ud fra positionen.", async () => {
      const ll = m.getLatLng(); m.remove(); busy = false;
      await act(() => api(`${BASE}/finn/points/${p.id}`, { method: "PATCH", body: { lat: ll.lat, lon: ll.lng } }), "Punktet er flyttet");
    }, () => { m.remove(); busy = false; drawSide(); });
  }

  async function removeSite(s) {
    if (!confirm(`Slet ${s.title}? Kun portalens egne områder uden tællinger kan slettes.`)) return;
    await act(() => api(`${BASE}/finn/sites/${s.id}`, { method: "DELETE" }), "Slettet");
    close();
  }

  // ------------------------------------------------ listen under kortet
  function drawList() {
    const q = h("input", { type: "search", placeholder: "Søg område …", value: query, style: { width: "min(260px, 100%)" },
      oninput: (e) => { query = e.target.value; fill(); } });
    const tbody = h("tbody", {});
    const fill = () => {
      const rows = D.sites.filter(s => !query || s.title.toLowerCase().includes(query.toLowerCase()) || String(s.id) === query.trim());
      tbody.replaceChildren(...rows.map(s => h("tr", { class: "click", onclick: () => { select("site", s.id, true); mapEl.scrollIntoView({ behavior: "smooth", block: "center" }); } },
        h("td", {}, h("span", { style: { display: "inline-block", width: "10px", height: "10px", borderRadius: "2px", background: siteColor(s), marginRight: "6px" } }), s.title,
          h("small", { class: "muted" }, ` ${s.id}`)),
        h("td", {}, ...tags(s).filter(Boolean).slice(1)),
        h("td", { style: { textAlign: "right" } }, fmtN(s.taellinger)),
        h("td", { style: { textAlign: "right" } }, s.seneste ? fmtDate(s.seneste) : "–"))));
    };
    fill();
    listEl.replaceChildren(h("div", { class: "row between wrap mb" }, h("h3", { style: { margin: 0 } }, `Områder (${fmtN(D.sites.length)})`), q),
      D.sites.length ? h("div", { class: "table-wrap", style: { maxHeight: "480px", overflow: "auto" } }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Område"), h("th", {}, "Status"), h("th", { style: { textAlign: "right" } }, "FiNN-tællinger"), h("th", { style: { textAlign: "right" } }, "Seneste"))),
        tbody)) : empty("Ingen områder endnu", "Hent dem fra DOFbasen ovenfor."));
  }

  // ------------------------------------------------ synkronisering
  async function loadSyncs() {
    let rows;
    try { rows = await api(`${BASE}/finn/syncs`); } catch (err) { syncEl.replaceChildren(errorBox(err)); return; }
    const open = rows.find(r => r.status === "klar");
    const last = rows.find(r => r.status === "anvendt");
    const fetchBtn = btn("Hent fra DOFbasen", async () => {
      fetchBtn.disabled = true;
      try { await api(`${BASE}/finn/syncs`, { method: "POST" }); await loadSyncs(); }
      catch (err) { toast(err.message, "err"); fetchBtn.disabled = false; }
    }, "btn primary");
    syncEl.replaceChildren(h("div", { class: "card stack" },
      h("div", { class: "row between wrap" },
        h("div", {}, h("h3", { style: { margin: 0 } }, "Synkronisering med DOFbasen"),
          h("small", { class: "muted" }, last ? `Sidst anvendt ${when(last.applied_at)}` : "Endnu ikke synkroniseret – appen henter FiNN-områderne direkte fra DOFbasen")),
        fetchBtn),
      open ? report(open) : h("small", { class: "muted" }, "Hentningen viser, hvad DOFbasen har ændret. Intet ændres i portalen, før du har valgt og trykket Anvend."),
      rows.some(r => r.status === "anvendt") ? h("details", {}, h("summary", {}, "Tidligere synkroniseringer"),
        h("div", { class: "stack mt", style: { gap: "4px" } }, ...rows.filter(r => r.status === "anvendt").map(r => h("small", {}, `${when(r.applied_at)} · `,
          ["sites", "points"].map(k => `${k === "sites" ? "områder" : "punkter"}: ${Object.entries(r.applied?.[k] || {}).map(([g, n]) => `${g} ${n}`).join(", ") || "intet"}`).join(" · "))))) : null));
  }

  function report(s) {
    const r = s.report, a = r.antal;
    const checks = Object.fromEntries(GROUPS.map(([k, , , on]) => [k, h("input", { type: "checkbox", checked: on })]));
    const conflict = h("select", { style: { width: "auto" } },
      h("option", { value: "portal" }, "Behold portalens rettelser (DOFbasens version gemmes til sammenligning)"),
      h("option", { value: "dofbasen" }, "Brug DOFbasens version"));
    const total = (k) => (a.sites[k] || 0) + (a.points[k] || 0);
    const nothing = GROUPS.every(([k]) => !total(k));
    const examples = (k) => {
      const rows = [...r.sites[k].map(x => ({ ...x, kind: "område" })), ...r.points[k].map(x => ({ ...x, kind: "punkt" }))];
      return rows.length ? h("details", {}, h("summary", { class: "muted" }, "Se dem"), h("div", { class: "stack mt", style: { gap: "2px", fontSize: ".85rem" } },
        ...rows.map(x => h("span", {}, `${x.title} (${x.kind} ${x.id})${x.felter ? " – " + x.felter.map(f => FIELD[f] || f).filter((v, i, arr) => arr.indexOf(v) === i).join(", ") : ""}`)),
        (a.sites[k] + a.points[k]) > rows.length ? h("small", { class: "muted" }, `… og ${fmtN(a.sites[k] + a.points[k] - rows.length)} mere`) : null)) : null;
    };
    const apply = btn("Anvend", async () => {
      const groups = GROUPS.map(([k]) => k).filter(k => checks[k].checked && total(k));
      if (!groups.length) { toast("Vælg mindst én gruppe med ændringer"); return; }
      apply.disabled = true;
      try { await api(`${BASE}/finn/syncs/${s.id}/apply`, { method: "POST", body: { groups, conflicts: conflict.value } }); toast("Synkroniseringen er anvendt"); await loadSyncs(); await load(); }
      catch (err) { toast(err.message, "err"); apply.disabled = false; }
    }, "btn primary");
    return h("div", { class: "card stack", style: { background: "var(--bg-2)" } },
      h("div", { class: "row between wrap" }, h("b", {}, `Hentet ${when(s.created_at)}: ${fmtN(r.hentet.sites)} områder og ${fmtN(r.hentet.points)} punkter hos DOFbasen`),
        btn("Forkast", async () => { await api(`${BASE}/finn/syncs/${s.id}`, { method: "DELETE" }).catch(() => {}); loadSyncs(); }, "btn sm ghost")),
      nothing ? h("div", { class: "notice" }, "Ingen ændringer – portalen svarer til DOFbasen.") : null,
      ...GROUPS.filter(([k]) => total(k)).map(([k, label, help]) => h("div", {},
        h("label", { class: "row", style: { gap: "8px", alignItems: "flex-start" } }, checks[k],
          h("span", {}, h("b", {}, `${label}: ${fmtN(a.sites[k])} områder, ${fmtN(a.points[k])} punkter`), h("small", { class: "muted", style: { display: "block" } }, help))),
        examples(k))),
      total("konflikter") ? h("label", { class: "field" }, h("span", {}, "Konflikter"), conflict) : null,
      nothing ? null : h("div", {}, apply));
  }

  await Promise.all([load(false), loadSyncs()]);
  return () => map.remove();
}
