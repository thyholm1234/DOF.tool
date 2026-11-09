// Spontantælling og FiNN-tælling: stå stille i fem minutter og indtast alt, hvad du ser og hører (komplet artsliste).
// Her vælges sted (GPS eller kortet), lokalitet og tidspunkt; tællingen gemmes som en tur med DOFbasens metode
// 98 (Spontantælling) / 99 (FiNN-tælling) og åbnes på turens observationsside med nedtælling (tur-view.js).
// FiNN-områderne og -punkterne kommer fra DOFbasen (server/portal/services/finn.py) og gemmes på enheden.
import { api, h, icon, toast, todayISO, state, requireLogin } from "../core.js";
import { baseMap, leaflet } from "../maps.js";
import { blankTrip, tripStore, tripHash, nowHM, addMinutes, nearestLocations, findLocations, meters, markCountStart, SPONTAN, FINN, COUNT_MIN } from "../tur-data.js";
import { cachedApi } from "../offline.js";
import { field, fail } from "../tur-ui.js";

const POINT_NEAR_M = 25;  // FiNN-punkt «ved»: inden for 25 m (som i DOF's app)
const TYPE = { management: "forvaltningspunkt", reference: "referencepunkt" };
const SOURCE = { researcher: "foreslået af forskere", volunteer: "foreslået af frivillige" };

function insideRing([y, x], ring) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [y1, x1] = ring[i], [y2, x2] = ring[j];
    if ((y1 > y) !== (y2 > y) && x < (x2 - x1) * (y - y1) / (y2 - y1) + x1) hit = !hit;
  }
  return hit;
}
const ringCenter = (ring) => [ring.reduce((s, p) => s + p[0], 0) / ring.length, ring.reduce((s, p) => s + p[1], 0) / ring.length];
const fmtM = (m) => m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1).replace(".", ",")} km`;

export async function render(page, [kind]) {
  if (requireLogin(page)) return;
  const isFinn = kind === "finn";
  await leaflet();
  let finn = { sites: [], points: [] };
  if (isFinn) {
    try { finn = await cachedApi("finn", "/api/trips/finn"); } catch { toast("FiNN-områderne kunne ikke hentes – du kan stadig tælle", "err"); }
  }
  const S = { pos: null, acc: null, loknr: null, lok: null, site: null, point: null, paper: false, dato: todayISO(), fra: nowHM() };

  // --- kort: din position (sigtekorn + nøjagtighed) og FiNN-områder/-punkter
  const mapEl = h("div", { class: "map", style: { height: "min(48vh, 380px)" } });
  const map = baseMap(mapEl, { center: [56.1, 10.8], zoom: 7 });
  let you = null, accC = null;
  const orange = "#ff8800";
  if (isFinn) {
    for (const s of finn.sites) {
      L.polygon(s.ring, { color: orange, fillColor: orange, weight: 2, fillOpacity: 0.15 }).bindTooltip(`${s.title} (FiNN-område)`).addTo(map);
    }
    for (const p of finn.points) {
      const color = p.source === "researcher" ? "#e53935" : "#1f7aaf";
      const html = `<span style="color:${color};font:700 18px/1 system-ui;text-shadow:0 0 2px #fff,0 0 2px #fff">${p.type === "management" ? "×" : "o"}</span>`;
      L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "", html, iconSize: [14, 14], iconAnchor: [7, 9] }) })
        .bindTooltip(`FiNN-${TYPE[p.type] || "punkt"}, ${SOURCE[p.source] || ""}`).addTo(map);
    }
  }
  map.on("click", (e) => setPos([e.latlng.lat, e.latlng.lng], null, false));

  // --- status for stedet
  const placeEl = h("div", { class: "stack", style: { gap: "4px" } });
  const drawPlace = () => {
    if (!S.pos) { placeEl.replaceChildren(h("small", { class: "muted" }, "Finder din position … Du kan også trykke på kortet, hvor du står.")); return; }
    const lines = [h("small", { class: "muted" }, `${S.pos[0].toFixed(5)}, ${S.pos[1].toFixed(5)}${S.acc ? ` · ±${Math.round(S.acc)} m` : ""}`)];
    if (isFinn) {
      if (S.site) lines.unshift(h("div", { class: "notice" }, h("b", {}, S.site.title), " – du står i FiNN-området."));
      else {
        const near = finn.sites.map(s => ({ s, m: meters(S.pos, ringCenter(s.ring)) })).sort((a, b) => a.m - b.m)[0];
        lines.unshift(h("div", { class: "notice warn" }, "Du står uden for FiNN-områderne (orange på kortet)",
          near ? `. Nærmeste: ${near.s.title}, ca. ${fmtM(near.m)}.` : "."));
      }
      if (S.point) lines.push(h("small", {}, `${S.point.m <= POINT_NEAR_M ? "Ved" : "Nærmeste"} FiNN-${TYPE[S.point.p.type] || "punkt"}: ${fmtM(S.point.m)}`));
    }
    placeEl.replaceChildren(...lines);
  };

  function setPos(pos, acc, center) {
    S.pos = pos.map(v => Math.round(v * 1e6) / 1e6); S.acc = acc;
    you ? you.setLatLng(S.pos) : (you = L.circleMarker(S.pos, { radius: 7, color: "#fff", weight: 2, fillColor: "#1a73e8", fillOpacity: 1 }).addTo(map));
    if (acc) { accC ? accC.setLatLng(S.pos).setRadius(acc) : (accC = L.circle(S.pos, { radius: acc, color: "#1a73e8", weight: 1, fillOpacity: 0.08 }).addTo(map)); }
    else if (accC) { accC.remove(); accC = null; }
    if (center) map.setView(S.pos, 16);
    if (isFinn) {
      S.site = finn.sites.find(s => insideRing(S.pos, s.ring)) || null;
      const pts = finn.points.map(p => ({ p, m: meters(S.pos, [p.lat, p.lon]) })).sort((a, b) => a.m - b.m);
      S.point = pts[0] && pts[0].m < 5000 ? pts[0] : null;
    }
    drawPlace();
    loadLocs();
  }
  const locate = () => navigator.geolocation?.getCurrentPosition(
    (p) => setPos([p.coords.latitude, p.coords.longitude], p.coords.accuracy, true),
    (err) => { toast("Position ikke tilgængelig: " + err.message, "err"); drawPlace(); },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });

  // --- lokalitet: de nærmeste (den første vælges); søg, hvis ingen passer
  const lokSel = h("select", { "aria-label": "Lokalitet" }, h("option", { value: "" }, "Vælg lokalitet …"));
  const known = new Map();
  const fill = (rows, head) => {
    rows.forEach(l => known.set(l.loknr, l.navn));
    lokSel.replaceChildren(h("option", { value: "" }, head), ...rows.map(l => h("option", { value: l.loknr },
      [l.navn, l.inde ? "her" : l.km != null ? `${String(l.km).replace(".", ",")} km` : l.kommune].filter(Boolean).join(" · "))));
  };
  const pick = (loknr) => { S.loknr = loknr || null; S.lok = known.get(S.loknr) || null; lokSel.value = S.loknr ? String(S.loknr) : ""; lokSel.classList.remove("invalid"); };
  lokSel.onchange = () => pick(Number(lokSel.value));
  let lokReq = 0;
  async function loadLocs() {
    const n = ++lokReq;
    try {
      const rows = await nearestLocations(S.pos[0], S.pos[1], 12);
      if (n !== lokReq) return;
      fill(rows, "Nærmeste lokaliteter …");
      pick(rows[0]?.loknr);
    } catch (err) { toast(err.message, "err"); }
  }
  const lokQ = h("input", { type: "search", placeholder: "Søg lokalitet …", autocomplete: "off" });
  let lokT;
  lokQ.oninput = () => { clearTimeout(lokT); lokT = setTimeout(async () => {
    const q = lokQ.value.trim(); if (q.length < 2) return;
    try { const rows = await findLocations(q); fill(rows, "Vælg blandt søgeresultaterne …"); } catch (err) { toast(err.message, "err"); }
  }, 250); };

  // --- tidspunkt: nu, eller dato og præcist starttidspunkt (talt på papir)
  const dato = h("input", { type: "date", max: todayISO(), value: S.dato, oninput: (e) => { S.dato = e.target.value; } });
  const fra = h("input", { type: "time", value: S.fra, oninput: (e) => { S.fra = e.target.value; } });
  const paperBox = h("div", { class: "form-grid two", style: { display: "none" } }, field("Dato", dato), field("Starttidspunkt", fra));
  const paper = h("input", { type: "checkbox", onchange: (e) => { S.paper = e.target.checked; paperBox.style.display = S.paper ? "" : "none"; } });

  // --- start
  const startBtn = h("button", { class: "btn primary", type: "button", style: { height: "48px", fontSize: "1.05rem" },
    html: `${icon("play")} ${S.paper ? "Indtast tællingen" : "Start tællingen"}` });
  paper.addEventListener("change", () => { startBtn.innerHTML = `${icon("play")} ${S.paper ? "Indtast tællingen" : `Start tællingen (${COUNT_MIN} min.)`}`; });
  startBtn.innerHTML = `${icon("play")} Start tællingen (${COUNT_MIN} min.)`;
  startBtn.onclick = async () => {
    if (!S.pos) return fail("Find din position, eller tryk på kortet, hvor du står", mapEl);
    if (!S.loknr) return fail("Vælg en lokalitet", lokSel);
    const start = S.paper ? S.fra : nowHM();
    const day = S.paper ? S.dato : todayISO();
    if (!day || !start) return fail("Angiv dato og præcist starttidspunkt", S.paper ? fra : null);
    if (isFinn && !S.site && !confirm("Du er tilsyneladende ved at starte en FiNN-tælling uden for et FiNN-område.\n\nØnsker du alligevel at starte en FiNN-tælling her?")) return;
    const T = blankTrip();
    Object.assign(T, {
      dato: day, loknr: S.loknr, lok: S.lok, turtidfra: start, turtidtil: addMinutes(start, COUNT_MIN),
      metode_id: isFinn ? FINN : SPONTAN, komplet: true, spontan: !isFinn, lat: S.pos[0], lon: S.pos[1],
      finn_site_id: isFinn ? S.site?.id ?? null : null,
      turnoter: isFinn && S.point && S.point.m <= POINT_NEAR_M ? `Ved FiNN-${TYPE[S.point.p.type] || "punkt"} (DOFbasen-id ${S.point.p.id}).` : "",
    });
    startBtn.disabled = true;
    // Vejret fra DMI (hvis brugeren har slået det til) – højst et par sekunder, tællingen må ikke vente
    if (state.user?.trip_weather) {
      try {
        const q = new URLSearchParams({ lat: T.lat, lon: T.lon, dato: T.dato, fra: T.turtidfra, til: T.turtidtil });
        const r = await Promise.race([api(`/api/trips/weather?${q}`), new Promise((_, rej) => setTimeout(() => rej(new Error("tid")), 2500))]);
        for (const [k, v] of Object.entries(r.values || {})) if (v != null && k in T) T[k] = v;
      } catch { /* uden vejr */ }
    }
    if (!S.paper) markCountStart(T.client_id);
    const store_ = tripStore(T, {});
    const status = await store_.saveNow();
    startBtn.disabled = false;
    if (status === "fejl") return;
    if (status === "offline") toast("Ingen forbindelse – tællingen gemmes på enheden og sendes automatisk", "ok");
    location.hash = tripHash(T);
  };

  const rules = h("details", { class: "card", style: { padding: "10px 14px" } }, h("summary", {}, h("b", {}, "Sådan tæller du")),
    h("ul", { style: { margin: "8px 0 0", paddingLeft: "1.2em" } },
      h("li", {}, `Stå stille i ${COUNT_MIN} minutter, og registrér alle fugle, du ser og hører og kan bestemme – det er en komplet artsliste.`),
      h("li", {}, "Tæl i godt vejr – undgå fx regn og tæt tåge."),
      h("li", {}, "Start ikke en tælling på grund af en sjælden eller særlig interessant fugl."),
      isFinn ? h("li", {}, "Tæl inden for et FiNN-område (orange). Fordel gerne dine tællinger på FiNN-punkterne: × er forvaltningspunkter, o er referencepunkter; blå er foreslået af frivillige, røde af forskere.") : null,
      h("li", {}, "Talt på papir? Brug uret, og angiv dato og præcist starttidspunkt nedenfor.")),
    h("p", { class: "muted", style: { fontSize: ".85rem", margin: "8px 0 0" } }, "Læs mere hos DOF: ",
      h("a", { href: isFinn ? "https://www.dof.dk/finn" : "https://www.dof.dk/fakta-om-fugle/spontantaellinger", target: "_blank", rel: "noopener" }, isFinn ? "dof.dk/finn" : "spontantællinger")));

  page.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("p", { style: { margin: 0 } }, h("a", { href: "#/indtast/ny" }, "← Indtast")),
      h("h1", { style: { marginTop: "4px" } }, isFinn ? "FiNN-tælling" : "Spontantælling"))),
    h("div", { class: "stack" },
      rules,
      h("div", { class: "card stack" },
        h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Hvor står du?"),
          h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Min position`, onclick: locate })),
        mapEl, placeEl,
        field("Lokalitet i DOFbasen", h("div", { class: "stack", style: { gap: "8px" } }, lokSel, lokQ)),
        h("small", { class: "muted", style: { marginTop: "-6px" } }, "Vælg den lokalitet, der passer bedst til tællingen.")),
      h("div", { class: "card stack" },
        h("label", { class: "check" }, paper, h("span", {}, "Jeg har talt på papir og vil indtaste bagefter")), paperBox,
        startBtn)));
  drawPlace();
  locate();
  return () => map.remove();
}
