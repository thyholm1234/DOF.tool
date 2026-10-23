// Tur: kun denne turs observationer. Her tilføjes og rettes observationer (+1 / +10 på hver), og ynglepar.
// Selve turens oplysninger redigeres på sin egen side (tur-form.js).
import { h, icon, fmtN, fmtDate, tag, toast, store, KAT } from "../core.js";
import { baseMap } from "../maps.js";
import { tracker, trackStart, trackPause, trackStop, trackLength } from "../tur-data.js";
import { isApp } from "../native.js";
import { openObsSheet, openPairSheet } from "../obs-sheet.js";

const STATE = { ok: ["Gemt", "ok"], gemmer: ["Gemmer …", "busy"], offline: ["Ikke sendt – sendes, når der er net", "off"], fejl: ["Kunne ikke gemmes", "err"] };

export function view(page, ref, store_, recent) {
  const { T, files } = store_;
  const sp = (artnr) => ref.species.find(s => s.artnr === artnr);
  const adfText = (code) => ref.codes.adf.find(a => a.code === code)?.text;
  const reuse = { on: !!store("turreuse") };

  // --- status for lagring
  const chip = h("span", { class: "sync-chip" });
  const drawChip = () => { const [t, k] = STATE[store_.status] || STATE.ok; chip.className = `sync-chip ${k}`; chip.textContent = t; chip.title = store_.message || ""; };
  drawChip();
  const unsub = store_.subscribe(() => { drawChip(); drawRows(); });

  // --- observationer
  const obsList = h("div", { class: "obs-list" });
  const countEl = h("small", { class: "muted" });
  const lastAdf = () => T.observations.at(-1)?.adfkode || "";
  const rowState = (o) => !o.id || store_.status !== "ok" ? h("span", { class: "obs-state off", title: "Ikke gemt på serveren endnu", html: icon("refresh") })
    : h("span", { class: "obs-state ok", title: "Gemt", html: icon("check") });
  const row = (o) => {
    const num = h("b", { class: "num" }, fmtN(o.antal));
    const bump = (d) => () => { o.antal = (Number(o.antal) || 0) + d; num.textContent = fmtN(o.antal); store_.save(); };
    return h("div", { class: `obs-row ${o.klass === "SU" ? "SU" : o.klass === "SUB" ? "SUB" : ""}` },
      h("button", { type: "button", class: "obs-main", onclick: () => edit(o) },
        h("span", { class: "obs-title" }, h("b", {}, o.art || sp(o.artnr)?.navn), o.klass && o.klass !== "Alm" ? tag(o.klass, KAT[o.klass] || o.klass) : null, o.hemmelig ? h("span", { class: "tag plain", html: icon("lock") }) : null),
        h("small", { class: "muted" }, [adfText(o.adfkode), o.obstidfra, (o._media || files[o.client_id]?.length) ? "📷" : null].filter(Boolean).join(" · "))),
      num,
      h("button", { type: "button", class: "btn sm accent", onclick: bump(1) }, "+1"),
      h("button", { type: "button", class: "btn sm", onclick: bump(10) }, "+10"),
      rowState(o));
  };
  const drawRows = () => {
    obsList.replaceChildren(...(T.observations.length ? [...T.observations].reverse().map(row) : [h("p", { class: "muted", style: { margin: 0 } }, "Ingen observationer endnu – tryk på + Observation.")]));
    countEl.textContent = `${T.observations.length} observation${T.observations.length === 1 ? "" : "er"} · ${new Set(T.observations.map(o => o.artnr)).size} arter`;
    drawMap();
  };

  const edit = (o = null) => openObsSheet({
    ref, recent, T, obs: o, files: o ? files[o.client_id] || [] : [], adfDefault: reuse.on ? lastAdf() : "", guideDefault: T.observations.some(x => x.retningslinjer),
    onSave: (O, newFiles) => {
      const i = o ? T.observations.findIndex(x => x.client_id === o.client_id) : -1;
      if (i >= 0) T.observations[i] = O; else T.observations.push(O);
      if (newFiles.length) files[O.client_id] = newFiles; else delete files[O.client_id];
      store_.save(); drawRows();
    },
    onDelete: () => { T.observations = T.observations.filter(x => x.client_id !== o.client_id); delete files[o.client_id]; store_.save(); drawRows(); },
  });

  // --- ynglepar
  const pairList = h("div", { class: "obs-list" });
  const pairCard = h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Ynglepar"), pairList);
  const addPair = (p = null) => openPairSheet({
    ref, recent, T, pair: p,
    onSave: (P) => { const i = p ? T.breeding_pairs.findIndex(x => x.client_id === p.client_id) : -1; if (i >= 0) T.breeding_pairs[i] = P; else T.breeding_pairs.push(P); store_.save(); drawPairs(); },
    onDelete: () => { T.breeding_pairs = T.breeding_pairs.filter(x => x.client_id !== p.client_id); store_.save(); drawPairs(); },
  });
  const drawPairs = () => {
    pairCard.classList.toggle("hide", !T.breeding_pairs.length);
    pairList.replaceChildren(...T.breeding_pairs.map(p => h("button", { type: "button", class: "obs-row pair", onclick: () => addPair(p) },
      h("span", { class: "obs-main" }, h("span", { class: "obs-title" }, h("b", {}, p.art || sp(p.artnr)?.navn), h("small", { class: "muted" }, `${p.antal} par`)), h("small", { class: "muted" }, adfText(p.adfkode) || "")))));
    drawMap();
  };

  // --- forstyrrelser (redigeres i turens data)
  const dist = T.disturbances.length ? h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Forstyrrelser"),
    ...T.disturbances.map(d => h("div", {}, h("b", {}, ref.forstyrrelser.find(x => x.id === d.type_id)?.navn || "Forstyrrelse"),
      h("small", { class: "muted" }, [d.antal != null ? ` · ${d.antal} stk.` : "", d.noter ? ` · ${d.noter}` : ""].join(""))))) : null;

  // --- GPS-rute
  const gpsInfo = h("small", { class: "muted" });
  const gpsBtns = h("div", { class: "row", style: { gap: "6px" } });
  const drawGps = () => {
    const n = T.gps_track.length, active = tracker.cid === T.client_id && tracker.watch != null;
    gpsInfo.textContent = n ? `${n} punkter · ${(trackLength(T.gps_track) / 1000).toFixed(2).replace(".", ",")} km${active ? " · optager" : ""}` : "Ruten gemmes og sendes med turen.";
    gpsBtns.replaceChildren(
      active ? h("button", { class: "btn sm", type: "button", onclick: () => { trackPause(); store_.save(); drawGps(); } }, "Pause")
        : h("button", { class: "btn sm", type: "button", html: `${icon("pin")} ${n ? "Fortsæt rute" : "Start rute"}`, onclick: startGps }),
      n || active ? h("button", { class: "btn sm", type: "button", onclick: () => { trackPause(); T.gps_aktiv = n > 0; store_.save(); drawGps(); } }, "Stop") : null,
      n && !active ? h("button", { class: "btn sm ghost", type: "button", onclick: () => { if (confirm("Slet den optagede rute?")) { trackStop(); T.gps_track = []; T.gps_aktiv = false; store_.save(); drawGps(); drawMap(); } } }, "Slet rute") : null);
  };
  const startGps = () => {
    if (!navigator.geolocation) return toast("Enheden understøtter ikke positionsbestemmelse", "err");
    if (!T.gps_aktiv && !confirm("Aktivér tracking for denne tur?\n\nRuten gemmes på din enhed og sendes med turen til portalen. Sporingen stopper, når du pauser eller stopper den. " + (isApp() ? "I appen optages ruten også med slukket skærm." : "Hold appen åben under turen."))) return;
    T.gps_aktiv = true;
    trackStart(T.client_id, T.gps_track); T.gps_track = tracker.points;
    store_.save(); drawGps();
  };
  if (tracker.cid === T.client_id) T.gps_track = tracker.points;
  let lastSave = 0;
  const onPoint = () => { drawGps(); drawMap(); if (Date.now() - lastSave > 60000) { lastSave = Date.now(); store_.save(); } };
  tracker.listeners.add(onPoint);

  // --- kort
  const mapEl = h("div", { class: "map small", style: { height: "240px" } });
  let map = null, layer = null;
  function drawMap() {
    if (!map) return;
    layer.clearLayers();
    const pts = [];
    if (T.lok_lat) pts.push([T.lok_lat, T.lok_lon]);
    if (T.gps_track.length > 1) { const line = T.gps_track.map(p => [p[0], p[1]]); L.polyline(line, { color: "#c0392b", weight: 3 }).addTo(layer); pts.push(...line); }
    for (const o of T.observations) if (o.obs_lat) { pts.push([o.obs_lat, o.obs_lon]); L.marker([o.obs_lat, o.obs_lon]).addTo(layer).bindPopup(`${fmtN(o.antal)} ${o.art}`); }
    for (const p of T.breeding_pairs) if (p.lat) { pts.push([p.lat, p.lon]); L.circleMarker([p.lat, p.lon], { radius: 7, color: "#fff", weight: 2, fillColor: "#e67e22", fillOpacity: 1 }).addTo(layer).bindPopup(`Ynglepar: ${p.art}`); }
    if (pts.length === 1) map.setView(pts[0], 14); else if (pts.length) map.fitBounds(pts, { padding: [20, 20], maxZoom: 16 });
  }
  setTimeout(() => { if (map || !mapEl.isConnected) return; map = baseMap(mapEl, { center: [56, 10.5], zoom: 7 }); layer = L.layerGroup().addTo(map); drawMap(); }, 100);

  const reuseBox = h("input", { type: "checkbox", checked: reuse.on, onchange: (e) => { reuse.on = e.target.checked; store("turreuse", reuse.on); } });
  const time = [T.turtidfra, T.turtidtil].filter(Boolean).join(" – ");
  page.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("p", { style: { margin: 0 } }, h("a", { href: "#/indtast" }, "← Mine indtastninger")),
      h("h1", { style: { marginTop: "4px" } }, T.lok || "Tur"),
      h("p", { class: "lead", html: `${icon("calendar")} ` }, [fmtDate(T.dato), time].filter(Boolean).join(" · "))),
      h("span", { class: "spacer" }), chip,
      h("a", { class: "btn", href: `#/indtast/tur/${T.server_id || `lokal/${T.client_id}`}/ret`, html: `${icon("edit")} Redigér tur` })),
    T.on_behalf_of ? h("div", { class: "notice warn mb" }, `Indtastet på vegne af ${T.on_behalf_of}.`) : null,
    h("div", { class: "card stack" }, h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Observationer"), countEl),
      h("label", { class: "check" }, reuseBox, h("span", {}, "Genanvend adfærd")), obsList),
    pairCard, dist,
    h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "GPS-rute"), gpsBtns, gpsInfo),
    h("div", { class: "card" }, mapEl),
    h("div", { class: "tur-bar" }, h("button", { class: "btn", type: "button", onclick: () => addPair() }, "Ynglepar"),
      h("button", { class: "btn primary grow", type: "button", html: `${icon("plus")} Observation`, onclick: () => edit() })));
  drawRows(); drawPairs(); drawGps();
  return () => { unsub(); tracker.listeners.delete(onPoint); map?.remove(); };
}
