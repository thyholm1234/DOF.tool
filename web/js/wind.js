// Vindanimation à la Windy: partikler der driver med et vindfelt og efterlader falmende spor, oven på et
// farvelag for vindstyrken. Feltet er enten et modelgitter (DMI HARMONIE: {lat0, lon0, step, nx, ny, u, v})
// eller DMI-stationer ([{lat, lon, speed, dir}]), som så interpoleres (IDW) til et gitter.
// Partiklerne har geografiske positioner og flyttes i skærmpixels, så de følger kortet ved panorering.
import "./maps.js";  // Leaflet (L) skal være hentet

const STATION_GRID = { lat0: 54.3, lon0: 7.3, step: 0.1, ny: 38, nx: 83 };

/** Farveskala for vindstyrke (m/s) – til farvelaget og forklaringen. */
export const SPEED_STOPS = [[0, [59, 91, 219]], [3, [49, 160, 201]], [6, [64, 182, 120]], [9, [196, 205, 70]],
  [12, [240, 160, 50]], [16, [226, 74, 51]], [21, [165, 46, 128]], [28, [110, 40, 160]]];

function speedRgb(s) {
  const i = SPEED_STOPS.findIndex(([v]) => v > s);
  if (i <= 0) return (i === 0 ? SPEED_STOPS[0] : SPEED_STOPS.at(-1))[1];
  const [a, ca] = SPEED_STOPS[i - 1], [b, cb] = SPEED_STOPS[i], f = (s - a) / (b - a);
  return ca.map((c, k) => Math.round(c + (cb[k] - c) * f));
}

/** Gradient til forklaring under kortet. */
export function speedLegend() {
  const max = SPEED_STOPS.at(-1)[0];
  const grad = SPEED_STOPS.map(([v, c]) => `rgb(${c.join(",")}) ${(v / max * 100).toFixed(0)}%`).join(", ");
  const el = document.createElement("div");
  el.className = "wind-legend";
  el.innerHTML = `<span>Vind m/s</span><span class="scale"><i style="background: linear-gradient(90deg, ${grad})"></i>` +
    `<span class="ticks">${[0, 5, 10, 15, 20, 25].map(v => `<b style="left:${v / max * 100}%">${v}</b>`).join("")}</span></span>`;
  return el;
}

function stationsToGrid(stations) {
  const g = { ...STATION_GRID, u: [], v: [] };
  const pts = stations.map(s => {
    const r = s.dir * Math.PI / 180; // dir = hvor vinden kommer FRA
    return { lat: s.lat, lon: s.lon, u: -s.speed * Math.sin(r), v: -s.speed * Math.cos(r) };
  });
  for (let j = 0; j < g.ny; j++) for (let i = 0; i < g.nx; i++) {
    const lat = g.lat0 + j * g.step, lon = g.lon0 + i * g.step;
    let wu = 0, wv = 0, ws = 0;
    for (const p of pts) {
      const dx = (p.lon - lon) * Math.cos(lat * Math.PI / 180), dy = p.lat - lat;
      const w = 1 / Math.pow(dx * dx + dy * dy + 0.01, 1.5);
      wu += w * p.u; wv += w * p.v; ws += w;
    }
    g.u.push(wu / ws); g.v.push(wv / ws);
  }
  return g;
}

/** Bilineær interpolation i gitteret → (lat, lon) ↦ [u, v] eller null uden for. */
function sampler(g) {
  return (lat, lon) => {
    const x = (lon - g.lon0) / g.step, y = (lat - g.lat0) / g.step;
    if (x < 0 || y < 0 || x >= g.nx - 1 || y >= g.ny - 1) return null;
    const i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j;
    const k = j * g.nx + i;
    const at = (A) => A[k] * (1 - fx) * (1 - fy) + A[k + 1] * fx * (1 - fy) + A[k + g.nx] * (1 - fx) * fy + A[k + g.nx + 1] * fx * fy;
    return [at(g.u), at(g.v)];
  };
}

/** Farvelag for vindstyrken som billede over gitterets udstrækning (glat skaleret af browseren). */
function speedOverlay(g, opacity) {
  const up = 4; // finere end gitteret: interpoler selv, så overgangene bliver bløde
  const c = document.createElement("canvas");
  c.width = (g.nx - 1) * up; c.height = (g.ny - 1) * up;
  const ctx = c.getContext("2d"), img = ctx.createImageData(c.width, c.height), wind = sampler(g);
  for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++) {
    const lat = g.lat0 + (c.height - 1 - y) / up * g.step, lon = g.lon0 + x / up * g.step;
    const w = wind(Math.min(lat, g.lat0 + (g.ny - 1.001) * g.step), Math.min(lon, g.lon0 + (g.nx - 1.001) * g.step));
    const rgb = speedRgb(w ? Math.hypot(w[0], w[1]) : 0), o = (y * c.width + x) * 4;
    img.data[o] = rgb[0]; img.data[o + 1] = rgb[1]; img.data[o + 2] = rgb[2]; img.data[o + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  const bounds = [[g.lat0, g.lon0], [g.lat0 + (g.ny - 1) * g.step, g.lon0 + (g.nx - 1) * g.step]];
  return L.imageOverlay(c.toDataURL(), bounds, { opacity, interactive: false, className: "wind-speed" });
}

export function windLayer(map, input, { density = 1 / 260, maxCount = 4500, pxPerMs = 0.28, overlay = 0.38 } = {}) {
  const grid = Array.isArray(input) ? (input.length ? stationsToGrid(input) : null) : (input?.u?.length ? input : null);
  if (!grid) return { remove() {} };
  const wind = sampler(grid);
  const speedLayer = overlay ? speedOverlay(grid, overlay).addTo(map) : null;
  const canvas = document.createElement("canvas");
  Object.assign(canvas.style, { position: "absolute", inset: "0", pointerEvents: "none", zIndex: 450 });
  map.getContainer().append(canvas);
  const ctx = canvas.getContext("2d");
  let particles = [], raf = 0, running = true, moving = false, zooming = false;

  const spawn = (p = {}) => {
    const s = map.getSize();
    const ll = map.containerPointToLatLng([Math.random() * s.x, Math.random() * s.y]);
    return Object.assign(p, { lat: ll.lat, lon: ll.lng, age: 0, life: 40 + Math.floor(Math.random() * 70) });
  };
  const reset = () => {
    const s = map.getSize(), dpr = window.devicePixelRatio || 1;
    canvas.width = s.x * dpr; canvas.height = s.y * dpr;
    canvas.style.width = s.x + "px"; canvas.style.height = s.y + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, s.x, s.y);
    const n = Math.min(maxCount, Math.round(s.x * s.y * density));
    particles = Array.from({ length: n }, () => { const p = spawn(); p.age = Math.floor(Math.random() * p.life); return p; });
  };
  const frame = () => {
    if (!running) return;
    raf = requestAnimationFrame(frame);
    if (zooming) return;
    const s = map.getSize();
    if (moving) ctx.clearRect(0, 0, s.x, s.y);
    else {
      // Lad sporene falme
      ctx.globalCompositeOperation = "destination-in";
      ctx.fillStyle = "rgba(0,0,0,0.92)";
      ctx.fillRect(0, 0, s.x, s.y);
      ctx.globalCompositeOperation = "source-over";
    }
    ctx.lineWidth = 1.25;
    ctx.lineCap = "round";
    ctx.strokeStyle = "rgba(255,255,255,0.85)";
    ctx.beginPath();
    for (const p of particles) {
      const w = wind(p.lat, p.lon);
      if (!w || ++p.age > p.life) { spawn(p); continue; }
      const a = map.latLngToContainerPoint([p.lat, p.lon]);
      if (a.x < -20 || a.y < -20 || a.x > s.x + 20 || a.y > s.y + 20) { spawn(p); continue; }
      const bx = a.x + w[0] * pxPerMs, by = a.y - w[1] * pxPerMs;
      ctx.moveTo(a.x, a.y); ctx.lineTo(bx, by);
      const ll = map.containerPointToLatLng([bx, by]);
      p.lat = ll.lat; p.lon = ll.lng;
    }
    ctx.stroke();
  };
  const onMoveStart = () => { moving = true; };
  const onMoveEnd = () => { moving = false; };
  const onZoomStart = () => { zooming = true; ctx.clearRect(0, 0, canvas.width, canvas.height); };
  const onZoomEnd = () => { zooming = false; reset(); };
  map.on("movestart", onMoveStart);
  map.on("moveend", onMoveEnd);
  map.on("zoomstart", onZoomStart);
  map.on("zoomend resize", onZoomEnd);
  reset();
  raf = requestAnimationFrame(frame);
  return {
    remove() {
      running = false; cancelAnimationFrame(raf);
      map.off("movestart", onMoveStart); map.off("moveend", onMoveEnd);
      map.off("zoomstart", onZoomStart); map.off("zoomend resize", onZoomEnd);
      canvas.remove(); speedLayer?.remove();
    },
    sample: wind,
  };
}
