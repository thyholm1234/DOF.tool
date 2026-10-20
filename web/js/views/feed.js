import { statusBadge, certaintyTag } from "../reportui.js";
import { api, state, h, icon, fmtN, fmtDate, ago, tag, todayISO, params, setParams, loadRef, store, empty, errorBox, modal, toast, KAT } from "../core.js";
import { live } from "../live.js";

const LEVELS = [["SU", "SU"], ["SUB", "SUB+"], ["bemaerk", "Bemærk+"], ["local", "Lokale hits"], ["all", "Alle"]];

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
      for (const a of await api("/api/areas")) opts.push([a.scope.startsWith("area:") ? `area:${a.id}` : a.scope, `★ ${a.name}`]);
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
  const localWrap = h("label", { class: "row hide", style: { fontSize: ".85rem", gap: ".4rem" } });
  const localVal = h("b", { class: "num" }, st.localMax);
  const localRange = h("input", { type: "range", min: 1, max: 100, value: st.localMax, style: { width: "130px" } });
  localWrap.append("Færre end", localVal, "obs. historisk", localRange);

  // Værktøjslinje som i DOF.not: to-tilstandsknapper; kategori og område kun når egne filtre er slået fra
  const toggles = h("div", { class: "toolbar" });
  const chips = h("div", { class: "chips" });
  const filterRow = h("div", { class: "row between wrap", style: { gap: "8px" } }, chips, h("div", { class: "row", style: { gap: "8px" } }, localWrap, scopeSel));
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
        h("button", { class: "btn sm ghost", onclick: () => shiftDay(-1), "aria-label": "Forrige dag" }, "‹"), dateInput,
        h("button", { class: "btn sm ghost", onclick: () => shiftDay(1), "aria-label": "Næste dag" }, "›"),
        h("button", { class: "btn sm", onclick: nearby, html: `${icon("pin")} Tæt på mig` }))),
    h("div", { class: "feed-layout" },
      h("div", { class: "stack" },
        h("div", { class: "card toolbox" }, toggles, filterRow),
        listEl),
      sideEl));

  const opts = (await scopeOptions()).filter(o => o[0] !== "mine");
  for (const [v, l] of opts) scopeSel.append(h("option", { value: v, selected: v === st.scope }, l));
  if (st.scope !== "mine" && !opts.some(o => o[0] === st.scope)) st.scope = "dk";
  drawChips();
  scopeSel.addEventListener("change", () => { st.scope = scopeSel.value; st.prevScope = st.scope; st.autoScope = false; if (st.level === "all" && st.scope === "dk") st.level = "bemaerk"; drawChips(); load(); });
  localRange.addEventListener("input", () => { localVal.textContent = localRange.value; });
  localRange.addEventListener("change", () => { st.localMax = Number(localRange.value); load(); });

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
      const q = new URLSearchParams({ date: st.date, level: st.level, scope: st.scope, local_max: st.localMax });
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
      t.portal ? h("div", { class: "row", style: { gap: "5px", marginTop: "5px" } },
        statusBadge(t.portal.status, t.portal.live, t.portal.status_at), t.source === "portal" ? h("span", { class: "tag portal" }, "Portal") : null,
        certaintyTag(t.portal.sikkerhed)) : null),
    h("div", { class: "side" }, tag(kat, KAT[kat]), h("span", { title: "Først set i portalen " + ago(t.first_seen) }, Date.now() - new Date(t.last_seen) < 45 * 60000 ? ago(t.last_seen) : (last.tid || ago(t.last_seen))),
      t.n_observers > 1 ? h("span", { title: "observatører" }, `${t.n_observers} obs.`) : null,
      t.likes ? h("span", { class: "likes", title: "tommel op" }, `👍 ${t.likes}`) : null,
      t.n_comments ? h("span", { title: "kommentarer" }, `💬 ${t.n_comments}`) : null,
      t.media?.images || t.media?.sounds ? h("span", { title: "Billeder og lyd i DOFbasen" },
        [t.media.images ? `📷 ${t.media.images}` : "", t.media.sounds ? `🔊 ${t.media.sounds}` : ""].filter(Boolean).join(" ")) : null,
      t.media?.dku?.length ? h("span", { class: "tag plain", title: "Status hos DKU" }, `DKU: ${t.media.dku.join(", ")}`) : null));
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
