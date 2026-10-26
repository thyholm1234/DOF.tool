// Små, afhængighedsfri SVG-grafer med tooltip.
import { h, esc } from "./core.js";

const NS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}) => { const el = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };
export const PALETTE = ["#2f7a5d", "#c8862a", "#2a6fb0", "#c0392b", "#7b4bb7", "#3d9aa6", "#a3527a", "#6b8e23", "#8c6d46", "#4a5bc4"];

// Én fælles tooltip for alle grafer; fjernes ved sideskift, så den ikke hænger, når grafen forsvinder under musen
let tip = null;
const hideTip = () => { tip?.remove(); tip = null; };
addEventListener("hashchange", hideTip);
addEventListener("popstate", hideTip);
document.addEventListener("click", hideTip, true);

function niceTicks(min, max, n = 5) {
  if (max <= min) max = min + 1;
  const span = max - min;
  const step0 = Math.pow(10, Math.floor(Math.log10(span / n)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * step0).find(st => span / st <= n) || step0 * 10;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

/**
 * series: [{ name, color, points: [[x, y], ...], dash, width }]
 * band: { lower: [[x,y]], upper: [[x,y]] }
 * xFormat(x) → label, yFormat(y) → label
 */
export function lineChart({ series, band, height = 260, xFormat = String, yFormat = String, xTicks, yMin = 0, step = false, markers = [] }) {
  const W = 720, H = height, m = { l: 44, r: 12, t: 12, b: 26 };
  const all = series.flatMap(se => se.points).concat(band ? [...band.lower, ...band.upper] : []);
  if (!all.length) return h("p", { class: "muted" }, "Ingen data endnu.");
  const xs = all.map(p => p[0]), ys = all.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = yMin ?? Math.min(...ys), y1 = Math.max(...ys) * 1.05 || 1;
  const X = (x) => m.l + (x - x0) / ((x1 - x0) || 1) * (W - m.l - m.r);
  const Y = (y) => H - m.b - (y - y0) / ((y1 - y0) || 1) * (H - m.t - m.b);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img" });
  for (const t of niceTicks(y0, y1)) {
    svg.append(s("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: "grid" }));
    const tx = s("text", { x: m.l - 6, y: Y(t) + 4, "text-anchor": "end" }); tx.textContent = yFormat(t); svg.append(tx);
  }
  for (const t of (xTicks || niceTicks(x0, x1, 6))) {
    if (t < x0 || t > x1) continue;
    const tx = s("text", { x: X(t), y: H - 8, "text-anchor": "middle" }); tx.textContent = xFormat(t); svg.append(tx);
  }
  svg.append(s("line", { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, class: "axis" }));
  if (band) {
    const pts = band.upper.map(p => `${X(p[0])},${Y(p[1])}`).concat([...band.lower].reverse().map(p => `${X(p[0])},${Y(p[1])}`));
    svg.append(s("polygon", { points: pts.join(" "), class: "band" }));
  }
  for (const mk of markers) {
    svg.append(s("line", { x1: X(mk.x), x2: X(mk.x), y1: m.t, y2: H - m.b, stroke: mk.color || "currentColor", "stroke-dasharray": "3 3", "stroke-width": 1.2, opacity: .7 }));
    if (mk.label) { const tx = s("text", { x: X(mk.x) + 4, y: m.t + 10 }); tx.textContent = mk.label; svg.append(tx); }
  }
  series.forEach((se, i) => {
    if (!se.points.length) return;
    let d = "";
    se.points.forEach((p, j) => {
      if (j === 0) d += `M${X(p[0])},${Y(p[1])}`;
      else if (step) d += `H${X(p[0])}V${Y(p[1])}`;
      else d += `L${X(p[0])},${Y(p[1])}`;
    });
    svg.append(s("path", { d, fill: "none", stroke: se.color || PALETTE[i % PALETTE.length], "stroke-width": se.width || 2,
      "stroke-dasharray": se.dash || "", "stroke-linejoin": "round", "stroke-linecap": "round" }));
  });
  // tooltip
  const cursor = s("line", { y1: m.t, y2: H - m.b, stroke: "currentColor", opacity: 0, "stroke-width": 1 });
  svg.append(cursor);
  svg.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width * W;
    const x = x0 + (px - m.l) / (W - m.l - m.r) * (x1 - x0);
    cursor.setAttribute("x1", X(x)); cursor.setAttribute("x2", X(x)); cursor.setAttribute("opacity", .25);
    const rows = series.map((se, i) => {
      let best = null;
      for (const p of se.points) { if (step ? p[0] <= x : true) { if (!best || Math.abs(p[0] - x) < Math.abs(best[0] - x) || (step && p[0] <= x)) best = p; } }
      return best ? `<span style="color:${se.color || PALETTE[i % PALETTE.length]}">●</span> ${esc(se.name)}: <b>${yFormat(best[1])}</b>` : null;
    }).filter(Boolean);
    if (!tip) { tip = h("div", { class: "chart-tip" }); document.body.append(tip); }
    tip.innerHTML = `<div style="opacity:.7">${xFormat(Math.round(x))}</div>` + rows.slice(0, 12).join("<br>");
    tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = (e.clientY + 14) + "px";
  });
  svg.addEventListener("pointerleave", () => { cursor.setAttribute("opacity", 0); tip?.remove(); tip = null; });
  return svg;
}

/** Vis den fælles tooltip ved musen (til grafer, der tegner deres egne mærker). */
export function showTip(e, html) {
  if (!tip) { tip = h("div", { class: "chart-tip" }); document.body.append(tip); }
  tip.innerHTML = html;
  tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = (e.clientY + 14) + "px";
}
export { hideTip };

/**
 * Søjlediagram: bars: [{ v, tip, hl }] i rækkefølge; xTicks: [[indeks, label]]; markers: [{ x (indeks, gerne brøk), label }].
 * width: viewBox-bredden – brug en mindre for halvbrede diagrammer, så teksten ikke skaleres ned. yMax deles mellem flere diagrammer, der skal kunne sammenlignes. onClick(i) gør søjlerne klikbare.
 */
export function barChart({ bars, width = 720, height = 220, xTicks = [], yFormat = String, yMax, markers = [], onClick }) {
  const W = width, H = height, m = { l: 44, r: 12, t: 12, b: 26 };
  if (!bars.length) return h("p", { class: "muted" }, "Ingen data endnu.");
  const top = (yMax ?? Math.max(...bars.map(b => b.v))) * 1.05 || 1;
  const slot = (W - m.l - m.r) / bars.length;
  const X = (i) => m.l + i * slot;
  const Y = (v) => H - m.b - v / top * (H - m.t - m.b);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img" });
  for (const t of niceTicks(0, top)) {
    if (t > top) continue;
    svg.append(s("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: "grid" }));
    const tx = s("text", { x: m.l - 6, y: Y(t) + 4, "text-anchor": "end" }); tx.textContent = yFormat(t); svg.append(tx);
  }
  for (const [i, label] of xTicks) {
    const tx = s("text", { x: X(i) + slot / 2, y: H - 8, "text-anchor": "middle" }); tx.textContent = label; svg.append(tx);
  }
  const gap = slot > 6 ? 2 : slot > 3 ? 1 : 0, bw = Math.max(slot - gap, 1), r = Math.min(4, bw / 2);
  bars.forEach((b, i) => {
    if (!(b.v > 0)) return;
    const x = X(i) + gap / 2, y = Y(b.v), y0 = H - m.b, rr = Math.min(r, y0 - y);
    svg.append(s("path", { d: `M${x},${y0}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + bw - rr}Q${x + bw},${y} ${x + bw},${y + rr}V${y0}Z`,
      style: `fill: var(${b.hl ? "--accent" : "--brand-2"})` }));
  });
  for (const mk of markers) {
    const x = X(mk.x);
    svg.append(s("line", { x1: x, x2: x, y1: m.t, y2: H - m.b, style: "stroke: var(--text-3)", "stroke-dasharray": "3 3", "stroke-width": 1.2 }));
    if (mk.label) { const tx = s("text", { x: x + 4, y: m.t + 10 }); tx.textContent = mk.label; svg.append(tx); }
  }
  svg.append(s("line", { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, class: "axis" }));
  // Hele søjlens højde er hit-område, så også små søjler kan rammes
  const hover = s("rect", { y: m.t, height: H - m.t - m.b, width: slot, style: "fill: var(--text)", opacity: 0, "pointer-events": "none" });
  svg.append(hover);
  bars.forEach((b, i) => {
    const hit = s("rect", { x: X(i), y: m.t, width: slot, height: H - m.t - m.b, fill: "transparent", style: onClick ? "cursor: pointer" : "" });
    hit.addEventListener("pointermove", (e) => { hover.setAttribute("x", X(i)); hover.setAttribute("opacity", .06); showTip(e, b.tip ?? yFormat(b.v)); });
    hit.addEventListener("pointerleave", () => { hover.setAttribute("opacity", 0); hideTip(); });
    if (onClick) hit.addEventListener("click", (e) => { e.stopPropagation(); hideTip(); onClick(i); });
    svg.append(hit);
  });
  return svg;
}

export function seriesLegend(series) {
  return h("div", { class: "legend" }, ...series.map((se, i) => h("span", {}, h("i", { style: { background: se.color || PALETTE[i % PALETTE.length] } }), se.name)));
}

/** Lille søjlediagram for værdier pr. år (fx q50-dato), med trendlinje. */
export function yearDots({ points, trend, yFormat = String, height = 220 }) {
  const series = [{ name: "År", points, color: PALETTE[0], width: 0 }];
  const el = lineChart({ series, height, yMin: null, yFormat, xFormat: (x) => String(x) });
  if (!(el instanceof SVGElement)) return el;
  // tegn punkter + trend oven i
  const W = 720, m = { l: 44, r: 12, t: 12, b: 26 };
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys) * 1.05;
  const X = (x) => m.l + (x - x0) / ((x1 - x0) || 1) * (W - m.l - m.r);
  const Y = (y) => height - m.b - (y - y0) / ((y1 - y0) || 1) * (height - m.t - m.b);
  for (const [x, y] of points) el.append(s("circle", { cx: X(x), cy: Y(y), r: 4.5, fill: PALETTE[0] }));
  if (trend) el.append(s("line", { x1: X(x0), y1: Y(trend.intercept + trend.slope * x0), x2: X(x1), y2: Y(trend.intercept + trend.slope * x1), stroke: PALETTE[1], "stroke-width": 2, "stroke-dasharray": "6 4" }));
  return el;
}
