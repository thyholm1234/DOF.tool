import { api, h, fmtN, ago, esc, KAT, windArrow, compass } from "../core.js";
import { baseMap, katColor, legend } from "../maps.js";
import { live } from "../live.js";

export async function render(view) {
  const mapEl = h("div", { class: "map full" });
  let level = "SUB";
  let showWind = true;
  const chips = h("div", { class: "chips" });
  const drawChips = () => chips.replaceChildren(
    ...[["SU", "SU"], ["SUB", "SUB+"], ["bemaerk", "Bemærk+"]].map(([v, l]) => h("button", { class: `chip ${level === v ? "on" : ""}`, onclick: () => { level = v; drawChips(); load(); } }, l)),
    h("button", { class: `chip ${showWind ? "on" : ""}`, onclick: () => { showWind = !showWind; drawChips(); wind(); } }, "Vind (DMI)"));
  drawChips();
  const info = h("small", { class: "muted" });
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Kort over dagens fund"), info), h("span", { class: "spacer" }), chips),
    h("div", { class: "card", style: { padding: "8px" } }, mapEl,
      h("div", { class: "row mt", style: { padding: "0 6px 4px" } }, legend([[katColor("SU"), "SU"], [katColor("SUB"), "SUB"], [katColor("bemaerk"), "Bemærk"]]),
        h("small", { class: "muted" }, " · Pile viser vindretning og -styrke (m/s) fra DMI's stationer"))));
  const map = baseMap(mapEl, { center: [56.0, 11.0], zoom: 7 });
  const obsLayer = L.layerGroup().addTo(map);
  const windLayer = L.layerGroup().addTo(map);

  async function load() {
    const items = await api(`/api/map/today?level=${level}`);
    obsLayer.clearLayers();
    const order = { bemaerk: 1, SUB: 2, SU: 3 };
    items.sort((a, b) => order[a.kategori] - order[b.kategori]);
    for (const t of items) {
      const r = t.kategori === "SU" ? 9 : t.kategori === "SUB" ? 7 : 5;
      L.circleMarker([t.lat, t.lon], { radius: r, color: "#fff", weight: 1.5, fillColor: katColor(t.kategori), fillOpacity: .95 })
        .addTo(obsLayer)
        .bindPopup(`<b>${fmtN(t.max_antal)} ${esc(t.art)}</b><br>${esc(t.lok || "")}<br><small>${KAT[t.kategori]} · ${ago(t.last_seen)}</small><br><a href="#/traad/${t.id}">Åbn tråd →</a>`);
    }
    info.textContent = `${items.length} tråde i dag`;
  }

  async function wind() {
    windLayer.clearLayers();
    if (!showWind) return;
    try {
      const st = await api("/api/weather/current");
      for (const s of st) {
        if (s.dir == null) continue;
        const icon = L.divIcon({ className: "wind-arrow", iconSize: [34, 34], html: `<div style="text-align:center;line-height:1">${windArrow(s.dir, 22)}<div style="font-size:10px;font-weight:700;color:var(--text)">${Math.round(s.speed)}</div></div>` });
        L.marker([s.lat, s.lon], { icon, interactive: true, keyboard: false }).addTo(windLayer)
          .bindPopup(`Station ${s.id}<br>${compass(s.dir)} ${s.speed?.toFixed(1)} m/s${s.gust ? ` (vindstød ${s.gust.toFixed(1)})` : ""}<br>${s.temp != null ? s.temp.toFixed(1) + " °C" : ""}`);
      }
    } catch { /* DMI utilgængelig */ }
  }

  await Promise.all([load(), wind()]);
  let t;
  const off = live.on("feed", () => { clearTimeout(t); t = setTimeout(load, 1500); });
  return () => { off(); map.remove(); };
}
