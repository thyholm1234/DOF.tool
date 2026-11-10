// Leaflet-hjælpere: grundkort, farver og tegneværktøj (polygon og radius).
// Leaflet (JS + CSS fra /vendor) hentes først, når et modul med kort importerer denne fil – forsiden og de fleste andre
// sider indlæser det aldrig. Top-level await: når importen er færdig, er det globale L klar til brug.
import { h, api, esc, icon } from "./core.js";

let loading = null;
/** Hent Leaflet én gang (genbruges af alle kort). */
export function leaflet() {
  if (window.L) return Promise.resolve(window.L);
  const add = (tag, attrs) => new Promise((resolve, reject) => {
    const el = Object.assign(document.createElement(tag), attrs, { onload: resolve, onerror: () => reject(new Error(`Kunne ikke hente ${attrs.src || attrs.href}`)) });
    document.head.append(el);
  });
  loading ??= Promise.all([
    add("link", { rel: "stylesheet", href: "/vendor/leaflet/leaflet.css" }),
    add("script", { src: "/vendor/leaflet/leaflet.js" }),
  ]).then(() => window.L).catch((err) => { loading = null; throw err; });
  return loading;
}
await leaflet();

let geomanLoading = null;
/** Leaflet-Geoman (tegn og ret polygoner, MIT) – kun til Admin → Lokaliteter. Kort, der skal kunne redigeres, skal
 *  oprettes efter dette kald (Geoman kobler sig på nye kort). */
export function geoman() {
  if (window.L?.PM) return Promise.resolve();
  const add = (tag, attrs) => new Promise((resolve, reject) => {
    const el = Object.assign(document.createElement(tag), attrs, { onload: resolve, onerror: () => reject(new Error("Kunne ikke hente redigeringsværktøjet")) });
    document.head.append(el);
  });
  geomanLoading ??= Promise.all([
    add("link", { rel: "stylesheet", href: "/vendor/geoman/leaflet-geoman.css" }),
    add("script", { src: "/vendor/geoman/leaflet-geoman.js" }),
  ]).catch((err) => { geomanLoading = null; throw err; });
  return geomanLoading;
}

export const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
export const katColor = (k) => cssVar({ SU: "--su", SUB: "--sub", bemaerk: "--bem", local: "--local" }[k] || "--text-3");

export function baseMap(el, { center = [56.1, 10.8], zoom = 7 } = {}) {
  const map = L.map(el, { zoomControl: true, attributionControl: true }).setView(center, zoom);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  setTimeout(() => map.invalidateSize(), 50);
  return map;
}

/** Tegneværktøj. mode: 'polygon' (klik punkter, afslut) eller 'radius' (klik centrum, vælg radius). */
export function drawTool(map, { mode, radius = 2000, onChange }) {
  const layer = L.layerGroup().addTo(map);
  let points = [];
  let center = null;
  let shape = null;
  const redraw = () => {
    layer.clearLayers();
    if (mode === "polygon") {
      points.forEach(p => L.circleMarker(p, { radius: 4, color: cssVar("--brand"), fillOpacity: 1 }).addTo(layer));
      if (points.length >= 2) shape = L.polygon(points, { color: cssVar("--brand"), weight: 2, fillOpacity: .15 }).addTo(layer);
      onChange?.(points.length >= 3 ? { type: "Polygon", coordinates: [[...points, points[0]].map(p => [p.lng, p.lat])] } : null);
    } else if (center) {
      shape = L.circle(center, { radius, color: cssVar("--brand"), weight: 2, fillOpacity: .15 }).addTo(layer);
      L.circleMarker(center, { radius: 4, color: cssVar("--brand"), fillOpacity: 1 }).addTo(layer);
      onChange?.({ center: [center.lat, center.lng], radius });
    }
  };
  const click = (e) => { if (mode === "polygon") points.push(e.latlng); else center = e.latlng; redraw(); };
  map.on("click", click);
  map.getContainer().style.cursor = "crosshair";
  return {
    undo() { points.pop(); redraw(); },
    clear() { points = []; center = null; redraw(); },
    setRadius(r) { radius = r; redraw(); },
    setMode(m) { mode = m; points = []; center = null; redraw(); },
    destroy() { map.off("click", click); layer.remove(); map.getContainer().style.cursor = ""; },
  };
}

export function legend(items) {
  return h("div", { class: "legend" }, ...items.map(([color, label]) => h("span", {}, h("i", { style: { background: color, height: "10px", width: "10px", borderRadius: "50%" } }), label)));
}

/** Pil der peger i retning `deg` (grader, 0 = nord) – til trækretning. */
export const arrowSvg = (deg, size, color) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" style="transform: rotate(${deg}deg); filter: drop-shadow(0 0 1px rgba(0,0,0,.6))">` +
  `<path d="M12 2 L19 20 L12 15.5 L5 20 Z" fill="${color}" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/></svg>`;

/** Farver for trækgrupperne (trækkort og kort over fund). */
export const GROUP_COLORS = { rov_svaev: "#c0392b", rov_aktiv: "#e67e22", vade: "#16a085", gaes: "#8e5a2b", hav: "#2a6fb0", spurv: "#7b4bb7", drossel: "#d35485" };

// ---------------------------------------------------------------- lokaliteternes omrids (lokalitetsvalg på kort)

const ringHas = (ring, x, y) => {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
};
const ringArea = (ring) => Math.abs(ring.reduce((a, [x, y], i) => { const [x2, y2] = ring[(i + 1) % ring.length]; return a + x * y2 - x2 * y; }, 0)) / 2;
const polys = (g) => g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
const polyHas = (g, x, y) => polys(g).some(([outer, ...holes]) => ringHas(outer, x, y) && !holes.some(r => ringHas(r, x, y)));

/** Lokaliteternes grænser på kortet (fra zoom 10; hentes for kortudsnittet, når kortet flyttes). Lokaliteter uden grænse
 *  vises som små prikker. Grænserne tager ikke imod klik, så et tryk sætter stadig positionen.
 *  at(lat, lon) = den inderste lokalitet med grænse, punktet ligger i ({ loknr, navn }) eller null; select(loknr)
 *  fremhæver en lokalitet (i highlight-farven, standard --accent). onChange() kaldes, når nye grænser er hentet. */
export function locationOutlines(map, { onChange, highlight, selectedOnly = false } = {}) {
  const feats = new Map();  // loknr → feature
  const color = cssVar("--brand"), hi = highlight || cssVar("--accent") || color;
  // selectedOnly: kun den valgte lokalitet tegnes – de øvrige hentes stadig (til at(), hvilken lokalitet man står i)
  const base = selectedOnly ? { color, weight: 0, opacity: 0, fillOpacity: 0 } : { color, weight: 1.5, opacity: .8, fillColor: color, fillOpacity: .06 };
  const layer = L.geoJSON(null, {
    interactive: false,
    style: () => base,
    pointToLayer: (_f, ll) => L.circleMarker(ll, selectedOnly ? { radius: 0, opacity: 0, fillOpacity: 0, interactive: false }
      : { radius: 3, color, weight: 1, fillOpacity: .6, interactive: false }),
  }).addTo(map);
  const hint = L.control({ position: "bottomleft" });
  hint.onAdd = () => h("div", { class: "map-hint" }, "Zoom ind for at se lokaliteternes grænser");
  let selected = null, seq = 0, timer = null, hintOn = false;
  const restyle = () => layer.eachLayer((l) => {
    const on = l.feature?.properties.loknr === selected;
    if (l.setStyle && l.feature?.geometry.type !== "Point") l.setStyle(on ? { color: hi, weight: 3, opacity: 1, fillColor: hi, fillOpacity: .18 } : base);
    if (on && l.bringToFront) l.bringToFront();
  });
  const load = async () => {
    const z = map.getZoom();
    if (z < 10) { if (!hintOn && !selectedOnly) { hint.addTo(map); hintOn = true; } return; }
    if (hintOn) { hint.remove(); hintOn = false; }
    const b = map.getBounds(), me = ++seq;
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(v => v.toFixed(4)).join(",");
    try {
      const fc = await api(`/api/locations/shapes?bbox=${bbox}&zoom=${Math.round(z)}`);
      if (me !== seq) return;
      const fresh = fc.features.filter(f => !feats.has(f.properties.loknr));
      fresh.forEach(f => feats.set(f.properties.loknr, f));
      if (fresh.length) { layer.addData(fresh); restyle(); onChange?.(); }
    } catch { /* uden net: ingen grænser – positionen kan stadig sættes */ }
  };
  const later = () => { clearTimeout(timer); timer = setTimeout(load, 250); };
  map.on("moveend", later);
  load();
  return {
    at(lat, lon) {
      let best = null, bestA = Infinity;
      for (const f of feats.values()) {
        if (!polyHas(f.geometry, lon, lat)) continue;
        const a = polys(f.geometry).reduce((s, p) => s + ringArea(p[0]), 0);
        if (a < bestA) { best = f.properties; bestA = a; }
      }
      return best;
    },
    select(loknr) { selected = loknr || null; restyle(); },
    remove() { clearTimeout(timer); map.off("moveend", later); layer.remove(); hint.remove(); },
  };
}

/** «Naviger hertil»: på Android et geo:-link, der åbner systemets egen appvælger (Google Maps, Waze, …); andre steder
 *  (iOS, computer) et almindeligt Google Maps-link, der åbner appen, hvis den er installeret, og ellers browseren. */
export function navButton(lat, lon, label = "", text = "Naviger hertil") {
  const ll = `${(+lat).toFixed(6)},${(+lon).toFixed(6)}`;
  const attrs = /Android/i.test(navigator.userAgent)
    ? { href: `geo:${ll}?q=${ll}${label ? `(${encodeURIComponent(label)})` : ""}` }  // i samme fane, så der ikke står en tom fane tilbage
    : { href: `https://www.google.com/maps/dir/?api=1&destination=${ll}`, target: "_blank", rel: "noopener" };
  return h("a", { class: "btn sm ghost", ...attrs, html: `${icon("pin")} ${esc(text)}` });
}
