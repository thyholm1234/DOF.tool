// Indtastning: ture og observationer med DOFbasens felter + status (under observation) og sikkerhed.
import { api, state, h, icon, fmtN, fmtDate, todayISO, tag, toast, modal, empty, errorBox, seg, requireLogin, searchSpecies, compressImage, params, setParams, store, sortBar, KAT, esc } from "../core.js";
import { baseMap } from "../maps.js";
import { reportCard, statusBadge, certaintyTag, SIKKERHED, GRUNDLAG } from "../reportui.js";

let CODES = null;
let SPECIES = null;
const nowHM = () => new Date().toTimeString().slice(0, 5);

async function loadLookups() {
  if (!CODES || !SPECIES) [CODES, SPECIES] = await Promise.all([api("/api/reports/codes"), api("/api/reports/species")]);
}

export async function render(view, [tripId]) {
  if (requireLogin(view)) return;
  view.append(h("div", { class: "skeleton", style: { height: "220px" } }));
  try { await loadLookups(); } catch (err) { view.replaceChildren(errorBox(err)); return; }
  return tripId ? tripPage(view, Number(tripId)) : startPage(view);
}

// ---------------------------------------------------------------- modul: mine indtastede fugle + ny observation

const KAT_ORDER = { SU: 0, SUB: 1, bemaerk: 2, alm: 3 };

async function startPage(view) {
  const p = params();
  const newPanel = h("div", { class: p.ny ? "" : "hide" });
  const newBtn = h("button", { class: "btn primary", html: `${icon("plus")} Ny observation`, onclick: () => {
    newPanel.classList.toggle("hide");
    setParams({ ny: newPanel.classList.contains("hide") ? "" : "1" });
    if (!newPanel.classList.contains("hide")) newPanel.querySelector("input[type=search]")?.focus();
  } });
  newPanel.append(newTripCard());
  const listEl = h("div", {}, h("div", { class: "skeleton", style: { height: "160px" } }));
  const tripsEl = h("div", {});
  view.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Mine indtastninger"),
      h("p", { class: "lead" }, "Dine egne fugle indtastet i portalen. De vises med det samme i trådene og alarmerne.")),
      h("span", { class: "spacer" }), newBtn),
    newPanel, listEl,
    h("details", { class: "more mt" }, h("summary", {}, "Ture og eksport"), tripsEl),
    h("p", { class: "muted mt", style: { fontSize: ".85rem" } }, "Portalen sender ikke data til DOFbasen. Indtast også dine fund i DOFbasen (app.dofbasen.dk), eller brug CSV-eksporten med DOFbasens kolonnenavne. Når fundet dukker op i DOFbasen, markeres det med ✓."));

  try { drawMine(listEl, await api("/api/reports/mine")); } catch (err) { listEl.replaceChildren(errorBox(err)); }
  try {
    const rows = await api("/api/reports/trips");
    tripsEl.replaceChildren(h("div", { class: "row mt mb" }, h("a", { class: "btn sm", href: "/api/reports/export.csv", download: "", html: `${icon("external")} Eksportér CSV (DOFbasens kolonner)` })),
      rows.length ? h("div", { class: "loc-list" }, ...rows.map(r => h("a", { class: "loc-item", href: `#/indtast/tur/${r.id}` },
        h("span", {}, h("b", {}, r.lok), h("small", { class: "muted" }, ` · ${fmtDate(r.dato, false)}${r.turtidfra ? " " + r.turtidfra : ""}`)),
        h("small", { class: "muted num" }, `${r.n} obs. · ${r.n_species} ${r.n_species === 1 ? "art" : "arter"}`)))) : h("p", { class: "muted" }, "Ingen ture endnu."));
  } catch { /* ikke kritisk */ }
}

function drawMine(el, rows) {
  if (!rows.length) { el.replaceChildren(empty("Ingen indtastede fugle endnu", "Tryk på 'Ny observation' for at begynde.")); return; }
  const sort = store("minesort") || { key: "dato", dir: -1 };
  const filter = { q: "", kind: store("minefilter") || "alle" };
  const list = h("div", { class: "stack", style: { gap: "6px" } });
  const count = h("small", { class: "muted" });
  const cmp = {
    dato: (a, b) => (a.dato + (a.tid || "")).localeCompare(b.dato + (b.tid || "")) || a.id - b.id,
    art: (a, b) => a.art.localeCompare(b.art, "da"),
    sys: (a, b) => (a.sortering || 0) - (b.sortering || 0),
    antal: (a, b) => a.antal - b.antal,
    lok: (a, b) => (a.lok || "").localeCompare(b.lok || "", "da"),
    kat: (a, b) => KAT_ORDER[a.kategori] - KAT_ORDER[b.kategori],
  };
  const draw = () => {
    const q = filter.q.toLowerCase();
    const shown = rows.filter(r => (!q || r.art.toLowerCase().includes(q) || (r.lok || "").toLowerCase().includes(q))
      && (filter.kind === "alle" || (filter.kind === "sjaeldne" && r.kategori !== "alm") || (filter.kind === "live" && r.live)))
      .sort((a, b) => sort.dir * cmp[sort.key](a, b));
    count.textContent = `${shown.length} af ${rows.length}`;
    list.replaceChildren(...(shown.length ? shown.map(r => h("a", { class: `myobs ${r.kategori}`, href: `#/indtast/tur/${r.trip_id}` },
      h("div", { class: "myobs-when" }, h("b", {}, fmtDate(r.dato, false)), h("small", { class: "muted" }, r.tid || "")),
      h("div", { class: "myobs-main" },
        h("div", { class: "row", style: { gap: "6px" } }, h("b", { class: "num" }, fmtN(r.antal)), h("b", {}, r.art),
          r.kategori !== "alm" ? tag(r.kategori, KAT[r.kategori]) : null, certaintyTag(r.sikkerhed), statusBadge(r.status, r.live, r.status_at)),
        h("div", { class: "muted myobs-sub" }, [r.adf, `${r.lok}${r.kommune ? " · " + r.kommune : ""}`].filter(Boolean).join(" · "))),
      h("div", { class: "myobs-side" }, r.n_media ? h("small", { class: "muted" }, `📷 ${r.n_media}`) : null,
        r.i_dofbasen ? h("small", { class: "muted", title: "Også i DOFbasen" }, "✓ DOFbasen") : null)))
      : [h("p", { class: "muted" }, "Ingen fugle matcher filteret.")]));
  };
  const search = h("input", { type: "search", placeholder: "Søg art eller lokalitet…", oninput: (e) => { filter.q = e.target.value; draw(); } });
  el.replaceChildren(h("div", { class: "card stack" },
    h("div", { class: "row", style: { gap: "8px" } }, h("div", { style: { flex: 1, minWidth: "180px" } }, search),
      seg([["alle", "Alle"], ["sjaeldne", "SU/SUB/Bemærk"], ["live", "Under observation"]], filter.kind, (v) => { filter.kind = v; store("minefilter", v); draw(); })),
    h("div", { class: "row between" }, sortBar([["dato", "Dato/tid"], ["art", "Art"], ["sys", "Systematik"], ["antal", "Antal"], ["lok", "Lokalitet"], ["kat", "Kategori"]], sort,
      () => { store("minesort", sort); draw(); }), count),
    list));
  draw();
}

function newTripCard() {
  const dato = h("input", { type: "date", value: todayISO(), max: todayISO() });
  const fra = h("input", { type: "time", value: nowHM() });
  const results = h("div", { class: "loc-list" });
  const q = h("input", { type: "search", placeholder: "Søg lokalitet i DOFbasen…", autocomplete: "off" });
  const choose = async (loc) => {
    try {
      const trip = await api("/api/reports/trips", { method: "POST", body: { loknr: loc.loknr, dato: dato.value, turtidfra: fra.value || null } });
      location.hash = `#/indtast/tur/${trip.id}`;
    } catch (err) { toast(err.message, "err"); }
  };
  const showLocs = (rows, withKm = false) => results.replaceChildren(...(rows.length ? rows.map(l => h("button", { class: "loc-item", type: "button", onclick: () => choose(l) },
    h("span", {}, h("b", {}, l.navn), l.kommune ? h("small", { class: "muted" }, ` · ${l.kommune}`) : null),
    withKm ? h("small", { class: "muted num" }, `${String(l.km).replace(".", ",")} km`) : l.dato ? h("small", { class: "muted" }, fmtDate(l.dato, false)) : null))
    : [h("p", { class: "muted" }, "Ingen lokaliteter fundet.")]));
  let t;
  q.oninput = () => {
    clearTimeout(t);
    if (q.value.trim().length < 2) return;
    t = setTimeout(async () => { try { showLocs(await api(`/api/locations?q=${encodeURIComponent(q.value.trim())}`)); } catch (err) { toast(err.message, "err"); } }, 250);
  };
  const gps = h("button", { class: "btn", type: "button", html: `${icon("pin")} Nær mig` });
  gps.onclick = () => {
    if (!navigator.geolocation) { toast("Din enhed understøtter ikke position", "err"); return; }
    gps.disabled = true;
    navigator.geolocation.getCurrentPosition(async (p) => {
      gps.disabled = false;
      try { showLocs(await api(`/api/reports/near?lat=${p.coords.latitude}&lon=${p.coords.longitude}`), true); } catch (err) { toast(err.message, "err"); }
    }, (err) => { gps.disabled = false; toast("Position ikke tilgængelig: " + err.message, "err"); }, { enableHighAccuracy: true, timeout: 15000 });
  };
  api("/api/reports/recent").then(recent => {
    if (!results.children.length && recent.locations.length) { showLocs(recent.locations); results.prepend(h("small", { class: "muted" }, "Dine seneste lokaliteter")); }
  }).catch(() => {});
  return h("div", { class: "card stack mb" }, h("h2", { style: { margin: 0 } }, "Hvor og hvornår?"),
    h("p", { class: "muted", style: { margin: 0 } }, "Vælg en lokalitet fra DOFbasen. Fuglens præcise position sætter du bagefter på kortet."),
    h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Dato"), dato), h("label", { class: "field" }, h("span", {}, "Turtid fra"), fra)),
    h("div", { class: "row" }, h("div", { style: { flex: 1, minWidth: "180px" } }, q), gps),
    results);
}

// ---------------------------------------------------------------- tur: oplysninger + observationer

async function tripPage(view, id) {
  let trip, recent;
  try { [trip, recent] = await Promise.all([api(`/api/reports/trips/${id}`), api("/api/reports/recent")]); }
  catch (err) { view.replaceChildren(errorBox(err), h("p", {}, h("a", { href: "#/indtast" }, "← Mine indtastninger"))); return; }

  const list = h("div", { class: "stack" });
  const drawList = () => list.replaceChildren(...(trip.reports.length ? [...trip.reports].reverse().map(r => reportCard(r, {
    showObserver: false,
    onChange: (upd) => { Object.assign(r, upd); drawList(); },
    onEdit: () => editReport(r),
    onDelete: async () => {
      if (!confirm(`Slet ${r.antal} ${r.art}?`)) return;
      await api(`/api/reports/${r.id}`, { method: "DELETE" });
      trip.reports = trip.reports.filter(x => x.id !== r.id); drawList(); drawHead();
    },
  })) : [empty("Ingen observationer på turen endnu")]));

  const editReport = (r) => {
    const m = modal(`Redigér ${r.art}`, reportForm({ trip, recent, initial: r, submitLabel: "Gem ændringer", onSaved: (upd) => {
      Object.assign(r, upd); drawList(); m.close();
    } }), { wide: true });
  };

  const head = h("div", {});
  const drawHead = () => head.replaceChildren(h("div", { class: "page-head" },
    h("div", {}, h("p", { style: { margin: 0 } }, h("a", { href: "#/indtast" }, "← Mine indtastninger")),
      h("h1", { style: { marginTop: "4px" } }, trip.lok),
      h("p", { class: "lead" }, [fmtDate(trip.dato), trip.kommune, trip.afdeling].filter(Boolean).join(" · "),
        ` · ${trip.reports.length} obs.`)),
    h("span", { class: "spacer" }),
    h("button", { class: "btn", onclick: () => tripModal(trip, () => { drawHead(); }), html: `${icon("edit")} Turoplysninger` })));
  drawHead();

  view.replaceChildren(head,
    h("div", { class: "report-layout" },
      h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, "Ny observation"),
        reportForm({ trip, recent, submitLabel: "Gem observation", onSaved: (r) => { trip.reports.push(r); drawList(); drawHead(); } })),
      h("div", {}, h("h2", { style: { marginTop: 0 } }, "Observationer på turen"), list)));
  drawList();
}

function tripModal(trip, done) {
  const f = {
    dato: h("input", { type: "date", value: trip.dato, max: todayISO() }),
    turtidfra: h("input", { type: "time", value: trip.turtidfra || "" }),
    turtidtil: h("input", { type: "time", value: trip.turtidtil || "" }),
    medobser: h("input", { value: trip.medobser || "", maxlength: 300, placeholder: "Navne eller obserkoder" }),
    turnoter: h("textarea", { maxlength: 2000, placeholder: "Vejr, dækning, #BB26 …" }, trip.turnoter || ""),
  };
  const m = modal("Turoplysninger", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const upd = await api(`/api/reports/trips/${trip.id}`, { method: "PATCH", body: { dato: f.dato.value, turtidfra: f.turtidfra.value || null,
        turtidtil: f.turtidtil.value || null, medobser: f.medobser.value || null, turnoter: f.turnoter.value || null } });
      Object.assign(trip, upd); m.close(); done(); toast("Turen er gemt");
    } catch (err) { toast(err.message, "err"); }
  } },
    h("div", { class: "form-grid two" },
      h("label", { class: "field" }, h("span", {}, "Dato"), f.dato),
      h("div", { class: "row", style: { gap: "8px" } },
        h("label", { class: "field", style: { flex: 1 } }, h("span", {}, "Turtid fra"), f.turtidfra),
        h("label", { class: "field", style: { flex: 1 } }, h("span", {}, "til"), f.turtidtil))),
    h("label", { class: "field" }, h("span", {}, "Medobservatører"), f.medobser),
    h("label", { class: "field" }, h("span", {}, "Turnoter"), f.turnoter),
    h("div", { class: "row between" },
      h("button", { class: "btn ghost danger", type: "button", onclick: async () => {
        if (!confirm("Slet turen og alle dens observationer?")) return;
        await api(`/api/reports/trips/${trip.id}`, { method: "DELETE" }); m.close(); location.hash = "#/indtast";
      } }, "Slet tur"),
      h("button", { class: "btn primary", type: "submit" }, "Gem"))));
}

// ---------------------------------------------------------------- observationsformular

const GUIDE_URL = "https://www.dof.dk/om-dof/aktiv-i-dof/grupper-og-udvalg/feltornitologisk-udvalg/nar-du-ser-en-sjaelden-fugl";
const req = () => h("span", { class: "req", title: "Påkrævet" }, "påkrævet");

function reportForm({ trip, recent, initial = null, submitLabel, onSaved }) {
  const isToday = trip.dato === todayISO();
  const v = {
    artnr: initial?.artnr ?? null, klass: null, sikkerhed: initial?.sikkerhed ?? "sikker", grundlag: [...(initial?.grundlag ?? ["set"])],
    obs_lat: initial?.obs_lat ?? null, obs_lon: initial?.obs_lon ?? null,
    status: initial ? null : "ukendt",
  };
  const files = [];
  let statusTouched = false;

  // --- art: dropdown med dine seneste arter når feltet er tomt, søgeresultater når der skrives
  const spInput = h("input", { type: "search", placeholder: "Skriv eller vælg art …", autocomplete: "off", autocorrect: "off", spellcheck: "false" });
  const suggest = h("div", { class: "suggest dropdown" });
  const chosen = h("div", { class: "chosen" });
  const spWarn = h("div", {});
  const recentSpecies = recent.species.map(s => SPECIES.find(x => x.artnr === s.artnr)).filter(Boolean).slice(0, 12);
  const option = (s) => h("button", { type: "button", onmousedown: (e) => e.preventDefault(), onclick: () => pick(s) },
    h("span", {}, s.navn), s.klass !== "Alm" ? tag(s.klass) : null, s.latin ? h("i", { class: "muted" }, s.latin) : null);
  const showSuggest = () => {
    const q = spInput.value.trim();
    if (q.length >= 2) suggest.replaceChildren(...searchSpecies(SPECIES, q).map(option));
    else suggest.replaceChildren(...(recentSpecies.length ? [h("small", { class: "muted dd-head" }, "Dine seneste arter"), ...recentSpecies.map(option)] : []));
  };
  spInput.onfocus = showSuggest;
  spInput.oninput = showSuggest;
  spInput.onblur = () => setTimeout(() => suggest.replaceChildren(), 150);
  spInput.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); suggest.querySelector("button")?.click(); } if (e.key === "Escape") suggest.replaceChildren(); };
  const pick = (sp) => {
    spInput.classList.remove("invalid");
    v.artnr = sp?.artnr ?? null;
    v.klass = sp?.klass ?? null;
    suggest.replaceChildren();
    spInput.value = "";
    spInput.classList.toggle("hide", !!sp);
    chosen.replaceChildren(...(sp ? [h("span", { class: "chosen-sp" }, h("b", {}, sp.navn), sp.latin ? h("i", { class: "muted" }, ` ${sp.latin}`) : null,
      sp.klass && sp.klass !== "Alm" ? tag(sp.klass) : null,
      h("button", { type: "button", class: "icon-btn", "aria-label": "Skift art", html: icon("x"), onclick: () => { pick(null); spInput.focus(); } }))] : []));
    const rare = sp && ["SU", "SUB"].includes(sp.klass);
    spWarn.replaceChildren(rare ? h("div", { class: "notice warn" }, `${sp.navn} er ${sp.klass}-art. Angiv sikkerhed og grundlag, husk dokumentation (foto/beskrivelse) til ${sp.klass === "SU" ? "Sjældenhedsudvalget" : "den lokale SUB"}, og bekræft DOF's retningslinjer nederst.`) : "");
    guideReq.classList.toggle("hide", !rare);
    if (rare && isToday && !statusTouched && !initial) { v.status = "til_stede"; drawStatus(); }
  };

  // --- antal og tidspunkt
  const antal = h("input", { type: "number", min: 0, max: 9900000, value: initial?.antal ?? 1, inputmode: "numeric", style: { maxWidth: "110px" } });
  const bump = (d) => { antal.value = Math.max(0, (Number(antal.value) || 0) + d); };
  const tfra = h("input", { type: "time", required: true, value: initial?.obstidfra || (isToday ? nowHM() : trip.turtidfra || "") });
  const ttil = h("input", { type: "time", value: initial?.obstidtil || "" });

  // --- adfærd (påkrævet som i DOFbasen)
  const adf = h("select", { required: true, class: "required" },
    h("option", { value: "" }, "Vælg adfærd …"),
    recent.adf.length ? h("optgroup", { label: "Dine seneste" }, ...recent.adf.map(a => h("option", { value: a.code }, a.text))) : null,
    h("optgroup", { label: "Mest brugte" }, ...CODES.adf.filter(a => a.popular).map(a => h("option", { value: a.code }, a.text))),
    h("optgroup", { label: "Alle" }, ...[...CODES.adf].sort((a, b) => a.text.localeCompare(b.text, "da")).map(a => h("option", { value: a.code }, `${a.text} (${a.code})`))));
  adf.value = initial?.adfkode ?? "";
  adf.onchange = () => adf.classList.remove("invalid");

  // --- noter (altid fremme)
  const noter = h("textarea", { maxlength: 2000, placeholder: "Kendetegn, adfærd, lyd, hvordan fuglen blev fundet …" }, initial?.fuglnoter ?? "");

  // --- kort (altid fremme): fuglens position inden for lokaliteten
  const sel = (opts, value, placeholder) => { const s = h("select", {}, h("option", { value: "" }, placeholder), ...opts.map(o => h("option", { value: o.code ?? o }, o.text ?? o))); s.value = value || ""; return s; };
  const radius = sel([10, 25, 50, 100, 250, 500, 1000].map(n => ({ code: String(n), text: `± ${n} m` })), initial?.radius_m ? String(initial.radius_m) : "", "Nøjagtighed");
  const posText = h("small", { class: "muted" });
  const mapEl = h("div", { class: "map small", style: { height: "240px" } });
  let map = null, marker = null;
  const setPos = (lat, lon) => {
    v.obs_lat = lat == null ? null : Math.round(lat * 1e6) / 1e6; v.obs_lon = lon == null ? null : Math.round(lon * 1e6) / 1e6;
    posText.textContent = v.obs_lat ? `Fuglens position: ${v.obs_lat.toFixed(5)}, ${v.obs_lon.toFixed(5)}` : `Tryk på kortet for fuglens position (ellers bruges ${trip.lok})`;
    if (map) { marker?.remove(); marker = v.obs_lat ? L.marker([v.obs_lat, v.obs_lon]).addTo(map) : null; }
  };
  setPos(v.obs_lat, v.obs_lon);
  setTimeout(() => {  // kortet oprettes når formularen er sat ind på siden
    if (map || !mapEl.isConnected) return;
    map = baseMap(mapEl, { center: v.obs_lat ? [v.obs_lat, v.obs_lon] : trip.lat ? [trip.lat, trip.lon] : [56, 10.5], zoom: 14 });
    if (trip.lat) L.circleMarker([trip.lat, trip.lon], { radius: 6, color: "#fff", weight: 2, fillColor: "#555", fillOpacity: 1 }).addTo(map).bindTooltip(esc(trip.lok));
    map.on("click", (e) => setPos(e.latlng.lat, e.latlng.lng));
    setPos(v.obs_lat, v.obs_lon);
  }, 80);

  // --- status (under observation)
  const statusWrap = h("div", {});
  const drawStatus = () => statusWrap.replaceChildren(seg([["til_stede", "Ja, til stede nu"], ["fløjet", "Væk"], ["ukendt", "Ved ikke"]], v.status,
    (s) => { v.status = s; statusTouched = true; }));
  if (!initial) drawStatus();

  // --- sikkerhed
  const basis = h("div", { class: "chips wrap" });
  const drawBasis = () => basis.replaceChildren(...Object.entries(GRUNDLAG).map(([k, label]) => h("button", { type: "button", class: `chip ${v.grundlag.includes(k) ? "on" : ""}`,
    onclick: () => { v.grundlag = v.grundlag.includes(k) ? v.grundlag.filter(x => x !== k) : [...v.grundlag, k]; drawBasis(); } }, label)));
  drawBasis();

  // --- retningslinjer (DOF: "Når du ser en sjælden fugl") – påkrævet for SU/SUB
  const guide = h("input", { type: "checkbox", checked: !!initial?.retningslinjer });
  const guideReq = h("span", { class: "req hide" }, "påkrævet for SU/SUB");
  guide.onchange = () => guideBox.classList.remove("invalid");
  tfra.oninput = () => tfra.classList.remove("invalid");
  const guideBox = h("label", { class: "check guide" }, guide, h("span", {},
    "Jeg har læst og følger DOF's retningslinjer ", h("a", { href: GUIDE_URL, target: "_blank", rel: "noopener" }, "Når du ser en sjælden fugl"),
    " – fx at stedet kan tåle besøgende, og at jordejere og lokale tages i betragtning, før fundet deles. ", guideReq));

  // --- flere felter (som i DOFbasen)
  const koen = sel(CODES.koen, initial?.koen, "–");
  const alder = sel(CODES.alder, initial?.alderkode, "–");
  const dragt = sel(CODES.dragt, initial?.dragtkode, "–");
  const metode = sel(CODES.metode, initial?.metode, "–");
  const more = h("details", { class: "more" }, h("summary", {}, "Køn, alder, dragt og metode"),
    h("div", { class: "form-grid two mt" },
      h("label", { class: "field" }, h("span", {}, "Køn"), koen), h("label", { class: "field" }, h("span", {}, "Alder"), alder),
      h("label", { class: "field" }, h("span", {}, "Dragt"), dragt), h("label", { class: "field" }, h("span", {}, "Metode"), metode)));

  // --- medier
  const fileInput = h("input", { type: "file", accept: "image/*,audio/*", multiple: true, class: "hide" });
  const fileList = h("div", { class: "chips wrap" });
  const drawFiles = () => fileList.replaceChildren(...files.map((f, i) => h("span", { class: "chip on" }, f.name.length > 24 ? f.name.slice(0, 22) + "…" : f.name,
    h("button", { type: "button", class: "chip-x", html: icon("x"), onclick: () => { files.splice(i, 1); drawFiles(); } }))));
  fileInput.onchange = () => { files.push(...fileInput.files); fileInput.value = ""; drawFiles();
    if (files.some(f => f.type.startsWith("image")) && !v.grundlag.includes("foto")) { v.grundlag.push("foto"); drawBasis(); }
    if (files.some(f => f.type.startsWith("audio")) && !v.grundlag.includes("lyd")) { v.grundlag.push("lyd"); drawBasis(); } };

  const fail = (msg, el) => { toast(msg, "err"); el?.classList.add("invalid"); el?.scrollIntoView({ block: "center", behavior: "smooth" }); el?.focus?.(); };
  const submit = h("button", { class: "btn primary", type: "submit" }, submitLabel);
  const form = h("form", { class: "stack report-form", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    if (!v.artnr) return fail("Vælg en art", spInput);
    if (!tfra.value) return fail("Angiv tidspunkt for observationen", tfra);
    if (!adf.value) return fail("Vælg adfærd – det er påkrævet", adf);
    if (["SU", "SUB"].includes(v.klass) && !guide.checked) return fail("Bekræft DOF's retningslinjer for sjældne fugle", guideBox);
    const body = {
      artnr: v.artnr, antal: Number(antal.value) || 0, adfkode: adf.value, koen: koen.value || null, alderkode: alder.value || null,
      dragtkode: dragt.value || null, metode: metode.value || null, obstidfra: tfra.value, obstidtil: ttil.value || null,
      obs_lat: v.obs_lat, obs_lon: v.obs_lon, radius_m: radius.value ? Number(radius.value) : null, fuglnoter: noter.value.trim() || null,
      sikkerhed: v.sikkerhed, grundlag: v.grundlag, retningslinjer: guide.checked,
    };
    submit.disabled = true;
    try {
      let r = initial
        ? await api(`/api/reports/${initial.id}`, { method: "PATCH", body })
        : await api(`/api/reports/trips/${trip.id}/reports`, { method: "POST", body: { ...body, status: v.status } });
      for (const f of files) {
        const fd = new FormData(); fd.append("file", await compressImage(f));
        try { const m = await api(`/api/reports/${r.id}/media`, { method: "POST", form: fd }); r = { ...r, media: m.media, grundlag: m.grundlag }; }
        catch (err) { toast(`${f.name}: ${err.message}`, "err"); }
      }
      toast(initial ? "Ændringer gemt" : `${fmtN(r.antal)} ${r.art} gemt${r.kategori !== "alm" ? ` (${KAT[r.kategori]})` : ""}`);
      onSaved(r);
      if (!initial) {  // klar til næste art; behold metode, sikkerhed og retningslinje-afkrydsning
        pick(null); antal.value = 1; adf.value = ""; noter.value = ""; koen.value = ""; alder.value = ""; dragt.value = "";
        tfra.value = isToday ? nowHM() : tfra.value; ttil.value = "";
        files.length = 0; drawFiles(); v.status = "ukendt"; statusTouched = false; drawStatus(); setPos(null, null); radius.value = "";
        v.grundlag = ["set"]; drawBasis();
        spInput.focus();
      }
    } catch (err) { toast(err.message, "err"); }
    finally { submit.disabled = false; }
  } },
    h("div", { class: "field" }, h("span", { class: "label" }, "Art ", req()), chosen, h("div", { class: "dd-wrap" }, spInput, suggest), spWarn),
    h("div", { class: "form-grid two" },
      h("label", { class: "field" }, h("span", {}, "Antal"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, antal,
        h("button", { class: "btn sm", type: "button", onclick: () => bump(-1) }, "−1"), h("button", { class: "btn sm", type: "button", onclick: () => bump(1) }, "+1"),
        h("button", { class: "btn sm", type: "button", onclick: () => bump(10) }, "+10"))),
      h("div", { class: "field" }, h("span", { class: "label" }, "Tidspunkt ", req()),
        h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, tfra, h("span", { class: "muted" }, "–"), ttil,
          h("button", { class: "btn sm", type: "button", onclick: () => { tfra.value = nowHM(); tfra.classList.remove("invalid"); } }, "Nu")))),
    h("label", { class: "field" }, h("span", { class: "label" }, "Adfærd ", req()), adf),
    h("label", { class: "field" }, h("span", {}, "Noter om fuglen"), noter),
    h("div", { class: "field" }, h("span", { class: "label" }, "Hvor sad fuglen?"), mapEl,
      h("div", { class: "row mt", style: { gap: "6px" } }, posText, h("span", { style: { flex: 1 } }), radius,
        h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Min position`, onclick: () => navigator.geolocation?.getCurrentPosition((p) => {
          setPos(p.coords.latitude, p.coords.longitude); map?.setView([v.obs_lat, v.obs_lon], 15);
          if (!radius.value) radius.value = String([10, 25, 50, 100, 250].find(n => n >= p.coords.accuracy) || 250);
        }, (err) => toast(err.message, "err"), { enableHighAccuracy: true, timeout: 15000 }) }),
        h("button", { class: "btn sm ghost", type: "button", onclick: () => setPos(null, null) }, "Fjern"))),
    initial ? null : h("div", { class: "field" }, h("span", { class: "label" }, "Er fuglen under observation?"), statusWrap,
      h("small", { class: "muted" }, "Andre kan se, om fuglen stadig er der, og melde tilbage. Opdatér status, når den flyver.")),
    h("div", { class: "field" }, h("span", { class: "label" }, "Sikkerhed"),
      seg(Object.entries(SIKKERHED), v.sikkerhed, (s) => { v.sikkerhed = s; }),
      h("small", { class: "muted", style: { display: "block", margin: "8px 0 4px" } }, "Bestemmelsen bygger på:"), basis),
    more,
    h("div", { class: "field" }, h("span", { class: "label" }, "Foto og lyd"),
      h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: () => fileInput.click(), html: `${icon("plus")} Tilføj foto eller lyd` }), fileInput,
        h("small", { class: "muted" }, "Store billeder gøres mindre automatisk")),
      fileList, initial?.media?.length ? h("small", { class: "muted" }, `${initial.media.length} fil(er) gemt`) : null),
    guideBox,
    h("div", { class: "row between" }, h("small", { class: "muted" }, initial ? "" : `${trip.lok} · ${fmtDate(trip.dato, false)}`), submit));

  if (initial) pick(SPECIES.find(s => s.artnr === initial.artnr) || { artnr: initial.artnr, navn: initial.art });
  return form;
}
