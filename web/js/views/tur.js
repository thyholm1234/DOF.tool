// Tur: indtastning som i app.dofbasen.dk – lokalitet, tider, metode, vejr og GPS-rute, og under turen observationer,
// ynglepar og forstyrrelser. Turen gemmes i portalens egen database (mærket "indtastet i DOF.tool"). Er der ikke
// forbindelse, gemmes den på enheden og sendes automatisk (se offline.js). Kladden gemmes løbende på enheden.
import { api, h, icon, fmtN, fmtDate, todayISO, tag, toast, errorBox, seg, requireLogin, searchSpecies, compressImage, modal, KAT } from "../core.js";
import { baseMap } from "../maps.js";
import { SIKKERHED, GRUNDLAG } from "../reportui.js";
import { newId, putLocal, getLocal, deleteLocal, upload, isNetworkError } from "../offline.js";

const nowHM = () => new Date().toTimeString().slice(0, 5);
const GUIDE_URL = "https://www.dof.dk/om-dof/aktiv-i-dof/grupper-og-udvalg/feltornitologisk-udvalg/nar-du-ser-en-sjaelden-fugl";
const req = () => h("span", { class: "req", title: "Påkrævet" }, "påkrævet");
const COUNT = { 1: "Uangivet", 2: "Præcis", 3: "Estimat" };
const TRIP_FIELDS = ["dato", "loknr", "turtidfra", "turtidtil", "metode_id", "komplet", "spontan", "medobser", "turnoter", "on_behalf_of", "lat", "lon",
  "temperatur", "skydaekke", "vindretning", "vindstyrke", "nedboer", "nedboertype", "sigtbarhed", "gps_aktiv"];
const num = (v) => v === "" || v == null ? null : Number(v);

let REF = null;  // arter, kodelister, turopslag
async function loadRef() {
  if (REF) return REF;
  const [species, codes, lookups] = await Promise.all([api("/api/reports/species"), api("/api/reports/codes"), api("/api/trips/lookups")]);
  return REF = { species, codes, ...lookups };
}

// ---------------------------------------------------------------- GPS-rute (lever videre, når man forlader siden)

const tracker = { cid: null, watch: null, points: [], paused: false, lock: null, listeners: new Set(), saver: null };
const meters = (a, b) => {
  const R = 6371000, rad = Math.PI / 180, dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
};
const trackLength = (pts) => pts.reduce((s, p, i) => i ? s + meters(pts[i - 1], p) : 0, 0);

function trackStart(cid, points) {
  if (tracker.cid !== cid) { trackStop(); tracker.cid = cid; tracker.points = points; }
  tracker.paused = false;
  if (tracker.watch != null) return;
  tracker.watch = navigator.geolocation.watchPosition((p) => {
    const pt = [Math.round(p.coords.latitude * 1e6) / 1e6, Math.round(p.coords.longitude * 1e6) / 1e6, Math.round(p.timestamp / 1000), Math.round(p.coords.accuracy)];
    const last = tracker.points[tracker.points.length - 1];
    if (pt[3] > 100 || (last && meters(last, pt) < 5) || tracker.points.length >= 20000) return;
    tracker.points.push(pt);
    tracker.listeners.forEach(fn => fn());
  }, (err) => {
    toast(err.code === 1 ? "Lokationstilladelse er afvist – GPS-ruten kan ikke optages" : "Position ikke tilgængelig: " + err.message, "err");
    trackPause();
  }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 30000 });
  navigator.wakeLock?.request("screen").then(l => { tracker.lock = l; }).catch(() => {});
  // Ruten gemmes i kladden hvert 20. sekund, så den overlever, at appen lukkes
  tracker.saver ||= setInterval(async () => {
    const rec = tracker.cid && await getLocal(tracker.cid);
    if (rec?.draft) await putLocal({ ...rec, draft: { ...rec.draft, gps_track: tracker.points } });
  }, 20000);
}
function trackPause() {
  if (tracker.watch != null) navigator.geolocation.clearWatch(tracker.watch);
  tracker.watch = null; tracker.paused = true;
  tracker.lock?.release?.().catch(() => {}); tracker.lock = null;
  tracker.listeners.forEach(fn => fn());
}
function trackStop() {
  trackPause();
  clearInterval(tracker.saver); tracker.saver = null;
  tracker.cid = null; tracker.points = []; tracker.paused = false;
}

// ---------------------------------------------------------------- siden

export async function render(view, [which]) {
  if (requireLogin(view)) return;
  view.append(h("div", { class: "skeleton", style: { height: "260px" } }));
  let ref, T, files = {};
  try {
    ref = await loadRef();
    if (which === "ny") T = blankTrip();
    else if (which.startsWith("lokal/")) {
      const rec = await getLocal(which.slice(6));
      if (!rec?.draft) throw new Error("Turen findes ikke på denne enhed");
      T = rec.draft; T.server_id = rec.server_id; files = rec.files || {};
    } else {
      T = fromServer(await api(`/api/trips/${which}`), ref);
    }
  } catch (err) { view.replaceChildren(errorBox(err), h("p", {}, h("a", { href: "#/indtast" }, "← Mine indtastninger"))); return; }
  return editor(view, ref, T, files);
}

function blankTrip() {
  return { client_id: newId(), server_id: null, dato: todayISO(), loknr: null, lok: null, turtidfra: nowHM(), turtidtil: "", metode_id: 1, komplet: null,
    spontan: false, medobser: "", turnoter: "", on_behalf_of: "", lat: null, lon: null, temperatur: null, skydaekke: null, vindretning: null,
    vindstyrke: null, nedboer: null, nedboertype: null, sigtbarhed: null, gps_aktiv: false, gps_track: [], observations: [], breeding_pairs: [],
    disturbances: [] };
}

function fromServer(t, ref) {
  const klass = (artnr) => ref.species.find(s => s.artnr === artnr)?.klass || "Alm";
  return { ...blankTrip(), ...t, client_id: t.client_id || newId(), server_id: t.id, turtidfra: t.turtidfra || "", turtidtil: t.turtidtil || "",
    on_behalf_of: t.for_andre ? t.obserkode : "", medobser: t.medobser || "", turnoter: t.turnoter || "", gps_track: t.gps_track || [],
    observations: t.observations.map(o => ({ ...o, client_id: o.client_id || newId(), klass: klass(o.artnr), status: "ukendt", _media: o.media?.length || 0 })),
    breeding_pairs: t.breeding_pairs.map(p => ({ ...p, client_id: p.client_id || newId() })),
    disturbances: t.disturbances.map(d => ({ ...d, client_id: d.client_id || newId() })) };
}

function toBody(T) {
  const body = {};
  for (const k of TRIP_FIELDS) body[k] = T[k] === "" ? null : T[k];
  body.client_id = T.client_id;
  body.on_behalf_of = T.on_behalf_of || null;
  body.gps_track = T.gps_track || [];
  body.observations = T.observations.map(o => ({
    id: o.id ?? null, client_id: o.client_id, artnr: o.artnr, antal: o.antal ?? 1, antalmetode: o.antalmetode ?? null, maerker: o.maerker || [], koen: o.koen || null,
    adfkode: o.adfkode || null, alderkode: o.alderkode || null, dragtkode: o.dragtkode || null, obstidfra: o.obstidfra || null, obstidtil: o.obstidtil || null,
    obs_lat: o.obs_lat ?? null, obs_lon: o.obs_lon ?? null, radius_m: o.radius_m ?? null, fuglnoter: o.fuglnoter || null, hemmelig: !!o.hemmelig,
    skjul_position: !!o.skjul_position, sikkerhed: o.sikkerhed || "sikker", grundlag: o.grundlag || [], status: o.status || "ukendt", retningslinjer: !!o.retningslinjer }));
  body.breeding_pairs = T.breeding_pairs.map(p => ({ id: p.id ?? null, client_id: p.client_id, artnr: p.artnr, antal: p.antal, adfkode: p.adfkode || null,
    lat: p.lat ?? null, lon: p.lon ?? null, noter: p.noter || null, hemmelig: !!p.hemmelig }));
  body.disturbances = T.disturbances.map(d => ({ id: d.id ?? null, client_id: d.client_id, type_id: d.type_id, antal: d.antal ?? null, noter: d.noter || null }));
  return body;
}

// ---------------------------------------------------------------- redigering

function editor(view, ref, T, files) {
  let recent = { locations: [], species: [], adf: [] };
  const sp = (artnr) => ref.species.find(s => s.artnr === artnr);
  const isNew = !T.server_id;
  let saving = false, dirty = false, saveTimer = null;
  if (tracker.cid === T.client_id) T.gps_track = tracker.points;

  const saveDraft = () => {
    dirty = true; clearTimeout(saveTimer);
    saveTimer = setTimeout(() => { if (!saving) putLocal({ client_id: T.client_id, server_id: T.server_id, state: "kladde", draft: T, files }).catch(() => {}); }, 600);
  };
  const bind = (el, key, conv = (v) => v) => { el.value = T[key] ?? ""; el.addEventListener("input", () => { T[key] = conv(el.value); saveDraft(); }); return el; };
  const field = (label, el, required) => h("label", { class: "field" }, h("span", {}, label, required ? [" ", req()] : null), el);

  // --- turen
  const dato = bind(h("input", { type: "date", max: todayISO() }), "dato");
  const start = bind(h("input", { type: "time" }), "turtidfra");
  const slut = bind(h("input", { type: "time" }), "turtidtil");
  const metode = h("select", {}, ...ref.metoder.map(m => h("option", { value: m.id }, `${m.navn}${m.kort && m.kort !== "-" ? ` (${m.kort})` : ""}`)));
  metode.value = T.metode_id; metode.onchange = () => { T.metode_id = Number(metode.value); saveDraft(); };
  const komplet = h("select", {}, h("option", { value: "" }, "Ikke angivet"), h("option", { value: "1" }, "Ja – komplet artsliste"), h("option", { value: "0" }, "Nej"));
  komplet.value = T.komplet == null ? "" : T.komplet ? "1" : "0"; komplet.onchange = () => { T.komplet = komplet.value === "" ? null : komplet.value === "1"; saveDraft(); };
  const spontan = h("input", { type: "checkbox", checked: !!T.spontan, onchange: (e) => { T.spontan = e.target.checked; saveDraft(); } });
  const medobs = bind(h("input", { type: "text", maxlength: 300, placeholder: "Navne på dem, du var sammen med" }), "medobser");
  const noter = bind(h("textarea", { maxlength: 4000, placeholder: "Noter om turen …" }), "turnoter");

  // --- lokalitet: søg, nærmeste til din position og seneste steder
  const lokSel = h("select", { "aria-label": "Lokalitet" });
  const known = new Map(T.loknr ? [[T.loknr, T.lok]] : []);
  const coords = new Map(T.lok_lat ? [[T.loknr, [T.lok_lat, T.lok_lon]]] : []);
  const fillLocs = (rows, head) => {
    rows.forEach(l => { known.set(l.loknr, l.navn); if (l.lat != null) coords.set(l.loknr, [l.lat, l.lon]); });
    const opts = !T.loknr || rows.some(l => l.loknr === T.loknr) ? rows : [{ loknr: T.loknr, navn: known.get(T.loknr) }, ...rows];
    lokSel.replaceChildren(h("option", { value: "" }, head || "Vælg lokalitet …"), ...opts.map(l => h("option", { value: l.loknr },
      [l.navn, l.km != null ? `${String(l.km).replace(".", ",")} km` : l.kommune].filter(Boolean).join(" · "))));
    lokSel.value = T.loknr ? String(T.loknr) : "";
  };
  lokSel.onchange = () => { T.loknr = Number(lokSel.value) || null; T.lok = known.get(T.loknr) || null; lokSel.classList.remove("invalid"); saveDraft(); drawMap(); };
  const lokQ = h("input", { type: "search", placeholder: "Søg lokalitet …", autocomplete: "off" });
  let lokT;
  lokQ.oninput = () => {
    clearTimeout(lokT);
    lokT = setTimeout(async () => {
      const q = lokQ.value.trim();
      if (q.length < 2) return;
      try { fillLocs(await api(`/api/locations?q=${encodeURIComponent(q)}`), "Vælg blandt søgeresultaterne …"); } catch (err) { toast(err.message, "err"); }
    }, 250);
  };
  const nearMe = () => navigator.geolocation?.getCurrentPosition(async (p) => {
    T.lat = Math.round(p.coords.latitude * 1e6) / 1e6; T.lon = Math.round(p.coords.longitude * 1e6) / 1e6; saveDraft();
    try {
      const rows = (await api(`/api/reports/near?lat=${T.lat}&lon=${T.lon}`)).slice(0, 12);
      fillLocs(rows, "Nærmeste lokaliteter …");
      if (!T.loknr && rows[0]) { T.loknr = rows[0].loknr; T.lok = rows[0].navn; lokSel.value = String(T.loknr); }
      drawMap();
    } catch (err) { toast(err.message, "err"); }
  }, (err) => toast("Position ikke tilgængelig: " + err.message, "err"), { enableHighAccuracy: true, timeout: 15000 });
  fillLocs([]);
  api("/api/reports/recent").then(r => { recent = r; if (r.locations.length && !lokQ.value) fillLocs(r.locations, "Dine seneste lokaliteter …"); }).catch(() => {});

  // --- på vegne af
  const behalf = bind(h("input", { type: "text", maxlength: 16, placeholder: "Obserkode, fx 8220CVH", style: { textTransform: "uppercase" } }), "on_behalf_of", (v) => v.trim().toUpperCase());
  const behalfNote = h("div", {});
  const behalfBox = h("details", { class: "more", open: !!T.on_behalf_of }, h("summary", {}, "Indtast på vegne af en anden observatør"),
    h("div", { class: "mt" }, field("Observatørens obserkode", behalf), h("small", { class: "muted" }, "Observationerne bliver registreret på den obserkode, men vises som indtastet af dig.")));
  behalf.addEventListener("input", () => behalfNote.replaceChildren(T.on_behalf_of ? h("div", { class: "notice warn" }, `Du indtaster på vegne af ${T.on_behalf_of}.`) : ""));
  behalfNote.append(T.on_behalf_of ? h("div", { class: "notice warn" }, `Du indtaster på vegne af ${T.on_behalf_of}.`) : "");

  // --- vejr
  const sel = (key, opts, ph) => { const s = h("select", {}, h("option", { value: "" }, ph), ...opts.map(o => h("option", { value: o[0] }, o[1]))); s.value = T[key] ?? ""; s.onchange = () => { T[key] = s.value === "" ? null : (typeof opts[0][0] === "number" ? Number(s.value) : s.value); saveDraft(); }; return s; };
  const numIn = (key, ph, min, max) => bind(h("input", { type: "number", inputmode: "numeric", step: 1, min, max, placeholder: ph }), key, num);
  const weather = h("details", { class: "more", open: [T.temperatur, T.skydaekke, T.vindretning, T.vindstyrke, T.nedboer, T.sigtbarhed].some(v => v != null) },
    h("summary", {}, "Vejr"),
    h("div", { class: "form-grid two mt" },
      field("Temperatur (°C)", numIn("temperatur", "fx 8", -60, 60)),
      field("Skydække (ottendedele)", sel("skydaekke", [...Array(9).keys()].map(i => [i, String(i)]), "–")),
      field("Vindretning", sel("vindretning", ref.vindretninger.map(v => [v, v]), "–")),
      field("Vindstyrke (m/s)", numIn("vindstyrke", "hele tal", 0, 50)),
      field("Nedbør (mm)", numIn("nedboer", "mm", 0, 1000)),
      field("Type af nedbør", sel("nedboertype", ref.nedboertyper.map(v => [v, v]), "–")),
      field("Sigtbarhed (m)", numIn("sigtbarhed", "meter", 0, 100000))));

  // --- GPS-rute
  const gpsInfo = h("small", { class: "muted" });
  const gpsBtns = h("div", { class: "row", style: { gap: "6px" } });
  const drawGps = () => {
    const n = T.gps_track.length, active = tracker.cid === T.client_id && tracker.watch != null;
    gpsInfo.textContent = n ? `${n} punkter · ${(trackLength(T.gps_track) / 1000).toFixed(2).replace(".", ",")} km${active ? " · optager" : tracker.paused && tracker.cid === T.client_id ? " · på pause" : ""}`
      : "Ruten gemmes på din enhed og sendes med turen. Sporingen stopper, når du pauser eller stopper den.";
    gpsBtns.replaceChildren(
      active ? h("button", { class: "btn sm", type: "button", onclick: () => { trackPause(); drawGps(); } }, "Pause") :
        h("button", { class: "btn sm primary", type: "button", html: `${icon("pin")} ${n ? "Fortsæt rute" : "Start rute"}`, onclick: startGps }),
      n || active ? h("button", { class: "btn sm", type: "button", onclick: () => { trackPause(); T.gps_aktiv = n > 0; saveDraft(); drawGps(); } }, "Stop") : null,
      n ? h("button", { class: "btn sm ghost", type: "button", onclick: () => { if (confirm("Slet den optagede rute?")) { trackStop(); T.gps_track = []; T.gps_aktiv = false; saveDraft(); drawGps(); drawMap(); } } }, "Slet rute") : null);
  };
  const startGps = () => {
    if (!navigator.geolocation) return toast("Enheden understøtter ikke positionsbestemmelse", "err");
    if (!T.gps_aktiv && !confirm("Aktivér tracking for denne tur?\n\nRuten gemmes på din enhed og sendes med turen til portalen. Sporingen stopper, når du pauser eller stopper den. Hold appen åben under turen.")) return;
    T.gps_aktiv = true; saveDraft();
    trackStart(T.client_id, T.gps_track);
    T.gps_track = tracker.points;
    drawGps();
  };
  const onPoint = () => { drawGps(); drawMap(); };
  tracker.listeners.add(onPoint);
  const gpsCard = h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "GPS-rute"), gpsBtns, gpsInfo);

  // --- kort over lokalitet, rute og observationer
  const mapEl = h("div", { class: "map small", style: { height: "260px" } });
  let map = null, layer = null;
  const drawMap = () => {
    if (!map) return;
    layer.clearLayers();
    const pts = [];
    const loc = coords.get(T.loknr);
    if (T.lat) { pts.push([T.lat, T.lon]); L.circleMarker([T.lat, T.lon], { radius: 6, color: "#fff", weight: 2, fillColor: "#2a6fb0", fillOpacity: 1 }).addTo(layer).bindPopup("Din position"); }
    if (loc) pts.push(loc);
    if (T.gps_track.length > 1) { const line = T.gps_track.map(p => [p[0], p[1]]); L.polyline(line, { color: "#c0392b", weight: 3 }).addTo(layer); pts.push(...line); }
    for (const o of T.observations) if (o.obs_lat) { pts.push([o.obs_lat, o.obs_lon]); L.marker([o.obs_lat, o.obs_lon]).addTo(layer).bindPopup(`${fmtN(o.antal)} ${o.art}`); }
    for (const p of T.breeding_pairs) if (p.lat) { pts.push([p.lat, p.lon]); L.circleMarker([p.lat, p.lon], { radius: 7, color: "#fff", weight: 2, fillColor: "#e67e22", fillOpacity: 1 }).addTo(layer).bindPopup(`Ynglepar: ${p.art}`); }
    if (pts.length === 1) map.setView(pts[0], 14); else if (pts.length) map.fitBounds(pts, { padding: [20, 20], maxZoom: 16 });
  };
  setTimeout(() => {
    if (map || !mapEl.isConnected) return;
    map = baseMap(mapEl, { center: [56, 10.5], zoom: 7 }); layer = L.layerGroup().addTo(map);
    drawMap();
  }, 100);

  // --- position på kort (til observation og ynglepar)
  const pickPosition = (current, done) => {
    let pos = current ? [...current] : null, marker = null;
    const el = h("div", { class: "map", style: { height: "min(60vh, 420px)" } });
    const info = h("small", { class: "muted" }, pos ? `${pos[0].toFixed(5)}, ${pos[1].toFixed(5)}` : "Tryk på kortet");
    const m = modal("Hvor sad fuglen?", h("div", { class: "stack", style: { padding: "0 16px 16px" } }, el, info,
      h("div", { class: "row", style: { gap: "6px" } },
        h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Min position`, onclick: () => navigator.geolocation?.getCurrentPosition((p) => set([p.coords.latitude, p.coords.longitude], true), (e) => toast("Position ikke tilgængelig: " + e.message, "err"), { enableHighAccuracy: true, timeout: 15000 }) }),
        h("span", { style: { flex: 1 } }),
        h("button", { class: "btn sm ghost", type: "button", onclick: () => { done(null); m.close(); } }, "Fjern position"),
        h("button", { class: "btn sm primary", type: "button", onclick: () => { if (pos) { done(pos.map(v => Math.round(v * 1e6) / 1e6)); m.close(); } else toast("Tryk på kortet først", "err"); } }, "Brug position"))), { wide: true });
    const mp = baseMap(el, { center: pos || (T.lat ? [T.lat, T.lon] : [56, 10.5]), zoom: pos || T.lat ? 15 : 7 });
    const set = (p, fly) => { pos = p; marker?.remove(); marker = L.marker(p).addTo(mp); info.textContent = `${p[0].toFixed(5)}, ${p[1].toFixed(5)}`; if (fly) mp.setView(p, 16); };
    if (pos) set(pos);
    mp.on("click", (e) => set([e.latlng.lat, e.latlng.lng]));
  };

  // --- art-vælger (deles af observationer og ynglepar)
  const speciesPicker = (onPick, initial) => {
    const input = h("input", { type: "search", placeholder: "Skriv eller vælg art …", autocomplete: "off", autocorrect: "off", spellcheck: "false" });
    const suggest = h("div", { class: "suggest dropdown" });
    const chosen = h("div", { class: "chosen" });
    const recentSp = () => recent.species.map(s => sp(s.artnr)).filter(Boolean).slice(0, 12);
    const option = (s) => h("button", { type: "button", onmousedown: (e) => e.preventDefault(), onclick: () => pick(s) },
      h("span", {}, s.navn), s.klass !== "Alm" ? tag(s.klass) : null, s.latin ? h("i", { class: "muted" }, s.latin) : null);
    const show = () => {
      const q = input.value.trim();
      suggest.replaceChildren(...(q.length >= 2 ? searchSpecies(ref.species, q).map(option)
        : recentSp().length ? [h("small", { class: "muted dd-head" }, "Dine seneste arter"), ...recentSp().map(option)] : []));
    };
    input.onfocus = show; input.oninput = show; input.onblur = () => setTimeout(() => suggest.replaceChildren(), 150);
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); suggest.querySelector("button")?.click(); } if (e.key === "Escape") suggest.replaceChildren(); };
    const pick = (s) => {
      input.classList.remove("invalid"); suggest.replaceChildren(); input.value = ""; input.classList.toggle("hide", !!s);
      chosen.replaceChildren(...(s ? [h("span", { class: "chosen-sp" }, h("b", {}, s.navn), s.latin ? h("i", { class: "muted" }, ` ${s.latin}`) : null,
        s.klass && s.klass !== "Alm" ? tag(s.klass) : null,
        h("button", { type: "button", class: "icon-btn", "aria-label": "Skift art", html: icon("x"), onclick: () => { pick(null); input.focus(); } }))] : []));
      onPick(s);
    };
    if (initial) pick(initial);
    return { el: h("div", { class: "field" }, h("span", { class: "label" }, "Art ", req()), chosen, h("div", { class: "dd-wrap" }, input, suggest)), input, pick };
  };

  const adfSelect = (value, only) => {
    const list = only ? ref.codes.adf.filter(only) : ref.codes.adf;
    const s = h("select", { required: true, class: "required" }, h("option", { value: "" }, "Vælg adfærd …"),
      !only && recent.adf.length ? h("optgroup", { label: "Dine seneste" }, ...recent.adf.map(a => h("option", { value: a.code }, a.text))) : null,
      !only && list.some(a => a.popular) ? h("optgroup", { label: "Mest brugte" }, ...list.filter(a => a.popular).map(a => h("option", { value: a.code }, a.text))) : null,
      h("optgroup", { label: "Alle" }, ...[...list].sort((a, b) => a.text.localeCompare(b.text, "da")).map(a => h("option", { value: a.code }, `${a.text} (${a.code})`))));
    s.value = value || ""; s.onchange = () => s.classList.remove("invalid");
    return s;
  };
  const codeSel = (opts, value) => { const s = h("select", {}, h("option", { value: "" }, "–"), ...opts.map(o => h("option", { value: o.code }, o.text))); s.value = value || ""; return s; };
  const fail = (msg, el) => { toast(msg, "err"); el?.classList.add("invalid"); el?.scrollIntoView?.({ block: "center", behavior: "smooth" }); el?.focus?.(); return false; };

  // --- observationer
  const obsList = h("div", { class: "stack", style: { gap: "6px" } });
  const obsForm = h("div", {});
  let lastAdf = T.observations.at(-1)?.adfkode || "", reuseAdf = true, lastGuide = false;
  const obsRow = (o, i) => h("div", { class: `tur-row ${sp(o.artnr)?.klass === "SU" ? "SU" : sp(o.artnr)?.klass === "SUB" ? "SUB" : ""}` },
    h("div", { class: "tur-main" }, h("div", { class: "row", style: { gap: "6px" } }, h("b", { class: "num" }, fmtN(o.antal)), h("b", {}, o.art),
      o.klass && o.klass !== "Alm" ? tag(o.klass) : null, o.hemmelig ? h("span", { class: "tag plain", html: `${icon("lock")} Hemmelig` }) : null),
      h("small", { class: "muted" }, [ref.codes.adf.find(a => a.code === o.adfkode)?.text, o.obstidfra, o.antalmetode > 1 ? COUNT[o.antalmetode] : null,
        o.obs_lat ? "position" : null, (o._files?.length || files[o.client_id]?.length || o._media) ? "📷" : null].filter(Boolean).join(" · "))),
    h("button", { class: "btn sm", type: "button", html: icon("edit"), "aria-label": "Redigér", onclick: () => openObs(i) }),
    h("button", { class: "btn sm", type: "button", html: icon("trash"), "aria-label": "Slet", onclick: () => { if (confirm(`Slet ${o.antal} ${o.art}?`)) { T.observations.splice(i, 1); delete files[o.client_id]; saveDraft(); drawLists(); drawMap(); } } }));
  const openObs = (idx = -1) => {
    const editing = idx >= 0 ? T.observations[idx] : null;
    const O = editing ? { ...editing } : { client_id: newId(), artnr: null, antal: 1, antalmetode: 1, maerker: [], grundlag: ["set"], sikkerhed: "sikker", status: "ukendt",
      obstidfra: T.turtidfra || nowHM(), adfkode: reuseAdf ? lastAdf : "", retningslinjer: lastGuide, hemmelig: false, skjul_position: false };
    const newFiles = [...(editing ? files[editing.client_id] || [] : [])];
    const picker = speciesPicker((s) => {
      O.artnr = s?.artnr ?? null; O.art = s?.navn; O.klass = s?.klass;
      const rare = s && ["SU", "SUB"].includes(s.klass);
      warn.replaceChildren(rare ? h("div", { class: "notice warn" }, `${s.navn} er ${s.klass}-art. Angiv sikkerhed og grundlag, husk dokumentation (foto/beskrivelse), og bekræft DOF's retningslinjer nederst.`) : "");
      guideBox.classList.toggle("hide", !rare);
      if (rare && !editing && T.dato === todayISO()) { O.status = "til_stede"; drawStatus(); }
    }, editing ? sp(editing.artnr) || { artnr: editing.artnr, navn: editing.art } : null);
    const warn = h("div", {});
    const antal = h("input", { type: "number", min: 0, max: 9900000, inputmode: "numeric", value: O.antal, style: { maxWidth: "110px" }, oninput: () => { O.antal = Number(antal.value) || 0; } });
    const bump = (d) => { antal.value = Math.max(0, (Number(antal.value) || 0) + d); O.antal = Number(antal.value); };
    const adf = adfSelect(O.adfkode);
    const tid = h("input", { type: "time", value: O.obstidfra || "" });
    const note = h("textarea", { maxlength: 2000, placeholder: "Kendetegn, adfærd, lyd …" }, O.fuglnoter || "");
    const marks = h("div", { class: "chips wrap" });
    const drawMarks = () => marks.replaceChildren(...ref.codes.maerker.map(m => h("button", { type: "button", class: `chip ${O.maerker.includes(m.code) ? "on" : ""}`,
      onclick: () => { O.maerker = O.maerker.includes(m.code) ? O.maerker.filter(x => x !== m.code) : [...O.maerker, m.code]; drawMarks(); } }, m.text)));
    drawMarks();
    const koen = codeSel(ref.codes.koen, O.koen), alder = codeSel(ref.codes.alder, O.alderkode), dragt = codeSel(ref.codes.dragt, O.dragtkode);
    const basis = h("div", { class: "chips wrap" });
    const drawBasis = () => basis.replaceChildren(...Object.entries(GRUNDLAG).map(([k, label]) => h("button", { type: "button", class: `chip ${O.grundlag.includes(k) ? "on" : ""}`,
      onclick: () => { O.grundlag = O.grundlag.includes(k) ? O.grundlag.filter(x => x !== k) : [...O.grundlag, k]; drawBasis(); } }, label)));
    drawBasis();
    const posInfo = h("small", { class: "muted" });
    const drawPos = () => { posInfo.textContent = O.obs_lat ? `Fuglens position: ${O.obs_lat.toFixed(5)}, ${O.obs_lon.toFixed(5)}` : "Ingen position valgt (valgfrit)"; };
    drawPos();
    const statusWrap = h("div", {});
    const drawStatus = () => statusWrap.replaceChildren(seg([["til_stede", "Ja, til stede nu"], ["fløjet", "Væk"], ["ukendt", "Ved ikke"]], O.status, (s) => { O.status = s; }));
    drawStatus();
    const guide = h("input", { type: "checkbox", checked: !!O.retningslinjer });
    const guideBox = h("label", { class: `check guide ${["SU", "SUB"].includes(O.klass) ? "" : "hide"}` }, guide, h("span", {},
      "Jeg har læst og følger DOF's retningslinjer ", h("a", { href: GUIDE_URL, target: "_blank", rel: "noopener" }, "Når du ser en sjælden fugl"), " ", h("span", { class: "req" }, "påkrævet for SU/SUB")));
    const hemmelig = h("input", { type: "checkbox", checked: !!O.hemmelig });
    const fileInput = h("input", { type: "file", accept: "image/*,audio/*", multiple: true, class: "hide" });
    const fileList = h("div", { class: "chips wrap" });
    const drawFiles = () => fileList.replaceChildren(...newFiles.map((f, i) => h("span", { class: "chip on" }, f.name.length > 24 ? f.name.slice(0, 22) + "…" : f.name,
      h("button", { type: "button", class: "chip-x", html: icon("x"), onclick: () => { newFiles.splice(i, 1); drawFiles(); } }))));
    drawFiles();
    fileInput.onchange = async () => {
      for (const f of fileInput.files) newFiles.push(await compressImage(f));
      fileInput.value = ""; drawFiles();
      if (newFiles.some(f => f.type.startsWith("image")) && !O.grundlag.includes("foto")) { O.grundlag.push("foto"); drawBasis(); }
      if (newFiles.some(f => f.type.startsWith("audio")) && !O.grundlag.includes("lyd")) { O.grundlag.push("lyd"); drawBasis(); }
    };
    const commit = (another) => {
      if (!O.artnr) return fail("Vælg en art", picker.input);
      if (!adf.value) return fail("Vælg adfærd – det er påkrævet", adf);
      if (["SU", "SUB"].includes(O.klass) && !guide.checked) return fail("Bekræft DOF's retningslinjer for sjældne fugle", guideBox);
      Object.assign(O, { antal: Number(antal.value) || 0, adfkode: adf.value, obstidfra: tid.value || null, fuglnoter: note.value.trim() || null, koen: koen.value || null,
        alderkode: alder.value || null, dragtkode: dragt.value || null, retningslinjer: guide.checked, hemmelig: hemmelig.checked, skjul_position: O.skjul_position && !!O.obs_lat });
      if (editing) T.observations[idx] = O; else T.observations.push(O);
      files[O.client_id] = newFiles.length ? newFiles : undefined;
      if (!newFiles.length) delete files[O.client_id];
      lastAdf = O.adfkode; lastGuide = O.retningslinjer;
      saveDraft(); drawLists(); drawMap();
      obsForm.replaceChildren();
      if (another && !editing) openObs();
    };
    obsForm.replaceChildren(h("div", { class: "card stack report-form" }, h("h3", { style: { margin: 0 } }, editing ? "Redigér observation" : "Ny observation"),
      picker.el, warn,
      h("div", { class: "field" }, h("span", { class: "label" }, "Antal"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, antal,
        h("button", { class: "btn sm", type: "button", onclick: () => bump(-1) }, "−1"), h("button", { class: "btn sm", type: "button", onclick: () => bump(1) }, "+1"),
        h("button", { class: "btn sm", type: "button", onclick: () => bump(10) }, "+10")),
        seg(Object.entries(COUNT).map(([k, v]) => [Number(k), v]), O.antalmetode || 1, (v) => { O.antalmetode = v; })),
      h("label", { class: "field" }, h("span", { class: "label" }, "Adfærd ", req()), adf),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: reuseAdf, onchange: (e) => { reuseAdf = e.target.checked; } }), h("span", {}, "Genbrug adfærd til næste observation")),
      h("div", { class: "field" }, h("span", { class: "label" }, "Markeringer"), marks),
      h("div", { class: "field" }, h("span", { class: "label" }, "Tidspunkt"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, tid,
        h("button", { class: "btn sm", type: "button", onclick: () => { tid.value = nowHM(); } }, "Nu"))),
      h("label", { class: "field" }, h("span", {}, "Noter om fuglen"), note),
      h("div", { class: "field" }, h("span", { class: "label" }, "Position"), h("div", { class: "row", style: { gap: "6px" } }, posInfo,
        h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Vælg på kort`, onclick: () => pickPosition(O.obs_lat ? [O.obs_lat, O.obs_lon] : null, (p) => { O.obs_lat = p?.[0] ?? null; O.obs_lon = p?.[1] ?? null; drawPos(); }) }))),
      h("details", { class: "more" }, h("summary", {}, "Køn, alder, dragt og sikkerhed"),
        h("div", { class: "form-grid two mt" }, field("Køn", koen), field("Alder", alder), field("Dragt", dragt)),
        h("div", { class: "field mt" }, h("span", { class: "label" }, "Sikkerhed"), seg(Object.entries(SIKKERHED), O.sikkerhed, (s) => { O.sikkerhed = s; }),
          h("small", { class: "muted", style: { display: "block", margin: "8px 0 4px" } }, "Bestemmelsen bygger på:"), basis)),
      h("div", { class: "field" }, h("span", { class: "label" }, "Er fuglen under observation?"), statusWrap),
      h("div", { class: "field" }, h("span", { class: "label" }, "Foto og lyd"),
        h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: () => fileInput.click(), html: `${icon("plus")} Tilføj foto eller lyd` }), fileInput,
          editing?._media ? h("small", { class: "muted" }, `${editing._media} fil(er) gemt`) : null), fileList),
      h("label", { class: "check" }, hemmelig, h("span", {}, "Hemmelig (kun synlig for dig)")),
      guideBox,
      h("div", { class: "row between" }, h("button", { class: "btn ghost", type: "button", onclick: () => obsForm.replaceChildren() }, "Annullér"),
        h("div", { class: "row", style: { gap: "6px" } }, editing ? null : h("button", { class: "btn", type: "button", onclick: () => commit(true) }, "Gem og næste"),
          h("button", { class: "btn primary", type: "button", onclick: () => commit(false) }, editing ? "Gem rettelser" : "Gem observation")))));
    obsForm.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  // --- ynglepar
  const pairList = h("div", { class: "stack", style: { gap: "6px" } });
  const pairForm = h("div", {});
  const yp = (a) => a.code.startsWith("YP");
  const openPair = (idx = -1) => {
    const editing = idx >= 0 ? T.breeding_pairs[idx] : null;
    const P = editing ? { ...editing } : { client_id: newId(), artnr: null, antal: 1, adfkode: "YP", lat: null, lon: null, noter: "", hemmelig: false };
    const picker = speciesPicker((s) => { P.artnr = s?.artnr ?? null; P.art = s?.navn; }, editing ? sp(editing.artnr) || { artnr: editing.artnr, navn: editing.art } : null);
    const antal = h("input", { type: "number", min: 1, max: 100000, value: P.antal, inputmode: "numeric", style: { maxWidth: "110px" } });
    const adf = adfSelect(P.adfkode, yp);
    const note = h("textarea", { maxlength: 2000, placeholder: "Fx redens placering …" }, P.noter || "");
    const hem = h("input", { type: "checkbox", checked: !!P.hemmelig });
    const posInfo = h("small", { class: "muted" });
    const drawPos = () => { posInfo.textContent = P.lat ? `Position: ${P.lat.toFixed(5)}, ${P.lon.toFixed(5)}` : "Ingen position valgt (valgfrit)"; };
    drawPos();
    pairForm.replaceChildren(h("div", { class: "card stack report-form" }, h("h3", { style: { margin: 0 } }, editing ? "Ret ynglepar" : "Ny indtastning af ynglepar"), picker.el,
      h("label", { class: "field" }, h("span", {}, "Antal par"), antal), h("label", { class: "field" }, h("span", { class: "label" }, "Adfærd ", req()), adf),
      h("div", { class: "field" }, h("span", { class: "label" }, "Position"), h("div", { class: "row", style: { gap: "6px" } }, posInfo,
        h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Vælg på kort`, onclick: () => pickPosition(P.lat ? [P.lat, P.lon] : null, (p) => { P.lat = p?.[0] ?? null; P.lon = p?.[1] ?? null; drawPos(); }) }))),
      h("label", { class: "field" }, h("span", {}, "Noter"), note),
      h("label", { class: "check" }, hem, h("span", {}, "Hemmelig")),
      h("div", { class: "row between" }, h("button", { class: "btn ghost", type: "button", onclick: () => pairForm.replaceChildren() }, "Annullér"),
        h("button", { class: "btn primary", type: "button", onclick: () => {
          if (!P.artnr) return fail("Vælg en art", picker.input);
          if (!adf.value) return fail("Vælg adfærd – det er påkrævet", adf);
          Object.assign(P, { antal: Math.max(1, Number(antal.value) || 1), adfkode: adf.value, noter: note.value.trim(), hemmelig: hem.checked });
          if (editing) T.breeding_pairs[idx] = P; else T.breeding_pairs.push(P);
          saveDraft(); drawLists(); drawMap(); pairForm.replaceChildren();
        } }, "Gem ynglepar"))));
    pairForm.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  // --- forstyrrelser
  const distList = h("div", { class: "stack", style: { gap: "6px" } });
  const dType = h("select", {}, h("option", { value: "" }, "Vælg forstyrrelse …"), ...ref.forstyrrelser.map(d => h("option", { value: d.id }, d.navn)));
  const dAntal = h("input", { type: "number", min: 0, inputmode: "numeric", placeholder: "Antal", style: { maxWidth: "100px" } });
  const dNote = h("input", { type: "text", maxlength: 1000, placeholder: "Note (valgfri)" });
  const addDist = () => {
    if (!dType.value) return fail("Vælg en forstyrrelse", dType);
    if (T.disturbances.some(d => d.type_id === Number(dType.value))) return fail("Forstyrrelsen er allerede noteret", dType);
    T.disturbances.push({ client_id: newId(), type_id: Number(dType.value), antal: num(dAntal.value), noter: dNote.value.trim() });
    dType.value = ""; dAntal.value = ""; dNote.value = ""; saveDraft(); drawLists();
  };

  const drawLists = () => {
    obsList.replaceChildren(...(T.observations.length ? T.observations.map(obsRow) : [h("p", { class: "muted", style: { margin: 0 } }, "Ingen observationer endnu.")]));
    pairList.replaceChildren(...T.breeding_pairs.map((p, i) => h("div", { class: "tur-row" },
      h("div", { class: "tur-main" }, h("div", { class: "row", style: { gap: "6px" } }, h("b", { class: "num" }, fmtN(p.antal)), h("b", {}, `${p.art || sp(p.artnr)?.navn} – par`)),
        h("small", { class: "muted" }, [ref.codes.adf.find(a => a.code === p.adfkode)?.text, p.lat ? "position" : null].filter(Boolean).join(" · "))),
      h("button", { class: "btn sm", type: "button", html: icon("edit"), "aria-label": "Redigér", onclick: () => openPair(i) }),
      h("button", { class: "btn sm", type: "button", html: icon("trash"), "aria-label": "Slet", onclick: () => { if (confirm("Slet ynglepar?")) { T.breeding_pairs.splice(i, 1); saveDraft(); drawLists(); drawMap(); } } }))));
    distList.replaceChildren(...T.disturbances.map((d, i) => h("div", { class: "tur-row" },
      h("div", { class: "tur-main" }, h("b", {}, ref.forstyrrelser.find(x => x.id === d.type_id)?.navn || "Forstyrrelse"),
        h("small", { class: "muted" }, [d.antal != null ? `${d.antal} stk.` : null, d.noter].filter(Boolean).join(" · "))),
      h("span", {}), h("button", { class: "btn sm", type: "button", html: icon("trash"), "aria-label": "Slet", onclick: () => { T.disturbances.splice(i, 1); saveDraft(); drawLists(); } }))));
    countEl.textContent = `${T.observations.length} observation${T.observations.length === 1 ? "" : "er"} · ${new Set(T.observations.map(o => o.artnr)).size} arter`;
  };
  const countEl = h("small", { class: "muted" });

  // --- gem
  const submit = h("button", { class: "btn primary", type: "button" }, isNew ? "Gem tur" : "Gem rettelser");
  const validate = () => {
    if (!T.dato) return fail("Angiv dato", dato);
    if (!T.loknr) return fail("Vælg en lokalitet", lokSel);
    if (!T.turtidfra) return fail("Angiv starttidspunkt", start);
    if (T.turtidtil && T.turtidtil < T.turtidfra) return fail("Sluttidspunktet ligger før starten", slut);
    return true;
  };
  submit.onclick = async () => {
    if (!validate()) return;
    if (!T.observations.length && !T.breeding_pairs.length && !confirm("Turen har ingen observationer. Gem alligevel?")) return;
    saving = true; clearTimeout(saveTimer); submit.disabled = true;
    const rec = { client_id: T.client_id, server_id: T.server_id, state: "klar", draft: T, body: toBody(T), files: Object.fromEntries(Object.entries(files).filter(([, v]) => v?.length)) };
    try {
      const { saved, left } = await upload(rec);
      if (Object.keys(left).length) await putLocal({ ...rec, server_id: saved.id, files: left });
      else await deleteLocal(T.client_id);
      trackStop();
      toast(`Turen er gemt (${saved.observations.length} observation${saved.observations.length === 1 ? "" : "er"})`);
      location.hash = "#/indtast";
    } catch (err) {
      saving = false; submit.disabled = false;
      if (isNetworkError(err)) {
        await putLocal(rec);
        trackStop();
        toast("Ingen forbindelse – turen er gemt på enheden og sendes automatisk", "ok");
        location.hash = "#/indtast";
      } else toast(err.message, "err");
    }
  };
  const cancel = async () => {
    if (!confirm(isNew ? "Er du sikker på at du vil annullere indtastningen af denne tur?" : "Kassér ændringerne?")) return;
    clearTimeout(saveTimer); saving = true;
    await deleteLocal(T.client_id).catch(() => {});
    if (tracker.cid === T.client_id) trackStop();
    location.hash = "#/indtast";
  };

  view.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("p", { style: { margin: 0 } }, h("a", { href: "#/indtast" }, "← Mine indtastninger")),
      h("h1", { style: { marginTop: "4px" } }, isNew ? "Ny tur" : "Ret tur"),
      h("p", { class: "lead" }, isNew ? "Indtast en tur med flere observationer. Den gemmes i DOF.tool, ikke i DOFbasen." : [fmtDate(T.dato), T.lok].filter(Boolean).join(" · "))),
      h("span", { class: "spacer" }),
      !isNew ? h("button", { class: "btn danger solid", html: `${icon("trash")} Slet tur`, onclick: async () => {
        if (!confirm("Er du sikker på at du vil slette turen og alle dens observationer?")) return;
        try { await api(`/api/trips/${T.server_id}`, { method: "DELETE" }); await deleteLocal(T.client_id).catch(() => {}); toast("Turen er slettet"); location.hash = "#/indtast"; } catch (err) { toast(err.message, "err"); }
      } }) : null),
    behalfNote,
    h("div", { class: "card stack report-form" }, h("h3", { style: { margin: 0 } }, "Turen"),
      h("div", { class: "form-grid two" }, field("Dato", dato, true),
        h("div", { class: "field" }, h("span", { class: "label" }, "Tid ", req()), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, start,
          h("button", { class: "btn sm", type: "button", onclick: () => { start.value = nowHM(); T.turtidfra = start.value; saveDraft(); } }, "Nu"), h("span", { class: "muted" }, "–"), slut,
          h("button", { class: "btn sm", type: "button", onclick: () => { slut.value = nowHM(); T.turtidtil = slut.value; saveDraft(); } }, "Nu")))),
      h("div", { class: "field" }, h("span", { class: "label" }, "Lokalitet ", req()),
        h("div", { class: "row", style: { gap: "6px" } }, h("div", { style: { flex: 1, minWidth: "180px" } }, lokSel), h("div", { style: { flex: 1, minWidth: "160px" } }, lokQ),
          h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Nær mig`, onclick: nearMe }))),
      h("div", { class: "form-grid two" }, field("Metode", metode), field("Komplet artsliste?", komplet)),
      h("label", { class: "check" }, spontan, h("span", {}, "Spontan observation (ikke planlagt tur)")),
      field("Medobservatører", medobs), field("Turnoter", noter), behalfBox),
    h("div", { class: "card" }, weather),
    gpsCard,
    h("div", { class: "card" }, mapEl),
    h("div", { class: "card stack" }, h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Observationer"), countEl), obsList, obsForm,
      h("div", {}, h("button", { class: "btn", type: "button", html: `${icon("plus")} Tilføj observation`, onclick: () => openObs() }))),
    h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Ynglepar"), pairList, pairForm,
      h("div", {}, h("button", { class: "btn", type: "button", html: `${icon("plus")} Tilføj ynglepar`, onclick: () => openPair() }))),
    h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Forstyrrelser"), distList,
      h("div", { class: "row", style: { gap: "6px" } }, h("div", { style: { flex: 2, minWidth: "180px" } }, dType), dAntal, h("div", { style: { flex: 2, minWidth: "140px" } }, dNote),
        h("button", { class: "btn sm", type: "button", onclick: addDist }, "Tilføj"))),
    h("div", { class: "row between tur-footer" }, h("button", { class: "btn ghost", type: "button", onclick: cancel }, isNew ? "Annullér indtastning" : "Kassér ændringer"), submit));
  drawLists(); drawGps();

  return () => { tracker.listeners.delete(onPoint); clearTimeout(saveTimer); map?.remove(); };
}
