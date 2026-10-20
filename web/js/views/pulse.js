// Puls: kommandokort over Danmark. Ved hver live-sync popper ændringerne op på kortet én ad gangen (50 → 56 bogfinker
// vises som 6 Bogfink), og anonyme birdere står som små figurer på lokaliteten i op til 15 minutter efter seneste ændring.
// Er der ikke tid til alt før næste sync, vises SU, SUB og Bemærk først og de største antal blandt resten.
import { api, h, fmtN, fmtTime, esc, KAT, tag, seg, store, todayISO, errorBox } from "../core.js";
import { baseMap } from "../maps.js";
import { live } from "../live.js";

const RANK = { SU: 3, SUB: 2, bemaerk: 1, alm: 0 };
const ACTIVE_MS = 15 * 60 * 1000;
const MAX_ON_MAP = 5;        // samtidige bobler
const MIN_GAP = 700;         // ms mellem to bobler
const SHOW_MS = { SU: 9000, SUB: 7500, bemaerk: 6000, alm: 4000 };
const KEEP_LIST = 300;

const PERSON = `<svg viewBox="0 0 24 32" width="18" height="24" aria-hidden="true"><circle cx="12" cy="6" r="5"/><path d="M3 31v-9a9 9 0 0 1 18 0v9z"/></svg>`;
const label = (it) => it.delta > 0 ? `${fmtN(it.delta)} ${it.art}` : it.art;
const threadHash = (it, day) => `#/traad/${day}/${it.artnr}/${it.loknr}`;

export async function render(view) {
  const saved = store("puls") || {};
  const st = { sort: saved.sort || "nyeste", since: null, interval: 120, lastAt: null, skipped: 0 };
  const mapEl = h("div", { class: "map full pulse-map" });
  const listEl = h("div", { class: "pulse-list" });
  const stats = { birders: h("b", { class: "num" }, "0"), last: h("b", { class: "num" }, "–"), shown: h("small", { class: "muted" }) };
  const sortBox = h("div", {});
  view.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Puls"),
      h("p", { class: "lead" }, "Danmark lige nu – nye fugle popper op, efterhånden som de bliver indtastet. Figurerne er birdere, der har indtastet inden for det sidste kvarter."))),
    h("div", { class: "pulse-stats" },
      h("span", { class: "pulse-stat" }, h("span", { class: "pulse-person", html: PERSON }), stats.birders, " aktive birdere"),
      h("span", { class: "pulse-stat" }, "Seneste sync ", stats.last), stats.shown),
    h("div", { class: "pulse-layout" }, mapEl,
      h("div", { class: "card pulse-side" }, h("div", { class: "card-head" }, h("h3", {}, "Nye fugle"), h("span", { class: "spacer" }), sortBox), listEl)));

  const map = baseMap(mapEl, { center: [56.15, 11.2], zoom: 7 });
  const birderLayer = L.layerGroup().addTo(map);
  const popLayer = L.layerGroup().addTo(map);
  const birders = new Map();  // id -> { marker, at }
  let items = [];             // til listen (nyeste først)
  let queue = [], gap = MIN_GAP, onMap = 0, timer = null, refreshing = false;

  // ------------------------------------------------ birdere: figur pr. anonym birder, falmer over 15 minutter
  const drawBirders = () => {
    const now = Date.now();
    for (const [id, b] of birders) {
      const age = now - b.at;
      if (age > ACTIVE_MS) { birderLayer.removeLayer(b.marker); birders.delete(id); continue; }
      const el = b.marker.getElement();
      if (el) el.style.opacity = String(1 - 0.65 * age / ACTIVE_MS);
    }
    stats.birders.textContent = fmtN(birders.size);
  };
  const setBirders = (rows) => {
    // Birdere serveren ikke længere sender (turen er afsluttet) fjernes
    const ids = new Set(rows.map(b => b.id));
    for (const [id, b] of birders) if (!ids.has(id)) { birderLayer.removeLayer(b.marker); birders.delete(id); }
    for (const b of rows) {
      const at = Date.parse(b.at), old = birders.get(b.id);
      if (old) { old.at = at; old.marker.setLatLng([b.lat, b.lon]); old.marker.setTooltipContent(esc(b.lok)); continue; }
      // lidt spredning, så flere birdere på samme lokalitet ikke står oven i hinanden
      const j = () => (Math.random() - 0.5) * 0.004;
      const marker = L.marker([b.lat + j(), b.lon + j()], { icon: L.divIcon({ className: "pulse-birder", html: PERSON, iconSize: [18, 24], iconAnchor: [9, 24] }), keyboard: false })
        .bindTooltip(esc(b.lok), { direction: "top", offset: [0, -22] }).addTo(birderLayer);
      birders.set(b.id, { marker, at });
    }
    drawBirders();
  };

  // ------------------------------------------------ bobler: ændringerne popper op og forsvinder igen
  const pop = (it) => {
    const k = it.kategori in RANK ? it.kategori : "alm";
    const html = `<div class="pp ${k}"><b>${esc(label(it))}</b><small>${esc(it.lok)}</small></div>`;
    const m = L.marker([it.lat, it.lon], { icon: L.divIcon({ className: "pulse-pop", html, iconSize: null }), zIndexOffset: 1000 + RANK[k] * 100 })
      .on("click", () => { location.hash = threadHash(it, it.day); }).addTo(popLayer);
    const ring = L.circleMarker([it.lat, it.lon], { radius: 6, className: `pulse-ring ${k}`, interactive: false }).addTo(popLayer);
    onMap++;
    const ms = SHOW_MS[k];
    setTimeout(() => m.getElement()?.classList.add("out"), ms - 600);
    setTimeout(() => { popLayer.removeLayer(m); popLayer.removeLayer(ring); onMap--; }, ms);
  };
  const tick = () => {
    clearTimeout(timer);
    if (!queue.length) return;
    if (onMap < MAX_ON_MAP) pop(queue.shift());
    timer = setTimeout(tick, gap);
  };
  // Hvad kan nås før næste sync? Sjældne først, derefter de største antal; resten springes over
  const plan = (fresh) => {
    const carry = queue.filter(it => RANK[it.kategori] > 0);  // ikke-viste sjældne fra sidste sync tages med
    const all = [...carry, ...fresh];
    const budget = Math.max(4, Math.floor(st.interval * 1000 * 0.9 / MIN_GAP));
    const ranked = [...all].sort((a, b) => (RANK[b.kategori] || 0) - (RANK[a.kategori] || 0) || b.delta - a.delta);
    const pick = ranked.slice(0, budget);
    st.skipped = all.length - pick.length;
    const rare = pick.filter(it => RANK[it.kategori] > 0), rest = pick.filter(it => !RANK[it.kategori]);
    for (let i = rest.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [rest[i], rest[j]] = [rest[j], rest[i]]; }
    queue = [...rare, ...rest];
    gap = Math.max(MIN_GAP, Math.min(5000, st.interval * 1000 * 0.9 / Math.max(1, queue.length)));
    stats.shown.textContent = st.skipped ? `${fmtN(st.skipped)} almindelige ændringer springes over på kortet – se listen` : "";
    tick();
  };

  // ------------------------------------------------ listen med sortering
  const SORTS = [["nyeste", "Nyeste"], ["prio", "Prioritet"], ["antal", "Antal"], ["art", "Art"]];
  const cmp = {
    nyeste: (a, b) => b.at.localeCompare(a.at) || (RANK[b.kategori] - RANK[a.kategori]),
    prio: (a, b) => (RANK[b.kategori] - RANK[a.kategori]) || b.delta - a.delta,
    antal: (a, b) => b.delta - a.delta,
    art: (a, b) => a.art.localeCompare(b.art, "da"),
  };
  const drawSort = () => sortBox.replaceChildren(seg(SORTS, st.sort, (v) => { st.sort = v; store("puls", { sort: v }); drawList(); drawSort(); }));
  const drawList = () => {
    if (!items.length) { listEl.replaceChildren(h("p", { class: "muted" }, "Venter på næste sync … nye fugle dukker op her og på kortet.")); return; }
    listEl.replaceChildren(...[...items].sort(cmp[st.sort]).slice(0, 150).map(it => h("a", { class: `pulse-item ${it.kategori}`, href: threadHash(it, it.day),
      onmouseenter: () => map.panTo([it.lat, it.lon], { animate: true }) },
      h("span", { class: "muted num" }, fmtTime(it.at)),
      h("span", {}, h("b", {}, label(it)), it.kategori !== "alm" ? tag(it.kategori, KAT[it.kategori]) : null,
        h("small", { class: "muted", style: { display: "block" } }, it.lok)))));
  };

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const d = await api(`/api/pulse${st.since ? `?since=${encodeURIComponent(st.since)}` : ""}`);
      st.interval = d.interval;
      setBirders(d.birders);
      if (d.batches.length) {
        const day = todayISO();
        const fresh = d.batches.flatMap(b => b.items.map(it => ({ ...it, at: b.at, day }))).reverse();
        const first = !st.since;
        st.since = d.batches[d.batches.length - 1].at;
        stats.last.textContent = fmtTime(st.since);
        items = [...fresh, ...items].slice(0, KEEP_LIST);
        drawList();
        plan(first ? fresh.filter(it => it.at === st.since) : fresh);  // første visning: kun seneste sync på kortet
      } else if (!st.since) {
        st.since = d.now;
        drawList();
      }
    } catch (err) { if (!items.length) listEl.replaceChildren(errorBox(err)); }
    finally { refreshing = false; }
  }

  drawSort();
  await refresh();
  const off = live.on("feed", (d) => { if (d.new || d.changed || d.portal) refresh(); });
  const poll = setInterval(refresh, 60000);       // hvis SSE er nede
  const fade = setInterval(drawBirders, 20000);
  return () => { off(); clearInterval(poll); clearInterval(fade); clearTimeout(timer); map.remove(); };
}
