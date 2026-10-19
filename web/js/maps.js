// Leaflet-hjælpere: grundkort, farver og tegneværktøj (polygon og radius).
import { h } from "./core.js";

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
