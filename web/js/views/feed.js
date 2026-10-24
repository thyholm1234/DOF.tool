import { statusBadge, certaintyTag } from "../reportui.js";
import { api, state, h, icon, fmtN, fmtDate, ago, doyLabel, tag, todayISO, params, setParams, loadRef, store, empty, errorBox, modal, toast, KAT } from "../core.js";
import { live } from "../live.js";
import { isApp, currentPosition } from "../native.js";

const LEVELS = [["SU", "SU"], ["SUB", "SUB+"], ["bemaerk", "Bemærk+"], ["local", "Lokale hits"], ["pheno", "Fænologi"], ["all", "Alle"]];

export async function scopeOptions() {
  await loadRef();
  const opts = [];
  if (state.user) opts.push(["mine", "★ Mine filtre (alarmregler)"]);
  opts.push(["dk", "Hele Danmark"]);
  const prefs = state.user?.prefs || {};
  const mine = new Set((prefs.afdelinger || []).map(String));
  const afd = [...state.afdelinger].sort((a, b) => mine.has(String(b.id)) - mine.has(String(a.id)));
  for (const a of afd) opts.push([`afd:${a.id}`, a.navn]);
  for (const k of prefs.kommuner || []) {
    const kk = state.kommuner.find(x => x.kode === k);
    if (kk) opts.push([`kom:${k}`, `${kk.navn} Kommune`]);
  }
  if (state.user) {
    try {
      for (const a of await api("/api/areas")) opts.push([a.scope.startsWith("area:") ? `area:${a.id}` : a.scope, `${a.ref === "me" ? "📍" : "★"} ${a.name}`]);
    } catch { /* ignore */ }
  }
  return opts;
}

export async function render(view) {
  const p = params();
  // v2: "Mine filtre" er ny standard for indloggede – ældre gemte valg nulstilles én gang
  const saved = (store("feed") || {}).v === 2 ? store("feed") : {};
  const st = {
    date: p.date || todayISO(),
    level: p.level || saved.level || state.user?.prefs?.feed_level || "bemaerk",
    scope: p.scope || saved.scope || state.user?.prefs?.feed_scope || (state.user ? "mine" : "dk"),
    autoScope: !p.scope && !saved.scope && !state.user?.prefs?.feed_scope,
    localMax: Number(p.n || saved.localMax || state.user?.prefs?.local_max || 10),
    prevScope: saved.prevScope || "dk",
    onlySU: !!saved.onlySU, sort: saved.sort || "nyeste", comments: !!saved.comments,
  };
  if (st.level === "all" && st.scope === "dk") st.level = "bemaerk";

  const listEl = h("div", { class: "threads" });
  const sideEl = h("div", { class: "stack" });
  const updatedEl = h("small", { class: "muted" });
  const scopeSel = h("select", { "aria-label": "Område", style: { width: "auto", maxWidth: "240px" } });
  // Må krympe og bryde linjen på smalle skærme – ellers skubber den værktøjslinjen (og siden) ud over kanten
  // Én linje uanset tallet: tallet har fast plads til tre cifre, og slideren krymper i stedet for at hoppe ned
  const localWrap = h("label", { class: "row hide", style: { fontSize: ".85rem", gap: ".4rem", flex: "1 1 240px", minWidth: 0, flexWrap: "nowrap", whiteSpace: "nowrap" } });
  const localVal = h("b", { class: "num", style: { display: "inline-block", minWidth: "3ch", textAlign: "center" } }, st.localMax);
  // Trin i stedet for 1–100: de lave tal (sjældne arter) er dem, der betyder noget, og skal kunne rammes på en telefon
  const LOCAL_STEPS = [1, 2, 3, 4, 5, 7, 10, 15, 20, 30, 50, 100];
  const stepOf = (n) => { const i = LOCAL_STEPS.findIndex(x => x >= n); return i < 0 ? LOCAL_STEPS.length - 1 : i; };
  const localRange = h("input", { type: "range", min: 0, max: LOCAL_STEPS.length - 1, step: 1, value: stepOf(st.localMax), "aria-label": "Færre end antal fund på lokaliteten",
    style: { flex: "1 1 90px", width: "auto", minWidth: "60px", maxWidth: "200px" } });
  localWrap.title = "Lokalt hit: arten er fundet færre gange end dette på lokaliteten gennem alle år (estimerede fund – en fugl der bliver i flere dage tæller én gang)";
  localWrap.append("Færre end", localVal, "fund på lokaliteten", localRange);

  // Værktøjslinje som i DOF.not: to-tilstandsknapper; kategori og område kun når egne filtre er slået fra
  const toggles = h("div", { class: "toolbar" });
  const chips = h("div", { class: "chips wrap", style: { marginTop: 0 } });
  const filterRow = h("div", { class: "row between wrap", style: { gap: "8px" } }, chips, h("div", { class: "row", style: { gap: "8px", minWidth: 0, maxWidth: "100%" } }, localWrap, scopeSel,
    h("button", { class: "btn sm", type: "button", title: "Filtrér på en radius om din position", html: `${icon("pin")} Nær mig`, onclick: () => nearMe({
      here: (km) => { setHere(km); },
      saved: async (area) => {
        scopeSel.replaceChildren(...(await scopeOptions()).filter(o => o[0] !== "mine").map(([v, l]) => h("option", { value: v }, l)));
        st.scope = st.prevScope = area.scope; st.autoScope = false; scopeSel.value = st.scope; drawChips(); load();
      } }) })));
  // Midlertidigt filter: enhedens position som midtpunkt for en radius – intet område oprettes
  const hereKm = (v) => v.startsWith("here:") ? Number(v.slice(5)) : null;
  const hereOption = (km) => {
    scopeSel.querySelectorAll("option[value^='here:']").forEach(o => o.remove());
    scopeSel.prepend(h("option", { value: `here:${km}` }, `📍 Min position (${km} km)`));
  };
  const setHere = (km) => {
    hereOption(km); st.scope = st.prevScope = `here:${km}`; st.autoScope = false; scopeSel.value = st.scope; drawChips(); load();
  };
  const tw = (label, on, onclick, title) => h("button", { type: "button", class: `twostate ${on ? "on" : ""}`, title, onclick }, label);
  const drawChips = () => {
    const mine = st.scope === "mine";
    toggles.replaceChildren(
      state.user ? tw("Mine filtre", mine, () => {
        if (mine) st.scope = st.prevScope || "dk"; else { st.prevScope = st.scope; st.scope = "mine"; }
        st.autoScope = false; scopeSel.value = st.scope; drawChips(); load();
      }, mine ? "Viser dine alarmregler – klik for at vælge kategori og område selv" : "Brug dine alarmregler som filter") : null,
      tw("Kun SU", st.onlySU, () => { st.onlySU = !st.onlySU; drawChips(); draw(); }, "Vis kun SU-arter"),
      tw(st.sort === "nyeste" ? "Nyeste" : "Alfabet", true, () => { st.sort = st.sort === "nyeste" ? "alfabet" : "nyeste"; drawChips(); draw(); }, "Skift sortering"),
      tw("Indlæg", st.comments, () => { st.comments = !st.comments; drawChips(); draw(); }, "Tråde med kommentarer øverst"),
      h("span", { class: "spacer" }), updatedEl);
    filterRow.classList.toggle("hide", mine);
    chips.replaceChildren(...LEVELS.map(([v, l]) => h("button", {
      class: `chip ${st.level === v ? "on" : ""}`, onclick: () => {
        if (v === "all" && st.scope === "dk") { toast("Vælg et område for at se alle observationer"); return; }
        st.level = v; drawChips(); load();
      } }, l)));
    saveState();
  };
  const saveState = () => store("feed", { v: 2, level: st.level, scope: st.autoScope ? "" : st.scope, localMax: st.localMax,
    prevScope: st.prevScope, onlySU: st.onlySU, sort: st.sort, comments: st.comments });

  const dateInput = h("input", { type: "date", value: st.date, max: todayISO(), style: { width: "auto" }, onchange: (e) => { st.date = e.target.value; load(); } });
  const shiftDay = (n) => { const d = new Date(st.date + "T12:00:00"); d.setDate(d.getDate() + n); const iso = d.toISOString().slice(0, 10); if (iso > todayISO()) return; st.date = iso; dateInput.value = iso; load(); };

  view.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Hvad sker der nu?"), h("p", { class: "lead" }, "Live fra DOFbasen – samlet i tråde pr. art og lokalitet.")),
      h("span", { class: "spacer" }),
      h("div", { class: "row" },
        h("button", { class: "btn sm ghost", onclick: () => shiftDay(-1), "aria-label": "Forrige dag", html: icon("prev") }), dateInput,
        h("button", { class: "btn sm ghost", onclick: () => shiftDay(1), "aria-label": "Næste dag", html: icon("next") }),
        h("button", { class: "btn sm", onclick: nearby, html: `${icon("pin")} Tæt på mig` }))),
    h("div", { class: "feed-layout" },
      h("div", { class: "stack" },
        h("div", { class: "card toolbox" }, toggles, filterRow),
        listEl),
      sideEl));

  const opts = (await scopeOptions()).filter(o => o[0] !== "mine");
  for (const [v, l] of opts) scopeSel.append(h("option", { value: v, selected: v === st.scope }, l));
  if (hereKm(st.scope)) hereOption(hereKm(st.scope));
  else if (st.scope !== "mine" && !opts.some(o => o[0] === st.scope)) st.scope = "dk";
  drawChips();
  scopeSel.addEventListener("change", () => { st.scope = scopeSel.value; st.prevScope = st.scope; st.autoScope = false; if (st.level === "all" && st.scope === "dk") st.level = "bemaerk"; drawChips(); load(); });
  localRange.addEventListener("input", () => { localVal.textContent = LOCAL_STEPS[localRange.value]; });
  localRange.addEventListener("change", () => { st.localMax = LOCAL_STEPS[localRange.value]; load(); });

  let threads = [];
  let known = new Set();
  let first = true;

  function draw() {
    const items = threads.filter(t => !st.onlySU || t.kategori === "SU");
    if (st.sort === "alfabet") items.sort((a, b) => (a.art || "").localeCompare(b.art || "", "da"));
    if (st.comments) items.sort((a, b) => (b.n_comments > 0) - (a.n_comments > 0));
    if (!items.length) { listEl.replaceChildren(empty("Ingen tråde med dette filter", st.date === todayISO() ? "Listen opdateres automatisk, når nye observationer kommer ind." : "")); return; }
    listEl.replaceChildren(...items.map(t => threadCard(t, st.scope === "mine" ? (t.local_hit ? "local" : "") : st.level, !first && !known.has(t.id + t.max_antal))));
    known = new Set(threads.map(t => t.id + t.max_antal));
    first = false;
  }

  async function load() {
    const mine = st.scope === "mine";
    setParams({ date: st.date === todayISO() ? "" : st.date, level: mine ? "" : st.level, scope: st.autoScope || st.scope === "dk" ? "" : st.scope,
      n: !mine && st.level === "local" ? st.localMax : "" });
    saveState();
    localWrap.classList.toggle("hide", mine || st.level !== "local");
    if (first) listEl.replaceChildren(...Array.from({ length: 5 }, () => h("div", { class: "skeleton" })));
    try {
      let scope = st.scope;
      if (hereKm(scope)) {
        // enhedens position nu (hentes igen højst hvert 2. minut); kan den ikke findes, vises hele landet
        try { const p = await devicePosition(); scope = `near:${p.lat},${p.lon},${hereKm(scope)}`; }
        catch (err) { toast(err.message, "err"); st.scope = "dk"; scopeSel.value = "dk"; drawChips(); scope = "dk"; }
      }
      const q = new URLSearchParams({ date: st.date, level: st.level, scope, local_max: st.localMax });
      const [data, sum] = await Promise.all([api(`/api/feed?${q}`), api(`/api/feed/summary?date=${st.date}`)]);
      if (mine && data.n_rules === 0) {
        // Ingen regler endnu: standardvisningen falder tilbage til hele landet, et aktivt valg viser en vejledning
        if (st.autoScope) { st.scope = "dk"; scopeSel.value = "dk"; st.autoScope = false; drawChips(); return load(); }
        listEl.replaceChildren(empty("Du har ingen aktive alarmregler", "Opret regler under Alarmer – så viser forsiden det, du får besked om."),
          h("p", { style: { textAlign: "center" } }, h("a", { class: "btn primary", href: "#/alarmer" }, "Opret alarmregel")));
        drawSide(sum); updatedEl.textContent = ""; return;
      }
      threads = data.threads;
      draw();
      drawSide(sum);
      updatedEl.textContent = `${threads.length} tråde${sum.last_update ? " · hentet " + ago(sum.last_update) : ""}`;
    } catch (err) { listEl.replaceChildren(errorBox(err)); }
  }

  function drawSide(sum) {
    sideEl.replaceChildren(
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Største træk i dag")),
        sum.migration.length ? h("ul", { class: "mini-list" }, ...sum.migration.map(m => h("li", {},
          h("a", { href: `#/lok/${m.loknr}` }, m.lok || "Ukendt"),
          h("span", { class: "muted num", title: (m.top || []).join(", ") }, `${fmtN(m.n)} ind.`)))) : h("p", { class: "muted" }, "Ingen trækobservationer endnu.")),
      newsCard);
  }
  const newsCard = h("div", {});
  api("/api/news?limit=3").then(n => {
    if (!n.length) return;
    newsCard.replaceChildren(h("div", { class: "card" }, h("h3", {}, "Nyt fra portalen"),
      ...n.map(x => h("div", { class: "mt" }, h("b", {}, x.title), h("p", { class: "muted", style: { margin: 0 } }, fmtDate(x.created_at)), h("p", {}, x.body)))));
  }).catch(() => {});

  await load();
  let timer;
  const off = live.on("feed", (d) => {
    if (st.date !== todayISO() || d.type !== "feed") return;
    clearTimeout(timer); timer = setTimeout(load, 800);
  });
  const poll = setInterval(() => { if (st.date === todayISO() && document.visibilityState === "visible") load(); }, 120000);
  return () => { off(); clearInterval(poll); clearTimeout(timer); };
}

export function threadCard(t, level, fresh = false) {
  const kat = level === "local" && !["SU", "SUB"].includes(t.kategori) ? "local" : t.kategori;
  const last = t.last || {};
  return h("a", { class: `thread ${kat} ${fresh ? "fresh" : ""}`, href: `#/traad/${t.id}` },
    h("span", { class: "bar" }),
    h("div", {},
      h("div", { class: "title" }, h("span", { class: "count" }, fmtN(t.max_antal)), h("span", {}, t.art)),
      h("div", { class: "meta" }, h("span", { class: "lok", title: t.lok || "" }, t.lok || "Ukendt lokalitet"), t.kommune ? h("span", { class: "muted" }, `\u00a0· ${t.kommune}`) : null),
      h("div", { class: "meta2" }, [last.adf, last.observer, last.tid].filter(Boolean).join(" · ")),
      t.pheno?.fra ? h("div", { class: "meta2", title: t.pheno.kilde === "dof" ? "Efter DOFbasens bemærk-vindue" : "Beregnet af observationerne de seneste 15 år" },
        `🗓 Uden for sæson – normalt ${doyLabel(t.pheno.fra)} – ${doyLabel(t.pheno.til)}`) : null,
      t.portal || t.status ? h("div", { class: "row", style: { gap: "5px", marginTop: "5px" } },
        // Status meldt på tråden (SU/SUB) vinder over indtasterens egen status
        t.status ? statusBadge(t.status.status, t.status.live, t.status.status_at) : statusBadge(t.portal.status, t.portal.live, t.portal.status_at),
        t.source === "portal" ? h("span", { class: "tag portal" }, "Portal") : null,
        t.portal ? certaintyTag(t.portal.sikkerhed) : null) : null),
    h("div", { class: "side" }, tag(kat, KAT[kat]), h("span", { title: "Først set i portalen " + ago(t.first_seen) }, Date.now() - new Date(t.last_seen) < 45 * 60000 ? ago(t.last_seen) : (last.tid || ago(t.last_seen))),
      t.n_observers > 1 ? h("span", { title: "observatører" }, `${t.n_observers} obs.`) : null,
      t.likes ? h("span", { class: "likes", title: "tommel op" }, `👍 ${t.likes}`) : null,
      t.n_comments ? h("span", { title: "kommentarer" }, `💬 ${t.n_comments}`) : null,
      t.media?.images || t.media?.sounds ? h("span", { title: "Billeder og lyd i DOFbasen" },
        [t.media.images ? `📷 ${t.media.images}` : "", t.media.sounds ? `🔊 ${t.media.sounds}` : ""].filter(Boolean).join(" ")) : null,
      t.media?.dku?.length ? h("span", { class: "tag plain", title: "Status hos DKU" }, `DKU: ${t.media.dku.join(", ")}`) : null));
}

const RADII = [2, 5, 10, 25, 50, 100];

let lastPos = null;
/** Enhedens position (genbruges i 2 minutter, så feedets opdateringer ikke bruger GPS hver gang). */
async function devicePosition() {
  if (lastPos && Date.now() - lastPos.at < 120000) return lastPos;
  lastPos = { ...(await currentPosition({ timeout: 20000 })), at: Date.now() };
  return lastPos;
}

/** Nær mig: enten et midlertidigt filter om enhedens position (intet gemmes), eller – hvis funktionen er slået til – et
 *  område, der følger brugeren, når appen sender positionen i baggrunden. */
async function nearMe({ here, saved }) {
  const canSave = !!state.user?.position;
  const [areas, where] = canSave ? await Promise.all([api("/api/areas").catch(() => []), api("/api/me/location").catch(() => ({ location: null }))]) : [[], { location: null }];
  const mine = areas.find(a => a.ref === "me");
  const loc = where.location;
  const fresh = loc && Date.now() - new Date(loc.updated_at) < 30 * 60000;
  const sel = h("select", { "aria-label": "Radius" }, ...RADII.map(km => h("option", { value: km, selected: km === (mine ? mine.radius_m / 1000 : 25) }, `${km} km`)));
  const useHere = h("button", { class: "btn primary", type: "button" }, "Brug min position som filter");
  const keep = canSave ? h("button", { class: "btn", type: "button" }, mine ? "Opdatér mit Nær mig-område" : "Gem som område, der følger mig") : null;
  const body = h("div", { class: "stack" },
    h("label", { class: "field" }, h("span", {}, "Radius om min position"), sel),
    h("p", { class: "muted", style: { margin: 0 } }, "Midtpunktet er din enheds position, når feedet hentes. Der oprettes ingen lokation eller noget område."),
    useHere,
    keep ? h("div", { class: "stack" }, h("hr", {}), h("p", { class: "muted", style: { margin: 0 } }, fresh
      ? `Alternativt: gem filtret som et område, der følger dig (din position fra appen er opdateret ${ago(loc.updated_at)}). Det kan også bruges i alarmer.`
      : isApp() ? "Alternativt: gem et område, der følger dig. Slå «Følg min position med denne enhed» til under Profil, så flytter det sig med dig."
        : "Alternativt: gem et område om din nuværende position. Det flytter sig kun med dig i Android-appen."), keep) : null);
  const m = modal("Nær mig", body);
  useHere.onclick = async () => {
    useHere.disabled = true;
    try { await devicePosition(); m.close(); toast(`Viser observationer inden for ${sel.value} km af din position`); here(Number(sel.value)); }
    catch (err) { useHere.disabled = false; toast(err.message, "err"); }
  };
  if (keep) keep.onclick = async () => {
    keep.disabled = true;
    try {
      const payload = { radius_km: Number(sel.value) };
      if (!fresh) { const p = await devicePosition(); payload.lat = p.lat; payload.lon = p.lon; }
      const area = await api("/api/areas/near-me", { method: "PUT", body: payload });
      m.close(); toast(`Området «${area.name}» er gemt`); await saved(area);
    } catch (err) { keep.disabled = false; toast(err.message, "err"); }
  };
}

async function nearby() {
  if (!navigator.geolocation) { toast("Din enhed understøtter ikke position", "err"); return; }
  const body = h("div", {}, h("div", { class: "skeleton" }));
  const m = modal("Tæt på mig (25 km, 3 dage)", body, { wide: true });
  navigator.geolocation.getCurrentPosition(async (pos) => {
    try {
      const { latitude: lat, longitude: lon } = pos.coords;
      const data = await api(`/api/nearby?lat=${lat}&lon=${lon}&radius_km=25&days=3&level=bemaerk`);
      if (!data.items.length) { body.replaceChildren(empty("Intet bemærkelsesværdigt inden for 25 km de sidste 3 dage")); return; }
      body.replaceChildren(h("div", { class: "table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 3 } },
        h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Lokalitet"), h("th", {}, "Dato"), h("th", { class: "right" }, "Antal"), h("th", { class: "right" }, "km"))),
        h("tbody", {}, ...data.items.map(i => h("tr", { class: "click", onclick: () => { m.close(); location.hash = `#/traad/${i.dato}/${i.artnr}/${i.loknr}`; } },
          h("td", {}, tag(i.kategori), " ", h("b", {}, i.art)), h("td", { class: "wide" }, i.lok), h("td", { class: "nowrap", "data-label": "Dato" }, fmtDate(i.dato, false)),
          h("td", { class: "right num", "data-label": "Antal" }, fmtN(i.antal)), h("td", { class: "right num", "data-label": "km" }, String(i.km).replace(".", ","))))))));
    } catch (err) { body.replaceChildren(errorBox(err)); }
  }, (err) => body.replaceChildren(errorBox(new Error("Position ikke tilgængelig: " + err.message))), { enableHighAccuracy: false, timeout: 15000 });
}
