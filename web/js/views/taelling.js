// Spontantælling og FiNN-tælling: stå stille i fem minutter og indtast alt, hvad du ser og hører (komplet artsliste).
// Her vælges sted (GPS eller kortet), lokalitet og tidspunkt; tællingen gemmes som en tur med DOFbasens metode
// 98 (Spontantælling) / 99 (FiNN-tælling) og åbnes på turens observationsside med nedtælling (tur-view.js).
// FiNN-områderne og -punkterne kommer fra DOFbasen (server/portal/services/finn.py) og gemmes på enheden.
import { api, h, icon, toast, todayISO, state, requireLogin } from "../core.js";
import { baseMap, leaflet } from "../maps.js";
import { blankTrip, tripStore, tripHash, nowHM, addMinutes, nearestLocations, findLocations, loadRecent, meters, markCountStart, SPONTAN, FINN, COUNT_MIN } from "../tur-data.js";
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
// Afstand til et område: 0 inde i det, ellers til det nærmeste hjørne af grænsen
const siteDist = (pos, s) => insideRing(pos, s.ring) ? 0 : Math.min(...s.ring.map(p => meters(pos, p)));
// Felt med knapper: <div>, ikke field()s <label> – en label sender ethvert klik videre til den første knap
const group = (label, el) => h("div", { class: "field", role: "group" }, h("span", {}, label), el);
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
  const areaLayer = L.layerGroup().addTo(map), dotLayer = L.layerGroup().addTo(map);
  if (isFinn) {
    for (const s of finn.sites) {
      L.polygon(s.ring, { color: orange, fillColor: orange, weight: 2.5, fillOpacity: 0.22 })
        .bindTooltip(s.title, { permanent: true, direction: "center", className: "finn-label" }).addTo(areaLayer);
      // Langt ude (små områder er usynlige): en orange prik midt i hvert område
      L.circleMarker(ringCenter(s.ring), { radius: 5, color: "#fff", weight: 1.5, fillColor: orange, fillOpacity: 1 })
        .bindTooltip(`${s.title} (FiNN-område)`).on("click", () => map.fitBounds(L.polygon(s.ring).getBounds(), { padding: [20, 20] })).addTo(dotLayer);
    }
    const zoomed = () => {
      const z = map.getZoom();
      if (z >= 12) { dotLayer.remove(); if (!map.hasLayer(areaLayer)) areaLayer.addTo(map); }
      else { if (!map.hasLayer(dotLayer)) dotLayer.addTo(map); }
      mapEl.classList.toggle("finn-labels-off", z < 13);  // navnene først, når man er tæt på
    };
    map.on("zoomend", zoomed); zoomed();
    for (const p of finn.points.filter(p => p.type !== "management")) {  // forvaltningspunkterne vises ikke
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
      const m = S.site ? siteDist(S.pos, S.site) : null;
      if (S.site && m === 0) lines.unshift(h("div", { class: "notice" }, h("b", {}, S.site.title), " – du står i FiNN-området."));
      else if (S.site) lines.unshift(h("div", { class: "notice warn" }, h("b", {}, S.site.title), ` er valgt, men du står uden for området (ca. ${fmtM(m)}).`));
      else lines.unshift(h("div", { class: "notice warn" }, "Du står uden for FiNN-områderne (orange på kortet). Vælg et område i listen."));
      if (S.point) lines.push(h("small", {}, `${S.point.m <= POINT_NEAR_M ? "Ved" : "Nærmeste"} FiNN-${TYPE[S.point.p.type] || "punkt"}: ${fmtM(S.point.m)}`));
    }
    placeEl.replaceChildren(...lines);
  };

  function setPos(pos, acc, center) {
    S.pos = pos.map(v => Math.round(v * 1e6) / 1e6); S.acc = acc;
    you ? you.setLatLng(S.pos) : (you = L.circleMarker(S.pos, { radius: 7, color: "#fff", weight: 2, fillColor: "#1a73e8", fillOpacity: 1 }).addTo(map));
    if (acc) { accC ? accC.setLatLng(S.pos).setRadius(acc) : (accC = L.circle(S.pos, { radius: acc, color: "#1a73e8", weight: 1, fillOpacity: 0.08 }).addTo(map)); }
    else if (accC) { accC.remove(); accC = null; }
    if (isFinn) {
      // De fem nærmeste FiNN-områder (det, du står i, først og valgt)
      S.near = finn.sites.map(s => ({ s, m: siteDist(S.pos, s) })).sort((a, b) => a.m - b.m).slice(0, 5);
      const here = S.near[0]?.m === 0 ? S.near[0].s : null;
      if (here || !S.siteManual) { S.site = here; S.siteManual = false; }
      drawSites();
      const pts = finn.points.filter(p => p.type !== "management").map(p => ({ p, m: meters(S.pos, [p.lat, p.lon]) })).sort((a, b) => a.m - b.m);
      S.point = pts[0] && pts[0].m < 5000 ? pts[0] : null;
    }
    if (center) {
      map.invalidateSize();  // positionen kan komme, før kortet har fået sin størrelse – så bliver udsnittet forkert
      if (isFinn && !S.site && finn.sites.length) {
        // Uden for områderne: vis både dig og det nærmeste område (og et par til, hvis de ligger tæt)
        const near = finn.sites.map(s => ({ s, m: meters(S.pos, ringCenter(s.ring)) })).sort((a, b) => a.m - b.m).slice(0, 3)
          .filter((x, i) => i === 0 || x.m < 15000);
        const b = L.latLngBounds([S.pos]);
        near.forEach(x => b.extend(L.polygon(x.s.ring).getBounds()));
        map.fitBounds(b, { padding: [24, 24], maxZoom: 16 });
      } else if (isFinn && S.site) map.fitBounds(L.latLngBounds([S.pos]).extend(L.polygon(S.site.ring).getBounds()), { padding: [24, 24], maxZoom: 16 });
      else map.setView(S.pos, 15);
    }
    drawPlace();
    loadLocs();
  }
  const locate = () => navigator.geolocation?.getCurrentPosition(
    (p) => setPos([p.coords.latitude, p.coords.longitude], p.coords.accuracy, true),
    (err) => { toast("Position ikke tilgængelig: " + err.message, "err"); drawPlace(); },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });

  // --- FiNN-tælling: listen med de fem nærmeste FiNN-områder; tryk for at vælge og se området på kortet
  const siteList = h("div", { class: "lok-list", role: "radiogroup", "aria-label": "FiNN-område" });
  function drawSites() {
    if (!isFinn) return;
    if (!S.near?.length) { siteList.replaceChildren(h("small", { class: "muted" }, S.pos ? "Ingen FiNN-områder fundet." : "Finder din position …")); return; }
    siteList.replaceChildren(...S.near.map(({ s, m }) => h("button", {
      type: "button", role: "radio", "aria-checked": String(s === S.site), class: `lok-opt${s === S.site ? " on" : ""}`,
      onclick: () => { S.site = s; S.siteManual = true; drawSites(); drawPlace();
        map.fitBounds(L.latLngBounds([S.pos]).extend(L.polygon(s.ring).getBounds()), { padding: [24, 24], maxZoom: 16 }); } },
      h("span", {}, s.title), h("small", { class: "muted" }, m === 0 ? "du står her" : fmtM(m)))));
  }

  // --- lokalitet: en liste med de fem nærmeste (den første – den, du står i – vælges); søg, hvis ingen passer.
  // Lokaliteterne tegnes ikke på kortet – der er kun FiNN-områderne.
  const lokSel = h("div", { class: "lok-list", role: "radiogroup", "aria-label": "Lokalitet" });
  const lokHead = h("small", { class: "muted" }, "Vælg lokalitet …");
  let lokRows = [];
  const where = (l) => l.inde ? "du står her" : l.km != null ? `${String(l.km).replace(".", ",")} km` : l.kommune || "";
  const drawLoks = () => lokSel.replaceChildren(...lokRows.map(l => h("button", {
    type: "button", role: "radio", "aria-checked": String(l.loknr === S.loknr), class: `lok-opt${l.loknr === S.loknr ? " on" : ""}`,
    onclick: () => pick(l.loknr) }, h("span", {}, l.navn), h("small", { class: "muted" }, where(l)))));
  const fill = (rows, head, n = 5) => { lokRows = rows.slice(0, n); lokHead.textContent = head; drawLoks(); };
  const pick = (loknr) => {
    S.loknr = loknr || null; S.lok = lokRows.find(l => l.loknr === S.loknr)?.navn || null;
    lokSel.classList.remove("invalid"); drawLoks();
  };
  let lokReq = 0;
  async function loadLocs() {
    const n = ++lokReq;
    try {
      const rows = await nearestLocations(S.pos[0], S.pos[1], 12);
      if (n !== lokReq) return;
      if (!rows.length) return;  // behold listen, der står
      fill(rows, isFinn ? "Nærmeste lokalitet – søg for at vælge en anden" : "De fem nærmeste lokaliteter", isFinn ? 1 : 5);
      pick(rows[0]?.loknr);
    } catch (err) { toast(err.message, "err"); }
  }
  const lokQ = h("input", { type: "search", placeholder: "Søg lokalitet …", autocomplete: "off" });
  let lokT;
  lokQ.oninput = () => { clearTimeout(lokT); lokT = setTimeout(async () => {
    const q = lokQ.value.trim(); if (q.length < 2) return;
    try { const rows = await findLocations(q); fill(rows, "Søgeresultater", 10); } catch (err) { toast(err.message, "err"); }
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
    if (isFinn && !(S.site && siteDist(S.pos, S.site) === 0) && !confirm("Du er tilsyneladende ved at starte en FiNN-tælling uden for et FiNN-område.\n\nØnsker du alligevel at starte en FiNN-tælling her?")) return;
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
      isFinn ? h("li", {}, "Tæl inden for et FiNN-område (orange). Fordel gerne dine tællinger på FiNN-referencepunkterne (o): blå er foreslået af frivillige, røde af forskere.") : null,
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
        isFinn ? group("FiNN-område – de fem nærmeste", siteList) : null,
        group("Lokalitet i DOFbasen", h("div", { class: "stack", style: { gap: "8px" } }, lokHead, lokSel, lokQ))),
      h("div", { class: "card stack" },
        h("label", { class: "check" }, paper, h("span", {}, "Jeg har talt på papir og vil indtaste bagefter")), paperBox,
        startBtn)));
  drawPlace(); drawSites();
  // Indtil positionen er fundet: dine seneste lokaliteter, så listen aldrig er tom
  loadRecent().then(r => { if (!S.pos && r.locations?.length) fill(r.locations, "Dine seneste lokaliteter"); }).catch(() => {});
  locate();
  return () => { map.remove(); };
}
