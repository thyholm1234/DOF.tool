// Vindanimation à la Windy: partikler der flyder med et vindfelt interpoleret fra DMI-stationer (IDW).
// Lægges som et canvas oven på et Leaflet-kort; tegnes forfra ved panorering/zoom.

const BOUNDS = { s: 54.3, n: 58.0, w: 7.3, e: 15.5 };
const STEP = 0.1; // gitter i grader

// Vindfelt (u, v i m/s – retning vinden blæser HEN) på et regulært gitter
function field(stations) {
  const pts = stations.map(s => {
    const r = s.dir * Math.PI / 180; // dir = hvor vinden kommer FRA
    return { lat: s.lat, lon: s.lon, u: -s.speed * Math.sin(r), v: -s.speed * Math.cos(r) };
  });
  const nx = Math.round((BOUNDS.e - BOUNDS.w) / STEP) + 1, ny = Math.round((BOUNDS.n - BOUNDS.s) / STEP) + 1;
  const U = new Float32Array(nx * ny), V = new Float32Array(nx * ny);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const lat = BOUNDS.s + j * STEP, lon = BOUNDS.w + i * STEP;
    let wu = 0, wv = 0, ws = 0;
    for (const p of pts) {
      const dx = (p.lon - lon) * Math.cos(lat * Math.PI / 180), dy = p.lat - lat;
      const w = 1 / Math.pow(dx * dx + dy * dy + 0.01, 1.5);
      wu += w * p.u; wv += w * p.v; ws += w;
    }
    U[j * nx + i] = wu / ws; V[j * nx + i] = wv / ws;
  }
  return (lat, lon) => {
    const x = (lon - BOUNDS.w) / STEP, y = (lat - BOUNDS.s) / STEP;
    if (x < 0 || y < 0 || x >= nx - 1 || y >= ny - 1) return null;
    const i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j;
    const at = (A) => A[j * nx + i] * (1 - fx) * (1 - fy) + A[j * nx + i + 1] * fx * (1 - fy) + A[(j + 1) * nx + i] * (1 - fx) * fy + A[(j + 1) * nx + i + 1] * fx * fy;
    return [at(U), at(V)];
  };
}

const speedColor = (s) => s < 3 ? "rgba(255,255,255,.75)" : s < 6 ? "rgba(190,230,255,.85)" : s < 9 ? "rgba(255,236,150,.9)" : s < 12 ? "rgba(255,180,90,.95)" : "rgba(255,110,90,1)";

export function windLayer(map, stations, { count = 1800, speedFactor = 0.00045 } = {}) {
  if (!stations?.length) return { remove() {} };
  const wind = field(stations);
  const canvas = document.createElement("canvas");
  Object.assign(canvas.style, { position: "absolute", inset: "0", pointerEvents: "none", zIndex: 450 });
  map.getContainer().append(canvas);
  const ctx = canvas.getContext("2d");
  let particles = [], raf = 0, running = true;
  const rand = () => {
    const b = map.getBounds();
    const s = Math.max(b.getSouth(), BOUNDS.s), n = Math.min(b.getNorth(), BOUNDS.n);
    const w = Math.max(b.getWest(), BOUNDS.w), e = Math.min(b.getEast(), BOUNDS.e);
    return { lat: s + Math.random() * (n - s), lon: w + Math.random() * (e - w), age: Math.floor(Math.random() * 80) };
  };
  const reset = () => {
    const size = map.getSize(), dpr = window.devicePixelRatio || 1;
    canvas.width = size.x * dpr; canvas.height = size.y * dpr;
    canvas.style.width = size.x + "px"; canvas.style.height = size.y + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);
    particles = Array.from({ length: count }, rand);
  };
  const frame = () => {
    if (!running) return;
    const size = map.getSize();
    // Lad sporene falme
    ctx.globalCompositeOperation = "destination-in";
    ctx.fillStyle = "rgba(0,0,0,0.93)";
    ctx.fillRect(0, 0, size.x, size.y);
    ctx.globalCompositeOperation = "source-over";
    ctx.lineWidth = 1.3;
    const zoomScale = Math.pow(2, 7 - map.getZoom());
    for (const p of particles) {
      const w = wind(p.lat, p.lon);
      if (!w || p.age++ > 90) { Object.assign(p, rand(), { age: 0 }); continue; }
      const a = map.latLngToContainerPoint([p.lat, p.lon]);
      p.lon += w[0] * speedFactor * zoomScale / Math.cos(p.lat * Math.PI / 180);
      p.lat += w[1] * speedFactor * zoomScale;
      const b = map.latLngToContainerPoint([p.lat, p.lon]);
      ctx.strokeStyle = speedColor(Math.hypot(w[0], w[1]));
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    raf = requestAnimationFrame(frame);
  };
  const pause = () => { cancelAnimationFrame(raf); ctx.clearRect(0, 0, canvas.width, canvas.height); };
  const resume = () => { cancelAnimationFrame(raf); reset(); raf = requestAnimationFrame(frame); };
  map.on("movestart zoomstart", pause);
  map.on("moveend zoomend resize", resume);
  resume();
  return {
    remove() {
      running = false; cancelAnimationFrame(raf);
      map.off("movestart zoomstart", pause); map.off("moveend zoomend resize", resume);
      canvas.remove();
    },
    sample: wind,
  };
}
