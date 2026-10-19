import { api, h, fmtN, ago, esc, KAT, store, seg, windArrow, compass } from "../core.js";
import { baseMap, katColor, arrowSvg } from "../maps.js";
import { live } from "../live.js";

// Klasser som i DOFbasen; almindelige arter vises kun når de er trækkende med retning
const CLASSES = [["SU", "SU"], ["SUB", "SUB"], ["bemaerk", "Bemærk"], ["alm", "Alm. (trækkende)"]];
const DAYS = [["1", "I dag"], ["y", "I går"], ["2", "2 døgn"]];

export async function render(view) {
  const saved = store("mapst") || {};
  const st = { days: saved.days || "1", on: new Set(saved.on || ["SU", "SUB", "bemaerk"]), migOnly: !!saved.migOnly, wind: saved.wind !== false };
  const save = () => store("mapst", { days: st.days, on: [...st.on], migOnly: st.migOnly, wind: st.wind });
  const mapEl = h("div", { class: "map full" });
  const chips = h("div", { class: "chips wrap", style: { marginTop: 0 } });
  const info = h("small", { class: "muted" });
  let items = [];

  const drawChips = () => chips.replaceChildren(
    ...CLASSES.map(([k, label]) => {
      const n = items.filter(t => t.kategori === k && (!st.migOnly || t.dir != null)).length;
      const solo = st.on.size === 1 && st.on.has(k);
      return h("span", { class: `chip toggle ${st.on.has(k) ? "on" : ""}`, role: "button", tabindex: 0, title: "Slå til/fra",
        onclick: () => { st.on.has(k) ? st.on.delete(k) : st.on.add(k); update(); } },
        h("i", { class: "dot", style: { background: katColor(k) } }), label, h("span", { class: "n" }, ` ${fmtN(n)}`),
        h("button", { type: "button", class: `solo ${solo ? "active" : ""}`, title: solo ? "Vis alle klasser igen" : "Vis kun denne klasse",
          onclick: (e) => { e.stopPropagation(); st.on = solo ? new Set(CLASSES.map(c => c[0])) : new Set([k]); update(); } }, "S"));
    }),
    h("button", { class: `chip ${st.migOnly ? "on" : ""}`, title: "Vis kun observationer med trækretning", onclick: () => { st.migOnly = !st.migOnly; update(); } }, "Kun trækkende"),
    h("button", { class: `chip ${st.wind ? "on" : ""}`, onclick: () => { st.wind = !st.wind; save(); drawChips(); wind(); } }, "Vind nu (DMI)"));

  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Kort over fund og træk"), info), h("span", { class: "spacer" }),
      seg(DAYS, st.days, (v) => { st.days = v; save(); load(); })),
    h("div", { class: "card", style: { padding: "8px" } }, h("div", { style: { padding: "2px 4px 8px" } }, chips), mapEl,
      h("small", { class: "muted", style: { display: "block", padding: "6px 6px 2px" } },
        "Pile viser trækretning (vægtet med antal; størrelse efter antal trækkende). Prikker er fund uden retning. ",
        "Klik på en klasse for at slå den til/fra – S viser kun den klasse. I går vises lidt svagere ved 2 døgn.")));
  const map = baseMap(mapEl, { center: [56.0, 11.0], zoom: 7 });
  const obsLayer = L.layerGroup().addTo(map);
  const windLayer = L.layerGroup().addTo(map);
  const renderer = L.canvas();

  function draw() {
    obsLayer.clearLayers();
    const order = { alm: 0, bemaerk: 1, SUB: 2, SU: 3 };
    const shown = items.filter(t => st.on.has(t.kategori) && (!st.migOnly || t.dir != null)).sort((a, b) => order[a.kategori] - order[b.kategori]);
    const latest = items.reduce((m, t) => t.dato > m ? t.dato : m, "");
    for (const t of shown) {
      const old = st.days === "2" && t.dato !== latest;
      const color = katColor(t.kategori);
      const popup = `<b>${fmtN(t.max_antal)} ${esc(t.art)}</b><br>${esc(t.lok || "")}<br><small>${KAT[t.kategori] || "Alm."} · ${esc(t.dato)}` +
        (t.dir != null ? `<br>${fmtN(t.n_dir)} trækkende mod ${compass(t.dir)} (${t.dir}°)` : "") + `</small><br><a href="#/traad/${t.id}">Åbn tråd →</a>`;
      if (t.dir != null) {
        const size = Math.round(Math.min(36, (t.kategori === "alm" ? 12 : 16) + 5 * Math.log10(t.n_dir + 1)));
        L.marker([t.lat, t.lon], { opacity: old ? .55 : 1, zIndexOffset: order[t.kategori] * 1000,
          icon: L.divIcon({ className: "mig-arrow", html: arrowSvg(t.dir, size, color), iconSize: [size, size], iconAnchor: [size / 2, size / 2] }) })
          .bindPopup(popup).addTo(obsLayer);
      } else {
        const r = { SU: 9, SUB: 7, bemaerk: 5, alm: 4 }[t.kategori];
        L.circleMarker([t.lat, t.lon], { renderer, radius: r, color: "#fff", weight: 1.5, fillColor: color, fillOpacity: old ? .5 : .95, opacity: old ? .5 : 1 })
          .bindPopup(popup).addTo(obsLayer);
      }
    }
    info.textContent = `${fmtN(shown.length)} tråde · ${fmtN(shown.filter(t => t.dir != null).length)} med trækretning`;
  }

  function update() { save(); drawChips(); draw(); }

  async function load() {
    try {
      const all = await api(`/api/map/days?days=${st.days === "1" ? 1 : 2}`);
      const today = all.reduce((m, t) => t.dato > m ? t.dato : m, "");
      items = st.days === "y" ? all.filter(t => t.dato !== today) : all;
    } catch { items = []; }
    drawChips(); draw();
  }

  async function wind() {
    windLayer.clearLayers();
    if (!st.wind) return;
    try {
      for (const s of await api("/api/weather/current")) {
        if (s.dir == null) continue;
        const icon = L.divIcon({ className: "wind-arrow", iconSize: [34, 34], html: `<div style="text-align:center;line-height:1">${windArrow(s.dir, 22)}<div style="font-size:10px;font-weight:700;color:var(--text)">${Math.round(s.speed)}</div></div>` });
        L.marker([s.lat, s.lon], { icon, keyboard: false }).addTo(windLayer)
          .bindPopup(`Station ${esc(s.id)}<br>${compass(s.dir)} ${s.speed?.toFixed(1)} m/s${s.gust ? ` (vindstød ${s.gust.toFixed(1)})` : ""}<br>${s.temp != null ? s.temp.toFixed(1) + " °C" : ""}`);
      }
    } catch { /* DMI utilgængelig */ }
  }

  await Promise.all([load(), wind()]);
  let t;
  const off = live.on("feed", () => { if (st.days !== "y") { clearTimeout(t); t = setTimeout(load, 1500); } });
  return () => { off(); clearTimeout(t); map.remove(); };
}
