import { api, state, h, icon, fmtN, fmtDate, params, setParams, seg, loadRef, requireLogin, empty, errorBox, modal, toast, ago, searchSpecies } from "../core.js";
import { baseMap, drawTool, cssVar } from "../maps.js";

const LEVELS = { SU: "Kun SU", SUB: "SU + SUB", bemaerk: "SU + SUB + Bemærk", local: "Lokale hits", all: "Alle observationer" };
const KIND_LABEL = { afdeling: "Lokalafdeling", kommune: "Kommune", lokalitet: "Lokalitet", polygon: "Tegnet område", radius: "Radius" };

export async function render(view) {
  if (requireLogin(view)) return;
  await loadRef();
  const tab = params().tab || "regler";
  const body = h("div", {});
  view.append(h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Alarmer"), h("p", { class: "lead" }, "Få besked om sjældne fugle og lokale hits – i hele landet, din afdeling, din kommune eller et område du selv tegner.")),
    h("span", { class: "spacer" }),
    seg([["regler", "Regler"], ["omraader", "Områder"], ["enheder", "Push & enheder"]], tab, (v) => { setParams({ tab: v }); body.replaceChildren(); draw(v); })), body);
  const draw = (t) => ({ regler: rules, omraader: areas, enheder: devices }[t] || rules)(body);
  await draw(tab);
}

// ---------------------------------------------------------------- push

export async function enablePush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("Denne browser understøtter ikke push. På iPhone: tilføj appen til hjemmeskærmen først.");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Notifikationer er ikke tilladt i browseren");
  const reg = await navigator.serviceWorker.ready;
  const { key } = await api("/api/push/key");
  const raw = atob(key.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - key.length % 4) % 4));
  const appKey = Uint8Array.from(raw, c => c.charCodeAt(0));
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: appKey });
  const json = sub.toJSON();
  await api("/api/push/subscribe", { method: "POST", body: { endpoint: json.endpoint, keys: json.keys } });
}

async function pushStatus() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = await reg?.pushManager.getSubscription();
    return sub ? "on" : "off";
  } catch { return "off"; }
}

async function pushBanner() {
  const st = await pushStatus();
  if (st === "on") return null;
  const btn = h("button", { class: "btn primary", onclick: async () => { try { await enablePush(); toast("Push er slået til på denne enhed"); banner.remove(); } catch (err) { toast(err.message, "err"); } } }, "Slå push til");
  const banner = h("div", { class: "notice warn row between mb" },
    h("span", {}, st === "denied" ? "Notifikationer er blokeret i browserens indstillinger." : st === "unsupported" ? "Push understøttes ikke her (på iPhone: Del → Føj til hjemmeskærm)." : "Push er ikke slået til på denne enhed – du ser stadig alarmer i indbakken."),
    st === "off" ? btn : null);
  return banner;
}

// ---------------------------------------------------------------- regler

async function rules(body) {
  const [list, areasList] = await Promise.all([api("/api/alerts/rules"), api("/api/areas")]);
  const banner = await pushBanner();
  body.append(banner || "",
    h("div", { class: "row mb" }, h("button", { class: "btn primary", onclick: () => editRule(null, areasList, () => { body.replaceChildren(); rules(body); }), html: `${icon("plus")} Ny regel` }),
      h("span", { class: "muted" }, "Tip: Start med 'SU + SUB i hele Danmark' og 'Lokale hits' i din kommune.")));
  if (!list.length) { body.append(h("div", { class: "card" }, empty("Ingen regler endnu", "Opret en regel for at få notifikationer."), h("div", { class: "row", style: { justifyContent: "center" } },
    h("button", { class: "btn", onclick: async () => { await api("/api/alerts/rules", { method: "POST", body: { name: "SU + SUB i Danmark", level: "SUB" } }); body.replaceChildren(); rules(body); } }, "Opret 'SU + SUB i Danmark'")))); return; }
  body.append(h("div", { class: "grid" }, ...list.map(r => h("div", { class: "card row between", style: { opacity: r.enabled ? 1 : .55 } },
    h("div", {}, h("b", {}, r.name), h("div", { class: "muted" }, `${r.area_name} · ${LEVELS[r.level]}${r.level === "local" ? ` (< ${r.local_max} obs.)` : ""}${r.min_antal > 1 ? ` · min. ${r.min_antal} ind.` : ""}`,
      r.species_include.length ? ` · ${r.species_include.length} udvalgte arter` : "", r.species_exclude.length ? ` · ${r.species_exclude.length} fravalgt` : "", r.push ? "" : " · kun indbakke")),
    h("div", { class: "row" },
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: r.enabled, onchange: async (e) => { await api(`/api/alerts/rules/${r.id}`, { method: "PUT", body: { ...strip(r), enabled: e.target.checked } }); r.enabled = e.target.checked; e.target.closest(".card").style.opacity = r.enabled ? 1 : .55; } }), "Aktiv"),
      h("button", { class: "btn sm", onclick: () => editRule(r, areasList, () => { body.replaceChildren(); rules(body); }) }, "Redigér"),
      h("button", { class: "btn sm ghost danger", onclick: async () => { if (!confirm("Slet reglen?")) return; await api(`/api/alerts/rules/${r.id}`, { method: "DELETE" }); body.replaceChildren(); rules(body); } }, "Slet"))))));
}

const strip = (r) => ({ name: r.name, area_id: r.area_id, kommuner: r.kommuner || [], afdelinger: r.afdelinger || [], level: r.level, local_max: r.local_max, min_antal: r.min_antal, species_include: r.species_include, species_exclude: r.species_exclude, push: r.push, enabled: r.enabled });

async function editRule(rule, areasList, done) {
  const r = rule ? strip(rule) : { name: "", area_id: null, kommuner: [], afdelinger: [], level: "SUB", local_max: 5, min_antal: 1, species_include: [], species_exclude: [], push: true, enabled: true };
  const name = h("input", { value: r.name, placeholder: "Fx 'Lokale hits i Odsherred'", required: true });
  const hasRegions = r.kommuner.length || r.afdelinger.length;
  const area = h("select", {}, h("option", { value: "" }, "Hele Danmark"), h("option", { value: "regions", selected: !r.area_id && hasRegions }, "Kommuner og lokalafdelinger…"),
    areasList.length ? h("optgroup", { label: "Mine områder" }, ...areasList.map(a => h("option", { value: a.id, selected: a.id === r.area_id }, `${a.name} (${KIND_LABEL[a.kind]})`))) : null);
  const regions = regionPicker(r.kommuner, r.afdelinger);
  const syncArea = () => regions.el.classList.toggle("hide", area.value !== "regions");
  area.onchange = syncArea; syncArea();
  const level = h("select", {}, ...Object.entries(LEVELS).map(([v, l]) => h("option", { value: v, selected: v === r.level }, l)));
  const localMax = h("input", { type: "number", min: 1, max: 1000, value: r.local_max });
  const minAntal = h("input", { type: "number", min: 1, value: r.min_antal });
  const push = h("input", { type: "checkbox", checked: r.push });
  const include = speciesPicker(r.species_include, "Kun disse arter (valgfrit)");
  const exclude = speciesPicker(r.species_exclude, "Ignorér disse arter");
  const localRow = h("label", { class: "field" }, h("span", {}, "Lokalt hit = færre end N observationer i området i hele databasen"), localMax);
  const sync = () => localRow.classList.toggle("hide", level.value !== "local");
  level.onchange = sync; sync();
  const m = modal(rule ? "Redigér regel" : "Ny regel", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const reg = area.value === "regions" ? regions.value() : { kommuner: [], afdelinger: [] };
    if (area.value === "regions" && !reg.kommuner.length && !reg.afdelinger.length) { toast("Vælg mindst én kommune eller lokalafdeling", "err"); return; }
    const payload = { ...r, name: name.value, area_id: area.value && area.value !== "regions" ? Number(area.value) : null, ...reg, level: level.value, local_max: Number(localMax.value), min_antal: Number(minAntal.value),
      species_include: include.value(), species_exclude: exclude.value(), push: push.checked };
    try {
      if (rule) await api(`/api/alerts/rules/${rule.id}`, { method: "PUT", body: payload });
      else await api("/api/alerts/rules", { method: "POST", body: payload });
      m.close(); toast("Reglen er gemt"); done();
    } catch (err) { toast(err.message, "err"); }
  } },
    h("label", { class: "field" }, h("span", {}, "Navn"), name),
    h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Område"), area), h("label", { class: "field" }, h("span", {}, "Hvad skal udløse en alarm?"), level)),
    regions.el,
    localRow,
    h("label", { class: "field" }, h("span", {}, "Mindste antal individer"), minAntal),
    include.el, exclude.el,
    h("label", { class: "check" }, push, "Send push-notifikation (ellers kun indbakke)"),
    h("p", { class: "muted", style: { fontSize: ".85rem" } }, areasList.length ? "" : "Kommuner og lokalafdelinger vælges direkte her. Lokaliteter, tegnede områder og radius oprettes under fanen 'Områder'."),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem"), h("button", { type: "button", class: "btn ghost", onclick: () => m.close() }, "Annullér"))), { wide: true });
}

// Flere kommuner og lokalafdelinger direkte på reglen – uden at oprette dem som områder først
function regionPicker(kommuner, afdelinger) {
  const items = [...state.afdelinger.map(a => ({ key: `afd:${a.id}`, label: a.navn, sub: "lokalafdeling" })),
    ...state.kommuner.map(k => ({ key: `kom:${k.kode}`, label: `${k.navn} Kommune`, sub: "kommune" }))];
  const selected = new Set([...kommuner.map(k => `kom:${k}`), ...afdelinger.map(a => `afd:${a}`)]);
  const byKey = new Map(items.map(i => [i.key, i]));
  const chosen = h("div", { class: "chips wrap" });
  const input = h("input", { type: "search", placeholder: "Søg kommune eller lokalafdeling…", autocomplete: "off", spellcheck: "false" });
  const results = h("div", { class: "suggest" });
  const draw = () => chosen.replaceChildren(...[...selected].map(k => h("span", { class: "chip on" }, byKey.get(k)?.label || k,
    h("button", { type: "button", class: "chip-x", "aria-label": `Fjern ${byKey.get(k)?.label || k}`, html: icon("x"), onclick: () => { selected.delete(k); draw(); } }))));
  const show = () => {
    const q = input.value.trim().toLowerCase();
    const hits = q ? items.filter(i => !selected.has(i.key) && i.label.toLowerCase().includes(q)).slice(0, 10) : [];
    results.replaceChildren(...hits.map(i => h("button", { type: "button", onclick: () => { selected.add(i.key); input.value = ""; results.replaceChildren(); draw(); input.focus(); } },
      h("span", {}, i.label), h("i", { class: "muted" }, i.sub))));
  };
  input.addEventListener("input", show);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); results.querySelector("button")?.click(); } });
  draw();
  return {
    el: h("div", { class: "field" }, h("span", { class: "label" }, "Kommuner og lokalafdelinger (reglen gælder i dem alle)"), input, results, chosen),
    value: () => ({ kommuner: [...selected].filter(k => k.startsWith("kom:")).map(k => k.slice(4)), afdelinger: [...selected].filter(k => k.startsWith("afd:")).map(k => Number(k.slice(4))) }),
  };
}

export function speciesPicker(initial, label) {
  const selected = new Map();
  let all = [];
  const chosen = h("div", { class: "chips wrap" });
  const input = h("input", { type: "search", placeholder: "Søg art…", autocomplete: "off", spellcheck: "false" });
  const results = h("div", { class: "suggest" });
  const draw = () => chosen.replaceChildren(...[...selected].map(([id, navn]) => h("span", { class: "chip on" }, navn,
    h("button", { type: "button", class: "chip-x", "aria-label": `Fjern ${navn}`, html: icon("x"), onclick: () => { selected.delete(id); draw(); } }))));
  const ready = api("/api/reports/species").then(list => { all = list; for (const s of list) if (initial?.includes(s.artnr)) selected.set(s.artnr, s.navn); draw(); });
  const show = () => {
    const hits = searchSpecies(all, input.value).filter(s => !selected.has(s.artnr));
    results.replaceChildren(...hits.map(s => h("button", { type: "button", onclick: () => { selected.set(s.artnr, s.navn); input.value = ""; results.replaceChildren(); draw(); input.focus(); } },
      h("span", {}, s.navn), s.latin ? h("i", { class: "muted" }, s.latin) : null)));
  };
  input.addEventListener("input", () => ready.then(show));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); results.querySelector("button")?.click(); } });
  return { el: h("div", { class: "field" }, h("span", { class: "label" }, label), input, results, chosen), value: () => [...selected.keys()] };
}

// ---------------------------------------------------------------- områder

async function areas(body) {
  const list = await api("/api/areas");
  body.append(h("div", { class: "row mb" }, h("button", { class: "btn primary", onclick: () => editArea(() => { body.replaceChildren(); areas(body); }), html: `${icon("plus")} Nyt område` }),
    h("span", { class: "muted" }, "Områder bruges både til alarmer, feed-filtre og egne årslister.")));
  if (!list.length) { body.append(h("div", { class: "card" }, empty("Ingen områder endnu", "Tegn fx din lokale fuglelokalitet, eller vælg din kommune."))); return; }
  body.append(h("div", { class: "grid-2" }, ...list.map(a => {
    const mapEl = h("div", { class: "map small", style: { height: "180px" } });
    const card = h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", {}, a.name), h("span", { class: "spacer" }), h("span", { class: "tag plain" }, KIND_LABEL[a.kind])),
      mapEl,
      h("div", { class: "row mt" },
        h("a", { class: "btn sm", href: `#/?scope=${a.scope.startsWith("area:") ? "area:" + a.id : a.scope}&level=local` }, "Lokale hits i dag"),
        a.use_for_lists ? h("a", { class: "btn sm", href: `#/lister?scope=${a.scope.startsWith("area:") ? "area:" + a.id : a.scope}` }, "Min liste") : null,
        h("button", { class: "btn sm", onclick: () => areaSpecies(a) }, "Arter"),
        h("span", { style: { flex: 1 } }),
        h("button", { class: "btn sm ghost danger", onclick: async () => { if (!confirm(`Slet området '${a.name}'? Regler der bruger det slettes også.`)) return; await api(`/api/areas/${a.id}`, { method: "DELETE" }); body.replaceChildren(); areas(body); } }, "Slet")));
    setTimeout(() => {
      const map = baseMap(mapEl);
      if (a.geojson) { const g = L.geoJSON(a.geojson, { style: { color: cssVar("--brand"), weight: 2, fillOpacity: .15 } }).addTo(map); map.fitBounds(g.getBounds(), { padding: [10, 10] }); }
      else if (a.kind === "afdeling") map.setView([56.1, 10.8], 6);
    });
    return card;
  })));
}

async function areaSpecies(a) {
  const out = h("div", {}, h("div", { class: "skeleton" }));
  modal(`Arter i ${a.name}`, out, { wide: true });
  try {
    const d = await api(`/api/areas/${a.id}/species`);
    if (!d.species.length) { out.replaceChildren(empty("Tallene beregnes i baggrunden", "Kom tilbage om et par minutter.")); return; }
    out.replaceChildren(h("p", { class: "muted" }, "Sorteret med de sjældneste først. Arter med færre observationer end din grænse udløser 'lokalt hit'."),
      h("div", { class: "table-wrap", style: { maxHeight: "60vh" } }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", { class: "right" }, "Obs."), h("th", { class: "right" }, "Dage"), h("th", {}, "Senest"))),
        h("tbody", {}, ...d.species.map(s => h("tr", {}, h("td", {}, s.navn), h("td", { class: "right num" }, fmtN(s.n_obs)), h("td", { class: "right num" }, fmtN(s.n_days)), h("td", {}, fmtDate(s.last_date))))))));
  } catch (err) { out.replaceChildren(errorBox(err)); }
}

function editArea(done) {
  const st = { kind: "polygon", geo: null, radius: 2000, ref: null };
  const name = h("input", { placeholder: "Fx 'Min matrikel', 'Vejlerne', 'Hjemkommune'", required: true });
  const forLists = h("input", { type: "checkbox", checked: true });
  const config = h("div", { class: "stack" });
  const mapEl = h("div", { class: "map", style: { height: "380px" } });
  let map, tool;
  const kindSeg = seg(Object.entries(KIND_LABEL).map(([k, l]) => [k, l]), st.kind, (k) => { st.kind = k; st.ref = null; st.geo = null; drawConfig(); });

  function drawConfig() {
    tool?.destroy(); tool = null;
    mapEl.classList.toggle("hide", !["polygon", "radius"].includes(st.kind));
    if (st.kind === "afdeling") {
      const sel = h("select", { onchange: (e) => { st.ref = e.target.value; if (!name.value) name.value = e.target.selectedOptions[0].text; } }, h("option", { value: "" }, "Vælg…"), ...state.afdelinger.map(a => h("option", { value: a.id }, a.navn)));
      config.replaceChildren(sel);
    } else if (st.kind === "kommune") {
      const sel = h("select", { onchange: (e) => { st.ref = e.target.value; if (!name.value) name.value = e.target.selectedOptions[0].text + " Kommune"; } }, h("option", { value: "" }, "Vælg…"), ...state.kommuner.map(k => h("option", { value: k.kode }, k.navn)));
      config.replaceChildren(sel);
    } else if (st.kind === "lokalitet") {
      const inp = h("input", { type: "search", placeholder: "Søg lokalitet…" });
      const res = h("div", { class: "stack" });
      let t;
      inp.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
        if (inp.value.length < 2) return;
        const rows = await api(`/api/locations?q=${encodeURIComponent(inp.value)}`);
        res.replaceChildren(...rows.map(l => h("button", { type: "button", class: "btn", style: { justifyContent: "flex-start" }, onclick: () => { st.ref = String(l.loknr); name.value ||= l.navn; res.replaceChildren(h("div", { class: "notice" }, `Valgt: ${l.navn}${l.kommune ? ` (${l.kommune})` : ""}`)); } }, l.navn, l.kommune ? h("span", { class: "muted" }, ` · ${l.kommune}`) : null)));
      }, 250); };
      config.replaceChildren(inp, res);
    } else {
      const r = h("input", { type: "range", min: 200, max: 30000, step: 100, value: st.radius });
      const rv = h("b", {}, `${(st.radius / 1000).toFixed(1)} km`);
      r.oninput = () => { st.radius = Number(r.value); rv.textContent = `${(st.radius / 1000).toFixed(1)} km`; tool?.setRadius(st.radius); };
      config.replaceChildren(st.kind === "polygon"
        ? h("div", { class: "row" }, h("span", { class: "muted" }, "Klik på kortet for at sætte hjørner (mindst 3)."), h("button", { type: "button", class: "btn sm", onclick: () => tool.undo() }, "Fortryd punkt"), h("button", { type: "button", class: "btn sm", onclick: () => tool.clear() }, "Ryd"),
          h("button", { type: "button", class: "btn sm", onclick: () => locate() }, "Min position"))
        : h("div", { class: "row" }, h("span", { class: "muted" }, "Klik for centrum. Radius:"), r, rv, h("button", { type: "button", class: "btn sm", onclick: () => locate() }, "Min position")));
      setTimeout(() => {
        map ||= baseMap(mapEl);
        map.invalidateSize();
        tool = drawTool(map, { mode: st.kind, radius: st.radius, onChange: (g) => { st.geo = g; } });
      });
    }
  }
  const locate = () => navigator.geolocation?.getCurrentPosition(p => map?.setView([p.coords.latitude, p.coords.longitude], 13));
  const m = modal("Nyt område", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const payload = { name: name.value, kind: st.kind, use_for_lists: forLists.checked };
    if (["afdeling", "kommune", "lokalitet"].includes(st.kind)) { if (!st.ref) { toast("Vælg " + KIND_LABEL[st.kind].toLowerCase(), "err"); return; } payload.ref = st.ref; }
    if (st.kind === "polygon") { if (!st.geo) { toast("Tegn mindst 3 punkter", "err"); return; } payload.geojson = st.geo; }
    if (st.kind === "radius") { if (!st.geo) { toast("Klik for at vælge centrum", "err"); return; } payload.center = st.geo.center; payload.radius_m = st.geo.radius; }
    try { await api("/api/areas", { method: "POST", body: payload }); m.close(); toast("Området er oprettet – lokale artstal beregnes i baggrunden"); done(); }
    catch (err) { toast(err.message, "err"); }
  } }, h("label", { class: "field" }, h("span", {}, "Navn"), name), kindSeg, config, mapEl,
    h("label", { class: "check" }, forLists, "Brug området til mine årslister"),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Opret område"), h("button", { type: "button", class: "btn ghost", onclick: () => m.close() }, "Annullér"))),
  { wide: true, onClose: () => { tool?.destroy(); map?.remove(); } });
  drawConfig();
}

// ---------------------------------------------------------------- enheder

async function devices(body) {
  const st = await pushStatus();
  const list = await api("/api/push/devices");
  body.append(h("div", { class: "card stack" },
    h("h3", {}, "Denne enhed"),
    h("p", {}, st === "on" ? "✅ Push er slået til." : st === "denied" ? "🚫 Notifikationer er blokeret i browseren." : st === "unsupported" ? "Push understøttes ikke i denne browser (på iPhone kræver det at appen er føjet til hjemmeskærmen)." : "Push er ikke slået til."),
    h("div", { class: "row" },
      st !== "on" && st !== "unsupported" ? h("button", { class: "btn primary", onclick: async () => { try { await enablePush(); toast("Push slået til"); body.replaceChildren(); devices(body); } catch (err) { toast(err.message, "err"); } } }, "Slå push til") : null,
      h("button", { class: "btn", onclick: async () => { const r = await api("/api/push/test", { method: "POST" }); toast(r.delivered ? `Testbesked sendt til ${r.delivered} enhed(er)` : "Ingen enheder modtog beskeden", r.delivered ? "" : "err"); } }, "Send testbesked")),
    h("p", { class: "muted" }, "Stille timer og globalt push-til/fra findes under ", h("a", { href: "#/profil" }, "profil"), ".")),
    h("div", { class: "card mt" }, h("h3", {}, "Registrerede enheder"),
      list.length ? h("table", { class: "data" }, h("tbody", {}, ...list.map(d => h("tr", {},
        h("td", {}, d.user_agent?.match(/(iPhone|Android|Windows|Macintosh|Linux)/)?.[0] || "Enhed", h("div", { class: "muted", style: { fontSize: ".78rem" } }, d.endpoint_host)),
        h("td", { class: "muted" }, d.last_ok ? `Sidst leveret ${ago(d.last_ok)}` : `Oprettet ${fmtDate(d.created_at)}`),
        h("td", { class: "right" }, h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/push/devices/${d.id}`, { method: "DELETE" }); body.replaceChildren(); devices(body); } }, "Fjern"))))))
        : h("p", { class: "muted" }, "Ingen enheder.")));
}
